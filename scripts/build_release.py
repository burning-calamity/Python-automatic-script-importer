"""Build and validate AutoScripts release artifacts (requires build and twine)."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [sys.executable, "-m", "build"],
    ]
    # Run build separately, then obtain the generated wheel/sdist filenames.
    for command in commands[:2]:
        subprocess.run(command, cwd=root, check=True)
    artifacts = sorted((root / "dist").glob("*"))
    if not artifacts:
        raise SystemExit("No distributions generated")
    subprocess.run([sys.executable, "-m", "twine", "check", *map(str, artifacts)],
                   cwd=root, check=True)
    print("Validated artifacts:")
    for file in artifacts:
        print(file)


if __name__ == "__main__":
    main()
