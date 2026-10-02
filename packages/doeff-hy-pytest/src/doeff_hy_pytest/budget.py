"""Hy の検の時間の上限(検 1 本の実行・検の file 1 本の収集)。

模擬の handler で回す検は速いことが前提なので、遅くなった検に気づかせる(proboscis/agora-redesign#907)。
利用側の pyproject の ``[tool.pytest.ini_options]`` に置く設定:

- ``doeff_test_call_budget_seconds`` — Hy の検の file から集めた検 1 本の実行(pytest の call の段階)の上限の CPU 秒。
- ``doeff_test_call_budget_by_marker`` — 印ごとの実行の上限(1 行 = ``印=秒``・例 ``real_world=10``)。検が持つ印
  (``item.iter_markers()`` — module の頭の ``pytestmark`` を含む)に当たる行があればその秒、複数当たれば最も長い秒、
  1 つも当たらなければ ``doeff_test_call_budget_seconds``。収集の上限は file 単位で印を持たないので 1 つの値のまま。
- ``doeff_test_collect_budget_seconds`` — Hy の検の file 1 本の module の import の上限の CPU 秒。
- ``doeff_test_budget_mode`` — ``report``(既定・超えても赤にせず、警告と終わりの一覧だけ)か ``fail``(超えたら赤)。
- ``doeff_test_budget_registry`` — 上限を超えてよい既存の検の登録簿の dir(rootdir からの相対・1 行 = 1 dir・
  1 つの値の書き方もそのまま読める)。どの dir に載った鍵も赤にしない。上限を超えた文が足し先に挙げるのは 1 行目の dir。

- ``doeff_test_budget_judge`` — 実行の判定に使う物。``seconds``(既定・CPU 秒)か ``steps``(doeff-vm の歩数 — 機体の負荷で
  揺れない決まった数・agora-redesign #2670)。``steps`` でも CPU 秒は測って報告に出す(移行の間は両方を測る)。戻し方 = ``seconds``。
- ``doeff_test_call_budget_steps`` — 判定が ``steps`` の時の、検 1 本の実行の上限の歩数(正の整数)。
- ``doeff_test_budget_steps_registry`` — 歩数の上限を超えてよい既存の検の登録簿の dir(秒の登録簿と分ける — 鍵が同じ nodeid で、
  秒の登録と数の登録の行き先が混ざらないため)。

上限の 3 つ(実行・印ごとの実行・収集)がどれも無ければ何もしない。設計の決め:

- 対象は ``.hy`` の検の file(deftest と、同じ file の素の検)だけ。Python の検は模擬の handler の検とは限らないので外す。
- 判定は CPU 時間(``time.process_time`` — process の全 thread の CPU 時間)で行い、壁時計を併記する。壁時計は機体の負荷
  (並べて走る他の検・席)で伸び縮みし、同じ検の合否が機体の混み具合で変わるため。模擬の handler の検は本物の待ちを
  持たない前提なので、遅さはほぼ CPU 時間に出る。
- 実行は call の段階だけを測り、setup と teardown(fixture)は含めない。session / module の範囲の fixture の準備は、
  その範囲で最初に走った検 1 本に丸ごと乗るので、含めると検の選び方と並び順で同じ検の合否が変わる。deftest の本体
  (模擬の handler で Program を回す所)は call の段階にある。
- file の上限は module の import を測り、別の module の初回の import(共有の依存の一度きりの重さ)は変換と同じく引く
  (2026-09-30・agora-redesign #1752 で改めた — 以前は依存の import を含めて測り、その重さがその process で最初に import した
  file に乗るので、同じ file の合否が並び順・1 file だけの実行か・テストの索引が冷えているかで変わった。戻し方 =
  ``CompileCounter.install`` の ``_find_and_load`` の差し替えを外す)。測る所は doeff-adr の hook
  ``pytest_doeff_import_hy_module`` の前後 — doeff-adr は記録で説明できる file を import せずに収集し、module の import を
  item の setup まで待つので(agora-redesign #1211 / #1225)、import は収集の中か setup の中のどちらかで 1 度起きる。
  どちらで起きても同じ hook を通るので、同じ鍵(file の path)で測る。超えて赤にする時は、その場で落とす(収集なら
  収集の誤り、setup ならその item の setup の誤り)。
- キャッシュ無しを赤にしない: 測った区間から、source から code への変換(``SourceFileLoader.source_to_code`` —
  バイトコードのキャッシュに当たらなかった時だけ呼ばれる。Hy の import も Hy が差し替えた同じ口を通る)に使った CPU 時間を
  引いた値で判定する(変換が入れ子になった時は最も外側の区間だけを引く)。回を丸ごと除く形にしないのは、着地の門と日次の
  走行が ``PYTHONDONTWRITEBYTECODE=1`` で撃ち、キャッシュが一度も作られないため — 除く形ではそこで一度も判定されない。
  キャッシュ有りなら引く物は 0 で値はそのまま。キャッシュ無しの値は下限(Hy が変換の中で読む ``require`` 先の module の
  実行も引かれる)なので、下限で超えた物は本当に超えている。
- 登録簿は 1 鍵 1 file(``<鍵の sha256 の先頭 12 字>.txt``・1 行目が鍵・2 行目からが理由で空は不可)。鍵は、実行なら
  pytest の nodeid、収集なら rootdir からの file の path。載った鍵は上限を超えても赤にしない。上限の内に戻った鍵は
  終わりの要約に「消せる」と出す(登録簿は縮める向きだけ — 増えたことを赤にするのは利用側の repo の git の検)。
  ``fail`` の形では、載った鍵が上限の ``STALE_RATIO``(半分)以下で終わった時、その検を古い登録として赤にする
  (消し忘れた行は、同じ検が遅く戻っても黙って通す — agora-redesign #1726・#1706。doeff-linter の DOEFF166 と同じ向き)。
  上限の半分から上限までの間は報告だけ(CPU 秒の揺れで同じ検の合否が走りごとに変わらないように)。
  測った検だけを判じる(走らなかった検・別の検査の鍵が同じ dir に在っても判じない)。
- 上限は doeff-vm の不変条件の検査が無効な走行を基準にする。検査が有効な走行(doeff 自身の pytest — root の
  conftest.py が有効にする・agora-redesign #980 からはどの build も検査を持ち実行時に切り替える)は VM の
  1 歩ごとに不変条件を検査し、同じ検が十数倍遅い。session の始めに ``doeff_vm.invariant_checks_enabled()`` を 1 回読み、
  検査つきなら ``fail`` でも赤にせず報告に留める(基準の違う build で誤った赤を出さない)。終わりの要約の見出しに
  build の種類を 1 行出す。読めない(import できない・古い VM で関数が無い)時は「不明」と出し、判定は今のまま。
"""

