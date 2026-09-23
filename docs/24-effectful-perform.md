# 24. `@effectful` と `perform` — effect を Python の文法のまま書く

`@effectful` の関数は、effect を `perform(effect)` という普通の関数呼び出しで書く。
pyright は答えの型と「出してよい effect」の両方を検査し、実行時は import の時に
`perform(e)` を `(yield e)` へ書き換えて、今の `@do` と同じ Program にする。

```python
from doeff import EffectBase, Effects, effectful

@dataclass(frozen=True)
class ReadClock(EffectBase[int]):          # 答えの型は int(docs/23-static-typing.md)
    pass

@effectful
def elapsed(perform: Effects[ReadClock], since: int) -> int:
    now = perform(ReadClock())             # now: int
    return now - since

@effectful
def place_turns(perform: Effects[ReadShared | WriteShared | ReadClock]) -> int:
    rows = perform(ReadShared("turn/"))    # rows: ReadShared の答えの型
    spent = perform(elapsed(400))          # 別の Program も perform で走らせる(spent: int)
    ...
    return placed

elapsed(400)    # Expand[int, ReadClock] — まだ走らせていない Program(@do と同じ型)
```

- `perform` は関数の**最初の引数**(method では `self` の次)で受け、`Effects[...]` に
  この関数が出してよい effect を並べる。戻り値の注記は普通の `-> int` のまま書く。
- 呼ぶ側から見た型は `Callable[P, Expand[T, E]]`(`perform` の引数は消える)。`@do` と同じ型
  なので、`@do` の Program と混ぜて使える。

## pyright が捕まえるもの

`tests/test_static_typing.py` のわざとの間違い 8 種を `perform` の形に書き直し、8 種とも赤に
なることを `tests/test_effectful.py::test_pyright_types_perform_and_catches_each_mistake` で
固定している。

| 間違い | 赤 |
|---|---|
| 答えの型違い `now: str = perform(ReadClock())` | `reportAssignmentType` |
| 宣言に無い effect `perform(Delete("row"))`(`Effects[ReadClock]` の中) | `reportArgumentType` |
| 呼んだ Program の effect が宣言に無い `perform(elapsed(1))`(`Effects[WriteShared]` の中) | `reportArgumentType` |
| Program の結果の型違い `label: str = perform(elapsed(1))` | `reportAssignmentType` |
| handler の答えの型違い `typed_resume(effect, k, "now")` | `reportArgumentType`(handler は今のまま生成器で書く) |
| `Spawn` した子の effect が宣言に無い `perform(Spawn(elapsed(1)))` | `reportArgumentType` |
| `Wait` の結果の型違い `text: str = perform(Wait(task))` | `reportAssignmentType` |
| 引数の型違い `elapsed("1")` | `reportArgumentType` |
| おまけ: `@effectful` の外で `perform` を呼ぶ | `reportUndefinedVariable`(`perform` はどこにも定義されていない) |

仕組み: `Effects[E]` の `__call__` は `(self: Effects[X], effect: _Performable[X, T]) -> T`。
`_Performable[X, T]` は「出す effect が X・答えが T」の形で、effect は自分自身を出して
`EffectBase[T]` の T を答え、`Expand[T, E]` は E を出して T を答え、`Spawn(p)` は自分と p の
effect を出して `Task[T]` を答える(docs/23 の型の宣言をそのまま読む)。`Effects` は E について
反変なので、X が宣言の E に入らなければ引数の型が合わず赤になる。

## 書ける場所と、書き換えが断るもの

`perform` は `@effectful` の関数本体そのものの中でだけ書ける(生成器を止められるのはそこだけ)。
次のものは import の時の書き換えが `SyntaxError`(file と行を指す)で断る。同じ検査を import
せずに走らせる道具が `doeff-effectful-check PATH ...`(CI や pre-commit に置ける)。

- `@effectful` でない関数が `Effects[...]` の引数を取る
- lambda・内包表記(list / set / dict)・生成器式・入れ子の普通の関数・class の本体の中の `perform`
  (入れ子の関数は `@effectful` にすれば、中で `perform` を書いて `perform(inner(...))` で呼べる)
- `perform` を値として渡す・代入する・返す
- `@effectful` の本体に `yield` / `yield from` / `await` を直接書く(effect の書き方を 1 つにする)
- `perform(...)` に渡す値が 1 つでない・関数に `perform` の引数が無い・`perform` に `Effects[...]` の注記が無い・`async def`

pyright だけでは lambda や内包表記の中の `perform` は赤にならない(型の上では普通の関数の
呼び出しなので)。そこは書き換えと `doeff-effectful-check` が受け持つ。

## import の時の書き換え

- 登録: `doeff.install_import_hook("mypkg")` を、その package を import する前に呼ぶ。
  package の `__init__.py` に `install_import_hook(__name__)` と書くのが基本の形で、その下の
  module がすべて対象になる。登録した package の中でも、source に `effectful` の文字が無い
  module は書き換えずに普通に compile する。
- 登録していない module で `@effectful` を使うと、`@effectful` がその場で `TypeError` を出し、
  どの package を登録すればよいかを文言で示す(書き換えの印 `__doeff_effectful__` が
  module に無い、または版が違う時)。
- 書き換えの中身: `perform(e)` → `(yield e)`、`perform` の引数を削除。結果は `@do` が受け取る
  生成器の関数と同じで、`@do` と同じ `program_factory`(doeff/do.py)で Program にする。
- traceback: 書き換えた node は元の呼び出しの位置(行と列)を持つので、traceback は元の
  source の行を指す(`test_traceback_of_a_handler_error_points_at_the_perform_line`)。
