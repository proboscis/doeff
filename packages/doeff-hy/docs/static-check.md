# doeff-hy-check — Hy のコードを静的に型検査する

## 何のためか

Hy の source は pyright から見えないので、doeff + Hy のコードは静的な型検査が 1 つも効かなかった
(2026-09-23 に agora-controllers の実験 branch `wt/doeff-worker-pod` で測定: わざと入れた間違いを、
Python の DI 版は pyright で 7 種中 7 種、型を付けた Python の doeff 版は 10 種中 10 種捕まえたのに、
Hy 版は静的に 0 種)。`doeff-hy-check` は Hy の source を本物の macro で展開して pyright に読ませ、
赤を `.hy` の行で返す。

## 使い方

```sh
doeff-hy-check [PATH ...] --root <repo の根> [--import-root clients/hy] [--json]
# 例(agora-controllers)
PYTHONPATH=.:clients/hy doeff-hy-check --root . --import-root clients/hy controllers/worker
```

- `PATH` は `.hy` の file か dir。赤は `path:行:列 - error: 文言 (規則)` の形で出る。
- 赤があれば exit 1、無ければ 0、道具として走れなければ 2。
- pyright の設定は根の `pyrightconfig.json`(無ければ `pyproject.toml` の `[tool.pyright]`)を使う。
  `reportDeprecated` だけを error に上げる(下の「文の位置の Program」のため)。
- 実行する Python(`--python`、既定は道具を動かしている Python)の `sys.path` で import を解く。
  `.hy` の依存は、検める file が import する根の下の `.hy` を道具が一緒に展開する。

## 仕組み

1. 各 `.hy` を Hy の compiler で展開する。doeff-hy の macro はこの時だけ「型検査のための展開」を出す
   (切替 = `doeff_hy/static_view.py`)。source の import は実行しない(macro の `require` だけは
   Hy の compile の常として macro の module を import する)。
2. 根の中身を symlink で映した一時の木に、展開した Python を `<名>.py` として置き、pyright を撃つ。
   利用者の repo の file は変えない。
3. pyright の診断の位置を、展開した Python の node → Hy の式の位置へ戻す。macro が合成した式は、
   包んでいる利用者の式の位置を持つ(`doeff_hy/positions.hy`・traceback の直しと同じ仕組み)。

macro の展開が型のために持つ形:

| 展開 | 実行時の展開 | 型検査のための展開 |
|---|---|---|
| `defk` / `deff` の引数 | `def f(x: 'int')`(`:pre` の型を文字列の注記に写す) | 同じ |
| `deff` の戻り値 | `-> 'T'` | 同じ |
| `defk` の戻り値 | 注記なし(生成器) | `-> 'T'`(`:post` の `(: % T)` を写す — この展開の defk は yield の無い普通の関数。自分を呼び直す defk でも pyright が答えの型を推論に頼らず読める。本体に yield を直に書いた defk・型が説明の文字列だけの `:post` には付けない・agora-redesign #2308) |
| `defk` / `deff` の契約の `:tp [T]`(型の引数) | 型の引数を出さない(PEP 695 の綴りを実行時の code に出さない)。契約の isinstance では T を `object` に消す — `(of Program T object)` は `Program`・素の `(: % T)` は常に真(外側の型だけを確かめる決め #1790 と同じ向き・失敗の文は書いた型のまま) | `def f[T](body: 'Program[T, object]') -> 'T'`(答えが引数の型で決まる関数 — 本体の答えを返す包みなど — の総称。道具 `doeff_hy.static_stub` の .pyi にも載り、答えが Incomplete にならない・agora-redesign #2893)。`defp` は `:tp` を断る |
| 本体の結果(`defk` / `deff`) | 関数の頭(`:pre` の確かめの直後)で `_contract_result: 'T'` を値なしで宣言し、出口(途中の `(return x)` と最後の式)では `(_contract_result,) = (値,)` と注記なしの要素 1 つのタプルで代入する(局所変数の注記は実行時に評価されない。どの出口の値も宣言の T と突き合わされ、本体の最後が `while True` で最後の代入に届かない形でも途中の出口が T で読まれる。タプルで代入するのは、Hy が match・if の値の一時の名を代入先へ移し替え、match が case の前に置く `= None` が T と突き合わされるのを避けるため — CPython はこの代入を素の代入と同じ bytecode にする・agora-redesign #4254) | 同じ |
| 本体の結果(`do!` / `defp`) | `_contract_result: 'T' = 最後の式`(出口は最後の式 1 つ) | 同じ |
| `(<- x T e)` | `x = yield e` + isinstance の検査 | `x: 'T' = _doeff_perform(e)`(Python の `@effectful` の `x = perform(e)` と同じ形・yield を出さない — docs/24-effectful-perform.md) |
| `(! e)` | `yield e`(open-bind を通す) | `_doeff_perform(e)`(`<-` と同じ — 注記の無い generator の yield の値は Unknown・agora-redesign #2279) |
| defk を呼んだ結果 | `doeff.do.do` | 同じ型(core の `Expand[T, E]`)に、yield の無い関数の overload を足した `doeff_hy/static_types.pyi` の `do` |
| `defhandler` の `(resume v)` / `(transfer v)` | `Resume(k, v)` / `Transfer(k, v)` | core の `typed_resume(effect, k, v)` / `typed_transfer`(v を effect の答えの型と突き合わせる) |
| `defhandler` / `handle` の節を回す関数 | `(effect: '節の型の和', k)`(VM の絞り込みのための注記) | `(effect: object, k: _doeff_Continuation) -> _doeff_ClauseRun`(節の `isinstance` が型を絞る)。import は `Pass` と `WithHandler` だけ(agora-redesign #2279) |
| `defhandler` の本文を受ける関数 | `def __doeff_handler_fn__(__doeff_body__):` | `(__doeff_body__: _doeff_HandlerBody[_doeff_HandledAnswer]) -> _doeff_HandledScope[_doeff_HandledAnswer]`(本文の答えの型をそのまま運ぶ・型 = `static_types.pyi`・名の表 = `static_view.py` の `HANDLER_STATIC_NAMES`) |
| `defhandler` の `session val` / `session var` | `(yield (Get key))` の答え(`Some` の中身)か初期化の値に名を束ねる(状態に置くのは初期化の値) | 名を初期化の値に束ねる(型は初期化の式の型 — Get の答えには型が無い)。キーの名は `:=` の `Put` が引くので残す。import は `Put :as Put`・`Some :as Some`(`:=` の無い handler で未使用の赤にしない)・`doeff_hy.session` の型は `session.pyi`(agora-redesign #2293) |
| `defeffect` の `:answer` | class の本体の `__doeff_answer__: ClassVar[object] = A \| B`(答えの型の式そのもの) | `__doeff_answer__: ClassVar[object] = cast(object, (A, B))`(union の要素の tuple — 値の位置の `A \| B` を pyright が型として評価して出す赤を避け、名を読むだけにする。記帳の setattr に置くと型検査から消え、答えの型のためだけの import が reportUnusedImport になっていた。`<-` の束ねの型は変えない・agora-redesign #2322) |
| 節の終わり方の検め | `doeff_hy.clause_endings` から import | module の頭で型付きの `static_types` の同じ名を import(clause_endings は .hy なので型が見えない) |
| 文の位置の式 | `_guard_statement_value(form, …)` | `_guard_statement_value(reveal_type(form), …)` |
| 関数への属性 | `setattr(f, '__doeff_body__', …)` | 同じ |
| `deftest` の関数 | `def test_x(doeff_interpreter, tmp_path):` + `return doeff_interpreter(…)` | `def test_x(doeff_interpreter: _doeff_DeftestInterpreter, tmp_path: _doeff_TmpPath) -> None:`(fixture の名 → 型の表 = `static_view.py` の `DEFTEST_FIXTURE_TYPES`・型 = `static_types.pyi`。表に無い名は書き手の `#^ T 名` を写し、無ければ `object`)。decorator の pytest は module の頭の `_doeff_pytest`(agora-redesign #2214) |
| pytest の item の記録 | `doeff_hy.pytest_items.record_at_import(globals(), …)` | 記帳として外す(`static_check.without_bookkeeping`) |

## 捕まえるもの(2026-09-23 の測定)

同じ 10 種(typed doeff の `pyright_mistakes_main.py` と同じ並び)+ 2 種を、agora-controllers の
検体(`controllers/worker/lab/turns.hy` の `summarize`・effect `ReadShared` / `WriteShared` /
`SleepSeconds` / `RemoteJob`・`defservice`)の上に Hy で書いて測った。基準の(間違いの無い)file は 0 件。

| 間違い | 捕まえるか | 赤 |
|---|---|---|
| 1 task の引数の型違い `(summarize 1 [])` | 捕まえる | `reportArgumentType`(`conv` は `str`) |
| 2 task の引数の不足 `(summarize conv)` | 捕まえる | `reportCallIssue` |
| 3 戻り値の型違い(`:post [(: % int)]` で dict を返す) | 捕まえる | `reportAssignmentType`(最後の式の行) |
| 4a `<-` の書き忘れ(値を使う) | 捕まえる | 使った所で `reportArgumentType` |
| 4b `<-` の書き忘れ(文だけ) | 捕まえる | `doeff-hy-unperformed` |
| 5 effect の引数の型違い `(SleepSeconds "1")` | 捕まえる | `reportArgumentType` |
| 6 env の handler の不足(書く service を読みだけの env に置く) | 捕まえない | — |
| 7 読みだけの job の書き(書く task を読みだけの env へ送る) | 捕まえない | — |
| 8 宣言に無い effect を出す | Hy では書けない(effect の集合を宣言する仕組みが無い) | — |
| 9 handler の答えの型違い `(resume "yes")` | effect が答えの型を宣言していれば捕まえる | `reportArgumentType`(`typed_transfer` の value) |
| 追加: effect の答えの型違い `(<- rows int (ReadShared …))` | effect が答えの型を宣言していれば捕まえる | `reportAssignmentType` |
| 追加: 束縛した defk の戻り値の型違い `(<- rows int (summarize …))` | 捕まえる | `reportAssignmentType` |

「答えの型を宣言していれば」= effect を `(defclass [(dataclass :frozen True)] ReadShared [(get EffectBase dict)] …)`
と書くこと(doeff core の `EffectBase[T]`・docs/23-static-typing.md)。agora-controllers の検体の effect は素の
`EffectBase` なので、そのままでは 9 と追加の 1 種は捕まらない。同じ欄の effect に答えの型だけを足した写しで測ると
両方とも捕まった。まとめ: 10 種のうち、そのままの検体で 6 種(1・2・3・4a・4b・5)、effect が答えの型を宣言すれば
7 種(+9)。追加の 2 種も答えの型の宣言があれば両方。

DI 版の README の 7 種(task の引数の型違い・引数の不足・戻り値の型違い・書き忘れ・時計の引数の型違い・
guard の method 不足・読みだけの job の書き)に当てると、Hy で書ける 6 種のうち 5 種(書き忘れは 2 形とも)。

回帰の検査 = `packages/doeff-hy/tests/test_static_check.py`(自己完結の検体で、捕まえる 9 種と基準 0 件)。

## 捕まえないもの・その直し先

- **env と effect の集合**(6・7・8): service の宣言(`doeff_cluster.shared.intent.service_model.service`)の `:env` と `RemoteJob` の `:env` は import path
  の文字列で、Program が出す effect の集合も Hy では宣言しない。doeff core の `@do` は
  `Generator[E, Any, T]` の E を「出してよい effect」と読む(docs/23-static-typing.md)ので、残る仕事は
  Hy 側に E を書く口を作ること(例: defk の契約に effect の並びを書き、型検査のための展開で生成器の
  注記にし、`<-` を `yield from` にして呼んだ Program の E を足す)と、agora-controllers の
  service の宣言の env を型で表すこと。
- **答えの型を宣言していない effect**: `(<- x T (Eff …))` の突き合わせと handler の答えの突き合わせは、
  effect が `EffectBase[T]` の T を持つ時だけ効く。宣言の無い effect の答えは Any として通す。

## 誤検出を出さないための決め

- 文の位置の式(ADR-DOE-HY-001)は `macros.pyi` の deprecated の overload で捕まえる。pyright は型の
  分からない(Unknown)値でも deprecated の overload を選ぶので、同じ位置の `reveal_type` が
  Unknown / Any なら道具が赤を落とす(型の分からない値を「走らない Program」と決めつけない)。
- 型が `object` / Unknown の値を `<-` しても赤にしない(`_doeff_perform` の最後の overload)。
- `.hy` が実体の module(`doeff_hy.macros` など)の「source が見つからない」警告は落とし、
  stub の無い `.hy` の module の「解決できない import」は赤でなく注記にする(その module の型は見えない)。
- macro が同じ利用者の式を 2 か所へ写すと同じ赤が 2 回出るので、同じ位置・同じ文言の赤は 1 つにする(`(<- x e)` は `_doeff_perform(e)` になり e は 1 回だけ現れる)。

agora-controllers の `controllers/worker` の 49 file(33 + 検体・テスト)で残った 45 件は、
利用者のコードの型の穴(`#^ object` の緩い注記・`str | None` をそのまま渡す等)、repo の古い
`typings/acp_client` の stub との食い違い(19 件)、Hy 本体の compile の形(内包表記の中の `setv` が
`nonlocal` になる・2 件)だった。doeff-hy の展開に起因する誤検出は 0 件。

## 決めたこと(戻せる決定・2026-09-23・doeff-hy の作業係)

| 決めたこと | 理由 | 戻し方 |
|---|---|---|
| 型検査は「本物の macro で展開した Python」を pyright に読ませる | macro の意味の定義点を 1 つに保つ(別の投影器に macro の意味を書き写すと 2 つ目の定義点になる) | `static_check.py` を消す |
| 型のための形は切替(`static_view`)の間だけ出す(`<-` の形・型付きの `do`・`typed_resume`) | 実行時の展開・bytecode の cache・実行の費用を変えない | `_static-view?` の枝を消す |
| 引数と結果の変数の注記は実行時の展開にも付ける(文字列) | 2 つの展開の食い違いを減らす。文字列なので定義の時に評価せず、後で定義する名前の型でも落ちない | `_annotate-params` / `_result-binding` を元に戻す |
| 関数への属性は `setattr` で付ける | 型検査器が関数の未知の属性への代入を赤にするため。実行時の意味は同じ | 元の `(setv (. f attr) …)` に戻す |

## 束縛の所見(ADR-DOE-HY-006)

型の診断とは別に、macro が展開の時に出す所見を同じ出力に並べる(`doeff_hy/static_view.py` の `collect_findings` で集め、
module の直下は source の一番外の並びを `binding_forms.module_findings` で読む)。

| 規則 | 重さ | 何か |
|---|---|---|
| `doeff-hy-setv` | warning | defk・deftest・defhandler の節の本体と module の直下の setv(名前を束縛する物)— val か var を使う |
| `doeff-hy-rebind` | error | 旧い書き方どうしの同じ名前の束縛し直し(for の変数と互いに排他な枝は数えない)・module の直下の val の名前の setv・効かない module の直下の `(:= …)` |
| `doeff-hy-legacy-lazy` | warning | defhandler の旧い lazy / lazy-val / lazy-var / set!(session val / session var / := へ移す) |

`doeff-hy-rebind` は束縛し直しの強制の 1 段目で、各 repo の本線で 0 件になってから展開の誤りへ切り替える(設計の記録
`docs/design/defk-val-var-lazy/design.md` §8.3)。
