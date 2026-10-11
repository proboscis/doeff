# 設計検証 lt-JQVA466JCC2G49XC7TSGKZD6D0 — 盲検の前の主張(card ki-043ea763c926)

この file は盲検 A・B を起こす前に書き、書いた後は変えない。盲検の後の結論は design.md に書く。

## 完了の範囲と順

- 範囲: **実装完了まで**。変更は doeff main `4527ff9178e70150f9cf6b77a9aa7fde52bc176e`(L2111)に着地済み。
- 基準版(変更の前): `c096e2273d0082a56cc8d6ae38f2f572a7cf7112`。
- 順を正直に書く: 実装・着地が先で、この主張は着地の後・盲検の前に書いた(完了の検査が設計検証を求めたため)。

## 要件(固定)

- R1(card ki-043ea763c926 の受入 cf5ffd611): `ex04_traverse_macro`・`ex07_proboscis_ema_rewrite`・`ex08_tqdm_progress` が doeff の main で走り、`packages/doeff-traverse/tests/test_examples_run.py` の BROKEN の表に載っていない。
- 依頼の直し: 3 本を Reduce で書き直し、BROKEN の表から外す。

## 何を変えたか(4527ff917)

- `fold`(2026-04-07 の fa37eaaf1 で消えた macro)を `(Reduce f init collection)` にした。`f` は module の段の名のある defk(畳みの 1 歩)。
- `[effect k]` の形の defk の handler(ex04 `replace-with-zero`・ex07 `mock-backend`・ex08 `tqdm-progress`・`compute-backend`)を `defhandler` にした。
- 残りの defk に `:pre/:post` を書いた。
- `test_examples_run.py` の BROKEN の表から 3 行と `_FOLD` を外した。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M1 例の pipeline | 何を計算するか(業務の流れ)。ex04 `pipeline`・`add-to-total-count`、ex07 `index-event`・`generate-signal`・`fetch-price`・`count-pair`・`news-pipeline`、ex08 `pipeline`・`add-value` | 段の並び・各段の値の形・畳み方 | 実行の順・並列の度合い・失敗の方針・進捗の表示・backend の中身 | defk の契約(`:pre/:post`)と Program を返す関数 | effect を出すだけ(Traverse・Reduce・Zip・Inspect・Fail(try_call 経由)・業務の effect)。実行しない | なし、Program の値 | 失敗の扱いを書かない(try/except・Result の照合が無い) |
| M2 例の strategy | どう実行するか。handler の stack(`with-stack` に渡す list)と `defhandler` の handler | 失敗の方針(fail_handler・replace-with-zero)・順と並列(sequential・parallel)・進捗(tqdm-progress)・backend(mock-backend・compute-backend) | 業務の流れ | Program → Program の関数(defhandler の値)を list に並べる | 効果の解釈(Resume・reperform・Await) | `run` の 1 回 | 同じ Program を別の stack で走らせられる |
| M3 doeff-traverse | Traverse・Reduce・Zip・Inspect の effect と sequential・parallel・fail_handler・normalize_to_none | Reduce は有効な件だけを件の順に畳む・件の失敗の扱い | 件の program の組み立て | effects.py・handlers.py の公開の名 | 件の program を走らせる | handler の install の間 | Reduce の答えは init の型・失敗した件は畳まない |
| M4 doeff-hy の macro | defk の契約の必須・`[effect k]` の形の defk の拒否・defhandler・traverse | 書き方の規則 | 展開の形 | macro の名 | なし、展開の時 | なし | 契約の無い defk・handler の形の defk は展開の時に SyntaxError |
| M5 検 test_examples_run.py | examples/*.hy を全部 subprocess で走らせる | 走らない例とその理由(BROKEN の表) | なし | pytest の検 | 子 process で例を走らせる | 検の 1 回 | 表に無い例は rc 0・表の例は rc ≠ 0(走るようになったら行を外せと赤) |

## 変更シナリオと主張(盲検の前)

- **S1 effects(適用)**: 失敗の方針や backend の handler を替える・足す(例: Fail を 0 や None に置き換える、retry、mock を本物の backend に替える)。
  - 主張: 変わるのは M2(stack の list と handler)だけ。M1 の pipeline と畳みの 1 歩、M3、M4 は変わらない。
  - 理由: M1 は失敗の扱いを書かず effect を出すだけで、失敗した件は M3 の Reduce が畳まない。
  - 前提: 置き換えの値の型が M1 の契約の型に合う。
- **S2 concurrency(適用)**: sequential を parallel(N) に替える・進捗の表示を足す。
  - 主張: 変わるのは M2 の stack だけで、答えは同じ。M3 の Reduce は件の順に畳む。
  - 理由: 並列は M3 の handler が持ち、tqdm-progress は Traverse の f を包み直して同じ k のまま reperform する(内の handler が件に届く)。
- **S3 simulation(適用)**: ex07 の mock-backend の筋書き(どの symbol が落ちるか・値)を替える。
  - 主張: 変わるのは M2 の mock-backend だけ。M1 は変わらない。backend が業務の effect の答えの型を破ると、M1 の defk の契約がその件を落とす(黙って畳まない)。
- **S4 storage(対象外)**: 例は記憶の中の Collection だけを持ち、保存の層が無い。
- **S5 hardware(対象外)**: 例は機器に依らない(ex08 の async_compute は sleep だけ)。
- **S6 distribution(対象外)**: 例は 1 process の読み物で、cluster へ件を配る handler は doeff-traverse に無い。足すなら S2 と同じ M2 の差し替えの見込みだが、試す物が無い。未要求の分散基盤は先回りしない。

## 強制方法(今ある物)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1 と M2 を分ける(handler を defk で書かない) | defk の展開が `effect`・`eff`・`k` の引数名を SyntaxError で断る | `packages/doeff-hy/src/doeff_hy/macros.hy` の `_reject-handler-signature` | 例を読み込む時の macro の展開 → M5 | 引数名だけを見る。名を変えた handler の形は通る |
| M1 の各段の値の形 | defk の `:pre/:post` 必須・型の確かめ | macros.hy の defk | 実行の時(契約の確かめ) | 実行した道筋だけ |
| 例が今の macro と library で走る | M5 | `packages/doeff-traverse/tests/test_examples_run.py` | 日次の全体検証の package の母集団(`make test-packages`)と手の実行。着地の検証(doeff の land-queue の focus-gate)は走らせない(L2111 の log は衝突の印の検査と uv sync だけ) | 着地では止まらない。rc しか見ない(出力の値は見ない) |

## 盲検の前に設計者が見つけた反例(盲検には渡さない)

- S1 の主張に対して: ex07 で stack の `fail_handler` を `normalize_to_none` に替えるだけの変更で、`fetch-price` の `:post [(: % float)]` が None を拒み、BADCO・CLOSED の件が `AssertionError: fetch-price: post-condition type error: return value expected float, got NoneType` で落ちる(成功 3・失敗 2 のまま)。mock-backend の註は「Fail の handler が再開すれば続く」と言う。方針を替えるのに M1 の契約も変える必要がある = S1 の前提「置き換えの値の型が M1 の契約の型に合う」を 4527ff917 の契約が満たしていない。
