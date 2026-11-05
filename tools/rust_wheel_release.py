"""Upload doeff's Rust package wheels to a store outside the machine (the public GitHub release `rust-wheels`) -- one of the store's writers
(addendum to ADR-DOE-BUILD-001 R5; design docs/design/rust-wheel-distribution). It runs only inside doeff's GitHub Actions
(.github/workflows/rust-wheels.yml), and its only credential is the Actions GITHUB_TOKEN (contents: write).

For each package it runs the build backend (tools/doeff_cargo_backend.py) through `uv build --wheel`. The backend looks the wheel up in the store, so
if a wheel with the same key is already there it is fetched and nothing more happens (reported source remote; not uploaded). Otherwise the backend builds it, and only wheels
whose source is built are uploaded as the asset `<package>-<key>.whl` (the local store dir name + .whl, the same name the backend looks up).
An asset with the same name is never overwritten (if it exists, nothing is uploaded).
After uploading, if the assets exceed KEEP_ASSETS, the oldest are deleted (a release holds at most 1000 assets).

Usage: python tools/rust_wheel_release.py --tag rust-wheels --python 3.14t packages/doeff-vm packages/doeff-linter ...
(requires env GH_TOKEN and GITHUB_REPOSITORY, which Actions provides)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Number of assets to keep (below the 1000 limit, leaving headroom so a single upload cannot exceed it).
KEEP_ASSETS = 800


@dataclass(frozen=True)
class Reported:
    """One line of the build backend report: project = package name, wheel = the wheel in the local store, source = origin (local, remote, built)."""

    project: str
    wheel: Path
    source: str

    @property
    def asset(self) -> str:
        """The store asset name (local store dir name `<package>-<key>` + .whl, the name the backend's _remote_wheel looks up)."""
        return f"{self.wheel.parent.name}.whl"


def _gh(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run gh and return the result (the caller reads the return code)."""
    return subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)


def _ensure_release(repo: str, tag: str) -> None:
    """Create the store release if it does not exist (a container for assets, not a version announcement, so it is not marked latest)."""
    if _gh("release", "view", tag, "--repo", repo).returncode == 0:
        return
    made = _gh(
        "release", "create", tag, "--repo", repo, "--prerelease", "--latest=false", "--title", "Rust wheels (build cache)",
        "--notes", "Store of Rust wheels that doeff's build backend (tools/doeff_cargo_backend.py) looks up. Asset name = <package>-<key>.whl. "
        "Written only by CI (.github/workflows/rust-wheels.yml). Design = docs/design/rust-wheel-distribution.",
    )
    if made.returncode != 0:
        sys.exit(f"rust_wheel_release: cannot create release {tag} - {made.stderr.strip()}")


def _build(package: Path, python: str, store: Path) -> tuple[Reported, ...]:
    """Run the package through the build backend and return the backend's report lines (the backend looks up the store; if the wheel exists it is only fetched, not built). Local store = store
    (an empty dir used by this run only; the reported wheels stay there until uploading finishes)."""
    with tempfile.TemporaryDirectory(prefix="rust-wheel-release-") as work:
        report = Path(work) / "report.jsonl"
        env = {**os.environ, "DOEFF_WHEEL_CACHE": str(store), "DOEFF_WHEEL_REPORT": str(report)}
        done = subprocess.run(["uv", "build", "--wheel", "--python", python, "--out-dir", str(Path(work) / "out"), str(package)], env=env, check=False)
        if done.returncode != 0:
            sys.exit(f"rust_wheel_release: uv build of {package} exited with rc {done.returncode}")
        rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()] if report.exists() else []
    return tuple(Reported(project=row["project"], wheel=Path(row["wheel"]), source=row["source"]) for row in rows)


def _upload(repo: str, tag: str, reported: Reported) -> None:
    """Upload the wheel the backend built under its asset name. If the same name already exists, report it without overwriting (another run with the same key uploaded first)."""
    with tempfile.TemporaryDirectory(prefix="rust-wheel-asset-") as work:
        asset = Path(work) / reported.asset
        shutil.copy2(reported.wheel, asset)
        sent = _gh("release", "upload", tag, str(asset), "--repo", repo)
    if sent.returncode == 0:
        print(f"rust_wheel_release: uploaded - {reported.asset} ({reported.wheel.name}, {reported.wheel.stat().st_size} bytes)")
    elif "already exists" in sent.stderr:
        print(f"rust_wheel_release: same name exists (not overwritten) - {reported.asset}")
    else:
        sys.exit(f"rust_wheel_release: cannot upload {reported.asset} - {sent.stderr.strip()}")


def _prune(repo: str, tag: str) -> None:
    """If the release assets exceed KEEP_ASSETS, delete the excess, oldest creation time first."""
    release = _gh("api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".id")
    listed = _gh("api", "--paginate", f"repos/{repo}/releases/{release.stdout.strip()}/assets", "--jq", ".[] | [.id, .created_at, .name] | @tsv")
    if release.returncode != 0 or listed.returncode != 0:
        sys.exit(f"rust_wheel_release: cannot read the asset list - {release.stderr.strip()} {listed.stderr.strip()}")
    assets = sorted((line.split("\t") for line in listed.stdout.splitlines() if line), key=lambda row: row[1])
    for asset_id, _, name in assets[: max(0, len(assets) - KEEP_ASSETS)]:
        gone = _gh("api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}")
        print(f"rust_wheel_release: deleted old asset - {name}" if gone.returncode == 0 else f"rust_wheel_release: cannot delete {name} - {gone.stderr.strip()}")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Upload doeff Rust package wheels to the store (release rust-wheels)")
    parser.add_argument("--tag", required=True, help="tag of the store release (rust-wheels)")
    parser.add_argument("--python", required=True, help="Python to build with (same ABI as the consuming venv, 3.14t)")
    parser.add_argument("packages", nargs="+", type=Path, help="package dirs to run through the build backend")
    args = parser.parse_args(argv)
    repo = os.environ["GITHUB_REPOSITORY"]
    _ensure_release(repo, args.tag)
    with tempfile.TemporaryDirectory(prefix="rust-wheel-store-") as store:
        reported = tuple(row for package in args.packages for row in _build(package, args.python, Path(store)))
        for row in reported:
            if row.source == "built":
                _upload(repo, args.tag, row)
            else:
                print(f"rust_wheel_release: already in the store or local (not uploaded) - {row.asset}, {row.source}")
    _prune(repo, args.tag)


if __name__ == "__main__":
    main(sys.argv[1:])
