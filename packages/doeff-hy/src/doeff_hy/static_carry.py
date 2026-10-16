"""doeff-hy-check の運び(`--carry <file>`)— 前の回から入力の変わらない .hy は pyright に当てず、前の回の pyright の診断を運ぶ
(agora-redesign card ki-cbd1c93cfe7c)。

なぜ: agora-controllers の日次の型の測り(hy-type-trend)は範囲の .hy(約 3,000 本)を毎回全部 pyright に当て、秒が repo の大きさと
機体の混みにつれて伸び、処理ステージの上限 7,200 秒で 2 回続けて切れた(card ki-c16990fab50b)。pyright が 1 つの file に出す診断は、
その file の展開と、その file が使う名の型を決める宣言(import で届く module の中)と、環境(pyright の版・設定・python の探し道)だけで
決まる。それらが前の回と同じ file は、前の回の診断をそのまま使う。

入力(file ごとの digest — InputGraph.inputs_of):
- その file 自身: .hy の source・展開した Python・位置の対応表(診断の位置は展開と対応表で .hy の位置へ戻る)。
- その file が使う名の宣言(module の名 1 つ — _Node)と、その宣言の表面が使う名の宣言(推移で全部)。表面 = pyright が呼び手の側で
  その名の型を決めるのに読む部分:
  - 型を全部宣言した関数(引数と答え)= decorator と署名。本体は空(`...`・`pass`・NotImplementedError の raise)ならそのまま、
    docstring だけなら印、それ以外も 1 つの印に替える(Protocol の空の method は実装の無い method と数えられるので、空か否かは表面)。
  - class = 全体(method は上の関数の決めで縮める。ただし self / cls の属性へ代入する method は本体ごと — pyright はそこから属性の型を
    推す)。
  - 型を宣言しない関数・global / nonlocal を持つ関数・変数・型の別名ほか = 文の全体(pyright は値と本体から型を推す)。module の段の
    import の文は名ごとに分ける。
  - docstring は落とし、行の番号は見ない(ast.dump の位置なし)— 説明の書き換えと行の移りは使い手へ伝えない。
- その file が import する module の解き先(探し道の各 dir で、pyright が当たりうる候補の file のうち在る物の並び — Resolution)。
  候補が増える・消える(新しい file が前の解き先を隠す・解けなかった import が解ける)と digest が変わる。名の宣言の digest も、その
  module の解き先を含む。
- 環境の指紋(Environment.fingerprint): 運びの形の版・pyright の版・pyright の設定・python の版と探し道・探し道のうち python の prefix の
  下の dir(site-packages・標準ライブラリ)の中身の名(dist-info の名 = 配布物の版)と .pth の中身・診断を作る関数の source(semantics)。
  中身を file ごとに見る探し道(根・extraPaths・typings・prefix の外の dir — editable で入れた doeff の checkout)は指紋に入れない —
  doeff の pin が上がっても、変わった宣言を使う .hy だけを当て直す。

安全の側へ倒す所: 構文の誤りで読めない Python の module は回ごとに違う digest にする(使う file を毎回当てる)。module を値で使う参照・
star import は、その module の全部の名に依る(_Node の name = None)。名が module に無い時は、star import の先・module の __getattr__・
同じ名の下の module に依る。関数の本体の中の名と module の名を区別しない(同じ綴りなら依る — 余分に当てるだけ)。

見直し(refresh_runs): 入力の変わらない file のうち、当てた回が一番古い ceil(本数 / refresh_runs) 本を毎回当て直し、運んだ診断と
違った file を数えて名乗る(summary の refreshMismatches)— この入力の決めが見落とす依存があれば、ここに出る(pyright の答えが
検める順で揺れる file — card ki-e56135ec1220 — もここに出る)。

運びの文書(JSON・版 CARRY_VERSION): {"version", "fingerprint", "run", "summary", "files": {相対 path: {"inputs", "checked",
"rows": [[行, 列, severity, 規則, 文言], …]}}}。run = 回の番号(運びの無い回が 1)・checked = その file を pyright に当てた回の番号・
rows = その回の pyright の診断(.hy の位置・pyright の答えの順 — 整える前の物。整え(static_check.settle)は毎回、運んだ物と当てた物を合わせてかける)。
summary は使い手(agora-controllers の scripts/hy_type_trend.hy)が読む欄: run・checked・carried・reasons・refreshMismatches・mismatched。
"""

import ast
import hashlib
import inspect
import json
import math
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import TypeVar

#: 運びの文書の版と、入力の決め方の版(決め方を替えたら上げる — 指紋に入るので、前の回の運びは全部当て直しになる)。
CARRY_VERSION = 1
#: 見直しの周期(回)の既定 — 入力の変わらない file も、この回数のうちに 1 度は当て直す(agora-controllers の日次 6 回 / 日で 2 日)。
DEFAULT_REFRESH_RUNS = 12
#: 当てる訳の語(summary の reasons の鍵): absent = 運びの file が無い(初回)・unreadable = 運びの file を読めない・
#: environment = 環境の指紋が違う・new = 前の回に無い file・changed = 入力が変わった・refresh = 見直し。
REASONS = ("absent", "unreadable", "environment", "new", "changed", "refresh")
#: summary に名を並べる、見直しで答えが違った file の数の上限。
MISMATCH_SHOWN = 20
#: 表面の文字列の定数を、注記の式として読みに行く長さの上限(長い文言は式でない)。
_ANNOTATION_TEXT_LIMIT = 400
#: 縮めた本体の代わりに置く印(空でない本体)と、docstring だけの本体の印。
_BODY_MARK = "<本体>"
_DOCSTRING_MARK = "<docstring>"
#: pyright の版の綴り(包みの道具が新しい版の知らせを足しても、指紋は版だけで決める)。
_PYRIGHT_VERSION = re.compile(r"pyright \d+\.\d+\.\d+")
#: python の探し道を読む命令(pyright も同じ python に探し道を問う)。
_PATH_PROBE = (
    "import json, sys; print(json.dumps({'path': sys.path, 'prefix': sys.prefix, "
    "'base': sys.base_prefix, 'version': sys.version}))"
)

@dataclass(frozen=True, order=True)
class CarriedRow:
    """pyright の診断 1 つ(.hy の位置)— 運びの文書の行(path は持ち主の CarriedFile が持つ)。"""

    line: int
    column: int
    severity: str
    rule: str
    message: str


@dataclass(frozen=True)
class Candidate:
    """module の解き先の候補 1 つ — path と種類(pyi・py・hy・hy-failed・hy-unstaged・package・init・native・typed:<中身の digest>)。"""

    path: str
    kind: str


#: module の解き先 — 探し道の順に、在る候補の並び。空 = 根と editable の探し道に無い(site-packages・標準ライブラリ — 指紋が覆う —
#: か、解けない)。
Resolution = tuple[Candidate, ...]

