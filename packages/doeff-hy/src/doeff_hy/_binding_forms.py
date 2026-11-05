"""Expansion of val, var, lazy val, lazy var, session val, session var and `:=` (ADR-DOE-HY-006).

Walk the body of a defk / deftest / defhandler clause exactly once at macro-expansion time and do the following.

- Read the declaration forms: `(val x expr)`, `(var x expr)`, `(lazy val x expr)`, `(lazy var x expr)`,
  `(session val x expr)`, `(session var x expr)`. The expression may be written as `!(f)` (the Hy reader
  reads `!(f)` as two forms, `!` and `(f)`, so a `! expr` pair inside a declaration is folded into `(! expr)`).
  When the value type cannot be determined from the expression (for example an empty dict), only val and var accept a `(val #^ type x expr)` type annotation
  (rewritten to a `name: type = expr` assignment).
- Expand the rewrite form `(:= x new-value)` according to the kind of declaration.
- Rewrite references to a lazy val / lazy var (bare names) into an expression that "evaluates and remembers on first use".
- Turn binding errors (rebinding a name of the new syntax, shadowing a lazy name, referencing a lazy where yield is impossible,
  using the old lazy forms in defk / deftest) into SyntaxError.
- Return findings for old-style code (use of setv, rebinding the same name, the old lazy-val / set! in defhandler)
  as Finding. doeff-hy-check reports them as warnings / errors (expansion does not fail).

Design record = docs/design/defk-val-var-lazy/design.md. This module is called only at macro-expansion
time (compile time) and there is no place to run a Program, so the functions are written as plain Python defs
(the same treatment as the defn expansion helpers in macros.hy).
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

from doeff_hy._expansion_names import alias_symbol

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Timing(Enum):
    """When the value is evaluated."""

    EAGER = "eager"  # val / var — evaluated where written
    LAZY = "lazy"  # lazy val / lazy var — on first use within that call
    SESSION = "session"  # session val / session var — once per doeff session (defhandler only)


class Mutability(Enum):
    """Whether it is bound once (val) or can be rewritten with := (var)."""
    VAL = "val"
    VAR = "var"


class BodyKind(Enum):
    """Kind of the owner of the body (the error text and the usable declarations differ)."""

    DEFK = "defk"
    DEFTEST = "deftest"
    CLAUSE = "defhandler clause"


class Severity(Enum):
    """Whether a finding is red or a warning in doeff-hy-check."""
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Declaration:
    """One declaration. name is the Hy spelling (before mangling)."""

    name: str
    symbol: Symbol
    timing: Timing
    mutability: Mutability
    init: Object
    form: Expression
    #: The type of `(val #^ type name expr)` (the type expression of `(annotate name type)`). None if absent — the value type is determined from the expression.
    annotation: Object | None = None

    @property
    def target(self) -> Object:
        """LHS of setv: just the name, or `(annotate name type)` when there is a type annotation (same as Hy's `(setv #^ type name expr)` — the position is that of the written name)."""
        if self.annotation is None:
            return self.symbol
        annotated = Expression([Symbol("annotate"), self.symbol, self.annotation])
        annotated.replace(self.symbol, recursive=False)
        return annotated

    @property
    def spelled(self) -> str:
        """The declaration spelling as written, shown in error text (e.g. `lazy val`)."""
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
    """A finding that does not stop expansion (reported by doeff-hy-check). Positions are 1-based in the Hy source."""

    rule: str
    severity: Severity
    line: int
    column: int
    message: str


@dataclass(frozen=True)
class Rewritten:
    """The rewritten body and the findings."""

    forms: tuple[Object, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class SessionName:
    """Session-held names visible from a defhandler clause (those directly under the declared handler).

    legacy = declared with the old lazy / lazy-val / lazy-var (rewriting accepts both set! and :=)."""

    name: str
    mutability: Mutability
    legacy: bool


@dataclass(frozen=True)
class ModuleNames:
    """Declarations directly under the same module visible from the body (used to rewrite lazy val references and to guide toward :=)."""

    lazies: frozenset[str] = frozenset()
    vars: frozenset[str] = frozenset()


# Names of the finding rules (inside the parentheses of the doeff-hy-check output).
RULE_SETV = "doeff-hy-setv"
RULE_REBIND = "doeff-hy-rebind"
RULE_LEGACY_LAZY = "doeff-hy-legacy-lazy"

# ---------------------------------------------------------------------------
# Reading declaration forms
# ---------------------------------------------------------------------------

_KINDS: Mapping[str, Mutability] = {"val": Mutability.VAL, "var": Mutability.VAR}
_TIMINGS: Mapping[str, Timing] = {"lazy": Timing.LAZY, "session": Timing.SESSION}
LEGACY_LAZY_HEADS: frozenset[str] = frozenset({"lazy", "lazy-val", "lazy-var"})


def _head(form: Object) -> str | None:
    """Head name of a form (to tell declarations, bindings and nested contexts apart). None if the head is not a name."""
    if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Symbol):
        return str(form[0])
    return None


def _line(form: Object) -> int:
    """Source line in Hy shown in error text and findings (0 if the synthesized expression has no position)."""
    return int(getattr(form, "start_line", 0) or 0)


def _column(form: Object) -> int:
    """Source column in Hy shown in findings (0 if there is no position)."""
    return int(getattr(form, "start_column", 0) or 0)


def _model_text(model: Object, field: str) -> str | None:
    """Read hy's FString.brackets / FComponent.conversion (hy does not declare the type of this field,
    so check "str or None" at this one boundary before using it)."""
    value: object = model.__dict__.get(field)
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"hy {type(model).__name__}.{field} is not a string: {type(value).__name__}")


