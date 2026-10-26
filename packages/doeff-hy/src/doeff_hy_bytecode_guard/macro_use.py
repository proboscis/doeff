"""展開が使った macro の記録の作り方と照らし方(:class:`doeff_hy_bytecode_guard.records.MacroRecord`)。

使った macro を拾う: 展開(compile)の間だけ、展開する module の macro の表(``_hy_macros``)を :class:`RecordingTable`(引いた名を
覚える dict)に替える。Hy 1.3.1 の ``hy.macros.macroexpand`` は展開する module の表を ``名 in 表`` で確かめ ``表[名]`` で引くので、
引かれた名が使った macro、表に無かった名が「引いて無かった名」になる。macro が展開した先で別の macro を呼んでも同じ表から
引かれる。関数の中の require の macro は、Hy の展開が module の表より先に引く局所の表(compiler の局所の状態)に入るので、
compiler が局所の状態を開く口を包んで局所の表を覚え(:func:`_watch_local_tables`)、その中の展開で実行された macro を使った
macro に入れる。

macro の digest = macro の関数の code の閉包の digest(:class:`_Walker`):
- code object の中身(命令・定数 — 入れ子の code は同じ規則で・名の表・引数の数・flags)。行番号と位置(co_firstlineno・
  co_linetable・co_positions)は入れない — macro の module に行を足しても、他の macro の digest は変わらない。
- 名で引く大域(code の名の表を関数の ``__globals__`` で引いた値)・既定値・閉包の cell を辿る。関数と class(class はその
  method)は同じ規則で再帰に辿る。素の値と、中身が素の値の組・集合は値で入れる。module は名と、code の名の表に出る属性
  (``module.名`` の読み)を入れる。code の文字列の定数は名の候補として、辿った module のその名の属性も入れる
  (``getattr(module, "名")`` の読み — 読む関数と module の参照が別の関数にあっても覆う)。ただし module の説明の文
  (``__doc__``)は入れない(展開の code の "__doc__" は展開の結果に説明を書く印で、module の説明の文は展開の結果に効かない)。
  module の置き場を名指すだけの属性(``__file__``・``__cached__`` — :data:`_LOCATION_NAMES`)は、名で出会っても値(作業木の
  置き場の path)を入れず印だけを入れる(同じ中身の木を別の dir に置いても digest は変わらない — card ki-1475765a3f18)。
  置き場を値として読む code か束縛の文(``Path(__file__).with_name(…)`` で隣の file を読む形)の digest は、その file の中身を
  覆えないので照らせない印にする(環境変数の読みと同じ :data:`doeff_hy_bytecode_guard.records.ENVIRONMENT_READ`)。
- 関数の code の中身は、その module の展開で実行された関数だけを覆う(agora-redesign #3769 の子 — defk の閉包の歩みが、defk から
  届く class(binding_forms._Walker)を method ごと全部辿り、展開で呼ばれない _assign の code の変更で、defk の使い手 1,197 本が
  作り直された)。歩みは今までどおり全部を辿り(関数の名・class の member の名・値・束縛の文・関数の大域と import の先)、関数に
  初めて出会った所の code の中身(:attr:`CodeFacts.shape`)だけを、記録が覆うと名指した関数の物にし、他は印 :data:`_UNCOVERED`
  にする(:class:`ClosureWalk`)。覆う関数 = 展開の間(:class:`MacroRecording` の ``with`` の中)に実行の始まった code の関数
  (:mod:`doeff_hy_bytecode_guard.called_code` が拾う)と、絞らない関数。記録は覆った関数を、歩みが関数に出会った順の番号の bit
  (:attr:`doeff_hy_bytecode_guard.records.UsedMacro.covered`)で持ち、照らしは同じ印で今の閉包の digest を作り直して比べる。
  実行されなかった関数の code は展開の結果を変えないので、中身を外しても照らしは古い展開を合うと判じない — 実行された関数の
  中身と、それが読む値が記録と同じなら、展開は同じ道を通る(呼ぶ関数を変えるには、実行された関数の中身か読む値が変わる)。
  絞らない関数:
  - 束縛の文の先(:meth:`_Walker._bound` の中で出会った関数と class と、そこから届く物)— 値を作った code は展開の外(読み込みの
    時・前の展開)で実行され、その中身が値を覆う(書き換える関数・束縛の文が呼ぶ関数)。2 度目に出会った物(番号だけ入る)も
    絞らない — 関数と class ごとに、中身を歩く間に出会った物を覚えて辿る。
  - 関数の中身を値として読む code(:data:`_CODE_READ_NAMES` — ``__doc__`` ・ ``__code__`` ・ ``inspect.signature`` など)を覆う時は、
    閉包の関数の中身を全部覆う(実行しない関数の説明の文・引数の形が展開の結果に効く)。
  - 拾えない環境(Python 3.11 まで・sys.monitoring の道具の番号が空いていない)・``with`` の外で作った記録・macro の外の値の行は全部。
  見ない形: 別の module の大域の表に、外の module の関数が展開をまたいで書き込む覚え(書き手は束縛の文の先にならず、前の展開で
  表を埋めた関数の中身を、今の展開が呼ばなければ覆わない — 同じ module の関数や class が書き込む表・``functools.lru_cache`` で包んだ
  関数は覆う)・文字列で引く ``getattr(f, "__doc__")``(展開の code の文字列の定数 "__doc__" は展開の結果に説明を書く印)・標準
  library と Hy が中で読む関数の中身。囲みの中で macro の module を初めて読むと、読み込みの時に実行された関数も覆うので、同じ
  source の記録の印が process で違いうる(どちらの記録も照らしでは合う)。
- Hy 自身(``hy.*``)と Python の標準 library と組み込みは名だけ入れる(Hy の版・Python の版で覆う)。
- 関数の中の import の先・hy.R の呼び出しの先・package の下の module: import して、名の表の名の属性を入れる(module 全体を
  file 単位で覆うと、defhandler が本体で import する doeff_hy.macros まで覆い、macros.hy のどの行の変更でも冷えた)。照らしは
  .pyc を読む途中(import の途中)に走るので、関数の中の import の先は、展開の時には通らない枝で、循環する import の途中の
  module を require して落ちうる — 落ちた時は名だけを入れる。
- import の途中の状態の digest(agora-redesign card ki-464fa307a62b): 歩みが import の途中の module(名前空間がまだ揃って
  いない)か import できない module に当たった digest は、import の進み具合 — どの process がどの順で何を読んだか — に依る
  (deff の閉包の doeff_hy.outcome_forms は、doeff_hy.record を .pyc から読む途中では import できず、読み込みの済んだ process
  では中身ごと辿られる)。その digest は process の覚えに入れず、記録にも入れない(None)。記録は代わりに、閉包が依った
  module の file の sha256(:attr:`MacroRecord.files`)を持ち、記録か今の歩みのどちらかが import の途中に当たった行は、
  digest でなくその file で比べる。両方が読み込みの済んだ状態なら digest で比べる(file の行は見ない)。
- process をまたぐ覚え(agora-redesign card ki-38508e6e56fd・条は packages/doeff-hy/architecture.hy の B1): digest は process の中で
  だけ覚える(:data:`_REMEMBERED`)。digest は生きた値(別の module から来た素の値・環境変数で決まる値)に依り、辿った file の
  状態だけでは信じられないので、新しい process は閉包を辿り直す。辿り直しの費用の大半だった source の文の読みは、中身で決まる
  純な読みとして置き場に覚える(:mod:`doeff_hy_bytecode_guard.bound_source`)。
- 環境変数の読み(設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 A の付録 3 — 基準版 cf7ea9cc0 から在った・依頼
  lt-D9C8NM324RSPKZZVKC9MHA9464): 標準 library は名だけ入れる(版で覆う)が、``os.environ`` は process の状態で、版も file も
  覆わない。環境変数から組んだ表は名を束縛する source の文で覆われ、文は環境変数の値を持たないので、環境変数だけを替えた
  process が古い展開を今に合うと判じた。歩みが環境変数の読み — code か束縛の文が os(posix・nt)の module を大域の名で引いて
  ``environ`` ・ ``environb`` ・ ``getenv`` ・ ``getenvb`` を読む・関数の中の import の os で読む・それらの object に値として当たる
  (別名の import・既定値・閉包の cell)— に当たった digest は照らせない: 覚えず、記録の行は印
  :data:`doeff_hy_bytecode_guard.records.ENVIRONMENT_READ`(照らしはいつも古いと判じ、使い手は process ごとに展開し直す)。
  環境変数から組んだ素の値の大域(文字列・数)は値で入るので、この規則に当たらない。辿れない値を持ち主の module の file で覆う
  時(既定値・閉包の cell・束縛を辿れない大域・名で辿れない読みの先 — :meth:`_Walker._fold_opaque`)は、持ち主の source に
  環境変数を持つ module の名と環境変数を読む属性の名の両方が現れれば印にする — file は読み込みの時に読んだ環境変数を写さない
  (設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464 の盲検 A)。見ない読み: 標準 library の関数が中で読む環境変数(``os.path.expanduser`` ・
  ``tempfile`` など)・文字列で引く読み(``getattr(os, "environ")``)・module の表を通す読み(``sys.modules["os"]`` ・
  ``hy.I.os``)・星の import で束縛した表(持ち主の file で覆い、import の先の source は読まない)。
- enum と namedtuple が class の定義から作る表(``_member_map_`` など)は入れない(member と ``__new__`` の既定値として辿る)。
- 辿れない値を狭く覆う(agora-redesign #3938):
  - module の大域の名で引いた辿れない値(list・dict・上のどれでもない object)は、その名を束縛し書き換える source の文で覆う
    (:mod:`doeff_hy_bytecode_guard.bound_source` — 文が読む名と、本体で書き換えうる top-level の関数も辿る)。
  - frozen の dataclass の値は class と欄の値で、ContextVar は名と既定値で入れる(今の値は実行の状態なので入れない)。
  - 拡張の module の class(Python の source の無い class)は、.so の file ではなく名と公開の形(基底・属性の名と種類)で覆う。
  - 関数の kwdefaults(名 → 既定値の dict)は名の順に値で入れる。
- 安全側に倒す: 大域の名でなく引いた辿れない値(既定値・閉包の cell・組の中・class の値)・source を読めない module の大域は、
  その module の file の sha256 を足す。名で辿れない読み(:class:`NamespaceRead`)は、大域の名として読むか属性として読むかで分ける:
  module の名前空間の読み(``globals()`` ・ ``vars`` ・ ``sys.modules`` ・ ``import_module`` ・ ``eval``)を持つ関数は、その関数の
  module の file と関数が参照する module の file を、object の名前空間の読み(``x.__dict__`` ・ ``__getattribute__``)を持つ関数は、
  関数が参照する module の file を足す(欄の名 ``x.vars`` は読みではない)。
"""

import bisect
import contextvars
import dataclasses
import dis
import enum
import functools
import hashlib
import importlib
import importlib.machinery
import importlib.util
import io
import operator
import os
import re
import struct
import sys
import threading
import types
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import chain, pairwise
from types import CodeType, FunctionType, ModuleType

from doeff_hy_bytecode_guard import bound_source, called_code, loader_hooks
from doeff_hy_bytecode_guard.import_scope import IMPORT_SCOPE, ImportScope, current_scope
from doeff_hy_bytecode_guard.records import (
    ANON_VAR_NAME,
    ENVIRONMENT_READ,
    GENERATED_NAME_PREFIX,
    LOCAL_MACRO_TABLE,
    MACRO_TABLE,
    READER_TABLE,
    MacroRecord,
    UsedFile,
    UsedMacro,
    UsedValue,
    ValueReference,
    WholeRequire,
    exported_macro_names,
)

Sha256Of = Callable[[str], "str | None"]


class RecordingTable(dict[str, object]):
    """展開する module の macro の表の代わり — Hy の展開が引いた名(使った macro)と、引いて無かった名を覚える。require が
    表に足す macro はそのまま入る(``dict`` の書きの口は替えない)。"""

    def __init__(self, entries: dict[str, object]) -> None:
        super().__init__(entries)
        self.used: set[str] = set()
        self.absent: set[str] = set()
        #: この表を開いた記録(compile の口の包みが、表から記録を作るため)。
        self.recording: MacroRecording | None = None
        #: この module の展開の間に Hy の compiler が開いた局所の macro の表(:func:`_watch_local_tables` が足す — 関数の中の
        #: require と defmacro が macro を入れる表。Hy の展開はこの表を module の表より先に引くので、ここから引かれた macro は
        #: :meth:`__getitem__` を通らない)。
        self.local_tables: tuple[dict[str, object], ...] = ()

    def __getitem__(self, name: str) -> object:
        """Hy の展開が macro を引く口 — 引かれた名を使った macro として覚える。"""
        value = super().__getitem__(name)
        self.used.add(name)
        return value

    def __contains__(self, name: object) -> bool:
        """Hy の展開が macro かを確かめる口 — 無かった名を覚える。"""
        found = super().__contains__(name)
        if not found and isinstance(name, str):
            self.absent.add(name)
        return found


#: Hy の compiler が局所の状態を開く口を包んだか(process に 1 つ)。包みは Hy の compiler の class という process の共有物を
#: 書き換えるので、持ち主はこの module 1 つ。free-threaded の Python では compile が並ぶので lock の下で 1 度だけ包む。
_LOCAL_WATCH = {"installed": False}
_LOCAL_WATCH_LOCK = threading.Lock()


def _watch_local_tables() -> None:
    """Hy の compiler が局所の状態(関数の本体など)を開くたびに、その macro の表を、展開している module の記録の表
    (:class:`RecordingTable`)に覚えさせる(process で 1 度だけ包む)。関数の中の require で入れた macro は局所の表から引かれ、
    module の表を通らないので、覚えないと記録から漏れ、macro を変えても使う側が古い展開のまま動いた(設計検証
    lt-JYZ9N64ZE70GCBXPP33QDG3WF7 の範囲の外の所見 1)。局所の表は dict のまま覚える — Hy の require は局所の表を
    ``type(target) is dict`` で見分けるので、引いた名を覚える表に替えられない。"""
    with _LOCAL_WATCH_LOCK:
        if _LOCAL_WATCH["installed"]:
            return
        # Hy の source を compile する時だけ読む(起動時に Hy を読まない)。
        from hy.compiler import HyASTCompiler

        opened = HyASTCompiler.new_local_state

        def new_local_state(compiler: HyASTCompiler) -> None:
            """Hy の局所の状態を開き、その macro の表を展開している module の記録の表に覚えさせる(包んだ口)。"""
            opened(compiler)
            # 一番下の状態は compiler が module を置く前に開く(module の直下の require は module の表へ入る)。
            module = getattr(compiler, "module", None)
            table = None if module is None else vars(module).get(MACRO_TABLE)
            if isinstance(table, RecordingTable):
                table.local_tables = (*table.local_tables, compiler.local_state_stack[-1][LOCAL_MACRO_TABLE])

        HyASTCompiler.new_local_state = new_local_state
        _LOCAL_WATCH["installed"] = True


