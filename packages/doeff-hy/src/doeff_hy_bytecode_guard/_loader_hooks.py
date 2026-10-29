"""``SourceFileLoader`` の 2 つの口の包み(file の読み書きをするのはこの module だけ)。

起動時に読まれるので、import は標準 library の軽い物に限る。記録の形と照合の判断は
:mod:`doeff_hy_bytecode_guard._records`(純関数)にあり、Hy の source に初めて当たった時に import する。

包みの外に別の包み(Hy の ``source_to_code``・doeff-hy-pytest の計時)が重なっても、下に入っても効く:
各包みは呼ばれた時の 1 つ前の関数を呼ぶだけで、この module の包みは「Hy の source か」「compile の直後か」だけを見る。
"""

import importlib.machinery
import importlib.util
import os
import sys
from collections.abc import Callable, Iterable
from types import CodeType, ModuleType

# 型検査のための展開の印(contextvars だけを読む軽い module — 起動時に読んでよい。compile の途中で初めて読むと、その .pyc を
# bytecode を書く設定の中で書いてしまう)。
from doeff_hy_bytecode_guard._expansion import TYPE_CHECK_EXPANSION

# 一番外の読み込み 1 つの間の覚え(contextvars と _thread だけを読む軽い module — 起動時に読んでよい)。
from doeff_hy_bytecode_guard._import_scope import IMPORT_SCOPE, ImportScope, current_scope

# 一番外の読み込みの間の GC の閾値の持ち主(gc・os・_thread だけを読む軽い module — 起動時に読んでよい)。
from doeff_hy_bytecode_guard import _import_gc as import_gc

TYPE_CHECKING = False  # typing を起動時に読まない(2 ms)— 型検査器はこの名の分岐を真として読む

if TYPE_CHECKING:
    import contextlib

    from doeff_hy_bytecode_guard._macro_use import MacroRecording
    from doeff_hy_bytecode_guard._records import MacroRecord, ValueReference

#: Hy の source の拡張子(doeff-hy は .hyk・.hyp も Hy として読ませる — doeff_hy/__init__.py)。定義点は保存先の module(code の鍵が
#: Hy の source かで欄を分ける)— 標準 library だけを読む軽い module なので起動時に読んでよい。
from doeff_hy_bytecode_guard._code_store import HY_SOURCE_SUFFIXES as HY_SOURCE_SUFFIXES

GetCode = Callable[[importlib.machinery.SourceFileLoader, str], "CodeType | None"]
GetData = Callable[[importlib.machinery.SourceFileLoader, str], bytes]
SourceToCode = Callable[..., CodeType]
ExecModule = Callable[[importlib.machinery.SourceFileLoader, ModuleType], None]

#: 包んだかどうか(process に 1 つ)。包みは process の ``SourceFileLoader`` という 1 つの共有物を書き換えるので、
#: 持ち主はこの module 1 つ。
#: .pth(起動時の 1 thread)と ``import doeff_hy``(import の lock の中)からしか呼ばれないので lock は持たない。
_installation = {"installed": False, "dataclasses": False}

#: この thread で Hy の source を compile した回数(path ごと)— get_code が「今の呼び出しの中で compile したか」を
#: 前後の回数の差で知るため(get_code は入れ子になる — compile の中の require が別の module を import する)。
#: threading は起動時に読まないため、初めて Hy の source に当たった時に作る。
_compile_counts: dict[str, object] = {}

#: この thread で最後に読んだ Hy の source の bytes(path ごと)— compile した bytes を、保存先の鍵に使うため
#: (agora-redesign #2799)。get_code が終わる時にその path の分を捨てる(source を process の間ずっと持たない)。
_source_reads: dict[str, object] = {}

#: file の sha256 — 鍵は (path, 更新時刻 ns, 大きさ)。同じ process で同じ macro の file を何百回も照合するので、
#: 変わっていない file を読み直さない。
_digests: dict[tuple[str, int, int], str] = {}

#: 本体を実行した source の file の状態(path → (更新時刻 ns, 大きさ) — 読みの口が本体の実行の直前に読んだ物・process の間)。
#: .pyc の記録の照らしの歩みは、読み込み済みの module の値を process の中から、その値を覆う文と file の sha256 を今の disk から
#: 読む。module を実行した後に file が書き換わった常駐の process では 2 つが食い違うので、歩みが確かめる(設計検証
#: lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 A — card ki-be596ac8276f)。
_executed: dict[str, tuple[int, int]] = {}

#: 読みの口が返した Hy の module の code に載った記録(source の path → 記録 — 記録の無い code は載せない・process の間)。
#: Hy の test file の item の記録の cache(doeff-adr の item_cache)が、import した test file の展開が使った macro を、file を
#: 読み直さずに引くため(:func:`loaded_record`)。
_loaded_records: dict[str, "MacroRecord"] = {}


def installed() -> bool:
    """この process の ``SourceFileLoader`` が包まれているか(起動時の .pth が効いたかを検査が確かめるため)。"""
    return _installation["installed"]