def _source(form: Object) -> str:
    """The literal text as written, quoted in error text."""
    text = hy.repr(form)
    return text[1:] if text.startswith("'") else text


def _join_bang(parts: list[Object], origin: Object, what: str) -> Object:
    """Make the value part of a declaration or := into one expression. A `! expr` pair is folded into `(! expr)`."""
    match parts:
        case [value]:
            return value
        case [Symbol() as bang, value] if str(bang) == "!":
            joined = Expression([bang, value])
            joined.replace(origin, recursive=False)
            return joined
        case _:
            raise SyntaxError(
                f"{what} (line {_line(origin)}): write exactly one expression as the value — {_source(origin)}\n\n"
                "  (val x expr) / (val x !(effect-expr))\n"
                "  If several steps are needed, wrap them in (do …) or give them a name with val beforehand."
            )


def is_new_lazy_form(form: Object) -> bool:
    """Whether this is the form `(lazy val x …)` / `(lazy var x …)` (to tell it from the old `(lazy name body…)`)."""
    return (
        isinstance(form, Expression)
        and len(form) >= 4
        and _head(form) in _TIMINGS
        and isinstance(form[1], Symbol)
        and str(form[1]) in _KINDS
        and (isinstance(form[2], Symbol) or _annotated_name(form[2]) is not None)
    )


def parse_declaration(form: Object) -> Declaration | None:
    """A Declaration if this is a declaration form, otherwise None. A malformed declaration is a SyntaxError."""
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
            return None  # old `(lazy val body…)` (an old lazy whose name is val) — treated as the old form
        timing = _TIMINGS[head]
        mutability = _KINDS[str(form[1])]
        rest = list(form[2:])
        spelled = f"{head} {form[1]}"
    else:
        return None
    annotated = _annotated_name(rest[0]) if rest else None
    name_symbol = annotated.name if annotated is not None else (rest[0] if rest else None)
    if len(rest) < 2 or not isinstance(name_symbol, Symbol) or str(name_symbol) in {"!", "val", "var"}:
        raise SyntaxError(
            f"({spelled} …) (line {_line(form)}): a name and an expression are required — {_source(form)}\n\n"
            f"  ({spelled} name expr)\n"
            f"  ({spelled} name !(effect-expr))"
        )
    if annotated is not None and timing is not Timing.EAGER:
        raise SyntaxError(
            f"({spelled} …) (line {_line(form)}): the type annotation `#^ type name` is accepted only by (val …) and (var …)"
            f" (the type of a lazy or session value is determined from the expression) — {_source(form)}"
        )
    init = _join_bang(rest[1:], form, f"({spelled} {name_symbol} …)")
    return Declaration(
        str(name_symbol), name_symbol, timing, mutability, init, form, annotated.annotation if annotated is not None else None
    )


class _AnnotatedName(NamedTuple):
    """Reading of `#^ type name` in the name position of a declaration: the name and the type the writer wrote."""

    name: Symbol
    annotation: Object


def _annotated_name(target: Object) -> _AnnotatedName | None:
    """Name and type of `#^ type name` (= `(annotate name type)`) in the name position of a declaration. None if the name is not annotated
    (the hook for the writer to give a type when the type of a module value cannot be determined from the expression — e.g. an empty dict)."""
    if isinstance(target, Expression) and len(target) == 3 and _head(target) == "annotate" and isinstance(target[1], Symbol):
        return _AnnotatedName(name=target[1], annotation=target[2])
    return None


@dataclass(frozen=True)
class Assignment:
    """The name that `(:= x value)` rewrites and the new value."""

    target: Symbol
    value: Object


def parse_assignment(form: Object) -> Assignment | None:
    """(x, value) if this is `(:= x value)`. None if it is not a `:=` form. A malformed form is a SyntaxError."""
    if not (isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Keyword)):
        return None
    if str(form[0]) != ":=":
        return None
    if len(form) < 3 or not isinstance(form[1], Symbol):
        raise SyntaxError(
            f"(:= …) (line {_line(form)}): the name to rewrite and a new value are required — {_source(form)}\n\n"
            "  (var count 0)\n  (:= count (+ count 1))"
        )
    target = form[1]
    assert isinstance(target, Symbol)
    return Assignment(target, _join_bang(list(form[2:]), form, f"(:= {target} …)"))


