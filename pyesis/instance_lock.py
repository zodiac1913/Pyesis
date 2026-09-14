from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys


LOCK_FILENAME = "pyesis.lock"


@dataclass
class InstanceLock:
    path: Path
    fd: int

    def release(self) -> None:
        try:
            _unlock(self.fd)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def lock_path(state_directory: Path) -> Path:
    return state_directory / LOCK_FILENAME


def acquire_instance_lock(state_directory: Path) -> InstanceLock | None:
    state_directory.mkdir(parents=True, exist_ok=True)
    path = lock_path(state_directory)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _lock_exclusive(fd)
    except OSError:
        os.close(fd)
        return None
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    return InstanceLock(path=path, fd=fd)


def _lock_exclusive(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def already_running_message() -> str:
    return "Pyesis is already running. Use the open window instead of starting a second copy."


def fatal_error_message(exc: BaseException) -> str:
    return f"Pyesis could not start.\n\n{type(exc).__name__}: {exc}"


def show_startup_dialog(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}", file=sys.stderr)