def install() -> None:
    """macro が変わった Hy の module を compile し直すよう ``SourceFileLoader`` の 2 つの口を包む(何度呼んでも 1 度だけ)。
    module の本体の実行の口も包み、一番外の実行の間、照らしが file の状態を 1 度だけ読む覚え(:mod:`doeff_hy_bytecode_guard._import_scope`)
    を置く。dataclasses が読み込み済みなら、型ごとの関数の source の compile も覚える(:mod:`doeff_hy_bytecode_guard._dataclass_code`)。"""
    _remember_dataclass_code()
    if _installation["installed"]:
        return
    loader = importlib.machinery.SourceFileLoader
    loader.source_to_code = _recording_source_to_code(loader.source_to_code)
    loader.get_code = _checking_get_code(loader.get_code)
    loader.get_data = _remembering_get_data(loader.get_data)
    loader.exec_module = _scoped_exec_module(loader.exec_module)
    _installation["installed"] = True


def _remember_dataclass_code() -> None:
    """読み込み済みの dataclasses の関数の source の compile を覚える形にする(1 度だけ)。dataclasses を自分から読まない — 起動時の
    .pth では大抵まだ無く、Hy の import が読むので、Hy の source を初めて読む時(読みの口)にもう一度呼ぶ。"""
    if _installation["dataclasses"]:
        return
    dataclasses = sys.modules.get("dataclasses")
    if dataclasses is None:
        return
    from doeff_hy_bytecode_guard import _dataclass_code as dataclass_code  # dataclasses が在る時だけ読む

    dataclass_code.install(dataclasses)
    _installation["dataclasses"] = True


def is_hy_source(path: object) -> bool:
    """包みが相手にする source か(Hy の拡張子の文字列の path だけ — それ以外は 1 つ前の関数へそのまま渡す)。"""
    return isinstance(path, str) and path.endswith(HY_SOURCE_SUFFIXES)


def loaded_record(path: str) -> "MacroRecord | None":
    """この process で読みの口が返した Hy の module の code に載った記録(path = source の file。読んでいない・記録の無い code は
    None)— Hy の test file の item の記録の cache(doeff-adr の item_cache — agora-redesign #1291)が、import した test file の
    展開が使った macro を、展開し直さず file も読み直さずに引くための公開の口。照らしは :func:`record_is_current_here`(.pyc と
    同じ 1 つの照らし方 — 展開で実行された関数の中身だけを覆う。file 単位で照らすと、macro の提供元のどの行の変更でも item の
    記録が全部冷えた・#3769 の子)。"""
    return _loaded_records.get(path)


def macro_recording(module: ModuleType) -> "MacroRecording":
    """Hy の module の展開(compile)を囲む口 — ``with macro_recording(module) as recording:`` の中の展開が使った macro を拾い、
    ``recording.record(source)`` が記録を作る。compile の口が code に足す記録(.pyc)と、展開した木の cache(doeff-effect-analyzer
    — agora-redesign #3598)と、型検査の展開の保存(doeff_hy.static_cache — agora-redesign #3862)が同じ 1 つの拾い方と作り方を
    使うための公開の口。``record`` の ``also`` は macro の外で展開の結果を変える値の在処(型検査の展開の後処理)。"""
    from doeff_hy_bytecode_guard import _macro_use as macro_use  # 起動時に読まない

    return macro_use.MacroRecording(module, _hy_version(), file_sha256)


def gensym_renaming(names: "Iterable[str]") -> "Callable[[str], str]":
    """名の並びに現れる Hy の gensym の名を、数えの小さい順に 1 から振り直す関数(他の名はそのまま返す)— code の木の正準化
    (``records.canonical_gensyms``)と同じ規則を、型検査の展開の木(doeff_hy.static_check — agora-redesign #3869)にも当てる
    公開の口。展開の文が、同じ process で先に何を展開したか・どの process で展開したかに依らなくなる。"""
    from doeff_hy_bytecode_guard import _records as records  # 起動時に読まない

    renames = records.gensym_renames(names)
    return lambda name: records.renamed(name, renames)


def record_is_current_here(record: "MacroRecord") -> bool:
    """記録が、今の Hy の版と今の環境の macro に合うか — 使った macro を module 名から今の環境で引き直し(定義した module を
    import する — 引けなければ古い)、その code の閉包の digest を比べる。記録か今の歩みが import の途中の module に当たった行は、
    digest でなく閉包が依った module の file の sha256 を import せずに比べる。記録は file の path を持たないので、別の木で作った .pyc(実行環境の
    準備が前の root から hardlink で引き継ぐ物 — agora-redesign #2598)や保存先の code も今の木の macro で照らす。.pyc の記録と、
    展開した木の cache(doeff-effect-analyzer — agora-redesign #3598)と、型検査の展開の保存(doeff_hy.static_cache)が同じ 1 つの
    照らし方を使うための公開の口。"""
    from doeff_hy_bytecode_guard import _macro_use as macro_use  # 起動時に読まない

    return macro_use.record_is_current(record, _hy_version(), file_sha256)


