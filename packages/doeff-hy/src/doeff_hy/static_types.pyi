"""型検査のための展開(doeff_hy/static_view.py)だけが参照する型の宣言。

実行時の展開はこの module を import しない。pyright にだけ見せる。型そのものは doeff core
の物(`Expand[T, E]`・`Program[T, E]`・`EffectBase[T]` — docs/23-static-typing.md)を使い、
ここは Hy の展開の形に合わせた口だけを持つ。

- `do`: defk / defp などの関数を包む。core の `doeff.do.do` そのもの(本体に yield の無い関数 —
  yield の無い defk は普通の関数になる — の overload も core が持つ)。
- `_doeff_perform(e)`: `(<- x e)` の x の型 = e の答えの型(Python の `@effectful` の
  `x = perform(e)` と同じ形・docs/24-effectful-perform.md)。effect は `EffectBase[T]` の T、
  Program(defk を呼んだ結果など)は `Program[T, E]` の T。宣言は `Program[_T, object] -> _T` の 1 つ
  だけで、`object -> Any` の受け皿を持たない(agora-redesign #3116・cisco-c8 2026-10-03 16:3x の決め):
  受け皿の overload が在ると、module の中で最初の評価が union の期待型の下(`(<- x (| int None) …)`)
  だった時に pyright 1.1.414 が以後の `_doeff_perform` を全部受け皿へ落とし、答えが Any になって
  束ねの型の食い違いを黙って通していた(順に依る — tests/test_static_check.py の
  test_a_bind_after_a_union_bind_keeps_its_answer_type)。Program でない値を `<-` に渡すと赤になる。
  Hy には effect の集合を宣言する口がまだ無いので、`perform: Effects[E]` の E の突き合わせはしない。
- deftest の引数の型(`DeftestInterpreter` と pytest の組み込みの fixture の型・下の節)。
- defhandler / handle の展開の型(節を回す関数・handler を被せる関数・節の終わり方の検め・末尾の節)。
"""

from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeAlias, TypeVar

import pytest
from doeff_vm import K, WithHandler

from doeff import Program

# `do` は core の物をそのまま渡し、2 つ目の宣言を持たない(agora-redesign #2321)。for/do を使う module は展開が
# 名指す `(import doeff [do :as _doeff-do])` を自分で書くので、doeff-hy-check が module の頭に置く `_doeff_do` の後に
# 利用者の `_doeff_do` が来る。2 つの型が違うと pyright は後の方を取り、yield の無い関数の overload を持たない方で
# defk の投影(本体の `<-` は `_doeff_perform` になり yield が無い)を読んで、defk を呼んだ答えが Unknown になっていた。
# 型が 1 つなら、どちらの import が後に来ても同じ型になる。
from doeff.do import do as do

_T = TypeVar("_T")

def _doeff_perform(effect: Program[_T, object], /) -> _T: ...

# for/do・traverse の件の引数の型(agora-redesign #2321)。型検査のための展開(macros.hy の _traverse-form)は、
# 件ごとの関数の引数を `object` で受け、本体の前で `x = _doeff_traverse_item(items, 件)` と読み直す — 件は items の
# 要素そのもの(Traverse の handler が items の各件を渡す)なので、答えは items の要素の型。
def traverse_item(items: Iterable[_T], item: object, /) -> _T: ...

# ---------------------------------------------------------------------------
# deftest の展開の引数の型(agora-redesign #2214)
# ---------------------------------------------------------------------------
# 型検査のための展開では、deftest が作る関数の引数に注記を付ける。注記の名は doeff_hy/static_view.py の
# DEFTEST_FIXTURE_TYPES(fixture の名 → ここの型の名の表・唯一の定義元)が引き、doeff-hy-check が module の頭で
# `_doeff_<型の名>` として import する。表に無い fixture(利用者が conftest で定義した物)は、書き手が `#^ T 名`
# と注記すればそれを、しなければ `object` を付ける。

class DeftestInterpreter(Protocol):
    """fixture `doeff_interpreter` — deftest の Program を走らせる関数(消費 repo の conftest が定義する)。

    展開は `(doeff_interpreter program)` か、`:env` がある時は `(doeff_interpreter program :env {…})` と呼ぶ。
    答えは展開が使わない(検の関数は None を返す)ので object。Program は答えと effect の両方で共変なので、
    `Program[object, object]` はどの Program も受ける。"""

    def __call__(
        self, program: Program[object, object], /, *, env: Mapping[str, object] = ...
    ) -> object: ...

# pytest の組み込みの fixture の型(pytest の公開の名)。
TmpPath: TypeAlias = Path
TmpPathFactory: TypeAlias = pytest.TempPathFactory
TmpdirFactory: TypeAlias = pytest.TempdirFactory
MonkeyPatch: TypeAlias = pytest.MonkeyPatch
CaptureStr: TypeAlias = pytest.CaptureFixture[str]
CaptureBytes: TypeAlias = pytest.CaptureFixture[bytes]
LogCapture: TypeAlias = pytest.LogCaptureFixture
FixtureRequest: TypeAlias = pytest.FixtureRequest
PytestConfig: TypeAlias = pytest.Config
WarningsRecorder: TypeAlias = pytest.WarningsRecorder
PytestCache: TypeAlias = pytest.Cache
Subtests: TypeAlias = pytest.Subtests
RecordProperty: TypeAlias = Callable[[str, object], None]
DoctestNamespace: TypeAlias = dict[str, object]

# ---------------------------------------------------------------------------
# defhandler / handle の展開の型(agora-redesign #2279)
# ---------------------------------------------------------------------------
# 型検査のための展開(doeff_hy/handle.hy)が、名を doeff_hy/static_view.py の HANDLER_STATIC_NAMES から
# `_doeff_<名>` として引く。実行時の展開は注記を付けず、節の終わり方の検めは clause_endings.hy から import する。

#: handler を被せる本文の答えの型。被せた結果は同じ答えの型を運ぶ(handler は本文の答えを変えない)。
HandledAnswer = TypeVar("HandledAnswer")

#: 節を回す関数の答え: effect・Pass・Resume / Transfer などの node を出し、送り返される値は続きの答えか
#: 外の handler の答えで、節の答えは handler を置いたスコープの答え(finish)— どれも型の決まらない値なので object。
ClauseRun: TypeAlias = Generator[object, object, object]
#: 節を回す関数の引数 k(続き)。
Continuation: TypeAlias = K
#: handler を被せる本文(どの Program・effect も受ける — Program は答えと effect の両方で共変)。
HandlerBody: TypeAlias = Program[HandledAnswer, object]
#: handler を被せた結果(本文と同じ答えの型)。
HandledScope: TypeAlias = WithHandler[HandledAnswer]

class Handler(Protocol):
    """defhandler が作る関数 — 本文に答え手を被せる(答えの型は本文のまま)。型の宣言の生成(static_stub.py)が
    defhandler の答えの型に使う(agora-redesign #2826)。"""

    def __call__(self, body: HandlerBody[HandledAnswer], /) -> HandledScope[HandledAnswer]: ...

#: 節 1 つの記述: (effect の型・使う操作の名・終える理由があるか)— handle.hy の ending-spec-form が作る組。
_ClauseEnding: TypeAlias = tuple[object, tuple[str, ...], bool]

def check_clause_endings(handler: str, clauses: Sequence[_ClauseEnding], /) -> None: ...
def check_clause_endings_once(
    data: Callable[..., object], handler: str, specs: Callable[[], Sequence[_ClauseEnding]], /
) -> None: ...
def fell_through(handler: str, effect_label: str, /) -> RuntimeError: ...
