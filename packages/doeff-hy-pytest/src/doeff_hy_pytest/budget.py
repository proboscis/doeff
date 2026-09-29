"""Hy の検の時間の上限(検 1 本の実行・検の file 1 本の収集)。

模擬の handler で回す検は速いことが前提なので、遅くなった検に気づかせる(proboscis/agora-redesign#907)。
利用側の pyproject の ``[tool.pytest.ini_options]`` に置く設定:

- ``doeff_test_call_budget_seconds`` — Hy の検の file から集めた検 1 本の実行(pytest の call の段階)の上限の CPU 秒。
- ``doeff_test_collect_budget_seconds`` — Hy の検の file 1 本の module の import の上限の CPU 秒。
- ``doeff_test_budget_mode`` — ``report``(既定・超えても赤にせず、警告と終わりの一覧だけ)か ``fail``(超えたら赤)。
- ``doeff_test_budget_registry`` — 上限を超えてよい既存の検の登録簿の dir(rootdir からの相対)。

上限の 2 つがどちらも無ければ何もしない。設計の決め:

- 対象は ``.hy`` の検の file(deftest と、同じ file の素の検)だけ。Python の検は模擬の handler の検とは限らないので外す。
- 判定は CPU 時間(``time.process_time`` — process の全 thread の CPU 時間)で行い、壁時計を併記する。壁時計は機体の負荷
  (並べて走る他の検・席)で伸び縮みし、同じ検の合否が機体の混み具合で変わるため。模擬の handler の検は本物の待ちを
  持たない前提なので、遅さはほぼ CPU 時間に出る。
- 実行は call の段階だけを測り、setup と teardown(fixture)は含めない。session / module の範囲の fixture の準備は、
  その範囲で最初に走った検 1 本に丸ごと乗るので、含めると検の選び方と並び順で同じ検の合否が変わる。deftest の本体
  (模擬の handler で Program を回す所)は call の段階にある。
- file の上限は module の import(依存の module の import を含む)を測る。測る所は doeff-adr の hook
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
- 上限は doeff-vm の不変条件の検査が無効な走行を基準にする。検査が有効な走行(doeff 自身の pytest — root の
  conftest.py が有効にする・agora-redesign #980 からはどの build も検査を持ち実行時に切り替える)は VM の
  1 歩ごとに不変条件を検査し、同じ検が十数倍遅い。session の始めに ``doeff_vm.invariant_checks_enabled()`` を 1 回読み、
  検査つきなら ``fail`` でも赤にせず報告に留める(基準の違う build で誤った赤を出さない)。終わりの要約の見出しに
  build の種類を 1 行出す。読めない(import できない・古い VM で関数が無い)時は「不明」と出し、判定は今のまま。
"""

import hashlib
import importlib.machinery
import time
import types
import warnings
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

CALL_BUDGET_INI = "doeff_test_call_budget_seconds"
COLLECT_BUDGET_INI = "doeff_test_collect_budget_seconds"
MODE_INI = "doeff_test_budget_mode"
REGISTRY_INI = "doeff_test_budget_registry"

Phase = Literal["call", "collect"]
Mode = Literal["report", "fail"]


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
    """設定から読んだ上限(None = その段階は測らない)・超えた時の扱い・登録簿(鍵 → 理由)・doeff-vm の build の種類。"""

    call_seconds: float | None
    collect_seconds: float | None
    mode: Mode
    registry: Mapping[str, str]
    registry_dir: str | None
    vm_build: VmBuild

    @property
    def fails_over_budget(self) -> bool:
        """超過を赤にするか — fail の形で、かつ doeff-vm が検査つきの build でない時だけ。"""
        return self.mode == "fail" and not isinstance(self.vm_build, CheckedVmBuild)


@dataclass(frozen=True)
class CompileTally:
    """ある時点までに数えた source からの変換の回数と、それに使った CPU 秒(最も外側の区間の和)。"""

    count: int
    cpu_seconds: float

    def since(self, before: "CompileTally") -> "CompileTally":
        """before からこの時点までの差 — 1 つの測りの区間の中で起きた変換。"""
        return CompileTally(self.count - before.count, self.cpu_seconds - before.cpu_seconds)


@dataclass(frozen=True)
class Measurement:
    """測った 1 区間 — CPU 秒と壁時計の秒(どちらも区間の全体)と、区間の中の変換。"""

    key: str
    phase: Phase
    cpu_seconds: float
    wall_seconds: float
    compile: CompileTally

    @property
    def judged_seconds(self) -> float:
        """判定に使う秒 = 区間の CPU 秒から変換の CPU 秒を引いた値(キャッシュ有りなら区間の CPU 秒そのもの)。"""
        return self.cpu_seconds - self.compile.cpu_seconds


