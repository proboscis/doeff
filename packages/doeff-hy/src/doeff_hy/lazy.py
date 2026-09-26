"""lazy val / lazy var の実行時の入れ物(ADR-DOE-HY-006)。

defk・deftest・defhandler の節の本体で `(lazy val x 式)` と書くと、macro はその呼び出しの
局所に LazyCell を 1 つ作り、`x` の参照を「用意ができていれば覚えた値・まだなら force を
実行して覚える」式に書き換える(doeff_hy/binding_forms.py)。

- 入れ物は呼び出しごとに作る局所の値(本体の局所変数と同じ寿命)。セッションをまたぐ状態は
  持たない — それは defhandler の session val / session var の役目(状態の効果 Get / Put を通す)。
- 初回の評価が例外で終わったら覚えない(次に使った時にもう一度評価する — Scala の lazy val と同じ)。
- lazy var の `(:= x v)` は assign を呼ぶ。初めて使う前に書き換えたら、初期値の式は一度も評価しない。

中身を書き換える入れ物なので frozen にはしない(覚える・書き換えるのがこの型の役目)。書き換える欄は
`_mut_` で始まる名で持ち、読みは property で出す。
"""

from collections.abc import Callable, Generator, Mapping

from doeff.do import do
from doeff.program import Expand

#: 初期値の式を包んだ、引数を取らない生成器の関数(macro は効果を使わない式でも生成器にして渡す)。
LazyInit = Callable[[], Generator[object, object, object]]


class LazyCell:
    """1 回の呼び出しの中の lazy val / lazy var を 1 つ持つ入れ物。

    init は初期値の式を包んだ生成器の関数。force が doeff の do で包んで実行する。値の型は参照する所の
    式が決める(型検査のための展開では lazy を書いた所で評価する形にするので、pyright は元の式の型を見る)。"""

    __slots__ = ("_init", "_mut_ready", "_mut_value")

    def __init__(self, init: LazyInit) -> None:
        self._init = init
        self._mut_ready = False
        self._mut_value: object = None

    @property
    def ready(self) -> bool:
        """覚えた値があるか(参照の式が、force を実行せずに値を返してよいかを見る)。"""
        return self._mut_ready

    @property
    def value(self) -> object:
        """覚えた値(ready の時だけ意味がある)。"""
        return self._mut_value

    def force(self) -> Expand:
        """まだ評価していなければ初期値の式を実行して覚える Program(評価済みなら覚えた値を返す)。"""
        return _force(self)

    def assign(self, value: object) -> None:
        """lazy var の書き換え。以後の参照はこの値になり、初期値の式はもう評価しない。"""
        self._mut_value = value
        self._mut_ready = True


@do
def _force(cell: LazyCell) -> Generator[object, object, object]:
    """LazyCell.force の本体: 初回だけ初期値の式を実行し、成功した時だけ覚える。"""
    if cell.ready:
        return cell.value
    value = yield do(cell._init)()
    if not cell.ready:
        cell.assign(value)
    return cell.value


# ---------------------------------------------------------------------------
# module の直下の lazy val(効果を使わない式だけ)
# ---------------------------------------------------------------------------
#
# `(lazy val NAME 式)` を module の直下に書くと、import の時には式を評価せず、初めて使った時に
# 1 回だけ評価して覚える。効果を使う式は展開の時点で誤り(module には handler が無く、最初に使った
# 呼び出しの handler の下で作った値が、以後の別のセッションでも使い回されるため)。
#
# 展開(binding_forms.module_declaration)は module の直下に次の 2 つの setv を出す:
#   (setv __doeff_module_lazies__ (with-module-lazy (globals) "NAME" (fn [] 式)))
#   (when (not-in "__getattr__" (globals)) (setv __getattr__ (module-getattr (globals))))
# 使える所: 同じ module の defk・deftest・defhandler の本体(macro が参照を書き換える)と、
# 他の module からの属性の参照・from import(PEP 562 の module の __getattr__)。
# 同じ module の defn・deff・module の直下の式の裸の参照は書き換えが届かない(NameError)。

MODULE_LAZIES = "__doeff_module_lazies__"


class ModuleLazy:
    """module の直下の lazy val 1 つ(初めて使った時に 1 回だけ評価して覚える)。"""

    __slots__ = ("_init", "_mut_ready", "_mut_value", "name")

    def __init__(self, name: str, init: Callable[[], object]) -> None:
        self.name = name
        self._init = init
        self._mut_ready = False
        self._mut_value: object = None

    def get(self) -> object:
        """覚えた値を返す。まだなら式を評価して覚える(例外なら覚えず、次にもう一度評価する)。"""
        if not self._mut_ready:
            self._mut_value = self._init()
            self._mut_ready = True
        return self._mut_value


def _lazy_table(namespace: Mapping[str, object]) -> Mapping[str, ModuleLazy]:
    """module の名前空間の lazy val の表(まだ無ければ空)。形が違えば誤り。"""
    table = namespace.get(MODULE_LAZIES, {})
    if not isinstance(table, Mapping):
        raise TypeError(f"{MODULE_LAZIES} が Mapping でない: {type(table).__name__}")
    for name, entry in table.items():
        if not isinstance(name, str) or not isinstance(entry, ModuleLazy):
            raise TypeError(f"{MODULE_LAZIES} の要素が (str, ModuleLazy) でない: {name!r}")
    return table


def with_module_lazy(namespace: Mapping[str, object], name: str, init: Callable[[], object]) -> dict[str, ModuleLazy]:
    """module の lazy val の表に 1 つ足した新しい表を返す(module の直下の setv がこれを表の名に束縛する)。"""
    return {**_lazy_table(namespace), name: ModuleLazy(name, init)}


def module_lazy_value(namespace: Mapping[str, object], name: str) -> object:
    """module の lazy val の値を取り出す(初回なら評価する)。他の module からの参照の口が使う。"""
    table = _lazy_table(namespace)
    if name not in table:
        raise NameError(f"module の lazy val `{name}` がまだ宣言されていません(宣言より前で使っている)")
    return table[name].get()


def module_getattr(namespace: Mapping[str, object]) -> Callable[[str], object]:
    """PEP 562 の module の __getattr__: 他の module が lazy val を名前で引いた時に評価して返す。"""

    def getattr_(attribute: str) -> object:
        """lazy val の名前なら値を、そうでなければ普通の属性の不在を返す。"""
        if attribute in _lazy_table(namespace):
            return module_lazy_value(namespace, attribute)
        raise AttributeError(f"module {namespace.get('__name__')!r} has no attribute {attribute!r}")

    return getattr_
