"""Windows scheduled-task helpers for portable TKO .exe (schtasks only)."""

from __future__ import annotations

import getpass
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TASK_NAME = "TkoBot"
SCHTASKS = "schtasks"


@dataclass(frozen=True, slots=True)
class ServicePlan:
    task_name: str
    exe_path: Path
    work_dir: Path
    command_line: str


def resolve_exe_and_workdir() -> tuple[Path, Path]:
    exe = Path(sys.executable).resolve()
    return exe, exe.parent


def build_task_command_line(exe: Path, work_dir: Path) -> str:
    return f'cmd.exe /c cd /d "{work_dir}" && "{exe}" run'


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
        try:
            ensure_portable_marker(plan.work_dir)
        except OSError as exc:
            return ServiceResult(False, f"Gagal membuat marker .portable: {exc}")
    exists = task_exists(plan.task_name)
    if exists and not force:
        return ServiceResult(
            False,
            f"Task '{plan.task_name}' sudah ada. Gunakan --force atau uninstall-service.",
        )
    args = schtasks_create_args(plan, force=force or exists, use_system=use_system)
    try:
        proc = run_schtasks(args)
    except subprocess.TimeoutExpired:
        return ServiceResult(False, "schtasks timeout")
    except OSError as exc:
        return ServiceResult(False, f"Gagal menjalankan schtasks: {exc}")
    combined = "\n".join(
        x for x in ((proc.stdout or "").strip(), (proc.stderr or "").strip()) if x
    )
    if proc.returncode != 0:
        lower = combined.lower()
        if "access" in lower and "denied" in lower:
            return ServiceResult(
                False, "Akses ditolak. Jalankan sebagai Administrator.", combined
            )
        return ServiceResult(False, f"schtasks gagal (kode {proc.returncode}).", combined)
    account = "SYSTEM" if use_system else getpass.getuser()
    lines = [
        f"Scheduled task '{plan.task_name}' berhasil dibuat.",
        f"  Exe     : {plan.exe_path}",
        f"  Workdir : {plan.work_dir}",
        f"  Trigger : ONSTART",
        f"  Account : {account}",
        "",
        "Peringatan: path absolut tersimpan. Jika exe dipindah, install ulang.",
    ]
    if use_system:
        lines += ["", "PERINGATAN KEAMANAN (/RU SYSTEM):", f"  {system_acl_hint(plan.work_dir)}"]
    else:
        lines += [
            "",
            "Tanpa password, task biasanya jalan saat user logon. Untuk tanpa logon: --use-system.",
        ]
    return ServiceResult(True, "\n".join(lines) + "\n", combined)


def uninstall_scheduled_task(task_name: str = DEFAULT_TASK_NAME) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "uninstall-service hanya didukung di Windows (schtasks).")
    name = task_name.strip() or DEFAULT_TASK_NAME
    if not task_exists(name):
        return ServiceResult(False, f"Task '{name}' tidak ditemukan.")
    try:
        proc = run_schtasks(schtasks_delete_args(name, force=True))
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ServiceResult(False, str(exc))
    if proc.returncode != 0:
        return ServiceResult(
            False, f"schtasks gagal menghapus (kode {proc.returncode}).", (proc.stderr or "")
        )
    return ServiceResult(True, f"Scheduled task '{name}' berhasil dihapus.")
