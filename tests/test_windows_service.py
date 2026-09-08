"""Unit tests for Windows scheduled-task helpers (mocked schtasks)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tko.runtime.windows_service import (
    DEFAULT_TASK_NAME,
    ServicePlan,
    build_service_plan,
    build_task_command_line,
    ensure_portable_marker,
    install_scheduled_task,
    schtasks_create_args,
    schtasks_delete_args,
    schtasks_query_args,
    task_exists,
    uninstall_scheduled_task,
)


def test_build_task_command_line_quotes_paths():
    exe = Path(r"C:\Tools\TKO\tko.exe")
    work = Path(r"C:\Tools\TKO")
    tr = build_task_command_line(exe, work)
    assert "cmd.exe /c cd /d" in tr
    assert r"C:\Tools\TKO" in tr or "Tools" in tr
    assert "tko.exe" in tr
    assert " run" in tr


def test_schtasks_create_args_onstart_system():
    plan = ServicePlan(
        task_name="TkoBot",
        exe_path=Path(r"D:\bot\tko.exe"),
        work_dir=Path(r"D:\bot"),
        command_line=build_task_command_line(Path(r"D:\bot\tko.exe"), Path(r"D:\bot")),
    )
    args = schtasks_create_args(plan, force=True)
    assert args[0] == "schtasks"
    assert "/Create" in args
    assert "/TN" in args and "TkoBot" in args
    assert "/SC" in args and "ONSTART" in args
    assert "/RU" in args and "SYSTEM" in args
    assert "/RL" in args and "HIGHEST" in args
    assert "/F" in args
    assert "/TR" in args
    tr = args[args.index("/TR") + 1]
    assert "tko.exe" in tr and " run" in tr


def test_schtasks_create_args_without_force_no_f_flag():
    plan = ServicePlan(
        task_name="MyBot",
        exe_path=Path(r"C:\a\tko.exe"),
        work_dir=Path(r"C:\a"),
        command_line="cmd.exe /c echo",
    )
    args = schtasks_create_args(plan, force=False)
    assert "/F" not in args
    assert "MyBot" in args


def test_schtasks_delete_and_query_args():
    q = schtasks_query_args("TkoBot")
    assert q == ["schtasks", "/Query", "/TN", "TkoBot", "/FO", "LIST"]
    d = schtasks_delete_args("TkoBot", force=True)
    assert d == ["schtasks", "/Delete", "/TN", "TkoBot", "/F"]


def test_task_exists_true_on_zero_exit():
    mock_proc = MagicMock(returncode=0, stdout="TaskName: TkoBot", stderr="")
    with patch("tko.runtime.windows_service.run_schtasks", return_value=mock_proc):
        assert task_exists("TkoBot") is True


def test_task_exists_false_on_nonzero():
    mock_proc = MagicMock(returncode=1, stdout="", stderr="ERROR: The system cannot find")
    with patch("tko.runtime.windows_service.run_schtasks", return_value=mock_proc):
        assert task_exists("Missing") is False


def test_task_exists_false_on_oserror():
    with patch("tko.runtime.windows_service.run_schtasks", side_effect=OSError("no schtasks")):
        assert task_exists("TkoBot") is False


def test_install_refuses_without_force_when_exists():
    with (
        patch("tko.runtime.windows_service.sys.platform", "win32"),
        patch("tko.runtime.windows_service.task_exists", return_value=True),
        patch("tko.runtime.windows_service.resolve_exe_and_workdir") as res,
        patch("tko.runtime.windows_service.ensure_portable_marker"),
        patch.object(Path, "exists", return_value=True),
    ):
        res.return_value = (Path(r"C:\tko\tko.exe"), Path(r"C:\tko"))
        result = install_scheduled_task("TkoBot", force=False)
    assert result.ok is False
    assert "--force" in result.message or "sudah ada" in result.message.lower()


def test_install_success_with_force():
    mock_proc = MagicMock(returncode=0, stdout="SUCCESS: The scheduled task", stderr="")
    with (
        patch("tko.runtime.windows_service.sys.platform", "win32"),
        patch("tko.runtime.windows_service.task_exists", return_value=True),
        patch("tko.runtime.windows_service.run_schtasks", return_value=mock_proc) as run,
        patch("tko.runtime.windows_service.resolve_exe_and_workdir") as res,
        patch("tko.runtime.windows_service.ensure_portable_marker"),
        patch.object(Path, "exists", return_value=True),
    ):
        res.return_value = (Path(r"C:\tko\tko.exe"), Path(r"C:\tko"))
        result = install_scheduled_task("TkoBot", force=True)
    assert result.ok is True
    assert "berhasil" in result.message.lower()
    called_args = run.call_args[0][0]
    assert "/F" in called_args
    assert "/RU" in called_args and "SYSTEM" in called_args


def test_install_access_denied_message():
    mock_proc = MagicMock(returncode=1, stdout="", stderr="ERROR: Access is denied.")
    with (
        patch("tko.runtime.windows_service.sys.platform", "win32"),
        patch("tko.runtime.windows_service.task_exists", return_value=False),
        patch("tko.runtime.windows_service.run_schtasks", return_value=mock_proc),
        patch("tko.runtime.windows_service.resolve_exe_and_workdir") as res,
        patch("tko.runtime.windows_service.ensure_portable_marker"),
        patch.object(Path, "exists", return_value=True),
    ):
        res.return_value = (Path(r"C:\tko\tko.exe"), Path(r"C:\tko"))
        result = install_scheduled_task("TkoBot", force=False)
    assert result.ok is False
    assert "Administrator" in result.message


def test_install_non_windows():
    with patch("tko.runtime.windows_service.sys.platform", "linux"):
        result = install_scheduled_task()
    assert result.ok is False
    assert "Windows" in result.message


def test_uninstall_not_found():
    with (
        patch("tko.runtime.windows_service.sys.platform", "win32"),
        patch("tko.runtime.windows_service.task_exists", return_value=False),
    ):
        result = uninstall_scheduled_task("TkoBot")
    assert result.ok is False
    assert "tidak ditemukan" in result.message.lower()


def test_uninstall_success():
    mock_proc = MagicMock(returncode=0, stdout="SUCCESS", stderr="")
    with (
        patch("tko.runtime.windows_service.sys.platform", "win32"),
        patch("tko.runtime.windows_service.task_exists", return_value=True),
        patch("tko.runtime.windows_service.run_schtasks", return_value=mock_proc),
    ):
        result = uninstall_scheduled_task("TkoBot")
    assert result.ok is True
    assert "berhasil dihapus" in result.message.lower()


def test_ensure_portable_marker(tmp_path: Path):
    marker = ensure_portable_marker(tmp_path)
    assert marker.exists()
    assert marker.name == ".portable"
    ensure_portable_marker(tmp_path)
    assert marker.exists()


def test_build_service_plan_uses_sys_executable():
    with patch("tko.runtime.windows_service.sys.executable", "/opt/tko/tko.exe"):
        plan = build_service_plan("CustomName")
    assert plan.task_name == "CustomName"
    assert plan.exe_path.name == "tko.exe"
    assert plan.work_dir == Path("/opt/tko")
    assert "run" in plan.command_line


def test_default_task_name():
    assert DEFAULT_TASK_NAME == "TkoBot"