class MacroRecording:
    """展開 1 回の間、module の macro の表を :class:`RecordingTable` に替える(``with`` の中が展開)。抜けた後も
    :meth:`record` で記録を作れる。同じ module で入れ子に開いた時は外の記録をそのまま使う(表を替えない)。``with`` の中では
    実行の始まった code も拾う(:mod:`doeff_hy_bytecode_guard.called_code` — digest が覆う関数の code を、展開で実行された関数に
    絞るため)。"""

    def __init__(self, module: ModuleType, hy_version: str, sha256_of: Sha256Of) -> None:
        self._module = module
        self._hy_version = hy_version
        self._sha256_of = sha256_of
        _watch_local_tables()
        namespace = vars(module)
        present = namespace.get(MACRO_TABLE)
        self._owner = not isinstance(present, RecordingTable)
        self._had_table = MACRO_TABLE in namespace
        self.table = (
            present
            if isinstance(present, RecordingTable)
            else RecordingTable(present if isinstance(present, dict) else {})
        )
        #: 展開の間の実行の拾い(``with`` に入る前は拾っていない — その時の記録は閉包の関数の code を全部覆う)。
        self._mut_called: called_code.CalledCode | called_code.Uncollectable = (
            called_code.Uncollectable("展開を囲んでいない(with の外)")
        )
        if self._owner:
            self.table.recording = self

    def __enter__(self) -> "MacroRecording":
        """展開の前に、module の表を引いた名を覚える表に替え、実行の始まった code を拾い始める。"""
        if self._owner:
            vars(self._module)[MACRO_TABLE] = self.table
            if isinstance(self._mut_called, called_code.CalledCode):
                # 同じ囲みに 2 度入った — 表は前の展開が引いた名を持ち続けるので、拾った code も残して続きを足す。
                self._mut_called.reopen()
            else:
                self._mut_called = called_code.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """展開の後に、module の表を普通の dict に戻す(展開の外の読みを覚えない)。"""
        if self._owner and isinstance(self._mut_called, called_code.CalledCode):
            self._mut_called.close()
        namespace = vars(self._module)
        if not self._owner or namespace.get(MACRO_TABLE) is not self.table:
            return
        if self._had_table or self.table:
            namespace[MACRO_TABLE] = dict(self.table)  # 展開の後の module には普通の dict を残す
        else:
            del namespace[MACRO_TABLE]

    def record(self, source: str | bytes, also: tuple[ValueReference, ...] = ()) -> MacroRecord:
        """展開が使った macro の記録(source = 展開した source の文 — 表を通らない hy.R の呼び出しを読む・also = macro の外で
        展開の結果を変える値の在処)。"""
        name = self._module.__name__
        # 展開の間に実行の始まった code(記録を作る歩みの間の実行は入れない — 歩みは閉包の関数を呼ばない)。
        match self._mut_called:
            case called_code.CalledCode() as collecting:
                executed: frozenset[CodeType] | None = collecting.codes()
            case called_code.Uncollectable():
                executed = None
        table = dict(self.table)
        found: dict[UsedMacroKey, FunctionType] = {}
        for used in sorted(self.table.used):
            macro = table.get(used)
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(MACRO_TABLE, macro)] = macro
        # 関数の中の require で入った macro(局所の表から引かれ、module の表の引いた名に入らない)— 展開で実行された macro
        # だけ(拾えない環境では局所の表の全部 — 多く入れても古いと判じやすくなるだけ)。
        for local in self.table.local_tables:
            for macro in list(local.values()):
                if (
                    isinstance(macro, FunctionType)
                    and _from_elsewhere(macro, name)
                    and (executed is None or macro.__code__ in executed)
                ):
                    found[_macro_key(MACRO_TABLE, macro)] = macro
        readers = vars(self._module).get(READER_TABLE)
        for macro in readers.values() if isinstance(readers, dict) else ():
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(READER_TABLE, macro)] = macro
        text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
        # 表を通らない hy.R の呼び出しの先も使った macro(自分の module の macro は source が覆う)。
        for reference in _source_references(text):
            if reference.module == name or _named_only(reference.module):
                continue
            called = _macro_now(MACRO_TABLE, reference.module, reference.name)
            if called is not None:
                found[UsedMacroKey(MACRO_TABLE, reference.module, reference.name)] = called
        macros = tuple(
            (key, macro_digest(key, macro, self._sha256_of).called(executed))
            for key, macro in sorted(found.items(), key=lambda item: item[0].sort_key)
        )
        others = tuple(
            (reference, value_digest(reference, self._sha256_of))
            for reference in sorted(also, key=lambda reference: (reference.module, reference.name))
        )
        depended = frozenset(
            module
            for closure in (*(closure for _, closure in macros), *(closure for _, closure in others))
            if closure is not None
            for module in closure.modules
        )
        return MacroRecord(
            self._hy_version,
            tuple(
                UsedMacro(key.table, key.module, key.name, closure.digest, f"{closure.covered:x}")
                for key, closure in macros
            ),
            tuple(sorted(self.table.absent - table.keys())),
            _whole_requires((table, *self.table.local_tables), name),
            tuple(
                UsedValue(reference.module, reference.name, closure.digest)
                for reference, closure in others
                if closure is not None
            ),
            _closure_files(depended, self._sha256_of),
        )


def record_is_current(record: MacroRecord, hy_version: str, sha256_of: Sha256Of) -> bool:
    """記録が今の環境に合うか — Hy の版、使った macro を module 名から今の環境で引き直した digest(定義した module を import
    する — 使い手の require がどのみち import する module)と macro の外の値の digest、引いて無かった名が今も提供元に無いこと。
    digest で比べられない行(記録か今の歩みが import の途中の module に当たった)があれば、閉包が依った module の file
    (import しない)も比べる。"""
    if record.hy_version != hy_version:
        return False
    if current_scope() is not None:
        return _rows_are_current(record, sha256_of)  # 一番外の読み込みの中 — file の状態はその中で 1 度だけ読む
    scope = ImportScope()  # 読み込みの外(道具が直に照らす)— この照らしの間だけの覚え
    token = IMPORT_SCOPE.set(scope)
    try:
        return _rows_are_current(record, sha256_of)
    finally:
        scope.close()
        IMPORT_SCOPE.reset(token)


def _rows_are_current(record: MacroRecord, sha256_of: Sha256Of) -> bool:
    """記録の行(使った macro・macro の外の値・引いて無かった名・digest で比べられない時の file)が今の環境に合うか。"""
    by_files = False
    for check in _row_checks(record, sha256_of):
        match check:
            case RowCheck.STALE:
                return False
            case RowCheck.BY_FILES:
                by_files = True
            case RowCheck.SAME:
                pass
    return all(_still_absent(provider, record.absent) for provider in record.providers) and (
        not by_files or all(_file_is_current(used, sha256_of) for used in record.files)
    )


class RowCheck(enum.Enum):
    """記録の digest の行 1 つ(使った macro・macro の外の値)の照らしの結末。

    - SAME = 今の digest が記録と同じ。
    - STALE = 違う・今の環境で引けない・記録は import の途中の状態で作ったが今は比べられる(作り直した記録は比べられる)・記録か
      今の歩みが環境変数の読みに当たった(照らせない)。
    - BY_FILES = 今の歩みが import の途中の module に当たった — 記録の file の行(閉包が依った module の file)で比べる。
    """

    SAME = "same"
    STALE = "stale"
    BY_FILES = "by-files"


def _row_checks(record: MacroRecord, sha256_of: Sha256Of) -> Iterator[RowCheck]:
    """記録の digest の行を順に今の環境で照らす(呼び手は古い行に当たったら止める — 残りの macro を import しない)。"""
    for used in record.macros:
        macro = _macro_now(used.table, used.module, used.name)
        key = UsedMacroKey(used.table, used.module, used.name)
        # 記録が中身を覆った関数(と今の歩みの絞らない関数)の中身で、今の閉包の digest を作り直す。
        yield _row_check(
            used.digest,
            None
            if macro is None
            else macro_digest(key, macro, sha256_of).covering(int(used.covered, 16)),
        )
    for value in record.values:
        reference = ValueReference(value.module, value.name)
        yield _row_check(value.digest, value_digest(reference, sha256_of))


def _row_check(recorded: str | None, now: "ClosureDigest | None") -> RowCheck:
    """記録の digest(None = 記録の時に import の途中だった)と今の閉包(None = 今の環境で引けない)の照らしの結末。"""
    if now is None or ENVIRONMENT_READ in (recorded, now.digest):
        # 環境変数の読みは、記録の側でも今の側でも照らせない — 今の歩みが import の途中で file の行で比べる形になっても、file は
        # 環境変数の違いを写さない。
        return RowCheck.STALE
    if now.digest is None:
        return RowCheck.BY_FILES
    if recorded is None:
        return RowCheck.STALE
    return RowCheck.SAME if now.digest == recorded else RowCheck.STALE


def _file_is_current(used: UsedFile, sha256_of: Sha256Of) -> bool:
    """閉包が依った module の file が記録と同じ中身か(module は import しない)。"""
    path = _module_file(used.module)
    return path is not None and sha256_of(path) == used.sha256


def _still_absent(provider: WholeRequire, absent: tuple[str, ...]) -> bool:
    """全部 require した提供元に、引いて無かった名(前置きを外した名)が今も無いか — 足されていれば、使い手の次の展開では
    その名の呼び出しが macro の展開に変わる。"""
    module = _imported(provider.module)
    table = None if module is None else vars(module).get(MACRO_TABLE)
    if not isinstance(table, dict):
        return False
    lead = f"{provider.prefix}." if provider.prefix else ""
    return not any(name.startswith(lead) and name[len(lead) :] in table for name in absent)


def _whole_requires(tables: Iterable[dict[str, object]], module_name: str) -> tuple[WholeRequire, ...]:
    """展開の後の使い手の表(module の表と、関数の中の局所の表)から、提供元の macro を全部 require した形を読む — 提供元
    (macro を定義した module)ごと・前置きごとに、使い手の表に入った名が提供元の公開の名(``_hy_export_macros`` か、``_`` で
    始まらない名)を全部含むなら全部の require。名を選んで全部を書き並べた require も全部と数える(表をまたいで足した名も —
    照らしが古いと判じる側に倒れるだけ)。"""
    entered: dict[WholeRequire, set[str]] = {}
    for key, macro in chain.from_iterable(list(table.items()) for table in tables):
        if not (isinstance(macro, FunctionType) and _from_elsewhere(macro, module_name)):
            continue
        owner = str(macro.__module__)
        provider = sys.modules.get(owner)
        entries = None if provider is None else vars(provider).get(MACRO_TABLE)
        if not isinstance(entries, dict):
            continue
        for defined in sorted(entries):
            if dict.__getitem__(entries, defined) is not macro:
                continue
            if key == defined:
                entered.setdefault(WholeRequire(owner, ""), set()).add(defined)
            elif key.endswith(f".{defined}"):
                prefix = key[: -len(defined) - 1]
                entered.setdefault(WholeRequire(owner, prefix), set()).add(defined)
    return tuple(
        sorted(
            (require for require, names in entered.items() if _exported(require.module) <= names),
            key=lambda require: (require.module, require.prefix),
        )
    )


def _exported(module_name: str) -> frozenset[str]:
    """提供元の公開の macro の名(Hy の require の ``*`` が入れる名 — 名の読みは :func:`records.exported_macro_names`)。"""
    module = sys.modules.get(module_name)
    return exported_macro_names({} if module is None else vars(module))


@dataclass(frozen=True)
class UsedMacroKey:
    """使った macro の在処(表の名・定義した module の名・その module の表の中の名)— 照らす時に今の環境で引き直す鍵。"""

    table: str
    module: str
    name: str

    @property
    def sort_key(self) -> str:
        """記録の行の並びの順(module・表・名)。"""
        return f"{self.module}\0{self.table}\0{self.name}"


@dataclass(frozen=True)
class ClosureDigest:
    """閉包 1 つの digest と、閉包が依った module の名(歩みが辿った物の持ち主・import できなかった module — 記録の file の
    行になる)。digest は、歩みが import の途中の module か import できない module に当たった時は None(その時の材料は
    import の進み具合に依る — 比べずに file の行で比べる)、環境変数の読みに当たった時は印 ENVIRONMENT_READ(照らせない —
    いつも古いと判じる)。

    digest を持つ時は、閉包の歩みの材料(walk)と、digest が中身を覆った関数の印(covered — 番号 i の関数を覆えば bit i が 1)
    も持つ。歩みが作る digest は閉包の関数の中身を全部覆い(macro の外の値の行はこの digest)、使った macro の行は
    :meth:`called` (記録の時)と :meth:`covering` (照らしの時)で覆う関数を絞る。"""

    digest: str | None
    modules: tuple[str, ...]
    walk: "ClosureWalk | None" = None
    covered: int = 0

    def called(self, codes: frozenset[CodeType] | None) -> "ClosureDigest":
        """記録の時の digest — 展開で実行の始まった code(None = 拾えなかった — 全部)の関数と、絞らない関数の中身を覆う。"""
        if self.walk is None:
            return self
        covered = self.walk.covered_by(codes)
        return ClosureDigest(self.walk.digest(covered), self.modules, self.walk, covered)

    def covering(self, covered: int) -> "ClosureDigest":
        """照らしの時の digest — 記録が中身を覆った関数(covered)と、今の歩みの絞らない関数の中身を覆う(今の歩みで絞らない関数が
        増えれば、その中身が入って digest は記録と違う — 古いと判じる)。"""
        if self.walk is None:
            return self
        widened = covered | self.walk.forced
        return ClosureDigest(self.walk.digest(widened), self.modules, self.walk, widened)


@dataclass(frozen=True)
class CodeSlot:
    """閉包の歩みが初めて出会った関数 1 つ — その code(記録の時に、展開で実行が始まったかを引く)と、code の中身の digest
    (:attr:`CodeFacts.shape`)。"""

    code: CodeType
    shape: str


#: 中身を覆わない関数の code の中身の代わりに入れる文(16 進の digest と決して一致しない)。
_UNCOVERED = "uncovered"


@dataclass(frozen=True)
class ClosureWalk:
    """閉包の歩み 1 つの材料 — 関数の code の中身の所で切った材料の bytes(関数の数 + 1 個 — 文字列ごとに長さを前に置いた綴り)と、
    出会った順の関数(slots)・絞らない関数の印(forced — 束縛の文の先)・関数の中身を値として読む関数の印(reads_code)。"""

    segments: tuple[bytes, ...]
    slots: tuple[CodeSlot, ...]
    forced: int
    reads_code: int
    #: 印ごとの digest の覚え(process の中 — 同じ印の使い手を照らす度に、材料を hash し直さない)。
    digests: dict[int, str] = dataclasses.field(default_factory=dict, compare=False, repr=False)
    #: 閉包に、関数の中身を値として読む関数が在る時の、注釈ごとの読み(:func:`_code_facts` の ``strict``)で歩き直した歩み。覆う関数に
    #: 読む関数が入る時の digest はこちらで作る(関数の型注釈は、読まない閉包の digest には入らない)。
    strict: "ClosureWalk | None" = dataclasses.field(default=None, compare=False, repr=False)

    @property
    def everything(self) -> int:
        """全部の関数の中身を覆う印。"""
        return (1 << len(self.slots)) - 1

    def covered_by(self, codes: frozenset[CodeType] | None) -> int:
        """記録の時に中身を覆う関数の印 — 展開で実行の始まった code の関数と、絞らない関数。拾えなかった時(None)と、覆う関数の
        どれかが関数の中身を値として読む時(呼ばない関数の説明の文・引数の形 — 実行しなくても展開に効く)は全部。"""
        if codes is None:
            return self.everything
        covered = functools.reduce(
            operator.or_,
            (1 << index for index, slot in enumerate(self.slots) if slot.code in codes),
            self.forced,
        )
        return self.everything if covered & self.reads_code else covered

    def digest(self, covered: int) -> str:
        """covered の関数は code の中身を、他の関数は印 :data:`_UNCOVERED` を入れた digest(16 進)。"""
        known = self.digests.get(covered)
        if known is not None:
            return known
        if self.strict is not None and covered & self.reads_code:
            # 覆う関数が中身を値として読む時は全部の関数の中身を覆う(:meth:`covered_by`)— 注釈ごとの読みの歩みの digest。
            return self.digests.setdefault(covered, self.strict.digest(self.strict.everything))
        pieces = [self.segments[0]]
        for index, slot in enumerate(self.slots):
            text = (slot.shape if covered >> index & 1 else _UNCOVERED).encode("utf-8")
            pieces.extend((_LENGTH.pack(len(text)), text, self.segments[index + 1]))
        return self.digests.setdefault(covered, hashlib.sha256(b"".join(pieces)).hexdigest())


