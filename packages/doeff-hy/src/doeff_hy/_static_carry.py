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
  - __all__: 名 1 つの宣言には、その名が __all__ に在るかだけを入れる(__all__ に別の名を足しても使い手へ伝えない)。__all__ を
    pyright の読める形(文字列の並びの代入・+=・extend・append)でない文で書く module と、module の全部の名(star import)は __all__ の
    文の全部。
- その file が import する module の解き先(探し道の各 dir で、pyright が当たりうる候補の file のうち在る物の並び — Resolution)。
  候補が増える・消える(新しい file が前の解き先を隠す・解けなかった import が解ける)と digest が変わる。名の宣言の digest も、その
  module の解き先を含む。
- 環境の指紋(Environment.fingerprint): 運びの形の版・pyright の版・pyright の設定・python の版と探し道・探し道のうち python の prefix の
  下の dir(site-packages・標準ライブラリ)の中身の名(dist-info の名 = 配布物の版)と .pth の中身・診断を作る関数の source(semantics —
  構文の木なので註と字下げは入らない)。中身を file ごとに見る探し道(根・extraPaths・typings・prefix の外の dir — editable で入れた
  doeff の checkout)は指紋に入れない(editable の dist-info も版を落とした名だけ)— doeff の pin や版が上がっても、変わった宣言を使う
  .hy だけを当て直す。
- 置き場の印(Places): 日次の回は task ごとの作業 dir の下に根を置き、doeff の pin の checkout を根の隣に置く(回ごとに path が違う)。
  digest の材料の中の根と根の親の綴りは印(@root・@parent)に替える — 同じ木を別の置き場で測っても同じ digest になる。

安全の側へ倒す所: 構文の誤りで読めない Python の module は回ごとに違う digest にする(使う file を毎回当てる)。module を値で使う参照・
star import は、その module の全部の名に依る(_Node の name = None)。名が module に無い時は、star import の先・module の __getattr__・
同じ名の下の module に依る。関数の本体の中の名と module の名を区別しない(同じ綴りなら依る — 余分に当てるだけ)。

見直し(refresh_runs): 入力の変わらない file のうち、当てた回が一番古い ceil(本数 / refresh_runs) 本を毎回当て直し、運んだ診断と
違った file を数えて名乗る(summary の refresh_mismatches)— この入力の決めが見落とす依存があれば、ここに出る(pyright の答えが
検める順で揺れる file — card ki-e56135ec1220 — もここに出る)。

運びの文書(JSON・版 CARRY_VERSION): {"version", "fingerprint", "run", "summary", "files": {相対 path: {"inputs", "checked",
"rows": [[行, 列, severity, 規則, 文言], …]}}}。run = 回の番号(運びの無い回が 1)・checked = その file を pyright に当てた回の番号・
rows = その回の pyright の診断(.hy の位置・pyright の答えの順 — 整える前の物。整え(static_check.settle)は毎回、運んだ物と当てた物を合わせてかける)。
summary は使い手(agora-controllers の scripts/hy_type_trend.hy)が読む欄: run・checked・carried・reasons・refresh_mismatches・mismatched。