_V = TypeVar("_V")


def _grouped(pairs: Iterable[tuple[str, _V]]) -> dict[str, tuple[_V, ...]]:
    """(鍵, 値) の並びを鍵ごとの値の tuple にする(値は来た順のまま)— 名から宣言を引く索引を作るため。"""
    ordered = sorted(((key, order, value) for order, (key, value) in enumerate(pairs)), key=itemgetter(0, 1))
    return {key: tuple(value for _, _, value in group) for key, group in groupby(ordered, key=itemgetter(0))}


def _resolution_json(resolution: Resolution) -> str:
    """解き先を digest の材料の文字列にするため。"""
    return json.dumps([[candidate.path, candidate.kind] for candidate in resolution], ensure_ascii=False)


# --- 運びの文書 ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FileInputs:
    """file 1 つのこの回の入力 — path = 根からの相対 path・inputs = 入力の digest(InputGraph.inputs_of)。"""

    path: str
    inputs: str


@dataclass(frozen=True)
class FileRows:
    """file 1 つのこの回の pyright の診断(pyright の答えの順)。"""

    path: str
    rows: tuple[CarriedRow, ...]


@dataclass(frozen=True)
class CarriedFile:
    """運びの文書の file 1 つ — inputs = 当てた時の入力の digest・checked = 当てた回の番号・rows = その回の pyright の診断(pyright の
    答えの順 — 運んだ回の出力の並びを、全部当てた回と同じにする)。"""

    path: str
    inputs: str
    checked: int
    rows: tuple[CarriedRow, ...]


@dataclass(frozen=True)
class CarryRecord:
    """運びの文書 1 つ — fingerprint = 環境の指紋・run = 回の番号・files = file ごとの記録(path の順)。"""

    fingerprint: str
    run: int
    files: tuple[CarriedFile, ...]


class CarryUnreadableError(ValueError):
    """運びの file が在るのに読めない・形か版が違う(呼び手は全部を当てる — 運びは測りの近道で、測りを止める訳にしない)。"""


