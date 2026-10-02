"""doeff の commit の hook が呼ぶ doeff-linter を、基点の file が名乗る鍵と同じ組み立ての入力から組まれた binary に固定する入口
(agora-redesign #2906 の 2 便目・決め = #2906 の comment・c3-w49 と c2-w28 の合意)。

何が壊れていたか: hook は探し道の `doeff-linter`(zeus では各席が手で入れる ~/.cargo/bin の物)を呼び、その版が基点の版と違えば
「測れない」と名乗って通った。linter の source を変える便のたびに、誰かが共有の binary を入れ直すまで hook の linter の項は
何も止めなかった(2026-10-02 の夕方)。land-arm は main の linter が変わるたびに開発版を自動で組み直しているが、hook はそれを
見ていなかった。

鍵 = 組み立ての入力だけの木の組: packages/doeff-linter と、その Cargo.toml の path 依存(dependencies・build-dependencies)の
`src`・`data`(include_str! で取り込む JSON)・`Cargo.toml`・`Cargo.lock`・`build.rs` の git の object id を並べた sha256。tests や
README だけの変更では動かず、path 依存(doeff-indexer)の src の変更では動く。

探す順(hook の中では組まない — 冷えた組み立ては 3〜5 分で commit を待たせ、land-arm の rustc と重なる):
  1. land-arm の開発版(~/.local/share/doeff-linter-dev)— 記録 installed.json の commit から鍵を計算する。
  2. 断面の置き場(~/.cache/doeff-linter-snapshots — packages/doeff-linter/scripts/linter_snapshot.py の既定)— dir の名の sha から。
  3. どちらにも無ければ None(呼び手が「測れない」と名指す)。
置き場は HOME の下の決まった場所で、環境変数では替えない(DOEFF004 — 検は HOME を一時の dir に向けて差し替える)。断面の道具を
XDG_CACHE_HOME や DOEFF_LINTER_SNAPSHOT_DIR で別の場所に置いた機体では断面が見つからず「測れない」になる(黙って別の版で比べない)。
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

CRATE: str = "packages/doeff-linter"
INPUT_ENTRIES: tuple[str, ...] = ("src", "data", "Cargo.toml", "Cargo.lock", "build.rs")
KEY_PREFIX: str = "doeff-linter の組み立ての入力 "
BIN_NAME: str = "doeff-linter"
DEV_RECORD: str = "installed.json"
# linter の `--version` が名乗る組んだ commit(build.rs の DOEFF_LINTER_COMMIT)。+dirty の付いた物は commit の鍵にしない。
BUILT_COMMIT = re.compile(r"\(doeff ([0-9a-f]{40})\)")


@dataclass(frozen=True)
class Located:
    """鍵の合った binary と、その出どころ(land-arm の開発版 / 断面の置き場)— 名指しの文に載せるため。"""

    binary: Path
    source: str


@dataclass(frozen=True)
class Candidate:
    """置き場に在る binary と、それを組んだ commit。"""

    binary: Path
    commit: str
    source: str


def _git(top: Path, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """repo の根で git を撃つため(doeff の hook は doeff の repo の中でだけ動くので、git が渡す GIT_DIR も同じ repo を指す)。"""
    return subprocess.run(["git", *args], cwd=top, input=stdin, capture_output=True, text=True, check=False)


def crates(top: Path, commit: str) -> tuple[str, ...] | None:
    """commit の linter の crate と、その Cargo.toml の path 依存の dir(repo の根から)。commit か Cargo.toml を git が知らなければ None。"""
    shown: subprocess.CompletedProcess[str] = _git(top, ["show", f"{commit}:{CRATE}/Cargo.toml"])
    if shown.returncode != 0:
        return None
    manifest: dict[str, object] = tomllib.loads(shown.stdout)
    paths: list[str] = [
        str(spec["path"])
        for section in ("dependencies", "build-dependencies")
        if isinstance(table := manifest.get(section), dict)
        for spec in table.values()
        if isinstance(spec, dict) and "path" in spec
    ]
    return (CRATE, *sorted(posixpath.normpath(posixpath.join(CRATE, path)) for path in paths))


def input_key(top: Path, commit: str) -> str | None:
    """commit の組み立ての入力の鍵。commit を git が知らなければ None(鍵にしない — 黙って別の鍵と照らさない)。"""
    found: tuple[str, ...] | None = crates(top, commit)
    if found is None:
        return None
    specs: list[str] = [f"{crate}/{entry}" for crate in found for entry in INPUT_ENTRIES]
    checked: subprocess.CompletedProcess[str] = _git(
        top, ["cat-file", "--batch-check=%(objectname)"], stdin="".join(f"{commit}:{spec}\n" for spec in specs),
    )
    answers: list[str] = checked.stdout.splitlines()
    if checked.returncode != 0 or len(answers) != len(specs):
        return None
    # 無い入口(build.rs の無い crate など)は「無い」として鍵に入れる — 後から足されれば鍵が動く。
    objects: list[str] = [answer if not answer.endswith("missing") else "-" for answer in answers]
    lines: str = "\n".join(f"{spec} {obj}" for spec, obj in zip(specs, objects, strict=True))
    return KEY_PREFIX + hashlib.sha256(lines.encode("utf-8")).hexdigest()[:40]


def binary_key(top: Path, binary: Path) -> str:
    """binary の鍵: `--version` が名乗る commit の組み立ての入力の鍵。名乗らない・+dirty・git が知らない commit の時は `--version` の
    1 行目そのもの(どの置き場の鍵とも合わず「測れない」になる — 黙って別の版で比べない)。"""
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        [str(binary), "--version"], cwd=top, capture_output=True, text=True, check=False,
    )
    lines: list[str] = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not lines:
        raise SystemExit(f"{binary} の版を読めなかった(rc {completed.returncode}): {completed.stderr.strip()[:300]}")
    named: str = lines[0].strip()
    built = BUILT_COMMIT.search(named)
    key: str | None = input_key(top, built.group(1)) if built is not None and "+dirty" not in named else None
    return key if key is not None else named


def dev_dir() -> Path:
    """land-arm の開発版の置き場。"""
    return Path.home() / ".local" / "share" / "doeff-linter-dev"


def snapshot_dir() -> Path:
    """断面の置き場(packages/doeff-linter/scripts/linter_snapshot.py の既定)。"""
    return Path.home() / ".cache" / "doeff-linter-snapshots"


def _dev_candidate() -> list[Candidate]:
    """land-arm の開発版(記録の commit と binary が揃っている時だけ)。"""
    record: Path = dev_dir() / DEV_RECORD
    binary: Path = dev_dir() / BIN_NAME
    if not (record.is_file() and binary.is_file()):
        return []
    commit: object = json.loads(record.read_text(encoding="utf-8")).get("commit")
    return [Candidate(binary, commit, "land-arm の開発版")] if isinstance(commit, str) else []


def _snapshot_candidates() -> list[Candidate]:
    """断面の置き場の binary(dir の名 = 40 桁の sha)— 新しい順(よく当たる物から照らす)。"""
    store: Path = snapshot_dir()
    if not store.is_dir():
        return []
    built: list[Path] = [d for d in store.iterdir() if re.fullmatch(r"[0-9a-f]{40}", d.name) and (d / BIN_NAME).is_file()]
    return [
        Candidate(d / BIN_NAME, d.name, "断面の置き場")
        for d in sorted(built, key=lambda d: (d / BIN_NAME).stat().st_mtime, reverse=True)
    ]


def locate(top: Path, key: str) -> Located | None:
    """鍵の合った binary を、land-arm の開発版 → 断面の置き場の順に探す(組まない)。無ければ None。"""
    for candidate in (*_dev_candidate(), *_snapshot_candidates()):
        if input_key(top, candidate.commit) == key:
            return Located(candidate.binary, candidate.source)
    return None


def searched() -> str:
    """名指しの文に載せる、探した置き場。"""
    return f"land-arm の開発版 {dev_dir()}・断面の置き場 {snapshot_dir()}"


def dev_key(top: Path) -> str | None:
    """land-arm の開発版の鍵(開発版が無い・記録の commit を git が知らない時は None)— 「取り込めば測れる」を言うため。"""
    found: list[Candidate] = _dev_candidate()
    return input_key(top, found[0].commit) if found else None


def main(argv: list[str]) -> int:
    """`which [<commit>]` — commit(既定 HEAD)の組み立ての入力の鍵の binary の path を 1 行出す(shell の hook が呼ぶ —
    scripts/lint-doeff-cluster.sh)。無ければ理由を出して 3(呼び手が「測れない」と名指す)。repo の根で呼ぶ。"""
    if argv[:1] != ["which"] or len(argv) > 2:
        print("使い方: doeff_linter_locked.py which [<commit>]", file=sys.stderr)
        return 2
    top: Path = Path.cwd()
    commit: str = argv[1] if len(argv) == 2 else "HEAD"
    key: str | None = input_key(top, commit)
    located: Located | None = locate(top, key) if key is not None else None
    if located is None:
        print(f"{commit} の linter の組み立ての入力({key})の binary が {searched()} に無い", file=sys.stderr)
        return 3
    print(located.binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