@dataclass(frozen=True)
class FileState:
    """digest の覚えが有効かを決める file の状態(path・更新時刻・大きさ)。"""

    path: str
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class RememberedDigest:
    """process の中の digest の覚え 1 つ — 辿った物の持ち主の file が今も同じ状態で、同じ関数(か module)の時だけ使う。"""

    subject: object
    files: tuple[FileState, ...]
    closure: ClosureDigest


#: process の中の digest の覚え(同じ macro の digest を、照らす度に辿り直さないため)。
_REMEMBERED: dict[UsedMacroKey | ValueReference, RememberedDigest] = {}


def macro_digest(key: UsedMacroKey, macro: FunctionType, sha256_of: Sha256Of) -> ClosureDigest:
    """macro の関数の code の閉包の digest(覚えがあればそれ)。"""
    return _remembered(key, macro, lambda walker: walker.function(macro), sha256_of)


def value_digest(reference: ValueReference, sha256_of: Sha256Of) -> ClosureDigest | None:
    """macro の外で展開の結果を変える値(module の属性)の閉包の digest(module を import する — 引けなければ None)。"""
    module = _imported(reference.module)
    if module is None or reference.name not in vars(module):
        return None
    value = vars(module)[reference.name]
    return _remembered(
        reference, value, lambda walker: walker.outside_value(module, value), sha256_of
    )


def _remembered(
    key: UsedMacroKey | ValueReference,
    subject: object,
    walk: Callable[["_Walker"], None],
    sha256_of: Sha256Of,
) -> ClosureDigest:
    """覚えた digest か、歩いて作った digest(同じ macro を照らす度に閉包を辿り直さないため)。"""
    remembered = _REMEMBERED.get(key)
    if remembered is not None and remembered.subject is subject and _confirmed(key, remembered):
        return remembered.closure
    entry = _facts_entry(key)
    stored = _stored_facts(entry) if entry is not None else {}
    walker = _Walker(sha256_of, CodeReadings(stored))
    walk(walker)
    walked = walker.walked()  # 文字列の定数の名の属性を入れ終える(そこでも import の途中の module に当たりうる)
    walkers = [walker]
    if walked.reads_code:
        # 関数の中身を値として読む関数が閉包に在る: 注釈ごとの読みで歩き直し、覆う関数にその関数が入る digest はこちらで作る。
        strict_walker = _Walker(sha256_of, CodeReadings(stored, strict=True))
        walk(strict_walker)
        walked = dataclasses.replace(walked, strict=strict_walker.walked())
        walkers.append(strict_walker)
    if entry is not None and any(each.readings.missed for each in walkers):
        # 次の process の同じ macro の歩みが code を読み直さない
        _write_facts(entry, {sha: facts for each in walkers for sha, facts in each.readings.used.items()})
    modules = tuple(sorted({name for each in walkers for name in (*each.files, *each.unimportable)}))
    if any(each._mut_environment for each in walkers):
        # 環境変数の読みに当たった digest は環境変数の値を写さない — 照らせない印にし、覚えない。import の途中の状態より先に判じる
        # (途中の状態の行は file で比べ、環境変数の違いに気づかない)。
        return ClosureDigest(ENVIRONMENT_READ, modules)
    if any(each._mut_partial for each in walkers):
        # import の途中の状態の digest は覚えない — 覚えると、その process の照らしと記録が全部その値になり、import の順の
        # 違う process と置き場の entry を書き直し合う(card ki-464fa307a62b)。
        return ClosureDigest(None, modules)
    closure = ClosureDigest(walked.digest(walked.everything), modules, walked, walked.everything)
    states = tuple(
        state
        for path in sorted({path for each in walkers for path in each.files.values()})
        if (state := _state_now(path)) is not None
    )
    _REMEMBERED[key] = RememberedDigest(subject, states, closure)
    return closure


def _confirmed(key: UsedMacroKey | ValueReference, remembered: RememberedDigest) -> bool:
    """覚えた digest を使ってよいか — 依った file が覚えた時の状態のままか。一番外の読み込み 1 つの中では file の状態は変わらない
    物として扱うので、確かめた覚えを 2 度確かめない(照らしは Hy の module 1 つごとに走り、同じ覚えを数百回確かめていた —
    agora-redesign card ki-be596ac8276f)。"""
    scope = current_scope()
    if scope is not None and scope.confirmed.get(key) is remembered:
        return True
    if not all(map(_unchanged, remembered.files)):
        return False
    if scope is not None:
        scope.confirmed[key] = remembered
    return True


def _file_state(path: str) -> FileState | None:
    """file の今の状態(覚えた digest を使ってよいかを決める — 消えた file は None)。"""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return FileState(path, status.st_mtime_ns, status.st_size)


#: 一番外の読み込みの覚えに、まだ状態を読んでいない file の印(消えた file の None と分ける)。
_UNREAD = object()


def _state_now(path: str) -> FileState | None:
    """覚えの確かめと覚える時に使う file の今の状態 — 一番外の読み込み 1 つ(:mod:`doeff_hy_bytecode_guard.import_scope`)の中では
    file ごとに 1 度だけ stat する(検の読み込みで照らし 797 回・stat 9,700 回だった — card ki-be596ac8276f)。読み込みの外で
    覚える時(記録を作る道具)は毎回 stat する。"""
    scope = current_scope()
    if scope is None:
        return _file_state(path)
    known = scope.states.get(path, _UNREAD)
    if known is _UNREAD:
        known = scope.states[path] = _file_state(path)
    return known if isinstance(known, FileState) else None


def _unchanged(state: FileState) -> bool:
    """file が覚えた時の状態のままか(消えた file は偽)— :func:`_file_state` と同じ判じ。"""
    now = _state_now(state.path)
    return now is not None and now.mtime_ns == state.mtime_ns and now.size == state.size


def _closure_files(names: frozenset[str], sha256_of: Sha256Of) -> tuple[UsedFile, ...]:
    """閉包が依った module の file の sha256 — digest で比べられない行(記録か今の歩みが import の途中の module に当たった)が
    ある時に、照らしが import せずに比べる。import できなかった module の file も入る(引けない file は毎回違う印 — いつも
    古いと判じられる)。"""
    return tuple(
        UsedFile(name, (sha256_of(path) if path is not None else None) or os.urandom(16).hex())
        for name in sorted(names)
        if not _named_only(name)
        for path in (_module_file(name),)
    )


def _module_file(module_name: str) -> str | None:
    """module 名の今の環境の file — 読み込み済みなら sys.modules、未だなら import の探索(親の package も含めて何も実行しない —
    ``importlib.util.find_spec`` は親の package を import し、その本体が別の module を読み込む)。"""
    module = sys.modules.get(module_name)
    if module is not None:
        file = vars(module).get("__file__")
        return file if isinstance(file, str) else None
    spec = _spec_without_import(module_name)
    origin = None if spec is None else spec.origin
    return origin if isinstance(origin, str) and os.path.isfile(origin) else None


def _spec_without_import(module_name: str) -> importlib.machinery.ModuleSpec | None:
    """module の spec を import の探索の finder に直に聞く(親の package は読み込み済みなら ``__path__``、未だなら同じく探した
    場所 — 何も実行しない)。"""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        spec = vars(loaded).get("__spec__")
        return spec if isinstance(spec, importlib.machinery.ModuleSpec) else None
    parent, _, _ = module_name.rpartition(".")
    locations: list[str] | None = None
    if parent:
        parent_spec = _spec_without_import(parent)
        found = None if parent_spec is None else parent_spec.submodule_search_locations
        if found is None:
            return None
        locations = list(found)
    for finder in sys.meta_path:
        spec = finder.find_spec(module_name, locations, None)
        if spec is not None:
            return spec
    return None


# ---- 拾った名から macro を引く -------------------------------------------------------------------------------------


def _from_elsewhere(macro: FunctionType, module_name: str) -> bool:
    """展開する module の外で定義された macro か(自分の macro は source が覆う・Hy 自身の macro は Hy の版が覆う)。"""
    owner = macro.__module__
    return isinstance(owner, str) and owner != module_name and not _named_only(owner)


def _macro_key(table: str, macro: FunctionType) -> UsedMacroKey:
    """macro を定義した module の表の中の名(同じ関数の object を引く名 — require の別名ではなく、定義した名)。"""
    owner = str(macro.__module__)
    module = sys.modules.get(owner)
    entries = None if module is None else vars(module).get(table)
    if isinstance(entries, dict):
        for name in sorted(entries):
            if dict.__getitem__(entries, name) is macro:
                return UsedMacroKey(table, owner, name)
    return UsedMacroKey(table, owner, macro.__name__)


def _macro_now(table: str, module_name: str, name: str) -> FunctionType | None:
    """今の環境で module 名から引いた macro(module を import する — 引けなければ None)。"""
    module = _imported(module_name)
    entries = None if module is None else vars(module).get(table)
    if not isinstance(entries, dict) or name not in entries:
        return None
    macro = dict.__getitem__(entries, name)
    return macro if isinstance(macro, FunctionType) else None


@dataclass(frozen=True)
class NotImportable:
    """digest のために import できなかった module(名と、Hy か import の失敗の文)。"""

    module: str
    reason: str


def _import_for_digest(module_name: str) -> ModuleType | NotImportable:
    """digest のために module を import する。照らしは .pyc を読む途中(import の途中)に走るので、関数の中の import の先が
    循環する import の途中の module を require して落ちうる(展開の時はその枝を通らないので落ちない)— 落ちたら値で返す。"""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    from hy.errors import HyError  # 照らしは Hy の module に当たった時だけ走る

    try:
        return importlib.import_module(module_name)
    except (ImportError, HyError) as error:
        return NotImportable(module_name, f"{type(error).__name__}: {error}")


def _imported(module_name: str) -> ModuleType | None:
    """module 名の module(読み込み済みでなければ import する — import できなければ None。照らす側は古いと判じる)。"""
    match _import_for_digest(module_name):
        case ModuleType() as module:
            return module
        case NotImportable():
            return None


@dataclass(frozen=True)
class MacroReference:
    """``hy.R.<module>.<名>`` の呼び出しが引く macro(module 名と mangle した名)。"""

    module: str
    name: str


#: source の文の中の ``hy.R.<module>.<名>``(module の部分の / は . の代わり)。
_HY_R = re.compile(r"hy\.R\.([^\s.()\[\]{}\"';]+)\.([^\s()\[\]{}\"';]+)")


def _source_references(text: str) -> Iterator[MacroReference]:
    """source の文が直に書いた hy.R の呼び出し(注釈の行は読まない)。"""
    for line in text.splitlines():
        if line.lstrip().startswith(";") or "hy.R." not in line:
            continue
        for match in _HY_R.finditer(line):
            yield _reference(match.group(1), match.group(2))


def _reference(module_part: str, name_part: str) -> MacroReference:
    """Hy の macroexpand と同じ読み方で、hy.R の module の部分と名の部分から macro の在処を作る。"""
    return MacroReference(
        _reference_module(module_part), ".".join(map(_mangle, name_part.split(".")))
    )


def _reference_module(module_part: str) -> str:
    """hy.R の module の部分(``doeff_hy/record``)の module 名。"""
    from hy.reader.mangling import slashes2dots

    return slashes2dots(_mangle(module_part))


def _mangle(text: str) -> str:
    """Hy の名の mangle(Hy は記録を作る時と照らす時には読み込み済み)。"""
    import hy

    return hy.mangle(text)


# ---- code の閉包の digest ------------------------------------------------------------------------------------------


#: 名だけ入れる package(Hy の版・Python の版で覆う物)。標準 library は sys.stdlib_module_names で足す。
#: doeff_hy_bytecode_guard は記録の作り方そのもの(記録の形は RECORD_TAG で覆う・process の中の覚えを持つ)。
_NAMED_ONLY = frozenset({"hy", "builtins", "funcparserlib", "doeff_hy_bytecode_guard"})


def _named_only(module_name: str) -> bool:
    """名だけ入れる module か(Hy 自身・標準 library・組み込み — 版で覆う物)。"""
    top = module_name.partition(".")[0]
    return top in _NAMED_ONLY or top in sys.stdlib_module_names


#: 環境変数を持つ標準の module(os と、その下の posix・nt)と、環境変数を読む属性の名(表 environ・environb と、それを読む
#: getenv・getenvb)— 名だけ入れる module のうち、版で覆えない process の状態の読み(module の docstring の「環境変数の読み」)。
_ENVIRONMENT_MODULES = frozenset({"os", "posix", "nt"})
_ENVIRONMENT_NAMES = frozenset({"environ", "environb", "getenv", "getenvb"})


def _reads_environment(namespace: Mapping[str, object], names: tuple[str, ...]) -> bool:
    """code か束縛の文が、環境変数を持つ module を大域の名で引いて環境変数を読むか — 照らせない digest を見分けるため。名の並びに
    環境変数を読む属性の名が在り、並びの名のどれかが大域でその module を指す時(``os.environ.get`` ・ ``(import os :as o)`` の
    ``o.getenv``)。名の並びは属性の名も含むので、別の object の属性 ``x.environ`` と os の参照が同じ code に在れば読みと数える
    (安全側 — その macro の使い手が照らせなくなるだけ)。"""
    if _ENVIRONMENT_NAMES.isdisjoint(names):
        return False
    return any(_environment_module(namespace.get(name)) for name in names)


def _source_reads_environment(names: tuple[str, ...]) -> bool:
    """module の source に現れる名の全部が環境変数の読みを含みうるか — 辿れない値を持ち主の file で覆う時に、照らせない印に
    するかを決めるため。環境変数を持つ module の名と、環境変数を読む属性の名の両方が現れる時(どこで読むか — 読み込みの時か
    呼ばれた時か — は分けない。安全側)。"""
    return not (_ENVIRONMENT_NAMES.isdisjoint(names) or _ENVIRONMENT_MODULES.isdisjoint(names))


def _environment_module(value: object) -> bool:
    """値が環境変数を持つ標準の module か(別名で import した os も同じ module の object)。"""
    return isinstance(value, ModuleType) and value.__name__ in _ENVIRONMENT_MODULES


def _environment_object(value: object) -> bool:
    """値が環境変数を読む object そのものか(os.environ・os.environb・os.getenv・os.getenvb・posix か nt の environ)— 名で
    module を引かずに値として当たった読み(別名の import・既定値・閉包の cell)を見分けるため。"""
    return any(
        value is found
        for module in _ENVIRONMENT_MODULES
        if (owner := sys.modules.get(module)) is not None
        for name in _ENVIRONMENT_NAMES
        if (found := vars(owner).get(name)) is not None
    )


class NamespaceRead(enum.Enum):
    """code が名で辿れない読みを持つか、持つならどの広さか。

    - NONE = 持たない。
    - OBJECT = 渡された object の名前空間の読み(``x.__dict__`` ・ ``__getattribute__`` ・ ``attrgetter`` ・ ``getattr_static``)。
      読む名は ``getattr(x, "名")`` と同じく code の文字列の定数として覆う(:attr:`CodeFacts.texts`)。関数の module そのものは、
      MODULE の読み(``globals()`` ・ ``sys.modules`` ・ ``import_module`` …)なしには object として渡せないので、関数が参照する
      module の file だけで覆う。
    - MODULE = module の名前空間・module の表・文字列の式の読み(``globals()`` ・ ``vars`` ・ ``sys.modules`` ・ ``eval`` …)—
      関数の module と、関数が参照する module の file で覆う。
    """

    NONE = "none"
    OBJECT = "object"
    MODULE = "module"


#: 大域の名として読むと MODULE の読みになる名(``vars`` は大域の組み込みとして呼ぶ時だけ — 欄の名 ``x.vars`` は読みではない)。
_MODULE_READ_GLOBALS = frozenset(
    {
        "globals",
        "vars",
        "locals",
        "eval",
        "exec",
        "__import__",
        "import_module",
        "modules",
        "macroexpand",
        "macroexpand_1",
    }
)