import contextlib
import hashlib
import importlib.machinery
import sys
import time
import types
import warnings
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

CALL_BUDGET_INI = "doeff_test_call_budget_seconds"
MARKER_CALL_BUDGET_INI = "doeff_test_call_budget_by_marker"
COLLECT_BUDGET_INI = "doeff_test_collect_budget_seconds"
MODE_INI = "doeff_test_budget_mode"
REGISTRY_INI = "doeff_test_budget_registry"
JUDGE_INI = "doeff_test_budget_judge"
CALL_STEPS_INI = "doeff_test_call_budget_steps"
STEPS_REGISTRY_INI = "doeff_test_budget_steps_registry"
# 判定に使う物(実行の段階だけ — 収集は Hy の変換と import の重さで、歩数に出ないので秒のまま)。
Judge = Literal["seconds", "steps"]
# 判定の単位(上限・登録・文の書き方が分かれる)。
Unit = Literal["seconds", "steps"]

Phase = Literal["call", "collect"]
# 測りから引く区間の種類(CompileCounter)。
SetAside = Literal["compile", "import"]
# 別の module の初回の import が通る口(import の機構が呼ぶたびに名で引く — CompileCounter が数えるために差し替える)。
_BOOTSTRAP_MODULE = "importlib._bootstrap"
_FIND_AND_LOAD = "_find_and_load"
Mode = Literal["report", "fail"]
# 登録簿に載った検を古い登録として赤にする秒の割合(上限 × この値以下で終わった時だけ — 上限の近くの揺れで赤と緑を行き来しない)。
STALE_RATIO = 0.5


@dataclass(frozen=True)
class CheckedVmBuild:
    """doeff-vm は invariant-checks つきの build(``make sync``)— 上限の基準と違うので超過を判定しない。"""


@dataclass(frozen=True)
class UncheckedVmBuild:
    """doeff-vm は検査なしの build — 上限の基準の build。"""


@dataclass(frozen=True)
class UnknownVmBuild:
    """doeff-vm の build の種類を読めなかった(理由つき)— 判定は検査なしの時と同じ。"""

    reason: str


VmBuild = CheckedVmBuild | UncheckedVmBuild | UnknownVmBuild


def read_vm_build() -> VmBuild:
    """doeff-vm の build の種類を ``doeff_vm.invariant_checks_enabled()`` で読む(session の始めに 1 回)。"""
    try:
        import doeff_vm
    except ImportError as exc:
        return UnknownVmBuild(f"doeff_vm を import できない: {exc}")
    reader = getattr(doeff_vm, "invariant_checks_enabled", None)
    if reader is None:
        return UnknownVmBuild("doeff_vm に invariant_checks_enabled が無い(古い VM)")
    return CheckedVmBuild() if reader() else UncheckedVmBuild()


@dataclass(frozen=True)
class VmWork:
    """ある時点までに process の全部の doeff-vm が進めた歩数と、handler を呼んだ回数(積み上げ — 機体の負荷で揺れない)。"""

    steps: int
    handler_calls: int

    def since(self, before: "VmWork") -> "VmWork":
        """before からこの時点までの差 — 1 つの測りの区間の中の仕事の量。"""
        return VmWork(self.steps - before.steps, self.handler_calls - before.handler_calls)


@dataclass(frozen=True)
class WorkReader:
    """doeff-vm の積み上げの数を読む口(``doeff_vm.doeff_vm.vm_work_counts`` — agora-redesign #2851)。"""

    read: Callable[[], tuple[int, int]]

    def tally(self) -> VmWork:
        """今の積み上げの数 — 区間の前後で読み、差をその区間の仕事の量にするため。"""
        steps, handler_calls = self.read()
        return VmWork(steps, handler_calls)


@dataclass(frozen=True)
class NoWorkReader:
    """doeff-vm に積み上げの数の口が無い(理由つき — 古い build・import できない)。数は測れず、判定は秒で行う。"""

    reason: str


WorkSource = WorkReader | NoWorkReader


def read_work_source() -> WorkSource:
    """積み上げの数の口を session の始めに 1 回探す。compiled の module から直に引く(``doeff_vm/__init__.py`` へは足さない
    — 古い ``.so`` で import が AttributeError になり全部の使い手が落ちるため・同じ file の註)。"""
    try:
        from importlib import import_module

        ext = import_module("doeff_vm.doeff_vm")
    except ImportError as exc:
        return NoWorkReader(f"doeff_vm を import できない: {exc}")
    reader = getattr(ext, "vm_work_counts", None)
    if reader is None:
        return NoWorkReader("doeff_vm に vm_work_counts が無い(積み上げの数の口より前の build)")
    return WorkReader(reader)


