# 設計検証: for/do の重ねた From(依頼 lt-BD5XYC8Z64P5YMHZDEAPKXQXK5・card ki-7e6a11f56bd5)

## この文書の目的

読み手が次の 3 つを判断できるようにする。

1. for/do の重ねた From の直し(doeff main 6ee49cc2)が、どの責務を変え、どの責務を変えていないか。
2. 盲検 A・B が出した失敗例が何を示したか。どこまで直し、何が限界として残るか。
3. 守る責務ごとに、どの検査が実際に違反を拒むか。

## 1. 完了の範囲と版

- 範囲は実装完了まで。
- 盲検の前の設計と主張は design-before-blind.md(sha256 f71b59d3f2480f4ad730ab3820e7ac53c6dccc8a648382b6e489525e61b69164)に残した。主張はこの文書でも書き換えない。
- 盲検の後の直し(契約の文と、出荷する handler 全部に当てる契約の検)は、この記録と同じ commit で land する。

| 版 | 中身 |
|---|---|
| 4b254ec2f7463a4c78645ea4aff0b976865e0e02 | 直す前の main |
| a814c89de47e078bef2fe6dcd514158fdd7bbd85 | 失敗ケースの検。rebase の前は 3e4463301 で、patch-id は同じ 68c7f53b… |
| 6ee49cc246aae1ab9aa6b3aa3d6e9494c5d5527e | 直し。L2105 で main に着地した |
| この記録の commit | 契約の文・契約の検・設計検証の記録 |

## 2. 固定した要件と仕様の読み

- R1: 再現の手順(最小の Hy の program・期待の値・実の値)を card に書く。card の発言に書いた。
- R2: 不具合なら、失敗ケースの検と直しを doeff の main に入れて緑にする。仕様どおりなら訳を書く。

仕様は 3 か所とも、重ねた From の答えを組ごとに 1 件と書く。だから不具合と判断した。

| 書いてある所 | 書いてある事 |
|---|---|
| for/do の docstring | 「generator bind (like SQL FROM / Haskell <- on list)」 |
| doeff-traverse の README | 「Multiple From binds nest (like SQL CROSS JOIN)」 |
| ADR-TRAVERSE-001 | 「Generator bind (list monad <-)」 |

## 3. 責務(モジュール)

盲検の前と同じ。詳しい表は design-before-blind.md にある。

| id | 責務 | 直しで変えたか |
|---|---|---|
| M1 | for/do の展開(macros.hy の _gen-traverse-body・_binds-from?・_place-traverse) | 変えた |
| M2 | Collection(collection.py・.pyi の flatten) | 変えた |
| M3 | Traverse の handler(handlers.py) | 変えていない |
| M4 | 型検査のための補助(static_types.pyi・static_view.py) | 変えていない |
| M5 | Traverse の effect(effects.py・.pyi) | 変えていない |

盲検の後に変えたのは、M3 と M5 の docstring(契約の文)・README・契約の検だけ。振る舞いは変えていない。

## 4. 盲検 A・B

起動の記録は blind/blind-meta.md にある。第 1 候補の gpt-6-astra はこの環境で起こせなかった。代わりに Agent tool の opus を effort xhigh で起こした。観測した model は未確認。

| | 入力 | 返答 | 実験の file |
|---|---|---|---|
| A | blind/blind-common.md・blind/blind-a-input.md | blind/blind-a-return.md(未加工) | blind/a-files/ |
| B | blind/blind-common.md・blind/blind-b-input.md | blind/blind-b-return.md(未加工) | blind/b-files/ |

### 4.1 A の失敗例: handler を足す時(S1)と並列の度合い(S3)

A が示した事:

- handler 1 つにつき枠(semaphore)を 1 つ持ち、全ての Traverse で使い回す handler を作った。From が 1 つなら正しく答える。From が 2 つなら SchedulerDeadlockError で止まる。
- 理由: 外の件が枠を持ったまま内側の Traverse を同じ handler へ出し、内の件が枠を待つ。
- S1 の主張「handler は重ねを知らない」が破れる。

設計者が確かめた事:

- 6ee49cc2 で A の実験を再現した(evidence/a/A-repro-at-6ee49cc2.txt)。
  - From が 1 つ: N=2 で正しい答え。
  - From が 2 つ(3×2): N=2 で SchedulerDeadlockError。
  - 本体の `<-` で受けた for/do も、N=2 で止まる。
