"""val・var・lazy val・lazy var・session val・session var と `:=` の展開(ADR-DOE-HY-006)。

defk・deftest・defhandler の節の本体を、macro の展開の時に 1 度だけ歩き、次をする。

- 宣言の形を読む: `(val x 式)`・`(var x 式)`・`(lazy val x 式)`・`(lazy var x 式)`・
  `(session val x 式)`・`(session var x 式)`。式は `!(f)` と書いてよい(Hy の reader は
  `!(f)` を `!` と `(f)` の 2 つに読むので、宣言の中の `! 式` の 2 つ組を `(! 式)` に畳む)。
- 書き換えの形 `(:= x 新しい値)` を、宣言の種類に合わせて展開する。
- lazy val / lazy var の参照(裸の名前)を「初回なら評価して覚える」式に書き換える。
- 束縛の誤り(新しい構文の名前の束縛し直し・lazy の名前の影・yield できない所での lazy の参照・
  旧い lazy の形を defk / deftest で使うこと)を SyntaxError にする。
- 旧い書き方の所見(setv の使用・同じ名前の束縛し直し・defhandler の旧い lazy-val / set!)を
  Finding として返す。doeff-hy-check が警告 / 赤として出す(展開は失敗させない)。

設計の記録 = docs/design/defk-val-var-lazy/design.md。この module は macro の展開の時
(compile の時)にだけ呼ばれ、Program を実行する場が無いので、関数は Python の def で書く
(macros.hy の展開の helper の defn と同じ扱い)。
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple

import hy
from hy.models import (
    Dict,
    Expression,
    FComponent,
    FString,
    Keyword,
    List,
    Object,
    Sequence,
    Set,
    Symbol,
    Tuple,
)

# ---------------------------------------------------------------------------
# 型
# ---------------------------------------------------------------------------


class Timing(Enum):
    """いつ評価するか。"""

    EAGER = "eager"  # val / var — 書いた所で評価する
    LAZY = "lazy"  # lazy val / lazy var — その呼び出しの中で初めて使った時
    SESSION = "session"  # session val / session var — doeff のセッションの間で 1 回(defhandler だけ)


class Mutability(Enum):
    """一度だけ束縛する(val)か、:= で書き換えられる(var)か。"""
    VAL = "val"
    VAR = "var"


class BodyKind(Enum):
    """本体の持ち主の種類(誤りの文と、使える宣言が違う)。"""

    DEFK = "defk"
    DEFTEST = "deftest"
    CLAUSE = "defhandler の節"


class Severity(Enum):
    """所見を doeff-hy-check で赤にするか警告にするか。"""
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Declaration:
    """1 つの宣言。name は Hy の綴り(mangle 前)。"""

    name: str
    symbol: Symbol
    timing: Timing
    mutability: Mutability
    init: Object
    form: Expression

    @property
    def spelled(self) -> str:
        """誤りの文に出す、書いたままの宣言の綴り(例 `lazy val`)。"""
        return {
            (Timing.EAGER, Mutability.VAL): "val",
            (Timing.EAGER, Mutability.VAR): "var",
            (Timing.LAZY, Mutability.VAL): "lazy val",
            (Timing.LAZY, Mutability.VAR): "lazy var",
            (Timing.SESSION, Mutability.VAL): "session val",
            (Timing.SESSION, Mutability.VAR): "session var",
        }[(self.timing, self.mutability)]


@dataclass(frozen=True)
class Finding:
    """展開を止めない所見(doeff-hy-check が出す)。位置は Hy の source の 1 始まり。"""

    rule: str
    severity: Severity
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class Rewritten:
    """書き換えた本体と所見。"""

    forms: tuple[Object, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class SessionName:
    """defhandler の節から見える、セッションに持つ名前(宣言した handler の直下の物)。

    legacy = 旧い lazy / lazy-val / lazy-var で宣言した物(書き換えは set! と := の両方を受ける)。"""

    name: str
    mutability: Mutability
    legacy: bool


@dataclass(frozen=True)
class ModuleNames:
    """本体から見える、同じ module の直下の宣言(lazy val の参照の書き換えと、:= の案内に使う)。"""

    lazies: frozenset[str] = frozenset()
    vars: frozenset[str] = frozenset()


# 所見の規則の名(doeff-hy-check の出力の括弧の中)。
RULE_SETV = "doeff-hy-setv"
RULE_REBIND = "doeff-hy-rebind"
RULE_LEGACY_LAZY = "doeff-hy-legacy-lazy"

# ---------------------------------------------------------------------------
# 宣言の形を読む
# ---------------------------------------------------------------------------

_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAL, "var": Mutability.VAR}
_TIMINGS: Mapping[str, Timing] = {"lazy": Timing.LAZY, "session": Timing.SESSION}
LEGACY_LAZY_HEADS: frozenset[str] = frozenset({"lazy", "lazy-val", "lazy-var"})


def _head(form: Object) -> str | None:
    """形の頭の名前(宣言・束縛・入れ子の文脈を見分けるため)。頭が名前でなければ None。"""
    if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Symbol):
        return str(form[0])
    return None


def _line(form: Object) -> int:
    """誤りの文と所見に出す、Hy の source の行(合成した式で位置が無ければ 0)。"""
    return int(getattr(form, "start_line", 0) or 0)


def _column(form: Object) -> int:
    """所見に出す、Hy の source の列(位置が無ければ 0)。"""
    return int(getattr(form, "start_column", 0) or 0)


def _model_text(model: Object, field: str) -> str | None:
    """hy の FString.brackets / FComponent.conversion を読む(hy はこの欄の型を宣言していないので、
    境界のこの 1 か所で「文字列か None」を検めてから使う)。"""
    value: object = model.__dict__.get(field)
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"hy の {type(model).__name__}.{field} が文字列でない: {type(value).__name__}")


def _source(form: Object) -> str:
    """誤りの文に引用する、書いたままの字面。"""
    text = hy.repr(form)
    return text[1:] if text.startswith("'") else text


def _join_bang(parts: list[Object], origin: Object, what: str) -> Object:
    """宣言や := の値の部分を 1 つの式にする。`! 式` の 2 つ組は `(! 式)` に畳む。"""
    match parts:
        case [value]:
            return value
        case [Symbol() as bang, value] if str(bang) == "!":
            joined = Expression([bang, value])
            joined.replace(origin, recursive=False)
            return joined
        case _:
            raise SyntaxError(
                f"{what} (line {_line(origin)}): 値は式を 1 つだけ書きます — {_source(origin)}\n\n"
                "  (val x 式) / (val x !(効果の式))\n"
                "  いくつかの手順が要るなら (do …) で包むか、前に val で名前を付けてください。"
            )


def is_new_lazy_form(form: Object) -> bool:
    """`(lazy val x …)` / `(lazy var x …)` の形か(旧い `(lazy 名前 本体…)` と見分ける)。"""
    return (
        isinstance(form, Expression)
        and len(form) >= 4
        and _head(form) in _TIMINGS
        and isinstance(form[1], Symbol)
        and str(form[1]) in _KINDS
        and isinstance(form[2], Symbol)
    )


def parse_declaration(form: Object) -> Declaration | None:
    """宣言の形なら Declaration、そうでなければ None。形が崩れた宣言は SyntaxError。"""
    head = _head(form)
    if not isinstance(form, Expression) or head is None:
        return None
    if head in _KINDS:
        timing = Timing.EAGER
        mutability = _KINDS[head]
        rest = list(form[1:])
        spelled = head
    elif head in _TIMINGS and len(form) >= 2 and isinstance(form[1], Symbol) and str(form[1]) in _KINDS:
        if head == "lazy" and not is_new_lazy_form(form):
            return None  # 旧い `(lazy val 本体…)`(名前が val の旧い lazy)— 旧い形として扱う
        timing = _TIMINGS[head]
        mutability = _KINDS[str(form[1])]
        rest = list(form[2:])
        spelled = f"{head} {form[1]}"
    else:
        return None
    if len(rest) < 2 or not isinstance(rest[0], Symbol) or str(rest[0]) in {"!", "val", "var"}:
        raise SyntaxError(
            f"({spelled} …) (line {_line(form)}): 名前と式が要ります — {_source(form)}\n\n"
            f"  ({spelled} 名前 式)\n"
            f"  ({spelled} 名前 !(効果の式))"
        )
    name_symbol = rest[0]
    assert isinstance(name_symbol, Symbol)
    init = _join_bang(rest[1:], form, f"({spelled} {name_symbol} …)")
    return Declaration(str(name_symbol), name_symbol, timing, mutability, init, form)


@dataclass(frozen=True)
class Assignment:
    """`(:= x 値)` の書き換える名前と新しい値。"""

    target: Symbol
    value: Object


def parse_assignment(form: Object) -> Assignment | None:
    """`(:= x 値)` なら (x, 値)。`:=` の形でなければ None。崩れた形は SyntaxError。"""
    if not (isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Keyword)):
        return None
    if str(form[0]) != ":=":
        return None
    if len(form) < 3 or not isinstance(form[1], Symbol):
        raise SyntaxError(
            f"(:= …) (line {_line(form)}): 書き換える名前と新しい値が要ります — {_source(form)}\n\n"
            "  (var count 0)\n  (:= count (+ count 1))"
        )
    target = form[1]
    assert isinstance(target, Symbol)
    return Assignment(target, _join_bang(list(form[2:]), form, f"(:= {target} …)"))


# ---------------------------------------------------------------------------
# 歩く場所の種類
# ---------------------------------------------------------------------------


class Scope(Enum):
    """歩いている所の種類 — lazy の参照を yield の式にできるか・宣言を書けるかを決める。"""
    BODY = "body"  # 本体の関数の scope(if / when / for / try / let / do の中も同じ)
    DO_CONTEXT = "do-context"  # 入れ子の do の文脈(fnk / do! / for/do / traverse / handle の節)— yield できる
    FN = "fn"  # 入れ子の関数・class・validate — yield できない
    COMPREHENSION = "comprehension"  # lfor / gfor / dfor / sfor — yield できない
    DEFINITION = "definition"  # 入れ子の defk / deftest / defhandler … — 自分の macro が本体を持つ


@dataclass(frozen=True)
class _Where:
    """歩いている所(種類と、誤りの文に出す囲みの形の頭)。"""
    scope: Scope
    head: str


_TOP = _Where(Scope.BODY, "")

_DO_CONTEXT_HEADS: frozenset[str] = frozenset({"for/do", "traverse", "fnk", "do!"})
_DEFINITION_HEADS: frozenset[str] = frozenset(
    {"defk", "defp", "defpp", "deftest", "defmcp-tool", "defhandler", "deff", "defmacro"}
)
_FN_HEADS: frozenset[str] = frozenset({"fn", "fn/a", "defn", "defn/a", "defclass", "validate"})
_COMPREHENSION_HEADS: frozenset[str] = frozenset({"lfor", "gfor", "dfor", "sfor"})
_SKIP_HEADS: frozenset[str] = frozenset({"quote", "quasiquote", "import", "require", "global", "nonlocal"})
_AUGMENTED: frozenset[str] = frozenset(
    {"+=", "-=", "*=", "/=", "//=", "%=", "**=", "@=", "<<=", ">>=", "&=", "|=", "^="}
)
_BRANCH_HEADS: frozenset[str] = frozenset({"if", "when", "unless", "cond", "try", "match"})
_LOOP_HEADS: frozenset[str] = frozenset({"for", "while"})


def _target_names(target: Object) -> list[Symbol]:
    """setv / for の左辺が束縛する名前(属性・添字への代入は名前の束縛ではない)。"""
    match target:
        case Symbol():
            return [] if str(target) in {"_", "*"} else [target]
        case List() | Tuple():
            return [name for item in target for name in _target_names(item)]
        case Expression() if _head(target) == "annotate" and len(target) >= 2:
            return _target_names(target[1])
        case Expression() if _head(target) == "unpack-iterable" and len(target) >= 2:
            return _target_names(target[1])
        case _:
            return []


# ---------------------------------------------------------------------------
# 歩き手
# ---------------------------------------------------------------------------

ExpandBangs = Callable[[Object, str], Object]


class _Walked(NamedTuple):
    """1 つの形を歩いた結果: 書き換えた形と、歩いた後の束縛の状態(この経路で束縛済みの名前)。"""

    node: Object
    state: frozenset[str]


class _WalkedMany(NamedTuple):
    """形の並びを歩いた結果: 書き換えた並びと、歩いた後の束縛の状態。"""

    nodes: list[Object]
    state: frozenset[str]


class _Walker:
    """1 つの本体を歩く。状態(宣言の名簿と所見)は歩き手の中だけに持ち、結果は Rewritten で返す。"""

    def __init__(
        self,
        owner: str,
        kind: BodyKind,
        params: Iterable[str],
        sessions: Iterable[SessionName],
        static: bool,
        expand_bangs: ExpandBangs,
        lazy_names: frozenset[str],
        module: ModuleNames,
    ) -> None:
        """1 つの本体を歩く準備(引数・セッションの名前は本体の前から束縛済みとして数える)。"""
        self.owner = owner
        self.kind = kind
        self.static = static
        self.expand_bangs = expand_bangs
        self.findings: list[Finding] = []
        self.declared: dict[str, Declaration] = {}
        self.sessions: dict[str, SessionName] = {s.name: s for s in sessions}
        # 本体の scope で一度でも束縛された名前(新しい構文の宣言と衝突を見る — 経路によらない)。
        self.ever_bound: set[str] = set(params) | set(self.sessions)
        self.lazy_names = lazy_names  # 本体の一番外の並びで lazy に宣言される名前(前もって読む)
        self.module = module

    # -- 誤りの文 ------------------------------------------------------------

    def _error(self, form: Object, message: str) -> SyntaxError:
        """展開を止める誤りを、持ち主と行の付いた修正の案内として作る。"""
        return SyntaxError(f"\n{self.owner} (line {_line(form)}): {message}\n")

    # -- 所見 ----------------------------------------------------------------

    def _note(self, rule: str, severity: Severity, form: Object, message: str) -> None:
        """展開を止めない所見を 1 つ記録する(doeff-hy-check が出す)。"""
        self.findings.append(Finding(rule, severity, _line(form), _column(form), message))

    # -- 名前の束縛 ------------------------------------------------------------

    def _bind(self, symbol: Symbol, where: _Where, state: frozenset[str], counted: bool) -> frozenset[str]:
        """名前 1 つの束縛を検める。counted = 旧い束縛し直しの数に入れるか(for の変数は入れない)。"""
        name = str(symbol)
        if name in self.lazy_names or name in self.module.lazies:
            raise self._error(
                symbol,
                f"lazy の名前 `{name}` を別の束縛で覆っています(影)。lazy の名前は本体の中で 1 つの意味だけを持ちます。\n"
                f"  別の名前を使ってください。",
            )
        if where.scope is not Scope.BODY:
            return state
        declared = self.declared.get(name)
        session = self.sessions.get(name)
        new_session = session if session is not None and not session.legacy else None
        if declared is not None or new_session is not None:
            if declared is not None:
                spelled, mutability = declared.spelled, declared.mutability
            else:
                assert new_session is not None
                spelled, mutability = f"session {new_session.mutability.value}", new_session.mutability
            if mutability is Mutability.VAR:
                hint = f"書き換えは (:= {name} 新しい値) で書きます。"
            else:
                hint = f"書き換えるなら ({spelled.replace('val', 'var')} {name} …) で宣言し、(:= {name} 新しい値) で書き換えます。"
            raise self._error(
                symbol,
                f"({spelled} {name} …) で宣言した名前を束縛し直しています。{hint}",
            )
        if counted and name in state:
            self._note(
                RULE_REBIND,
                Severity.ERROR,
                symbol,
                f"`{name}` を束縛し直しています — 一度だけなら (val {name} …)、書き換えるなら "
                f"(var {name} …) と (:= {name} 新しい値) を使う [ADR-DOE-HY-006]",
            )
        self.ever_bound.add(name)
        return state | {name}

    # -- 本体の並び ------------------------------------------------------------

    def body(self, forms: Iterable[Object], state: frozenset[str]) -> _WalkedMany:
        """本体の一番外の並びを歩く(lazy の宣言はここにだけ書ける)。"""
        out: list[Object] = []
        for form in forms:
            declaration = parse_declaration(form)
            if declaration is not None:
                rewritten, state = self._declare(declaration, _TOP, state, top=True)
                out.append(rewritten)
                continue
            rewritten, state = self.walk(form, _TOP, state)
            out.append(rewritten)
        return _WalkedMany(out, state)

    def _sequence(
        self, forms: Iterable[Object], where: _Where, state: frozenset[str]
    ) -> _WalkedMany:
        """形の並びを左から順に歩き、束縛の状態を引き継ぐ。"""
        out: list[Object] = []
        for form in forms:
            rewritten, state = self.walk(form, where, state)
            out.append(rewritten)
        return _WalkedMany(out, state)

    # -- 宣言 ------------------------------------------------------------------

    def _declare(
        self, declaration: Declaration, where: _Where, state: frozenset[str], top: bool
    ) -> _Walked:
        """宣言 1 つを検めて、setv か lazy の入れ物の作成に書き換える。"""
        form = declaration.form
        name = declaration.name
        if where.scope is not Scope.BODY:
            raise self._error(
                form,
                f"({declaration.spelled} {name} …) は defk・deftest・defhandler の節の本体にだけ書けます"
                f"(({where.head} …) の中には書けません)。",
            )
        if declaration.timing is Timing.SESSION:
            if self.kind is BodyKind.CLAUSE:
                raise self._error(
                    form,
                    f"(session {declaration.mutability.value} {name} …) は defhandler の直下(節と並べる所)に書きます。"
                    f"節の中の、その 1 回の実行の間だけの値は (val …) / (lazy val …) です。",
                )
            raise self._error(
                form,
                f"(session {declaration.mutability.value} {name} …) は defhandler にだけ書けます。"
                f"{self.kind.value} に隠れたセッションの状態を持ち込まないためです。\n"
                f"  この呼び出しの中で初めて使った時に 1 回だけ評価する値は (lazy val {name} 式) です。\n"
                f"  セッションで共有する値は defhandler の (session val {name} 式) に置き、効果として求めてください。",
            )
        if declaration.timing is Timing.LAZY and not top:
            raise self._error(
                form, f"({declaration.spelled} {name} …) は本体の一番外の並びにだけ書けます(if・when などの中には書けません)。"
            )
        if name in self.declared or name in self.sessions:
            first = self.declared.get(name)
            spelled = first.spelled if first is not None else "session " + self.sessions[name].mutability.value
            raise self._error(
                form,
                f"`{name}` は ({spelled} {name} …) で宣言済みです。同じ名前をもう一度束縛できません。"
                + (
                    f" 書き換えは (:= {name} 新しい値) で書きます。"
                    if (first is not None and first.mutability is Mutability.VAR)
                    else ""
                ),
            )
        if name in self.ever_bound:
            raise self._error(
                form,
                f"`{name}` は前で束縛されています({declaration.spelled} で束縛し直せません)。"
                f"別の名前を使うか、前の束縛を ({declaration.spelled} {name} …) の 1 つにまとめてください。",
            )
        if declaration.timing is Timing.LAZY:
            init, _ = self.walk(declaration.init, _Where(Scope.DO_CONTEXT, f"lazy {name}"), state)
            self.declared[name] = declaration
            self.ever_bound.add(name)
            return _Walked(self._lazy_declaration(declaration, init), state | {name})
        init, state = self.walk(declaration.init, where, state)
        self.declared[name] = declaration
        self.ever_bound.add(name)
        return _Walked(self._located(Expression([Symbol("setv"), declaration.symbol, init]), form), state | {name})

    def _cell(self, name: str) -> Symbol:
        """lazy の名前の入れ物(LazyCell)を持つ局所変数の名前。"""
        return Symbol(f"_doeff_lazy_{name}")

    def _lazy_declaration(self, declaration: Declaration, init: Object) -> Object:
        """lazy の宣言を、初期値の式を包んだ LazyCell の作成に書き換える(型検査のための展開では書いた所で評価)。"""
        form = declaration.form
        if self.static:
            # 型検査のための展開: 型は書いた所で評価した値と同じ(参照は裸の名前のまま)。
            return self._located(Expression([Symbol("setv"), declaration.symbol, init]), form)
        thunk_body = self.expand_bangs(init, f"{self.owner} ({declaration.spelled} {declaration.name})")
        # 初期値の関数は常に生成器にする(`(when False (yield None))` — 効果を使わない式でも doeff の do の
        # 型「生成器を返す関数」に合う)。
        thunk = Expression(
            [
                Symbol("fn"),
                List([]),
                Expression([Symbol("when"), Symbol("False"), Expression([Symbol("yield"), Symbol("None")])]),
                Expression([Symbol("return"), thunk_body]),
            ]
        )
        return self._located(
            Expression(
                [
                    Symbol("do"),
                    Expression(
                        [
                            Symbol("import"),
                            Expression([Symbol("."), Symbol("doeff_hy"), Symbol("lazy")]),
                            List([Symbol("LazyCell"), Keyword("as"), Symbol("_doeff_LazyCell")]),
                        ]
                    ),
                    Expression(
                        [
                            Symbol("setv"),
                            self._cell(declaration.name),
                            Expression([Symbol("_doeff_LazyCell"), thunk]),
                        ]
                    ),
                ]
            ),
            form,
        )

    def _lazy_reference(self, symbol: Symbol, where: _Where) -> Object:
        """lazy の名前の参照を「用意ができていれば覚えた値・まだなら force を実行」の式にする。yield できない所では誤り。"""
        name = str(symbol)
        if name not in self.declared:
            raise self._error(
                symbol,
                f"lazy の `{name}` を宣言より前(か自分の式の中)で参照しています。(lazy … {name} …) の後で使ってください。",
            )
        if where.scope in {Scope.FN, Scope.COMPREHENSION, Scope.DEFINITION}:
            raise self._error(
                symbol,
                f"lazy の `{name}` を ({where.head} …) の中で参照しています。ここでは効果を実行できない"
                f"(yield できない)ので、初回の評価ができません。\n"
                f"  先に本体で取り出してから使ってください:\n\n"
                f"    (val {name}-value {name})\n"
                f"    ({where.head} … {name}-value …)",
            )
        if self.static:
            return symbol
        cell = self._cell(name)
        return self._located(
            Expression(
                [
                    Symbol("if"),
                    Expression([Symbol("."), cell, Symbol("ready")]),
                    Expression([Symbol("."), cell, Symbol("value")]),
                    Expression([Symbol("yield"), Expression([Expression([Symbol("."), cell, Symbol("force")])])]),
                ]
            ),
            symbol,
        )

    # -- := --------------------------------------------------------------------

    def _assign(
        self, form: Expression, target: Symbol, value: Object, where: _Where, state: frozenset[str]
    ) -> _Walked:
        """(:= x 値) を宣言の種類(var・lazy var・session var)に合わせた書き換えにする。"""
        name = str(target)
        if where.scope is not Scope.BODY:
            raise self._error(
                form,
                f"(:= {name} …) は defk・deftest・defhandler の節の本体にだけ書けます(({where.head} …) の中には書けません)。",
            )
        value, state = self.walk(value, where, state)
        declared = self.declared.get(name)
        session = self.sessions.get(name)
        if declared is not None:
            if declared.mutability is Mutability.VAL:
                raise self._error(
                    form,
                    f"({declared.spelled} {name} …) は書き換えられません。書き換えるなら "
                    f"({declared.spelled.replace('val', 'var')} {name} …) で宣言してください。",
                )
            if declared.timing is Timing.LAZY and not self.static:
                assign = Expression([Symbol("."), self._cell(name), Symbol("assign")])
                return _Walked(self._located(Expression([assign, value]), form), state)
            return _Walked(self._located(Expression([Symbol("setv"), target, value]), form), state)
        if session is not None:
            if session.mutability is Mutability.VAL:
                raise self._error(
                    form,
                    f"(session val {name} …) は書き換えられません。書き換えるなら (session var {name} …) で宣言してください。",
                )
            key = Symbol(f"_lazy_{name}_key")
            return _Walked(
                self._located(
                    Expression(
                        [
                            Symbol("do"),
                            Expression([Symbol("setv"), target, value]),
                            Expression(
                                [
                                    Symbol("yield"),
                                    Expression(
                                        [Symbol("Put"), key, Expression([Symbol("Some"), target])]
                                    ),
                                ]
                            ),
                        ]
                    ),
                    form,
                ),
                state,
            )
        if name in self.module.vars:
            raise self._error(
                form,
                f"`{name}` は module の var です。関数の中からは書き換えません(隠れた状態になる)。"
                f"書き換える状態は効果の状態(Get / Put)か defhandler の (session var {name} …) に持ちます。",
            )
        if name in self.ever_bound:
            raise self._error(
                form,
                f"`{name}` は var で宣言していないので (:= …) で書き換えられません。(var {name} 式) で宣言してください。",
            )
        raise self._error(form, f"`{name}` は宣言されていません。(var {name} 式) で宣言してから (:= {name} …) で書き換えます。")

    # -- 1 つの形 --------------------------------------------------------------

    def walk(self, node: Object, where: _Where, state: frozenset[str]) -> _Walked:
        """1 つの形を歩いて書き換え、束縛の状態(この経路で束縛済みの名前)を返す。"""
        match node:
            case Symbol():
                if str(node) in self.lazy_names:
                    return _Walked(self._lazy_reference(node, where), state)
                if str(node) in self.module.lazies and not self.static:
                    return _Walked(module_lazy_reference(node), state)
                return _Walked(node, state)
            case Expression():
                return self._expression(node, where, state)
            case FString():
                parts, state = self._sequence(node, where, state)
                rebuilt = FString(parts, brackets=_model_text(node, "brackets"))
                return _Walked(self._located(rebuilt, node), state)
            case FComponent():
                parts, state = self._sequence(node, where, state)
                return _Walked(self._located(FComponent(parts, conversion=_model_text(node, "conversion")), node), state)
            case List() | Tuple() | Set() | Dict():
                parts, state = self._sequence(node, where, state)
                return _Walked(self._located(type(node)(parts), node), state)
            case _:
                return _Walked(node, state)

    def _located(self, built: Object, origin: Object) -> Object:
        """合成した形に元の形の位置を付ける(traceback と所見が書いた行を指すように)。"""
        if hasattr(origin, "_start_line"):
            built.replace(origin, recursive=False)
        return built

    def _rebuild(self, node: Expression, parts: list[Object]) -> Expression:
        """子を書き換えた式を、元の式の位置のまま作り直す。"""
        rebuilt = Expression(parts)
        rebuilt.replace(node, recursive=False)
        return rebuilt

    def _expression(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """式 1 つを頭で見分けて、宣言・:=・束縛・入れ子の文脈・分岐のそれぞれの歩き方へ渡す。"""
        if len(node) == 0:
            return _Walked(node, state)
        assignment = parse_assignment(node)
        if assignment is not None:
            return self._assign(node, assignment.target, assignment.value, where, state)
        declaration = parse_declaration(node)
        if declaration is not None:
            return self._declare(declaration, where, state, top=False)
        head = _head(node)
        if head is None:
            parts, state = self._sequence(node, where, state)
            return _Walked(self._rebuild(node, parts), state)
        if head in LEGACY_LAZY_HEADS or head == "set!":
            return self._legacy(node, head, where, state)
        if head in _SKIP_HEADS:
            return _Walked(node, state)
        if head in _DEFINITION_HEADS:
            self._forbid_lazy_inside(node, _Where(Scope.DEFINITION, head))
            return _Walked(node, state)
        if head in _DO_CONTEXT_HEADS:
            inner = _Where(Scope.DO_CONTEXT, head)
            parts, _ = self._sequence(node[1:], inner, state)
            return _Walked(self._rebuild(node, [node[0], *parts]), state)
        if head == "handle" and len(node) >= 2:
            # 包む program は外の文脈。節は handle の macro が自分の本体として書き換える(val / var も
            # そこで効く)ので、外の lazy の名前は節の中では参照できない(先に val で取り出す)。
            program, state = self.walk(node[1], where, state)
            for clause in node[2:]:
                self._forbid_lazy_inside(clause, _Where(Scope.DEFINITION, "handle の節"))
            return _Walked(self._rebuild(node, [node[0], program, *node[2:]]), state)
        if head in _FN_HEADS:
            inner = _Where(Scope.FN, head)
            self._bind_definition_name(node, head, where, state)
            parts, _ = self._sequence(node[1:], inner, state)
            state = self._after_definition(node, head, where, state)
            return _Walked(self._rebuild(node, [node[0], *parts]), state)
        if head in _COMPREHENSION_HEADS:
            inner = _Where(Scope.COMPREHENSION, head)
            parts, _ = self._sequence(node[1:], inner, state)
            return _Walked(self._rebuild(node, [node[0], *parts]), state)
        if head == ".":
            return self._attribute(node, where, state)
        if head in {"setv", "setx"}:
            return self._setv(node, where, state)
        if head == "<-":
            return self._effect_bind(node, where, state)
        if head in _AUGMENTED and len(node) >= 2:
            value_parts, state = self._sequence(node[2:], where, state)
            for symbol in _target_names(node[1]):
                self._bind(symbol, where, state, counted=False)
                if where.scope is Scope.BODY and str(symbol) not in self.declared:
                    self._note(
                        RULE_REBIND,
                        Severity.ERROR,
                        symbol,
                        f"`{symbol}` を ({head} …) で書き換えています — (var {symbol} …) で宣言し "
                        f"(:= {symbol} (… {symbol} …)) で書き換える [ADR-DOE-HY-006]",
                    )
                state = state | {str(symbol)}
            return _Walked(self._rebuild(node, [node[0], node[1], *value_parts]), state)
        if head in _BRANCH_HEADS:
            return self._branch(node, head, where, state)
        if head in _LOOP_HEADS:
            return self._loop(node, head, where, state)
        if head == "let" and len(node) >= 2 and isinstance(node[1], List):
            return self._let(node, where, state)
        if head == "with" and len(node) >= 2 and isinstance(node[1], List):
            return self._with(node, where, state)
        parts, state = self._sequence(node, where, state)
        return _Walked(self._rebuild(node, parts), state)

    # -- 個別の形 --------------------------------------------------------------

    def _legacy(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """旧い lazy / lazy-val / lazy-var / set! を扱う: defk・deftest では誤り、defhandler の節の set! は所見だけ。"""
        if self.kind is BodyKind.CLAUSE:
            if head == "set!" and len(node) == 3 and isinstance(node[1], Symbol):
                self._note(
                    RULE_LEGACY_LAZY,
                    Severity.WARNING,
                    node,
                    f"(set! {node[1]} …) は (:= {node[1]} 新しい値) へ移す(意味は同じ) [ADR-DOE-HY-006]",
                )
                value, state = self.walk(node[2], where, state)
                return _Walked(self._rebuild(node, [node[0], node[1], value]), state)
            parts, state = self._sequence(node, where, state)
            return _Walked(self._rebuild(node, parts), state)
        if head == "lazy" and is_new_lazy_form(node):  # parse_declaration が先に拾うので来ない
            raise self._error(node, "内部の誤り: lazy の新しい形が宣言として読まれなかった")
        name = str(node[1]) if len(node) >= 2 else "x"
        if head == "set!":
            guide = f"(var {name} 式) で宣言し、(:= {name} 新しい値) で書き換えてください。"
        else:
            guide = (
                f"その呼び出しの中で初めて使った時に 1 回だけ評価する値は (lazy val {name} 式)、"
                f"書き換える値は (lazy var {name} 式) と (:= {name} 新しい値) です。"
            )
        raise self._error(
            node,
            f"({head} {name} …) は {self.kind.value} では使えません(旧い形。defk の旧い lazy はセッションをまたぐ"
            f"隠れた状態で、新しい (lazy val …) の意味と衝突します)。\n"
            f"  {guide}\n"
            f"  セッションで共有する値は defhandler の (session val {name} 式) に置き、効果として求めてください。"
            f" [ADR-DOE-HY-006]",
        )

    def _forbid_lazy_inside(self, node: Object, where: _Where) -> None:
        """自分の macro が本体を持つ入れ子の形の中に lazy の名前が在れば誤りにする(書き換えが届かないので)。"""
        match node:
            case Symbol() if str(node) in self.lazy_names:
                self._lazy_reference(node, where)
            case Expression() if _head(node) in {"quote", "quasiquote"}:
                return
            case Sequence():
                for child in node:
                    self._forbid_lazy_inside(child, where)
            case _:
                return

    def _bind_definition_name(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> None:
        """入れ子の defn / defclass の名前が lazy の名前を覆っていないかを、本体を歩く前に検める。"""
        if head in {"defn", "defn/a", "defclass"}:
            name = node[1] if len(node) >= 2 else None
            if isinstance(name, List) and len(node) >= 3:
                name = node[2]
            if isinstance(name, Symbol) and str(name) in self.lazy_names:
                self._bind(name, where, state, counted=False)

    def _after_definition(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> frozenset[str]:
        """入れ子の defn / defclass の名前を本体の scope の束縛として数える。"""
        if head in {"defn", "defn/a", "defclass"}:
            name = node[1] if len(node) >= 2 else None
            if isinstance(name, List) and len(node) >= 3:
                name = node[2]
            if isinstance(name, Symbol):
                return self._bind(name, where, state, counted=False)
        return state

    def _attribute(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """(. obj 属性 …) の obj だけを参照として歩く(属性の名は変数の参照ではない)。"""
        if len(node) < 2:
            return _Walked(node, state)
        target, state = self.walk(node[1], where, state)
        rest: list[Object] = []
        for item in node[2:]:
            match item:
                case Symbol():
                    rest.append(item)
                case Expression() if len(item) > 0:
                    args, state = self._sequence(item[1:], where, state)
                    rest.append(self._rebuild(item, [item[0], *args]))
                case _:
                    walked, state = self.walk(item, where, state)
                    rest.append(walked)
        return _Walked(self._rebuild(node, [node[0], target, *rest]), state)

    def _setv(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """setv / setx の束縛を検め、本体の setv には val / var を勧める所見を付ける。"""
        pairs = list(node[1:])
        out: list[Object] = [node[0]]
        binds_name = False
        for index in range(0, len(pairs) - 1, 2):
            target, value = pairs[index], pairs[index + 1]
            value, state = self.walk(value, where, state)
            names = _target_names(target)
            if names:
                binds_name = True
            elif not isinstance(target, Symbol):
                target, state = self.walk(target, where, state)
            for symbol in names:
                state = self._bind(symbol, where, state, counted=True)
            out.extend([target, value])
        if len(pairs) % 2:
            out.append(pairs[-1])
        if binds_name and where.scope is Scope.BODY:
            first = next((n for t in pairs[0::2] for n in _target_names(t)), None)
            shown = str(first) if first is not None else "x"
            self._note(
                RULE_SETV,
                Severity.WARNING,
                node,
                f"setv の代わりに (val {shown} …)、書き換えるなら (var {shown} …) と (:= {shown} …) を使う "
                "[ADR-DOE-HY-006]",
            )
        return _Walked(self._rebuild(node, out), state)

    def _effect_bind(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """(<- x 効果) を val と同じ一度だけの束縛として数える。"""
        parts = list(node[1:])
        if len(parts) >= 2 and isinstance(parts[0], Symbol):
            rest, state = self._sequence(parts[1:], where, state)
            state = self._bind(parts[0], where, state, counted=True)
            return _Walked(self._rebuild(node, [node[0], parts[0], *rest]), state)
        rest, state = self._sequence(parts, where, state)
        return _Walked(self._rebuild(node, [node[0], *rest]), state)

    def _branch(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """分岐: 枝どうしは互いに排他なので、各枝は分岐の前の状態から歩き、後は和を取る。"""
        items = list(node[1:])
        out: list[Object] = [node[0]]
        match head:
            case "if" | "when" | "unless":
                if not items:
                    return _Walked(node, state)
                test, state = self.walk(items[0], where, state)
                out.append(test)
                if head == "if":
                    merged = state if len(items) < 3 else frozenset()
                    for branch in items[1:]:
                        walked, after = self.walk(branch, where, state)
                        out.append(walked)
                        merged = merged | after
                    return _Walked(self._rebuild(node, out), merged)
                walked_body, after = self._sequence(items[1:], where, state)
                out.extend(walked_body)
                return _Walked(self._rebuild(node, out), state | after)
            case "cond":
                merged = frozenset[str]()
                current = state
                for index, item in enumerate(items):
                    if index % 2 == 0:
                        walked, current = self.walk(item, where, current)
                    else:
                        walked, after = self.walk(item, where, current)
                        merged = merged | after
                    out.append(walked)
                return _Walked(self._rebuild(node, out), merged | current)
            case "try":
                merged = frozenset[str]()
                body_after = state
                for item in items:
                    item_head = _head(item)
                    if item_head in {"except", "except*"} and isinstance(item, Expression):
                        walked, after = self._except(item, where, state)
                    elif item_head == "else" and isinstance(item, Expression):
                        walked, after = self.walk(item, where, body_after)
                    elif item_head == "finally" and isinstance(item, Expression):
                        walked, after = self.walk(item, where, merged | body_after)
                    else:
                        walked, body_after = self.walk(item, where, body_after)
                        after = body_after
                    out.append(walked)
                    merged = merged | after
                return _Walked(self._rebuild(node, out), merged | body_after)
            case _:  # match
                if not items:
                    return _Walked(node, state)
                subject, state = self.walk(items[0], where, state)
                out.append(subject)
                merged = state
                for index, item in enumerate(items[1:]):
                    if index % 2 == 0:
                        out.append(item)  # pattern(束縛の形はここでは数えない)
                    else:
                        walked, after = self.walk(item, where, state)
                        out.append(walked)
                        merged = merged | after
                return _Walked(self._rebuild(node, out), merged)

    def _except(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """except の節を歩く(例外の変数は束縛し直しに数えない)。"""
        items = list(node[1:])
        out: list[Object] = [node[0]]
        if items and isinstance(items[0], List):
            spec = items[0]
            spec_parts: list[Object] = []
            for index, part in enumerate(spec):
                if index == 0 and isinstance(part, Symbol) and len(spec) >= 2:
                    self._bind_uncounted(part, where, state)
                    spec_parts.append(part)
                else:
                    walked, state = self.walk(part, where, state)
                    spec_parts.append(walked)
            out.append(self._located(List(spec_parts), spec))
            items = items[1:]
        body, state = self._sequence(items, where, state)
        return _Walked(self._rebuild(node, out + body), state)

    def _bind_uncounted(self, symbol: Symbol, where: _Where, state: frozenset[str]) -> None:
        """for / with / except の変数: 束縛し直しの数には入れないが、新しい構文の名前との衝突と影は見る。"""
        self._bind(symbol, where, state, counted=False)

    def _loop(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """for / while を歩く(for の変数は束縛し直しに数えない・本体の束縛は 1 回の繰り返しの分として数える)。"""
        items = list(node[1:])
        out: list[Object] = [node[0]]
        if head == "for" and items and isinstance(items[0], List):
            spec = list(items[0])
            walked_spec: list[Object] = []
            index = 0
            while index < len(spec):
                part = spec[index]
                if isinstance(part, Keyword):
                    walked_spec.append(part)
                    if index + 1 < len(spec):
                        walked, state = self.walk(spec[index + 1], where, state)
                        walked_spec.append(walked)
                    index += 2
                    continue
                if index + 1 < len(spec):
                    iterable, state = self.walk(spec[index + 1], where, state)
                    for symbol in _target_names(part):
                        self._bind_uncounted(symbol, where, state)
                    walked_spec.extend([part, iterable])
                    index += 2
                    continue
                walked_spec.append(part)
                index += 1
            out.append(self._located(List(walked_spec), items[0]))
            items = items[1:]
        elif items:
            test, state = self.walk(items[0], where, state)
            out.append(test)
            items = items[1:]
        body, after = self._sequence(items, where, state)
        out.extend(body)
        return _Walked(self._rebuild(node, out), state | after)

    def _let(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """let を歩く(let の名前は別の名前に写されるので数えないが、lazy の名前の影は誤り)。"""
        spec = list(node[1])
        walked_spec: list[Object] = []
        for index in range(0, len(spec) - 1, 2):
            value, state = self.walk(spec[index + 1], where, state)
            for symbol in _target_names(spec[index]):
                if str(symbol) in self.lazy_names:
                    self._bind(symbol, where, state, counted=False)
            walked_spec.extend([spec[index], value])
        body, state = self._sequence(node[2:], where, state)
        return _Walked(self._rebuild(node, [node[0], self._located(List(walked_spec), node[1]), *body]), state)

    def _with(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """with を歩く(with の変数は束縛し直しに数えない)。"""
        spec = list(node[1])
        walked_spec: list[Object] = []
        index = 0
        while index < len(spec):
            part = spec[index]
            if isinstance(part, Symbol) and index + 1 < len(spec):
                value, state = self.walk(spec[index + 1], where, state)
                self._bind_uncounted(part, where, state)
                walked_spec.extend([part, value])
                index += 2
                continue
            walked, state = self.walk(part, where, state)
            walked_spec.append(walked)
            index += 1
        body, state = self._sequence(node[2:], where, state)
        return _Walked(self._rebuild(node, [node[0], self._located(List(walked_spec), node[1]), *body]), state)


# ---------------------------------------------------------------------------
# 公開の口
# ---------------------------------------------------------------------------


def _lazy_names(forms: Iterable[Object]) -> frozenset[str]:
    """本体の一番外の並びで lazy に宣言される名前(宣言より前の参照を見分けるため、歩く前に読む)。"""
    names: set[str] = set()
    for form in forms:
        declaration = parse_declaration(form)
        if declaration is not None and declaration.timing is Timing.LAZY:
            names.add(declaration.name)
    return frozenset(names)


def rewrite_body(
    forms: Iterable[Object],
    *,
    owner: str,
    kind: BodyKind,
    params: Iterable[str],
    sessions: Iterable[SessionName] = (),
    module: ModuleNames = ModuleNames(),
    static: bool,
    expand_bangs: ExpandBangs,
) -> Rewritten:
    """本体の並びを書き換える(宣言・:=・lazy の参照)。誤りは SyntaxError、旧い書き方は所見で返す。

    - owner: 誤りの文の頭(例 `defk load-price`)
    - params: 本体の前から束縛されている名前(引数・節の欄・effect / k)
    - sessions: defhandler の節から見えるセッションの名前
    - module: 同じ module の直下で前に宣言した lazy val(参照を書き換える)と var の名前
    - static: doeff-hy-check の型検査のための展開か(lazy を書いた所で評価する形にする)
    - expand_bangs: lazy の初期化の式の `(! …)` をその場の yield にする展開(macros.hy の `_expand-bangs`)
    """
    listed = list(forms)
    walker = _Walker(owner, kind, params, sessions, static, expand_bangs, _lazy_names(listed), module)
    out, _ = walker.body(listed, frozenset(walker.ever_bound))
    return Rewritten(tuple(out), tuple(walker.findings))


def legacy_session_finding(form: Object, handler: str) -> Finding:
    """defhandler の旧い lazy / lazy-val / lazy-var の所見(展開は今までどおり)。"""
    head = _head(form) or "lazy"
    name = str(form[1]) if isinstance(form, Expression) and len(form) >= 2 else "x"
    new = "session var" if head == "lazy-var" else "session val"
    return Finding(
        RULE_LEGACY_LAZY,
        Severity.WARNING,
        _line(form),
        _column(form),
        f"defhandler {handler}: ({head} {name} …) は ({new} {name} …) へ移す"
        "(意味と保存先のキーは同じ・書き換えは set! の代わりに :=) [ADR-DOE-HY-006]",
    )


def legacy_session_message(form: Object, handler: str) -> str:
    """DeprecationWarning の文(legacy_session_finding と同じ案内)。"""
    return legacy_session_finding(form, handler).message


# ---------------------------------------------------------------------------
# module の直下の val / var
# ---------------------------------------------------------------------------
#
# module の直下(defk などの外)の `(val NAME 式)` / `(var NAME 式)` / `(lazy val NAME 式)` は
# doeff_hy.macros の val / var / lazy の macro が展開する。同じ file の compile の間だけ生きる名簿
# (ModuleBindings — Hy の compiler に付ける)で同じ名前の 2 回目を誤りにし、後に続く defk・deftest・
# defhandler の本体へ lazy val と var の名前を渡す(lazy val の参照を書き換えるため)。
#
# main 席の決めた項目(ADR-DOE-HY-006 R8〜R10):
# - module の直下の lazy val は効果を使わない式だけ(初めて使った時に 1 回だけ評価して覚える —
#   import の時に重い計算をしない)。効果を使う式は誤り: module には handler が無く、最初に使った呼び出しの
#   handler の下で作った値が、以後の別のセッション・別の handler の組でも使い回されるため。
#   (一度「作らない」と決めて外し、operator の指示 "nonono have lazyval" で戻した — 設計の記録 §6)
# - module の直下の lazy var は作らない(誤り — var か defhandler の session var を案内する)。
# - val / var の式も効果を使えない(実行する handler が無い)。
# - Hy の reader は `(:= …)` を keyword の呼び出しとして読み、macro を通らないので、module の直下では
#   `:=` が効かない。module の var は module の直下の setv で書き換える(var の名前の setv は警告しない)。

MODULE_ONLY_HEADS: frozenset[str] = frozenset({"val", "var", "lazy", "session"})


class ModuleBindings:
    """1 つの file の compile の間の、module の直下の宣言の名簿(同じ名前の 2 回目を誤りにする)。"""

    def __init__(self) -> None:
        self.declared: dict[str, Declaration] = {}

    def names(self) -> ModuleNames:
        """後に続く本体へ渡す、module の lazy val(参照を書き換える)と var(:= を案内する)の名前。"""
        return ModuleNames(
            frozenset(n for n, d in self.declared.items() if d.timing is Timing.LAZY),
            frozenset(n for n, d in self.declared.items() if d.mutability is Mutability.VAR),
        )


def _contains_effect(form: Object) -> bool:
    """式が効果を使うか(`!` の印か `<-` を含むか — quote の中は数えない)。"""
    match form:
        case Symbol() if str(form) == "!":
            return True
        case Expression() if _head(form) in {"quote", "quasiquote"}:
            return False
        case Expression() if _head(form) in {"!", "<-"}:
            return True
        case Sequence():
            return any(_contains_effect(child) for child in form)
        case _:
            return False


def module_declaration(bindings: ModuleBindings, form: Expression, static: bool) -> Object:
    """module の直下の宣言 1 つを検めて、setv か lazy val の登録に書き換える(session・lazy var・効果を使う式・
    2 回目は誤り)。static = 型検査のための展開(lazy val も書いた所で評価する形にする — 型は同じ)。"""
    declaration = parse_declaration(form)
    head = _head(form)
    if declaration is None:
        raise SyntaxError(
            f"\n({head} …) (line {_line(form)}): module の直下で使える形は (val 名前 式)・(var 名前 式)・"
            f"(lazy val 名前 式) です — {_source(form)}\n"
        )
    name = declaration.name
    where = f"\nmodule の直下 (line {_line(form)}): "
    if declaration.timing is Timing.SESSION:
        raise SyntaxError(
            where + f"(session {declaration.mutability.value} {name} …) は defhandler の直下(節と並べる所)にだけ書けます。\n"
        )
    if declaration.timing is Timing.LAZY and declaration.mutability is Mutability.VAR:
        raise SyntaxError(
            where + f"module の直下に (lazy var {name} …) は置けません。書き換える値は (var {name} 式)、"
            f"セッションの間で書き換える状態は defhandler の (session var {name} 式) に置きます。 [ADR-DOE-HY-006]\n"
        )
    if declaration.timing is Timing.LAZY and _contains_effect(declaration.init):
        raise SyntaxError(
            where + f"module の直下の (lazy val {name} …) の式は効果を使えません(`!` / `<-` を含む)。\n"
            f"  module には handler が無く、最初に使った呼び出しの handler の下で作った値が、以後の別のセッション・\n"
            f"  別の handler の組でも使い回されるからです。効果を使う資源は defhandler の (session val {name} 式) に置き、\n"
            f"  効果として求めてください。 [ADR-DOE-HY-006]\n"
        )
    if _contains_effect(declaration.init):
        raise SyntaxError(
            where + f"module の直下の ({declaration.spelled} {name} …) の式は効果を使えません(実行する handler が無い)。\n"
            f"  効果で作る資源は defhandler の (session val {name} 式) に置き、効果として求めてください。 [ADR-DOE-HY-006]\n"
        )
    if name in bindings.declared:
        first = bindings.declared[name]
        raise SyntaxError(
            where + f"`{name}` は ({first.spelled} {name} …) で宣言済みです。同じ名前をもう一度束縛できません。\n"
        )
    bindings.declared[name] = declaration
    setv = Expression([Symbol("setv"), declaration.symbol, declaration.init])
    if declaration.timing is Timing.EAGER or static:
        setv.replace(form, recursive=False)
        return setv
    installed = Expression(
        [
            Symbol("do"),
            Expression(
                [
                    Symbol("import"),
                    Expression([Symbol("."), Symbol("doeff_hy"), Symbol("lazy")]),
                    List(
                        [
                            Symbol("with_module_lazy"),
                            Keyword("as"),
                            Symbol("_doeff_with_module_lazy"),
                            Symbol("module_getattr"),
                            Keyword("as"),
                            Symbol("_doeff_module_getattr"),
                        ]
                    ),
                ]
            ),
            # module の lazy val の表に 1 つ足した新しい表を、module の直下で表の名に束縛する。
            Expression(
                [
                    Symbol("setv"),
                    Symbol("__doeff_module_lazies__"),
                    Expression(
                        [
                            Symbol("_doeff_with_module_lazy"),
                            Expression([Symbol("globals")]),
                            hy.models.String(hy.mangle(name)),
                            Expression([Symbol("fn"), List([]), declaration.init]),
                        ]
                    ),
                ]
            ),
            # 他の module からの参照の口(PEP 562)。書き手が __getattr__ を持つ module では置かない。
            Expression(
                [
                    Symbol("when"),
                    Expression([Symbol("not-in"), hy.models.String("__getattr__"), Expression([Symbol("globals")])]),
                    Expression(
                        [
                            Symbol("setv"),
                            Symbol("__getattr__"),
                            Expression([Symbol("_doeff_module_getattr"), Expression([Symbol("globals")])]),
                        ]
                    ),
                ]
            ),
        ]
    )
    installed.replace(form, recursive=False)
    return installed


def module_lazy_reference(symbol: Symbol) -> Object:
    """本体の中の module の lazy val の参照を、値の取り出しの式にする(効果を使わないのでどこでも書ける)。"""
    # ((. (get (get (globals) "__doeff_module_lazies__") "NAME") get)) — doeff_hy.lazy.ModuleLazy.get
    table = Expression([Symbol("get"), Expression([Symbol("globals")]), hy.models.String("__doeff_module_lazies__")])
    entry = Expression([Symbol("get"), table, hy.models.String(hy.mangle(str(symbol)))])
    built = Expression([Expression([Symbol("."), entry, Symbol("get")])])
    built.replace(symbol, recursive=False)
    return built


_MODULE_SCOPE_HEADS: frozenset[str] = frozenset({"do", "when", "if", "cond", "try", "else", "finally", "except"})


def module_findings(forms: Iterable[Object]) -> list[Finding]:
    """module の直下の setv の所見(val / var を勧める警告・val の名前の束縛し直しの赤・効かない := の赤)。

    doeff-hy-check が 1 file の source の一番外の並びに当てる(module の直下は macro の外なので、
    setv は展開の時には見えない)。"""
    findings: list[Finding] = []
    declared: dict[str, Declaration] = {}

    def visit(form: Object) -> None:
        """module の scope の中の 1 つの形を見る(defn などの入れ子の scope には入らない)。"""
        declaration = parse_declaration(form) if _head(form) in MODULE_ONLY_HEADS else None
        if declaration is not None:
            declared.setdefault(declaration.name, declaration)
            return
        if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Keyword) and str(form[0]) == ":=":
            findings.append(
                Finding(RULE_REBIND, Severity.ERROR, _line(form), _column(form),
                        "module の直下の (:= …) は Hy では keyword の呼び出しとして読まれ、何も書き換えない — "
                        "module の var は module の直下の setv で書き換える [ADR-DOE-HY-006]")
            )
            return
        head = _head(form)
        if head in {"setv", "setx"} and isinstance(form, Expression):
            names = [n for t in list(form[1:])[0::2] for n in _target_names(t)]
            for symbol in names:
                first = declared.get(str(symbol))
                if first is not None and first.mutability is Mutability.VAL:
                    findings.append(
                        Finding(RULE_REBIND, Severity.ERROR, _line(symbol), _column(symbol),
                                f"({first.spelled} {symbol} …) で宣言した名前を setv で束縛し直している — "
                                f"書き換えるなら (var {symbol} …) で宣言する [ADR-DOE-HY-006]")
                    )
            unwarned = [n for n in names if str(n) not in declared]
            if unwarned:
                shown = unwarned[0]
                findings.append(
                    Finding(RULE_SETV, Severity.WARNING, _line(form), _column(form),
                            f"module の直下の setv の代わりに (val {shown} …)、書き換えるなら (var {shown} …) を使う "
                            "[ADR-DOE-HY-006]")
                )
            return
        if head in _MODULE_SCOPE_HEADS and isinstance(form, Expression):
            for child in form[1:]:
                visit(child)

    for form in forms:
        visit(form)
    return findings