def vm_build_line(build: VmBuild) -> str:
    """終わりの要約の見出しに出す build の種類の 1 行。"""
    match build:
        case CheckedVmBuild():
            return (
                "doeff-vm は検査つきの build(invariant-checks)— 上限は検査なしの build が基準のため、"
                "この走行の超過は判定しない"
            )
        case UncheckedVmBuild():
            return "doeff-vm は検査なしの build — 上限の基準の build で判定する"
        case UnknownVmBuild(reason=reason):
            return f"doeff-vm の build の種類は不明({reason})— 判定は設定のまま"


@dataclass(frozen=True)
class Budgets:
    """設定から読んだ上限(None = その段階は測らない)・印ごとの実行の上限・超えた時の扱い・登録簿(全 dir の鍵 → 理由)・
    登録簿の dir(設定の順)・doeff-vm の build の種類。"""

    call_seconds: float | None
    call_seconds_by_marker: Mapping[str, float]
    collect_seconds: float | None
    mode: Mode
    registry: Mapping[str, str]
    registry_dirs: tuple[str, ...]
    vm_build: VmBuild
    judge: Judge = "seconds"
    call_steps: int | None = None
    steps_registry: Mapping[str, str] = types.MappingProxyType({})
    steps_registry_dirs: tuple[str, ...] = ()
    work_source: WorkSource = NoWorkReader("測っていない")

    def registry_dirs_for(self, unit: Unit) -> tuple[str, ...]:
        """単位ごとの登録簿の dir — 超えた文の足し先を、判定した単位の登録簿にするため。"""
        match unit:
            case "seconds":
                return self.registry_dirs
            case "steps":
                return self.steps_registry_dirs

    @property
    def judges_steps(self) -> bool:
        """実行を歩数で判じるか — 判定が steps で、上限があり、数の口がある時だけ(どれかが欠ければ秒で判じる)。"""
        return self.judge == "steps" and self.call_steps is not None and isinstance(self.work_source, WorkReader)

    @property
    def fails_over_budget(self) -> bool:
        """秒の超過を赤にするか — fail の形で、かつ doeff-vm が検査つきの build でない時だけ。"""
        return self.mode == "fail" and not isinstance(self.vm_build, CheckedVmBuild)

    def fails_for(self, unit: Unit) -> bool:
        """その単位の超過を赤にするか。歩数は不変条件の検査の有無で変わらない(検査は歩の中で走り、歩を足さない)ので、
        検査つきの build でも fail の形なら赤にする — 秒では判じられなかった doeff 自身の走行も歩数なら判じられる。"""
        match unit:
            case "seconds":
                return self.fails_over_budget
            case "steps":
                return self.mode == "fail"


@dataclass(frozen=True)
class CompileTally:
    """ある時点までに数えた source からの変換の回数と、それに使った CPU 秒(最も外側の区間の和)。"""

    count: int
    cpu_seconds: float

    def since(self, before: "CompileTally") -> "CompileTally":
        """before からこの時点までの差 — 1 つの測りの区間の中で起きた変換。"""
        return CompileTally(self.count - before.count, self.cpu_seconds - before.cpu_seconds)


@dataclass(frozen=True)
class ImportTally:
    """ある時点までに数えた、別の module の初回の import の回数と、それに使った CPU 秒(最も外側の区間の和)。"""

    count: int
    cpu_seconds: float

    def since(self, before: "ImportTally") -> "ImportTally":
        """before からこの時点までの差 — 1 つの測りの区間の中で起きた依存の初回の import。"""
        return ImportTally(self.count - before.count, self.cpu_seconds - before.cpu_seconds)


NO_IMPORTS = ImportTally(0, 0.0)


@dataclass(frozen=True)
class Measurement:
    """測った 1 区間 — CPU 秒と壁時計の秒(どちらも区間の全体)と、区間の中の変換と依存の初回の import。"""

    key: str
    phase: Phase
    cpu_seconds: float
    wall_seconds: float
    compile: CompileTally
    imports: ImportTally = NO_IMPORTS
    # 区間の doeff-vm の仕事の量(数の口が無い時・収集の段階は None)。
    work: VmWork | None = None

    @property
    def judged_seconds(self) -> float:
        """判定に使う秒 = 区間の CPU 秒から、変換の CPU 秒と依存の初回の import の CPU 秒を引いた値(どちらも無ければ区間の
        CPU 秒そのもの)。"""
        return self.cpu_seconds - self.compile.cpu_seconds - self.imports.cpu_seconds


@dataclass(frozen=True)
class WithinBudget:
    measurement: Measurement


def judged_value(measurement: Measurement, unit: Unit) -> float:
    """判定の単位で見た測りの値 — 秒なら変換と import を引いた CPU 秒、歩数なら区間の歩数(数の無い測りを歩数で判じない)。"""
    match unit:
        case "seconds":
            return measurement.judged_seconds
        case "steps":
            if measurement.work is None:
                raise AssertionError(f"歩数の無い測りを歩数で判じた: {measurement.key}")
            return float(measurement.work.steps)


@dataclass(frozen=True)
class OverBudget:
    """上限を超えた(登録簿に無い)。budget = 判定した上限(unit の単位)。"""

    measurement: Measurement
    budget: float
    unit: Unit = "seconds"


@dataclass(frozen=True)
class RegisteredOverBudget:
    """上限を超えたが登録簿に載っている(赤にしない)。"""

    measurement: Measurement
    unit: Unit = "seconds"