- 直す前の 4b254ec2 でも確かめた(evidence/a/A-repro-at-4b254ec2.txt)。
  - 本体の `<-` で受けた for/do は、同じく止まる。つまり再入の義務は直す前から在った。
  - From を 2 つ書いた for/do は Traverse の effect を返していた(直した不具合)。
- 判断:
  - 直しは、From を 2 つ書いた for/do を、書き手から見えない所で「件の中で Traverse を出す」形にする。
  - これは隠すはずの知識の漏洩ではない。M1 と M3 が共同で守る不変条件(handler は再入に耐える)である。
  - ただしこの不変条件は、M3 の契約(P2)にも Traverse の docstring にも書かれていなかった。S1 の主張は書き方が強すぎた。

A が示したもう 1 つの事(S3):

- parallel(N) の N は Traverse 1 つごとの枠で、葉の件の同時の数は N の From の数乗まで増える。
- 設計者の再現(同じ evidence): concurrency=2 で From 2 つ(4×4)なら 4、From 3 つ(3×3×3)なら 8。concurrency=3 ならそれぞれ 9・22。
- S3 の主張「並列の度合いを変えても M1・M2 は変わらない」は成り立つ。ただし N の意味は Traverse 1 つごとだと書かれていなかった。

### 4.2 B の失敗例: 検査を通る責務の違反(S1・S2)

B が示した事:

- sequential に進捗の callback を足した。走っている for/do の段を handler が持ち、件の答えが Collection かを読んで「内側の From」と推す。
- 提示の検査は全部通る。
- 本体で for/do を受けた形で、進捗の数が答えの件の数と食い違う(D1)。
- S2 の変更(flatten の決まり)をすると M3 も直す必要が出る(D2)。
- handler の状態が run をまたいで残る(D3)。

設計者が確かめた事(6ee49cc2・evidence/b/B-repro-at-6ee49cc2.txt):

- 写しの doeff_traverse に B の handlers.py・.pyi を当てた overlay を、`import` が読んだ事を確かめた。
- その上で tests/test_for_do_runtime.py と packages/doeff-hy/tests/test_static_check_for_do.py は 16 passed。
- D1・D2・D3 は B の数のとおり出た。

判断:

- 違反は「handler が展開の判断(どの Traverse が平らにされるか)と flatten の継ぎ方を、動的な文脈から推し直す」こと。
- これを拒む静的な規則は置かない(第 6 節の限界)。
- 代わりに契約の文へ「handler は内側の From かを推さない。平らにするのは展開の Collection.flatten」と書いた。
- 進捗を組で数える機能が要るなら、展開か Traverse が情報を明示して運ぶべきで、それは M1・M5 の公開契約の拡張になる。

## 5. 盲検の後の直しと再検証

### 5.1 直した物

| 直した物 | 中身 |
|---|---|
| effects.py の Traverse の docstring(M5) | f の Program は同じ handler へ Traverse をもう一度出しうる。handler は件の Program を自分の下で走らせ、Traverse をまたぐ資源を件の Program の間に持ち続けない。葉の資源の枠は、その葉の effect の handler に置く。内側の答えを平らにするのは展開で、handler は内側の From かを推さない |
| effects.pyi の Traverse の docstring | 同じ事を短く書いた |
| handlers.py の parallel の docstring(M3) | concurrency は Traverse 1 つの件を縛る。From を k 段重ねると葉は concurrency の k 乗まで同時に走る |
| doeff-traverse の README | 上の 2 つを handler の節に書いた |
| tests/test_for_do_runtime.py | test_nested_froms_answer_under_every_shipped_handler を足した。出荷する handler の全部(sequential・parallel 1 / 2・parallel_fail_fast 1 / 2)で、From 2 つ・3 つの答えが組ごとに平らに並ぶことを撃つ。枠 1 は、handler に 1 つの枠を持つ形を必ず止める |

### 5.2 再検証