#: 属性として読むと MODULE の読みになる名(``sys.modules`` ・ ``hy.eval`` ・ ``importlib.import_module`` ・ ``f.__globals__``)。
_MODULE_READ_ATTRIBUTES = frozenset(
    {
        "eval",
        "exec",
        "__import__",
        "import_module",
        "modules",
        "macroexpand",
        "macroexpand_1",
        "__globals__",
    }
)

#: 大域の名・属性として読むと OBJECT の読みになる名。
_OBJECT_READ_GLOBALS = frozenset({"attrgetter", "getattr_static"})
_OBJECT_READ_ATTRIBUTES = frozenset(
    {"__dict__", "__getattribute__", "attrgetter", "getattr_static"}
)

_UNTRACEABLE_NAMES = (
    _MODULE_READ_GLOBALS | _MODULE_READ_ATTRIBUTES | _OBJECT_READ_GLOBALS | _OBJECT_READ_ATTRIBUTES
)


#: class の中身のうち、振る舞いでない物(行番号・注記・抽象 class の内部の覚え・dataclass の欄の記述)。
_CLASS_SKIP = frozenset(
    {
        "__dict__",
        "__weakref__",
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
        "__annotations__",
        "__annotate__",
        "__annotate_func__",
        "__annotations_cache__",
        "__orig_bases__",
        "__parameters__",
        "__type_params__",
        "__dataclass_fields__",
        "__dataclass_params__",
        "_abc_impl",
        # enum と namedtuple が class の定義から作る表(member と欄の既定値は member・__new__ の既定値として辿る)。
        "_member_map_",
        "_value2member_map_",
        "_member_names_",
        "_hashable_values_",
        "_unhashable_values_",
        "_unhashable_values_map_",
        "_member_type_",
        "_new_member_",
        "_use_args_",
        "_value_repr_",
        "__classdictcell__",
        "_field_defaults",
    }
)


@dataclass(frozen=True)
class ImportReference:
    """code の中の import の命令 1 つ(module の名と相対の段数)。"""

    name: str
    level: int


@dataclass(frozen=True)
class CodeFacts:
    """code object 1 つ(入れ子の code を含む)から読んだ物 — 行番号と位置を除いた中身の digest・名の表・文字列の定数
    (``getattr(x, "名")`` と ``d["名"]`` の名の候補)・import・hy.R の呼び出しの module・名で辿れない読みを持つか・module の
    置き場(:data:`_LOCATION_NAMES`)を値として読むか(大域の読み・属性の読み・文字列の定数の名 — 書くだけなら偽)。"""

    shape: str
    names: tuple[str, ...]
    texts: tuple[str, ...]
    imports: tuple[ImportReference, ...]
    called_modules: tuple[str, ...]
    reads: NamespaceRead
    reads_location: bool


#: process の中の code の読みの覚え(code object の id → (code・読んだ物) — code を生かしておき id の使い回しを防ぐ)。
#: 意味に効かない物(関数の型注釈・Hy の一時変数の名の番号)を除いた読み。
_FACTS: dict[int, tuple[CodeType, CodeFacts]] = {}

#: 注釈ごとの読み(:func:`_code_facts` の ``strict``)の覚え。注釈を値として読む閉包(:data:`_CODE_READ_NAMES`)の歩みだけが使う。
_STRICT_FACTS: dict[int, tuple[CodeType, CodeFacts]] = {}


def _code_facts(code: CodeType, strict: bool = False) -> CodeFacts:
    """code object から digest に要る物を読む(行番号と位置を除く — process の中で code ごとに 1 度だけ読む)。

    ``strict`` が偽の読みは、展開の結果に効かない 2 つを除く(card ki-968309595391 — 型注釈を足しただけの変更や、前に関数が増えて
    Hy の一時変数の番号がずれただけの変更で、macro の閉包の digest が変わり、使い手の module が全部展開し直されていた):

    - 関数の型注釈(3.14 の ``__annotate__`` の入れ子の code と、それを関数に付ける命令 — :func:`_function_annotations`。3.13 までの、
      関数を作る前に積む注釈の組とそれを付ける旗 — :func:`_inline_function_annotations`)。注釈の中だけに
      現れる名・import なども、その code の読み(名の表・文字列・import …)に入れない。class の本体の注釈(``__annotate_func__`` を
      束縛する物 — dataclass の欄)は除かない。
    - Hy の一時変数の名の番号(``_hy_anon_46`` → ``_hy_anon_48``)。code 1 つごとに、命令の出現順の通し番号へ振り直す
      (:func:`_numbered_anon_names`)。

    ``strict`` が真の読みは注釈も入れる(番号の振り直しは同じ)。"""
    cache = _STRICT_FACTS if strict else _FACTS
    remembered = cache.get(id(code))
    if remembered is not None and remembered[0] is code:
        return remembered[1]
    instructions = _Instructions(code, drop_annotations=not strict)
    nested = tuple(
        _code_facts(constant, strict)
        for constant in instructions.constants
        if isinstance(constant, CodeType)
    )
    texts = tuple(
        constant for constant in instructions.constants if isinstance(constant, str)
    )  # 定数の順のまま
    facts = CodeFacts(
        shape=instructions.shape(strict),
        names=tuple(sorted({*instructions.names, *(name for inner in nested for name in inner.names)})),
        texts=tuple(sorted({*texts, *(text for inner in nested for text in inner.texts)})),
        imports=(*_imports(code), *(i for inner in nested for i in inner.imports)),
        called_modules=tuple(
            sorted(
                {*_called_modules(texts), *(m for inner in nested for m in inner.called_modules)}
            )
        ),
        reads=_widest((_namespace_read(code), *(inner.reads for inner in nested))),
        reads_location=_reads_location(code, texts) or any(inner.reads_location for inner in nested),
    )
    cache[id(code)] = (code, facts)
    return facts


def _reads_location(code: CodeType, texts: tuple[str, ...]) -> bool:
    """code(入れ子を除く)が module の置き場を値として読むか — 大域の読み(``__file__``)・属性の読み(``m.__file__``)・文字列の
    定数の名(``getattr(m, "__file__")``)。作った module に書く(``module.__file__ = …`` — STORE_ATTR)だけなら読みではない。"""
    if not _LOCATION_NAMES.isdisjoint(texts):
        return True
    if _LOCATION_NAMES.isdisjoint(code.co_names):
        return False
    return any(
        use.name in _LOCATION_NAMES and use.way is not NameWay.OTHER for use in _name_uses(code)
    )


def _constant_text(value: object, strict: bool) -> str:
    """code の定数 1 つの文(入れ子の code は行番号を除いた digest・集合は並びに依らない形)。"""
    match value:
        case CodeType():
            return f"code:{_code_facts(value, strict).shape}"
        case tuple():
            return "(" + ",".join(_constant_text(item, strict) for item in value) + ")"
        case frozenset():
            return "{" + ",".join(sorted(_constant_text(item, strict) for item in value)) + "}"
        case _:
            # 文字列の定数の中の Hy の一時の名(接頭辞 + ``…_N``)は名でなく値(振り直さない — :func:`_numbered_anon_names` が拾わない
            # 形にするため、接頭辞の最後の ``_`` の前に NUL を挟む)。
            return f"{type(value).__name__}:{value!r}".replace(
                GENERATED_NAME_PREFIX, GENERATED_NAME_PREFIX[:-1] + "\0_"
            )


#: 関数に付ける注釈の命令(3.14 の ``SET_FUNCTION_ATTRIBUTE`` の旗 — 0x10 = ``__annotate__``・0x08 = 閉包)。
_ANNOTATE_ATTRIBUTE = 0x10
_CLOSURE_ATTRIBUTE = 0x08
#: 3.13 までの、関数に注釈の組を付ける旗(3.13 の ``SET_FUNCTION_ATTRIBUTE``・3.11 と 3.12 の ``MAKE_FUNCTION``)。
_ANNOTATIONS_ATTRIBUTE = 0x04
#: 閉包の組を作る前に cell を積む命令(1 つ積む物と 2 つ積む物)。
_CELL_LOADS = frozenset({"LOAD_FAST", "LOAD_FAST_BORROW", "LOAD_FAST_CHECK", "LOAD_DEREF"})
_CELL_PAIR_LOADS = frozenset({"LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW"})
#: 引数が番号でなく、番号の指す名・定数・jump 先に替えて数える命令の類(番号は定数の表・名の表の並びで変わるため)。
_NAMED_OPCODES = frozenset((*dis.hasname, *dis.haslocal, *dis.hasfree))
#: 名の表(``co_names``)の名を指す命令の類。
_NAME_OPCODES = frozenset(dis.hasname)
_JUMP_OPCODES = frozenset(dis.hasjump) if hasattr(dis, "hasjump") else frozenset((*dis.hasjrel, *dis.hasjabs))
_CONST_OPCODES = frozenset(dis.hasconst)


def _function_annotations(instructions: list[dis.Instruction]) -> tuple[set[int], list[CodeType]]:
    """命令の並びのうち、関数に型注釈を付ける命令の位置と、その ``__annotate__`` の code を挙げる。

    3.14 は関数の注釈を、注釈の関数を作る命令(閉包が要れば cell を積んで組にする命令・``LOAD_CONST`` の code・``MAKE_FUNCTION``・
    閉包なら ``SET_FUNCTION_ATTRIBUTE`` の閉包)を関数本体の作りの前に置き、関数を作ったあと ``SET_FUNCTION_ATTRIBUTE`` の
    ``__annotate__`` で付ける。この形に完全に合う時だけ挙げ、合わない形(class の本体の ``__annotate_func__`` を束縛する物・
    型引数の関数・読めない並び)は挙げない — 挙げないと注釈ごと digest に入るだけで、古い展開を合うと判じる向きには倒れない。"""
    dropped: set[int] = set()
    codes: list[CodeType] = []
    count = len(instructions)
    for index, instruction in enumerate(instructions):
        if (
            instruction.opname != "LOAD_CONST"
            or not isinstance(instruction.argval, CodeType)
            or instruction.argval.co_name != "__annotate__"
        ):
            continue
        end = index + 1
        if end >= count or instructions[end].opname != "MAKE_FUNCTION":
            continue
        end += 1
        start = index
        if end < count and _sets_attribute(instructions[end], _CLOSURE_ATTRIBUTE):
            end += 1
            start = _closure_tuple_start(instructions, index)
            if start < 0:
                continue
        attach = _annotate_attachment(instructions, end)
        if attach < 0:
            continue
        dropped.update(range(start, end))
        dropped.add(attach)
        codes.append(instruction.argval)
    return dropped, codes


def _sets_attribute(instruction: dis.Instruction, flag: int) -> bool:
    """命令が関数の属性 ``flag`` を付ける ``SET_FUNCTION_ATTRIBUTE`` か。"""
    return instruction.opname == "SET_FUNCTION_ATTRIBUTE" and instruction.arg == flag


def _closure_tuple_start(instructions: list[dis.Instruction], at: int) -> int:
    """位置 ``at`` の命令が積む関数の閉包の組を作る命令の始まり(``BUILD_TUPLE n`` と、cell を n 個積む命令。読めなければ -1)。"""
    last = at - 1
    if last < 0 or instructions[last].opname != "BUILD_TUPLE" or not isinstance(
        instructions[last].arg, int
    ):
        return -1
    wanted = instructions[last].arg
    taken = 0
    position = last - 1
    while taken < wanted:
        if position < 0:
            return -1
        name = instructions[position].opname
        if name in _CELL_LOADS:
            taken += 1
        elif name in _CELL_PAIR_LOADS:
            taken += 2
        else:
            return -1
        position -= 1
    return position + 1 if taken == wanted else -1


def _annotate_attachment(instructions: list[dis.Instruction], start: int) -> int:
    """注釈の関数を積んだ後(位置 ``start``)から、その関数を付ける関数の作り(閉包の組・``LOAD_CONST`` の code・``MAKE_FUNCTION``・
    閉包の ``SET_FUNCTION_ATTRIBUTE``)を読み、続く ``SET_FUNCTION_ATTRIBUTE`` の ``__annotate__`` の位置を返す(合わなければ -1)。"""
    count = len(instructions)
    position = start
    while position < count and instructions[position].opname in (
        *_CELL_LOADS,
        *_CELL_PAIR_LOADS,
        "BUILD_TUPLE",
    ):
        position += 1
    if (
        position + 1 >= count
        or instructions[position].opname != "LOAD_CONST"
        or not isinstance(instructions[position].argval, CodeType)
        or instructions[position + 1].opname != "MAKE_FUNCTION"
    ):
        return -1
    position += 2
    if position < count and _sets_attribute(instructions[position], _CLOSURE_ATTRIBUTE):
        position += 1
    if position < count and _sets_attribute(instructions[position], _ANNOTATE_ATTRIBUTE):
        return position
    return -1


def _inline_function_annotations(
    instructions: list[dis.Instruction],
) -> tuple[set[int], dict[int, dis.Instruction]]:
    """命令の並びのうち、3.13 までの形で関数に型注釈を付ける命令の位置と、旗を外して数える ``MAKE_FUNCTION`` を挙げる。

    3.13 までは関数の注釈を、名(文字列の定数)と注釈の式を交互に積んで ``BUILD_TUPLE`` で組にし、関数の本体の code の前
    (閉包の組が在ればその前)に積む。3.13 は関数を作ったあと ``SET_FUNCTION_ATTRIBUTE`` の旗 0x04 で付け、3.11・3.12 は
    ``MAKE_FUNCTION`` の旗 0x04 で付ける。組の積みを命令の stack の増減で読み、名がどれも文字列の定数の組に完全に合う時だけ挙げる
    (合わない形 — 注釈の中の条件式・呼び出しなど — は挙げず、注釈ごと digest に入るだけ)。"""
    dropped: set[int] = set()
    unflagged: dict[int, dis.Instruction] = {}
    for index, instruction in enumerate(instructions):
        if (
            instruction.opname != "MAKE_FUNCTION"
            or index == 0
            or instructions[index - 1].opname != "LOAD_CONST"
            or not isinstance(instructions[index - 1].argval, CodeType)
        ):
            continue
        attributes = _attribute_run(instructions, index + 1)
        flags = (
            functools.reduce(operator.or_, (instructions[position].arg or 0 for position in attributes), 0)
            if attributes
            else instruction.arg or 0
        )
        if not flags & _ANNOTATIONS_ATTRIBUTE:
            continue
        end = index - 2
        if flags & _CLOSURE_ATTRIBUTE:
            end = _value_start(instructions, end) - 1
        start = _value_start(instructions, end)
        if end < 0 or start < 0 or not _is_annotation_pairs(instructions, start, end):
            continue
        dropped.update(range(start, end + 1))
        attach = [position for position in attributes if instructions[position].arg == _ANNOTATIONS_ATTRIBUTE]
        if attributes:
            dropped.update(attach)
        else:
            unflagged[index] = instruction._replace(arg=flags & ~_ANNOTATIONS_ATTRIBUTE)
    return dropped, unflagged


def _attribute_run(instructions: list[dis.Instruction], start: int) -> range:
    """位置 ``start`` から続く ``SET_FUNCTION_ATTRIBUTE`` の並びの位置(3.12 までは空)。"""
    end = next(
        (
            position
            for position in range(start, len(instructions))
            if instructions[position].opname != "SET_FUNCTION_ATTRIBUTE"
        ),
        len(instructions),
    )
    return range(start, end)


def _value_start(instructions: list[dis.Instruction], end: int) -> int:
    """位置 ``end`` で終わり、値をちょうど 1 つ積む命令の並びの始まり(stack の増減を後ろから足す。jump を含むか読めなければ -1)。"""
    total = 0
    for position in range(end, -1, -1):
        instruction = instructions[position]
        if instruction.opcode in _JUMP_OPCODES:
            return -1
        try:
            total += dis.stack_effect(
                instruction.opcode, instruction.arg if instruction.opcode >= dis.HAVE_ARGUMENT else None
            )
        except ValueError:
            return -1
        if total == 1:
            return position
        if total > 1:
            return -1
    return -1