def read_carry(path: Path) -> CarryRecord | None:
    """前の回の運びの file を読むため(None = file が無い — 初回)。読めない・形か版が違えば CarryUnreadableError。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise CarryUnreadableError(f"運びの file {path} を読めない: {error}") from error
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise CarryUnreadableError(f"運びの file {path} が JSON でない: {error}") from error
    return _record_of(loaded, path)


def _record_of(loaded: object, path: Path) -> CarryRecord:
    """運びの文書の JSON を読むため(JSON の境界 — 欠けた欄を既定で埋めない)。"""
    match loaded:
        case {"version": 1, "fingerprint": str(fingerprint), "run": int(run), "files": dict(files)}:
            return CarryRecord(
                fingerprint, run, tuple(_file_of(str(key), value, path) for key, value in sorted(files.items()))
            )
        case {"version": version} if version != CARRY_VERSION:
            raise CarryUnreadableError(f"運びの file {path} の版 {version!r} は読めない(読める版 = {CARRY_VERSION})")
        case _:
            raise CarryUnreadableError(f"運びの file {path} の形が違う(version・fingerprint・run・files が要る)")


def _file_of(key: str, value: object, path: Path) -> CarriedFile:
    """運びの文書の file 1 つを読むため。"""
    match value:
        case {"inputs": str(inputs), "checked": int(checked), "rows": list(rows)}:
            return CarriedFile(key, inputs, checked, tuple(_row_of(row, path) for row in rows))
        case _:
            raise CarryUnreadableError(f"運びの file {path} の {key} に inputs・checked・rows が揃っていない: {value!r}"[:400])


def _row_of(row: object, path: Path) -> CarriedRow:
    """運びの文書の診断 1 つを読むため。"""
    match row:
        case [int(line), int(column), str(severity), str(rule), str(message)]:
            return CarriedRow(line, column, severity, rule, message)
        case _:
            raise CarryUnreadableError(f"運びの file {path} の診断の形が違う: {row!r}"[:400])


@dataclass(frozen=True)
class ReasonCount:
    """当てた訳 1 つ(REASONS の語)と、その訳で当てた file の数。"""

    reason: str
    count: int


@dataclass(frozen=True)
class Summary:
    """1 回の運びの数 — checked = pyright に当てた file・carried = 前の回の診断を運んだ file・reasons = 当てた訳ごとの数(REASONS の
    順)・mismatched = 見直しで当て直して、運んでいた診断と答えが違った file。"""

    run: int
    checked: int
    carried: int
    reasons: tuple[ReasonCount, ...]
    mismatched: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        """使い手が読む運びの文書の欄 summary の形にするため(JSON の境界 — 名は MISMATCH_SHOWN まで・数は全部)。"""
        return {
            "run": self.run,
            "checked": self.checked,
            "carried": self.carried,
            "reasons": {count.reason: count.count for count in self.reasons},
            "refreshMismatches": len(self.mismatched),
            "mismatched": list(self.mismatched[:MISMATCH_SHOWN]),
        }

    def line(self) -> str:
        """この回にどれだけ当て、どれだけ運んだかを人に見せる 1 行にするため(標準エラーへ出す)。"""
        reasons = "・".join(f"{count.reason} {count.count}" for count in self.reasons if count.count) or "なし"
        shown = "・".join(self.mismatched[:MISMATCH_SHOWN])
        mismatch = f": {shown}" if self.mismatched else ""
        return (
            f"運び(回 {self.run}): pyright に当てた .hy {self.checked} 本・前の回の診断を運んだ .hy {self.carried} 本"
            f"(当てた訳: {reasons})— 見直しで答えが違った {len(self.mismatched)} 本{mismatch}"
        )


def carry_text(record: CarryRecord, summary: Summary) -> str:
    """運びの文書を JSON の文字列にするため(JSON の境界 — file は path の順・同じ中身なら同じ文字列)。"""
    files = {
        carried.path: {
            "inputs": carried.inputs,
            "checked": carried.checked,
            "rows": [[row.line, row.column, row.severity, row.rule, row.message] for row in carried.rows],
        }
        for carried in sorted(record.files, key=lambda f: f.path)
    }
    document = {
        "version": CARRY_VERSION,
        "fingerprint": record.fingerprint,
        "run": record.run,
        "summary": summary.as_json(),
        "files": files,
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_carry(path: Path, text: str) -> None:
    """この回の運びの文書を次の回のために残すため(同じ dir の別名に書いてから置き換える — 途中で切られても前の回の物が残る)。"""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


# --- 当てる file の決め ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Checking:
    """この回に pyright に当てる file 1 つ — reason = 訳(REASONS の語)・before = 見直しで当てる時の前の回の記録(当て直した答えと
    比べる。見直しでなければ None)。"""

    path: str
    reason: str
    before: CarriedFile | None = None


@dataclass(frozen=True)
class Plan:
    """この回の運びの計画 — checking = pyright に当てる file・carried = 前の回の診断を運ぶ file・run = この回の番号。"""

    checking: tuple[Checking, ...]
    carried: tuple[CarriedFile, ...]
    run: int

    @property
    def checked_paths(self) -> frozenset[str]:
        """pyright に当てる file の相対 path の集合(当てる file を選ぶため)。"""
        return frozenset(checking.path for checking in self.checking)


def plan_carry(
    previous: CarryRecord | CarryUnreadableError | None, fingerprint: str, inputs: Sequence[FileInputs], refresh_runs: int
) -> Plan:
    """純粋: 前の回の運び(None = 無い・CarryUnreadableError = 読めない)と今の入力から、この回に当てる file と運ぶ file を決めるため。"""
    if previous is None or isinstance(previous, CarryUnreadableError) or previous.fingerprint != fingerprint:
        reason = "absent" if previous is None else "unreadable" if isinstance(previous, CarryUnreadableError) else "environment"
        run = previous.run + 1 if isinstance(previous, CarryRecord) else 1
        return Plan(tuple(Checking(file.path, reason) for file in inputs), (), run)
    known = {carried.path: carried for carried in previous.files}  # path から前の回の記録を引く索引(この関数の中だけ)
    moved = tuple(
        Checking(file.path, "new" if before is None else "changed")
        for file in inputs
        if (before := known.get(file.path)) is None or before.inputs != file.inputs
    )
    moved_paths = frozenset(checking.path for checking in moved)
    same = tuple(known[file.path] for file in inputs if file.path not in moved_paths)
    quota = math.ceil(len(inputs) / refresh_runs) if refresh_runs > 0 else 0
    refreshed = tuple(sorted(same, key=lambda carried: (carried.checked, carried.path))[:quota])
    refreshed_paths = frozenset(carried.path for carried in refreshed)
    return Plan(
        (*moved, *(Checking(carried.path, "refresh", carried) for carried in refreshed)),
        tuple(carried for carried in same if carried.path not in refreshed_paths),
        previous.run + 1,
    )


def next_record(
    plan: Plan, fingerprint: str, inputs: Sequence[FileInputs], fresh: Sequence[FileRows]
) -> tuple[CarryRecord, Summary]:
    """純粋: この回の運びの文書と数を作るため(当てた file は今回の診断 fresh と今回の番号、運んだ file は前の回の記録のまま)。"""
    answered = {rows.path: rows.rows for rows in fresh}  # path からこの回の診断を引く索引(この関数の中だけ)
    carried = {kept.path: kept for kept in plan.carried}  # path から運ぶ記録を引く索引(この関数の中だけ)
    checked = plan.checked_paths
    files = tuple(
        CarriedFile(file.path, file.inputs, plan.run, answered.get(file.path, ()))
        if file.path in checked
        else carried[file.path]
        for file in inputs
    )
    mismatched = tuple(
        sorted(
            checking.path
            for checking in plan.checking
            if checking.before is not None
            and sorted(checking.before.rows) != sorted(answered.get(checking.path, ()))
        )
    )
    reasons = tuple(
        ReasonCount(reason, sum(1 for checking in plan.checking if checking.reason == reason)) for reason in REASONS
    )
    summary = Summary(plan.run, len(plan.checking), len(plan.carried), reasons, mismatched)
    return CarryRecord(fingerprint, plan.run, files), summary


# --- 環境の指紋 ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Environment:
    """環境の指紋と、中身を file ごとに見る python の探し道(prefix の外の dir — editable で入れた checkout の dir)。"""

    fingerprint: str
    searched: tuple[Path, ...]


def semantics_digest(functions: Iterable[Callable[..., object]]) -> str:
    """pyright の診断を作って .hy の位置へ戻す関数の source と、この module の source の digest を作るため(どちらかが替われば、
    前の回の診断を運ばない)。"""
    return _digest((*(inspect.getsource(function) for function in functions), Path(__file__).read_text(encoding="utf-8")))


def probe_environment(settings: Mapping[str, object], python: str, pyright: str, semantics: str) -> Environment:
    """環境の指紋を作るため(pyright の版と python の探し道を子の process で読む — 読めなければ RuntimeError)。"""
    version = subprocess.run([pyright, "--version"], capture_output=True, text=True, check=False)
    named = _PYRIGHT_VERSION.search(version.stdout + version.stderr)
    if version.returncode != 0 or named is None:
        raise RuntimeError(f"pyright の版を読めない(exit {version.returncode}): {(version.stdout + version.stderr)[:400]}")
    probed = subprocess.run([python, "-B", "-I", "-c", _PATH_PROBE], capture_output=True, text=True, check=False)
    try:
        answer: object = json.loads(probed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"python の探し道を読めない(exit {probed.returncode}): {probed.stderr[:400]}") from error
    match answer:
        case {"path": list(listed), "prefix": str(prefix), "base": str(base), "version": str(python_version)}:
            entries = tuple(str(e) for e in listed if isinstance(e, str) and e and Path(e).is_dir())
        case _:
            raise RuntimeError(f"python の探し道の形が違う: {probed.stdout[:400]}")
    fixed = tuple(Path(p).resolve() for p in (prefix, base))
    under_prefix = {entry: any(Path(entry).resolve().is_relative_to(f) for f in fixed) for entry in entries}
    listings = [[entry, _listing(Path(entry))] for entry in entries if under_prefix[entry]]
    searched = tuple(dict.fromkeys(Path(os.path.normpath(Path(e).absolute())) for e in entries if not under_prefix[e]))
    fingerprint = _digest(
        (
            str(CARRY_VERSION),
            named.group(0),
            json.dumps(dict(settings), sort_keys=True, ensure_ascii=False, default=str),
            python_version,
            json.dumps(listed, ensure_ascii=False, default=str),
            json.dumps(listings, ensure_ascii=False),
            semantics,
        )
    )
    return Environment(fingerprint, searched)


def _listing(place: Path) -> list[str]:
    """prefix の下の探し道 1 つの型を覆う指紋の材料にするため — 中身の名(dist-info の名 = 配布物の版)と .pth の中身。"""
    return [
        piece
        for name in sorted(os.listdir(place))
        if name != "__pycache__"
        for piece in ((name, _text_of(place / name)) if name.endswith(".pth") else (name,))
    ]


def _text_of(path: Path) -> str:
    """小さい file の中身(読めなければその訳)— 指紋の材料。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"読めない: {error}"


def _digest(parts: Iterable[str]) -> str:
    """文字列の並びを 1 つの sha256 にするため(長さを前に置いて繋ぐ — 区切りの取り違えで別の並びを同じ digest にしない)。"""
    hasher = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


