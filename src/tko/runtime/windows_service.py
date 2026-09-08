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
            "TKO portable mode - state and logs live next to this executable.\n",
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
        detail = (result.stderr or result.stdout or "")[:500]
        return ServiceResult(
            False,
            "Gagal membuat task - jalankan sebagai Administrator.",
            detail=detail,
        )
    return ServiceResult(
        True, f"Task berhasil terpasang: {plan.task_name}", detail=restart_policy_notes()
    )


def uninstall_scheduled_task(task_name: str = DEFAULT_TASK_NAME) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "uninstall-service hanya didukung di Windows.")
    if not task_exists(task_name):
        return ServiceResult(False, f"Task tidak ditemukan: {task_name}")
    result = run_schtasks(schtasks_delete_args(task_name))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[:500]
        return ServiceResult(False, "schtasks /Delete gagal", detail=detail)
    return ServiceResult(True, f"Task berhasil dihapus: {task_name}")


def restart_policy_notes() -> str:
    return (
        "MultipleInstancesPolicy=IgnoreNew (never parallel LIVE instances). "
        "RestartOnFailure: bounded count (e.g. 3) with interval >= 60s. "
        "Every restart must pass application startup reconciliation barrier. "
        "InstanceLock is the secondary hard gate against dual READY."
    )


def build_task_xml(plan: ServicePlan, *, use_system: bool = False) -> str:
    """Task Scheduler 1.2 XML with MultipleInstancesPolicy=IgnoreNew (INV-44)."""
    tr = plan.command_line
    if tr.startswith("cmd.exe /c "):
        args = tr[len("cmd.exe /c ") :]
    else:
        args = tr
    args_esc = (
        args.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    work = str(plan.work_dir).replace("&", "&amp;")
    user = "S-1-5-18" if use_system else "S-1-5-32-544"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>TKO LIVE bot MultipleInstances=IgnoreNew</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <BootTrigger><Enabled>true</Enabled></BootTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{user}</UserId>\n"
        "      <RunLevel>HighestAvailable</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>3</Count>\n"
        "    </RestartOnFailure>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        "      <Command>cmd.exe</Command>\n"
        f"      <Arguments>/c {args_esc}</Arguments>\n"
        f"      <WorkingDirectory>{work}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def schtasks_create_xml_args(plan: ServicePlan, xml_path: Path, *, force: bool = False) -> list[str]:
    args = [SCHTASKS, "/Create", "/TN", plan.task_name, "/XML", str(xml_path)]
    if force:
        args.append("/F")
    return args


def install_scheduled_task_xml(
    task_name: str = DEFAULT_TASK_NAME,
    *,
    force: bool = False,
    use_system: bool = False,
    xml_dir: Path | None = None,
) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "install-service hanya didukung di Windows (schtasks).")
    plan = build_service_plan(task_name)
    if not plan.exe_path.exists():
        return ServiceResult(False, f"Executable tidak ditemukan: {plan.exe_path}")
    ensure_portable_marker(plan.work_dir)
    out_dir = xml_dir or plan.work_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / f"{plan.task_name}.xml"
    xml_path.write_text(build_task_xml(plan, use_system=use_system), encoding="utf-16")
    if task_exists(plan.task_name) and not force:
        return ServiceResult(False, f"Task sudah ada: {plan.task_name}. Gunakan --force.")
    result = run_schtasks(schtasks_create_xml_args(plan, xml_path, force=force))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[:500]
        return ServiceResult(
            False,
            "Gagal membuat task - jalankan sebagai Administrator.",
            detail=detail,
        )
    return ServiceResult(
        True,
        f"Task berhasil terpasang: {plan.task_name}",
        detail=restart_policy_notes(),
    )