def _is_annotation_pairs(instructions: list[dis.Instruction], start: int, end: int) -> bool:
    """位置 ``start``〜``end`` の命令が、名(文字列の定数)と注釈を交互に並べた組を積むか(畳まれた定数の組 1 つの形も含む)。"""
    last = instructions[end]
    if start == end:
        value = last.argval
        return (
            last.opname == "LOAD_CONST"
            and isinstance(value, tuple)
            and len(value) % 2 == 0
            and all(isinstance(name, str) for name in value[::2])
        )
    if last.opname != "BUILD_TUPLE" or not isinstance(last.arg, int) or last.arg % 2:
        return False
    starts = _value_starts(instructions, end - 1, last.arg)
    return (
        starts is not None
        and starts[-1] == start
        and all(  # 後ろから数えたので、奇数番目が名
            instructions[at].opname == "LOAD_CONST" and isinstance(instructions[at].argval, str)
            for at in starts[1::2]
        )
    )


def _value_starts(instructions: list[dis.Instruction], end: int, count: int) -> tuple[int, ...] | None:
    """位置 ``end`` から後ろへ並ぶ、値を 1 つずつ積む ``count`` 個の並びの始まり(後ろから。読めなければ None)。"""
    if count == 0:
        return ()
    found = _value_start(instructions, end)
    rest = None if found < 0 else _value_starts(instructions, found - 1, count - 1)
    return None if rest is None else (found, *rest)


class _Instructions:
    """code 1 つ(入れ子を除く)の命令の並びと、意味に効かない物を除いた中身の digest(:func:`_code_facts`)。"""

    def __init__(self, code: CodeType, drop_annotations: bool) -> None:
        self._code = code
        listed = [
            instruction
            for instruction in dis.get_instructions(code, adaptive=False)
            if instruction.opname != "EXTENDED_ARG"
        ]
        dropped, self._annotations = (
            _function_annotations(listed) if drop_annotations else (set(), [])
        )
        inline, unflagged = _inline_function_annotations(listed) if drop_annotations else (set(), {})
        dropped |= inline
        self._kept = [
            unflagged.get(index, instruction)
            for index, instruction in enumerate(listed)
            if index not in dropped
        ]
        removed = [listed[index] for index in dropped]
        # 除いた命令だけが指す名と定数(3.13 までの注釈の名・引数の名の文字列)は、名の表・定数の表の読みからも除く。
        kept_names = {instruction.argval for instruction in self._kept if instruction.opcode in _NAME_OPCODES}
        self._removed_names = {
            instruction.argval for instruction in removed if instruction.opcode in _NAME_OPCODES
        } - kept_names
        kept_constants = {instruction.arg for instruction in self._kept if instruction.opcode in _CONST_OPCODES}
        self._removed_constants = {
            instruction.arg for instruction in removed if instruction.opcode in _CONST_OPCODES
        } - kept_constants

    @property
    def names(self) -> tuple[str, ...]:
        """名の表のうち、除いた命令だけが指す名を除いた物(表の順のまま)。"""
        return tuple(name for name in self._code.co_names if name not in self._removed_names)

    @property
    def constants(self) -> list[object]:
        """定数の表のうち、除いた注釈の code と、除いた命令だけが指す定数を除いた物(表の順のまま)。"""
        return [
            constant
            for index, constant in enumerate(self._code.co_consts)
            if index not in self._removed_constants
            and not (isinstance(constant, CodeType) and self.dropped(constant))
        ]

    def dropped(self, constant: CodeType) -> bool:
        """定数の code が、除いた注釈の code か。"""
        return any(constant is annotation for annotation in self._annotations)

    def shape(self, strict: bool) -> str:
        """行番号と位置を除いた中身の digest。命令は番号でなく指す名・定数・jump 先の命令の順で数え(定数の表と名の表の並びが
        変わっても変わらない)、除いた注釈の命令と code は入れず、Hy の一時変数の名の番号は出現順に振り直す。"""
        code = self._code
        offsets = [instruction.offset for instruction in self._kept]
        lines = [self._line(instruction, offsets, strict) for instruction in self._kept]
        handlers = [
            ",".join(
                (
                    str(bisect.bisect_left(offsets, start)),
                    str(bisect.bisect_left(offsets, end)),
                    str(bisect.bisect_left(offsets, target)),
                    str(depth),
                    str(lasti),
                )
            )
            for start, end, target, depth, lasti in _exception_table(code)
        ]
        # 定数の表の並びは残す(説明の文は命令に指されず、表の先頭の位置だけが印)— 除く注釈の code と定数の分だけ詰める。
        constants = [_constant_text(constant, strict) for constant in self.constants]
        document = "\0".join(
            (
                code.co_name,
                code.co_qualname,
                str(code.co_argcount),
                str(code.co_posonlyargcount),
                str(code.co_kwonlyargcount),
                str(code.co_flags),
                "\n".join(lines),
                "\n".join(handlers),
                repr(self.names),
                repr(code.co_varnames),
                repr(code.co_freevars),
                repr(code.co_cellvars),
                *constants,
            )
        )
        return hashlib.sha256(
            _numbered_anon_names(document).encode("utf-8", "surrogatepass")
        ).hexdigest()

    @staticmethod
    def _line(instruction: dis.Instruction, offsets: list[int], strict: bool) -> str:
        """命令 1 つの文(名・引数 — 番号の指す物で数える)。"""
        opcode = instruction.opcode
        if opcode in _JUMP_OPCODES:
            argument = f"@{bisect.bisect_left(offsets, instruction.argval)}"
        elif opcode in _CONST_OPCODES:
            argument = _constant_text(instruction.argval, strict)
        elif opcode in _NAMED_OPCODES:
            argument = instruction.argrepr
        else:
            argument = str(instruction.arg)
        return f"{instruction.opname} {argument}"


def _exception_table(code: CodeType) -> Iterator[tuple[int, int, int, int, bool]]:
    """code の例外表の行(始まり・終わり・handler の位置・深さ・lasti。位置は byte の offset — ``dis`` の非公開の読みと同じ varint)。"""
    iterator = iter(code.co_exceptiontable)

    def varint() -> int:
        byte = next(iterator)
        value = byte & 63
        while byte & 64:
            value <<= 6
            byte = next(iterator)
            value |= byte & 63
        return value

    try:
        while True:
            start = varint() * 2
            end = start + varint() * 2
            target = varint() * 2
            depth_and_lasti = varint()
            yield start, end, target, depth_and_lasti >> 1, bool(depth_and_lasti & 1)
    except StopIteration:
        return


def _numbered_anon_names(document: str) -> str:
    """文の中の Hy の一時変数の名(:data:`doeff_hy_bytecode_guard.records.ANON_VAR_NAME` — ``_hy_anon_46``・``_hy_anon_fn_12``)の番号を、
    文に現れる順の通し番号へ振り直す。Hy は一時変数の番号を module の通し番号で振るので、前に関数が 1 つ増えるだけで後ろの関数の
    番号がずれる。同じ名は同じ番号・違う名は違う番号になる(1 対 1 — code の中の変数の同一性は保たれる)。"""
    ranks: dict[str, str] = {}

    def renumber(found: re.Match[str]) -> str:
        name = found.group(0)
        if name not in ranks:
            ranks[name] = f"{found.group(1)}_#{len(ranks) + 1}"
        return ranks[name]

    return ANON_VAR_NAME.sub(renumber, document)


# ---- code の読みを process をまたいで覚える(agora-redesign card ki-be596ac8276f)-------------------------------------
#
# code の読み(:class:`CodeFacts`)は code object の中身と、読み方(この module の code・Python の版・hy.R の module の名を作る Hy の
# mangle の版)だけで決まる純な計算なので、
# Hy の code の置き場(code_store の種類 FACTS_SUFFIX)に code の中身(marshal の bytes の sha256)で引いて覚える — 新しい process の
# 照らしが、macro の閉包の code を毎回読み直していた(検の読み込みで歩みの約 3 割・関数 378 個 0.05〜0.06 秒。marshal と sha256 は
# 0.006 秒)。digest は覚えない(生きた値に依る — packages/doeff-hy/architecture.hy の B1)。entry は歩み 1 つ(macro か値の在処と、
# その module の file)ごとに、その歩みが使った code の読みを並べる。entry の鍵は当たりやすさだけを決め、正しさは中の code の
# sha256 で決まる。


#: code の読みの entry の鍵の印(entry の形を変えたら末尾の番号を上げる)。読み方そのもの(この module の code)の変更は、鍵に
#: 入れるこの module の file の sha256(:func:`_own_digest`)が覆い、読みが呼ぶ Hy の関数(``hy.mangle``・``slashes2dots``)の変更は
#: 鍵に入れる Hy の版が覆う。
#: 2 = 置き場を値として読むかの欄を足した形(設計検証 lt-TBEF3PAMAVQ8B3Z07MDSCA994C の盲検 B)。
#: 3 = 関数の型注釈と Hy の一時変数の番号を除いた読みと、注釈ごとの読み(行の鍵の末尾に ``!``)を並べる形(card ki-968309595391)。
FACTS_TAG = "doeff-hy/code-facts/3"

#: 名で辿れない読みの広さの値(置き場から読んだ行の確かめ)。
_READS = frozenset(read.value for read in NamespaceRead)


class CodeReadings:
    """歩み 1 つが使う code の読み — 置き場から読んだ物(code の sha256 → 読み)・この歩みが使った物・置き場に無く読み直したか。"""

    def __init__(self, stored: dict[str, CodeFacts], strict: bool = False) -> None:
        self._stored = stored
        #: 注釈ごとの読みを使うか(注釈を値として読む閉包の歩み — :func:`_code_facts`)。置き場の行の鍵は、注釈ごとの読みなら
        #: code の sha256 の末尾に ``!`` を付ける(同じ code の 2 つの読みを 1 つの entry に並べる)。
        self._strict = strict
        self.used: dict[str, CodeFacts] = {}
        self._mut_missed = False

    def facts(self, code: CodeType) -> CodeFacts:
        """関数の code の読み — process の中の覚え・置き場の覚え(前の process が同じ中身の code を読んだ物)・読み直しの順。"""
        sha = _code_sha256(code) + ("!" if self._strict else "")
        cache = _STRICT_FACTS if self._strict else _FACTS
        remembered = cache.get(id(code))
        if remembered is not None and remembered[0] is code:
            facts = remembered[1]
        elif sha in self._stored:
            facts = self._stored[sha]
            cache[id(code)] = (code, facts)
        else:
            facts = _code_facts(code, self._strict)
            self._mut_missed = True
        self.used[sha] = facts
        return facts

    @property
    def missed(self) -> bool:
        """置き場に無く読み直した code があったか(あれば歩みの後に entry を書く)。"""
        return self._mut_missed


#: process の中の code の中身の sha256 の覚え(code object の id → (code・sha256) — code を生かしておき id の使い回しを防ぐ)。
_CODE_SHAS: dict[int, tuple[CodeType, str]] = {}


def _code_sha256(code: CodeType) -> str:
    """code object の中身の sha256(marshal の bytes — 入れ子の code・定数・名の表・file の名・行番号まで入る。行番号と file の名が
    違う同じ code は別の鍵になり、読み直すだけ)。"""
    remembered = _CODE_SHAS.get(id(code))
    if remembered is not None and remembered[0] is code:
        return remembered[1]
    import marshal  # 歩みが関数に当たった時だけ読む

    sha = hashlib.sha256(marshal.dumps(code)).hexdigest()
    _CODE_SHAS[id(code)] = (code, sha)
    return sha


def _facts_entry(key: "UsedMacroKey | ValueReference") -> str | None:
    """歩み 1 つの code の読みの entry の path(置き場を使わない設定・この module の code を読めない時は None)。鍵 = 印・この module
    の code・Python の版の全部(命令の番号)・Hy の版(hy.R の module の名の mangle — 設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 A
    の付録)・在処・在処の module の file(作業木ごとに分ける — code の sha256 は file の名を含む)。"""
    import hy  # 歩みは Hy の module の照らしの中 — 読み込み済み

    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    store = code_store.store_dir()
    own = None if store is None else _own_digest()
    if store is None or own is None:
        return None
    match key:
        case UsedMacroKey(table=table, module=module, name=name):
            place = f"macro\0{table}\0{module}\0{name}"
        case ValueReference(module=module, name=name):
            place = f"value\0{module}\0{name}"
    parts = (FACTS_TAG, own, sys.version, hy.__version__, place, _module_file(module) or "")
    return code_store.entry_path(store, code_store.content_key(parts, b""), code_store.FACTS_SUFFIX)


@dataclass(frozen=True)
class OwnCode:
    """この module の file の中身の sha256(読めなければ None — 読み方の code が分からないので置き場を使わない)。"""

    sha256: str | None


#: この module の code の覚え(process に 1 度 — 鍵は "own" の 1 つだけ)。
_OWN: dict[str, OwnCode] = {}


def _own_digest() -> str | None:
    """この module の file の中身の sha256 — 読み方の code を変えた版が、前の版の読みを置き場から引かないため。"""
    known = _OWN.get("own")
    if known is None:
        try:
            with open(__file__, "rb") as stream:
                known = OwnCode(hashlib.sha256(stream.read()).hexdigest())
        except OSError:
            known = OwnCode(None)
        known = _OWN.setdefault("own", known)
    return known.sha256


#: 置き場の entry の中身の形(marshal の境界 — 欄の名と型は :class:`CodeFacts` が持ち、ここは書く時と読む時の並びだけ):
#: code の sha256・digest の形・名の表・文字列の定数・import(名・段数)・hy.R の module・名で辿れない読みの広さ・置き場の読み。
FactsRow = tuple[
    str, str, tuple[str, ...], tuple[str, ...], tuple[tuple[str, int], ...], tuple[str, ...], str, bool
]


def _stored_facts(entry: str) -> dict[str, CodeFacts]:
    """置き場の code の読みの entry を読む(無ければ空)。形の違う entry は名指して除き、空(歩みは読み直して足し直す)。"""
    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    data = code_store.read_entry(entry)
    if data is None:
        return {}
    import marshal  # 当たった時だけ読む

    try:
        rows = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        code_store.discard_entry(entry, f"marshal で読めない: {error}")
        return {}
    read = _facts_from_rows(rows)
    if read is None:
        code_store.discard_entry(entry, "code の読みの形が違う")
        return {}
    return read


def _write_facts(entry: str, used: dict[str, CodeFacts]) -> None:
    """歩みが使った code の読みを置き場へ足す(書かない設定では名指しの置き場だけ — loader_hooks の code の足し方と同じ決め。
    書けない置き場では黙って足さない — 置き場は速さのためだけ)。"""
    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    if sys.dont_write_bytecode and not code_store.store_is_named():
        return
    import marshal  # 足す時だけ読む

    rows: tuple[FactsRow, ...] = tuple(
        (
            sha,
            facts.shape,
            facts.names,
            facts.texts,
            tuple((reference.name, reference.level) for reference in facts.imports),
            facts.called_modules,
            facts.reads.value,
            facts.reads_location,
        )
        for sha, facts in sorted(used.items())
    )
    code_store.write_entry(entry, marshal.dumps(rows))


def _facts_from_rows(rows: object) -> dict[str, CodeFacts] | None:
    """置き場から読んだ並びを code の読みに組む(形が違えば None)。"""
    if not isinstance(rows, tuple):
        return None
    read: dict[str, CodeFacts] = {}
    for row in rows:
        match row:
            case (
                str() as sha,
                str() as shape,
                tuple() as names,
                tuple() as texts,
                tuple() as imports,
                tuple() as called,
                str() as reads,
                bool() as location,
            ) if (
                _strings(names)
                and _strings(texts)
                and _strings(called)
                and all(_import_pair(pair) for pair in imports)
                and reads in _READS
            ):
                read[sha] = CodeFacts(
                    shape,
                    names,
                    texts,
                    tuple(ImportReference(name, level) for name, level in imports),
                    called,
                    NamespaceRead(reads),
                    location,
                )
            case _:
                return None
    return read