# --- module の中身の読み ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Imported:
    """import の行き先 1 つ — module = import した module の綴り・level = 相対の深さ・name = from の名(None = module そのもの)・
    whole = `import a.b.c`(as 無し)が束ねた根の名の時の、import した module の全部の綴り(a.b.c)。"""

    module: str
    level: int
    name: str | None = None
    whole: str | None = None


@dataclass(frozen=True)
class Statement:
    """module の段の文 1 つの表面 — surface = 縮めた文の ast.dump(位置なし)・chains = 表面が読む名と属性の連なり・imports = 表面の中の
    import が束ねる名 → 行き先(本体を残した関数の中の import も)・binds = この文が module の段で import で束ねる名 → 行き先。"""

    surface: str
    chains: tuple[tuple[str, ...], ...]
    imports: Mapping[str, tuple[Imported, ...]]
    binds: Mapping[str, tuple[Imported, ...]]


@dataclass(frozen=True)
class Facts:
    """module の file 1 つの読み — statements = module の段の文の表面・bound = 名 → それを束ねる文の番号・defined = import でない文が
    束ねる名・stars = star import の先・header = __all__ を読み書きする文の ast.dump・module_imports = module の段の import が束ねる名 →
    行き先・volatile = 構文の誤りで読めない(回ごとに違う digest にする)。"""

    statements: tuple[Statement, ...]
    bound: Mapping[str, tuple[int, ...]]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str
    module_imports: Mapping[str, tuple[Imported, ...]]
    volatile: bool = False


_VOLATILE = Facts((), {}, frozenset(), (), "", {}, volatile=True)


@dataclass(frozen=True)
class _Read:
    """module の段の文 1 つ(import の文は名 1 つ分)の読み — 表面と、束ねる名(names = import も・defined = import でない文の物)と、
    star import の先と、__all__ の文なら header に入れる ast.dump。"""

    statement: Statement
    names: frozenset[str]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str | None


