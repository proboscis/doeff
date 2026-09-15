#!/usr/bin/env python3
"""ACP(proboscis/agent-control-plane)の契約の写しを作る 1 つの口(段 11 lane 11e・agora-redesign #126)。

形 = schema registry と消費者の lock(Pact / protobuf の生成と同じ)。正本は ACP の
``docs/contracts/`` の 1 点で、この repo は

* pin を 1 か所で持つ — ``contracts.lock``(schema ``acp.contracts.lock.v1``): ACP の
  **commit 1 つ**と写しごとの sha256。方策の定義点はこの file ちょうど。
* 写しは **生成物** — この script が pin の commit から取り寄せて書く。手で直した写しは検が赤。

消費者は agentd(``packages/doeff-agents/src/doeff_agents/sessionhost/acp/``)。
姉妹の口 = agora-controllers の ``scripts/sync_contracts.hy``(同じ lock の形・同じ動詞)。

⚠ **正本の repo は private で、この repo は公開**。だから検は 2 段に分かれる:

* 写しの中で閉じる検(ACP の checkout が要らない)= 写しが lock の sha256 と一致する・契約が版と
  互換の規則を名乗る・``docs/contracts/reads.json`` の読む欄が写しに実在する・宣言 file が正本の
  在処を名乗る。既定 pytest(``packages/doeff-agents/tests/test_sessionhost_acp.py`` の写しの節)が
  この関数をそのまま呼ぶので、公開の CI でも走る。動詞 = ``--check-copies``。
* 正本との突合(ACP の checkout が要る)= pin の commit から取り寄せた**生成物との差分 0**。
  動詞 = ``--check``。checkout の無い機体では名指しで落ちる(黙って skip しない)。

動詞:
  python3 scripts/sync_contracts.py                  … 生成物の差分 0 の検(ACP の checkout が要る)
  python3 scripts/sync_contracts.py --check-copies    … 写しの中で閉じる検(checkout は要らない)
  python3 scripts/sync_contracts.py --write           … pin の commit から写しを生成し直す
  python3 scripts/sync_contracts.py --pin [<commit>]  … pin を進める(既定 = ACP の origin/main の先端)

ACP の checkout は env ``ACP_CHECKOUT``(既定 ``~/repos/agent-control-plane``)。
exit code: 0 = 一致 / 1 = 食い違い・実行環境の失敗。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LOCK_REL = "contracts.lock"
READS_REL = "docs/contracts/reads.json"
CONTRACTS_README_REL = "docs/contracts/README.md"
LOCK_SCHEMA = "acp.contracts.lock.v1"
READS_SCHEMA = "acp.contract-reads.v1"
COMPATIBILITY_RULE = "additive-only"
CANON_REPO = "proboscis/agent-control-plane"
COPY_KINDS = ("contract", "code")
DEFAULT_PIN_SPEC = "origin/main"
#: 食い違いを 1 段下の鍵まで名指す節(kind ごとに変わる理由が別だから)。
SECTIONS_READ_ONE_LEVEL_DEEPER = ("kinds", "intents")

ROOT = Path(__file__).resolve().parents[1]


# ---- 判断(純関数・I/O なし)------------------------------------------------


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit_spelling(commit: object) -> bool:
    """pin の commit は 40 桁の小文字 16 進ちょうど(短縮形は別の commit に化けうる)。"""
    return (
        isinstance(commit, str)
        and len(commit) == 40
        and all(c in "0123456789abcdef" for c in commit)
    )


def lock_problems(lock: dict) -> list[str]:
    """pin の file の形。中身(正本との一致)は別の検。"""
    problems: list[str] = []
    if lock.get("schema") != LOCK_SCHEMA:
        problems.append(f"{LOCK_REL} の schema が {LOCK_SCHEMA} でない")
    if lock.get("canonRepo") != CANON_REPO:
        problems.append(f"{LOCK_REL} の canonRepo が {CANON_REPO} でない")
    if not commit_spelling(lock.get("commit")):
        problems.append(f"{LOCK_REL} の commit が 40 桁の commit の綴りでない: {lock.get('commit')!r}")
    files = lock.get("files")
    if not isinstance(files, list) or not files:
        problems.append(f"{LOCK_REL} の files が空(写しの宣言が 1 本も無い)")
        return problems
    seen: set[str] = set()
    for entry in files:
        for field in ("kind", "canonPath", "copyPath", "sha256"):
            if not entry.get(field):
                problems.append(f"{LOCK_REL} の行に {field} が無い: {json.dumps(entry, ensure_ascii=False)}")
        kind = entry.get("kind")
        if kind and kind not in COPY_KINDS:
            problems.append(f"{LOCK_REL} の kind が {' / '.join(COPY_KINDS)} でない: {kind!r}")
        rel = entry.get("copyPath")
        if rel:
            if rel in seen:
                problems.append(f"{LOCK_REL} が同じ写しを 2 度宣言している: {rel}")
            seen.add(rel)
    return problems


def path_exists(document: object, path: str) -> bool:
    """読む欄の path(dot 区切り)が写しに実在するか。* は 1 段の全部・数字は list の位置。"""
    if not path.strip():
        return False
    frontier: list[object] = [document]
    for part in path.split("."):
        nxt: list[object] = []
        for node in frontier:
            if part == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict):
                if part in node:
                    nxt.append(node[part])
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                nxt.append(node[int(part)])
        frontier = nxt
        if not frontier:
            return False
    return True


def differing_sections(canon: bytes, copy: bytes) -> list[str]:
    """食い違う節の名(最上位の鍵。kinds と intents は 1 段下まで)。JSON として読めなければ空。"""
    try:
        left = json.loads(canon.decode("utf-8"))
        right = json.loads(copy.decode("utf-8"))
    except Exception:
        return []
    if not isinstance(left, dict) or not isinstance(right, dict):
        return []
    names: list[str] = []
    for key in sorted(set(left) | set(right)):
        canon_part = left.get(key)
        copy_part = right.get(key)
        if canon_part == copy_part:
            continue
        if key in SECTIONS_READ_ONE_LEVEL_DEEPER and isinstance(canon_part, dict) and isinstance(copy_part, dict):
            for sub in sorted(set(canon_part) | set(copy_part)):
                if canon_part.get(sub) != copy_part.get(sub):
                    names.append(f"{key}.{sub}")
        else:
            names.append(key)
    return names


def diff_summary(canon: bytes, copy: bytes) -> str:
    """生成物と写しの差を人が読める形に(節の名 + 差分の頭 12 行)。"""
    sections = differing_sections(canon, copy)
    diff = list(
        difflib.unified_diff(
            copy.decode("utf-8", "replace").splitlines(True),
            canon.decode("utf-8", "replace").splitlines(True),
            fromfile="写し(働く木)",
            tofile="生成物(pin の commit の正本)",
            n=1,
        )
    )
    head = (
        f"写しが生成物と byte 一致しない(生成物 {len(canon)} byte / 写し {len(copy)} byte)"
        " — 生成の口で写し直す: python3 scripts/sync_contracts.py --write"
    )
    section_line = f"\n      食い違う節: {', '.join(sections)}" if sections else ""
    return head + section_line + "\n      " + "      ".join(diff[:12])


def contract_problems(contract_id: str, copy: bytes, reads: dict) -> list[str]:
    """契約の写しが名乗るべきもの — 版・互換の規則・読む欄の実在。"""
    problems: list[str] = []
    try:
        document = json.loads(copy.decode("utf-8"))
    except Exception as exc:  # 壊れた JSON は名指しで赤(黙って通さない)
        return [f"契約の写しが JSON として読めない: {exc}"]
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append("契約の写しが version(1 以上の整数)を名乗っていない")
    compatibility = document.get("compatibility")
    if not isinstance(compatibility, dict) or compatibility.get("rule") != COMPATIBILITY_RULE:
        problems.append(f"契約の写しが compatibility.rule = {COMPATIBILITY_RULE} を名乗っていない")
    rows = reads.get("reads", {})
    if contract_id not in rows:
        problems.append(f"{READS_REL} に契約 {contract_id} の行が無い(読まないなら [] と明示する)")
    else:
        for path in rows[contract_id]:
            if not path_exists(document, path):
                problems.append(f"{READS_REL} が読む欄が写しに無い: {contract_id} の {path}")
    return problems


def reads_coverage_problems(lock: dict, reads: dict) -> list[str]:
    """読む欄の宣言は契約の写しの集合ちょうど(余計な行 = 消えた契約を読んでいる)。"""
    problems: list[str] = []
    if reads.get("schema") != READS_SCHEMA:
        problems.append(f"{READS_REL} が無いか schema が {READS_SCHEMA} でない")
    declared = {Path(e["copyPath"]).stem for e in lock.get("files", []) if e.get("kind") == "contract"}
    for extra in sorted(set(reads.get("reads", {})) - declared):
        problems.append(f"{READS_REL} が写しに無い契約を読んでいる: {extra}")
    return problems


def copy_problems(lock: dict, copies: dict[str, bytes], reads: dict, contracts_readme: str) -> list[str]:
    """写しの中で閉じる検 — ACP の checkout が要らない側(公開の CI と既定 pytest が呼ぶ)。

    写しが lock の sha256 ちょうどであること = 「pin の commit から生成し直したものと同じ」。
    lock 自身が正本と合っているかは canon_problems(正本の checkout が要る)が撃つ。
    """
    problems = lock_problems(lock)
    if problems:
        return problems
    for entry in lock["files"]:
        rel = entry["copyPath"]
        label = f"[{entry['kind']} {rel}]"
        copy = copies.get(rel)
        if copy is None:
            problems.append(f"{label} 写しが無い(生成の口で作る: python3 scripts/sync_contracts.py --write)")
            continue
        if sha256_of(copy) != entry["sha256"]:
            problems.append(
                f"{label} 写しが {LOCK_REL} の pin と一致しない"
                "(写しを手で直した — 正本を直して pin を進める: --pin)"
            )
        if entry["kind"] == "contract":
            problems.extend(f"{label} {p}" for p in contract_problems(Path(rel).stem, copy, reads))
            marker = f"acp-contract-canon: {CANON_REPO}:{entry['canonPath']}"
            if marker not in contracts_readme:
                problems.append(f"{label} {CONTRACTS_README_REL} が正本の名乗りを持たない: {marker}")
    problems.extend(reads_coverage_problems(lock, reads))
    return problems


def canon_problems(lock: dict, canon_map: dict[str, bytes], copies: dict[str, bytes]) -> list[str]:
    """正本との突合 — pin の commit の中身が lock の sha256 と、そして写しと byte 一致すること。"""
    problems: list[str] = []
    for entry in lock["files"]:
        rel = entry["copyPath"]
        label = f"[{entry['kind']} {rel}]"
        canon = canon_map.get(rel)
        if canon is None:
            problems.append(f"{label} pin の commit に正本が無い: {entry['canonPath']}")
            continue
        if sha256_of(canon) != entry["sha256"]:
            problems.append(
                f"{label} {LOCK_REL} の sha256 が pin の commit の正本と一致しない"
                "(pin を手で直した — --pin で書き直す)"
            )
        copy = copies.get(rel)
        if copy is not None and copy != canon:
            problems.append(f"{label} {diff_summary(canon, copy)}")
    return problems


# ---- 実 I/O(正本の取り寄せと file の読み書き)------------------------------


def acp_checkout() -> Path:
    """正本の checkout(env ACP_CHECKOUT → ~/repos/agent-control-plane)。無ければ RuntimeError。"""
    for candidate in (os.environ.get("ACP_CHECKOUT", "").strip(), "~/repos/agent-control-plane"):
        if not candidate:
            continue
        path = Path(os.path.expanduser(candidate))
        # .git は clone では dir・worktree では file(どちらも正本の checkout)。
        if (path / ".git").exists():
            return path
    raise RuntimeError(
        f"正本({CANON_REPO})の checkout が見つからない — env ACP_CHECKOUT を渡すか "
        "~/repos/agent-control-plane を用意する(正本との突合を黙って skip しない)。"
        "checkout を持てない機体は --check-copies(写しの中で閉じる検)を撃つ"
    )


def git(checkout: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=False)


def ensure_commit(checkout: Path, commit: str) -> None:
    """pin の commit が checkout に在ること。浅い checkout なら 1 度だけ取り寄せる。"""
    probe = ["cat-file", "-e", f"{commit}^{{commit}}"]
    if git(checkout, probe).returncode == 0:
        return
    git(checkout, ["fetch", "--quiet", "origin", commit])
    if git(checkout, probe).returncode != 0:
        raise RuntimeError(
            f"pin の commit が正本の checkout に無い(fetch も届かない): {commit}"
            " — checkout を新しくするか、pin を進める"
        )


def resolve_commit(checkout: Path, spec: str) -> str:
    done = git(checkout, ["rev-parse", f"{spec}^{{commit}}"])
    if done.returncode != 0:
        raise RuntimeError(f"commit を解決できない: {spec} — {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout.decode("utf-8").strip()


def canon_map_of(checkout: Path, commit: str, files: list[dict]) -> dict[str, bytes]:
    """生成の材料 — pin の commit の正本の中身(取り寄せの 1 点)。"""
    ensure_commit(checkout, commit)
    rows: dict[str, bytes] = {}
    for entry in files:
        done = git(checkout, ["show", f"{commit}:{entry['canonPath']}"])
        if done.returncode == 0:
            rows[entry["copyPath"]] = done.stdout
    return rows


def read_copies(root: Path, files: list[dict]) -> dict[str, bytes]:
    rows: dict[str, bytes] = {}
    for entry in files:
        path = root / entry["copyPath"]
        if path.exists():
            rows[entry["copyPath"]] = path.read_bytes()
    return rows


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_text_or_empty(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_copies(root: Path, lock: dict, canon_map: dict[str, bytes]) -> int:
    """生成 — pin の commit の正本を写しの置き場へ書く。"""
    written = 0
    for entry in lock["files"]:
        blob = canon_map.get(entry["copyPath"])
        if blob is None:
            raise RuntimeError(f"pin の commit に正本が無い: {entry['canonPath']}")
        path = root / entry["copyPath"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != blob:
            path.write_bytes(blob)
            written += 1
    return written


def write_lock(root: Path, lock: dict) -> None:
    (root / LOCK_REL).write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---- 動詞 -------------------------------------------------------------------


def report(problems: list[str], headline: str) -> int:
    if not problems:
        return 0
    print(headline, file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def run_check_copies(root: Path, lock: dict) -> int:
    problems = copy_problems(
        lock,
        read_copies(root, lock.get("files", [])),
        load_json(root / READS_REL),
        read_text_or_empty(root / CONTRACTS_README_REL),
    )
    if report(problems, f"ACP の契約の写しが pin({LOCK_REL})と食い違っている:"):
        return 1
    print(
        f"sync_contracts: 写し {len(lock['files'])} 本すべて pin と一致"
        f"(正本 = {CANON_REPO} @ {lock['commit'][:8]}・正本との突合は --check)"
    )
    return 0


def run_check(root: Path, lock: dict) -> int:
    files = lock.get("files", [])
    copies = read_copies(root, files)
    problems = copy_problems(
        lock, copies, load_json(root / READS_REL), read_text_or_empty(root / CONTRACTS_README_REL)
    )
    if not lock_problems(lock):
        problems += canon_problems(lock, canon_map_of(acp_checkout(), lock["commit"], files), copies)
    if report(problems, f"ACP の契約の写しが生成物と食い違っている(pin = {lock.get('commit', '')[:8]}):"):
        return 1
    print(
        f"sync_contracts: 写し {len(files)} 本すべて生成物と差分 0"
        f"(正本 = {CANON_REPO} @ {lock['commit'][:8]})"
    )
    return 0


def run_write(root: Path, lock: dict) -> int:
    checkout = acp_checkout()
    written = write_copies(root, lock, canon_map_of(checkout, lock["commit"], lock["files"]))
    print(f"sync_contracts: pin({lock['commit'][:8]})から写しを生成した — 書き換え {written} 本")
    return 0


def run_pin(root: Path, lock: dict, spec: str) -> int:
    """pin を進める(1 commit で lock と写しが一緒に動く — 契約を変える便の消費者側の 1 手)。"""
    checkout = acp_checkout()
    commit = spec if commit_spelling(spec) else resolve_commit(checkout, spec)
    canon_map = canon_map_of(checkout, commit, lock["files"])
    for entry in lock["files"]:
        blob = canon_map.get(entry["copyPath"])
        if blob is None:
            raise RuntimeError(f"pin の commit に正本が無い: {entry['canonPath']}")
        entry["sha256"] = sha256_of(blob)
    lock["commit"] = commit
    write_copies(root, lock, canon_map)
    write_lock(root, lock)
    print(f"sync_contracts: pin を {commit[:8]} へ進め、写しを生成した")
    return 0


def main(argv: list[str]) -> int:
    lock = load_json(ROOT / LOCK_REL)
    if not lock:
        print(f"{LOCK_REL} が無い(pin の file はこの repo の 1 点)", file=sys.stderr)
        return 1
    try:
        if "--write" in argv:
            return run_write(ROOT, lock)
        if "--pin" in argv:
            rest = argv[argv.index("--pin") + 1 :]
            return run_pin(ROOT, lock, rest[0] if rest else DEFAULT_PIN_SPEC)
        if "--check-copies" in argv:
            return run_check_copies(ROOT, lock)
        return run_check(ROOT, lock)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