- bytecode の cache: `__pycache__/<名>.<cache_tag>-doeff-effectful-<版>.pyc`。版は書き換えの
  module(`doeff/_effectful_rewrite.py`)の中身の hash なので、書き換えが変わると cache の名前が
  変わり、古い cache は読まれない(pytest の assertion rewriting が名前に版を入れるのと同じ考え方)。
  中身の有効性は標準の import と同じく source の mtime と大きさで判定する。

## 実行時の意味と費用

- handler(`Resume` / `Transfer`)・入れ子の Program・method・`Spawn` / `Wait` / `Gather`・
  `Cancel` で `finally` が走ること・まだ走らせていない Program を cloudpickle で別の process へ
  送ることを、`tests/test_effectful.py` で確かめている。
- 書き換えた関数の bytecode は `@do` + `yield` で書いた同じ関数と一致する(`co_code` が同じ)。
  effect 1 回の費用(2026-09-23・`Transfer` の handler・20 万回の最良値):
  `yield` 0.90 µs・`perform` 0.91 µs・`yield from` 1.00 µs。`perform` は `yield` と同じで、
  `yield from` より約 10% 安い。

## Hy の側

doeff-hy の型検査のための展開(`doeff-hy-check`・packages/doeff-hy/docs/static-check.md)は、
`(<- x T e)` を `x: 'T' = _doeff_perform(e)` と出す。Python の `x = perform(e)` と同じ形で、
`yield` も `yield from` も使わずに同じ答えの型が付く。Hy にはまだ effect の集合を書く口が無いので、
`_doeff_perform` は宣言の突き合わせをせず、型の分からない値を Any として通す。実行時の Hy の展開は
今のまま `(yield e)` でよい — それが `@effectful` の書き換えの結果と同じ形だから。

## `yield from` の形との関係

docs/23 の `x = yield from Eff(...)` の形は、effect と Program の実行時の `__iter__`(Rust の
`src/typing_support.rs`)と、型の宣言の `__iter__`(`doeff_vm/__init__.pyi` など)の 2 つで
成り立っている。`perform` の形は**実行時の `__iter__` を使わない**(書き換えの結果は `yield e`)が、
**型の宣言の `__iter__` は `_Performable` がそのまま読んでいる**。

- 実行時の `__iter__` は、`yield from` の書き方を廃止すれば外せる。
- 型の宣言の `__iter__` は、外すなら「出す effect と答えの型」を運ぶ別の宣言(例えば型だけの
  専用の属性)へ移し、`_Performable` をそれに向け直してから外す。
- `yield from` の書き方をやめるかどうか(利用者のコードへの影響)は別に決める。

## 決めたこと(戻せる決定・2026-09-23)

| 決めたこと | 理由 | 戻し方 |
|---|---|---|
| 出してよい effect は戻り値の注記ではなく `perform: Effects[E]` の引数で宣言する | `-> Program[int, E]` と書くと本体の `return n` が pyright の赤になり(`int` は `Program[int, E]` でない)、しかも大域の `perform(e: EffectBase[T]) -> T` は宣言に無い effect を捕まえられない(実測: 正しいコードで 2 件の赤・宣言に無い effect は 0 件)。引数にすると 8 種すべてを捕まえる | `Effects` と `effectful` の overload を変える |
| 登録は package の名前で明示する(`install_import_hook`) | 全 module の source を読んで判定する方式は、関係の無い import の費用を増やす。登録を忘れた時は `@effectful` が名指しの誤りを出すので黙って壊れない | `EffectfulFinder.covers` を変える |
| cache の版は書き換えの module の hash | 版の数字を手で上げ忘れると古い書き換えの cache が読まれる。hash なら書き換えを変えるたびに必ず変わる | `REWRITE_VERSION` を定数にする |
| `Spawn` の型の宣言(`__iter__`)で、自分自身を `Spawn[_T, _E]` でなく `Spawn[Any, Any]` として出す | `perform(Spawn(p))` で p が何も出さない(`E = Never`)時、strict の pyright が宣言の並びから `Spawn[Unknown, Unknown]` を埋めて「部分的に Unknown」の赤を出したため(`yield from` の形では出ない)。宣言の並びは元から `Spawn[Any, Any]` と書くので、捕まえる物は変わらない。型だけの overload で止める案は、本線の型引数の既定値の変更と組むと `yield from Spawn(...)` を「合う overload が無い」にしたので採らない | scheduler.py の `__iter__` の注記の `Spawn[Any, Any]` を `Spawn[_T, _E]` に戻す |
| Hy の型検査のための展開の `(<- x e)` を `_doeff_perform(e)` にした(`yield` を出さない) | Python の形と揃え、e を 1 回だけ出す。agora-controllers の `controllers/worker` で赤が変わらないこと(57 件・同じ集合)を確かめた | macros.hy の `_bind-yield` の静的な枝を `_doeff_bound(e, (yield e))` に戻す |

## まだ確かめていないこと・範囲の外

- handler を `@effectful` で書くこと(handler は今のまま生成器と `typed_resume` で書く)。
- pytest の test file(`test_*.py`)の中に直接 `@effectful` を書くこと: pytest の assertion
  rewriting と import hook が重なるので、`@effectful` の関数は test file の外の module に置く。
- `program_signature` は `@effectful` の関数の E を読まない(`perform` の引数ごと注記が消えるため)。
- Python 3.10〜3.13 での実測(この測定は 3.14 の free-threaded 版)。
