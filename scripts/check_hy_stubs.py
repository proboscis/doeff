"""Compare whether the .pyi next to a .hy being committed is the same as one rebuilt from the current .hy (commit hook).

A .pyi is a generated artifact made from the .hy by doeff_hy._static_stub, and each package's test_generated_stubs compares "rebuilt == committed" daily.
A commit that changes only the .hy and does not rebuild the .pyi would be made failing later by that test (afaf956cd -- kube.hy), so
fire the same comparison at commit time and stop it.

A script with only the standard library (the hook's python is 3.12). doeff_hy is called in the tree's environment (uv run).
Usage: python scripts/check_hy_stubs.py <.hy path from the repo root>...
A .hy without a .pyi, and a hand-written .pyi, are not compared (the tool's --check excludes them). A .hy outside a package's source dir (packages/<name>/src)
is dropped even if passed.
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_root_of(path: Path) -> Path | None:
    """The source dir of a package with .hy (packages/<name>/src). None if outside."""
    parts = path.relative_to(ROOT).parts
    if len(parts) > 3 and parts[0] == "packages" and parts[2] == "src":
        return ROOT / "packages" / parts[1] / "src"
    return None


def main(argv: list[str]) -> int:
    by_root: dict[Path, list[Path]] = defaultdict(list)
    for name in argv:
        path = (ROOT / name).resolve() if not Path(name).is_absolute() else Path(name)
        if path.suffix != ".hy" or not path.with_suffix(".pyi").exists():
            continue
        root = source_root_of(path)
        if root is not None:
            by_root[root].append(path)
    failed = False
    for root, paths in by_root.items():
        done = subprocess.run(
            ["uv", "run", "python", "-m", "doeff_hy._static_stub", "--check", "--root", str(root), *map(str, paths)], cwd=ROOT, check=False
        )
        failed = failed or done.returncode != 0
        if done.returncode != 0:
            fix = " ".join(str(p.relative_to(ROOT)) for p in paths)
            print(
                f"doeff commit hook: the .pyi next to a .hy is stale. Rebuild and stage: "
                f"(cd {root.relative_to(ROOT)} && uv run python -m doeff_hy._static_stub --write --root . <the .hy shown above>) -- candidates: {fix}",
                file=sys.stderr,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
