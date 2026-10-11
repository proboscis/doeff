# 設計検証 lt-JQVA466JCC2G49XC7TSGKZD6D0 — doeff-traverse の例 3 本を fold から Reduce へ(card ki-043ea763c926)

## この文書の目的

読み終えた人が次の 3 つを言えるようにする。

1. 書き直した例 3 本(ex04_traverse_macro・ex07・ex08)で、「何を計算するか(pipeline)」と「どう実行するか(handler の stack)」の分離が、どの変更で保たれ、どの変更で保たれないか。
2. その分離を何が機械で守り、何が守らないか。
3. 盲検 A・B の失敗例を受けて、例の何を直し、何を範囲の外として残したか。

## 完了の範囲と版

| 項目 | 値 |
|---|---|
| 範囲 | 実装完了まで |
| 基準版(変更の前) | `c096e2273d0082a56cc8d6ae38f2f572a7cf7112` |
| 書き直しの版(着地済み・L2111) | `4527ff9178e70150f9cf6b77a9aa7fde52bc176e` |
| 盲検の前の主張 | `design-before-blind.md`(着地の後・盲検の前に書き、その後は変えていない。版 7548af7da) |
| 盲検の後の直し | この記録と同じ commit の ex07・ex08 の変更 |

順を正直に書く: 実装と着地が先で、設計検証は完了の検査に求められて後から行った。

## 要件(固定)

