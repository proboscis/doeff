#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""enforcement 台帳 — 生成と突合の単一の家(ADR-DOE-ENFORCE-001 R5 / R7 / R9)。

台帳 docs/adr/enforcement-ledger.json は**生成物**で、手で書かない。このファイルが木から
enforcement 資産の一覧を作る:

- defadr_files                 = docs/adr/defadr_*.hy の file 名
- semgrep_rules                = .semgrep.yaml の規則 id
- adr_laws                     = ADR の (law 名 …) — 「<ADR id> <law 名>」
- adr_deftest_enforcements     = ADR の (deftest 名 …) — 「<ADR id> <deftest 名>」
- adr_defsemgrep_enforcements  = ADR の (defsemgrep 名 …) — 「<ADR id> <defsemgrep 名>」

ADR は Hy の読みの規則(文字列・`;` の註・`#_` で捨てた形・`#[[…]]` の文字列の中は数えない)で
形として読む — 字面の数え(`"(law "` の出現数)は註や文字列の中の綴りも数えていた(2026-09-28 実測:
law 134 に対し形は 132・deftest 82 に対し形は 80)。読みが Hy の reader と同じ答えを返すことは
tests/test_enforcement_ledger.py が実物の ADR 全部で突き合わせる。

台帳は数ではなく項目の名の一覧なので、並行する 2 便が別々の項目を足しても git の merge が行ごとに
正しく合わせる(数の台帳は 2 便が同じ「104 → 105」を刻むと黙って 105 に合わさった — 2026-09-17
ed98775a)。減った項目は台帳の差分に名前で残る。stdlib 単独(venv・依存不要 — 呼び口は `uv run --script`。
機体の python は撃たない)— git の pre-commit hook(scripts/git-hooks/pre-commit・.pre-commit-config.yaml)からも既定 pytest
(tests/test_enforcement_ledger.py)からも、この同じ勘定を使う。

modes:
  (既定)    working tree を台帳と突合する(絶対一致 — 日次の pytest と同じ面)
  --staged   git index(commit に入る断面)を突合する — hook 用。台帳と一致すれば通す。一致しなくても、
             この commit が動かした項目(HEAD からの増減)が台帳の増減にそのまま写っていれば通し、
             HEAD に既に在ったずれは申告だけする(ずれを持ち込んだ commit の責任で、後の commit を塞がない)。
  --write    working tree から台帳を生成して書く。台帳から外れる項目は名前で申告する。
  --root=P   repo root の明示(既定 = cwd の git toplevel)

