"""Windows Task Scheduler install/uninstall helpers for portable TKO."""

from __future__ import annotations

import getpass
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCHTASKS = "schtasks"
DEFAULT_TASK_NAME = "TKO-LiveBot"


@dataclass(frozen=True, slots=True)
class ServicePlan:
    task_name: str
    exe_path: Path
    work_dir: Path
    command_line: str


def resolve_exe_and_workdir() -> tuple[Path, Path]:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        work = exe.parent
    else:
        exe = Path(sys.executable).resolve()
        work = Path.cwd().resolve()
    return exe, work


def build_task_command_line(exe: Path, work: Path) -> str:
    return f'"{exe}" run --state-dir "{work / "state"}"'


def build_service_plan(task_name: str = DEFAULT_TASK_NAME) -> ServicePlan:
    exe, work = resolve_exe_and_workdir()
    return ServicePlan(
        task_name=task_name.strip() or DEFAULT_TASK_NAME,
        exe_path=exe,
        work_dir=work,
        command_line=build_task_command_line(exe, work),
    )


def schtasks_query_args(task_name: str) -> list[str]:
    return [SCHTASKS, "/Query", "/TN", task_name, "/FO", "LIST"]


def schtasks_create_args(
    plan: ServicePlan,
    *,
    force: bool = False,
    use_system: bool = False,
    run_as_user: str | None = None,
) -> list[str]:
    args = [
        SCHTASKS, "/Create", "/TN", plan.task_name, "/TR", plan.command_line,
        "/SC", "ONSTART", "/RL", "HIGHEST",
    ]
    if use_system:
        args.extend(["/RU", "SYSTEM"])
    else:
        args.extend(["/RU", run_as_user or getpass.getuser()])
    if force:
        args.append("/F")
    return args


def schtasks_delete_args(task_name: str, *, force: bool = True) -> list[str]:
    args = [SCHTASKS, "/Delete", "/TN", task_name]
    if force:
        args.append("/F")
    return args


def run_schtasks(args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, shell=False, check=False
    )


def task_exists(task_name: str) -> bool:
    try:
        return run_schtasks(schtasks_query_args(task_name)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_portable_marker(work_dir: Path) -> Path:
    marker = work_dir / ".portable"
    if not marker.exists():
        marker.write_text(
            "TKO portable mode — state and logs live next to this executable.\n",
            encoding="utf-8",
        )
    return marker


def system_acl_hint(work_dir: Path) -> str:
    return (
        f'icacls "{work_dir}" /inheritance:r '
        f'/grant:r SYSTEM:(OI)(CI)F /grant:r Administrators:(OI)(CI)F'
    )


@dataclass(frozen=True, slots=True)
class ServiceResult:
    ok: bool
    message: str
    detail: str = ""


def install_scheduled_task(
    task_name: str = DEFAULT_TASK_NAME,
    *,
    force: bool = False,
    create_portable_marker: bool = True,
    use_system: bool = False,
) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "install-service hanya didukung di Windows (schtasks).")
    plan = build_service_plan(task_name)
    if not plan.exe_path.exists():
        return ServiceResult(False, f"Executable tidak ditemukan: {plan.exe_path}")
    if create_portable_marker:
        ensure_portable_marker(plan.work_dir)
    if task_exists(plan.task_name) and not force:
        return ServiceResult(False, f"Task sudah ada: {plan.task_name}. Gunakan --force.")
    result = run_schtasks(schtasks_create_args(plan, force=force, use_system=use_system))
    if result.returncode != 0:
        return ServiceResult(False, "schtasks /Create gagal", detail=(result.stderr or result.stdout or "")[:500])
    return ServiceResult(True, f"Task terpasang: {plan.task_name}", detail=restart_policy_notes())


def uninstall_scheduled_task(task_name: str = DEFAULT_TASK_NAME) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "uninstall-service hanya didukung di Windows.")
    if not task_exists(task_name):
        return ServiceResult(True, f"Task tidak ada (noop): {task_name}")
    result = run_schtasks(schtasks_delete_args(task_name))
    if result.returncode != 0:
        return ServiceResult(False, "schtasks /Delete gagal", detail=(result.stderr or result.stdout or "")[:500])
    return ServiceResult(True, f"Task dihapus: {task_name}")


def restart_policy_notes() -> str:
    """Document safe Windows Task Scheduler recovery policy (Stage 4)."""
    return (
        "MultipleInstancesPolicy=IgnoreNew (never parallel LIVE instances). "
        "RestartOnFailure: bounded count (e.g. 3) with interval >= 60s. "
        "Every restart must pass application startup reconciliation barrier. "
        "InstanceLock is the secondary hard gate against dual READY."
    )