@dataclass(frozen=True)
class RegisteredWithinBudget:
    """登録簿に載っているが上限の内に終わった(消せる登録)。budget = 判定した上限(unit の単位)。"""

    measurement: Measurement
    budget: float
    unit: Unit = "seconds"

    @property
    def stale(self) -> bool:
        """古い登録として赤にするか — 上限 × STALE_RATIO 以下で終わった時だけ。"""
        return judged_value(self.measurement, self.unit) <= self.budget * STALE_RATIO


Verdict = WithinBudget | OverBudget | RegisteredOverBudget | RegisteredWithinBudget


class RegistryError(Exception):
    """登録簿の file の形が決まりに合わない。"""


class BudgetWarning(pytest.PytestWarning):
    """報告のみの形で上限を超えた検の警告。"""


def registry_file_name(key: str) -> str:
    """登録簿の鍵の file の名を作る(鍵の sha256 の先頭 12 字 + .txt)— 同時に走る便が同じ行を書き換えて衝突しないため。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] + ".txt"


def load_registry(directory: Path) -> dict[str, str]:
    """登録簿の dir を 鍵 → 理由 に読む — 載った検を赤にしないため。dir が無ければ空。名が鍵の hash と合わない・理由が空の file は止める。"""
    if not directory.is_dir():
        return {}
    table: dict[str, str] = {}
    for path in sorted(directory.glob("*.txt")):
        lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        key = lines[0].strip()
        reason = "\n".join(lines[1:]).strip()
        if path.name != registry_file_name(key):
            raise RegistryError(
                f"{path} の名が鍵の hash と合わない(鍵 {key!r} の名は {registry_file_name(key)})"
            )
        if not reason:
            raise RegistryError(f"{path} に理由が無い(2 行目から理由を書く)")
        table[key] = reason
    return table


def judge(measurement: Measurement, budget: float, registry: Mapping[str, str]) -> Verdict:
    """測った 1 区間を、変換の CPU 秒を引いた値で上限と登録簿に照らす(キャッシュ無しの重さを検の重さにしない)。"""
    if measurement.judged_seconds <= budget:
        if measurement.key in registry:
            return RegisteredWithinBudget(measurement, budget)
        return WithinBudget(measurement)
    if measurement.key in registry:
        return RegisteredOverBudget(measurement)
    return OverBudget(measurement, budget)


def judge_steps(measurement: Measurement, budget_steps: int, registry: Mapping[str, str]) -> Verdict:
    """測った 1 区間を、歩数の上限と歩数の登録簿に照らす — 機体の負荷に依らず、同じ検は毎回同じ判定になるため(#2670)。"""
    steps = judged_value(measurement, "steps")
    if steps <= budget_steps:
        if measurement.key in registry:
            return RegisteredWithinBudget(measurement, float(budget_steps), "steps")
        return WithinBudget(measurement)
    if measurement.key in registry:
        return RegisteredOverBudget(measurement, "steps")
    return OverBudget(measurement, float(budget_steps), "steps")


@dataclass(frozen=True)
class SettingError:
    """設定の値が読めない(文つき)— session の始めに UsageError で止める。"""

    message: str


def parse_positive_seconds(raw: str) -> float | SettingError:
    """秒の文字を正の数に読む。数でない・正でない値は既定へ黙って倒さず誤りにする。"""
    try:
        value = float(raw)
    except ValueError:
        return SettingError(f"正の秒の数: {raw!r}")
    if value <= 0:
        return SettingError(f"正の秒の数: {raw!r}")
    return value


def parse_marker_budgets(lines: Sequence[str]) -> Mapping[str, float] | SettingError:
    """印ごとの実行の上限の行(``印=秒``)を 印 → 秒 に読む。= の無い行・空の印・読めない秒・同じ印の 2 行は誤り。"""
    table: dict[str, float] = {}
    for line in lines:
        marker, sep, raw = line.partition("=")
        marker = marker.strip()
        if not sep or not marker:
            return SettingError(f"{MARKER_CALL_BUDGET_INI} の行は 印=秒 の形: {line!r}")
        if marker in table:
            return SettingError(f"{MARKER_CALL_BUDGET_INI} に印 {marker} の行が 2 つある")
        match parse_positive_seconds(raw.strip()):
            case SettingError(message=message):
                return SettingError(f"{MARKER_CALL_BUDGET_INI} の {marker} は{message}")
            case float() as seconds:
                table[marker] = seconds
    return table


def call_budget_for(
    markers: Iterable[str], by_marker: Mapping[str, float], default: float | None
) -> float | None:
    """検 1 本の実行の上限を選ぶ — 当たる印の秒のうち最も長い物、当たる印が無ければ既定(None = 測らない)。"""
    matched = [by_marker[name] for name in markers if name in by_marker]
    return max(matched) if matched else default


def load_registries(root: Path, directories: Sequence[str]) -> dict[str, str]:
    """登録簿の dir を順に読み、全 dir の 鍵 → 理由 にまとめる(同じ鍵が 2 つの dir にあれば先の dir の理由)。"""
    table: dict[str, str] = {}
    for directory in directories:
        for key, reason in load_registry(root / directory).items():
            table.setdefault(key, reason)
    return table


def _seconds_text(measurement: Measurement) -> str:
    """測りの秒の書き方(判定に使う CPU 秒・壁時計・引いた変換)。"""
    text = f"CPU {measurement.judged_seconds:.3f} 秒(壁時計 {measurement.wall_seconds:.3f} 秒"
    if measurement.compile.count:
        text += f"・キャッシュ無しの変換 {measurement.compile.count} 回の CPU {measurement.compile.cpu_seconds:.3f} 秒を引いた"
    if measurement.imports.count:
        text += f"・依存の module の初回の import {measurement.imports.count} 回の CPU {measurement.imports.cpu_seconds:.3f} 秒を引いた"
    if measurement.work is not None:
        text += f"・doeff-vm の歩数 {measurement.work.steps}・handler の呼び出し {measurement.work.handler_calls} 回"
    return text + ")"


def _limit_text(budget: float, unit: Unit) -> str:
    """上限の書き方(単位つき)— 文の中で秒と歩数を取り違えないため。"""
    match unit:
        case "seconds":
            return f"CPU {budget:.3f} 秒"
        case "steps":
            return f"歩数 {int(budget)}"


def _registry_ini(unit: Unit) -> str:
    """単位ごとの登録簿の設定の名 — 足し先の dir を名指す文のため。"""
    match unit:
        case "seconds":
            return REGISTRY_INI
        case "steps":
            return STEPS_REGISTRY_INI


def over_budget_message(verdict: OverBudget, registry_dirs: Sequence[str]) -> str:
    """上限を超えた検の文 — 何秒(何歩)かかったか・上限・直し方を読み手に渡すため(足し先は、その単位の登録簿の 1 行目の dir)。"""
    m = verdict.measurement
    what = "実行(call)" if m.phase == "call" else "読み込み(import)"
    where = registry_dirs[0] if registry_dirs else f"{_registry_ini(verdict.unit)} で指す dir"
    return (
        f"doeff の検の時間の上限を超えた: {m.key} の{what}が {_seconds_text(m)}・上限 {_limit_text(verdict.budget, verdict.unit)}。"
        "検を速くする(模擬の世界を小さくする・待ちを書き込みで起こす)か、直せない理由があれば "
        f"{where} に鍵 {m.key!r} の file {registry_file_name(m.key)} を理由つきで足す(登録簿は縮める向きだけ)。"
    )


def stale_registration_message(verdict: RegisteredWithinBudget, registry_dirs: Sequence[str]) -> str:
    """古い登録を赤にした時の文(どの検が・何秒〔何歩〕で・上限のいくらで・どの file を消すか)。"""
    m = verdict.measurement
    return (
        f"{m.key} は登録簿に載っているが、{m.phase} が上限 {_limit_text(verdict.budget, verdict.unit)} の"
        f"{STALE_RATIO:g} 倍以下({_seconds_text(m)})— 古い登録なので登録簿の file "
        f"{registry_file_name(m.key)} を消す(登録簿は縮める向きだけ・agora-redesign #1726)"
    )


class CompileCounter:
    """測りから引く区間(source から code への変換・別の module の初回の import)の回数と CPU 秒を数える — 検の重さに
    キャッシュ無しの重さと、共有の依存の一度きりの import の重さを入れないため。

    ``SourceFileLoader.source_to_code`` はバイトコードのキャッシュに当たらない時だけ呼ばれ、Hy の import も Hy が
    差し替えた同じ口を通る(Hy の差し替えは元の口を呼び直すので、Hy がこの数えの前後どちらで差し替えても数えに乗る)。
    変換の中で別の module の import と変換が起きる(Hy の ``require``)ので、秒は最も外側の区間だけを、その区間の種類の
    欄に足す(依存の import の中の変換は依存の import の欄に 1 度だけ入る — 二重に引かない)。

    別の module の初回の import は ``importlib._bootstrap._find_and_load`` の区間(import 文も ``importlib.import_module`` も、
    sys.modules に無い名の時にここを通る)。共有の依存の一度きりの重さは、その process で最初に import した検の file に
    乗るので、引かないと同じ file の合否が並び順・1 file だけの実行か・テストの索引が冷えているかで変わる
    (agora-redesign #1752 — 索引が冷えた初回の収集だけ赤になっていた)。検の file 自身は doeff-adr が
    ``module_from_spec`` と ``exec_module`` で読むのでこの区間に入らず、file 自身の本体の重さは測りに残る。

    doeff-effect-analyzer が入っていれば、その Hy の展開(展開した構文木の cache に当たらない時だけ走る — 同じく
    キャッシュ無しの重さ)も変換の数えに足す(``observe_expansions`` の口 — 解析器は誰が数えるかを知らない・agora-redesign #1534)。
    """

    def __init__(self) -> None:
        self._mut_count = 0
        self._mut_cpu_seconds = 0.0
        self._mut_import_count = 0
        self._mut_import_cpu_seconds = 0.0
        self._mut_depth = 0
        self._mut_outer: SetAside | None = None
        self._mut_original: Callable[..., object] | None = None
        self._mut_original_find_and_load: Callable[..., object] | None = None
        self._mut_stop_observing: Callable[[], None] | None = None

    def tally(self) -> CompileTally:
        """今までの変換の数え(区間の前後の差で使う)。"""
        return CompileTally(self._mut_count, self._mut_cpu_seconds)

    def import_tally(self) -> ImportTally:
        """今までの依存の初回の import の数え(区間の前後の差で使う)。"""
        return ImportTally(self._mut_import_count, self._mut_import_cpu_seconds)

    @contextlib.contextmanager
    def _set_aside(self, kind: SetAside) -> Generator[None]:
        """引く区間 1 回 — 最も外側の区間なら、その CPU 秒をその区間の種類の欄に足すため。"""
        if kind == "compile":
            self._mut_count += 1
        else:
            self._mut_import_count += 1
        if self._mut_depth == 0:
            self._mut_outer = kind
        self._mut_depth += 1
        started = time.process_time()
        try:
            yield
        finally:
            self._mut_depth -= 1
            if self._mut_depth == 0:
                elapsed = time.process_time() - started
                if self._mut_outer == "compile":
                    self._mut_cpu_seconds += elapsed
                else:
                    self._mut_import_cpu_seconds += elapsed
                self._mut_outer = None

    def converting(self) -> contextlib.AbstractContextManager[None]:
        """キャッシュ無しの変換 1 回の区間。"""
        return self._set_aside("compile")

    def importing(self) -> contextlib.AbstractContextManager[None]:
        """別の module の初回の import 1 回の区間。"""
        return self._set_aside("import")

    def install(self) -> None:
        """数える口を差し込む(session の間だけ — uninstall で戻す)。"""
        original = importlib.machinery.SourceFileLoader.source_to_code
        self._mut_original = original
        counter = self

        def counting_source_to_code(loader: object, *args: object, **kwargs: object) -> object:
            """元の変換を呼び、キャッシュ無しの変換の区間として数える。"""
            with counter.converting():
                return original(loader, *args, **kwargs)

        importlib.machinery.SourceFileLoader.source_to_code = counting_source_to_code  # type: ignore[method-assign]  # 数えるための差し替え(uninstall で戻す)
        bootstrap = sys.modules[_BOOTSTRAP_MODULE]
        original_find_and_load = getattr(bootstrap, _FIND_AND_LOAD)
        self._mut_original_find_and_load = original_find_and_load

        def counting_find_and_load(name: str, import_: object) -> object:
            """元の import を呼び、sys.modules に無い名なら依存の初回の import の区間として数える。"""
            if name in sys.modules:
                return original_find_and_load(name, import_)
            with counter.importing():
                return original_find_and_load(name, import_)

        # 数えるための差し替え(uninstall で戻す)— import の機構は呼ぶたびにこの名を module から引くので、import 文にも効く。
        setattr(bootstrap, _FIND_AND_LOAD, counting_find_and_load)
        self._mut_stop_observing = observe_analyzer_expansions(self.converting)

    def uninstall(self) -> None:
        """install の前の口へ戻す。"""
        if self._mut_original is not None:
            importlib.machinery.SourceFileLoader.source_to_code = self._mut_original  # type: ignore[method-assign]  # install の前へ戻す
            self._mut_original = None
        if self._mut_original_find_and_load is not None:
            setattr(sys.modules[_BOOTSTRAP_MODULE], _FIND_AND_LOAD, self._mut_original_find_and_load)
            self._mut_original_find_and_load = None
        if self._mut_stop_observing is not None:
            self._mut_stop_observing()
            self._mut_stop_observing = None


def observe_analyzer_expansions(
    converting: Callable[[], contextlib.AbstractContextManager[None]],
) -> Callable[[], None] | None:
    """doeff-effect-analyzer の Hy の展開も数えに乗せるため(解析器が入っていない環境では何もしない — None)。"""
    try:
        from doeff_effect_analyzer.program_effects import observe_expansions
    except ImportError:
        return None
    return observe_expansions(converting)


@dataclass(frozen=True)
class CallMeasured:
    """call の段階の CPU 秒と、変換と依存の初回の import(makereport で判定するまで item に置く)。"""

    cpu_seconds: float
    compile: CompileTally
    imports: ImportTally
    work: VmWork | None = None


_BUDGETS_KEY = pytest.StashKey[Budgets]()
_COUNTER_KEY = pytest.StashKey[CompileCounter]()
_VERDICTS_KEY = pytest.StashKey[list[Verdict]]()
_CALL_MEASURED_KEY = pytest.StashKey[CallMeasured]()


def pytest_addoption(parser: pytest.Parser) -> None:
    """設定の名を pytest に登録する。"""
    parser.addini(
        CALL_BUDGET_INI, "Hy の検 1 本の実行(call の段階)の上限の CPU 秒(空 = 測らない)", default=""
    )
    parser.addini(
        MARKER_CALL_BUDGET_INI,
        "印ごとの Hy の検 1 本の実行の上限の CPU 秒(1 行 = 印=秒・複数の印に当たれば最も長い秒)",
        type="linelist",
        default=[],
    )
    parser.addini(
        COLLECT_BUDGET_INI,
        "Hy の検の file 1 本の module の import の上限の CPU 秒(空 = 測らない)",
        default="",
    )
    parser.addini(
        MODE_INI,
        "上限を超えた時の扱い: report(警告と一覧だけ・既定)か fail(赤にする)",
        default="report",
    )
    parser.addini(
        REGISTRY_INI,
        "上限を超えてよい既存の検の登録簿の dir(rootdir からの相対・1 行 = 1 dir)",
        type="linelist",
        default=[],
    )
    parser.addini(
        JUDGE_INI,
        "実行の判定に使う物: seconds(CPU 秒・既定)か steps(doeff-vm の歩数 — 負荷で揺れない数)",
        default="seconds",
    )
    parser.addini(
        CALL_STEPS_INI,
        "判定が steps の時の、Hy の検 1 本の実行の上限の歩数(正の整数・空 = 歩数で判じない)",
        default="",
    )
    parser.addini(
        STEPS_REGISTRY_INI,
        "歩数の上限を超えてよい既存の検の登録簿の dir(秒の登録簿と分ける・1 行 = 1 dir)",
        type="linelist",
        default=[],
    )


def _seconds(config: pytest.Config, name: str) -> float | None:
    """上限の秒を読む(空 = 測らない)。数でない・正でない値は既定へ黙って倒さず止める。"""
    raw = str(config.getini(name)).strip()
    if not raw:
        return None
    match parse_positive_seconds(raw):
        case SettingError(message=message):
            raise pytest.UsageError(f"{name} は{message}")
        case float() as value:
            return value


def _marker_seconds(config: pytest.Config) -> Mapping[str, float]:
    """印ごとの実行の上限を読む。読めない行は止める。"""
    match parse_marker_budgets([str(line) for line in config.getini(MARKER_CALL_BUDGET_INI)]):
        case SettingError(message=message):
            raise pytest.UsageError(message)
        case table:
            return table


def _judge(config: pytest.Config) -> Judge:
    """実行の判定に使う物を読む(戻し方の 1 か所)。語彙の外は止める。"""
    raw = str(config.getini(JUDGE_INI)).strip()
    if raw == "seconds":
        return "seconds"
    if raw == "steps":
        return "steps"
    raise pytest.UsageError(f"{JUDGE_INI} は seconds か steps: {raw!r}")


def parse_positive_steps(raw: str) -> int | SettingError:
    """歩数の文字を正の整数に読む。数でない・正でない値は既定へ黙って倒さず誤りにする。"""
    try:
        value = int(raw)
    except ValueError:
        return SettingError(f"正の整数の歩数: {raw!r}")
    if value <= 0:
        return SettingError(f"正の整数の歩数: {raw!r}")
    return value


def _steps(config: pytest.Config) -> int | None:
    """歩数の上限を読む(空 = 歩数で判じない)。読めない値は止める。"""
    raw = str(config.getini(CALL_STEPS_INI)).strip()
    if not raw:
        return None
    match parse_positive_steps(raw):
        case SettingError(message=message):
            raise pytest.UsageError(f"{CALL_STEPS_INI} は{message}")
        case int() as value:
            return value


def _mode(config: pytest.Config) -> Mode:
    """超えた時の扱いを読む。語彙の外は止める。"""
    raw = str(config.getini(MODE_INI)).strip()
    if raw == "report":
        return "report"
    if raw == "fail":
        return "fail"
    raise pytest.UsageError(f"{MODE_INI} は report か fail: {raw!r}")


def pytest_configure(config: pytest.Config) -> None:
    """上限が 1 つでも設定されていれば、登録簿を読み、変換の数えを差し込む。"""
    call_seconds = _seconds(config, CALL_BUDGET_INI)
    call_seconds_by_marker = _marker_seconds(config)
    collect_seconds = _seconds(config, COLLECT_BUDGET_INI)
    call_steps = _steps(config)
    if call_seconds is None and not call_seconds_by_marker and collect_seconds is None and call_steps is None:
        return
    mode = _mode(config)
    registry_dirs = tuple(str(line) for line in config.getini(REGISTRY_INI))
    steps_registry_dirs = tuple(str(line) for line in config.getini(STEPS_REGISTRY_INI))
    try:
        registry = load_registries(Path(config.rootpath), registry_dirs)
        steps_registry = load_registries(Path(config.rootpath), steps_registry_dirs)
    except RegistryError as exc:
        raise pytest.UsageError(str(exc)) from None
    config.stash[_BUDGETS_KEY] = Budgets(
        call_seconds,
        call_seconds_by_marker,
        collect_seconds,
        mode,
        registry,
        registry_dirs,
        read_vm_build(),
        judge=_judge(config),
        call_steps=call_steps,
        steps_registry=types.MappingProxyType(steps_registry),
        steps_registry_dirs=steps_registry_dirs,
        work_source=read_work_source(),
    )
    budgets = config.stash[_BUDGETS_KEY]
    if budgets.judge == "steps" and not budgets.judges_steps:
        # 歩数で判じると決めたのに判じられない(上限が無い・数の口が無い)— 黙って秒へ倒さず、名指して秒で判じる。
        why = budgets.work_source.reason if isinstance(budgets.work_source, NoWorkReader) else f"{CALL_STEPS_INI} が空"
        warnings.warn(BudgetWarning(f"{JUDGE_INI} = steps だが歩数で判じられない({why})— この走行は CPU 秒で判じる"), stacklevel=1)
    counter = CompileCounter()
    counter.install()
    config.stash[_COUNTER_KEY] = counter
    config.stash[_VERDICTS_KEY] = []


def pytest_unconfigure(config: pytest.Config) -> None:
    """差し込んだ変換の数えを外す。"""
    counter = config.stash.get(_COUNTER_KEY, None)
    if counter is not None:
        counter.uninstall()


def _relative_key(path: Path, root: Path) -> str:
    """収集の鍵(rootdir からの path)。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _record(config: pytest.Config, verdict: Verdict) -> bool:
    """判定を session の一覧に足し、赤にするべきかを返す(報告のみの形なら警告だけ出して赤にしない)。"""
    config.stash[_VERDICTS_KEY].append(verdict)
    budgets = config.stash[_BUDGETS_KEY]
    match verdict:
        case OverBudget():
            if budgets.fails_for(verdict.unit):
                return True
            warnings.warn(
                BudgetWarning(over_budget_message(verdict, budgets.registry_dirs_for(verdict.unit))), stacklevel=1
            )
            return False
        case RegisteredWithinBudget():
            return verdict.stale and budgets.fails_for(verdict.unit)
        case WithinBudget() | RegisteredOverBudget():
            return False


def _failure_message(verdict: Verdict, budgets: Budgets) -> str:
    """赤にした判定の文(足し先・消す先は、判定した単位の登録簿)。"""
    match verdict:
        case OverBudget():
            return over_budget_message(verdict, budgets.registry_dirs_for(verdict.unit))
        case RegisteredWithinBudget():
            return stale_registration_message(verdict, budgets.registry_dirs_for(verdict.unit))
        case WithinBudget() | RegisteredOverBudget():
            raise AssertionError(f"赤にしない判定の文を求めた: {verdict}")


@pytest.hookimpl(wrapper=True, optionalhook=True)
def pytest_doeff_import_hy_module(
    collector: pytest.Module,
) -> Generator[None, types.ModuleType, types.ModuleType]:
    """Hy の検の file 1 本の module の import を測って判定する(doeff-adr の hook — 収集の中でも setup の中でも)。"""
    config = collector.config
    budgets = config.stash.get(_BUDGETS_KEY, None)
    if budgets is None or budgets.collect_seconds is None or collector.path.suffix != ".hy":
        return (yield)
    counter = config.stash[_COUNTER_KEY]
    compile_before = counter.tally()
    imports_before = counter.import_tally()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    module = yield
    measurement = Measurement(
        key=_relative_key(collector.path, Path(config.rootpath)),
        phase="collect",
        cpu_seconds=time.process_time() - cpu_started,
        wall_seconds=time.perf_counter() - wall_started,
        compile=counter.tally().since(compile_before),
        imports=counter.import_tally().since(imports_before),
    )
    verdict = judge(measurement, budgets.collect_seconds, budgets.registry)
    if _record(config, verdict):
        pytest.fail(_failure_message(verdict, budgets), pytrace=False)
    return module


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """call の段階の CPU 秒と変換を測って item に置く(判定は makereport)。"""
    counter = item.config.stash.get(_COUNTER_KEY, None)
    if counter is None:
        return (yield)
    source = item.config.stash[_BUDGETS_KEY].work_source
    work_before = source.tally() if isinstance(source, WorkReader) else None
    compile_before = counter.tally()
    imports_before = counter.import_tally()
    cpu_started = time.process_time()
    try:
        return (yield)
    finally:
        cpu_seconds = time.process_time() - cpu_started
        work = source.tally().since(work_before) if isinstance(source, WorkReader) and work_before is not None else None
        item.stash[_CALL_MEASURED_KEY] = CallMeasured(
            cpu_seconds=cpu_seconds,
            compile=counter.tally().since(compile_before),
            imports=counter.import_tally().since(imports_before),
            work=work,
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """通った Hy の検 1 本の call の段階を、その検の印で選んだ上限で判定する(落ちた検はその失敗のまま)。"""
    report = yield
    config = item.config
    budgets = config.stash.get(_BUDGETS_KEY, None)
    measured = item.stash.get(_CALL_MEASURED_KEY, None)
    if (
        budgets is None
        or measured is None
        or call.when != "call"
        or report.outcome != "passed"
        or item.path.suffix != ".hy"
    ):
        return report
    markers = [marker.name for marker in item.iter_markers()]
    measurement = Measurement(
        key=item.nodeid,
        phase="call",
        cpu_seconds=measured.cpu_seconds,
        wall_seconds=call.duration,
        compile=measured.compile,
        imports=measured.imports,
        work=measured.work,
    )
    # 印ごとの秒の上限に当たる検(本物の I/O を持つ検 — real_world など)は、歩数に重さが出ないので秒で判じる。
    marked = any(name in budgets.call_seconds_by_marker for name in markers)
    if budgets.judges_steps and measured.work is not None and budgets.call_steps is not None and not marked:
        verdict = judge_steps(measurement, budgets.call_steps, budgets.steps_registry)
    else:
        budget = call_budget_for(markers, budgets.call_seconds_by_marker, budgets.call_seconds)
        if budget is None:
            return report
        verdict = judge(measurement, budget, budgets.registry)
    if _record(config, verdict):
        report.outcome = "failed"
        report.longrepr = _failure_message(verdict, budgets)
    return report


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """終わりに、上限を超えた検・登録簿に載った超過・消せる登録を一覧にし、歩数を測れた検の合計(-v なら検ごと)を出す —
    歩数の上限を決める材料にするため(#2852・#2853)。"""
    verdicts = config.stash.get(_VERDICTS_KEY, None)
    if not verdicts:
        return
    budgets = config.stash[_BUDGETS_KEY]
    over = [v for v in verdicts if isinstance(v, OverBudget)]
    registered = [v for v in verdicts if isinstance(v, RegisteredOverBudget)]
    back_in_budget = sorted({v.measurement.key for v in verdicts if isinstance(v, RegisteredWithinBudget)})
    worked = [v.measurement for v in verdicts if v.measurement.work is not None]
    if not (over or registered or back_in_budget or worked):
        return
    terminalreporter.section("doeff の検の時間の上限")
    terminalreporter.line(vm_build_line(budgets.vm_build))
    terminalreporter.line(
        "実行は doeff-vm の歩数で判じる(CPU 秒は報告だけ)" if budgets.judges_steps else "実行は CPU 秒で判じる"
    )
    if worked:
        terminalreporter.line(
            f"歩数を測れた検 {len(worked)} 本・歩数の合計 {sum(m.work.steps for m in worked if m.work is not None)}"
            "(検ごとは -v)"
        )
        if config.get_verbosity() >= 1:
            for m in sorted(worked, key=lambda m: m.work.steps if m.work is not None else 0, reverse=True):
                terminalreporter.line(f"  {m.key}: {_seconds_text(m)}")
    for verdict in over:
        m = verdict.measurement
        label = "赤" if budgets.fails_for(verdict.unit) else "報告のみ"
        terminalreporter.line(
            f"上限を超えた({label}・上限 {_limit_text(verdict.budget, verdict.unit)}): {m.key}({m.phase} {_seconds_text(m)})"
        )
    for verdict in registered:
        m = verdict.measurement
        terminalreporter.line(f"登録簿に載った超過: {m.key}({m.phase} {_seconds_text(m)})")
    for key in back_in_budget:
        terminalreporter.line(
            f"上限の内に戻った登録: {key} — 登録簿の file {registry_file_name(key)} を消せる"
        )