# ---------------------------------------------------------------------------
# Kinds of places being walked
# ---------------------------------------------------------------------------


class Scope(Enum):
    """Kind of place being walked — decides whether a lazy reference can be a yield expression and whether declarations can be written."""
    BODY = "body"  # scope of the body function (same inside if / when / for / try / let / do)
    DO_CONTEXT = "do-context"  # nested do context (fnk / do! / for/do / traverse / handle clauses) — yield is possible
    FN = "fn"  # nested function, class, validate — yield is impossible
    COMPREHENSION = "comprehension"  # lfor / gfor / dfor / sfor — yield is impossible
    DEFINITION = "definition"  # nested defk / deftest / defhandler … — their own macro owns the body


@dataclass(frozen=True)
class _Where:
    """The place being walked (the kind, and the head of the enclosing form shown in error text)."""
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
    """Names bound by the LHS of setv / for (assignment to an attribute or subscript does not bind a name)."""
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
# Walker
# ---------------------------------------------------------------------------

ExpandBangs = Callable[[Object, str], Object]


class _Walked(NamedTuple):
    """Result of walking one form: the rewritten form and the binding state after walking (names bound on this path)."""

    node: Object
    state: frozenset[str]


class _WalkedMany(NamedTuple):
    """Result of walking a sequence of forms: the rewritten sequence and the binding state after walking."""

    nodes: list[Object]
    state: frozenset[str]