組ごとの書き直し(--carry-group・agora-redesign card ki-5ea68e47b927): 当てる file を組に分けて pyright に当て、組を当て終えるたびに
途中の文書(next_record の done = 当て終えた file)を書く。途中の文書は、当て終えた file の今回の診断・運んだ file・まだ当てていない
見直しの file の前の回の記録だけを持ち、まだ当てていない新しい・変わった file を持たない(次の回は new として当てる)。使い手が時間で
道具を切っても、当て終えた組の診断は次の回へ残る — 以前は切られた回が文書を書かず、全部を当てる回(環境の指紋が替わった回)が上限を
越えると、次の回も同じ全部を当て直して二度と測れなかった(2026-10-27〜29 の日次 13 回)。
"""

import ast
import hashlib
import inspect
import json
import keyword
import math
import os
import re
import subprocess
import sys
import textwrap
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
#: 注記として読みに行く文字列の字(名・属性・添字・union・Literal の中の文字列と数)— 他の字を含む文言は式として読まない。
_ANNOTATION_TEXT = re.compile(r"[\w.\[\](),|'\"\s*=-]+")
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
    ordered = sorted(
        ((key, order, value) for order, (key, value) in enumerate(pairs)), key=itemgetter(0, 1)
    )
    return {
        key: tuple(value for _, _, value in group)
        for key, group in groupby(ordered, key=itemgetter(0))
    }


def _resolution_json(resolution: Resolution) -> str:
    """解き先を digest の材料の文字列にするため。"""
    return json.dumps(
        [[candidate.path, candidate.kind] for candidate in resolution], ensure_ascii=False
    )


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
                fingerprint,
                run,
                tuple(_file_of(str(key), value, path) for key, value in sorted(files.items())),
            )
        case {"version": version} if version != CARRY_VERSION:
            raise CarryUnreadableError(
                f"運びの file {path} の版 {version!r} は読めない(読める版 = {CARRY_VERSION})"
            )
        case _:
            raise CarryUnreadableError(
                f"運びの file {path} の形が違う(version・fingerprint・run・files が要る)"
            )


def _file_of(key: str, value: object, path: Path) -> CarriedFile:
    """運びの文書の file 1 つを読むため。"""
    match value:
        case {"inputs": str(inputs), "checked": int(checked), "rows": list(rows)}:
            return CarriedFile(key, inputs, checked, tuple(_row_of(row, path) for row in rows))
        case _:
            raise CarryUnreadableError(
                f"運びの file {path} の {key} に inputs・checked・rows が揃っていない: {value!r}"[
                    :400
                ]
            )


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
            "refresh_mismatches": len(self.mismatched),
            "mismatched": list(self.mismatched[:MISMATCH_SHOWN]),
        }

    def line(self) -> str:
        """この回にどれだけ当て、どれだけ運んだかを人に見せる 1 行にするため(標準エラーへ出す)。"""
        reasons = (
            "・".join(f"{count.reason} {count.count}" for count in self.reasons if count.count)
            or "なし"
        )
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
            "rows": [
                [row.line, row.column, row.severity, row.rule, row.message] for row in carried.rows
            ],
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
    previous: CarryRecord | CarryUnreadableError | None,
    fingerprint: str,
    inputs: Sequence[FileInputs],
    refresh_runs: int,
) -> Plan:
    """純粋: 前の回の運び(None = 無い・CarryUnreadableError = 読めない)と今の入力から、この回に当てる file と運ぶ file を決めるため。"""
    if (
        previous is None
        or isinstance(previous, CarryUnreadableError)
        or previous.fingerprint != fingerprint
    ):
        reason = (
            "absent"
            if previous is None
            else "unreadable"
            if isinstance(previous, CarryUnreadableError)
            else "environment"
        )
        run = previous.run + 1 if isinstance(previous, CarryRecord) else 1
        return Plan(tuple(Checking(file.path, reason) for file in inputs), (), run)
    known = {
        carried.path: carried for carried in previous.files
    }  # path から前の回の記録を引く索引(この関数の中だけ)
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


def next_record(  # noqa: DOEFF006 — 内部の小さな組の戻り
    plan: Plan,
    fingerprint: str,
    inputs: Sequence[FileInputs],
    fresh: Sequence[FileRows],
    done: frozenset[str] | None = None,
) -> tuple[CarryRecord, Summary]:
    """純粋: この回の運びの文書と数を作るため(当てた file は今回の診断 fresh と今回の番号、運んだ file は前の回の記録のまま)。done =
    組ごとの途中の文書で当て終えた file(None = 全部を当て終えた)— まだ当てていない見直しの file は前の回の記録のまま持ち、まだ
    当てていない新しい・変わった file は持たない(頭の註「組ごとの書き直し」)。数も当て終えた file だけを数える。"""
    answered = {
        rows.path: rows.rows for rows in fresh
    }  # path からこの回の診断を引く索引(この関数の中だけ)
    carried = {
        kept.path: kept for kept in plan.carried
    }  # path から運ぶ記録を引く索引(この関数の中だけ)
    checked = plan.checked_paths if done is None else plan.checked_paths & done
    waiting = {
        checking.path: checking.before
        for checking in plan.checking
        if checking.path not in checked and checking.before is not None
    }  # まだ当てていない見直しの file の前の回の記録(この関数の中だけ)
    files = tuple(
        CarriedFile(file.path, file.inputs, plan.run, answered.get(file.path, ()))
        if file.path in checked
        else carried[file.path]
        if file.path in carried
        else waiting[file.path]
        for file in inputs
        if file.path in checked or file.path in carried or file.path in waiting
    )
    finished = tuple(checking for checking in plan.checking if checking.path in checked)
    mismatched = tuple(
        sorted(
            checking.path
            for checking in finished
            if checking.before is not None
            and sorted(checking.before.rows) != sorted(answered.get(checking.path, ()))
        )
    )
    reasons = tuple(
        ReasonCount(reason, sum(1 for checking in finished if checking.reason == reason))
        for reason in REASONS
    )
    summary = Summary(plan.run, len(finished), len(plan.carried), reasons, mismatched)
    return CarryRecord(fingerprint, plan.run, files), summary


# --- 環境の指紋 ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """回ごとに違う置き場 1 つ — path = この回の綴り・mark = 回をまたいで同じ印。"""

    path: str
    mark: str


@dataclass(frozen=True)
class Places:
    """digest の材料の中の、回ごとに違う置き場の綴りを印に替えるため(頭の註「置き場の印」)。places = 長い綴りから・pattern = その
    どれかに当たり、後ろが path の続き(/)か区切りの所だけを拾う式(当たった group の番号が places の番号 + 1)。"""

    places: tuple[Place, ...]
    pattern: re.Pattern[str] | None

    def spell(self, text: str) -> str:
        """text の中の置き場の綴りを印に替えるため。"""
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda hit: self.places[(hit.lastindex or 1) - 1].mark, text)


def places_of(root: Path) -> Places:
    """根を @root・根の親(doeff の pin の checkout を置く隣の並び)を @parent に替える置き場の組を作るため(綴りは absolute と
    resolve の両方 — symlink を通った綴りも同じ印にする)。"""
    absolute = Path(os.path.normpath(root.absolute()))
    pairs = [(absolute, "@root"), (absolute.resolve(), "@root")]
    if absolute.parent != absolute:
        pairs.extend([(absolute.parent, "@parent"), (absolute.resolve().parent, "@parent")])
    places = tuple(
        sorted(
            {Place(str(path), mark) for path, mark in pairs if path.parent != path},
            key=lambda place: (-len(place.path), place.path),
        )
    )
    if not places:
        return Places((), None)
    alternatives = "|".join(f"({re.escape(place.path)})" for place in places)
    return Places(places, re.compile(f"(?:{alternatives})" + r"(?=[/\"'\s,;:\])]|$)"))


@dataclass(frozen=True)
class Environment:
    """環境の指紋と、中身を file ごとに見る python の探し道(prefix の外の dir — editable で入れた checkout の dir)と、digest の材料の
    中の置き場の印(places)。"""

    fingerprint: str
    searched: tuple[Path, ...]
    places: Places


def semantics_digest(functions: Iterable[Callable[..., object]]) -> str:
    """pyright の診断を作って .hy の位置へ戻す関数の source と、この module の source の digest を作るため(どちらかが替われば、
    前の回の診断を運ばない)。この module の source も関数と同じく inspect で読む(置き場の path から file を読まない — 検
    tests/test_static_cache.py の test_doeff_hy_sources_do_not_read_their_place)。材料は構文の木(_syntax_of)で、註・字下げ・
    空行だけの書き換えは指紋を替えない(2026-10-17 に `# noqa` の註 4 行の追加で全部の .hy を当て直した)。"""
    return _digest(
        (
            *(_syntax_of(inspect.getsource(function)) for function in functions),
            _syntax_of(inspect.getsource(sys.modules[__name__])),
        )
    )


def _syntax_of(source: str) -> str:
    """純粋: source の構文の木の綴り(ast.dump — 註・字下げ・空行を落とす)— 意味の変わらない書き換えで指紋を替えないため。"""
    return ast.dump(ast.parse(textwrap.dedent(source)))


def probe_environment(
    settings: Mapping[str, object], python: str, pyright: str, semantics: str, places: Places
) -> Environment:
    """環境の指紋を作るため(pyright の版と python の探し道を子の process で読む — 読めなければ RuntimeError)。設定と探し道と .pth の
    中の置き場の綴りは印に替える(places)。"""
    version = subprocess.run([pyright, "--version"], capture_output=True, text=True, check=False)
    named = _PYRIGHT_VERSION.search(version.stdout + version.stderr)
    if version.returncode != 0 or named is None:
        raise RuntimeError(
            f"pyright の版を読めない(exit {version.returncode}): {(version.stdout + version.stderr)[:400]}"
        )
    probed = subprocess.run(
        [python, "-B", "-I", "-c", _PATH_PROBE], capture_output=True, text=True, check=False
    )
    try:
        answer: object = json.loads(probed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"python の探し道を読めない(exit {probed.returncode}): {probed.stderr[:400]}"
        ) from error
    match answer:
        case {
            "path": list(listed),
            "prefix": str(prefix),
            "base": str(base),
            "version": str(python_version),
        }:
            entries = tuple(str(e) for e in listed if isinstance(e, str) and e and Path(e).is_dir())
        case _:
            raise RuntimeError(f"python の探し道の形が違う: {probed.stdout[:400]}")
    fixed = tuple(Path(p).resolve() for p in (prefix, base))
    under_prefix = {
        entry: any(Path(entry).resolve().is_relative_to(f) for f in fixed) for entry in entries
    }
    listings = [[entry, _listing(Path(entry))] for entry in entries if under_prefix[entry]]
    searched = tuple(
        dict.fromkeys(
            Path(os.path.normpath(Path(e).absolute())) for e in entries if not under_prefix[e]
        )
    )
    fingerprint = _digest(
        (
            str(CARRY_VERSION),
            named.group(0),
            places.spell(
                json.dumps(dict(settings), sort_keys=True, ensure_ascii=False, default=str)
            ),
            python_version,
            places.spell(json.dumps(listed, ensure_ascii=False, default=str)),
            places.spell(json.dumps(listings, ensure_ascii=False)),
            semantics,
        )
    )
    return Environment(fingerprint, searched, places)


def _listing(place: Path) -> list[str]:
    """prefix の下の探し道 1 つの型を覆う指紋の材料にするため — 中身の名(dist-info の名 = 配布物の版)と .pth の中身。editable で
    入れた配布物の dist-info は版を落とした名にする(中身は探し道の file ごとの digest が覆う — 2026-10-17 に doeff の版の上げ
    0.5.0 → 0.6.0 → 0.7.0 だけで 2 回、全部の .hy を当て直した)。"""
    return [
        piece
        for name in sorted(os.listdir(place))
        if name != "__pycache__"
        for piece in _listed(place, name)
    ]


def _listed(place: Path, name: str) -> tuple[str, ...]:
    """探し道の中身 1 つの指紋の材料(.pth は名と中身・editable の dist-info は版の無い名・ほかは名)。"""
    if name.endswith(".pth"):
        return (name, _text_of(place / name))
    if name.endswith(".dist-info") and _editable(place / name):
        return (f"editable:{name.split('-', 1)[0]}",)
    return (name,)


def _editable(dist_info: Path) -> bool:
    """dist-info が editable の install か(direct_url.json の dir_info.editable — PEP 610。読めなければ editable でない側)。"""
    try:
        answer: object = json.loads((dist_info / "direct_url.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    match answer:
        case {"dir_info": {"editable": True}}:
            return True
        case _:
            return False


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

    @property
    def imported_module(self) -> "Imported":
        """import した module そのもの(from の名を外し、`import a.b.c` は a.b.c)— 解けるか・どこに解けるかを引くため。"""
        return Imported(self.whole if self.whole is not None else self.module, self.level)


@dataclass(frozen=True)
class Statement:
    """module の段の文 1 つの表面 — surface = 縮めた文の ast.dump(位置なし)・chains = 表面が読む名と属性の連なり・imports = 表面の中の
    import が束ねる名 → 行き先(本体を残した関数の中の import も — 名から行き先を引く索引)・binds = この文が module の段で import で
    束ねる名 → 行き先(同じく索引)。"""

    surface: str
    chains: tuple[tuple[str, ...], ...]
    imports: Mapping[str, tuple[Imported, ...]]
    binds: Mapping[str, tuple[Imported, ...]]


@dataclass(frozen=True)
class Facts:
    """module の file 1 つの読み — statements = module の段の文の表面・bound = 名 → それを束ねる文の番号(名から宣言を引く索引)・
    defined = import でない文が束ねる名・stars = star import の先・header = __all__ を読み書きする文の ast.dump・exported = __all__ の名
    (__all__ が無い・pyright の読める形でない文が在る時は None — 名ごとの節にも header を入れる)・module_imports = module の段の import が
    束ねる名 → 行き先(索引)・volatile = 構文の誤りで読めない(回ごとに違う digest にする)。"""

    statements: tuple[Statement, ...]
    bound: Mapping[str, tuple[int, ...]]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str
    exported: frozenset[str] | None
    module_imports: Mapping[str, tuple[Imported, ...]]
    volatile: bool = False


_VOLATILE = Facts((), {}, frozenset(), (), "", None, {}, volatile=True)


@dataclass(frozen=True)
class _AllStep:
    """__all__ を書く文 1 つ — resets = 代入(前の名を捨てる)か、足す(+=・extend・append)か・names = その文の名。"""

    resets: bool
    names: frozenset[str]


@dataclass(frozen=True)
class _Read:
    """module の段の文 1 つ(import の文は名 1 つ分)の読み — 表面と、束ねる名(names = import も・defined = import でない文の物)と、
    star import の先と、__all__ の文なら header に入れる ast.dump。"""

    statement: Statement
    names: frozenset[str]
    defined: frozenset[str]
    stars: tuple[Imported, ...]
    header: str | None


@dataclass(frozen=True)
class _Scan:
    """木を 1 度歩いた読み — chains = 読まれる名と属性の連なり(文字列の注記の中も)・imports = 木の中の import が束ねる名 → 行き先
    (索引)・stars = star import の先・walrus = `:=` が束ねる名・mentions_all = __all__ を読み書きするか。"""

    chains: tuple[tuple[str, ...], ...]
    imports: Mapping[str, tuple[Imported, ...]]
    stars: tuple[Imported, ...]
    walrus: frozenset[str]
    mentions_all: bool


def module_facts(text: str) -> Facts:
    """純粋: module の Python の source を、名ごとの宣言の表面を引ける形に読むため(読めなければ volatile)。"""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _VOLATILE
    body = tree.body[1:] if tree.body and _is_docstring(tree.body[0]) else tree.body
    grouped = tuple((statement, tuple(_reads_of(statement))) for statement in body)
    reads = tuple(read for _, group in grouped for read in group)
    return Facts(
        tuple(read.statement for read in reads),
        _grouped((name, index) for index, read in enumerate(reads) for name in sorted(read.names)),
        frozenset(name for read in reads for name in read.defined),
        tuple(star for read in reads for star in read.stars),
        "\n".join(read.header for read in reads if read.header is not None),
        _exported(
            tuple(
                statement
                for statement, group in grouped
                if any(read.header is not None for read in group)
            )
        ),
        _grouped(
            (name, target)
            for read in reads
            for name, targets in read.statement.binds.items()
            for target in targets
        ),
    )


def _reads_of(statement: ast.stmt) -> Iterator[_Read]:
    """module の段の文 1 つを読むため。module の段の import の文は名ごとの文に分ける(1 つの文に並んだ名の 1 つを足しても、他の名の
    宣言の digest を変えない)。それ以外の文は、文の段だけを巡って束ねる名を集め(式には入らない — 関数の本体の式を歩かない)、表面へ
    縮めた木を 1 度だけ歩いて、読む名と import と `:=` を集める。"""
    if isinstance(statement, (ast.Import, ast.ImportFrom)) and all(
        a.name != "*" for a in statement.names
    ):
        for alias in statement.names:
            single: ast.Import | ast.ImportFrom = (
                ast.Import(names=[alias])
                if isinstance(statement, ast.Import)
                else ast.ImportFrom(module=statement.module, names=[alias], level=statement.level)
            )
            name, target = next(_import_targets(single))
            yield _Read(
                Statement(ast.dump(single), (), {}, {name: (target,)}),
                frozenset({name}),
                frozenset(),
                (),
                None,
            )
        return
    scope = tuple(_module_scope_statements(statement))
    binds = _grouped(
        (name, target)
        for inner in scope
        if isinstance(inner, (ast.Import, ast.ImportFrom))
        for name, target in _import_targets(inner)
    )
    stars = tuple(
        Imported(inner.module or "", inner.level)
        for inner in scope
        if isinstance(inner, ast.ImportFrom) and any(alias.name == "*" for alias in inner.names)
    )
    targets = frozenset(name for inner in scope for name in _statement_targets(inner, imports=True))
    defined_targets = frozenset(
        name for inner in scope for name in _statement_targets(inner, imports=False)
    )
    declared = frozenset(_global_names(scope)) | frozenset(_except_names(scope))
    reduced = _reduce(statement, method=False)  # 木はこの読みだけの物 — その場で縮める
    scan = _scan(reduced)
    surface = ast.dump(reduced)
    yield _Read(
        Statement(surface, scan.chains, scan.imports, binds),
        targets | declared | scan.walrus,
        defined_targets | declared | scan.walrus,
        stars,
        surface if scan.mentions_all else None,
    )


def _exported(statements: Sequence[ast.stmt]) -> frozenset[str] | None:
    """module の段の __all__ を読み書きする文(順のまま)から __all__ の名を読むため — 名ごとの節には、その名が __all__ に在るかだけを
    入れる(__all__ に別の名を 1 つ足しても、他の名の使い手を当て直さない)。__all__ が無い・どれかが pyright の読める形でなければ None。"""
    steps = tuple(_all_step(statement) for statement in statements)
    if not steps or any(step is None for step in steps):
        return None
    names: frozenset[str] = frozenset()
    for step in steps:
        if step is not None:
            names = step.names if step.resets else names | step.names
    return names


def _all_step(statement: ast.stmt) -> _AllStep | None:
    """__all__ を書く文 1 つの読み(pyright の読める形 — 文字列の並びの代入・注記つきの代入・+=・extend・append — でなければ None)。"""
    match statement:
        case ast.Assign(
            targets=[ast.Name(id="__all__")],
            value=ast.List(elts=elements) | ast.Tuple(elts=elements),
        ):
            resets = True
        case ast.AnnAssign(
            target=ast.Name(id="__all__"), value=ast.List(elts=elements) | ast.Tuple(elts=elements)
        ):
            resets = True
        case ast.AugAssign(
            target=ast.Name(id="__all__"),
            op=ast.Add(),
            value=ast.List(elts=elements) | ast.Tuple(elts=elements),
        ):
            resets = False
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="__all__"), attr="extend"),
                args=[ast.List(elts=elements) | ast.Tuple(elts=elements)],
                keywords=[],
            )
        ):
            resets = False
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="__all__"), attr="append"),
                args=[element],
                keywords=[],
            )
        ):
            elements, resets = [element], False
        case _:
            return None
    names = tuple(
        e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )
    return _AllStep(resets, frozenset(names)) if len(names) == len(elements) else None


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
        case ast.Raise(
            exc=ast.Name(id="NotImplementedError")
            | ast.Call(func=ast.Name(id="NotImplementedError"))
        ):
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
        argument.annotation is not None
        for argument in (*positional, *arguments.kwonlyargs, *starred)
    )


def _decorated_with(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """関数が名 name(か属性の連なりの末尾が name)の decorator を持つか — staticmethod の最初の引数を self と見ないため。"""
    return any(
        (isinstance(d, ast.Name) and d.id == name)
        or (isinstance(d, ast.Attribute) and d.attr == name)
        for d in function.decorator_list
    )


def _child_statements(statement: ast.stmt, into_scopes: bool) -> Iterator[ast.stmt]:
    """文の中にじかに入れ子になった文(if・try・for・while・with・match の本体 — into_scopes なら関数・class の本体も)。式には入らない。"""
    if not into_scopes and isinstance(
        statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
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


def _statements_within(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """本体の中の文の全部(入れ子の関数・class の本体も)— global と self の属性への代入を、式を歩かずに探すため。"""
    for statement in body:
        yield statement
        yield from _statements_within(tuple(_child_statements(statement, into_scopes=True)))


def _module_scope_statements(statement: ast.stmt) -> Iterator[ast.stmt]:
    """module の段の文と、その中の module の段のままの文の全部(関数・class の本体には入らない)— module の名を束ねる文を探すため。"""
    yield statement
    for inner in _child_statements(statement, into_scopes=False):
        yield from _module_scope_statements(inner)


def _global_names(scope: Iterable[ast.stmt]) -> Iterator[str]:
    """module の段の関数・class の中(入れ子も)の global が module の段に束ねる名。"""
    for inner in scope:
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from (
                n
                for s in _statements_within(inner.body)
                if isinstance(s, ast.Global)
                for n in s.names
            )


def _except_names(scope: Iterable[ast.stmt]) -> Iterator[str]:
    """module の段の try の `except … as 名` が束ねる名。"""
    for inner in scope:
        handlers = getattr(inner, "handlers", None)
        if isinstance(handlers, list):
            yield from (h.name for h in handlers if isinstance(h, ast.ExceptHandler) and h.name)


def _assigns_attribute_of(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """method が最初の引数(self / cls)の属性へ代入・削除するか(pyright はそこから属性の型を推すので、本体は表面)。代入の文の的だけを
    見る(`:=` の的は名だけ・内包表記の的に self の属性を書く形は数えない)。"""
    positional = [*function.args.posonlyargs, *function.args.args]
    if not positional:
        return False
    owner = positional[0].arg
    return any(
        isinstance(inner, ast.Attribute)
        and isinstance(inner.value, ast.Name)
        and inner.value.id == owner
        for statement in _statements_within(function.body)
        for target in _assignment_targets(statement)
        for inner in ast.walk(target)
    )


def _assignment_targets(statement: ast.stmt) -> Iterator[ast.expr]:
    """文 1 つの代入・削除の的。"""
    match statement:
        case ast.Assign(targets=targets) | ast.Delete(targets=targets):
            yield from targets
        case (
            ast.AnnAssign(target=target)
            | ast.AugAssign(target=target)
            | ast.For(target=target)
            | ast.AsyncFor(target=target)
        ):
            yield target
        case ast.With(items=items) | ast.AsyncWith(items=items):
            yield from (item.optional_vars for item in items if item.optional_vars is not None)
        case _:
            pass


def _reduce(statement: ast.stmt, method: bool) -> ast.stmt:
    """module の段の文を、呼び手の型に効く表面へ縮めるため(頭の註「表面」)— 文の段だけを巡り、関数を縮め、class は全体を残して method を
    縮める。木はこの読みだけの物なので、その場で替える。method = class の本体の中の文か。"""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return _reduce_function(statement, method)
        case ast.ClassDef():
            statement.body = [_reduce(s, True) for s in _without_docstring(statement.body)]
            return statement
        case _:
            for field in ("body", "orelse", "finalbody"):
                inner = getattr(statement, field, None)
                if isinstance(inner, list) and all(isinstance(s, ast.stmt) for s in inner):
                    setattr(statement, field, [_reduce(s, method) for s in inner])
            for handler in getattr(statement, "handlers", None) or ():
                if isinstance(handler, ast.ExceptHandler):
                    handler.body = [_reduce(s, method) for s in handler.body]
            if isinstance(statement, ast.Match):
                for case in statement.cases:
                    case.body = [_reduce(s, method) for s in case.body]
            return statement


def _reduce_function(function: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> ast.stmt:
    """型を全部宣言し、global を持たず、(method なら)self / cls の属性へ代入しない関数は、本体を空の本体か印に替える。それ以外は本体を
    残し、頭の docstring だけを落とす。"""
    keeps_body = (
        not _fully_annotated(function, method)
        or any(isinstance(s, ast.Global) for s in _statements_within(function.body))
        or (method and _assigns_attribute_of(function))
    )
    if keeps_body:
        function.body = _without_docstring(function.body)
        return function
    stripped = [s for s in function.body if not _is_docstring(s)]
    if not stripped:
        function.body = [ast.Expr(value=ast.Constant(value=_DOCSTRING_MARK))]
    elif not _trivial(stripped):
        function.body = [ast.Expr(value=ast.Constant(value=_BODY_MARK))]
    else:
        function.body = stripped
    return function


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """本体の頭の docstring を落とすため(空になれば pass — 本体は空にできない)。"""
    if body and _is_docstring(body[0]):
        return body[1:] or [ast.Pass()]
    return body


def _scan(node: ast.AST) -> _Scan:
    """木を 1 度だけ歩き、読まれる名と属性の連なり・import・star import・`:=` の名・__all__ の有無を集めるため。"""
    nodes = tuple(ast.walk(node))
    inner = {
        id(n.value) for n in nodes if isinstance(n, ast.Attribute)
    }  # 連なりの途中(a.b.c の a.b と a)は外の連なりが読む
    return _Scan(
        tuple(dict.fromkeys(chain for n in nodes if id(n) not in inner for chain in _chains_at(n))),
        _grouped(
            (name, target)
            for n in nodes
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for name, target in _import_targets(n)
        ),
        tuple(
            Imported(n.module or "", n.level)
            for n in nodes
            if isinstance(n, ast.ImportFrom) and any(alias.name == "*" for alias in n.names)
        ),
        frozenset(
            name for n in nodes if isinstance(n, ast.NamedExpr) for name in _target_names(n.target)
        ),
        any(isinstance(n, ast.Name) and n.id == "__all__" for n in nodes),
    )


def _chains_at(node: ast.AST) -> Iterator[tuple[str, ...]]:
    """node 1 つが読む名か属性の連なり(連なりでない属性は無し)。文字列の定数は注記の式として読めれば、その中の名も(pyright は文字列の
    注記を式として解く)。"""
    match node:
        case ast.Attribute():
            chain = _chain(node)
            if chain is not None:
                yield chain
        case ast.Name(id=name):
            yield (name,)
        case ast.Constant(value=str(text)) if 0 < len(
            text
        ) <= _ANNOTATION_TEXT_LIMIT and _ANNOTATION_TEXT.fullmatch(text):
            yield from _text_chains(text)
        case _:
            pass


def _text_chains(text: str) -> Iterator[tuple[str, ...]]:
    """文字列の注記の中の名と属性の連なり。名か名の連なりだけの文字列(ほとんどの文字列)は式として読まずに綴りを分ける。"""
    parts = text.strip().split(".")
    if all(part.isidentifier() and not keyword.iskeyword(part) for part in parts):
        yield tuple(parts)
        return
    parsed = _expression(text)
    if parsed is not None:
        yield from chains_of(parsed)


def chains_of(node: ast.AST) -> Iterator[tuple[str, ...]]:
    """木の中で読まれる名と属性の連なりを集めるため(`a.b.c` は 1 つの連なり ('a', 'b', 'c'))。"""
    yield from _scan(node).chains


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


def _statement_targets(inner: ast.stmt, imports: bool) -> Iterator[str]:
    """文 1 つの的(定義の名・import の名・代入の的・for と with の的・match の型・型の別名)が束ねる名 — imports = import の名も数えるか。"""
    match inner:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            yield name
        case ast.Import() | ast.ImportFrom():
            if imports:
                yield from (name for name, _ in _import_targets(inner))
        case ast.Assign(targets=targets) | ast.Delete(targets=targets):
            yield from (n for t in targets for n in _target_names(t))
        case (
            ast.AnnAssign(target=target)
            | ast.AugAssign(target=target)
            | ast.For(target=target)
            | ast.AsyncFor(target=target)
        ):
            yield from _target_names(target)
        case ast.With(items=items) | ast.AsyncWith(items=items):
            yield from (
                n
                for item in items
                if item.optional_vars is not None
                for n in _target_names(item.optional_vars)
            )
        case ast.Match(cases=cases):
            yield from (
                n for case in cases for node in ast.walk(case.pattern) for n in _pattern_names(node)
            )
        case _:
            type_alias = getattr(ast, "TypeAlias", None)  # 3.12 から
            if type_alias is not None and isinstance(inner, type_alias):
                named = vars(inner).get("name")  # 型の別名の文の名(3.12 の ast.TypeAlias)
                if isinstance(named, ast.AST):
                    yield from _target_names(named)


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

#: module の file と package の __init__ の接尾辞と、その候補の種類(探す順 — pyright は .pyi を先に読む)。
_SOURCE_SUFFIXES = ((".pyi", "pyi"), (".py", "py"), (".hy", "hy"))
_INIT_FILES = (("__init__.pyi", "pyi"), ("__init__.py", "py"), ("__init__.hy", "hy"))


def _under(path: str, base: str) -> bool:
    """path が dir base か、その下か(文字列で判じる — pathlib の演算は 1 回の実行で数十万回呼ぶと重い)。"""
    return path == base or path.startswith(base.rstrip("/") + "/")


def _join(base: str, *parts: str) -> str:
    """dir base の下の path の綴り。"""
    return "/".join((base.rstrip("/"), *parts)) if parts else base


class InputGraph:
    """file ごとの入力の digest を作るため(頭の註「入力」)。root = repo の根・roots = import の根(根と extraPaths)・stub_dir = pyright の
    stubPath・environment = 環境の指紋と探し道・staged = 根の下の .hy → 展開した Python(pyright が `<名>.py` として読む物)・failed =
    展開に失敗した .hy(pyright には見えない)。1 回の実行の中で、dir の中身・読み・解き先・節の digest を覚える(path は中では文字列)。"""

    def __init__(
        self,
        root: Path,
        roots: Sequence[Path],
        stub_dir: Path,
        environment: Environment,
        staged: Mapping[Path, str],
        failed: Iterable[Path],
    ) -> None:
        self._root = str(root)
        self._environment = environment
        self._global_dirs = tuple(
            dict.fromkeys(str(d) for d in (*roots, stub_dir, *environment.searched))
        )
        self._staged = {str(path): text for path, text in staged.items()}
        self._failed = frozenset(str(path) for path in failed)
        self._mut_entries: dict[str, dict[str, bool]] = {}
        self._mut_natives: dict[str, dict[str, tuple[str, ...]]] = {}
        self._mut_facts: dict[str, Facts] = {}
        self._mut_in_dir: dict[tuple[str, str, bool], Resolution] = {}
        self._mut_resolved: dict[tuple[str, str, int], Resolution] = {}
        self._mut_local_dirs: dict[str, tuple[str, ...]] = {}
        self._mut_expanded: dict[_Node, _Expanded] = {}
        self._mut_deep: dict[_Node, str] = {}
        self._mut_markers: dict[str, str] = {}

    # 解き先 ------------------------------------------------------------------------------------------------------------------

    def _entries(self, directory: str) -> dict[str, bool]:
        """dir の中身の名 → dir か(dir でなければ空)を、1 回の実行の中で dir ごとに 1 度だけ読むため。"""
        known = self._mut_entries.get(directory)
        if known is not None:
            return known
        try:
            with os.scandir(directory) as scanned:
                listed = {entry.name: entry.is_dir() for entry in scanned}
        except OSError:
            listed = {}
        self._mut_entries[directory] = listed
        return listed

    def _natives(self, directory: str) -> dict[str, tuple[str, ...]]:
        """dir の中の native の module の file(`<名>.….so`・`.pyd`)を、名の頭ごとに引く索引(1 回の実行の中で dir ごとに 1 度だけ作る)。"""
        known = self._mut_natives.get(directory)
        if known is None:
            entries = self._entries(directory)
            known = _grouped(
                (name.split(".", 1)[0], name)
                for name in sorted(entries)
                if entries[name] is False and name.endswith((".so", ".pyd")) and "." in name
            )
            self._mut_natives[directory] = known
        return known

    def _marker(self, path: str) -> str:
        """py.typed の中身の digest(partial の印を見る — package の型の読まれ方を変える)。"""
        if path not in self._mut_markers:
            self._mut_markers[path] = _digest((_text_of(Path(path)),))
        return self._mut_markers[path]

    def _hy_kind(self, path: str) -> str:
        """根の下の .hy の種類(展開できた = hy・展開に失敗した = hy-failed・展開の範囲の外 = hy-unstaged)。"""
        if path in self._staged:
            return "hy"
        return "hy-failed" if path in self._failed else "hy-unstaged"

    def _sources_at(self, base: str, leaf: str, stub_package: bool) -> Iterator[Candidate]:
        """dir base の中の module の file(leaf.pyi・.py・根の下だけ .hy)と、同じ名の native の module。"""
        entries = self._entries(base)
        in_root = _under(base, self._root)
        for suffix, kind in _SOURCE_SUFFIXES:
            name = leaf + suffix
            if (
                entries.get(name) is False
                and (kind == "pyi" or not stub_package)
                and (kind != "hy" or in_root)
            ):
                path = _join(base, name)
                yield Candidate(path, self._hy_kind(path) if kind == "hy" else kind)
        if not stub_package:
            yield from (
                Candidate(_join(base, name), "native") for name in self._natives(base).get(leaf, ())
            )

    def _package_files(self, here: str, stub_package: bool) -> Iterator[Candidate]:
        """package の dir here と、その __init__ の file(根の下だけ .hy も)。"""
        yield Candidate(here, "package")
        entries = self._entries(here)
        in_root = _under(here, self._root)
        for init, kind in _INIT_FILES:
            if (
                entries.get(init) is False
                and (kind == "pyi" or not stub_package)
                and (kind != "hy" or in_root)
            ):
                path = _join(here, init)
                yield Candidate(path, self._hy_kind(path) if kind == "hy" else kind)

    def _candidates(self, base: str, parts: Sequence[str], stubs: bool) -> Iterator[Candidate]:
        """dir base の下で module(parts)に pyright が当たりうる候補のうち、在る物(stub の package・py.typed・親の package の印も)。"""
        entries = self._entries(base)
        for top in (f"{parts[0]}-stubs", parts[0]) if stubs else (parts[0],):
            if not (
                entries.get(top) is True
                or any(entries.get(top + suffix) is False for suffix, _ in _SOURCE_SUFFIXES)
                or top in self._natives(base)
            ):
                continue  # dir base に名の頭が無い(ほとんどの探し道はここで終わる)
            stub_package = top != parts[0]
            top_dir = _join(base, top)
            if self._entries(top_dir).get("py.typed") is False:
                marker = _join(top_dir, "py.typed")
                yield Candidate(marker, f"typed:{self._marker(marker)}")
            yield from (
                Candidate(_join(parent, init), "init")
                for parent in (_join(top_dir, *parts[1:i]) for i in range(1, len(parts)))
                for init, _ in _INIT_FILES
                if self._entries(parent).get(init) is False
            )
            here = _join(top_dir, *parts[1:])
            parent, leaf = here.rsplit("/", 1)
            yield from self._sources_at(parent, leaf, stub_package)
            if self._entries(parent).get(leaf) is True:
                yield from self._package_files(here, stub_package)

    def _in_dir(self, base: str, dotted: str, stubs: bool) -> Resolution:
        """dir base の下の module(dotted)の候補を、1 回の実行の中で 1 度だけ探すため。"""
        key = (base, dotted, stubs)
        known = self._mut_in_dir.get(key)
        if known is None:
            known = tuple(self._candidates(base, dotted.split("."), stubs))
            self._mut_in_dir[key] = known
        return known

    def _local_dirs(self, directory: str) -> tuple[str, ...]:
        """import する file の dir から探し道の根までの親(pyright は探し道で解けない絶対の import をここでも探す — static_check の
        search_dirs と同じ考え。根の外の file は、その file を含む一番深い探し道の dir まで)。dir ごとに 1 度だけ作る。"""
        known = self._mut_local_dirs.get(directory)
        if known is None:
            containing = [d for d in self._global_dirs if _under(directory, d)]
            stop = (
                self._root
                if _under(directory, self._root)
                else max(containing, key=len, default=directory)
            )
            known = tuple(_ancestors(directory, stop))
            self._mut_local_dirs[directory] = known
        return known

    def resolve(self, importer: str, target: Imported) -> Resolution:
        """import の行き先 1 つの module の解き先を引くため(相対の import は import する file の dir から)。解き先は import する file の
        dir と module の綴りと深さだけで決まるので、1 回の実行の中でその組ごとに 1 度だけ探す。"""
        directory = os.path.dirname(importer)
        key = (directory, target.module, target.level)
        known = self._mut_resolved.get(key)
        if known is None:
            known = self._resolved(directory, target)
            self._mut_resolved[key] = known
        return known

    def _resolved(self, directory: str, target: Imported) -> Resolution:
        """resolve の中身 — 探し道の dir と import する file の dir からの親で、module の候補を探す。"""
        if target.level > 0:
            base = directory
            for _ in range(target.level - 1):
                base = os.path.dirname(base)
            return (
                tuple(self._package_files(base, stub_package=False))
                if not target.module
                else self._in_dir(base, target.module, False)
            )
        if not target.module:
            return ()
        dirs = dict.fromkeys((*self._global_dirs, *self._local_dirs(directory)))
        return tuple(
            dict.fromkeys(c for d in dirs for c in self._in_dir(d, target.module, stubs=True))
        )

    def _submodule(self, resolution: Resolution, name: str) -> Resolution:
        """解き先の package の下の module(name)の解き先(package の dir と __init__ の在る dir の下)— 名が module に無い時に探す先。"""
        packages = dict.fromkeys(
            c.path if c.kind == "package" else os.path.dirname(c.path)
            for c in resolution
            if c.kind == "package"
            or (c.kind in ("pyi", "py", "hy") and os.path.basename(c.path).startswith("__init__."))
        )
        return tuple(dict.fromkeys(c for p in packages for c in self._in_dir(p, name, stubs=False)))

    def _spelled(self, resolution: Resolution) -> str:
        """解き先を digest の材料の文字列にするため(回ごとに違う置き場の綴りは印に替える — Places)。"""
        return self._environment.places.spell(_resolution_json(resolution))

    # 読み --------------------------------------------------------------------------------------------------------------------

    def _facts(self, path: str, kind: str) -> Facts:
        """候補の file 1 つを、1 回の実行の中で 1 度だけ読むため(展開した .hy は展開した Python を、.py / .pyi は file を読む)。"""
        known = self._mut_facts.get(path)
        if known is not None:
            return known
        if kind == "hy":
            text = self._staged.get(path)
        else:
            try:
                with open(path, encoding="utf-8") as opened:
                    text = opened.read()
            except (OSError, UnicodeDecodeError):
                text = None
        facts = _VOLATILE if text is None else module_facts(text)
        self._mut_facts[path] = facts
        return facts

    def _contents(self, resolution: Resolution) -> Iterator[tuple[str, Facts]]:
        """解き先の候補のうち中身を読む file(.pyi・.py・展開した .hy)とその読み。"""
        for candidate in resolution:
            if candidate.kind in ("pyi", "py", "hy"):
                yield candidate.path, self._facts(candidate.path, candidate.kind)

    # 節 ----------------------------------------------------------------------------------------------------------------------

    def _imported_nodes(
        self, importer: str, targets: Sequence[Imported], chain: tuple[str, ...]
    ) -> Iterator[_Node]:
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
                yield _Node(
                    self.resolve(importer, Imported(target.module, target.level)), target.name
                )
            elif target.whole is not None:  # import a.b.c(根の名 a を束ねる)
                yield from self._walked_chain(importer, chain, prefixes)
            else:  # import a.b as x
                yield _Node(self.resolve(importer, target), chain[1] if len(chain) > 1 else None)

    def _walked_chain(
        self, importer: str, chain: tuple[str, ...], prefixes: set[str]
    ) -> Iterator[_Node]:
        """`import a.b.c` で束ねた a からの連なり a.b.c.x を、import した module の綴りの所まで辿った節(途中の package の同じ名の宣言も
        — package の __init__ の名は下の module を隠す)。"""
        depth = next(
            (i for i in range(1, len(chain)) if ".".join(chain[: i + 1]) not in prefixes),
            len(chain),
        )
        for i in range(1, depth):
            yield _Node(self.resolve(importer, Imported(".".join(chain[:i]), 0)), chain[i])
        module = self.resolve(importer, Imported(".".join(chain[:depth]), 0))
        yield _Node(module, chain[depth] if depth < len(chain) else None)

    def _statement_nodes(
        self,
        path: str,
        resolution: Resolution,
        facts: Facts,
        statement: Statement,
        name: str | None,
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
        yield json.dumps([node.name, self._spelled(node.resolution)], ensure_ascii=False)
        contents = tuple(self._contents(node.resolution))
        for path, facts in contents:
            if facts.volatile:
                yield f"volatile:{uuid.uuid4()}"
                continue
            if node.name is None or facts.exported is None:
                yield facts.header
            else:
                yield f"__all__ に在る:{node.name in facts.exported}"
            indexes = (
                range(len(facts.statements))
                if node.name is None
                else facts.bound.get(node.name, ())
            )
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
                yield self._spelled(below)
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
            pending = self._next_unvisited(node, successors, index, low, on_stack)
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

    def _next_unvisited(
        self,
        node: _Node,
        successors: Iterator[_Node],
        index: dict[_Node, int],
        low: dict[_Node, int],
        on_stack: set[_Node],
    ) -> _Node | None:
        """節の後続を、まだ訪ねていない物に当たるまで進める(訪ねた・閉じた後続は飛ばし、stack に在る後続で low を下げる)— Tarjan の
        算法の 1 歩。後続の iterator は節ごとに 1 つで、戻ってきた時は続きから読む(辺の数に比例する)。"""
        for successor in successors:
            if successor in self._mut_deep:
                continue
            if successor not in index:
                return successor
            if successor in on_stack:
                low[node] = min(low[node], index[successor])  # noqa: DOEFF007 - Tarjan の探索が自分の作業表を更新する
        return None

    def _close(self, head: _Node, stack: list[_Node], on_stack: set[_Node]) -> None:
        """強連結成分 1 つ(stack の head から上)を閉じ、成分の全部の節に同じ推移の digest を付けるため。"""
        start = len(stack) - 1 - stack[::-1].index(head)
        members = tuple(stack[start:])
        del stack[start:]  # noqa: DOEFF007 - Tarjan の探索が自分の作業表を更新する
        on_stack.difference_update(members)  # noqa: DOEFF007 - Tarjan の探索が自分の作業表を更新する
        inside = set(members)
        locals_ = sorted(self._expand(m).local for m in members)
        outside = sorted(
            {
                self._mut_deep[s]
                for m in members
                for s in self._expand(m).successors
                if s not in inside
            }
        )
        digest = _digest(("scc", *locals_, "→", *outside))
        self._mut_deep.update(dict.fromkeys(members, digest))

    def inputs_of(self, source: Path, own: str) -> str:
        """根の下の .hy 1 つの入力の digest を作るため(own = その file 自身の digest — own_digest)。展開を読めなければ回ごとに違う
        digest(毎回当てる)。木は 1 度だけ歩く。"""
        path = str(source)
        text = self._staged.get(path)
        if text is None:
            return f"unstaged:{uuid.uuid4()}"
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return f"volatile:{uuid.uuid4()}"
        scan = _scan(tree)
        imported = tuple(target for targets in scan.imports.values() for target in targets)
        used = (
            node
            for chain in scan.chains
            if chain[0] in scan.imports
            for node in self._imported_nodes(path, scan.imports[chain[0]], chain)
        )
        # from-import の名の全部(使わない名も — 名が module に在るかは、その file の診断に出る)と star の先。
        named = (
            _Node(self.resolve(path, target.imported_module), target.name)
            for target in imported
            if target.name is not None
        )
        stars = (_Node(self.resolve(path, star), None) for star in scan.stars)
        # import する module の全部の解き先(解けるか・どこに解けるかは、その file の診断に出る)。
        resolutions = sorted(
            {
                self._spelled(self.resolve(path, target.imported_module))
                for target in (*imported, *scan.stars)
            }
        )
        deep = sorted({self._deep(node) for node in dict.fromkeys((*used, *named, *stars))})
        return _digest(("target", self._environment.fingerprint, own, *resolutions, "→", *deep))


def _ancestors(directory: str, stop: str) -> Iterator[str]:
    """dir から stop(含む)までの親の dir(dir 自身から — stop の外へは出ない)。"""
    current = directory
    while _under(current, stop):
        yield current
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


def own_digest(source_text: str, projection_text: str, spans: object) -> str:
    """.hy 1 つ自身の digest を作るため — source・展開した Python・位置の対応表(診断の位置は展開と対応表で .hy の位置へ戻る)。"""
    return _digest(("own", source_text, projection_text, repr(spans)))
