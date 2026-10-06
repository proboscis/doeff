"""``SourceFileLoader`` の 2 つの口の包み(file の読み書きをするのはこの module だけ)。

起動時に読まれるので、import は標準 library の軽い物に限る。記録の形と照合の判断は
:mod:`doeff_hy_bytecode_guard.records`(純関数)にあり、Hy の source に初めて当たった時に import する。

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
from doeff_hy_bytecode_guard.expansion import TYPE_CHECK_EXPANSION

TYPE_CHECKING = False  # typing を起動時に読まない(2 ms)— 型検査器はこの名の分岐を真として読む

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard.records import MacroDependency, MacroRecord

#: Hy の source の拡張子(doeff-hy は .hyk・.hyp も Hy として読ませる — doeff_hy/__init__.py)。定義点は保存先の module(code の鍵が
#: Hy の source かで欄を分ける)— 標準 library だけを読む軽い module なので起動時に読んでよい。
from doeff_hy_bytecode_guard.code_store import HY_SOURCE_SUFFIXES as HY_SOURCE_SUFFIXES

GetCode = Callable[[importlib.machinery.SourceFileLoader, str], "CodeType | None"]
GetData = Callable[[importlib.machinery.SourceFileLoader, str], bytes]
SourceToCode = Callable[..., CodeType]

#: 包んだかどうか(process に 1 つ)。包みは process の ``SourceFileLoader`` という 1 つの共有物を書き換えるので、
#: 持ち主はこの module 1 つ。
#: .pth(起動時の 1 thread)と ``import doeff_hy``(import の lock の中)からしか呼ばれないので lock は持たない。
_installation = {"installed": False}

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


def installed() -> bool:
    """この process の ``SourceFileLoader`` が包まれているか(起動時の .pth が効いたかを検査が確かめるため)。"""
    return _installation["installed"]


def install() -> None:
    """macro が変わった Hy の module を compile し直すよう ``SourceFileLoader`` の 2 つの口を包む(何度呼んでも 1 度だけ)。"""
    if _installation["installed"]:
        return
    loader = importlib.machinery.SourceFileLoader
    loader.source_to_code = _recording_source_to_code(loader.source_to_code)
    loader.get_code = _checking_get_code(loader.get_code)
    loader.get_data = _remembering_get_data(loader.get_data)
    _installation["installed"] = True


def is_hy_source(path: object) -> bool:
    """包みが相手にする source か(Hy の拡張子の文字列の path だけ — それ以外は 1 つ前の関数へそのまま渡す)。"""
    return isinstance(path, str) and path.endswith(HY_SOURCE_SUFFIXES)


def macro_dependencies(
    module: ModuleType, path: str, also: "tuple[ModuleType, ...]" = ()
) -> "list[MacroDependency]":
    """読み込み済みの Hy の module の展開が依った macro の file と今の sha256 — 記録を作る口と、索引のキャッシュの鍵
    (agora-redesign #1291)が同じ 1 つの辿り方を使うための公開の口。``also`` は ``records.macro_provider_files`` の同名の引数。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    return [
        # 読めない file は、どの sha256 とも合わない印で記録する — 記録から外すと、その file の変更を見落とす。
        records.MacroDependency(name, file, file_sha256(file) or records.UNREADABLE)
        for name, file in records.macro_provider_files(module, path, sys.modules, also).items()
    ]