class _Walker:
    """Walk one body. The state (declaration roster and findings) is held only inside the walker; the result is returned as Rewritten."""

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
        """Prepare to walk one body (arguments and session names count as bound from before the body)."""
        self.owner = owner
        self.kind = kind
        self.static = static
        self.expand_bangs = expand_bangs
        self.findings: list[Finding] = []
        self.declared: dict[str, Declaration] = {}
        self.sessions: dict[str, SessionName] = {s.name: s for s in sessions}
        # Names bound at least once in the body scope (checked for collisions with declarations of the new syntax — regardless of path).
        self.ever_bound: set[str] = set(params) | set(self.sessions)
        self.lazy_names = lazy_names  # Names declared lazy in the outermost sequence of the body (read in advance)
        self.module = module

    # -- error text ------------------------------------------------------------

    def _error(self, form: Object, message: str) -> SyntaxError:
        """Make an error that stops expansion, as a fix guide carrying the owner and line."""
        return SyntaxError(f"\n{self.owner} (line {_line(form)}): {message}\n")

    # -- findings --------------------------------------------------------------

    def _note(self, rule: str, severity: Severity, form: Object, message: str) -> None:
        """Record one finding that does not stop expansion (reported by doeff-hy-check)."""
        self.findings.append(Finding(rule, severity, _line(form), _column(form), message))

    # -- name binding ------------------------------------------------------------

    def _bind(self, symbol: Symbol, where: _Where, state: frozenset[str], counted: bool) -> frozenset[str]:
        """Check the binding of one name. counted = whether it counts toward the number of old-style rebindings (for-loop variables do not)."""
        name = str(symbol)
        if name in self.lazy_names or name in self.module.lazies:
            raise self._error(
                symbol,
                f"The lazy name `{name}` is covered by another binding (shadow). A lazy name has exactly one meaning inside the body.\n"
                f"  Use a different name.",
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
                hint = f"Rewrite it with (:= {name} new-value)."
            else:
                hint = f"To rewrite it, declare it with ({spelled.replace('val', 'var')} {name} …) and rewrite it with (:= {name} new-value)."
            raise self._error(
                symbol,
                f"({spelled} {name} …) declared this name, but it is rebound here. {hint}",
            )
        if counted and name in state:
            self._note(
                RULE_REBIND,
                Severity.ERROR,
                symbol,
                f"`{name}` is rebound — for a one-time binding use (val {name} …), to rewrite use "
                f"(var {name} …) and (:= {name} new-value) [ADR-DOE-HY-006]",
            )
        self.ever_bound.add(name)
        return state | {name}

    # -- sequence of the body ------------------------------------------------------

    def body(self, forms: Iterable[Object], state: frozenset[str]) -> _WalkedMany:
        """Walk the outermost sequence of the body (lazy declarations can be written only here)."""
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
        """Walk a sequence of forms from the left, carrying over the binding state."""
        out: list[Object] = []
        for form in forms:
            rewritten, state = self.walk(form, where, state)
            out.append(rewritten)
        return _WalkedMany(out, state)

    # -- declarations ------------------------------------------------------------------

    def _declare(
        self, declaration: Declaration, where: _Where, state: frozenset[str], top: bool
    ) -> _Walked:
        """Check one declaration and rewrite it into a setv or the creation of a lazy container."""
        form = declaration.form
        name = declaration.name
        if where.scope is not Scope.BODY:
            raise self._error(
                form,
                f"({declaration.spelled} {name} …) can be written only in the body of a defk, deftest or defhandler clause"
                f"(not inside ({where.head} …)).",
            )
        if declaration.timing is Timing.SESSION:
            if self.kind is BodyKind.CLAUSE:
                raise self._error(
                    form,
                    f"(session {declaration.mutability.value} {name} …) is written directly under defhandler (alongside the clauses). "
                    f"A value inside a clause that lives only for that single run is (val …) / (lazy val …).",
                )
            raise self._error(
                form,
                f"(session {declaration.mutability.value} {name} …) can be written only in defhandler. "
                f"This keeps hidden session state out of {self.kind.value}.\n"
                f"  A value evaluated once, on first use within this call, is (lazy val {name} expr).\n"
                f"  Put a value shared across the session in defhandler's (session val {name} expr) and obtain it as an effect.",
            )
        if declaration.timing is Timing.LAZY and not top:
            raise self._error(
                form, f"({declaration.spelled} {name} …) can be written only in the outermost sequence of the body (not inside if, when, etc.)."
            )
        if name in self.declared or name in self.sessions:
            first = self.declared.get(name)
            spelled = first.spelled if first is not None else "session " + self.sessions[name].mutability.value
            raise self._error(
                form,
                f"`{name}` is already declared with ({spelled} {name} …). The same name cannot be bound again."
                + (
                    f" Rewrite it with (:= {name} new-value)."
                    if (first is not None and first.mutability is Mutability.VAR)
                    else ""
                ),
            )
        if name in self.ever_bound:
            raise self._error(
                form,
                f"`{name}` is bound earlier (it cannot be rebound with {declaration.spelled}). "
                f"Use a different name, or merge the earlier binding into a single ({declaration.spelled} {name} …).",
            )
        if declaration.timing is Timing.LAZY:
            init, _ = self.walk(declaration.init, _Where(Scope.DO_CONTEXT, f"lazy {name}"), state)
            self.declared[name] = declaration
            self.ever_bound.add(name)
            return _Walked(self._lazy_declaration(declaration, init), state | {name})
        init, state = self.walk(declaration.init, where, state)
        self.declared[name] = declaration
        self.ever_bound.add(name)
        return _Walked(self._located(Expression([Symbol("setv"), declaration.target, init]), form), state | {name})

    def _cell(self, name: str) -> Symbol:
        """Name of the local variable holding the container (LazyCell) of a lazy name."""
        return Symbol(f"_doeff_lazy_{name}")

    def _lazy_declaration(self, declaration: Declaration, init: Object) -> Object:
        """Rewrite a lazy declaration into the creation of a LazyCell wrapping the initial-value expression (in the type-checking expansion it is evaluated where written)."""
        form = declaration.form
        if self.static:
            # Expansion for type checking: the type is the same as the value evaluated where written (references stay bare names).
            return self._located(Expression([Symbol("setv"), declaration.symbol, init]), form)
        thunk_body = self.expand_bangs(init, f"{self.owner} ({declaration.spelled} {declaration.name})")
        # The initial-value function is always a generator (`(when False (yield None))` — so that even an expression that uses no effects matches the doeff do
        # type "function returning a generator").
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
                            Expression([Symbol("."), Symbol("doeff_hy"), Symbol("_lazy")]),
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
        """Turn a reference to a lazy name into an expression "remembered value if ready, otherwise run force". An error where yield is impossible."""
        name = str(symbol)
        if name not in self.declared:
            raise self._error(
                symbol,
                f"The lazy `{name}` is referenced before its declaration (or inside its own expression). Use it after (lazy … {name} …).",
            )
        if where.scope in {Scope.FN, Scope.COMPREHENSION, Scope.DEFINITION}:
            raise self._error(
                symbol,
                f"The lazy `{name}` is referenced inside ({where.head} …). Effects cannot be run here "
                f"(yield is impossible), so the first evaluation cannot happen.\n"
                f"  Fetch it in the body first, then use it:\n\n"
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
        """Turn (:= x value) into a rewrite matching the kind of declaration (var, lazy var, session var)."""
        name = str(target)
        if where.scope is not Scope.BODY:
            raise self._error(
                form,
                f"(:= {name} …) can be written only in the body of a defk, deftest or defhandler clause (not inside ({where.head} …)).",
            )
        value, state = self.walk(value, where, state)
        declared = self.declared.get(name)
        session = self.sessions.get(name)
        if declared is not None:
            if declared.mutability is Mutability.VAL:
                raise self._error(
                    form,
                    f"({declared.spelled} {name} …) cannot be rewritten. To rewrite it, declare it with "
                    f"({declared.spelled.replace('val', 'var')} {name} …).",
                )
            if declared.timing is Timing.LAZY and not self.static:
                assign = Expression([Symbol("."), self._cell(name), Symbol("assign")])
                return _Walked(self._located(Expression([assign, value]), form), state)
            return _Walked(self._located(Expression([Symbol("setv"), target, value]), form), state)
        if session is not None:
            if session.mutability is Mutability.VAL:
                raise self._error(
                    form,
                    f"(session val {name} …) cannot be rewritten. To rewrite it, declare it with (session var {name} …).",
                )
            key = Symbol(f"_lazy_{name}_key")
            # Both expansions look names up through aliases with the `_doeff_` prefix (so they do not hide the writer's own effects such as KV Put — expansion_names.py).
            # The alias bundle is placed, in the type-checking expansion, only when doeff-hy-check reads the module head
            # (static_view.SESSION_STATIC_IMPORTS); at run time it is the import that defhandler emits.
            put = alias_symbol("Put")
            some = alias_symbol("Some")
            return _Walked(
                self._located(
                    Expression(
                        [
                            Symbol("do"),
                            Expression([Symbol("setv"), target, value]),
                            Expression(
                                [
                                    Symbol("yield"),
                                    Expression([put, key, Expression([some, target])]),
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
                f"`{name}` is a module var. It is not rewritten from inside a function (that would be hidden state). "
                f"Keep state to be rewritten as effect state (Get / Put) or in defhandler's (session var {name} …).",
            )
        if name in self.ever_bound:
            raise self._error(
                form,
                f"`{name}` is not declared with var, so it cannot be rewritten with (:= …). Declare it with (var {name} expr).",
            )
        raise self._error(form, f"`{name}` is not declared. Declare it with (var {name} expr) and then rewrite it with (:= {name} …).")

    # -- one form --------------------------------------------------------------

    def walk(self, node: Object, where: _Where, state: frozenset[str]) -> _Walked:
        """Walk and rewrite one form, and return the binding state (names bound on this path)."""
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
        """Attach the position of the original form to a synthesized form (so the traceback and findings point to the written line)."""
        if hasattr(origin, "_start_line"):
            built.replace(origin, recursive=False)
        return built

    def _rebuild(self, node: Expression, parts: list[Object]) -> Expression:
        """Rebuild an expression with rewritten children, at the position of the original expression."""
        rebuilt = Expression(parts)
        rebuilt.replace(node, recursive=False)
        return rebuilt

    def _expression(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """Tell one expression apart by its head and hand it to the walker for declarations, :=, bindings, nested contexts or branches."""
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
        if head in {"handle", "on-raise"} and len(node) >= 2:
            # The wrapped program is the outer context. The clauses are rewritten by the handle macro as its own body (val / var also
            # take effect there), so an outer lazy name cannot be referenced inside a clause (fetch it with val first).
            # The on-raise pattern and its target are value expressions evaluated inside the handler when a Raise is received (same treatment).
            program, state = self.walk(node[1], where, state)
            for clause in node[2:]:
                self._forbid_lazy_inside(clause, _Where(Scope.DEFINITION, f"{head} clause"))
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
                        f"`{symbol}` is rewritten with ({head} …) — declare it with (var {symbol} …) and use "
                        f"(:= {symbol} (… {symbol} …)) to rewrite it [ADR-DOE-HY-006]",
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

    # -- individual forms --------------------------------------------------------------

    def _legacy(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """Handle the old lazy / lazy-val / lazy-var / set!: an error in defk and deftest; for set! in a defhandler clause, only a finding."""
        if self.kind is BodyKind.CLAUSE:
            if head == "set!" and len(node) == 3 and isinstance(node[1], Symbol):
                self._note(
                    RULE_LEGACY_LAZY,
                    Severity.WARNING,
                    node,
                    f"(set! {node[1]} …) should be moved to (:= {node[1]} new-value) (same meaning) [ADR-DOE-HY-006]",
                )
                value, state = self.walk(node[2], where, state)
                return _Walked(self._rebuild(node, [node[0], node[1], value]), state)
            parts, state = self._sequence(node, where, state)
            return _Walked(self._rebuild(node, parts), state)
        if head == "lazy" and is_new_lazy_form(node):  # never reached because parse_declaration picks it up first
            raise self._error(node, "internal error: the new lazy form was not read as a declaration")
        name = str(node[1]) if len(node) >= 2 else "x"
        if head == "set!":
            guide = f"Declare it with (var {name} expr) and rewrite it with (:= {name} new-value)."
        else:
            guide = (
                f"A value evaluated once, on first use within that call, is (lazy val {name} expr); "
                f"a value to be rewritten is (lazy var {name} expr) with (:= {name} new-value)."
            )
        raise self._error(
            node,
            f"({head} {name} …) cannot be used in {self.kind.value} (old form. The old lazy of defk is session-spanning "
            f"hidden state and conflicts with the meaning of the new (lazy val …)).\n"
            f"  {guide}\n"
            f"  Put a value shared across the session in defhandler's (session val {name} expr) and obtain it as an effect."
            f" [ADR-DOE-HY-006]",
        )

    def _forbid_lazy_inside(self, node: Object, where: _Where) -> None:
        """If a lazy name appears inside a nested form whose own macro owns the body, make it an error (the rewrite cannot reach it)."""
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
        """Before walking the body, check that the name of a nested defn / defclass does not shadow a lazy name."""
        if head in {"defn", "defn/a", "defclass"}:
            name = node[1] if len(node) >= 2 else None
            if isinstance(name, List) and len(node) >= 3:
                name = node[2]
            if isinstance(name, Symbol) and str(name) in self.lazy_names:
                self._bind(name, where, state, counted=False)

    def _after_definition(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> frozenset[str]:
        """Count the name of a nested defn / defclass as a binding in the body scope."""
        if head in {"defn", "defn/a", "defclass"}:
            name = node[1] if len(node) >= 2 else None
            if isinstance(name, List) and len(node) >= 3:
                name = node[2]
            if isinstance(name, Symbol):
                return self._bind(name, where, state, counted=False)
        return state

    def _attribute(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """Walk only the obj of (. obj attr …) as a reference (the attribute name is not a variable reference)."""
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
        """Check the bindings of setv / setx, and attach a finding recommending val / var for a setv in the body."""
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
                f"use (val {shown} …) instead of setv, or (var {shown} …) with (:= {shown} …) to rewrite "
                "[ADR-DOE-HY-006]",
            )
        return _Walked(self._rebuild(node, out), state)

    def _effect_bind(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """Count (<- x effect) as a one-time binding just like val. The trailing `:absent failure` (ADR-DOE-CORE-EFFECTS-003 R6) is
        not a binding name, so separate it. The failure is a value expression evaluated inside the function only when absent, so walk it as inside a function."""
        parts = list(node[1:])
        suffix: list[Object] = []
        if len(parts) >= 3 and isinstance(parts[-2], Keyword) and str(parts[-2]) == ":absent":
            suffix, parts = parts[-2:], parts[:-2]
        if len(parts) >= 2 and isinstance(parts[0], Symbol):
            rest, state = self._sequence(parts[1:], where, state)
            state = self._bind(parts[0], where, state, counted=True)
            head = [node[0], parts[0], *rest]
        else:
            rest, state = self._sequence(parts, where, state)
            head = [node[0], *rest]
        if suffix:
            failure, _ = self.walk(suffix[1], _Where(Scope.FN, "<- :absent"), state)
            suffix = [suffix[0], failure]
        return _Walked(self._rebuild(node, [*head, *suffix]), state)

    def _branch(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """Branch: the arms are mutually exclusive, so walk each arm from the state before the branch, and take the union afterwards."""
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
                        out.append(item)  # pattern (binding forms are not counted here)
                    else:
                        walked, after = self.walk(item, where, state)
                        out.append(walked)
                        merged = merged | after
                return _Walked(self._rebuild(node, out), merged)

    def _except(self, node: Expression, where: _Where, state: frozenset[str]) -> _Walked:
        """Walk an except clause (the exception variable does not count as a rebinding)."""
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
        """Variable of for / with / except: not counted as a rebinding, but collisions with names of the new syntax and shadowing are checked."""
        self._bind(symbol, where, state, counted=False)

    def _loop(self, node: Expression, head: str, where: _Where, state: frozenset[str]) -> _Walked:
        """Walk for / while (the for variable does not count as a rebinding; bindings in the body count as one iteration)."""
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
        """Walk let (let names are mapped to other names so they are not counted, but shadowing a lazy name is an error)."""
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
        """Walk with (the with variable does not count as a rebinding)."""
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
# Public entry points
# ---------------------------------------------------------------------------


def _lazy_names(forms: Iterable[Object]) -> frozenset[str]:
    """Names declared lazy in the outermost sequence of the body (read before walking, to tell references that precede the declaration)."""
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
    """Rewrite the sequence of the body (declarations, :=, lazy references). Errors are SyntaxError; old-style code is returned as findings.

    - owner: head of the error text (e.g. `defk load-price`)
    - params: names bound from before the body (arguments, clause fields, effect / k)
    - sessions: session names visible from a defhandler clause
    - module: names of lazy vals (references are rewritten) and vars declared earlier directly under the same module
    - static: whether this is the type-checking expansion of doeff-hy-check (lazy is evaluated where written)
    - expand_bangs: expansion that turns the `(! …)` in a lazy initializer into an in-place yield (`_expand-bangs` in macros.hy)
    """
    listed = list(forms)
    walker = _Walker(owner, kind, params, sessions, static, expand_bangs, _lazy_names(listed), module)
    out, _ = walker.body(listed, frozenset(walker.ever_bound))
    return Rewritten(tuple(out), tuple(walker.findings))


def legacy_session_finding(form: Object, handler: str) -> Finding:
    """Findings for the old lazy / lazy-val / lazy-var of defhandler (expansion works as before)."""
    head = _head(form) or "lazy"
    name = str(form[1]) if isinstance(form, Expression) and len(form) >= 2 else "x"
    new = "session var" if head == "lazy-var" else "session val"
    return Finding(
        RULE_LEGACY_LAZY,
        Severity.WARNING,
        _line(form),
        _column(form),
        f"defhandler {handler}: ({head} {name} …) should be moved to ({new} {name} …)"
        " (same meaning and storage key; rewrite with := instead of set!) [ADR-DOE-HY-006]",
    )


def legacy_session_message(form: Object, handler: str) -> str:
    """Text of the DeprecationWarning (the same guidance as legacy_session_finding)."""
    return legacy_session_finding(form, handler).message


# ---------------------------------------------------------------------------
# val / var directly under a module
# ---------------------------------------------------------------------------
#
# `(val NAME expr)` / `(var NAME expr)` / `(lazy val NAME expr)` directly under a module (outside defk etc.) are
# expanded by the val / var / lazy macros of doeff_hy.macros. A roster that lives only during the compile of the same file
# (ModuleBindings — attached to the Hy compiler) makes a second declaration of the same name an error, and passes the lazy val and var names to the
# bodies of the following defk, deftest and defhandler (to rewrite lazy val references).
#
# Items decided by the main seat (ADR-DOE-HY-006 R8-R10):
# - A module-level lazy val is only an expression that uses no effects (evaluated and remembered once on first use —
#   so no heavy computation happens at import time). An expression that uses effects is an error: a module has no handler, and the value built under the
#   handler of the first call that used it would be reused by later sessions and other handler sets.
#   (It was once decided not to provide it and removed, then restored later — design record section 6)
# - A module-level lazy var is not provided (an error — it points to var or defhandler's session var).
# - The expressions of val / var also cannot use effects (there is no handler to run them).
# - The Hy reader reads `(:= …)` as a keyword call and it does not go through a macro, so directly under a module
#   `:=` has no effect. A module var is rewritten with a setv directly under the module (setv of a var name is not warned).

MODULE_ONLY_HEADS: frozenset[str] = frozenset({"val", "var", "lazy", "session"})


class ModuleBindings:
    """Roster of declarations directly under a module during the compile of one file (a second declaration of the same name is an error)."""

    def __init__(self) -> None:
        self.declared: dict[str, Declaration] = {}

    def names(self) -> ModuleNames:
        """Names of the module lazy vals (references are rewritten) and vars (guide toward :=) to hand to the following bodies."""
        return ModuleNames(
            frozenset(n for n, d in self.declared.items() if d.timing is Timing.LAZY),
            frozenset(n for n, d in self.declared.items() if d.mutability is Mutability.VAR),
        )


def _contains_effect(form: Object) -> bool:
    """Whether an expression uses effects (contains a `!` mark or `<-` — inside a quote is not counted)."""
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
    """Check one declaration directly under a module and rewrite it into a setv or the registration of a lazy val (session, lazy var, an expression using effects, and
    a second declaration are errors). static = type-checking expansion (a lazy val is also evaluated where written — the type is the same)."""
    declaration = parse_declaration(form)
    head = _head(form)
    if declaration is None:
        raise SyntaxError(
            f"\n({head} …) (line {_line(form)}): the forms usable directly under a module are (val name expr), (var name expr) and"
            f"(lazy val name expr) — {_source(form)}\n"
        )
    name = declaration.name
    where = f"\ndirectly under a module (line {_line(form)}): "
    if declaration.timing is Timing.SESSION:
        raise SyntaxError(
            where + f"(session {declaration.mutability.value} {name} …) can be written only directly under defhandler (alongside the clauses).\n"
        )
    if declaration.timing is Timing.LAZY and declaration.mutability is Mutability.VAR:
        raise SyntaxError(
            where + f"(lazy var {name} …) cannot be placed directly under a module. A value to be rewritten is (var {name} expr), "
            f"and state rewritten during a session goes in defhandler's (session var {name} expr). [ADR-DOE-HY-006]\n"
        )
    if declaration.timing is Timing.LAZY and _contains_effect(declaration.init):
        raise SyntaxError(
            where + f"The expression of a module-level (lazy val {name} …) cannot use effects (it contains `!` / `<-`).\n"
            f"  A module has no handler, so a value built under the handler of the first call that used it would be reused by later other sessions and\n"
            f"  other handler sets. Put a resource that uses effects in defhandler's (session val {name} expr) and\n"
            f"  obtain it as an effect. [ADR-DOE-HY-006]\n"
        )
    if _contains_effect(declaration.init):
        raise SyntaxError(
            where + f"The expression of a module-level ({declaration.spelled} {name} …) cannot use effects (there is no handler to run it).\n"
            f"  Put a resource built with effects in defhandler's (session val {name} expr) and obtain it as an effect. [ADR-DOE-HY-006]\n"
        )
    if name in bindings.declared:
        first = bindings.declared[name]
        raise SyntaxError(
            where + f"`{name}` is already declared with ({first.spelled} {name} …). The same name cannot be bound again.\n"
        )
    bindings.declared[name] = declaration
    setv = Expression([Symbol("setv"), declaration.target, declaration.init])
    if declaration.timing is Timing.EAGER or static:
        setv.replace(form, recursive=False)
        return setv
    installed = Expression(
        [
            Symbol("do"),
            Expression(
                [
                    Symbol("import"),
                    Expression([Symbol("."), Symbol("doeff_hy"), Symbol("_lazy")]),
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
            # Bind a new table, the module lazy val table plus one entry, to the table name directly under the module.
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
            # Entry point for references from other modules (PEP 562). Not placed in a module where the writer defines __getattr__.
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
    """Turn a reference to a module lazy val inside a body into a value-fetching expression (it uses no effects, so it can be written anywhere)."""
    # ((. (get (get (globals) "__doeff_module_lazies__") "NAME") get)) — doeff_hy._lazy.ModuleLazy.get
    table = Expression([Symbol("get"), Expression([Symbol("globals")]), hy.models.String("__doeff_module_lazies__")])
    entry = Expression([Symbol("get"), table, hy.models.String(hy.mangle(str(symbol)))])
    built = Expression([Expression([Symbol("."), entry, Symbol("get")])])
    built.replace(symbol, recursive=False)
    return built


_MODULE_SCOPE_HEADS: frozenset[str] = frozenset({"do", "when", "if", "cond", "try", "else", "finally", "except"})


class ModuleScope:
    """Look at the forms directly under a module one by one and collect findings for setv directly under it (a warning recommending val / var, an error for rebinding a val name,
    and an error for an ineffective :=).

    doeff-hy-check applies this to the outermost sequence of one file's source (directly under a module is outside macros, so setv is not visible at expansion time).
    It receives forms one at a time so that the same form can be passed each time the Hy compile reads one — it does not re-read
    the source for findings, nor hold the forms it has read until the end."""

    def __init__(self) -> None:
        self._findings: list[Finding] = []
        self._declared: dict[str, Declaration] = {}

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Findings for the forms seen so far."""
        return tuple(self._findings)

    def visit(self, form: Object) -> None:
        """Look at one form in the module scope (does not enter nested scopes such as defn)."""
        declaration = parse_declaration(form) if _head(form) in MODULE_ONLY_HEADS else None
        if declaration is not None:
            self._declared.setdefault(declaration.name, declaration)
            return
        if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Keyword) and str(form[0]) == ":=":
            self._findings.append(
                Finding(RULE_REBIND, Severity.ERROR, _line(form), _column(form),
                        "A (:= …) directly under a module is read by Hy as a keyword call and rewrites nothing — "
                        "rewrite a module var with a setv directly under the module [ADR-DOE-HY-006]")
            )
            return
        head = _head(form)
        if head in {"setv", "setx"} and isinstance(form, Expression):
            names = [n for t in list(form[1:])[0::2] for n in _target_names(t)]
            for symbol in names:
                first = self._declared.get(str(symbol))
                if first is not None and first.mutability is Mutability.VAL:
                    self._findings.append(
                        Finding(RULE_REBIND, Severity.ERROR, _line(symbol), _column(symbol),
                                f"({first.spelled} {symbol} …) declared this name, but it is rebound with setv — "
                                f"to rewrite it, declare it with (var {symbol} …) [ADR-DOE-HY-006]")
                    )
            unwarned = [n for n in names if str(n) not in self._declared]
            if unwarned:
                shown = unwarned[0]
                self._findings.append(
                    Finding(RULE_SETV, Severity.WARNING, _line(form), _column(form),
                            f"use (val {shown} …) instead of a setv directly under a module, or (var {shown} …) to rewrite "
                            "[ADR-DOE-HY-006]")
                )
            return
        if head in _MODULE_SCOPE_HEADS and isinstance(form, Expression):
            for child in form[1:]:
                self.visit(child)



def module_findings(forms: Iterable[Object]) -> list[Finding]:
    """Findings for setv directly under a module (the result of showing the outermost sequence to ModuleScope in order)."""
    scope = ModuleScope()
    for form in forms:
        scope.visit(form)
    return list(scope.findings)
