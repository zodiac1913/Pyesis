from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
APP_NAME = "Pyesis"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a native Pyesis artifact for the current platform.")
    parser.add_argument("--tag", required=True, help="Release tag, for example v2026.06.03.01")
    return parser.parse_args()


def platform_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def build_with_pyinstaller(current_platform: str) -> Path:
    # Use the current interpreter so PyInstaller resolves from the active environment.
    command = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--name", APP_NAME]

    data_separator = ";" if current_platform == "windows" else ":"
    command.extend(["--add-data", f"assets{data_separator}assets"])

    if current_platform == "windows":
        command.extend(["--onefile", "--windowed", "--icon", "assets/pyesis.ico"])
    elif current_platform == "macos":
        command.extend(["--windowed", "main.py"])
        subprocess.run(command, cwd=ROOT, check=True)
        return DIST_DIR / f"{APP_NAME}.app"
    else:
        command.extend(["--onefile", "--windowed"])

    command.append("main.py")
    subprocess.run(command, cwd=ROOT, check=True)
    suffix = ".exe" if current_platform == "windows" else ""
    return DIST_DIR / f"{APP_NAME}{suffix}"


def archive_artifact(built_path: Path, tag: str, current_platform: str) -> Path:
    arch = platform.machine().lower().replace("x86_64", "x64").replace("amd64", "x64").replace("aarch64", "arm64")
    if current_platform == "windows":
        target = DIST_DIR / f"{APP_NAME}-{tag}-{current_platform}-{arch}.exe"
        shutil.copy2(built_path, target)
        return target

    if current_platform == "macos":
        target = DIST_DIR / f"{APP_NAME}-{tag}-{current_platform}-{arch}.zip"
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
            for child in built_path.rglob("*"):
                archive.write(child, child.relative_to(DIST_DIR))
        return target

    target = DIST_DIR / f"{APP_NAME}-{tag}-{current_platform}-{arch}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.add(built_path, arcname=built_path.name)
    return target


def main() -> None:
    args = parse_args()
    current_platform = platform_name()
    built_path = build_with_pyinstaller(current_platform)
    artifact = archive_artifact(built_path, args.tag, current_platform)
    print(artifact)


if __name__ == "__main__":
    main()