@dataclass(frozen=True)
class WithinBudget:
    measurement: Measurement


@dataclass(frozen=True)
class OverBudget:
    measurement: Measurement
    budget: float


@dataclass(frozen=True)
class RegisteredOverBudget:
    """上限を超えたが登録簿に載っている(赤にしない)。"""

    measurement: Measurement


Verdict = WithinBudget | OverBudget | RegisteredOverBudget


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
        return WithinBudget(measurement)
    if measurement.key in registry:
        return RegisteredOverBudget(measurement)
    return OverBudget(measurement, budget)


def _seconds_text(measurement: Measurement) -> str:
    """測りの秒の書き方(判定に使う CPU 秒・壁時計・引いた変換)。"""
    text = f"CPU {measurement.judged_seconds:.3f} 秒(壁時計 {measurement.wall_seconds:.3f} 秒"
    if measurement.compile.count:
        text += f"・キャッシュ無しの変換 {measurement.compile.count} 回の CPU {measurement.compile.cpu_seconds:.3f} 秒を引いた"
    return text + ")"


def over_budget_message(verdict: OverBudget, registry_dir: str | None) -> str:
    """上限を超えた検の文 — 何秒かかったか・上限・直し方を読み手に渡すため。"""
    m = verdict.measurement
    what = "実行(call)" if m.phase == "call" else "読み込み(import)"
    where = registry_dir if registry_dir else f"{REGISTRY_INI} で指す dir"
    return (
        f"doeff の検の時間の上限を超えた: {m.key} の{what}が {_seconds_text(m)}・上限 CPU {verdict.budget:.3f} 秒。"
        "検を速くする(模擬の世界を小さくする・待ちを書き込みで起こす)か、直せない理由があれば "
        f"{where} に鍵 {m.key!r} の file {registry_file_name(m.key)} を理由つきで足す(登録簿は縮める向きだけ)。"
    )


class CompileCounter:
    """source から code への変換の回数と CPU 秒を数える — 測りから変換の時間を引くため。

    ``SourceFileLoader.source_to_code`` はバイトコードのキャッシュに当たらない時だけ呼ばれ、Hy の import も Hy が
    差し替えた同じ口を通る(Hy の差し替えは元の口を呼び直すので、Hy がこの数えの前後どちらで差し替えても数えに乗る)。
    変換の中で別の module の import と変換が起きる(Hy の ``require``)ので、秒は最も外側の区間だけを足す。
    """

    def __init__(self) -> None:
        self._mut_count = 0
        self._mut_cpu_seconds = 0.0
        self._mut_depth = 0
        self._mut_original: Callable[..., object] | None = None

    def tally(self) -> CompileTally:
        """今までの数え(区間の前後の差で使う)。"""
        return CompileTally(self._mut_count, self._mut_cpu_seconds)

    def install(self) -> None:
        """数える口を差し込む(session の間だけ — uninstall で戻す)。"""
        original = importlib.machinery.SourceFileLoader.source_to_code
        self._mut_original = original
        counter = self

        def counting_source_to_code(loader: object, *args: object, **kwargs: object) -> object:
            """元の変換を呼び、回数と(最も外側なら)CPU 秒を足す。"""
            counter._mut_count += 1
            counter._mut_depth += 1
            started = time.process_time()
            try:
                return original(loader, *args, **kwargs)
            finally:
                counter._mut_depth -= 1
                if counter._mut_depth == 0:
                    counter._mut_cpu_seconds += time.process_time() - started

        importlib.machinery.SourceFileLoader.source_to_code = counting_source_to_code  # type: ignore[method-assign]  # 数えるための差し替え(uninstall で戻す)

    def uninstall(self) -> None:
        """install の前の口へ戻す。"""
        if self._mut_original is not None:
            importlib.machinery.SourceFileLoader.source_to_code = self._mut_original  # type: ignore[method-assign]  # install の前へ戻す
            self._mut_original = None


@dataclass(frozen=True)
class CallMeasured:
    """call の段階の CPU 秒と変換(makereport で判定するまで item に置く)。"""

    cpu_seconds: float
    compile: CompileTally


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
        REGISTRY_INI, "上限を超えてよい既存の検の登録簿の dir(rootdir からの相対)", default=""
    )