- R1(受入 cf5ffd611): 3 本の例が doeff の main で走り、`packages/doeff-traverse/tests/test_examples_run.py` の BROKEN の表に載っていない。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 例の pipeline | 何を計算するか(各例の pipeline の defk と、Reduce に渡す畳みの 1 歩の defk) | 段の並び・値の形・畳み方 | 実行の順・並列・失敗の方針・進捗・backend の中身 | defk の契約(`:pre/:post`)と Program |
| M2 例の strategy | どう実行するか(`with-stack` に渡す list と defhandler の handler) | 失敗の方針・順と並列・進捗・backend | 業務の流れ | Program → Program の関数の list |
| M3 doeff-traverse | Traverse・Reduce・Zip・Inspect と sequential・parallel・fail_handler・normalize_to_none | Reduce は有効な件だけを件の順に畳む・件の失敗の扱い | 件の program の組み立て | effects.py・handlers.py |
| M4 doeff-hy の macro | defk の契約の必須・`[effect k]` の形の defk の拒否・defhandler | 書き方の規則 | 展開の形 | macro |
| M5 検 test_examples_run.py | examples/*.hy を全部 subprocess で走らせる | 走らない例と理由 | なし | pytest |

業務の effect の公開契約(ex07 の FetchPrice)は、盲検の後に「失敗の種類を型 `SymbolNotFound`・`MarketClosed` で伝える」を足した(下の「直し」の D2)。

## 変更シナリオ — 事前の予測と実測

| シナリオ | 事前の主張(design-before-blind.md) | 実測 | 判定 |
|---|---|---|---|
| S1 effects: 失敗の方針・backend の handler を替える | 変わるのは M2 だけ。前提: 置き換えの値の型が M1 の契約に合う | 4527ff917 では `normalize_to_none` への差し替えで `fetch-price` の `:post (: % float)` が None を拒み、成功 3 のまま(C1 の before)。M1 の契約が S1 の前提を満たしていなかった(盲検の前に設計者が見つけた・盲検 B の変種 C も同じ)。直した後は stack だけの差し替えで成功 5(C1 の after)。handler を defk で書くと展開で断られ M5 が赤(C2) | 主張は直した後に成立。直す前は M1 の契約の誤り |
| S2 concurrency: parallel(N)・進捗の表示 | 変わるのは M2 だけで答えは同じ | 答えは sequential・parallel・tqdm の有無で同じ(C3)。進捗の handler が reperform せずに委ねると内側の handler が件に届かない(C4)。**盲検 A: 失敗する件のある pipeline に進捗を足すと bar が n < total で閉じない**。正確な進捗には件ごとの結末が要り、それを持つのは M3 だけ | 答えについては成立。**進捗の正確さについては不成立**(M3 の公開契約の拡張が要る) |
| S3 simulation: mock-backend の筋書き | 変わるのは M2 だけ。答えの型を破る backend は M1 の契約が件を落とす | mock だけの差し替えで成功 2・失敗 3(C5 の c5a)。価格を文字列で答えると `fetch-price` の契約が件を落とす(C6)。**盲検 B: M1 が誤りの文面を読んで失敗を分けると、M2 の文面の変更で M1 の答えが黙って変わり、どの検査も止めない**。直した後は失敗の種類を型で読めて、文面を替えても型で分けた答えは同じ(C5 の c5b) | mock の差し替えは成立。文面を読む書き方は今も検査で止まらない(限界) |
| S4 storage | 対象外: 保存の層が無い | — | — |
| S5 hardware | 対象外: 機器に依らない | — | — |
| S6 distribution | 対象外: cluster へ件を配る handler が無い | — | — |

## 盲検 A・B と設計者の再現

記録: `blind/blind-meta.md`(起動の記録)・`blind/blind-common.md`・`blind/blind-a-input.md`・`blind/blind-b-input.md`(入力)・`blind/blind-a-return.md`・`blind/blind-b-return.md`(未加工の返答)・`blind/a-files/`・`blind/b-files/`(実験の file)。

### 盲検 A — 失敗する件のある pipeline に進捗の表示を足す

- 返答の要点: ex08 の `tqdm-progress` を ex07・ex04 の stack に足すだけ(M2 の配線)だと、答えは同じだが bar が 3/5・4/5 で止まり閉じない。M2 だけで直すには M3 の隠す規則(失敗した件の持ち越し・件の例外・Skip)を写すしかなく、そうしない道は M3 が件の結末を出す公開契約の拡張。
- 設計者の再現(`evidence/a/A-repro-before.txt`): ex07 + tqdm-progress で price の bar が `n=3/5 closed=False`、ex04 で extract・normalize が `n=4/5 closed=False`。答えは success=3 fail=2・mean=45.0 で同じ。**成立**。
- 加えて、M2 の中で Traverse の終わりを知る道を試した(C4): `(reperform effect)` を `(<- coll effect)` に替えると、件に内側の handler が届かず `UnhandledEffect: Compute` で全件が落ちる。進捗の handler は同じ k のまま reperform するしかなく、Traverse の終わりも件の結末も M2 からは知れない。

### 盲検 B — M1 が backend の誤りの文面を読んで失敗を分ける

- 返答の要点: ex07 の pipeline に「失敗の理由の内訳」と「取り直す銘柄」を足す。M1 が `str(err)` の頭を読んで分ける。mock-backend の文面 2 行だけを替える(M2 だけの変更)と内訳が `{'other': 2}` に変わり、rc 0・M5 は 12 passed。
- 設計者の再現(`evidence/b/B-repro-before.txt`): 変種 A で `{'symbol_not_found': 1, 'market_closed': 1}`・変種 B で `{'other': 2}`、どちらも rc 0。**成立**。

## 直し(この記録と同じ commit)

| id | 直し | 受けた失敗例 | 再検証 |
|---|---|---|---|
| D1 | ex07 `fetch-price` の `:post` を `(: % (| float None))` にし、docstring に「Fail の handler が None で再開した時は None」と書く | 設計者が盲検の前に見つけた S1 の反例・盲検 B の変種 C | C1: stack だけの差し替え(fail_handler → normalize_to_none)で成功 5。C6: 文字列の価格は今も拒む |
| D2 | ex07 に例外の型 `SymbolNotFound`・`MarketClosed` を置き、FetchPrice の docstring に「失敗は Fail と型で伝える・読み手は文面でなく型で分ける」と書き、mock-backend はその型で Fail を出す | 盲検 B | C5: 文面を替えた mock(c5b)でも型で分けた答えは `{'SymbolNotFound': 1, 'MarketClosed': 1}` で同じ |
| D3 | ex08 `tqdm-progress` の docstring に限界を書く: bar は f が終わった件だけを数え、失敗した件・前の段から失敗として持ち越した件・When で飛ばした件は数えないので、total に届かず開いたままになりうる。Traverse の handler は Collection を一度に答え件ごとの結末を出さず、reperform が要るので Traverse の終わりに code も走らせられない | 盲検 A | 直したのは書き物だけ。正確な進捗は M3 の拡張が要り、この card の範囲(例の書き直し)の外(下の「後続」) |

D2 で M1 に分類の code は足していない(未要求の機能を足さない)。分類の判断(どれを取り直すか)は M2 の方針で、型は業務の effect の公開契約として M1・M2 のどちらからも読める。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| strategy を pipeline の defk で書かない(M1 と M2 を分ける) | defk の展開が引数名 effect・eff・k を SyntaxError で断る | `packages/doeff-hy/src/doeff_hy/macros.hy` `_reject-handler-signature` | 例を読み込む時の展開 → M5(C2 で赤を確かめた) | 引数名だけを見る。名を変えた handler の形は通る |
| M1 の各段の値の形(backend の答えの型) | defk の `:pre/:post` 必須と実行の時の型の確かめ | macros.hy の defk、ex07 `fetch-price` の `:post (: % (| float None))` | 例の実行(C6 で文字列を拒むのを確かめた) | 実行した道筋だけ。件の中の違反は handler が件の失敗として吸い込み、rc は 0 のまま |
| 失敗の種類を文面でなく型で伝える | ex07 の FetchPrice の公開契約(`SymbolNotFound`・`MarketClosed`) | `packages/doeff-traverse/examples/ex07_proboscis_ema_rewrite.hy` | 書き物と例の実行 | **型を使うことを強制する静的な規則は無い**。M1 が `str(err)` を読む書き方(盲検 B)は今も通る。例の 1 file のために linter の規則は足さない |
| 例が今の macro と library で走る | M5 | `packages/doeff-traverse/tests/test_examples_run.py` | 日次の全体検証の package の母集団(`make test-packages`)と手の実行。**着地の検証では走らない**(L2111 の log は衝突の印の検査と uv sync だけ) | rc しか見ない(出力の値・進捗の正確さは見ない) |

import の向き・役割ごとの小さい interface・業務の意味を区別する型: 例は 1 file の読み物で、module の層を持たない。業務の意味の型は D2 の例外の型だけを足した(値の dict は例の元の形のまま)。

## 実装の証拠(checks)

| id | シナリオ | 種類 | 証拠 |
|---|---|---|---|
| C1 | S1 | 正常例 | `evidence/s1/C1-normalize-swap.txt`(直す前 成功 3・直した後 成功 5) |
| C2 | S1 | 失敗例 | `evidence/s1/C2-defk-handler-rejected.txt`(rc 1・SyntaxError・写しの木の M5 で ex04 が FAILED) |
| C3 | S2 | 正常例 | `evidence/s2/C3-same-answer-across-strategies.txt` |
| C4 | S2 | 失敗例 | `evidence/s2/C4-delegation-loses-inner-handlers.txt`(委ねる形で `Done: []`・`UnhandledEffect: Compute`) |
| C5 | S3 | 正常例 | `evidence/s3/C5-mock-swap.txt` |
| C6 | S3 | 失敗例 | `evidence/s3/C6-str-price-rejected.txt`(件 4 が `fetch-price: post-condition type error … got str`) |
| C7 | S1・S2・S3 | 正常例 | `evidence/gate/C7-examples-gate.txt`(直した例で M5 が 12 passed) |

## 後続(この card の範囲の外・依頼元へ報告する)

1. **M3 の公開契約の拡張(盲検 A)**: Traverse の handler が件ごとの結末(ok・failed・skipped・持ち越し)を外へ出す effect か hook を持てば、進捗の handler は M3 の内部の規則を写さずに正確に数え、Traverse の終わりで bar を閉じられる。盲検 A の対照実験(`blind/a-files/m3_itemdone.py.txt`・`exp4_ex04_when_itemdone.hy.txt`)が sequential の写しで形を示した。sequential・parallel・parallel_fail_fast の 3 つに要る。
2. **doeff-hy の fnk・do! の UnboundLocalError**(書き直しの途中で見つけた): 依頼元が card に切ると返事した(lt-MJ4TTEYNSXDPWFQBBQNX0695VJ)。

## 未確認事項

- 盲検の model と effort の実の値(blind-meta.md)。
- 端末(TTY)での bar の見え方(stderr を file に取っただけ)。
- 本物の backend での失敗の伝え方(mock だけで試した)。
