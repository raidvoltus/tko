"""Windows scheduled-task helpers for portable TKO .exe (schtasks only)."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TASK_NAME = "TkoBot"
SCHTASKS = "schtasks"


@dataclass(frozen=True, slots=True)
class ServicePlan:
    """Resolved paths and task name for install/uninstall."""

    task_name: str
    exe_path: Path
    work_dir: Path
    command_line: str  # value for /TR


def resolve_exe_and_workdir() -> tuple[Path, Path]:
    """Absolute path to this process binary and its directory."""
    exe = Path(sys.executable).resolve()
    work = exe.parent
    return exe, work


def build_task_command_line(exe: Path, work_dir: Path) -> str:
    """Build /TR string: run from work_dir so relative data paths resolve.

    Uses cmd.exe so the working directory is the folder that contains the exe
    (not System32, which is the default for many scheduled tasks).
    """
    exe_s = str(exe)
    work_s = str(work_dir)
    return f'cmd.exe /c cd /d "{work_s}" && "{exe_s}" run'


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
) -> list[str]:
    """Arguments for: schtasks /Create ... /RU SYSTEM /SC ONSTART."""
    args = [
        SCHTASKS,
        "/Create",
        "/TN",
        plan.task_name,
        "/TR",
        plan.command_line,
        "/SC",
        "ONSTART",
        "/RU",
        "SYSTEM",
        "/RL",
        "HIGHEST",
    ]
    if force:
        args.append("/F")
    return args


def schtasks_delete_args(task_name: str, *, force: bool = True) -> list[str]:
    args = [SCHTASKS, "/Delete", "/TN", task_name]
    if force:
        args.append("/F")
    return args


def run_schtasks(args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Run schtasks; never raises for non-zero exit — caller inspects returncode."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )


def task_exists(task_name: str) -> bool:
    """Return True if schtasks /Query finds the task."""
    try:
        proc = run_schtasks(schtasks_query_args(task_name))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def ensure_portable_marker(work_dir: Path) -> Path:
    """Create .portable next to exe so state/logs stay beside the binary under SYSTEM."""
    marker = work_dir / ".portable"
    if not marker.exists():
        marker.write_text(
            "TKO portable mode — state and logs live next to this executable.\n",
            encoding="utf-8",
        )
    return marker


def system_acl_hint(work_dir: Path) -> str:
    return f'icacls "{work_dir}" /grant "SYSTEM:(OI)(CI)F" /T'


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
) -> ServiceResult:
    """Create ONSTART task running as SYSTEM. Fail-safe: never raises."""
    if sys.platform != "win32":
        return ServiceResult(
            False,
            "install-service hanya didukung di Windows (schtasks).",
        )

    plan = build_service_plan(task_name)

    if not plan.exe_path.exists():
        return ServiceResult(
            False,
            f"Executable tidak ditemukan: {plan.exe_path}",
        )

    if create_portable_marker:
        try:
            ensure_portable_marker(plan.work_dir)
        except OSError as exc:
            return ServiceResult(
                False,
                f"Gagal membuat marker .portable di {plan.work_dir}: {exc}",
            )

    exists = task_exists(plan.task_name)
    if exists and not force:
        return ServiceResult(
            False,
            (
                f"Task '{plan.task_name}' sudah ada. "
                "Jalankan ulang dengan --force untuk menimpa, "
                "atau uninstall-service terlebih dahulu."
            ),
        )

    args = schtasks_create_args(plan, force=force or exists)
    try:
        proc = run_schtasks(args)
    except subprocess.TimeoutExpired:
        return ServiceResult(False, "schtasks timeout saat membuat task.")
    except OSError as exc:
        return ServiceResult(False, f"Gagal menjalankan schtasks: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    combined = "\n".join(x for x in (out, err) if x)

    if proc.returncode != 0:
        lower = combined.lower()
        if "access is denied" in lower or "access denied" in lower:
            return ServiceResult(
                False,
                "Akses ditolak. Jalankan Command Prompt sebagai Administrator.",
                combined,
            )
        if "system" in lower and ("password" in lower or "logon" in lower or "denied" in lower):
            return ServiceResult(
                False,
                (
                    "Tidak dapat membuat task dengan /RU SYSTEM "
                    "(kebijakan keamanan atau hak admin kurang). "
                    "Batalkan — tidak meminta password interaktif. "
                    "Jalankan sebagai Administrator, atau longgarkan kebijakan GPO."
                ),
                combined,
            )
        return ServiceResult(
            False,
            f"schtasks gagal (kode {proc.returncode}).",
            combined,
        )

    hint = system_acl_hint(plan.work_dir)
    msg = (
        f"Scheduled task '{plan.task_name}' berhasil dibuat.\n"
        f"  Exe     : {plan.exe_path}\n"
        f"  Workdir : {plan.work_dir}\n"
        f"  Trigger : ONSTART (saat sistem menyala)\n"
        f"  Account : SYSTEM (tanpa login)\n"
        f"\n"
        f"Peringatan portabilitas:\n"
        f"  - Path absolut disimpan di task. Jika exe dipindah, jalankan "
        f"uninstall-service lalu install-service lagi di lokasi baru.\n"
        f"  - Pastikan akun SYSTEM bisa baca/tulis folder workdir "
        f"(state, logs, kredensial).\n"
        f"  - Jika perlu, berikan izin:\n"
        f"      {hint}\n"
    )
    return ServiceResult(True, msg, combined)


def uninstall_scheduled_task(task_name: str = DEFAULT_TASK_NAME) -> ServiceResult:
    """Delete scheduled task. Fail-safe: never raises."""
    if sys.platform != "win32":
        return ServiceResult(
            False,
            "uninstall-service hanya didukung di Windows (schtasks).",
        )

    name = task_name.strip() or DEFAULT_TASK_NAME
    if not task_exists(name):
        return ServiceResult(
            False,
            f"Task '{name}' tidak ditemukan (mungkin sudah dihapus).",
        )

    args = schtasks_delete_args(name, force=True)
    try:
        proc = run_schtasks(args)
    except subprocess.TimeoutExpired:
        return ServiceResult(False, "schtasks timeout saat menghapus task.")
    except OSError as exc:
        return ServiceResult(False, f"Gagal menjalankan schtasks: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    combined = "\n".join(x for x in (out, err) if x)

    if proc.returncode != 0:
        lower = combined.lower()
        if "access is denied" in lower or "access denied" in lower:
            return ServiceResult(
                False,
                "Akses ditolak. Jalankan Command Prompt sebagai Administrator.",
                combined,
            )
        return ServiceResult(
            False,
            f"schtasks gagal menghapus task (kode {proc.returncode}).",
            combined,
        )

    return ServiceResult(
        True,
        f"Scheduled task '{name}' berhasil dihapus.",
        combined,
    )
