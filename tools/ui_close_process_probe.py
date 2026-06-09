#!/usr/bin/env python3
"""Probe that verifies the LoopMaster UI process exits after a normal close.

The probe starts the current application entry point in a child process, waits
for the visible MainWindow, sends a Windows WM_CLOSE message, and then checks
that the child exits within the configured budget.
"""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
DEFAULT_RUNTIME_DIR = TOOLS_DIR / "ui-close-process-probe-runtime"
DEFAULT_TITLE_SUBSTRING = "LoopMaster"
WM_CLOSE = 0x0010
ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


@dataclass
class StreamTails:
    stdout: deque[str]
    stderr: deque[str]


def _ms(seconds: float) -> float:
    return seconds * 1000.0


def _fmt_ms(seconds: float) -> str:
    return f"{_ms(seconds):.1f}ms"


def _hwnd_value(hwnd) -> int:
    value = getattr(hwnd, "value", hwnd)
    return int(value)


def _load_user32():
    if os.name != "nt":
        raise RuntimeError("this probe uses Windows top-level window APIs")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [
        ENUM_WINDOWS_PROC,
        wintypes.LPARAM,
    ]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    return user32


def _window_pid(user32, hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _window_title(user32, hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def _process_tree_pids(pid: int) -> set[int]:
    pids = {pid}
    try:
        root = psutil.Process(pid)
        pids.update(child.pid for child in root.children(recursive=True))
    except psutil.Error:
        pass
    return pids


def _process_tree_alive(pid: int) -> list[int]:
    alive: list[int] = []
    for candidate in _process_tree_pids(pid):
        if psutil.pid_exists(candidate):
            alive.append(candidate)
    return sorted(alive)


def _find_windows_for_pids(user32, pids: set[int], title_substring: str) -> list[WindowInfo]:
    matches: list[WindowInfo] = []
    needle = title_substring.casefold()

    @ENUM_WINDOWS_PROC
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = _window_pid(user32, hwnd)
        if owner_pid not in pids:
            return True
        title = _window_title(user32, hwnd)
        if title and (not needle or needle in title.casefold()):
            matches.append(WindowInfo(hwnd=_hwnd_value(hwnd), pid=owner_pid, title=title))
        return True

    if not user32.EnumWindows(enum_proc, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return matches


def _post_close(user32, hwnd: int) -> None:
    ok = user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _stream_reader(stream, sink: deque[str]) -> None:
    try:
        for line in stream:
            sink.append(line.rstrip("\r\n"))
    except Exception as exc:  # pragma: no cover - diagnostic only
        sink.append(f"<stream reader failed: {exc}>")


def _start_stream_readers(proc: subprocess.Popen[str], max_lines: int) -> tuple[StreamTails, list[threading.Thread]]:
    tails = StreamTails(stdout=deque(maxlen=max_lines), stderr=deque(maxlen=max_lines))
    threads: list[threading.Thread] = []
    for stream, sink, name in (
        (proc.stdout, tails.stdout, "stdout"),
        (proc.stderr, tails.stderr, "stderr"),
    ):
        if stream is None:
            continue
        thread = threading.Thread(
            target=_stream_reader,
            args=(stream, sink),
            name=f"ui-close-probe-{name}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return tails, threads


def _join_readers(threads: list[threading.Thread], timeout: float = 0.25) -> None:
    deadline = time.perf_counter() + timeout
    for thread in threads:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        thread.join(remaining)


def _cleanup_process_tree(proc: subprocess.Popen[str]) -> str:
    alive_pids = _process_tree_alive(proc.pid)
    if not alive_pids:
        return "already-exited"
    processes = []
    for pid in reversed(alive_pids):
        try:
            processes.append(psutil.Process(pid))
        except psutil.Error:
            pass
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(processes, timeout=3.0)
    if not alive:
        return "terminated"
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(alive, timeout=3.0)
    return "kill-timeout" if alive else "killed"


def _wait_process_tree_exit(root_pid: int, timeout: float) -> tuple[bool, list[int]]:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        alive = _process_tree_alive(root_pid)
        if not alive:
            return True, []
        time.sleep(0.05)
    return False, _process_tree_alive(root_pid)


def _cleanup_process(proc: subprocess.Popen[str]) -> str:
    if proc.poll() is not None:
        return "already-exited"
    try:
        proc.terminate()
        proc.wait(timeout=3.0)
        return "terminated"
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            return "kill-timeout"
        return "killed"


def _resolve_command(args: argparse.Namespace) -> tuple[list[str], str]:
    scenario_args: list[str] = []
    if args.scenario != "idle":
        scenario_args = ["--scenario", args.scenario]
        if args.exe is not None:
            raise ValueError("--scenario is only supported with a Python entry")
        if args.entry is None:
            args.entry = TOOLS_DIR / "ui_close_scenario_entry.py"

    if args.exe is not None:
        exe = args.exe.resolve()
        if not exe.exists():
            raise FileNotFoundError(f"exe not found: {exe}")
        return [str(exe), *args.entry_args], "exe"

    entry = (args.entry or (ROOT / "main.py")).resolve()
    if not entry.exists():
        candidates = sorted((ROOT / "dist").glob("LoopMaster*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return [str(candidates[0]), *args.entry_args], "auto-exe"
        raise FileNotFoundError(f"entry not found: {entry}")

    python = args.python.resolve() if args.python is not None else Path(sys.executable).resolve()
    if not python.exists():
        raise FileNotFoundError(f"python not found: {python}")
    return [str(python), str(entry), *scenario_args, *args.entry_args], (
        f"python-entry:{args.scenario}" if args.scenario != "idle" else "python-entry"
    )


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) if not existing else str(ROOT) + os.pathsep + existing
    return env


def _print_tails(tails: StreamTails) -> None:
    def safe(text: str) -> str:
        encoding = sys.stdout.encoding or "utf-8"
        return text.encode(encoding, "replace").decode(encoding, "replace")

    if tails.stdout:
        print("child_stdout_tail:")
        for line in tails.stdout:
            print(f"  {safe(line)}")
    if tails.stderr:
        print("child_stderr_tail:")
        for line in tails.stderr:
            print(f"  {safe(line)}")


def _print_result(
    status: str,
    *,
    reason: str,
    command_kind: str,
    pid: int | None,
    returncode: int | None,
    launch_elapsed: float,
    window_elapsed: float | None,
    close_elapsed: float | None,
    total_elapsed: float,
    window: WindowInfo | None,
    cleanup: str | None = None,
) -> None:
    parts = [
        status,
        "ui-close-process",
        f"reason={reason}",
        f"command={command_kind}",
    ]
    if pid is not None:
        parts.append(f"pid={pid}")
    if returncode is not None:
        parts.append(f"returncode={returncode}")
    parts.append(f"launch={_fmt_ms(launch_elapsed)}")
    if window_elapsed is not None:
        parts.append(f"window={_fmt_ms(window_elapsed)}")
    if close_elapsed is not None:
        parts.append(f"close_to_exit={_fmt_ms(close_elapsed)}")
    parts.append(f"total={_fmt_ms(total_elapsed)}")
    if window is not None:
        parts.append(f"hwnd=0x{window.hwnd:X}")
        parts.append(f"window_pid={window.pid}")
        parts.append(f"title={window.title!r}")
    if cleanup:
        parts.append(f"cleanup={cleanup}")
    print(" ".join(parts), flush=True)


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    if os.name != "nt":
        _print_result(
            "FAIL",
            reason="unsupported-os",
            command_kind="none",
            pid=None,
            returncode=None,
            launch_elapsed=0.0,
            window_elapsed=None,
            close_elapsed=None,
            total_elapsed=time.perf_counter() - started,
            window=None,
        )
        return 2

    user32 = _load_user32()
    runtime_dir = args.cwd.resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    try:
        command, command_kind = _resolve_command(args)
    except Exception as exc:
        _print_result(
            "FAIL",
            reason=f"resolve-command:{exc}",
            command_kind="none",
            pid=None,
            returncode=None,
            launch_elapsed=0.0,
            window_elapsed=None,
            close_elapsed=None,
            total_elapsed=time.perf_counter() - started,
            window=None,
        )
        return 2

    launch_started = time.perf_counter()
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(runtime_dir),
            env=_build_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except Exception as exc:
        _print_result(
            "FAIL",
            reason=f"launch:{exc}",
            command_kind=command_kind,
            pid=None,
            returncode=None,
            launch_elapsed=time.perf_counter() - launch_started,
            window_elapsed=None,
            close_elapsed=None,
            total_elapsed=time.perf_counter() - started,
            window=None,
        )
        return 2

    tails, reader_threads = _start_stream_readers(proc, args.log_tail_lines)
    launch_elapsed = time.perf_counter() - launch_started
    window_started = time.perf_counter()
    window: WindowInfo | None = None

    while time.perf_counter() - window_started < args.startup_timeout:
        returncode = proc.poll()
        if returncode is not None:
            _join_readers(reader_threads)
            total_elapsed = time.perf_counter() - started
            _print_result(
                "FAIL",
                reason="exited-before-window",
                command_kind=command_kind,
                pid=proc.pid,
                returncode=returncode,
                launch_elapsed=launch_elapsed,
                window_elapsed=time.perf_counter() - window_started,
                close_elapsed=None,
                total_elapsed=total_elapsed,
                window=None,
            )
            _print_tails(tails)
            return 1

        matches = _find_windows_for_pids(user32, _process_tree_pids(proc.pid), args.title_substring)
        if matches:
            window = matches[0]
            break
        time.sleep(args.poll_interval)

    window_elapsed = time.perf_counter() - window_started
    if window is None:
        cleanup = _cleanup_process_tree(proc)
        _join_readers(reader_threads)
        total_elapsed = time.perf_counter() - started
        _print_result(
            "FAIL",
            reason="window-timeout",
            command_kind=command_kind,
            pid=proc.pid,
            returncode=proc.poll(),
            launch_elapsed=launch_elapsed,
            window_elapsed=window_elapsed,
            close_elapsed=None,
            total_elapsed=total_elapsed,
            window=None,
            cleanup=cleanup,
        )
        _print_tails(tails)
        return 1

    if args.settle > 0:
        time.sleep(args.settle)
        if proc.poll() is not None:
            _join_readers(reader_threads)
            total_elapsed = time.perf_counter() - started
            _print_result(
                "FAIL",
                reason="exited-before-close",
                command_kind=command_kind,
                pid=proc.pid,
                returncode=proc.returncode,
                launch_elapsed=launch_elapsed,
                window_elapsed=window_elapsed,
                close_elapsed=None,
                total_elapsed=total_elapsed,
                window=window,
            )
            _print_tails(tails)
            return 1

    close_started = time.perf_counter()
    try:
        _post_close(user32, window.hwnd)
    except Exception as exc:
        cleanup = _cleanup_process_tree(proc)
        _join_readers(reader_threads)
        total_elapsed = time.perf_counter() - started
        _print_result(
            "FAIL",
            reason=f"post-close:{exc}",
            command_kind=command_kind,
            pid=proc.pid,
            returncode=proc.poll(),
            launch_elapsed=launch_elapsed,
            window_elapsed=window_elapsed,
            close_elapsed=time.perf_counter() - close_started,
            total_elapsed=total_elapsed,
            window=window,
            cleanup=cleanup,
        )
        _print_tails(tails)
        return 1

    exited, alive_pids = _wait_process_tree_exit(proc.pid, args.exit_timeout)
    if not exited:
        close_elapsed = time.perf_counter() - close_started
        cleanup = _cleanup_process_tree(proc)
        _join_readers(reader_threads)
        total_elapsed = time.perf_counter() - started
        _print_result(
            "FAIL",
            reason=f"exit-timeout alive={alive_pids}",
            command_kind=command_kind,
            pid=proc.pid,
            returncode=proc.poll(),
            launch_elapsed=launch_elapsed,
            window_elapsed=window_elapsed,
            close_elapsed=close_elapsed,
            total_elapsed=total_elapsed,
            window=window,
            cleanup=cleanup,
        )
        _print_tails(tails)
        return 1
    returncode = proc.poll()
    if returncode is None:
        returncode = 0

    close_elapsed = time.perf_counter() - close_started
    _join_readers(reader_threads)
    total_elapsed = time.perf_counter() - started
    if returncode != 0:
        _print_result(
            "FAIL",
            reason="nonzero-exit",
            command_kind=command_kind,
            pid=proc.pid,
            returncode=returncode,
            launch_elapsed=launch_elapsed,
            window_elapsed=window_elapsed,
            close_elapsed=close_elapsed,
            total_elapsed=total_elapsed,
            window=window,
        )
        _print_tails(tails)
        return 1

    _print_result(
        "PASS",
        reason="closed-and-exited",
        command_kind=command_kind,
        pid=proc.pid,
        returncode=returncode,
        launch_elapsed=launch_elapsed,
        window_elapsed=window_elapsed,
        close_elapsed=close_elapsed,
        total_elapsed=total_elapsed,
        window=window,
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch LoopMaster, close the Qt MainWindow normally, and verify process exit.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--entry", type=Path, default=None, help="Python entry script; defaults to ROOT/main.py")
    target.add_argument("--exe", type=Path, default=None, help="Packaged LoopMaster exe to launch instead of Python")
    parser.add_argument("--python", type=Path, default=None, help="Python executable for --entry; defaults to sys.executable")
    parser.add_argument("--cwd", type=Path, default=DEFAULT_RUNTIME_DIR, help="Child working directory")
    parser.add_argument("--title-substring", default=DEFAULT_TITLE_SUBSTRING, help="Visible window title substring")
    parser.add_argument("--startup-timeout", type=float, default=25.0, help="Seconds to wait for the MainWindow")
    parser.add_argument("--exit-timeout", type=float, default=8.0, help="Seconds to wait after WM_CLOSE")
    parser.add_argument("--settle", type=float, default=0.5, help="Seconds to wait after the window appears before closing")
    parser.add_argument("--poll-interval", type=float, default=0.05, help="Window polling interval in seconds")
    parser.add_argument("--log-tail-lines", type=int, default=60, help="Child stdout/stderr tail lines to show on failure")
    parser.add_argument(
        "--scenario",
        choices=("idle", "sampling", "slow-sampling", "serial-worker"),
        default="idle",
        help="Use a synthetic MainWindow scenario before closing the process.",
    )
    parser.add_argument("entry_args", nargs=argparse.REMAINDER, help="Arguments passed to the entry/exe after --")
    args = parser.parse_args(argv)
    if args.entry_args and args.entry_args[0] == "--":
        args.entry_args = args.entry_args[1:]
    args.startup_timeout = max(0.1, args.startup_timeout)
    args.exit_timeout = max(0.1, args.exit_timeout)
    args.settle = max(0.0, args.settle)
    args.poll_interval = max(0.01, args.poll_interval)
    args.log_tail_lines = max(1, args.log_tail_lines)
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