def _strings(values: tuple[object, ...]) -> bool:
    """組の中身が全部文字列か(型の集合で判じる — 置き場の entry 1 つに名と文字列が数千ある)。"""
    return set(map(type, values)) <= _STR_TYPE


def _import_pair(pair: object) -> bool:
    """import の行(名・段数)の形か。"""
    match pair:
        case (str(), int()):
            return True
        case _:
            return False


#: 文字列だけの組の型の集合(:func:`_strings`)。
_STR_TYPE = frozenset({str})

#: digest の文字列 1 つの bytes の長さの綴り(8 byte の little endian)と、文字列の bytes の作り方(:meth:`_Walker.digest`)。
_LENGTH = struct.Struct("<Q")
_ENCODE = operator.methodcaller("encode", "utf-8", "surrogatepass")

_IMPORT_NAME = dis.opmap["IMPORT_NAME"]
#: IMPORT_NAME の引数から名の表の番号を出す右への shift(3.15 から下の 2 bit は lazy・eager の印 — PEP 810)。
_IMPORT_NAME_SHIFT = 2 if sys.version_info >= (3, 15) else 0
_LOAD_CONST = dis.opmap["LOAD_CONST"]
_LOAD_SMALL_INT = dis.opmap.get("LOAD_SMALL_INT", -1)
_EXTENDED_ARG = dis.EXTENDED_ARG


@dataclass(frozen=True)
class _Unit:
    """命令 1 つ(命令の番号と、EXTENDED_ARG を畳んだ引数)。"""

    op: int
    arg: int


def _units(code: CodeType) -> Iterator[_Unit]:
    """code の命令を順に挙げる(``co_code`` は特殊化の前の形で、cache の欄は 0 — 命令として読まない)。dis.get_instructions は
    macro の閉包の全部の関数で 1 回 0.3 秒かかったので、要る 2 つの命令(import と、その相対の段数)だけを読む軽い読み。"""
    raw = code.co_code
    extended = 0
    for offset in range(0, len(raw), 2):
        op, arg = raw[offset], raw[offset + 1]
        if op == _EXTENDED_ARG:
            extended = (extended | arg) << 8
            continue
        if op != 0:
            yield _Unit(op, extended | arg)
        extended = 0


def _imports(code: CodeType) -> Iterator[ImportReference]:
    """関数の中の import の命令(macro が展開の時にだけ import する補助を、閉包に入れるため)。相対の段数は import の命令の
    2 つ前の命令(段数の数・fromlist・import の順に積まれる)。import の命令を持たない code(閉包の関数のほとんど)は、命令の番号の
    並び(偶数の位置の byte)を C で探すだけで命令を読まない(macro の閉包の歩みの約 1/4 が、ここで全部の命令を読む所だった —
    agora-redesign card ki-38508e6e56fd)。"""
    if _IMPORT_NAME not in code.co_code[::2]:
        return
    previous: tuple[_Unit | None, _Unit | None] = (None, None)
    for unit in _units(code):
        if unit.op == _IMPORT_NAME:
            match previous[0]:
                case _Unit(op=op, arg=arg) if op == _LOAD_SMALL_INT:
                    level = arg
                case _Unit(op=op, arg=arg) if op == _LOAD_CONST and isinstance(
                    code.co_consts[arg], int
                ):
                    level = int(code.co_consts[arg])
                case _:
                    level = 0
            yield ImportReference(code.co_names[unit.arg >> _IMPORT_NAME_SHIFT], level)
        previous = (previous[1], unit)


def _opcode(name: str) -> int:
    """命令の番号(この Python の版に無い命令は -1)。"""
    return dis.opmap.get(name, -1)


#: 名の表の名を大域として読む命令と、引数から名の表の番号を出す右への shift(3.11 から LOAD_GLOBAL は 1 つ・3.12 から LOAD_ATTR は
#: 1 つ・LOAD_SUPER_ATTR は 2 つ)。
_GLOBAL_NAME_LOADS: dict[int, int] = {
    _opcode("LOAD_GLOBAL"): 1 if sys.version_info >= (3, 11) else 0,
    _opcode("LOAD_NAME"): 0,
    _opcode("LOAD_FROM_DICT_OR_GLOBALS"): 0,
    _opcode("IMPORT_FROM"): 0,
}
_ATTRIBUTE_NAME_LOADS: dict[int, int] = {
    _opcode("LOAD_ATTR"): 1 if sys.version_info >= (3, 12) else 0,
    _opcode("LOAD_METHOD"): 0,
    _opcode("LOAD_SUPER_ATTR"): 2,
}
#: 名の表の名を読みでなく使う命令(書き・消し・import の module の名)と、引数から名の表の番号を出す右への shift — 名で辿れない
#: 読みの印の名がこれだけに出れば読みではない。
_OTHER_NAME_USES: dict[int, int] = {
    op: shift
    for op, shift in {
        _opcode("STORE_ATTR"): 0,
        _opcode("DELETE_ATTR"): 0,
        _opcode("STORE_GLOBAL"): 0,
        _opcode("DELETE_GLOBAL"): 0,
        _opcode("STORE_NAME"): 0,
        _opcode("DELETE_NAME"): 0,
        _IMPORT_NAME: _IMPORT_NAME_SHIFT,
    }.items()
    if op != -1
}


def _namespace_read(code: CodeType) -> NamespaceRead:
    """code object 1 つ(入れ子は除く)の名で辿れない読みの広さ — 印の名を、大域として読むか属性として読むかで分ける。印の名が
    分けられる命令のどれにも出ない時(知らない命令の使い)は、安全側に MODULE と判じる。"""
    marks = _UNTRACEABLE_NAMES.intersection(code.co_names)
    if not marks:
        return NamespaceRead.NONE
    uses = tuple(_name_uses(code))
    read = _widest(tuple(_mark_read(use) for use in uses if use.name in marks))
    unexplained = marks - {use.name for use in uses}
    return NamespaceRead.MODULE if unexplained else read


class NameWay(enum.Enum):
    """名の表の名の使い方(大域の読み・属性の読み・それ以外)。"""

    GLOBAL = "global"
    ATTRIBUTE = "attribute"
    OTHER = "other"


@dataclass(frozen=True)
class NameUse:
    """code の中の名の表の名の使い 1 つ(名と使い方)。"""

    name: str
    way: NameWay


def _name_uses(code: CodeType) -> Iterator[NameUse]:
    """code の命令のうち、名の表の名を使う物。"""
    for unit in _units(code):
        if unit.op in _GLOBAL_NAME_LOADS:
            yield NameUse(code.co_names[unit.arg >> _GLOBAL_NAME_LOADS[unit.op]], NameWay.GLOBAL)
        elif unit.op in _ATTRIBUTE_NAME_LOADS:
            yield NameUse(
                code.co_names[unit.arg >> _ATTRIBUTE_NAME_LOADS[unit.op]], NameWay.ATTRIBUTE
            )
        elif unit.op in _OTHER_NAME_USES:
            yield NameUse(code.co_names[unit.arg >> _OTHER_NAME_USES[unit.op]], NameWay.OTHER)


def _mark_read(use: NameUse) -> NamespaceRead:
    """印の名の使い 1 つの読みの広さ。"""
    match use:
        case NameUse(way=NameWay.GLOBAL, name=name) if name in _MODULE_READ_GLOBALS:
            return NamespaceRead.MODULE
        case NameUse(way=NameWay.GLOBAL, name=name) if name in _OBJECT_READ_GLOBALS:
            return NamespaceRead.OBJECT
        case NameUse(way=NameWay.ATTRIBUTE, name=name) if name in _MODULE_READ_ATTRIBUTES:
            return NamespaceRead.MODULE
        case NameUse(way=NameWay.ATTRIBUTE, name=name) if name in _OBJECT_READ_ATTRIBUTES:
            return NamespaceRead.OBJECT
        case NameUse():
            return NamespaceRead.NONE


def _widest(reads: tuple[NamespaceRead, ...]) -> NamespaceRead:
    """読みの広さのうち一番広い物。"""
    if NamespaceRead.MODULE in reads:
        return NamespaceRead.MODULE
    if NamespaceRead.OBJECT in reads:
        return NamespaceRead.OBJECT
    return NamespaceRead.NONE


def _called_modules(texts: tuple[str, ...]) -> Iterator[str]:
    """macro が組む ``hy.R.<module>.<名>`` の呼び出しの module — 引用した記号は ``(. hy R <module> <名>)`` の式になり、code の
    文字列の定数には "R" の直後に <module> が初めて現れる(定数は初めて使った順に並ぶ)。1 つの記号の文字列
    (``"hy.R.<module>.<名>"``)の形も読む。読めない形(R の後に定数が無い)は空の名(歩みの側が関数の module の file で覆う)。"""
    for index, text in enumerate(texts):
        if text == "R" and "hy" in texts:
            yield _reference_module(texts[index + 1]) if index + 1 < len(texts) else ""
        elif text.startswith("hy.R."):
            for match in _HY_R.finditer(text):
                yield _reference_module(match.group(1))


_COMPILED_CALLABLES = (
    types.BuiltinFunctionType,
    types.MethodDescriptorType,
    types.WrapperDescriptorType,
    types.ClassMethodDescriptorType,
    types.MethodWrapperType,
    types.GetSetDescriptorType,
    types.MemberDescriptorType,
)

_PLAIN_VALUES = (type(None), bool, int, float, complex, str, bytes, type(Ellipsis))


def _plain(value: object) -> bool:
    """値で入れる物か — 素の値・中身が素の値の組と frozenset(入れ子も)・中身がそれだけの set。"""
    match value:
        case set():
            return all(_frozen_plain(item) for item in value)
        case _:
            return _frozen_plain(value)


def _frozen_plain(value: object) -> bool:
    """書き換えられない素の値か(素の値・中身がそれだけの組と frozenset)。"""
    match value:
        case tuple() | frozenset():
            return all(_frozen_plain(item) for item in value)
        case _:
            return isinstance(value, _PLAIN_VALUES)


def _plain_text(value: object) -> str:
    """素の値の入れ物の、並びに依らない文(集合は文の順に並べる — str の hash は process ごとに違う)。"""
    match value:
        case tuple():
            return "(" + ",".join(_plain_text(item) for item in value) + ")"
        case frozenset() | set():
            return "{" + ",".join(sorted(_plain_text(item) for item in value)) + "}"
        case _:
            return f"{type(value).__name__}:{value!r}"