exit code: 0 = 一致(または --write の成功)/ 1 = 不一致・ADR が読めない / 2 = 実行環境の失敗(git が引けない等)。
"""

import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

LEDGER_PATH = "docs/adr/enforcement-ledger.json"
SEMGREP_PATH = ".semgrep.yaml"
ADR_DIR = "docs/adr"
ADR_GLOB = "docs/adr/defadr_*.hy"
ADR_NAME = re.compile(r"defadr_[^/]*\.hy")

KEYS = (
    "defadr_files",
    "semgrep_rules",
    "adr_laws",
    "adr_deftest_enforcements",
    "adr_defsemgrep_enforcements",
)
#: ADR の中で数える形の頭 → 台帳の鍵。
FORM_KEYS = {
    "law": "adr_laws",
    "deftest": "adr_deftest_enforcements",
    "defsemgrep": "adr_defsemgrep_enforcements",
}
LEDGER_COMMENT = (
    "生成物 — 手で書かない。`make enforcement-ledger`(= scripts/check_enforcement_ledger.py --write)が"
    "木から作る(ADR-DOE-ENFORCE-001 R5 / R9)。各項目は enforcement 資産 1 つの名で、減った項目は"
    "この file の差分に名前で残る。commit の hook と既定 pytest がこの一覧と木の一致を検める。"
)

_CLOSERS = {"(": ")", "[": "]", "{": "}", "#(": ")", "#{": "}"}
_DELIMITERS = frozenset('()[]{}"; \t\r\n\f')
_SPACE = frozenset(" \t\r\n\f")
_SEMGREP_RULE = re.compile(r"^  - id:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)


class UnreadableAdrError(ValueError):
    """ADR の file が Hy の形として読めない(括弧や文字列が閉じていない)。"""


# ---------------------------------------------------------------------------
# Hy の読み(数えるのに要る分だけ)。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Atom:
    """綴り(symbol・keyword・数・引用の前置き ' ` ~ ~@・`#^` などの前置き)。"""

    text: str


@dataclass(frozen=True)
class Text:
    """文字列("…" と #[[…]])。中の綴りは形として数えない。"""

    content: str


@dataclass(frozen=True)
class Form:
    """括弧の並び。opener = ( [ { #( #{ のどれか。"""

    opener: str
    children: list = field(default_factory=list)

    def head(self) -> str | None:
        if self.opener == "(" and self.children and isinstance(self.children[0], Atom):
            return self.children[0].text
        return None

    def name(self) -> str:
        """2 番目の要素(定義の名)の綴り。"""
        second = self.children[1] if len(self.children) > 1 else None
        if isinstance(second, Atom):
            return second.text
        if isinstance(second, Text):
            return second.content
        return "<名なし>"


class _Reader:
    """1 つの file の字面を頭から読む。_mut_pos は次に読む位置。"""

    def __init__(self, text: str, where: str) -> None:
        self.text = text
        self.where = where
        self._mut_pos = 0

    def fail(self, what: str) -> UnreadableAdrError:
        return UnreadableAdrError(f"{self.where}: {what}(offset {self._mut_pos})")

    def skip_space(self) -> None:
        text = self.text
        while self._mut_pos < len(text):
            if text[self._mut_pos] in _SPACE:
                self._mut_pos += 1
            elif text[self._mut_pos] == ";":
                end = text.find("\n", self._mut_pos)
                self._mut_pos = len(text) if end < 0 else end + 1
            else:
                return

    def at_end(self) -> bool:
        self.skip_space()
        return self._mut_pos >= len(self.text)

    def string(self) -> Text:
        start = self._mut_pos + 1
        pos = start
        while pos < len(self.text):
            if self.text[pos] == "\\":
                pos += 2
            elif self.text[pos] == '"':
                self._mut_pos = pos + 1
                return Text(self.text[start:pos])
            else:
                pos += 1
        raise self.fail("文字列が閉じていない")

    def bracket_string(self) -> Text:
        """`#[delim[ … ]delim]`(pos は `#` の位置)。"""
        open_end = self.text.find("[", self._mut_pos + 2)
        if open_end < 0:
            raise self.fail("#[ の文字列の開きが閉じていない")
        closer = f"]{self.text[self._mut_pos + 2 : open_end]}]"
        end = self.text.find(closer, open_end + 1)
        if end < 0:
            raise self.fail(f"{closer} で閉じる文字列が閉じていない")
        self._mut_pos = end + len(closer)
        return Text(self.text[open_end + 1 : end])

    def atom(self) -> Atom:
        start = self._mut_pos
        while self._mut_pos < len(self.text) and self.text[self._mut_pos] not in _DELIMITERS:
            self._mut_pos += 1
        return Atom(self.text[start : self._mut_pos])

    def sequence(self, opener: str) -> Form:
        """開き括弧の直後から、対応する閉じ括弧までの並び。"""
        closer = _CLOSERS[opener]
        children = []
        while not self.at_end():
            if self.text[self._mut_pos] == closer:
                self._mut_pos += 1
                return Form(opener, children)
            child = self.form()
            if child is not None:
                children.append(child)
        raise self.fail(f"{opener!r} が閉じていない")

    def hashed(self) -> Form | Text | Atom | None:
        """`#` で始まる形。`#(` `#{` は並び・`#[` は文字列・`#_` は次の形を捨てる(None)・他は綴り。"""
        following = self.text[self._mut_pos + 1 : self._mut_pos + 2]
        if following in ("(", "{"):
            self._mut_pos += 2
            return self.sequence("#" + following)
        if following == "[":
            return self.bracket_string()
        if following == "_":
            self._mut_pos += 2
            if self.at_end():
                raise self.fail("#_ の後に形が無い")
            self.form()
            return None
        return self.atom()

    def form(self) -> Form | Text | Atom | None:
        """pos から 1 つの形を読む。捨てた形(`#_`)は None。"""
        char = self.text[self._mut_pos]
        if char in ")]}":
            raise self.fail(f"対応の無い閉じ括弧 {char!r}")
        if char in "([{":
            self._mut_pos += 1
            return self.sequence(char)
        if char == '"':
            return self.string()
        if char == "#":
            return self.hashed()
        if char in "'`~":
            # 引用の前置き(' ` ~ ~@)は形そのものではない — 次の形を並びの要素として読む。
            step = 2 if self.text.startswith("~@", self._mut_pos) else 1
            self._mut_pos += step
            return Atom(self.text[self._mut_pos - step : self._mut_pos])
        return self.atom()


def read_forms(text: str, where: str = "<adr>") -> list:
    """Hy の file の最上位の形の並び(文字列・註・`#_` の形は数えられる形にならない)。"""
    reader = _Reader(text, where)
    if text.startswith("#!"):
        end = text.find("\n")
        reader._mut_pos = len(text) if end < 0 else end + 1
    forms = []
    while not reader.at_end():
        form = reader.form()
        if form is not None:
            forms.append(form)
    return forms


def adr_items(file_name: str, text: str) -> dict[str, list[str]]:
    """1 冊の ADR の law / deftest / defsemgrep の項目(持ち主 = 囲む defadr の id・無ければ file 名)。"""
    items: dict[str, list[str]] = {key: [] for key in FORM_KEYS.values()}

    def visit(node: object, owner: str) -> None:
        if not isinstance(node, Form):
            return
        head = node.head()
        if head == "defadr":
            owner = node.name()
        elif head in FORM_KEYS:
            items[FORM_KEYS[head]].append(f"{owner} {node.name()}")
        for child in node.children:
            visit(child, owner)

    for form in read_forms(text, file_name):
        visit(form, file_name)
    return items


def inventory_from_texts(adr_texts: dict[str, str], semgrep_text: str) -> dict[str, list[str]]:
    """木の enforcement 資産の一覧(鍵ごとに並べた名の list・重複も数として残す)。"""
    found: dict[str, list[str]] = {key: [] for key in KEYS}
    found["defadr_files"] = list(adr_texts)
    found["semgrep_rules"] = _SEMGREP_RULE.findall(semgrep_text)
    for file_name, text in adr_texts.items():
        for key, names in adr_items(file_name, text).items():
            found[key].extend(names)
    return {key: sorted(names) for key, names in found.items()}


def counts(inventory: dict[str, list[str]]) -> dict[str, int]:
    return {key: len(inventory.get(key, [])) for key in KEYS}


# ---------------------------------------------------------------------------
# 台帳の読み書きと差。
# ---------------------------------------------------------------------------


def ledger_from_text(text: str) -> dict[str, list[str]]:
    """台帳の一覧。旧い形(鍵ごとの数)は一覧として読めないので ValueError。"""
    data = json.loads(text)
    ledger = {key: value for key, value in data.items() if not key.startswith("_")}
    for key, value in ledger.items():
        if not isinstance(value, list):
            raise ValueError(f"台帳の {key} が名の一覧でない(旧い数の形)— 生成し直す")
    return {key: sorted(str(item) for item in value) for key, value in ledger.items()}


def ledger_text(inventory: dict[str, list[str]]) -> str:
    data = {"_comment": LEDGER_COMMENT, **{key: inventory[key] for key in KEYS}}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


@dataclass(frozen=True)
class Change:
    """1 つの鍵の差 — after にだけ在る名(added)と before にだけ在る名(removed)。"""

    added: list
    removed: list


def difference(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, Change]:
    """鍵ごとの差(重複も数として比べる)。差の無い鍵は載せない。"""
    changes = {
        key: Change(
            sorted((Counter(after.get(key, [])) - Counter(before.get(key, []))).elements()),
            sorted((Counter(before.get(key, [])) - Counter(after.get(key, []))).elements()),
        )
        for key in sorted(set(before) | set(after))
    }
    return {key: change for key, change in changes.items() if change.added or change.removed}


# ---------------------------------------------------------------------------
# 木の断面(working tree / git index / HEAD)。
# ---------------------------------------------------------------------------


def worktree_inventory(root: Path) -> dict[str, list[str]]:
    adr_files = sorted(root.glob(ADR_GLOB))
    return inventory_from_texts(
        {path.name: path.read_text(encoding="utf-8") for path in adr_files},
        (root / SEMGREP_PATH).read_text(encoding="utf-8"),
    )


def worktree_ledger(root: Path) -> dict[str, list[str]]:
    return ledger_from_text((root / LEDGER_PATH).read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _adr_names(listing: str) -> list[str]:
    """git の一覧から docs/adr の直下の defadr_*.hy だけ(working tree の glob と同じ母集団)。"""
    return sorted(
        name
        for name in listing.splitlines()
        if name.rsplit("/", 1)[0] == ADR_DIR and ADR_NAME.fullmatch(name.rsplit("/", 1)[-1])
    )


def _revision_inventory(root: Path, revision: str) -> dict[str, list[str]]:
    """git の断面の一覧。revision = "" は index、"HEAD" 等は commit。"""
    listing = (
        _git(root, "ls-files", "--cached", "--", ADR_DIR)
        if not revision
        else _git(root, "ls-tree", "-r", "--name-only", revision, "--", ADR_DIR)
    )
    return inventory_from_texts(
        {
            name.rsplit("/", 1)[-1]: _git(root, "show", f"{revision}:{name}")
            for name in _adr_names(listing)
        },
        _git(root, "show", f"{revision}:{SEMGREP_PATH}"),
    )


def staged_inventory(root: Path) -> dict[str, list[str]]:
    return _revision_inventory(root, "")


def staged_ledger(root: Path) -> dict[str, list[str]]:
    return ledger_from_text(_git(root, "show", f":{LEDGER_PATH}"))


def head_drift_matches(root: Path, actual: dict, ledger: dict) -> bool:
    """この commit の増減(HEAD → index)が台帳の増減にそのまま写っているか。

    HEAD が無い・HEAD の台帳が旧い形・HEAD の ADR が読めない時は判じられないので False(= 絶対一致を求める)。
    """
    try:
        head_actual = _revision_inventory(root, "HEAD")
        head_ledger = ledger_from_text(_git(root, "show", f"HEAD:{LEDGER_PATH}"))
    except (RuntimeError, ValueError):
        return False
    return difference(head_actual, actual) == difference(head_ledger, ledger)


# ---------------------------------------------------------------------------
# 申告。
# ---------------------------------------------------------------------------

_SHOWN = 12


def _listing(label: str, names: list) -> list[str]:
    shown = [f"      {name}" for name in names[:_SHOWN]]
    rest = [f"      … ほか {len(names) - _SHOWN} 件"] if len(names) > _SHOWN else []
    return [f"    {label} {len(names)} 件:", *shown, *rest] if names else []


def describe(diff: dict[str, Change]) -> str:
    return "\n".join(
        line
        for key, change in diff.items()
        for line in (
            f"  {key}:",
            *_listing("木に在って台帳に無い(足した・台帳に未記入)", change.added),
            *_listing("台帳に在って木に無い(消えた)", change.removed),
        )
    )


def _repair_hint(root: Path) -> str:
    return (
        "直し方: 台帳は生成物 — `make enforcement-ledger`"
        "(= scripts/check_enforcement_ledger.py --write)で作り直し、差分の「消えた」項目が"
        f"意図した削除かを確かめてから台帳を stage する。台帳: {root / LEDGER_PATH}"
    )


def _mismatch_message(face: str, diff: dict[str, Change], root: Path) -> str:
    return (
        f"enforcement 台帳と木({face})が食い違う — ADR-DOE-ENFORCE-001 R5 / R9。\n"
        f"{describe(diff)}\n{_repair_hint(root)}"
    )


def _unreadable_ledger(root: Path, error: ValueError) -> int:
    print(f"enforcement 台帳が読めない — {error}\n{_repair_hint(root)}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# 入口。
# ---------------------------------------------------------------------------


def check_worktree(root: Path) -> int:
    actual = worktree_inventory(root)
    try:
        ledger = worktree_ledger(root)
    except ValueError as error:
        return _unreadable_ledger(root, error)
    drift = difference(ledger, actual)
    if drift:
        print(_mismatch_message("working tree", drift, root), file=sys.stderr)
    return 1 if drift else 0


def check_staged(root: Path) -> int:
    actual = staged_inventory(root)
    try:
        ledger = staged_ledger(root)
    except ValueError as error:
        return _unreadable_ledger(root, error)
    drift = difference(ledger, actual)
    if drift and head_drift_matches(root, actual, ledger):
        # この commit の増減は台帳に写っている。残るずれは HEAD に既に在ったもの。
        print(
            "enforcement 台帳: この commit の増減は台帳に写っている(通す)。ただし HEAD に既に在った"
            "ずれが残っている — ずれを持ち込んだ commit の直しを待つか、ここで直す。\n"
            f"{describe(drift)}\n{_repair_hint(root)}",
            file=sys.stderr,
        )
        return 0
    if drift:
        print(_mismatch_message("staged 断面", drift, root), file=sys.stderr)
    return 1 if drift else 0


def write_ledger(root: Path) -> int:
    actual = worktree_inventory(root)
    path = root / LEDGER_PATH
    try:
        previous = ledger_from_text(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    path.write_text(ledger_text(actual), encoding="utf-8")
    summary = ", ".join(f"{key} {value}" for key, value in counts(actual).items())
    print(f"enforcement 台帳を生成した({summary}): {path}", file=sys.stderr)
    removed = {
        key: Change([], change.removed)
        for key, change in difference(previous, actual).items()
        if previous and change.removed
    }
    if removed:
        print(
            "⚠ 台帳から外れた項目(木から消えた enforcement)— 意図した削除かを確かめてから stage する:\n"
            + describe(removed),
            file=sys.stderr,
        )
    return 0


def main(argv: list[str]) -> int:
    roots = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--root=")]
    if "--write" in argv:
        run = write_ledger
    elif "--staged" in argv:
        run = check_staged
    else:
        run = check_worktree
    try:
        root = (
            Path(roots[-1]).resolve()
            if roots
            else Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        )
        return run(root)
    except UnreadableAdrError as error:
        print(f"check_enforcement_ledger: ADR が Hy の形として読めない — {error}", file=sys.stderr)
        return 1
    except (RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"check_enforcement_ledger: 突合を実行できない — {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
