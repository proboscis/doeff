# /// script
# requires-python = ">=3.11"
# ///
"""doeff の外の repo(agora-controllers)が doeff_agents の何を使うかを数える。

doeff_agents の公開の名を外す時に、外の repo を先に直す必要がある名と module を知るため。
使い方: uv run --script agents_external_callers.py <repo> <rev> <inventory.json> <出力 JSON>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO, REV, INVENTORY, OUT = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])


def git(*args: str) -> str:
    """repo の rev の木を、作業木に触らずに読むため。"""
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, check=False).stdout


def main() -> None:
    listed = git("grep", "-l", "doeff_agents", REV, "--", "*.py", "*.hy", "*.pyi").splitlines()
    files = {line.split(":", 1)[1]: git("show", line) for line in listed}
    words = {f: (frozenset(re.findall(r"\w+", t)), frozenset(re.findall(r"[\w-]+", t))) for f, t in files.items()}
    inv = json.loads(INVENTORY.read_text())

    def users(name: str) -> list[str]:
        hy = name.replace("_", "-")
        return sorted(f for f, (w, hw) in words.items() if name in w or (f.endswith(".hy") and hy in hw))

    def importers(mod: str) -> list[str]:
        pat = re.compile(r"(?<![\w.])" + re.escape(mod) + r"(?![\w])")
        return sorted(f for f, t in files.items() if pat.search(t))

    out = {
        "rev": git("rev-parse", REV).strip(),
        "files": len(files),
        "top": {e["name"]: users(e["name"]) for e in inv["top"]},
        "modules": {m: importers(m) for m in inv["modules"]},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"files={len(files)} rev={out['rev'][:12]}")


if __name__ == "__main__":
    main()
