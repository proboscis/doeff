from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path


def _read_wheel_metadata(artifact: Path) -> str:
    with zipfile.ZipFile(artifact) as wheel:
        metadata_entry = next(
            (name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_entry is None:
            raise RuntimeError(f"No METADATA entry found in wheel: {artifact}")
        return wheel.read(metadata_entry).decode("utf-8", errors="replace")


def _read_sdist_pkg_info(artifact: Path) -> str:
    with tarfile.open(artifact, mode="r:gz") as sdist:
        pkg_info_entry = next(
            (
                member
                for member in sdist.getmembers()
                if member.isfile() and member.name.endswith("PKG-INFO")
            ),
            None,
        )
        if pkg_info_entry is None:
            raise RuntimeError(f"No PKG-INFO entry found in sdist: {artifact}")
        extracted = sdist.extractfile(pkg_info_entry)
        if extracted is None:
            raise RuntimeError(f"Could not extract PKG-INFO from sdist: {artifact}")
        return extracted.read().decode("utf-8", errors="replace")


def _find_leaks(metadata: str) -> list[str]:
    leaks: list[str] = []
    requires_dist_lines = [
        line for line in metadata.splitlines() if line.startswith("Requires-Dist:")
    ]
    for line in requires_dist_lines:
        if "@ file:" in line or "@file:" in line:
            leaks.append(line)
        if "file://" in line:
            leaks.append(line)
        if re.search(r"@\s*(\.{1,2}/|/)", line):
            leaks.append(line)
    return leaks


def _read_metadata(artifact: Path) -> str:
    if artifact.suffix == ".whl":
        return _read_wheel_metadata(artifact)
    if artifact.suffixes[-2:] == [".tar", ".gz"]:
        return _read_sdist_pkg_info(artifact)
    raise RuntimeError(f"Unsupported artifact type: {artifact}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify built distributions do not leak local workspace path dependencies.",
    )
    parser.add_argument("artifacts", nargs="+", help="Wheel or sdist artifact paths")
    args = parser.parse_args()

    has_errors = False
    for artifact_arg in args.artifacts:
        artifact = Path(artifact_arg)
        if not artifact.exists():
            print(f"[FAIL] Missing artifact: {artifact}")
            has_errors = True
            continue

        try:
            metadata = _read_metadata(artifact)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] Could not inspect {artifact}: {exc}")
            has_errors = True
            continue

        leaks = _find_leaks(metadata)
        if leaks:
            print(f"[FAIL] {artifact} contains local dependency leaks:")
            for leak in leaks:
                print(f"  - {leak}")
            has_errors = True
        else:
            print(f"[OK] {artifact}")

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
