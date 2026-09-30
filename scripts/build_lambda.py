"""Build the Linux ARM64 Lambda asset using pinned, platform-independent wheels."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "build" / "handler"


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--platform",
            "manylinux2014_aarch64",
            "--python-version",
            "3.12",
            "--target",
            str(TARGET),
            "-r",
            str(ROOT / "src" / "handler" / "requirements.txt"),
        ],
        check=True,
    )
    for source in (ROOT / "src" / "handler").glob("*.py"):
        shutil.copy2(source, TARGET / source.name)


if __name__ == "__main__":
    main()
