"""据わっている Claude Code 本体から、この設計が依っている契約の逐語を読む 3 値の計器。

これは dotfiles `agent/tests/check_native_claude_home_contract.py` の**雛形**
(設計段の実験用)。先例 = dotfiles `agent/tests/check_native_worktree_contract.py`。

  棄権 abstain … 本体を同定できない(別の機体 / 本体が無い / tree の層)
  緑   green   … 逐語が揃っている(設計の前提 P3 が生きている)
  赤   red     … 同定できたのに逐語が動いた(前提が偽になった日を名指す)

⚠ 機体に縛られる(MACHINE_BOUND)。tree の層では走らせない。
"""
from __future__ import annotations

import sys

MACHINE_BOUND = True
DEFAULT_BODY = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"

# 同定 — 製品の公開の綴り(版が上がっても動きにくい)
IDENTIFY = b"CLAUDE_CONFIG_DIR"

# ⚠ 追補 2026-09-21T07:01Z(依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 が入れた・出典 = 郵便
#   lt-C22T2AV9VGX520WYFCEB0Q8X8B): 下の逐語は **2.1.263 の縮めた識別子**で pin してある。
#   6 本すべてが版で変わる名(Ke/Se・ae・wgr/CN・Ah/Se/HS・N・Sgr)に依り、局所変数の名も動く
#   (2.1.278 では t/v/q/d → n/O/st/g)。⇒ **写す時は識別子の位置を形の pin(\w+)に置き換える**こと。
#   綴りのまま会社 Mac(2.1.278)へ持って行くと **偽の赤**になる。この pod(2.1.263)では 2026-09-21T07:01Z に green。

# この設計が依っている契約の逐語(design.md §2 / evidence/body_contract.log)
EXPECTED: tuple[tuple[str, bytes], ...] = (
    ("user-memory-lives-under-the-config-dir",
     b'case"User":return Ke(Se(),"CLAUDE.md")'),
    ("user-layer-drops-symlink-and-hardlink",
     b'if(t==="User"&&!v)try{let q=await ae().lstat(e);'
     b'if(d===0&&q.isSymbolicLink()||(q.nlink??1)>1&&q.isFile())return[]}catch{}'),
    ("the-gate-fires-only-for-local-agent",
     b'function wgr(){return CN()!=="local-agent"}'),
    ("user-skills-live-under-the-config-dir",
     b'let r=Ah(Se(),"skills"),o=Ah(HS(),".claude","skills")'),
    ("skills-entries-accept-a-symlink",
     b'if(!N.isDirectory()&&!N.isSymbolicLink())return null;'),
    ("md-excludes-applies-to-the-user-layer",
     b'function Sgr(e,t){if(t!=="User"&&t!=="Project"&&t!=="Local")return!1;'),
)

CHUNK = 1 << 22


def scan(path: str, needles: tuple[bytes, ...]) -> set[bytes]:
    """大きい実行体を塊で読み、境界をまたぐ一致を落とさない。"""
    found: set[bytes] = set()
    overlap = max(len(n) for n in needles) - 1
    tail = b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            window = tail + chunk
            for needle in needles:
                if needle not in found and needle in window:
                    found.add(needle)
            if len(found) == len(needles):
                break
            tail = window[-overlap:] if overlap else b""
    return found


def inspect(path: str) -> dict:
    try:
        found = scan(path, (IDENTIFY,) + tuple(v for _, v in EXPECTED))
    except OSError as exc:
        return {"verdict": "abstain", "why": f"読めない: {exc}", "missing": []}
    if IDENTIFY not in found:
        return {"verdict": "abstain", "why": "本体を同定できない", "missing": []}
    missing = [name for name, verbatim in EXPECTED if verbatim not in found]
    if missing:
        return {"verdict": "red", "why": "同定できたのに逐語が動いた", "missing": missing}
    return {"verdict": "green", "why": "逐語が揃っている", "missing": []}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BODY
    result = inspect(target)
    print(f"{result['verdict']}\t{target}\t{result['why']}"
          + (f"\t欠け={result['missing']}" if result["missing"] else ""))
    sys.exit(0 if result["verdict"] in ("green", "abstain") else 1)