class _Walker:
    """code の閉包の digest を 1 つ作る歩み。同じ関数と class は 2 度目から番号で入れる(循環と共有)。"""

    def __init__(self, sha256_of: Sha256Of, readings: CodeReadings) -> None:
        #: digest に入れる文字列(:meth:`walked` が順に綴る)。
        self._parts: list[str] = []
        self._sha256_of = sha256_of
        #: 関数の code の読み(置き場に覚えた前の process の読みを使い、この歩みが使った読みを集める)。
        self.readings = readings
        #: 辿った object の id → (順の番号・object — 歩みの間 object を生かし、id の使い回しを防ぐ)。
        self._seen: dict[int, tuple[int, object]] = {}
        #: 参照した module(名 → module)と、入れた属性の名。
        self._modules: dict[str, ModuleType] = {}
        self._attributes: dict[str, set[str]] = {}
        #: 辿った関数の code の文字列の定数(``getattr(m, "名")``・``d["名"]`` の名の候補)。
        self._texts: set[str] = set()
        #: 辿った物の持ち主の module の名 → file(digest の覚えが有効かを決める・file 単位で覆う module の記録の行)。
        self.files: dict[str, str] = {}
        #: import できなかった module の名(digest には名だけが入り、記録の file の行で覆う)。
        self.unimportable: set[str] = set()
        #: import の途中の module か import できない module に出会ったか、今の disk から読む file が module を実行した時から
        #: 変わっていたか(その digest は import の進み具合か、process の中の古い値に依るので、覚えず記録にも入れない)。
        self._mut_partial = False
        #: 環境変数の読みに当たったか(その digest は環境変数の値を写さないので照らせない — 覚えず、記録の行は印 ENVIRONMENT_READ)。
        self._mut_environment = False
        #: source の文で覆った大域(module の名・大域の名 — 2 度目は印だけ入れる)。
        self._bound_names: set[tuple[str, str]] = set()
        #: 初めて出会った関数の code の中身の在処(:attr:`_parts` の番号 → 関数の番号・関数・関数の中身を値として読むか — 番号の順)。
        self._slots: dict[int, _SlotPlace] = {}
        #: 歩いている途中の関数と class の番号(:attr:`_seen` の順の番号 — 一番後ろが今歩いている物)。
        self._mut_stack: tuple[int, ...] = ()
        #: 関数と class の番号 → その中身を歩く間に出会った関数と class の番号(初めて・2 度目のどちらも)。
        self._edges: dict[int, set[int]] = {}
        #: 束縛の文の先(:meth:`_bound` の中)で出会った関数と class の番号 — その物とそこから届く物の関数の中身は絞らない。
        self._state_roots: set[int] = set()
        #: :meth:`_bound` の入れ子の深さ(0 = 束縛の文の外)。
        self._mut_state_depth = 0

    def walked(self) -> "ClosureWalk":
        """辿った物の材料 — 参照した module の、文字列の定数の名の属性を入れ終えてから。積んだ文字列は、それぞれの bytes の長さ
        (8 byte の little endian)を前に置いて綴り(文字列ごとに hash を呼ぶと、検の読み込みで 2.8 万回 — card ki-be596ac8276f)、
        関数の code の中身の所で切る(中身を覆うかは記録と照らしが決める — :meth:`ClosureWalk.digest`)。"""
        self._attributes_named_by_texts()
        encoded = list(map(_ENCODE, self._parts))
        framed = list(
            chain.from_iterable(zip(map(_LENGTH.pack, map(len, encoded)), encoded, strict=True))
        )
        cuts = (-1, *self._slots, len(encoded))
        segments = tuple(
            b"".join(framed[2 * (start + 1) : 2 * end]) for start, end in pairwise(cuts)
        )
        self._parts.clear()
        places = tuple(self._slots.values())
        forced_nodes = self._reached_from(self._state_roots)
        return ClosureWalk(
            segments,
            tuple(place.slot for place in places),
            _bits(index for index, place in enumerate(places) if place.node in forced_nodes),
            _bits(index for index, place in enumerate(places) if place.reads_code),
        )

    def _reached_from(self, roots: set[int]) -> frozenset[int]:
        """roots から、中身を歩く間に出会った物を辿って届く関数と class の番号(roots も入る)。"""
        reached: frozenset[int] = frozenset()
        frontier = frozenset(roots)
        while frontier:
            reached |= frontier
            frontier = (
                frozenset(chain.from_iterable(self._edges.get(node, ()) for node in frontier))
                - reached
            )
        return reached

    def _attributes_named_by_texts(self) -> None:
        """名前で引かない読み(``getattr(m, "名")``・文字列の key)の先 — 歩みで出会った文字列の定数を名として、参照した module の
        その名の属性を入れる(入れた属性が新しい module と文字列を持ち込む間くり返す)。読みの関数と module の参照が
        別の関数にあっても(module を引数で渡す)覆う。"""
        while True:
            # 名前空間に在る文字列だけを集合の積で先に選ぶ(_text_attribute は名前空間に無い名を偽とする — module × 文字列を
            # 1 つずつ判じると、検の読み込みで 3 万回を越えた・card ki-be596ac8276f)。並びは前と同じ(module の名・文字列の順)。
            pending = tuple(
                (name, text)
                for name in sorted(self._modules)
                for text in sorted(
                    self._texts.intersection(vars(self._modules[name])) - self._attributes[name]
                )
                if _text_attribute(self._modules[name], text)
            )
            if not pending:
                return
            for name, text in pending:
                if text not in self._attributes[name]:
                    self._attribute(self._modules[name], text, ())

    def _feed(self, *parts: str) -> None:
        """digest に文字列を足す(:meth:`walked` が長さを前に置いて綴る — 切れ目の違う 2 つの並びが同じ digest にならない)。"""
        self._parts.extend(parts)

    def _first_visit(self, value: object) -> bool:
        """初めて辿る object か(2 度目からは番号だけを入れる — 循環で止まらず、共有と複製を分ける)。どちらの時も、今歩いている
        関数か class から出会った事と、束縛の文の先で出会った事を覚える(絞らない関数を決める — :meth:`walked`)。"""
        seen = self._seen.get(id(value))
        node = len(self._seen) if seen is None else seen[0]
        if self._mut_stack:
            self._edges.setdefault(self._mut_stack[-1], set()).add(node)
        if self._mut_state_depth:
            self._state_roots.add(node)
        if seen is not None:
            self._feed("seen", str(seen[0]))
            return False
        self._seen[id(value)] = (node, value)
        return True

    def _node(self, value: object) -> int:
        """辿った object の番号(:meth:`_first_visit` の後に引く)。"""
        return self._seen[id(value)][0]

    def _note(self, module: ModuleType | None) -> None:
        """辿った物の持ち主の module の file を覚える(digest の覚えの有効の判じと、file 単位の記録の行に使う)。"""
        file = None if module is None else vars(module).get("__file__")
        if module is not None and isinstance(file, str):
            self.files[module.__name__] = file

    def _against_executed(self, file: str) -> None:
        """今の disk から読む部分(file の sha256・値を覆う文)が、process の中の module の値と同じ時点の物かを確かめる — module を
        実行した後に file が書き換わっていれば(常駐の process)、この digest は展開が使った値を写さないので、import の途中の状態と
        同じく覚えず記録にも入れない(記録の行は None — 新しい process は古いと判じて展開し直す)。設計検証
        lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 A(基準版 cf7ea9cc0 から在った・card ki-be596ac8276f)。"""
        if not loader_hooks.executed_source_is_current(file):
            self._mut_partial = True

    def _note_environment(self, reads: bool) -> None:
        """環境変数の読みに当たった事を覚える — この digest を照らせない印にするため(:func:`_remembered`)。"""
        if reads:
            self._mut_environment = True

    def _note_location(self, reads: bool) -> None:
        """module の置き場を値として読む code か束縛の文に当たった事を覚える — 置き場から辿る file(隣の data・stub)の中身を歩みは
        覆わないので、環境変数の読みと同じく照らせない印にする(:data:`_LOCATION_NAMES`・設計検証
        lt-TBEF3PAMAVQ8B3Z07MDSCA994C の盲検 B)。"""
        self._note_environment(reads)

    def _fold(self, module: ModuleType | None) -> None:
        """安全側: module の file の sha256 を digest に足す(読めなければ毎回違う印 — いつも古いと判じられる)。"""
        file = None if module is None else vars(module).get("__file__")
        if module is None or not isinstance(file, str):
            self._feed("file", "none" if module is None else module.__name__)
            return
        self.files[module.__name__] = file
        self._against_executed(file)
        digest = self._sha256_of(file)
        self._feed("file", module.__name__, digest if digest is not None else os.urandom(16).hex())

    def _fold_opaque(self, module: ModuleType | None) -> None:
        """辿れない値(大域の名で引けない値・束縛を辿れない大域・名で辿れない読みの先)を、持ち主の module の file で覆う
        (:meth:`_fold`)。file は持ち主の source が読み込みの時に読んだ環境変数を写さないので、source が環境変数を読みうるなら
        照らせない印にする(設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464 の盲検 A — 環境変数から組んだ表を既定値・閉包の cell に持つ形)。"""
        self._fold(module)
        file = None if module is None else vars(module).get("__file__")
        names = bound_source.source_names(file) if isinstance(file, str) else None
        self._note_environment(names is not None and _source_reads_environment(names))

    def _lazy_module(
        self, module_name: str, home: ModuleType | None, names: tuple[str, ...]
    ) -> ModuleType | None:
        """関数の中の import の先・package の下の module・hy.R の先 — import して、名の表の名の属性を入れる。import できない
        時(循環する import の途中の module を require する物など)は名だけを入れ、import の途中の状態の digest として扱う
        (覚えず・記録の行は file で比べる)。読めない名は関数の module の file で覆う。"""
        if not module_name:
            self._fold(home)
            return None
        if _named_only(module_name):
            self._feed("module", module_name)
            # 関数の中で import した os で環境変数を読む(大域に os が無くても)。
            self._note_environment(
                module_name in _ENVIRONMENT_MODULES and not _ENVIRONMENT_NAMES.isdisjoint(names)
            )
            return None
        match _import_for_digest(module_name):
            case ModuleType() as module:
                self._module(module, names)
                return module
            case NotImportable():
                self._feed("unimportable", module_name)
                self.unimportable.add(module_name)
                self._mut_partial = True
                return None

    def function(self, function: FunctionType) -> None:
        """関数の code と、名で引く大域・既定値・閉包の cell を辿る。"""
        module_name = function.__module__ if isinstance(function.__module__, str) else ""
        if _named_only(module_name):
            self._feed("function-name", module_name, function.__qualname__)
            self._note_environment(_environment_object(function))  # os から import した getenv など
            return
        if not self._first_visit(function):
            return
        outer = self._mut_stack
        self._mut_stack = (*outer, self._node(function))
        try:
            self._function_body(function, module_name)
        finally:
            self._mut_stack = outer

    def _function_body(self, function: FunctionType, module_name: str) -> None:
        """初めて出会った関数の中身(code の中身は在処を覚え、覆うかを記録と照らしが決める — :class:`ClosureWalk`)。"""
        home = sys.modules.get(module_name)
        self._note(home)
        facts = self.readings.facts(function.__code__)
        self._texts.update(facts.texts)
        self._feed("function", module_name, function.__qualname__)
        self._slots[len(self._parts)] = _SlotPlace(
            self._node(function),
            CodeSlot(function.__code__, facts.shape),
            _reads_code(facts) or _reads_code_by_alias(function.__globals__, facts.names),
        )
        self._feed(facts.shape)
        # 展開の時か読み込みの時に、code が環境変数を読む・module の置き場を値として読む。
        self._note_environment(_reads_environment(function.__globals__, facts.names))
        self._note_location(facts.reads_location)
        self._value(function.__defaults__, home, facts.names)
        # keyword だけの引数の既定値は名 → 値の dict(名の順に入れる — dict のまま入れると辿れない値になる)。
        for keyword, default in sorted((function.__kwdefaults__ or {}).items()):
            self._feed("kwdefault", keyword)
            self._value(default, home, facts.names)
        for cell in function.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:  # 束縛の前の cell
                self._feed("empty-cell")
                continue
            self._value(content, home, facts.names)
        namespace = function.__globals__
        # 大域の名の在処(関数の大域が module の名前空間そのものの時だけ — 辿れない値を、その名の source の文で覆う)。
        owner = home if home is not None and vars(home) is namespace else None
        for name in facts.names:
            if name in namespace:
                self._feed("global", name)
                if name in _LOCATION_NAMES:
                    self._feed("location")
                    continue
                self._value(
                    namespace[name],
                    home,
                    facts.names,
                    None if owner is None else GlobalName(owner, name),
                )
        package = namespace.get("__package__")
        for reference in facts.imports:
            self._lazy_module(
                _import_target(reference, package if isinstance(package, str) else None),
                home,
                facts.names,
            )
        for called in facts.called_modules:
            self._called_macros(called, facts.texts, home)
        match facts.reads:
            case NamespaceRead.MODULE:
                self._fold_opaque(home)
                self._fold_referenced_modules(namespace, facts.names)
            case NamespaceRead.OBJECT:
                self._fold_referenced_modules(namespace, facts.names)
            case NamespaceRead.NONE:
                pass

    def _fold_referenced_modules(
        self, namespace: dict[str, object], names: tuple[str, ...]
    ) -> None:
        """関数が大域の名で参照する module の file の sha256 を足す(名で辿れない読みの先になりうる module)。"""
        for name in names:
            value = namespace.get(name)
            if isinstance(value, ModuleType) and not _named_only(value.__name__):
                self._fold_opaque(value)

    def outside_value(self, module: ModuleType, value: object) -> None:
        """macro の外で展開の結果を変える値(module の属性)を入れる。"""
        self._feed("outside", module.__name__)
        self._note(module)
        self._value(value, module, ())

    def _called_macros(
        self, module_name: str, texts: tuple[str, ...], home: ModuleType | None
    ) -> None:
        """macro が組む hy.R の呼び出しの先 — module の macro のうち、code の文字列の定数に名が現れる物を全部辿る(定数の並びから
        名を 1 つに決められない — 前に使った定数と重なると並びに現れない)。"""
        module = self._lazy_module(module_name, home, ())
        table = None if module is None else vars(module).get(MACRO_TABLE)
        if not isinstance(table, dict):
            return
        for text in texts:
            macro = dict.get(table, _mangle(text)) if text else None
            if isinstance(macro, FunctionType):
                self._feed("hy.R", module_name, text)
                self.function(macro)

    def module_content(self, module: ModuleType) -> None:
        """module で定義された関数と class と素の値(名の順)。"""
        self._feed("module-content", module.__name__)
        self._note(module)
        if _initializing(module):
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        namespace = vars(module)
        for name in sorted(namespace):
            value = namespace[name]
            defined_here = (
                isinstance(value, (FunctionType, type)) and value.__module__ == module.__name__
            )
            if defined_here or isinstance(value, _PLAIN_VALUES):
                self._feed("member", name)
                self._value(value, module, ())

    def _value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None" = None,
    ) -> None:
        """値 1 つを入れる(型ごとの規則は module の docstring)。bound = 値が module の大域の名で引かれた時の在処(辿れない値を
        その名の source の文で覆う)。"""
        match value:
            case None | bool() | int() | float() | complex() | str() | bytes():
                kind = type(value)
                self._feed("value", f"{kind.__module__}.{kind.__qualname__}", repr(value))
            case tuple():
                # 組は object の同一で畳まない — 同じ中身の組が共有されるかは process ごとに違う(定数の組の共有)。
                self._feed("tuple", str(len(value)))
                for item in value:
                    self._value(item, home, names)
            case frozenset() | set() if _plain(value):
                # 中身が素の値だけの集合は値で入れる(Hy の #{…} の定数の表は set になる)。
                self._feed(type(value).__name__, _plain_text(value))
            case FunctionType():
                self.function(value)
            case type():
                self._class(value)
            case ModuleType():
                self._module(value, names)
            case _:
                self._wrapped_value(value, home, names, bound)

    def _wrapped_value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None",
    ) -> None:
        """関数を包む値(staticmethod・property・partial・bound method)・enum・C の関数・Hy の値・辿れない値を入れる。"""
        match value:
            case staticmethod() | classmethod():
                self._feed(type(value).__name__)
                self._value(value.__func__, home, names)
            case property():
                self._feed("property")
                for accessor in (value.fget, value.fset, value.fdel):
                    self._value(accessor, home, names)
            case functools.partial():
                self._feed("partial")
                self._value(value.func, home, names)
                self._value(value.args, home, names)
                self._value(value.keywords, home, names)
            case types.MethodType():
                self._feed("method")
                self._value(value.__func__, home, names)
                self._value(value.__self__, home, names)
            case enum.Enum():
                self._feed("enum", value.name)
                self._value(value.value, home, names)
                self._class(type(value))
            case _ if type(value).__qualname__ == "_tuplegetter" and type(value).__module__ in (
                "collections",
                "_collections",
            ):
                # namedtuple の欄の読み(``Alias for field number N`` — 欄の番号だけが振る舞い)。
                self._feed("tuplegetter", str(value.__doc__))
            case _ if isinstance(value, _COMPILED_CALLABLES):
                self._compiled(value, home)
            case _ if type(value).__module__.partition(".")[0] == "hy":
                kind = type(value)
                self._feed("hy-value", kind.__qualname__, repr(value))
            case _:
                self._state_value(value, home, names, bound)

    def _state_value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None",
    ) -> None:
        """関数を包まない値のうち、中身を値で入れられない物(ContextVar・frozen の dataclass の値・表・辿れない object)。"""
        match value:
            case contextvars.ContextVar():
                # 名と既定値で覆う。今の値は入れない — 実行の状態で、展開の結果を決める表ではない(型検査の展開の印
                # TYPE_CHECK_EXPANSION が真の間の展開は記録を付けない — loader_hooks)。
                self._feed("context-variable", value.name)
                self._context_default(value, home, names)
            case _ if _environment_object(value):
                # 環境変数の表そのもの(os から別名で import した environ・既定値・閉包の cell)— 中身は process の状態で、束縛の
                # 文も file も覆わない。
                self._feed("environment")
                self._note_environment(True)
            case _ if _frozen_dataclass_instance(value):
                # frozen の dataclass の値(binding_forms の _TOP・ModuleNames() の既定値など)— class と欄の値で入れる。
                self._feed("frozen-instance")
                self._class(type(value))
                for field in dataclasses.fields(value):
                    self._feed("field", field.name)
                    self._value(getattr(value, field.name), home, names)
            case _ if bound is not None:
                # 辿れない値を module の大域の名で引いた — その名を束縛し書き換える source の文で覆う。list と dict もここ。
                # process の中の覚え(doeff_hy.macros の _RESUME_ANALYSIS_CACHE は object の id の組を鍵に持つ dict・呼ばれた順を
                # 積む list)の中身は実行の途中で変わり、中身を入れると同じ macro の digest が process ごとに違って照らす度に
                # 古いと判じる。初めの中身と書き換えは source に書いてある。
                self._feed("opaque", type(value).__module__, type(value).__qualname__)
                self._bound(bound)
            case _:
                # 辿れない値を大域の名でなく引いた(既定値・閉包の cell・組の中・class の値)— 持ち主の module の file で覆う
                # (source が環境変数を読みうるなら照らせない印)。
                self._feed("opaque", type(value).__module__, type(value).__qualname__)
                self._fold_opaque(home)

    def _context_default(
        self,
        variable: "contextvars.ContextVar[object]",
        home: ModuleType | None,
        names: tuple[str, ...],
    ) -> None:
        """ContextVar の既定値(空の Context の中で読む — 既定値が無ければ無いことを入れる)。素の値は値で、そうでなければ型の名で
        入れる。"""
        try:
            default = contextvars.Context().run(variable.get)
        except LookupError:
            self._feed("no-default")
            return
        if _plain(default):
            self._value(default, home, names)
        else:
            kind = type(default)
            self._feed("default-type", kind.__module__, kind.__qualname__)

    def _bound(self, bound: "GlobalName") -> None:
        """module の大域 1 つを、その名を束縛し書き換える source の文で覆う(:mod:`doeff_hy_bytecode_guard.bound_source`)。
        文が読む名は同じ規則で辿り、本体でその名を書き換えうる top-level の定義はその関数か class を辿り、import で束縛する名は
        import の先の module で辿る。source を読めない・束縛が見つからない・辿れない束縛がある時は、module の file で覆う。
        ここで出会う関数と class(と、そこから届く物)は値を作った code — 展開の外(読み込みの時・前の展開)で実行されて値を
        作るので、その関数の code の中身は絞らない(:meth:`walked`)。"""
        module, name = bound.module, bound.name
        self._feed("bound", module.__name__, name)
        if (module.__name__, name) in self._bound_names:
            return
        self._bound_names.add((module.__name__, name))
        self._mut_state_depth += 1
        try:
            self._bound_statements(module, name)
        finally:
            self._mut_state_depth -= 1

    def _bound_statements(self, module: ModuleType, name: str) -> None:
        """大域 1 つの束縛の文・書き換える関数・import の先を辿る(:meth:`_bound` の中身)。"""
        self._note(module)
        namespace = vars(module)
        file = namespace.get("__file__")
        if isinstance(file, str):
            self._against_executed(file)
        binding = bound_source.binding_of(file, name) if isinstance(file, str) else None
        if binding is None or binding.unresolved or not (binding.bound or binding.imports):
            self._fold_opaque(module)
            return
        for statement in binding.statements:
            self._feed("statement", statement.text)
            # 読み込みの時に文が環境変数を読んで値を組む(Hy の文は記号の各部分を loads に持つ)— 文の text は環境変数の値を
            # 持たない(付録 3 の形)。
            self._note_environment(
                _reads_environment(namespace, (*statement.loads, *statement.attributes))
            )
            # 読み込みの時に文が置き場から隣の file を読んで値を組む(``TABLE = json.loads(Path(__file__).with_name(…)…)``)。
            self._note_location(
                not _LOCATION_NAMES.isdisjoint((*statement.loads, *statement.attributes))
            )
            for load in statement.loads:
                if load != name and load in namespace:
                    self._feed("load", load)
                    self._value(
                        namespace[load], module, statement.attributes, GlobalName(module, load)
                    )
        for mutator in binding.mutators:
            value = namespace.get(mutator)
            self._feed("mutator", mutator)
            if isinstance(value, (FunctionType, type)):
                self._value(value, module, ())
            else:
                self._fold_opaque(module)
        for imported in binding.imports:
            self._imported_binding(module, imported)

    def _imported_binding(self, module: ModuleType, imported: bound_source.ImportedName) -> None:
        """import で束縛した大域を、import の先の module の同じ名で辿る(module そのものの import は module の参照として)。"""
        package = vars(module).get("__package__")
        target = _import_target(
            ImportReference(imported.module, imported.level),
            package if isinstance(package, str) else None,
        )
        self._feed("imported", target, imported.name)
        source = None if not target or _named_only(target) else sys.modules.get(target)
        if not target:
            self._fold_opaque(module)
        elif source is None:
            # 標準 library・Hy 自身(版で覆う)か、読み込まれていない module(import の名が値と合わない)。
            if not _named_only(target):
                self._fold_opaque(module)
        elif not imported.name:
            self._module(source, ())
        elif imported.name in vars(source):
            self._value(vars(source)[imported.name], source, (), GlobalName(source, imported.name))
        else:
            self._fold_opaque(module)

    def _class(self, cls: type) -> None:
        """class の基底と中身(method・class の値)を辿る。"""
        module_name = cls.__module__ if isinstance(cls.__module__, str) else ""
        if _named_only(module_name):
            self._feed("class-name", module_name, cls.__qualname__)
            return
        if not self._first_visit(cls):
            return
        outer = self._mut_stack
        self._mut_stack = (*outer, self._node(cls))
        try:
            self._class_body(cls, module_name)
        finally:
            self._mut_stack = outer

    def _class_body(self, cls: type, module_name: str) -> None:
        """初めて出会った class の基底と中身。"""
        home = sys.modules.get(module_name)
        self._note(home)
        self._feed("class", module_name, cls.__qualname__)
        if not _python_source(home):
            self._extension_class(cls, home)
            return
        for base in cls.__bases__:
            self._value(base, home, ())
        members = vars(cls)
        for name in sorted(members):
            if name in _CLASS_SKIP:
                continue
            self._feed("member", name)
            # 3.15 の dataclass の __doc__ は、初めて読む時に署名から文を作る記述子(dataclasses._AutoDocstring)— class から読んだ
            # 文で入れる(3.14 までと同じ値・記述子のままでは辿れない値として module の file で覆う)。
            value = cls.__doc__ if name == "__doc__" else members[name]
            self._value(value, home, ())

    def _extension_class(self, cls: type, home: ModuleType | None) -> None:
        """拡張の module の class(Python の source の無い class)を、.so の file の全体ではなく名と公開の形で覆う — 基底(同じ
        規則で辿る)と、属性の名の並びと種類(method・記述子・値 — 素の値はその値・そうでなければ型の名)。"""
        self._feed("extension-class")
        for base in cls.__bases__:
            self._value(base, home, ())
        members = vars(cls)
        for name in sorted(members):
            if name in _CLASS_SKIP:
                continue
            self._feed("member", name, *_public_kind(members[name]))

    def _module(self, module: ModuleType, names: tuple[str, ...]) -> None:
        """module の参照 — 名と、code の名の表に出る属性(``module.名`` の読み)。package の下の module は file 単位で覆う
        (読み込まれたかで属性に現れるかが変わる)。文字列の定数の名の属性は歩みの終わりに入れる。"""
        self._feed("module", module.__name__)
        if _named_only(module.__name__):
            return
        if _initializing(module):
            # import の途中の module(展開する module 自身や、それを import している module)の名前空間はまだ揃っていない —
            # 属性を読むと、同じ macro の digest が import の進み具合で変わる。file の sha256 で覆い、この digest は覚えない。
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        if module.__name__ not in self._modules:
            self._modules[module.__name__] = module
            self._attributes[module.__name__] = set()
            self._note(module)
        for name in names:
            if name in self._attributes[module.__name__]:
                continue
            submodule = _submodule(module, name)
            if submodule is not None:
                self._attributes[module.__name__].add(name)
                self._feed("attribute", module.__name__, name)
                self._lazy_module(submodule, module, names)
            elif name in vars(module):
                self._attribute(module, name, names)

    def _attribute(self, module: ModuleType, name: str, names: tuple[str, ...]) -> None:
        """module の属性 1 つを入れる(module ごとに 1 度 — 置き場を名指すだけの属性は値でなく印だけ・:data:`_LOCATION_NAMES`)。"""
        self._attributes[module.__name__].add(name)
        self._feed("attribute", module.__name__, name)
        if name in _LOCATION_NAMES:
            self._feed("location")
            return
        self._value(vars(module)[name], module, names, GlobalName(module, name))

    def _compiled(self, value: object, home: ModuleType | None) -> None:
        """C で書かれた関数と記述子 — 名を入れ、標準 library と組み込みでなければその拡張の module の file の sha256 を足す。"""
        owner = _compiled_owner(value)
        qualname = value.__qualname__ if isinstance(value, _COMPILED_CALLABLES) else ""
        self._feed("compiled", owner or "", str(qualname))
        if owner is None:
            self._fold(home)
        elif not _named_only(owner):
            self._fold(sys.modules.get(owner))