def _seconds(config: pytest.Config, name: str) -> float | None:
    """上限の秒を読む(空 = 測らない)。数でない・正でない値は既定へ黙って倒さず止める。"""
    raw = str(config.getini(name)).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise pytest.UsageError(f"{name} は正の秒の数: {raw!r}") from None
    if value <= 0:
        raise pytest.UsageError(f"{name} は正の秒の数: {raw!r}")
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
    collect_seconds = _seconds(config, COLLECT_BUDGET_INI)
    if call_seconds is None and collect_seconds is None:
        return
    mode = _mode(config)
    registry_dir = str(config.getini(REGISTRY_INI)).strip() or None
    try:
        registry = load_registry(Path(config.rootpath) / registry_dir) if registry_dir else {}
    except RegistryError as exc:
        raise pytest.UsageError(str(exc)) from None
    config.stash[_BUDGETS_KEY] = Budgets(
        call_seconds, collect_seconds, mode, registry, registry_dir, read_vm_build()
    )
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
    if not isinstance(verdict, OverBudget):
        return False
    budgets = config.stash[_BUDGETS_KEY]
    if budgets.fails_over_budget:
        return True
    warnings.warn(BudgetWarning(over_budget_message(verdict, budgets.registry_dir)), stacklevel=1)
    return False


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
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    module = yield
    measurement = Measurement(
        key=_relative_key(collector.path, Path(config.rootpath)),
        phase="collect",
        cpu_seconds=time.process_time() - cpu_started,
        wall_seconds=time.perf_counter() - wall_started,
        compile=counter.tally().since(compile_before),
    )
    verdict = judge(measurement, budgets.collect_seconds, budgets.registry)
    if _record(config, verdict) and isinstance(verdict, OverBudget):
        pytest.fail(over_budget_message(verdict, budgets.registry_dir), pytrace=False)
    return module


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """call の段階の CPU 秒と変換を測って item に置く(判定は makereport)。"""
    counter = item.config.stash.get(_COUNTER_KEY, None)
    if counter is None:
        return (yield)
    compile_before = counter.tally()
    cpu_started = time.process_time()
    try:
        return (yield)
    finally:
        item.stash[_CALL_MEASURED_KEY] = CallMeasured(
            cpu_seconds=time.process_time() - cpu_started,
            compile=counter.tally().since(compile_before),
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """通った Hy の検 1 本の call の段階を判定する(落ちた検はその失敗のまま)。"""
    report = yield
    config = item.config
    budgets = config.stash.get(_BUDGETS_KEY, None)
    measured = item.stash.get(_CALL_MEASURED_KEY, None)
    if (
        budgets is None
        or budgets.call_seconds is None
        or measured is None
        or call.when != "call"
        or report.outcome != "passed"
        or item.path.suffix != ".hy"
    ):
        return report
    measurement = Measurement(
        key=item.nodeid,
        phase="call",
        cpu_seconds=measured.cpu_seconds,
        wall_seconds=call.duration,
        compile=measured.compile,
    )
    verdict = judge(measurement, budgets.call_seconds, budgets.registry)
    if _record(config, verdict) and isinstance(verdict, OverBudget):
        report.outcome = "failed"
        report.longrepr = over_budget_message(verdict, budgets.registry_dir)
    return report


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """終わりに、上限を超えた検・登録簿に載った超過・消せる登録を一覧にする。"""
    verdicts = config.stash.get(_VERDICTS_KEY, None)
    if not verdicts:
        return
    budgets = config.stash[_BUDGETS_KEY]
    over = [v for v in verdicts if isinstance(v, OverBudget)]
    registered = [v for v in verdicts if isinstance(v, RegisteredOverBudget)]
    back_in_budget = sorted(
        {
            v.measurement.key
            for v in verdicts
            if isinstance(v, WithinBudget) and v.measurement.key in budgets.registry
        }
    )
    if not (over or registered or back_in_budget):
        return
    terminalreporter.section("doeff の検の時間の上限")
    terminalreporter.line(vm_build_line(budgets.vm_build))
    label = "赤" if budgets.fails_over_budget else "報告のみ"
    for verdict in over:
        m = verdict.measurement
        terminalreporter.line(
            f"上限を超えた({label}・上限 CPU {verdict.budget:.3f} 秒): {m.key}({m.phase} {_seconds_text(m)})"
        )
    for verdict in registered:
        m = verdict.measurement
        terminalreporter.line(f"登録簿に載った超過: {m.key}({m.phase} {_seconds_text(m)})")
    for key in back_in_budget:
        terminalreporter.line(
            f"上限の内に戻った登録: {key} — 登録簿の file {registry_file_name(key)} を消せる"
        )
