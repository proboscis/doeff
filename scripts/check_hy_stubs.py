"""commit する .hy の隣の .pyi が、今の .hy から作り直した物と同じかを照らす(commit の hook — card ki-5755818ef0d8)。

.pyi は doeff_hy.static_stub が .hy から作る生成物で、各 package の test_generated_stubs が「作り直した物 == commit された物」を
日次に照らす。.hy だけを変えて .pyi を作り直さない commit は、その test が後から failing にするので(afaf956cd — kube.hy)、
commit の時に同じ照らしを撃って止める。

標準の library だけの script(hook の python は 3.12)。doeff_hy は木の環境(uv run)で呼ぶ。
使い方: python scripts/check_hy_stubs.py <repo の根からの .hy の path>…
.pyi を持たない .hy・手で書いた .pyi は照らさない(道具の --check が外す)。package の source の dir(packages/<名>/src)の外の
.hy は渡されても捨てる。
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_root_of(path: Path) -> Path | None:
    """.hy を持つ package の source の dir(packages/<名>/src)。その外なら None。"""
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
            ["uv", "run", "python", "-m", "doeff_hy.static_stub", "--check", "--root", str(root), *map(str, paths)], cwd=ROOT, check=False
        )
        failed = failed or done.returncode != 0
        if done.returncode != 0:
            fix = " ".join(str(p.relative_to(ROOT)) for p in paths)
            print(
                f"doeff の commit の hook: .hy の隣の .pyi が古い。作り直して stage する: "
                f"(cd {root.relative_to(ROOT)} && uv run python -m doeff_hy.static_stub --write --root . <上に出た .hy>) — 対象の候補: {fix}",
                file=sys.stderr,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