@dataclass(frozen=True)
class GlobalName:
    """値を引いた module の大域の名(辿れない値をその名の source の文で覆うため)。"""

    module: ModuleType
    name: str


@dataclass(frozen=True)
class _SlotPlace:
    """歩みが初めて出会った関数 1 つの在処 — 関数の番号(:attr:`_Walker._seen` の順)・関数の code と中身の digest・関数の中身を
    値として読む code か(:func:`_reads_code`)。"""

    node: int
    slot: CodeSlot
    reads_code: bool


#: 関数の中身を値として読む名(属性の読み・大域の名)— 呼ばない関数の code・既定値・説明の文・引数の形・注記を読む。この名を
#: 持つ関数の中身を覆う(展開で実行された・絞らない)時は、閉包の関数の中身を全部覆う(実行しない関数の中身が展開の結果に効く)。
#: 文字列の定数(``getattr(f, "__code__")`` の名)としても読む。ただし ``__doc__`` は名の表だけで見る — 展開の code の文字列の
#: 定数 "__doc__" は展開の結果に説明を書く印(:data:`_TEXT_SKIP` と同じ)。
_CODE_READ_NAMES = frozenset(
    {
        "__code__",
        "__defaults__",
        "__kwdefaults__",
        "__closure__",
        "__doc__",
        "__signature__",
        "__text_signature__",
        "__annotations__",
        "__annotate__",
        "signature",
        "getsource",
        "getsourcelines",
        "findsource",
        "getfullargspec",
        "getdoc",
        "getcomments",
        "getclosurevars",
        "getcallargs",
        "get_annotations",
        "get_type_hints",
        "get_instructions",
        "Bytecode",
        "code_info",
        "show_code",
        # 標準 library の中で注釈を読む物(中は歩かない — 名で出会った時に注釈ごとの digest にする)。
        "singledispatch",
        "singledispatchmethod",
        "register",
        "validate_call",
        "validate_arguments",
        "TypeAdapter",
    }
)
_CODE_READ_TEXTS = _CODE_READ_NAMES - {"__doc__"}


def _reads_code(facts: CodeFacts) -> bool:
    """関数の code が、関数の中身を値として読むか(:data:`_CODE_READ_NAMES`)。"""
    return not (
        _CODE_READ_NAMES.isdisjoint(facts.names) and _CODE_READ_TEXTS.isdisjoint(facts.texts)
    )


def _reads_code_by_alias(namespace: Mapping[str, object], names: tuple[str, ...]) -> bool:
    """関数が大域の名で呼ぶ関数の本来の名が、関数の中身を読む名(:data:`_CODE_READ_NAMES`)か — ``from inspect import signature as sig``
    の別名は、名の表に ``sig`` しか現れない。"""
    return any(
        isinstance(value := namespace.get(name), (FunctionType, types.BuiltinFunctionType, types.MethodType))
        and getattr(value, "__name__", None) in _CODE_READ_NAMES
        for name in names
    )


def _bits(indexes: Iterator[int]) -> int:
    """番号の並びを bit にした数(番号 i なら bit i が 1)。"""
    return functools.reduce(operator.or_, (1 << index for index in indexes), 0)


def _frozen_dataclass_instance(value: object) -> bool:
    """frozen の dataclass の値か(class ではなく)。"""
    if isinstance(value, type) or not dataclasses.is_dataclass(value):
        return False
    params = vars(type(value)).get("__dataclass_params__")
    return isinstance(params, dataclasses._DataclassParams) and params.frozen


def _public_kind(member: object) -> tuple[str, ...]:
    """拡張の class の属性 1 つの種類(method・記述子・値 — 素の値は値の文・そうでなければ型の名)。"""
    match member:
        case (
            FunctionType()
            | staticmethod()
            | classmethod()
            | types.BuiltinFunctionType()
            | types.MethodDescriptorType()
            | types.WrapperDescriptorType()
            | types.ClassMethodDescriptorType()
            | types.MethodWrapperType()
        ):
            return ("method",)
        case types.GetSetDescriptorType() | types.MemberDescriptorType() | property():
            return ("descriptor",)
        case _ if _plain(member):
            return ("value", _plain_text(member))
        case _:
            kind = type(member)
            return ("value-type", kind.__module__, kind.__qualname__)


def _initializing(module: ModuleType) -> bool:
    """module が import の途中か(importlib が spec に立てる印 — 本体の実行が終わるまで真)。"""
    spec = vars(module).get("__spec__")
    return spec is not None and vars(spec).get("_initializing") is True


#: package の下の module の名が import の探索で見つかるかの覚え(process の中 — module の名 → 見つかったか)。
_SUBMODULES: dict[str, bool] = {}


def _submodule(module: ModuleType, name: str) -> str | None:
    """module が package で、名がその下の module なら、その module の名(読み込み済みかに依らない — 読み込まれた package の
    下の module だけが属性に現れるので、属性で決めると同じ macro の digest が process の import の順で変わる)。"""
    if "__path__" not in vars(module) or not name.isidentifier():
        return None
    full = f"{module.__name__}.{name}"
    value = vars(module).get(name)
    if isinstance(value, ModuleType):
        return full if value.__name__ == full else None
    found = _SUBMODULES.get(full)
    if found is None:
        try:
            found = (
                importlib.util.find_spec(full) is not None
            )  # 本体は実行しない(package は読み込み済み)
        except (ImportError, ValueError):
            found = False
        _SUBMODULES[full] = found
    return full if found and name not in vars(module) else None


#: 文字列の定数の名の候補から外す module の属性 — module の説明の文。展開の code の "__doc__" は展開の結果の関数や class に
#: 説明を書く印で、module の説明の文は展開の結果に効かない(入れると、doeff_hy の record_runtime の docstring の 1 行の変更で
#: defhandler の使い手が全部古いと判じられた — card ki-70d92767a917 の子)。
_TEXT_SKIP = frozenset({"__doc__"})

#: module の置き場を名指すだけの属性(import の仕組みが入れる source と bytecode の file の path)— 大域の名・module の属性・
#: 文字列の定数の名のどれで出会っても、値でなく印だけを入れる。値は作業木の置き場で変わり、同じ中身の code の振る舞いは
#: 変えない(記録は別の木へ引き継いで照らす — #2598)。入れると、日次の全体検証が回ごとに別の dir に置いた doeff で、
#: 型検査の展開の後処理(static_check の project — `module.__file__ = …` が名の表に __file__ を出す)の digest が回ごとに
#: 変わり、保存した展開が毎回全部外れた(agora-redesign card ki-1475765a3f18)。path の綴りは path の先の file の中身を
#: 覆わないので、外しても覆う物は減らない(module の code と値は、それを辿る規則が中身で覆う)。
#: 置き場を値として読む code(大域の読み・属性の読み・文字列の定数の名)か束縛の文は、その先の file(隣の data・stub)を読みうるが、
#: 歩みはその中身を覆わない — 環境変数の読みと同じく照らせない印にする(_note_location・設計検証 lt-TBEF3PAMAVQ8B3Z07MDSCA994C の
#: 盲検 B)。作った module に書くだけ(STORE_ATTR)は読みではない。
_LOCATION_NAMES = frozenset({"__file__", "__cached__"})


def _text_attribute(module: ModuleType, text: str) -> bool:
    """文字列の定数の名の属性を入れるか — module の名前空間にあり、module の説明の文(:data:`_TEXT_SKIP`)でも package の下の
    module でもない物(package の下の module は読み込まれたかで属性に現れるかが変わる — 名の表の名なら :func:`_submodule` が
    file 単位で覆う)。"""
    if text in _TEXT_SKIP or text not in vars(module):
        return False
    value = vars(module)[text]
    return not (isinstance(value, ModuleType) and value.__name__ == f"{module.__name__}.{text}")


def _compiled_owner(value: object) -> str | None:
    """C で書かれた関数・記述子の持ち主の module の名(分からなければ None)。"""
    match value:
        case (
            types.MethodDescriptorType()
            | types.WrapperDescriptorType()
            | types.ClassMethodDescriptorType()
            | types.GetSetDescriptorType()
            | types.MemberDescriptorType()
        ):
            return value.__objclass__.__module__
        case types.MethodWrapperType():
            return type(value.__self__).__module__
        case types.BuiltinFunctionType():
            owner = value.__module__
            if isinstance(owner, str):
                return owner
            bound = value.__self__
            return bound.__name__ if isinstance(bound, ModuleType) else type(bound).__module__
        case _:
            return None


def _python_source(module: ModuleType | None) -> bool:
    """module の class を中身(基底・method の本体)で辿れるか — 偽は、拡張の module(file の末尾が標準の
    importlib.machinery.EXTENSION_SUFFIXES のどれか)と file の無い module だけ。source の拡張子の写しで判じない: Hy の拡張子を
    定義点(code_store.HY_SOURCE_SUFFIXES)に足した時、写しの直し忘れでその module の class を公開の形だけで覆い、method の本体の
    変更を見逃した(card ki-38508e6e56fd の設計検証の盲検 A)。知らない末尾は中身で辿る側(安全側)に倒す。"""
    file = None if module is None else vars(module).get("__file__")
    return isinstance(file, str) and not file.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES))


def _import_target(reference: ImportReference, package: str | None) -> str:
    """import の命令が読む module の名(相対なら関数の package から解く — 解けなければ空)。import はしない。"""
    if reference.level <= 0:
        return reference.name
    if not package:
        return ""
    try:
        return importlib.util.resolve_name("." * reference.level + reference.name, package)
    except ImportError:
        return ""


def source_text(data: object, path: str) -> str | bytes | None:
    """compile した source の文 — 渡された bytes / str か、file から読んだ物(読めなければ None)。"""
    if isinstance(data, (str, bytes)):
        return data
    try:
        with io.open_code(path) as source:
            return source.read()
    except OSError:
        return None