def current_record(
    module: ModuleType, path: str, also: "tuple[ModuleType, ...]" = ()
) -> "MacroRecord":
    """Hy の module(path の file を展開した物)の展開が依った物の記録 — 今の Hy の版と、macro の提供元の file と今の sha256。
    compile の口が code に足す記録と、展開した木の cache(doeff-effect-analyzer — agora-redesign #3598)と、型検査の展開の
    保存(doeff_hy.static_cache — agora-redesign #3862)が同じ 1 つの作り方を使うための公開の口。``also`` は
    ``records.macro_provider_files`` の同名の引数(macro の外で展開の結果を変える module)。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    return records.MacroRecord(_hy_version(), tuple(macro_dependencies(module, path, also)))


def gensym_renaming(names: "Iterable[str]") -> "Callable[[str], str]":
    """名の並びに現れる Hy の gensym の名を、数えの小さい順に 1 から振り直す関数(他の名はそのまま返す)— code の木の正準化
    (``records.canonical_gensyms``)と同じ規則を、型検査の展開の木(doeff_hy.static_check — agora-redesign #3869)にも当てる
    公開の口。展開の文が、同じ process で先に何を展開したか・どの process で展開したかに依らなくなる。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    renames = records.gensym_renames(names)
    return lambda name: records.renamed(name, renames)


def record_from_rows(hy_version: str, rows: "tuple[tuple[str, str, str], ...]") -> "MacroRecord":
    """保存した記録の行(module 名・file・sha256)から記録を組み直す — 記録を file に書いて読み戻す保存(型検査の展開の
    保存 doeff_hy.static_cache — agora-redesign #3862)が、記録の形の持ち主(records)を直に import しないための公開の口。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    return records.MacroRecord(hy_version, tuple(records.MacroDependency(*row) for row in rows))


def record_is_current_here(record: "MacroRecord") -> bool:
    """記録が、今の Hy の版と今の環境の macro の file に合うか。

    記録の path は作った木の絶対 path。別の木で作った .pyc(実行環境の準備が前の root から hardlink で引き継ぐ物)の記録を
    そのまま照らすと、作った木の macro が残っている限り、今の木の macro が変わっても古い展開を使う(agora-redesign #2598)。
    保存先の code と同じく、提供元の file を module 名から今の環境で引き直して照らす。.pyc の記録と、展開した木の
    cache(doeff-effect-analyzer — agora-redesign #3598)の記録が同じ 1 つの照らし方を使うための公開の口。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    current = records.rebased_record(record, _current_file_of)
    return current is not None and records.record_is_current(current, _hy_version(), file_sha256)


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
    name = loader.name
    present = sys.modules.get(name)
    if present is not None and vars(present).get("__file__") == path:
        return loader.source_to_code(data, path)  # 読み込み済みの module の中で compile する(import の途中と同じ)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    if spec is None:
        raise ImportError(f"{path} の module の spec を作れない", name=name, path=path)
    sys.modules[name] = importlib.util.module_from_spec(spec)
    try:
        return loader.source_to_code(data, path)
    finally:
        if present is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = present


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
        from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

        # Hy の source の compile は import・作り直し・前もって作る道具(source_to_code_as_import)・image の組み立て(source_to_code を
        # 直に呼ぶ)のどれもこの口を通る — Hy の口の上でも下でも、ここが受けるのは compile の済んだ code。gensym の名を module の
        # 中の順の通し番号にし、compile の順・process・thread に依らない code にする(agora-redesign #3667)。
        code = records.canonical_gensyms(code)
        counts = _counts()
        counts[path] = counts.get(path, 0) + 1
        module = _module_being_loaded(self, path)
        if module is None:
            # module の外での compile(py_compile・image の組み立ての warm・runpy)— 何の macro を require したかが
            # 見えないので記録を足さない。記録の無い bytecode は、Python が source と突き合わせる形なら次の読みで
            # compile し直される(get_code)。突き合わせない形(image の hash 方式)はそのまま信じる。bytecode を前もって
            # 作る道具は source_to_code_as_import で module の中で compile し、記録を付ける。
            return code
        if TYPE_CHECK_EXPANSION.get():
            # 型検査のための展開(doeff_hy.static_view)の code は実行できない(:pre の isinstance と実行の時の import が無い)—
            # 記録を付けない。記録の無い code は保存先に入らず(_to_shared_store)、.pyc に書かれても次の普通の import が
            # compile し直す(_record_is_current_here)。付けると普通の展開と同じ鍵で残り、普通の import が読んで落ちた(I-3)。
            return code
        return records.with_record(code, current_record(module, path))

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
def _checking_get_code(previous: GetCode):
    """読みの口の包み — .pyc から読んだ Hy の module の展開が古い macro に依っていれば compile し直すため。"""

    def get_code(self: importlib.machinery.SourceFileLoader, fullname: str) -> CodeType | None:
        path = self.get_filename(fullname)
        if not is_hy_source(path):
            return previous(self, fullname)
        try:
            return _hy_code(self, fullname, path)
        finally:
            _last_sources().pop(path, None)  # 覚えた source はこの読みの間だけ使う

    def _hy_code(self: importlib.machinery.SourceFileLoader, fullname: str, path: str) -> CodeType | None:
        """Hy の source の code — 保存先・.pyc・compile のどれかから、展開が今の macro に依る物を返すため。"""
        shared = _from_shared_store(self, fullname, path)
        if shared is not None:
            return shared  # 別の作業木で作った同じ中身の code — 記録は今の環境の macro の file で確かめた
        counts = _counts()
        before = counts.get(path, 0)
        code = previous(self, fullname)
        if code is None or counts.get(path, 0) != before:
            if code is not None:
                _to_shared_store(fullname, path, _compiled_source(path), code)
            return code  # 今 compile した物 — 依った macro は今の file
        from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

        if _record_is_current_here(code):
            return code
        header = _bytecode_header(self, path)
        if header is not None and not records.python_checks_source(
            header, _check_hash_based_pycs()
        ):
            # Python が source と突き合わせない .pyc(PEP 552 の unchecked-hash — image の組み立てが焼く形)は、
            # Python が source の変更を信じないのと同じく、macro の変更も信じない(組み立ての中で焼き直す前提)。
            return code
        recompiled = _recompile(self, path, header)
        _to_shared_store(fullname, path, _compiled_source(path), recompiled)
        return recompiled

    return get_code


def _record_is_current_here(code: CodeType) -> bool:
    """code に載った記録が、今の Hy の版と今の環境の macro の file に合うか(記録が無ければ偽・照らし方は
    record_is_current_here)。"""
    from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

    record = records.record_of(code)
    return record is not None and record_is_current_here(record)


def bytecode_is_current(path: str, pyc: bytes, source: bytes) -> bool:
    """木に既に在る .pyc の中身 pyc を、今の source と今の環境の macro にそのまま使えるか — bytecode を前もって作る道具が、前の木から
    引き継いだ .pyc を焼き直さずに残すかを決めるため(agora-redesign #2598)。

    使えるのは、頭に source の hash を持つ PEP 552 の hash 方式の .pyc で、その hash が今の source と同じ物だけ。Hy の source の
    .pyc は、さらに記録が今の環境の macro に合うこと(読みの口と同じ照らし方)。前もって作る道具がここで焼き直さないと、
    macro の変わった版の初回の import が、引き継いだ Hy の module を全部 compile し直す。"""
    from doeff_hy_bytecode_guard import records  # 道具が引き継いだ .pyc に当たった時だけ読む

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


def _store_entry(store: str, fullname: str, path: str, source: bytes) -> str:
    """source の中身から決まる code の entry の path(鍵と並びの定義点は code_store の 1 つ)。"""
    from doeff_hy_bytecode_guard import code_store  # 保存先を使う時だけ読む

    key = code_store.code_key(
        path, source, fullname, _hy_version(), sys.implementation.cache_tag or "", sys.flags.optimize
    )
    return code_store.entry_path(store, key, code_store.CODE_SUFFIX)


def _from_shared_store(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str
) -> CodeType | None:
    """作業木の .pyc が使えない時に、別の作業木で作った同じ中身の code を保存先(code_store — 作業木・版をまたいで中身で引く)から
    引く(当たらなければ None)— 新しい作業木の .pyc は source の絶対 path に結びつくので必ず冷え、同じ中身の file の変換(macro の
    展開)をやり直していた(agora-redesign #1753)。

    当たった entry の記録は、提供元の file を module 名から今の環境で引き直して照らし、記録もその path に付け替える
    (付け替えないと、次からの .pyc の照合が別の作業木の macro を見続ける)。作業木の .pyc も標準の timestamp の形で書く。"""
    from doeff_hy_bytecode_guard import code_store  # 保存先を使う時だけ読む

    store = code_store.store_dir()
    if store is None:
        return None
    from doeff_hy_bytecode_guard import records  # 保存先を使う時だけ読む

    try:
        stats = self.path_stats(path)
    except OSError:
        return None
    mtime, size = int(stats["mtime"]), int(stats["size"])
    header = _bytecode_header(self, path)
    if header is not None and (
        records.timestamp_header_matches(header, mtime=mtime, size=size)
        or int.from_bytes(header[4:8], "little") & records.FLAG_HASH_BASED
    ):
        return None  # 作業木の .pyc が使える(か hash の方式 — Python 自身の判定に任せる)
    try:
        source = self.get_data(path)
    except OSError:
        return None  # source を読めなければ標準の get_code が同じ誤りを名乗る
    code = code_store.stored_code(_store_entry(store, fullname, path, source))
    if code is None:
        return None
    record = records.record_of(code)
    if record is None:
        return None
    rebased = records.rebased_record(record, _current_file_of)
    if rebased is None or not records.record_is_current(rebased, _hy_version(), file_sha256):
        return None
    code = records.with_record(code, rebased)
    import _imp  # 標準の _compile_bytecode と同じ口で、code の file 名を今の source の path に直す

    _imp._fix_co_filename(code, path)
    if not sys.dont_write_bytecode:
        import contextlib

        data = records.pyc_bytes(code, source=source, mtime=mtime, size=size, previous_header=None)
        with contextlib.suppress(OSError):
            self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _compiled_source(path: str) -> bytes | None:
    """今 compile した Hy の source の bytes — この thread で compile の直前に読んだ中身(読みの口が覚えた物)。読みの口を
    通らずに compile した時(覚えが無い)は None で、保存先には足さない(鍵の bytes が compile した物と同じとは言えない)。"""
    data = _last_sources().get(path)
    return data if isinstance(data, bytes) else None


def _to_shared_store(fullname: str, path: str, source: bytes | None, code: CodeType) -> None:
    """今 compile した Hy の code を、compile した source の bytes を鍵にして保存先(code_store)へ足す(記録の無い code・bytecode を
    書かない設定では足さない)— 次に同じ中身の file を別の作業木で読む時に、変換をやり直さないため。鍵は compile した bytes
    からだけ作り、file を読み直さない(読み直すと、間に書き換わった file の中身の鍵に古い code が入る — agora-redesign #2799)。
    書けない保存先では足さない — import の口は venv のすべての Python の起動で走るので、書けない理由を import のたびに出さない
    (保存先は速さのためだけ・worker の道具は同じ書きの理由を名指して出す)。"""
    from doeff_hy_bytecode_guard import code_store  # 保存先を使う時だけ読む

    store = code_store.store_dir()
    if store is None or source is None or sys.dont_write_bytecode:
        return
    from doeff_hy_bytecode_guard import records  # 保存先を使う時だけ読む

    if records.record_of(code) is None:
        return
    import marshal

    code_store.write_entry(_store_entry(store, fullname, path, source), marshal.dumps(code))


def _current_file_of(module_name: str) -> str | None:
    """module 名の今の環境の file(読み込み済みなら sys.modules・未だなら import の探索 — 見つからなければ None)。"""
    module = sys.modules.get(module_name)
    if module is not None:
        file = vars(module).get("__file__")
        return file if isinstance(file, str) else None
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return None
    origin = None if spec is None else spec.origin
    return origin if isinstance(origin, str) else None


def _recompile(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> CodeType:
    """古い展開の代わりに source から compile し直し(記録は compile の口の包みが足す)、読んだ .pyc と同じ方式で書き直す。"""
    from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

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
    from doeff_hy_bytecode_guard import records  # 古い記録に当たった時だけ呼ばれる

    return data[: records.PYC_HEADER_BYTES]


def _module_being_loaded(
    self: importlib.machinery.SourceFileLoader, path: str
) -> ModuleType | None:
    """import の途中の module(``sys.modules`` に先に置かれている)— compile の中の require がここへ macro を載せるので、記録の元になる。"""
    module = sys.modules.get(self.name)
    if module is None or vars(module).get("__file__") != path:
        return None
    return module


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