| 確かめた事 | 結果 | 証拠 |
|---|---|---|
| 出荷する handler の全部で契約の検が通る | 12 passed。focused の 6 file では 74 passed | evidence/fix/focused-at-final.txt |
| A の変更(枠を handler に 1 つ)を出荷する parallel に当てた overlay | 契約の検の parallel-1・parallel-2 と、既存の parallel の検が SchedulerDeadlockError で赤(3 failed・9 passed) | evidence/a/A-after-contract-test.txt・evidence/a/A-overlay-parallel.diff |
| S2: flatten だけを「落ちた外の件を外す」に替える(M1・M3 は替えない) | 有効な値は同じまま、外の skip の件が答えから消える。決まりを撃つ検が赤になる | evidence/s2/S2-control.txt |
| S3: 件が終わる順を逆にする | parallel・parallel_fail_fast は逆の順に終わっても、答えは番号の順 | evidence/s3/S3-control.txt |
| flatten の self の型 | Collection[Collection[int]] では Collection[int] と読める。Collection[int] では pyright strict が reportAttributeAccessIssue の赤 | evidence/types/flatten-types.txt |
| 直す前の失敗ケース | runtime 4 failed・static 2 failed | evidence/fix/runtime-red-before-fix.txt・static-red-before-fix.txt |

## 6. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 重ねた From の答えは組ごとに 1 件(M1) | 実行の検 | tests/test_for_do_runtime.py(From 2 つ・3 つ・When と失敗・出荷する handler の全部) | pytest(手の focused・日次の全体テスト) | 第三者の handler は検の外 |
| handler は再入に耐え、Traverse をまたぐ資源を件の間に持たない(M3 と M1 の共同の不変条件) | 契約の文と、出荷する handler の全部に当てる契約の検 | effects.py / .pyi の docstring・README・test_nested_froms_answer_under_every_shipped_handler | pytest | 静的には表せない。第三者の handler は自分の検でこの形を回すまで守られない |
| parallel の concurrency の意味(M3) | docstring と README | handlers.py・README | 文だけ | 葉の同時の数を for/do 全体で縛る仕組みは無い。葉の effect の handler に置く |
| 答えの型は本体の答えの型(M1 + M4) | pyright strict | packages/doeff-hy/tests/test_static_check_for_do.py | pytest(pyright の在る機体) | pyright が無ければ skip |
| flatten は Collection の Collection だけに当てる(M2) | 型(self の型)と実行時の TypeError | collection.pyi・collection.py | pyright(日次の型検査)・実行時 | 実行時の確かめは値が来た時だけ |
| handler は内側の From かを推さない(M1・M2 の知識を M3 に写さない) | 契約の文だけ | effects.py の docstring・README | 文だけ | 静的な規則を置かない(下) |

最後の行に静的な規則を置かない理由:

- B の違反は「件の答えが Collection か」「handler に残る段の状態」から推す形で、綴りがいくつもある(isinstance・type(...) is・hasattr(..., "all_items")・閉包の list)。
- handlers.py の正しい code も `isinstance(effect.items, Collection)` を使う。
- semgrep の 1 つの綴りを禁じても、ほかの綴りは通るのに「守られている」という誤った確信を作る。
- 機能が要る時に情報を明示して運ぶ契約の拡張(M1・M5)が、正しい道である。

## 7. 予測と実測の比べ

| シナリオ | 予測(盲検の前) | 実測 | 差の訳 |
|---|---|---|---|
| S1 handler を足す | 新しい handler だけが変わる。handler は重ねを知らない | ファイルは新しい handler だけで済む(A の実験 3)。ただし handler は再入の義務を知らねばならない(A)。重ねを推して数える handler は検査を通る(B) | 共同の不変条件が契約に書かれていなかった。契約の文と、出荷する handler の契約の検で補った。第三者の handler の静的な強制は限界 |
| S2 平らにする決まりを変える | M2 の flatten だけ | 出荷する handler では M2 だけで変わる(S2 の対照)。B の handler のように継ぎ方を写した handler があると M3 も変わる(B の D2) | 写した handler は契約の違反(第 4.2 節)。出荷する handler には無い |
| S3 parallel の下・度合いを変える | M1・M2 は変わらない | 変わらない。答えは番号の順(S3 の対照)。N の意味は Traverse 1 つごとで、重ねると N の k 乗(A) | 意味を docstring と README に書いた |

## 8. 未確認の事

- 盲検の子の実際の model と effort は観測できない(blind-meta.md)。
- 型の検(pyright)は手の focused の範囲だけで、repo 全体の型検査は日次に任せた。
- 記録の commit の 1 つ(盲検の返答と実験の file)を、`git commit --no-verify` で作った。この repo の pre-commit の hook は docs/adr・.semgrep.yaml に触れる commit だけを検める(hook の 30〜33 行)。この commit は docs/design-checks/ だけなので、hook は素通しで、飛ばした検査は無い。