def value_reference(module: str, name: str) -> "ValueReference":
    """macro の外で展開の結果を変える値の在処(module の名と属性の名)— ``recording.record`` の ``also`` に渡す。記録の形の
    持ち主(records)を直に import しないための公開の口。"""
    from doeff_hy_bytecode_guard import _records as records  # 起動時に読まない

    return records.ValueReference(module, name)


def record_fingerprint(record: "MacroRecord") -> str:
    """記録の中身の指紋 — 展開した木の cache(doeff-effect-analyzer)と型検査の展開の保存(doeff_hy.static_cache)の entry の名が
    同じ 1 つの作り方を使うための公開の口。"""
    from doeff_hy_bytecode_guard import _records as records  # 起動時に読まない

    return records.record_fingerprint(record)


def record_to_json(record: "MacroRecord") -> list[object]:
    """記録を JSON に書ける値にする — 記録を file に書いて読み戻す保存(型検査の展開の保存 doeff_hy.static_cache)が、記録の形の
    持ち主(records)を直に import しないための公開の口。"""
    from doeff_hy_bytecode_guard import _records as records  # 起動時に読まない

    return records.record_to_json(record)


def record_from_json(value: object) -> "MacroRecord | None":
    """JSON から読んだ値を記録に組む(形が違えば None)— :func:`record_to_json` の逆。"""
    from doeff_hy_bytecode_guard import _records as records  # 起動時に読まない

    return records.record_from_json(value)


def source_to_code_as_import(
    loader: importlib.machinery.SourceFileLoader, data: bytes, path: str
) -> CodeType:
    """import の外で source を compile する口(bytecode を前もって作る道具が使う)— Hy の source は import と同じく、その module を
    ``sys.modules`` に置いた中で compile し、展開が依った macro の記録を code に足す。

    import の外で ``source_to_code`` を直に呼ぶと、Hy は仮の module を作って compile の直後に消すので、compile の口の包みが
    module を見られず記録を足さない。記録の無い .pyc は import の時に古いかもしれない物として compile し直されるので、前もって
    作った bytecode が無駄になり、起動の時に macro の展開を全部やり直していた(agora-redesign #2598 — 預かり所の job の起動の
    CPU 約 46 秒のうち約 43 秒)。Hy 以外の source は ``source_to_code`` をそのまま呼ぶ。"""
    install()
    if not is_hy_source(path):
        return loader.source_to_code(data, path)
    present = sys.modules.get(loader.name)
    if present is not None and vars(present).get("__file__") == path:
        with macro_recording(_expansion_or(loader, path, present)):  # 読み込み済みの module の中で compile する(import の途中と同じ)
            return loader.source_to_code(data, path)
    with _PlacedExpansion(loader, path):  # compile の口の包みが、この展開が使った macro の記録を足す
        return loader.source_to_code(data, path)


