# 盲検の共通の材料(依頼 lt-JQVA466JCC2G49XC7TSGKZD6D0)

## 対象

- repo: doeff。読み取り用の作業木: `/home/ubuntu/.worktrees/doeff-wt-ki-043e-dc`(版 `4527ff9178e70150f9cf6b77a9aa7fde52bc176e` に、この記録の file だけを足した物)。
- 変更の前の版: `c096e2273d0082a56cc8d6ae38f2f572a7cf7112`。変更の差分は `git -C /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc show 4527ff917` で読める。
- 対象の file:
  - `packages/doeff-traverse/examples/ex04_traverse_macro.hy`
  - `packages/doeff-traverse/examples/ex07_proboscis_ema_rewrite.hy`
  - `packages/doeff-traverse/examples/ex08_tqdm_progress.hy`
  - `packages/doeff-traverse/tests/test_examples_run.py`
  - 依存先(読むだけ): `packages/doeff-traverse/doeff_traverse/effects.py`・`handlers.py`・`collection.py`、`packages/doeff-hy/src/doeff_hy/macros.hy`(defk・fnk・do!・traverse・`_reject-handler-signature`)、`packages/doeff-hy/src/doeff_hy/handle.hy`(defhandler)

## 要件(固定)

- R1: 3 本の例が doeff の main で走り、`test_examples_run.py` の BROKEN の表に載っていない。
- 例は `hy examples/<名>.hy` で走らせる読み物。設計の考え: Program は値(pipeline は計算の組み立てで、実行しない)・方針は handler(失敗の扱い・並列・進捗・backend を外から差し込む)・実行は `run` の 1 か所だけ。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 不変条件 |
|---|---|---|---|---|---|---|
| M1 例の pipeline | 何を計算するか。ex04 `pipeline`・`add-to-total-count`、ex07 `index-event`・`generate-signal`・`fetch-price`・`count-pair`・`news-pipeline`、ex08 `pipeline`・`add-value` | 段の並び・値の形・畳み方 | 実行の順・並列・失敗の方針・進捗・backend | defk の契約(`:pre/:post`)と Program | effect を出すだけ | 失敗の扱いを書かない |
| M2 例の strategy | どう実行するか。`with-stack` に渡す handler の list と defhandler の handler | 失敗の方針・順と並列・進捗・backend | 業務の流れ | Program → Program の関数の list | 効果の解釈 | 同じ Program を別の stack で走らせられる |
| M3 doeff-traverse | Traverse・Reduce・Zip・Inspect と sequential・parallel・fail_handler・normalize_to_none | Reduce は有効な件だけを件の順に畳む | 件の program の組み立て | effects.py・handlers.py | 件の program を走らせる | 失敗した件は畳まない |
| M4 doeff-hy の macro | defk の契約の必須・handler の形の defk の拒否・defhandler・traverse | 書き方の規則 | 展開の形 | macro | なし | 契約の無い defk・`[effect k]` の defk は展開の時に SyntaxError |
| M5 検 test_examples_run.py | examples/*.hy を全部 subprocess で走らせる | 走らない例と理由(BROKEN の表) | なし | pytest | 子 process | 表に無い例は rc 0・表の例は rc ≠ 0 |

## 変更シナリオと設計者の主張

- S1 effects: 失敗の方針や backend の handler を替える・足す(Fail を 0 や None に置き換える・retry・mock を本物の backend に替える)。主張: 変わるのは M2 だけ。M1・M3・M4 は変わらない。前提: 置き換えの値の型が M1 の契約の型に合う。
- S2 concurrency: sequential を parallel(N) に替える・進捗の表示を足す。主張: 変わるのは M2 の stack だけで答えは同じ。tqdm-progress は Traverse の f を包み直して同じ k のまま reperform する。
- S3 simulation: ex07 の mock-backend の筋書きを替える。主張: 変わるのは M2 の mock-backend だけ。backend が業務の effect の答えの型を破ると M1 の defk の契約がその件を落とす。
- storage・hardware・distribution は対象外(保存の層が無い・機器に依らない・cluster へ件を配る handler が無い)。

## 検査の実体と検出範囲

| 検査 | 実体 | 検出範囲 | 実行経路 |
|---|---|---|---|
| handler の形の defk の拒否 | macros.hy `_reject-handler-signature`(引数名 effect・eff・k) | 展開の時 | 例を読み込む時 |
| defk の契約の必須と型の確かめ | macros.hy の defk(`_validate-pre-type-checks`・`_validate-post-type-check`) | 展開の時(契約の有無)・実行の時(型) | 例の実行 |
| 例が走る | `test_examples_run.py`(rc だけを見る・出力の値は見ない) | 全 example | 日次の全体検証の package の母集団と手の実行。着地の検証では走らない |

## 走らせ方

- 例: `cd /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc/packages/doeff-traverse && uv run python -m hy examples/<名>.hy`
- 自分で作った実験の file は `/tmp/<自分の dir>/` に置き、`cd /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc/packages/doeff-traverse && uv run python -m hy /tmp/<自分の dir>/<file>.hy` で走らせられる。
- 検: `cd /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc && uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q`(約 10 秒)
- 作業木の file は変えない(examples/ に file を足すと検の母集団が変わる)。