def module_facts(text: str) -> Facts:
    """純粋: module の Python の source を、名ごとの宣言の表面を引ける形に読むため(読めなければ volatile)。"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _VOLATILE
    body = tree.body[1:] if tree.body and _is_docstring(tree.body[0]) else tree.body
    reads = tuple(read for statement in body for read in _reads_of(statement))
    return Facts(
        tuple(read.statement for read in reads),
        _grouped((name, index) for index, read in enumerate(reads) for name in sorted(read.names)),
        frozenset(name for read in reads for name in read.defined),
        tuple(star for read in reads for star in read.stars),
        "\n".join(read.header for read in reads if read.header is not None),
        _grouped((name, target) for read in reads for name, targets in read.statement.binds.items() for target in targets),
    )


def _reads_of(statement: ast.stmt) -> Iterator[_Read]:
    """module の段の文 1 つを読むため。module の段の import の文は名ごとの文に分ける(1 つの文に並んだ名の 1 つを足しても、他の名の
    宣言の digest を変えない)。"""
    if isinstance(statement, (ast.Import, ast.ImportFrom)) and all(a.name != "*" for a in statement.names):
        for alias in statement.names:
            single: ast.Import | ast.ImportFrom = (
                ast.Import(names=[alias])
                if isinstance(statement, ast.Import)
                else ast.ImportFrom(module=statement.module, names=[alias], level=statement.level)
            )
            name, target = next(_import_targets(single))
            yield _Read(Statement(ast.dump(single), (), {}, {name: (target,)}), frozenset({name}), frozenset(), (), None)
        return
    binds = _grouped(
        (name, target)
        for inner in _module_scope_statements(statement)
        if isinstance(inner, (ast.Import, ast.ImportFrom))
        for name, target in _import_targets(inner)
    )
    names = frozenset(_bound_names(statement, imports=True))
    defined = frozenset(_bound_names(statement, imports=False))
    stars = tuple(_module_stars(statement))
    header = ast.dump(statement) if any(isinstance(n, ast.Name) and n.id == "__all__" for n in ast.walk(statement)) else None
    reduced = _Reducer().visit(statement)  # 木はこの読みだけの物 — その場で縮める
    chains = tuple(dict.fromkeys(chains_of(reduced)))
    yield _Read(Statement(ast.dump(reduced), chains, _imports_within(reduced), binds), names, defined, stars, header)


def _is_docstring(statement: ast.stmt) -> bool:
    """文が docstring(文字列の定数だけの式の文)か — 表面から説明の文を落とすため。"""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _trivial(body: Sequence[ast.stmt]) -> bool:
    """本体が空(`...`・`pass`・NotImplementedError の raise だけ)かを判じるため(docstring を落とした後の本体で)。"""
    return all(_trivial_statement(statement) for statement in body)


def _trivial_statement(statement: ast.stmt) -> bool:
    """空の本体の文 1 つか(`...`・`pass`・NotImplementedError の raise)。"""
    match statement:
        case ast.Pass():
            return True
        case ast.Expr(value=ast.Constant(value=value)):
            return value is Ellipsis
        case ast.Raise(exc=ast.Name(id="NotImplementedError") | ast.Call(func=ast.Name(id="NotImplementedError"))):
            return True
        case _:
            return False


def _fully_annotated(function: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> bool:
    """関数の引数(method の self / cls を除く)と答えに型の宣言が全部在るか — 本体を表面から落としてよいかを判じるため。"""
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if method and positional and not _decorated_with(function, "staticmethod"):
        positional = positional[1:]
    starred = [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
    return function.returns is not None and all(
        argument.annotation is not None for argument in (*positional, *arguments.kwonlyargs, *starred)
    )


def _decorated_with(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """関数が名 name(か属性の連なりの末尾が name)の decorator を持つか — staticmethod の最初の引数を self と見ないため。"""
    return any(
        (isinstance(d, ast.Name) and d.id == name) or (isinstance(d, ast.Attribute) and d.attr == name)
        for d in function.decorator_list
    )


def _assigned_targets(node: ast.AST) -> Iterator[ast.expr]:
    """木の中の代入・削除の的の全部 — method が self / cls の属性へ代入するかを判じるため。"""
    for inner in ast.walk(node):
        match inner:
            case ast.Assign(targets=assigned) | ast.Delete(targets=assigned):
                yield from assigned
            case ast.AnnAssign(target=target) | ast.AugAssign(target=target) | ast.NamedExpr(target=target):
                yield target
            case ast.For(target=target) | ast.AsyncFor(target=target) | ast.comprehension(target=target):
                yield target
            case ast.withitem(optional_vars=ast.expr() as target):
                yield target
            case _:
                pass


def _assigns_attribute_of(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """method が最初の引数(self / cls)の属性へ代入・削除するか(pyright はそこから属性の型を推すので、本体は表面)。"""
    positional = [*function.args.posonlyargs, *function.args.args]
    if not positional:
        return False
    owner = positional[0].arg
    return any(
        isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id == owner
        for target in _assigned_targets(function)
        for inner in ast.walk(target)
    )


def _declares_scope(function: ast.AST) -> bool:
    """関数の中に global / nonlocal の宣言が在るか(外の名を束ねる — 本体を表面に残すため)。"""
    return any(isinstance(node, (ast.Global, ast.Nonlocal)) for node in ast.walk(function))


class _Reducer(ast.NodeTransformer):
    """module の段の文を、呼び手の型に効く表面へ縮めるため(頭の註「表面」)。木はこの読みだけの物なので、その場で替える。"""

    def __init__(self) -> None:
        self._mut_in_class = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """関数を縮める(_function)。"""
        return self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        """async 関数を縮める(_function)。"""
        return self._function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        """class は全体を残すため、docstring だけを落として method を縮める。"""
        node.body = _without_docstring(node.body)
        self._mut_in_class += 1
        try:
            return self.generic_visit(node)
        finally:
            self._mut_in_class -= 1

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        """型を全部宣言し、外の名を束ねず、(method なら)self / cls の属性へ代入しない関数は、本体を空の本体か印に替える。それ以外は
        本体を残し、中の docstring だけを落とす。"""
        method = self._mut_in_class > 0
        keeps_body = (
            not _fully_annotated(node, method) or _declares_scope(node) or (method and _assigns_attribute_of(node))
        )
        if keeps_body:
            node.body = _without_docstring(node.body)
            inside = self._mut_in_class
            self._mut_in_class = 0  # 本体の中の関数は method ではない
            try:
                return self.generic_visit(node)
            finally:
                self._mut_in_class = inside
        stripped = [s for s in node.body if not _is_docstring(s)]
        if not stripped:
            node.body = [ast.Expr(value=ast.Constant(value=_DOCSTRING_MARK))]
        elif not _trivial(stripped):
            node.body = [ast.Expr(value=ast.Constant(value=_BODY_MARK))]
        else:
            node.body = stripped
        return node


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """本体の頭の docstring を落とすため(空になれば pass — 本体は空にできない)。"""
    if body and _is_docstring(body[0]):
        return body[1:] or [ast.Pass()]
    return body


def chains_of(node: ast.AST) -> Iterator[tuple[str, ...]]:
    """木の中で読まれる名と属性の連なりを集めるため(`a.b.c` は 1 つの連なり ('a', 'b', 'c'))。文字列の定数は注記の式として読めれば、
    その中の名も(pyright は文字列の注記を式として解く)。"""
    nodes = tuple(ast.walk(node))
    inner = {id(n.value) for n in nodes if isinstance(n, ast.Attribute) and _chain(n) is not None}
    for current in nodes:
        if id(current) in inner:
            continue
        match current:
            case ast.Attribute():
                chain = _chain(current)
                if chain is not None:
                    yield chain
            case ast.Name(id=name):
                yield (name,)
            case ast.Constant(value=str(text)) if 0 < len(text) <= _ANNOTATION_TEXT_LIMIT:
                parsed = _expression(text)
                if parsed is not None:
                    yield from chains_of(parsed)
            case _:
                pass


def _expression(text: str) -> ast.AST | None:
    """文字列を式として読む(読めなければ None)— 文字列の注記の中の名を拾うため。"""
    try:
        return ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError):
        return None


def _chain(node: ast.expr) -> tuple[str, ...] | None:
    """名から始まる属性の連なりの綴り(それ以外の式は None)— import した module の名を辿るため。"""
    match node:
        case ast.Name(id=name):
            return (name,)
        case ast.Attribute(value=value, attr=attr):
            base = _chain(value)
            return None if base is None else (*base, attr)
        case _:
            return None


def _import_targets(statement: ast.Import | ast.ImportFrom) -> Iterator[tuple[str, Imported]]:
    """import の文 1 つが束ねる名と行き先(star は返さない)— 名から宣言の在りかを引くため。"""
    match statement:
        case ast.Import(names=names):
            for alias in names:
                if alias.asname is not None:
                    yield alias.asname, Imported(alias.name, 0)
                else:
                    root = alias.name.split(".")[0]
                    yield root, Imported(root, 0, whole=alias.name)
        case ast.ImportFrom(module=module, level=level, names=names):
            for alias in names:
                if alias.name != "*":
                    yield alias.asname or alias.name, Imported(module or "", level, alias.name)


def _imports_within(node: ast.AST) -> dict[str, tuple[Imported, ...]]:
    """木の中(本体を残した関数の中も)の import が束ねる名 → 行き先 — 表面の中の名を解くため。"""
    return _grouped(
        (name, target)
        for inner in ast.walk(node)
        if isinstance(inner, (ast.Import, ast.ImportFrom))
        for name, target in _import_targets(inner)
    )


def _nested_statements(statement: ast.stmt) -> Iterator[ast.stmt]:
    """文の中の、module の段のままの文(if・try・for・while・with・match の本体 — 関数・class の本体は返さない)。"""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for field in ("body", "orelse", "finalbody"):
        inner = getattr(statement, field, None)
        if isinstance(inner, list):
            yield from (s for s in inner if isinstance(s, ast.stmt))
    handlers = getattr(statement, "handlers", None)  # Try・TryStar
    if isinstance(handlers, list):
        yield from (s for h in handlers if isinstance(h, ast.ExceptHandler) for s in h.body)
    if isinstance(statement, ast.Match):
        yield from (s for case in statement.cases for s in case.body)


def _module_scope_statements(statement: ast.stmt) -> Iterator[ast.stmt]:
    """module の段の文と、その中の module の段のままの文の全部 — module の名を束ねる文を探すため。"""
    yield statement
    for inner in _nested_statements(statement):
        yield from _module_scope_statements(inner)


def _module_stars(statement: ast.stmt) -> Iterator[Imported]:
    """module の段の star import の先 — 名が module に無い時に探す先。"""
    for inner in _module_scope_statements(statement):
        if isinstance(inner, ast.ImportFrom) and any(alias.name == "*" for alias in inner.names):
            yield Imported(inner.module or "", inner.level)


def _target_names(target: ast.AST) -> Iterator[str]:
    """代入の的が束ねる名(tuple・list・star の中も。属性と添字は名を束ねない)。"""
    match target:
        case ast.Name(id=name):
            yield name
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                yield from _target_names(element)
        case ast.Starred(value=value):
            yield from _target_names(value)
        case _:
            pass


def _walrus_names(node: ast.AST) -> Iterator[str]:
    """式の中の `:=` が束ねる名(関数・lambda・class の本体の中は数えない — 内包表記の中は外の scope を束ねる)。"""
    if isinstance(node, ast.NamedExpr):
        yield from _target_names(node.target)
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _walrus_names(child)


def _bound_names(statement: ast.stmt, imports: bool) -> Iterator[str]:
    """module の段の文 1 つが module の段で束ねる名の全部(入れ子の if・try ほかの中・関数の中の global も)— imports = import の文が
    束ねる名も数えるか。"""
    for inner in _module_scope_statements(statement):
        yield from _names_bound_here(inner, imports)


def _names_bound_here(inner: ast.stmt, imports: bool) -> Iterator[str]:
    """文 1 つがその文の所で module の段に束ねる名(入れ子の文は _bound_names が辿る)— 文の的と、except の名と、文の頭の式の `:=`。"""
    yield from _statement_targets(inner, imports)
    yield from _names_around(inner)


def _statement_targets(inner: ast.stmt, imports: bool) -> Iterator[str]:
    """文 1 つの的(定義の名・import の名・代入の的・for と with の的・match の型・型の別名)が束ねる名。"""
    match inner:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            yield name
            yield from (n for node in ast.walk(inner) if isinstance(node, ast.Global) for n in node.names)
            yield from (n for d in inner.decorator_list for n in _walrus_names(d))
        case ast.Import() | ast.ImportFrom():
            if imports:
                yield from (name for name, _ in _import_targets(inner))
        case ast.Assign(targets=targets) | ast.Delete(targets=targets):
            yield from (n for t in targets for n in _target_names(t))
        case ast.AnnAssign(target=target) | ast.AugAssign(target=target):
            yield from _target_names(target)
        case ast.For(target=target) | ast.AsyncFor(target=target):
            yield from _target_names(target)
        case ast.With(items=items) | ast.AsyncWith(items=items):
            yield from (n for item in items if item.optional_vars is not None for n in _target_names(item.optional_vars))
        case ast.Match(cases=cases):
            yield from (
                n
                for case in cases
                for node in ast.walk(case.pattern)
                for n in _pattern_names(node)
            )
        case _:
            type_alias = getattr(ast, "TypeAlias", None)  # 3.12 から
            if type_alias is not None and isinstance(inner, type_alias):
                named = vars(inner).get("name")  # 型の別名の文の名(3.12 の ast.TypeAlias)
                if isinstance(named, ast.AST):
                    yield from _target_names(named)


def _names_around(inner: ast.stmt) -> Iterator[str]:
    """文 1 つの except … as の名と、文の頭の式(値・条件・反復の元・with の式・match の主)の `:=` が束ねる名。"""
    handlers = getattr(inner, "handlers", None)  # Try・TryStar の except … as 名
    if isinstance(handlers, list):
        yield from (h.name for h in handlers if isinstance(h, ast.ExceptHandler) and h.name)
    if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
        # 文の頭の式(値・条件・反復の元・with の式・match の主)の `:=`。本体の文は _bound_names が辿る。
        yield from (
            n
            for field, value in ast.iter_fields(inner)
            if field not in ("body", "orelse", "finalbody", "handlers", "cases")
            for item in (value if isinstance(value, list) else [value])
            if isinstance(item, ast.AST)
            for n in _walrus_names(item)
        )


def _pattern_names(node: ast.AST) -> Iterator[str]:
    """match の型の部品 1 つが束ねる名。"""
    match node:
        case ast.MatchAs(name=str(name)) | ast.MatchStar(name=str(name)):
            yield name
        case ast.MatchMapping(rest=str(name)):
            yield name
        case _:
            pass


# --- 入力の digest ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Node:
    """依存の節 — 解き先の module の名 1 つの宣言(name = None なら module の全部の名 — module を値で使う参照・star import)。"""

    resolution: Resolution
    name: str | None


@dataclass(frozen=True)
class _Expanded:
    """節 1 つの中身の digest(local)と、表面が使う節(successors)。"""

    local: str
    successors: tuple[_Node, ...]


#: 節の中身の読みの 1 片 — 中身の digest の材料(str)か、表面が使う節。
_Piece = str | _Node


class InputGraph:
    """file ごとの入力の digest を作るため(頭の註「入力」)。root = repo の根・roots = import の根(根と extraPaths)・stub_dir = pyright の
    stubPath・environment = 環境の指紋と探し道・staged = 根の下の .hy → 展開した Python(pyright が `<名>.py` として読む物)・failed =
    展開に失敗した .hy(pyright には見えない)。1 回の実行の中で、dir の中身・読み・解き先・節の digest を覚える。"""

    def __init__(
        self,
        root: Path,
        roots: Sequence[Path],
        stub_dir: Path,
        environment: Environment,
        staged: Mapping[Path, str],
        failed: Iterable[Path],
    ) -> None:
        self._root = root
        self._environment = environment
        self._global_dirs = tuple(dict.fromkeys([*roots, stub_dir, *environment.searched]))
        self._staged = staged
        self._failed = frozenset(failed)
        self._mut_entries: dict[Path, dict[str, bool] | None] = {}
        self._mut_facts: dict[str, Facts] = {}
        self._mut_in_dir: dict[tuple[Path, str, bool], Resolution] = {}
        self._mut_expanded: dict[_Node, _Expanded] = {}
        self._mut_deep: dict[_Node, str] = {}
        self._mut_texts: dict[Path, str] = {}

    # 解き先 ------------------------------------------------------------------------------------------------------------------

    def _entries(self, directory: Path) -> dict[str, bool]:
        """dir の中身の名 → dir か(dir でなければ空)を、1 回の実行の中で dir ごとに 1 度だけ読むため。"""
        known = self._mut_entries.get(directory)
        if known is not None or directory in self._mut_entries:
            return known or {}
        try:
            with os.scandir(directory) as scanned:
                listed: dict[str, bool] | None = {entry.name: entry.is_dir() for entry in scanned}
        except OSError:
            listed = None
        self._mut_entries[directory] = listed
        return listed or {}

    def _marker(self, path: Path) -> str:
        """py.typed の中身の digest(partial の印を見る — package の型の読まれ方を変える)。"""
        if path not in self._mut_texts:
            self._mut_texts[path] = _digest((_text_of(path),))
        return self._mut_texts[path]

    def _hy_kind(self, path: Path) -> str:
        """根の下の .hy の種類(展開できた = hy・展開に失敗した = hy-failed・展開の範囲の外 = hy-unstaged)。"""
        if path in self._staged:
            return "hy"
        return "hy-failed" if path in self._failed else "hy-unstaged"

    def _sources_at(self, base: Path, leaf: str, stub_package: bool) -> Iterator[Candidate]:
        """dir base の中の module の file(leaf.pyi・.py・根の下だけ .hy)と、同じ名の native の module。"""
        entries = self._entries(base)
        in_root = base.is_relative_to(self._root)
        for suffix, kind in ((".pyi", "pyi"), (".py", "py"), (".hy", "hy")):
            name = leaf + suffix
            if entries.get(name) is False and (suffix == ".pyi" or not stub_package) and (suffix != ".hy" or in_root):
                yield Candidate(str(base / name), self._hy_kind(base / name) if kind == "hy" else kind)
        if not stub_package:
            yield from (
                Candidate(str(base / name), "native")
                for name in sorted(entries)
                if entries[name] is False and name.startswith(leaf + ".") and name.endswith((".so", ".pyd"))
            )

    def _package_files(self, here: Path, stub_package: bool) -> Iterator[Candidate]:
        """package の dir here と、その __init__ の file(根の下だけ .hy も)。"""
        yield Candidate(str(here), "package")
        entries = self._entries(here)
        in_root = here.is_relative_to(self._root)
        for init, kind in (("__init__.pyi", "pyi"), ("__init__.py", "py"), ("__init__.hy", "hy")):
            if entries.get(init) is False and (kind == "pyi" or not stub_package) and (kind != "hy" or in_root):
                yield Candidate(str(here / init), self._hy_kind(here / init) if kind == "hy" else kind)

    def _candidates(self, base: Path, parts: Sequence[str], stubs: bool) -> Iterator[Candidate]:
        """dir base の下で module(parts)に pyright が当たりうる候補のうち、在る物(stub の package・py.typed・親の package の印も)。"""
        tops = (f"{parts[0]}-stubs", parts[0]) if stubs else (parts[0],)
        for top in tops:
            stub_package = top != parts[0]
            top_dir = base / top
            if self._entries(top_dir).get("py.typed") is False:
                yield Candidate(str(top_dir / "py.typed"), f"typed:{self._marker(top_dir / 'py.typed')}")
            parents = [top_dir.joinpath(*parts[1:i]) for i in range(1, len(parts))]
            yield from (
                Candidate(str(parent / init), "init")
                for parent in parents
                for init in ("__init__.pyi", "__init__.py", "__init__.hy")
                if self._entries(parent).get(init) is False
            )
            here = top_dir.joinpath(*parts[1:])
            yield from self._sources_at(here.parent, here.name, stub_package)
            if self._entries(here.parent).get(here.name) is True:
                yield from self._package_files(here, stub_package)

    def _in_dir(self, base: Path, dotted: str, stubs: bool) -> Resolution:
        """dir base の下の module(dotted)の候補を、1 回の実行の中で 1 度だけ探すため。"""
        key = (base, dotted, stubs)
        if key not in self._mut_in_dir:
            self._mut_in_dir[key] = tuple(self._candidates(base, dotted.split("."), stubs))
        return self._mut_in_dir[key]

    def _local_dirs(self, importer: Path) -> tuple[Path, ...]:
        """import する file の dir から探し道の根までの親(pyright は探し道で解けない絶対の import をここでも探す — static_check の
        search_dirs と同じ考え。根の外の file は、その file を含む一番深い探し道の dir まで)。"""
        containing = [d for d in (self._root, *self._global_dirs) if importer.is_relative_to(d)]
        deepest = max(containing, key=lambda d: len(d.parts), default=importer.parent)
        stop = self._root if importer.is_relative_to(self._root) else deepest
        return tuple(d for d in (importer.parent, *importer.parent.parents) if d.is_relative_to(stop))

    def resolve(self, importer: Path, target: Imported) -> Resolution:
        """import の行き先 1 つの module の解き先を引くため(相対の import は import する file の dir から)。"""
        if target.level > 0:
            base = importer.parents[target.level - 1]
            return self._package_at(base) if not target.module else self._in_dir(base, target.module, stubs=False)
        if not target.module:
            return ()
        dirs = dict.fromkeys((*self._global_dirs, *self._local_dirs(importer)))
        return tuple(dict.fromkeys(c for d in dirs for c in self._in_dir(d, target.module, stubs=True)))

    def _package_at(self, base: Path) -> Resolution:
        """dir base そのものの package(`from . import x` の module)の解き先。"""
        return tuple(self._package_files(base, stub_package=False))

    def _submodule(self, resolution: Resolution, name: str) -> Resolution:
        """解き先の package の下の module(name)の解き先(package の dir と __init__ の在る dir の下)— 名が module に無い時に探す先。"""
        packages = dict.fromkeys(
            Path(c.path) if c.kind == "package" else Path(c.path).parent
            for c in resolution
            if c.kind == "package" or (c.kind in ("pyi", "py", "hy") and Path(c.path).stem == "__init__")
        )
        return tuple(dict.fromkeys(c for p in packages for c in self._in_dir(p, name, stubs=False)))

    # 読み --------------------------------------------------------------------------------------------------------------------

    def _facts(self, path: str, kind: str) -> Facts:
        """候補の file 1 つを、1 回の実行の中で 1 度だけ読むため(展開した .hy は展開した Python を、.py / .pyi は file を読む)。"""
        known = self._mut_facts.get(path)
        if known is not None:
            return known
        if kind == "hy":
            text = self._staged.get(Path(path))
        else:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = None
        facts = _VOLATILE if text is None else module_facts(text)
        self._mut_facts[path] = facts
        return facts

    def _contents(self, resolution: Resolution) -> Iterator[tuple[Path, Facts]]:
        """解き先の候補のうち中身を読む file(.pyi・.py・展開した .hy)とその読み。"""
        for candidate in resolution:
            if candidate.kind in ("pyi", "py", "hy"):
                yield Path(candidate.path), self._facts(candidate.path, candidate.kind)

    # 節 ----------------------------------------------------------------------------------------------------------------------

    def _imported_nodes(self, importer: Path, targets: Sequence[Imported], chain: tuple[str, ...]) -> Iterator[_Node]:
        """import が束ねた名 chain[0] を頭にした連なりが使う節(targets = その名を束ねた import の行き先の全部)。"""
        prefixes = {
            ".".join(parts[:i])
            for target in targets
            if target.whole is not None
            for parts in (target.whole.split("."),)
            for i in range(1, len(parts) + 1)
        }
        for target in targets:
            if target.name is not None:  # from m import n [as x]
                yield _Node(self.resolve(importer, Imported(target.module, target.level)), target.name)
            elif target.whole is not None:  # import a.b.c(根の名 a を束ねる)
                yield from self._walked_chain(importer, chain, prefixes)
            else:  # import a.b as x
                resolution = self.resolve(importer, target)
                yield _Node(resolution, chain[1] if len(chain) > 1 else None)

    def _walked_chain(self, importer: Path, chain: tuple[str, ...], prefixes: set[str]) -> Iterator[_Node]:
        """`import a.b.c` で束ねた a からの連なり a.b.c.x を、import した module の綴りの所まで辿った節(途中の package の同じ名の宣言も
        — package の __init__ の名は下の module を隠す)。"""
        depth = next((i for i in range(1, len(chain)) if ".".join(chain[: i + 1]) not in prefixes), len(chain))
        for i in range(1, depth):
            yield _Node(self.resolve(importer, Imported(".".join(chain[:i]), 0)), chain[i])
        module = self.resolve(importer, Imported(".".join(chain[:depth]), 0))
        yield _Node(module, chain[depth] if depth < len(chain) else None)

    def _statement_nodes(
        self, path: Path, resolution: Resolution, facts: Facts, statement: Statement, name: str | None
    ) -> Iterator[_Node]:
        """module の段の文 1 つの表面が使う節(表面の中の連なりを、文の中の import・module の段の import・module の段の名・star の先で
        解く)と、その文が import で束ねる名(name — None なら全部)の行き先。"""
        for chain in statement.chains:
            head = chain[0]
            hits = (*statement.imports.get(head, ()), *facts.module_imports.get(head, ()))
            yield from self._imported_nodes(path, hits, chain)
            if head in facts.defined:
                yield _Node(resolution, head)
            elif not hits:
                yield from (_Node(self.resolve(path, star), head) for star in facts.stars)
        for bound, targets in statement.binds.items():
            if name is None or bound == name:
                yield from self._imported_nodes(path, targets, (bound,))

    def _pieces(self, node: _Node) -> Iterator[_Piece]:
        """節 1 つの中身の digest の材料と、表面が使う節。"""
        yield json.dumps([node.name, _resolution_json(node.resolution)], ensure_ascii=False)
        contents = tuple(self._contents(node.resolution))
        for path, facts in contents:
            if facts.volatile:
                yield f"volatile:{uuid.uuid4()}"
                continue
            yield facts.header
            indexes = range(len(facts.statements)) if node.name is None else facts.bound.get(node.name, ())
            for index in indexes:
                statement = facts.statements[index]
                yield statement.surface
                yield from self._statement_nodes(path, node.resolution, facts, statement, node.name)
            if node.name is None:
                yield from (_Node(self.resolve(path, star), None) for star in facts.stars)
            elif not indexes:
                yield "名が無い"
                yield from (_Node(self.resolve(path, star), node.name) for star in facts.stars)
                if "__getattr__" in facts.bound:
                    yield _Node(node.resolution, "__getattr__")
        if node.name is not None and not any(node.name in facts.bound for _, facts in contents):
            below = self._submodule(node.resolution, node.name)
            if below:
                yield _resolution_json(below)
                yield _Node(below, None)

    def _expand(self, node: _Node) -> _Expanded:
        """節 1 つの中身の digest と表面が使う節を、1 回の実行の中で 1 度だけ作るため。"""
        known = self._mut_expanded.get(node)
        if known is not None:
            return known
        pieces = tuple(self._pieces(node))
        expanded = _Expanded(
            _digest(p for p in pieces if isinstance(p, str)),
            tuple(dict.fromkeys(p for p in pieces if isinstance(p, _Node))),
        )
        self._mut_expanded[node] = expanded
        return expanded

    def _deep(self, root: _Node) -> str:
        """節の推移の digest(節と、そこから届く節の全部の中身)を作るため。輪は強連結成分ごとにまとめる — Tarjan の算法を、深い依存で
        再帰の上限に当たらないよう手で回す。"""
        known = self._mut_deep.get(root)
        if known is not None:
            return known
        index: dict[_Node, int] = {}
        low: dict[_Node, int] = {}
        stack: list[_Node] = []
        on_stack: set[_Node] = set()
        work: list[tuple[_Node, Iterator[_Node]]] = []

        def visit(node: _Node) -> None:
            index[node] = low[node] = len(index)
            stack.append(node)
            on_stack.add(node)
            work.append((node, iter(self._expand(node).successors)))

        visit(root)
        while work:
            node, successors = work[-1]
            pending = next((s for s in successors if s not in self._mut_deep and s not in index), None)
            for successor in self._expand(node).successors:
                if successor in on_stack:
                    low[node] = min(low[node], index[successor])
            if pending is not None:
                visit(pending)
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                self._close(node, stack, on_stack)
        return self._mut_deep[root]

    def _close(self, head: _Node, stack: list[_Node], on_stack: set[_Node]) -> None:
        """強連結成分 1 つ(stack の head から上)を閉じ、成分の全部の節に同じ推移の digest を付けるため。"""
        start = len(stack) - 1 - stack[::-1].index(head)
        members = tuple(stack[start:])
        del stack[start:]
        on_stack.difference_update(members)
        inside = set(members)
        locals_ = sorted(self._expand(m).local for m in members)
        outside = sorted({self._mut_deep[s] for m in members for s in self._expand(m).successors if s not in inside})
        digest = _digest(("scc", *locals_, "→", *outside))
        self._mut_deep.update(dict.fromkeys(members, digest))

    def inputs_of(self, source: Path, own: str) -> str:
        """根の下の .hy 1 つの入力の digest を作るため(own = その file 自身の digest — own_digest)。展開を読めなければ回ごとに違う
        digest(毎回当てる)。"""
        text = self._staged.get(source)
        if text is None:
            return f"unstaged:{uuid.uuid4()}"
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return f"volatile:{uuid.uuid4()}"
        imports = _imports_within(tree)
        used = (
            node
            for chain in dict.fromkeys(chains_of(tree))
            if chain[0] in imports
            for node in self._imported_nodes(source, imports[chain[0]], chain)
        )
        named = tuple(self._import_statement_nodes(source, tree))
        resolutions = sorted({_resolution_json(r) for r in self._import_resolutions(source, tree)})
        deep = sorted({self._deep(node) for node in dict.fromkeys((*used, *named))})
        return _digest(("target", self._environment.fingerprint, own, *resolutions, "→", *deep))

    def _import_statement_nodes(self, source: Path, tree: ast.AST) -> Iterator[_Node]:
        """file の from-import の名の全部(使わない名も — 名が module に在るかは、その file の診断に出る)と star の先の節。"""
        for inner in ast.walk(tree):
            if isinstance(inner, ast.ImportFrom):
                resolution = self.resolve(source, Imported(inner.module or "", inner.level))
                yield from (_Node(resolution, None if a.name == "*" else a.name) for a in inner.names)

    def _import_resolutions(self, source: Path, tree: ast.AST) -> Iterator[Resolution]:
        """file が import する module の全部の解き先(解けるか・どこに解けるかは、その file の診断に出る)。"""
        for inner in ast.walk(tree):
            match inner:
                case ast.ImportFrom(module=module, level=level):
                    yield self.resolve(source, Imported(module or "", level))
                case ast.Import(names=names):
                    yield from (self.resolve(source, Imported(alias.name, 0)) for alias in names)
                case _:
                    pass


def own_digest(source_text: str, projection_text: str, spans: object) -> str:
    """.hy 1 つ自身の digest を作るため — source・展開した Python・位置の対応表(診断の位置は展開と対応表で .hy の位置へ戻る)。"""
    return _digest(("own", source_text, projection_text, repr(spans)))