class _PlacedExpansion:
    """module を ``sys.modules`` に置いた中で展開が使った macro を拾う囲み(出る時に置く前へ戻す)— 置く module が無い compile で、
    Hy が仮の module を作って compile の直後に消すと、compile の口の包みが module を見られず記録を足さないため。

    使う所は 2 つ: :func:`source_to_code_as_import`(import の外で bytecode を前もって作る道具)と、import を通らず読みの口が
    呼ばれる読み(runpy の ``hy <script>``・``hy -m`` — :func:`_expansion_recording`)。後者は .pyc に記録が付かず、次の読みで
    必ず古い物として compile し直され、保存先にも入らなかった。"""

    def __init__(self, loader: importlib.machinery.SourceFileLoader, path: str) -> None:
        self._loader = loader
        self._path = path
        self._present = sys.modules.get(loader.name)
        self._mut_recording: MacroRecording | None = None  # 関数の中の注釈は実行時に評価されない(MacroRecording は型検査の時だけ import する)

    def __enter__(self) -> "MacroRecording":
        loader, path = self._loader, self._path
        spec = importlib.util.spec_from_file_location(loader.name, path, loader=loader)
        if spec is None:
            raise ImportError(f"{path} の module の spec を作れない", name=loader.name, path=path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        try:
            self._mut_recording = macro_recording(_expansion_or(loader, path, module)).__enter__()
        except BaseException:
            self._restore()
            raise
        return self._mut_recording

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._mut_recording is not None:
                self._mut_recording.__exit__(*exc_info)
        finally:
            self._restore()

    def _restore(self) -> None:
        if self._present is None:
            sys.modules.pop(self._loader.name, None)
        else:
            sys.modules[self._loader.name] = self._present


def file_sha256(path: str) -> str | None:
    """file の中身の sha256(読めなければ None)— 記録を作る側と照合する側が同じ 1 つを使うため。"""
    import hashlib  # 起動時に読まない

    try:
        status = os.stat(path)
    except OSError:
        return None
    key = (path, status.st_mtime_ns, status.st_size)
    digest = _digests.get(key)
    if digest is None:
        try:
            with open(path, "rb") as source:
                digest = hashlib.sha256(source.read()).hexdigest()
        except OSError:
            return None
        _digests[key] = digest
    return digest


# 戻り値の型は内側の関数の推論に任せる — ``SourceFileLoader.source_to_code`` と引数の名まで同じ形なので、そのまま口へ戻せる
# (Callable の別名で書くと引数の名が消え、口への代入が型の食い違いになる)。
def _recording_source_to_code(previous: SourceToCode):
    """compile の口の包み — Hy の module を compile した直後に、gensym の名を正準化し、展開が依った macro の記録を code に足すため。"""

    def source_to_code(
        self: importlib.machinery.SourceFileLoader,
        data: object,
        path: object,
        *args: object,
        **kwargs: object,
    ) -> CodeType:
        code = previous(self, data, path, *args, **kwargs)
        if not isinstance(path, str) or not is_hy_source(path):
            return code
        from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

        # Hy の source の compile は import・作り直し・前もって作る道具(source_to_code_as_import)・image の組み立て(source_to_code を
        # 直に呼ぶ)のどれもこの口を通る — Hy の口の上でも下でも、ここが受けるのは compile の済んだ code。gensym の名を module の
        # 中の順の通し番号にし、compile の順・process・thread に依らない code にする(agora-redesign #3667)。
        code = records.canonical_gensyms(code)
        counts = _counts()
        counts[path] = counts.get(path, 0) + 1
        module = _module_being_loaded(self, path)
        if module is None:
            # module の外での compile(py_compile・image の組み立ての warm)— 何の macro を require したかが
            # 見えないので記録を足さない。記録の無い bytecode は、Python が source と突き合わせる形なら次の読みで
            # compile し直される(get_code)。突き合わせない形(image の hash 方式)はそのまま信じる。bytecode を前もって
            # 作る道具は source_to_code_as_import で module の中で compile し、記録を付ける。runpy の読み(``hy <script>``・
            # ``hy -m``)は読みの口が module を置いて囲むので、ここへ来ない(_expansion_recording)。
            return code
        if TYPE_CHECK_EXPANSION.get():
            # 型検査のための展開(doeff_hy.static_view)の code は実行できない(:pre の isinstance と実行の時の import が無い)—
            # 記録を付けない。記録の無い code は保存先に入らず(_to_shared_store)、.pyc に書かれても次の普通の import が
            # compile し直す(_record_is_current_here)。付けると普通の展開と同じ鍵で残り、普通の import が読んで落ちた(I-3)。
            return code
        from doeff_hy_bytecode_guard import _macro_use as macro_use  # Hy の source に当たった時だけ読む

        table = vars(module).get(records.MACRO_TABLE)
        if not isinstance(table, macro_use.RecordingTable):
            # 使った macro を拾う囲み(get_code・source_to_code_as_import の macro_recording)の外の compile — 何の macro を使ったかが
            # 見えないので記録を足さない(記録の無い code は次の読みで compile し直される)。
            return code
        # 渡された source の文(Hy の口の下では展開した後の木が来る — その時は読みの口が覚えた bytes か file から読む)。
        source = macro_use.source_text(_last_sources().get(path, data), path)
        if source is None:
            return code
        recording = table.recording
        if recording is None:
            return code
        record = recording.record(source)
        # Hy の口の下では展開した後の木が来る — 同じ source を doeff-effect-analyzer が展開し直さないよう、記録と並べて置く
        # (card ki-e053a847c515)。置き場の dir の持ち主は expansion_trees(木でない物・dir の無い時は何も置かない)。
        from doeff_hy_bytecode_guard import _expansion_trees as expansion_trees

        expansion_trees.add_import_tree(module.__name__, source, record, data)
        return records.with_record(code, record)

    return source_to_code


# 戻り値の型は内側の関数の推論に任せる(上と同じ理由)。
def _remembering_get_data(previous: GetData):
    """読みの口の包み — Hy の source を読んだ bytes を、この thread の「最後に読んだ中身」として覚えるため。compile の口は
    Hy の包みの内側では展開した後の木しか受け取らないので、compile した bytes が見えるのは読みの所だけ。保存先の鍵は
    この bytes から作り、file を読み直さない(agora-redesign #2799)。"""

    def get_data(self: importlib.machinery.SourceFileLoader, path: str) -> bytes:
        data = previous(self, path)
        if is_hy_source(path):
            _last_sources()[path] = data
        return data

    return get_data


# 戻り値の型は内側の関数の推論に任せる(上と同じ理由)。
def _scoped_exec_module(previous: ExecModule):
    """module の本体の実行の口の包み — 一番外の実行(中の import を含む)の間、.pyc の記録の照らしが file の状態を 1 度だけ読む
    覚えを置くため(照らしは Hy の module 1 つごとに走り、照らしごとに確かめると同じ file を何百回も stat した — agora-redesign card
    ki-be596ac8276f)。入れ子の実行は外の覚えを使う。閉じる時は覚えを閉じる — 中で起きた thread と写した context に値が残る。
    同じ間、GC の閾値を上げる(:mod:`doeff_hy_bytecode_guard._import_gc` — 読み込みが heap を育てるたびに heap 全体を掃いていた・
    card ki-de9d3593bbde)。"""

    def exec_module(self: importlib.machinery.SourceFileLoader, module: ModuleType) -> None:
        _note_executed(self.path)
        if current_scope() is not None:
            return previous(self, module)
        scope = ImportScope()
        token = IMPORT_SCOPE.set(scope)
        import_gc.hold()
        try:
            return previous(self, module)
        finally:
            import_gc.release()
            scope.close()
            IMPORT_SCOPE.reset(token)

    return exec_module


def _note_executed(path: object) -> None:
    """本体を実行する source の file の今の状態を覚える(読めない file は覚えを消す)— 照らしの歩みが、module の値と今の file の
    中身が同じ時点の物かを確かめるため(:func:`executed_source_is_current`)。"""
    if not isinstance(path, str):
        return
    try:
        status = os.stat(path)
    except OSError:
        _executed.pop(path, None)
        return
    _executed[path] = (status.st_mtime_ns, status.st_size)


def executed_source_is_current(path: str) -> bool:
    """path の file が今も、この process の読みの口がその module の本体を最後に実行した時の状態(更新時刻 ns・大きさ)か(読めない
    file は偽)。読みの口を通らずに読み込まれた module(包む前の起動・拡張の module)は確かめる材料が無いので真。読み込み 1 つの
    写し(照らしの覚えの確かめ)でなく今の disk を stat する — 照らしの歩みは続けて今の disk から文と sha256 を読むので、同じ時点で
    比べる(歩み 1 回に file 数個 — 照らしごとではない)。"""
    executed = _executed.get(path)
    if executed is None:
        return True
    try:
        status = os.stat(path)
    except OSError:
        return False
    return (status.st_mtime_ns, status.st_size) == executed


# 戻り値の型は内側の関数の推論に任せる(上と同じ理由)。
def _checking_get_code(previous: GetCode):
    """読みの口の包み — .pyc から読んだ Hy の module の展開が古い macro に依っていれば compile し直すため。"""

    def get_code(self: importlib.machinery.SourceFileLoader, fullname: str) -> CodeType | None:
        path = self.get_filename(fullname)
        if not is_hy_source(path):
            return previous(self, fullname)
        _remember_dataclass_code()  # Hy の import の後なら dataclasses は読み込み済み
        try:
            code = _hy_code(self, fullname, path)
        finally:
            _last_sources().pop(path, None)  # 覚えた source はこの読みの間だけ使う
        record = _remember_record(path, code)
        if code is not None and record is not None:
            # macro を require し忘れた・import で入れた module に、書き足す 1 行を言う(実行の前 — 文は Python が出し、
            # その frame に doeff の code が無い・card ki-909e814cc835)。.pyc・保存先から読んだ code にも同じ記録が載る。
            from doeff_hy_bytecode_guard import _macro_hints as macro_hints  # Hy の source に当たった時だけ読む

            macro_hints.warn_about_macro_use(code, record, path, fullname)
        return code

    def _hy_code(self: importlib.machinery.SourceFileLoader, fullname: str, path: str) -> CodeType | None:
        """Hy の source の code — 保存先・.pyc・compile のどれかから、展開が今の macro に依る物を返すため。"""
        header = _bytecode_header(self, path)
        shared = _from_shared_store(self, fullname, path, header)
        if shared is not None:
            return shared  # 別の作業木で作った同じ中身の code — 記録は今の環境の macro の file で確かめた
        counts = _counts()
        before = counts.get(path, 0)
        if _bytecode_matches_source(self, path, header):
            # 作業木の .pyc が source に合う — 標準の口は .pyc を読むだけで compile しないので、展開の module を引かない(引き方は
            # 読み込み済みの module 全部を歩く。Hy の module ごとに引くと、検の読み込みで CPU 約 0.6 秒の固定費になった —
            # card ki-e77d44aa61e5)。.pyc が壊れていて compile した時は記録が無いので、下の照らしが囲みの中で compile し直す。
            code = previous(self, fullname)
        else:
            # compile し直す時(.pyc が無い・古い)に、展開が使った macro を拾う(compile の口の包みが記録を作る)。
            with _expansion_recording(self, path):
                code = previous(self, fullname)
            if code is None or counts.get(path, 0) != before:
                if code is not None:
                    _to_shared_store(fullname, path, _compiled_source(path), code)
                return code  # 今 compile した物 — 依った macro は今の file
        if code is None:
            return None
        from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

        if _record_is_current_here(code):
            return code
        if header is not None and not records.python_checks_source(
            header, _check_hash_based_pycs()
        ):
            # Python が source と突き合わせない .pyc(PEP 552 の unchecked-hash — image の組み立てが焼く形)は、
            # Python が source の変更を信じないのと同じく、macro の変更も信じない(組み立ての中で焼き直す前提)。
            return code
        return _replacing_stale_pyc(self, fullname, path, header)

    return get_code


def _replacing_stale_pyc(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType:
    """記録が古い .pyc の代わりの code — 保存先に今の環境の macro で合う entry が在ればそれを使い(compile せず、.pyc を書き直す)、
    無ければ compile し直して保存先へ足す(card ki-11a90ffff495)。"""
    stored = _from_shared_store_over_stale_pyc(self, fullname, path, header)
    if stored is not None:
        return stored
    with _expansion_recording(self, path):
        recompiled = _recompile(self, path, header)
    _to_shared_store(fullname, path, _compiled_source(path), recompiled)
    return recompiled


def _remember_record(path: str, code: CodeType | None) -> "MacroRecord | None":
    """読みの口が返した code に載った記録を覚える(:func:`loaded_record`)— 記録の無い code(module の外の compile・型検査の展開)
    なら前の覚えを消す(別の code の記録を残さない)。覚えた記録(無ければ None)を返す。"""
    from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

    record = None if code is None else records.record_of(code)
    if record is None:
        _loaded_records.pop(path, None)
    else:
        _loaded_records[path] = record
    return record


def _record_is_current_here(code: CodeType) -> bool:
    """code に載った記録が、今の Hy の版と今の環境の macro の file に合うか(記録が無ければ偽・照らし方は
    record_is_current_here)。"""
    from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

    record = records.record_of(code)
    return record is not None and record_is_current_here(record)


def bytecode_is_current(path: str, pyc: bytes, source: bytes) -> bool:
    """木に既に在る .pyc の中身 pyc を、今の source と今の環境の macro にそのまま使えるか — bytecode を前もって作る道具が、前の木から
    引き継いだ .pyc を焼き直さずに残すかを決めるため(agora-redesign #2598)。

    使えるのは、頭に source の hash を持つ PEP 552 の hash 方式の .pyc で、その hash が今の source と同じ物だけ。Hy の source の
    .pyc は、さらに記録が今の環境の macro に合うこと(読みの口と同じ照らし方)。前もって作る道具がここで焼き直さないと、
    macro の変わった版の初回の import が、引き継いだ Hy の module を全部 compile し直す。"""
    from doeff_hy_bytecode_guard import _records as records  # 道具が引き継いだ .pyc に当たった時だけ読む

    if len(pyc) < records.PYC_HEADER_BYTES or pyc[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(pyc[4:8], "little")
    if not flags & records.FLAG_HASH_BASED or pyc[8:16] != importlib.util.source_hash(source):
        return False
    if not is_hy_source(path):
        return True
    import marshal  # Hy の .pyc に当たった時だけ読む

    try:
        code = marshal.loads(pyc[records.PYC_HEADER_BYTES :])
    except (EOFError, ValueError, TypeError):
        return False  # 中身の壊れた .pyc は使えない(焼き直す)
    return isinstance(code, CodeType) and _record_is_current_here(code)


def _store_key(fullname: str, path: str, source: bytes) -> str:
    """source の中身から決まる code の鍵(鍵と並びの定義点は code_store の 1 つ — 鍵 1 つに macro の環境ごとの entry が並ぶ)。"""
    from doeff_hy_bytecode_guard import _code_store as code_store  # 保存先を使う時だけ読む

    return code_store.code_key(
        path, source, fullname, _hy_version(), sys.implementation.cache_tag or "", sys.flags.optimize
    )


def _from_shared_store(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType | None:
    """作業木の .pyc が使えない時に、別の作業木で作った同じ中身の code を保存先(code_store — 作業木・版をまたいで中身で引く)から
    引く(当たらなければ None)— 新しい作業木の .pyc は source の絶対 path に結びつくので必ず冷え、同じ中身の file の変換(macro の
    展開)をやり直していた(agora-redesign #1753)。

    鍵の entry(macro の環境ごとに並ぶ — doeff の版の違う書き手の物も)の記録を、使った macro を module 名から今の環境で引き直して
    照らし、合う物を使う(記録は file の path を持たないので、そのまま作業木の .pyc に書ける)。作業木の .pyc も標準の timestamp の形で
    書く。"""
    from doeff_hy_bytecode_guard import _code_store as code_store  # 保存先を使う時だけ読む

    store = code_store.store_dir()
    if store is None:
        return None
    from doeff_hy_bytecode_guard import _records as records  # 保存先を使う時だけ読む

    try:
        stats = self.path_stats(path)
    except OSError:
        return None
    mtime, size = int(stats["mtime"]), int(stats["size"])
    if header is not None and (
        records.timestamp_header_matches(header, mtime=mtime, size=size)
        or int.from_bytes(header[4:8], "little") & records.FLAG_HASH_BASED
    ):
        return None  # 作業木の .pyc が使える(か hash の方式 — Python 自身の判定に任せる)
    return _stored_code(self, fullname, path, mtime=mtime, size=size, previous_header=None)


def _from_shared_store_over_stale_pyc(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType | None:
    """作業木の .pyc は source に合うが、記録が今の環境の macro に合わない時(兄弟の doeff を替えた後に使い回す木)に、compile し直す前に
    保存先を引く(当たらなければ None で、呼び手が今どおり compile して足す)— 保存先に今の環境で合う entry が在るのに、.pyc が在るという
    だけで引かずに木の Hy の module を全部 compile し直していた(card ki-11a90ffff495)。照らしは :func:`_from_shared_store` と同じ。
    作業木の .pyc は読んだ .pyc と同じ方式(timestamp か hash)で書き直す。"""
    from doeff_hy_bytecode_guard import _code_store as code_store  # 保存先を使う時だけ読む

    if code_store.store_dir() is None:
        return None
    try:
        stats = self.path_stats(path)
    except OSError:
        return None
    return _stored_code(
        self, fullname, path, mtime=int(stats["mtime"]), size=int(stats["size"]), previous_header=header
    )


def _stored_code(
    self: importlib.machinery.SourceFileLoader,
    fullname: str,
    path: str,
    *,
    mtime: int,
    size: int,
    previous_header: bytes | None,
) -> CodeType | None:
    """保存先の鍵の entry のうち、記録が今の環境の macro に合う物を使う(無ければ None)。code の file 名を今の source の path に直し、
    作業木の .pyc を previous_header の方式(None なら標準の timestamp)で書く(bytecode を書かない設定では書かない)。"""
    from doeff_hy_bytecode_guard import _code_store as code_store, _records as records  # 保存先を使う時だけ読む

    store = code_store.store_dir()
    if store is None:
        return None
    try:
        source = self.get_data(path)
    except OSError:
        return None  # source を読めなければ標準の get_code が同じ誤りを名乗る
    code = code_store.current_code(store, _store_key(fullname, path, source), _record_is_current_here)
    if code is None:
        return None
    import _imp  # 標準の _compile_bytecode と同じ口で、code の file 名を今の source の path に直す

    _imp._fix_co_filename(code, path)
    if not sys.dont_write_bytecode:
        import contextlib

        data = records.pyc_bytes(code, source=source, mtime=mtime, size=size, previous_header=previous_header)
        with contextlib.suppress(OSError):
            self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _compiled_source(path: str) -> bytes | None:
    """今 compile した Hy の source の bytes — この thread で compile の直前に読んだ中身(読みの口が覚えた物)。読みの口を
    通らずに compile した時(覚えが無い)は None で、保存先には足さない(鍵の bytes が compile した物と同じとは言えない)。"""
    data = _last_sources().get(path)
    return data if isinstance(data, bytes) else None


def _to_shared_store(fullname: str, path: str, source: bytes | None, code: CodeType) -> None:
    """今 compile した Hy の code を、compile した source の bytes を鍵にして保存先(code_store)へ足す(記録の無い code は足さない。
    bytecode を書かない設定では、保存先を名指した時だけ足す — 書かない設定は木への書きの決めで、名指しの保存先は木の外・
    agora-redesign #3737)— 次に同じ中身の file を別の作業木で読む時に、変換をやり直さないため。鍵は compile した bytes
    からだけ作り、file を読み直さない(読み直すと、間に書き換わった file の中身の鍵に古い code が入る — agora-redesign #2799)。
    書けない保存先では足さない — import の口は venv のすべての Python の起動で走るので、書けない理由を import のたびに出さない
    (保存先は速さのためだけ・worker の道具は同じ書きの理由を名指して出す)。"""
    from doeff_hy_bytecode_guard import _code_store as code_store  # 保存先を使う時だけ読む

    store = code_store.store_dir()
    if store is None or source is None or (sys.dont_write_bytecode and not code_store.store_is_named()):
        return
    from doeff_hy_bytecode_guard import _records as records  # 保存先を使う時だけ読む

    if records.record_of(code) is None:
        return
    code_store.add_code(store, _store_key(fullname, path, source), code)


def _recompile(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> CodeType:
    """古い展開の代わりに source から compile し直し(記録は compile の口の包みが足す)、読んだ .pyc と同じ方式で書き直す。"""
    from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

    source = self.get_data(path)
    code = self.source_to_code(source, path)
    if sys.dont_write_bytecode:
        return code
    stats = self.path_stats(path)
    data = records.pyc_bytes(
        code,
        source=source,
        mtime=int(stats["mtime"]),
        size=int(stats["size"]),
        previous_header=header,
    )
    import contextlib  # 古い記録に当たった時だけ読む

    # 標準の get_code と同じく、書けない場所(読み取り専用の木)では書かずに compile した code を使う。
    with contextlib.suppress(OSError):
        self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _bytecode_header(self: importlib.machinery.SourceFileLoader, path: str) -> bytes | None:
    """source の隣の .pyc の頭(無ければ None)— 方式(timestamp / hash・検めるか)を見て、同じ方式で書き直すため。"""
    try:
        data = self.get_data(importlib.util.cache_from_source(path))
    except OSError:
        return None
    from doeff_hy_bytecode_guard import _records as records  # 古い記録に当たった時だけ呼ばれる

    return data[: records.PYC_HEADER_BYTES]


def _bytecode_matches_source(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> bool:
    """作業木の .pyc(頭 header)が、標準の timestamp の形で今の source の更新時刻と大きさに合うか — 合えば標準の get_code は
    .pyc を読むだけで compile しない。hash の形は Python 自身の判定に任せ、合うとしない(compile し得る側に数える)。"""
    if header is None or header[:4] != importlib.util.MAGIC_NUMBER:
        return False
    from doeff_hy_bytecode_guard import _records as records  # Hy の source に当たった時だけ読む

    try:
        stats = self.path_stats(path)
    except OSError:
        return False
    return records.timestamp_header_matches(header, mtime=int(stats["mtime"]), size=int(stats["size"]))


def _expansion_recording(
    self: importlib.machinery.SourceFileLoader, path: str
) -> "contextlib.AbstractContextManager[object]":
    """compile し得る読みを囲む口 — 展開が走る module(:func:`_module_being_loaded`)の表で、展開が使った macro を拾う。module は
    compile し得る時にだけ引く(引き方は読み込み済みの module 全部を歩く)。

    module が無いのは、import を通らず読みの口が呼ばれた時(runpy の ``hy <script>``・``hy -m`` は loader の get_code を、module を
    ``sys.modules`` に置く前に呼ぶ)。その名の module を置いた中で拾う(:class:`_PlacedExpansion`)— 拾わないと、Hy は仮の module
    で compile して compile の直後に消すので、記録の無い .pyc が 毎回「古い物」として compile し直され、保存先にも入らない。
    同じ名を別の file の module が使っていれば(``hy json.hy`` — Hy 自身も既存の module の ``__file__`` を書き換えて消す形)置かず、
    何も拾わない。"""
    import contextlib  # compile し得る時だけ読む

    module = _module_being_loaded(self, path)
    if module is not None:
        return macro_recording(module)
    if self.name in sys.modules:
        return contextlib.nullcontext()
    return _PlacedExpansion(self, path)


def _module_being_loaded(
    self: importlib.machinery.SourceFileLoader, path: str
) -> ModuleType | None:
    """Hy の compile の展開が走る module — compile の中の require がここの表へ macro を載せ、展開がここの表を引くので、記録の元になる。

    引き方は Hy の compile の口(``hy.importer.loader_module_obj``)と同じ: file の名から読み込み済みの module を引き
    (``inspect.getmodule`` — 同じ file を別の名で先に読んだ module があればそれ)、無ければ import の途中の module
    (``sys.modules`` に先に置かれている ``self.name``)。``self.name`` だけで引くと、同じ file を package の名で読んだ process で
    path から別の名で読み直した時(worker の bytecode の道具の bake_plan.hy)、展開が引かない表から空の記録を作り、照らしが
    素通りの entry を共有の置き場に入れた(2026-10-11 atlas・card ki-9180656459ac)。"""
    import inspect  # Hy の source に当たった時だけ読む(Hy の compile の口も読む)

    try:
        module = inspect.getmodule(None, _filename=self.path)
    except KeyError:  # Hy の口と同じ — 引く途中で sys.modules から消えた module は無いのと同じ
        module = None
    if module is None:
        module = sys.modules.get(self.name)
    if module is None or vars(module).get("__file__") != path:
        return None
    return module


def _expansion_or(
    loader: importlib.machinery.SourceFileLoader, path: str, placed: ModuleType
) -> ModuleType:
    """import の外の compile で展開が走る module — 置いた module placed より、Hy が file の名から引く読み込み済みの module
    (:func:`_module_being_loaded`)が先。記録の囲みを、展開が引く表の module に開くため。"""
    return _module_being_loaded(loader, path) or placed


def _last_sources() -> dict[str, bytes]:
    """この thread で最後に読んだ Hy の source の表(初めてなら作る)。"""
    import threading  # 起動時に読まない

    local = _source_reads.get("local")
    if not isinstance(local, threading.local):
        local = _source_reads.setdefault("local", threading.local())
    return vars(local).setdefault("sources", {})


def _counts() -> dict[str, int]:
    """この thread の compile の回数の表(初めてなら作る)。"""
    import threading  # 起動時に読まない

    local = _compile_counts.get("local")
    if not isinstance(local, threading.local):
        local = _compile_counts.setdefault("local", threading.local())
    return vars(local).setdefault("counts", {})


def _hy_version() -> str:
    """読み込み済みの Hy の版 — Hy を上げたら全部の Hy の module を compile し直すため(Hy が無ければ空)。"""
    hy = sys.modules.get("hy")
    version = None if hy is None else vars(hy).get("__version__")
    return version if isinstance(version, str) else ""


def _check_hash_based_pycs() -> str:
    """``--check-hash-based-pycs`` の値 — 標準の get_code が hash 方式の .pyc を検めるかを決める値を同じ所から読むため。"""
    import _imp  # この値の唯一の定義元(標準の importlib._bootstrap_external も同じ所を読む)

    return _imp.check_hash_based_pycs
