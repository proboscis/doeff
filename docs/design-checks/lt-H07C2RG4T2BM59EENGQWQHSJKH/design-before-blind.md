# 設計検証 lt-H07C2RG4T2BM59EENGQWQHSJKH — 盲検の前の主張(card ki-e3a936788353)

## この文書の目的

読み終えた人が、盲検 A・B の前に固定した次の 2 つを言えるようにする。

1. 件ごとの結末の契約(`ItemOutcome`)について、どの将来の変更がどの責務に閉じると主張したか。
2. その主張を何が機械で守り、何が守らないか。

この文書は盲検の前に書き、その後は変えない。盲検の結果と直しは `design.md` に書く。

## 範囲と版

| 項目 | 値 |
|---|---|
| 範囲 | 実装完了まで(実装は着地済み) |
| 基準版(変更の前) | `a2254d66cfc52f9fa9c3b4829bff2d76191bccb3`(3293bc4cc の親) |
| 実装の版 | `3293bc4cc357ec9f6ed02e07d4facf75de0ae882`(L2136) |
| 順 | 実装と着地が先。設計検証は完了の検査に求められて後から行う |

## 要件(固定)

| id | 受入の条 |
|---|---|
| R1 | c481163fe: doeff-traverse の sequential・parallel・parallel_fail_fast の 3 つが件ごとの結末を公開の契約で出し、その契約が doeff の main に在る |
| R2 | c6eeb01ac: 失敗する件のある pipeline で tqdm-progress の bar が n == total で閉じる事を確かめるテストが doeff の main に在る |

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
|---|---|---|---|---|
| M1 利用者の pipeline | 何を計算するか(for/do・Traverse を出す Program) | 段の並び・件の値 | 実行の順・並列・進捗・結末の知らせ | `Traverse(f, items, label)` を出す Program |
| M2 結末の消費者 | 件の結末をどう数え・見せるか(`progress_bars`・ex08 の `tqdm-progress`・利用者の handler) | bar の開閉・数え方・表示 | なし(M3 の内部の規則を持たない) | `ItemOutcome` を効果の引数の注記で名指す handler。`progress_bars(make_bar: BarFactory)` |
| M3 Traverse の handler | 件をどう走らせ、結末をどう決めて知らせるか(sequential・parallel・parallel_fail_fast) | 結末の分類(ok・failed・skipped・carried)・持ち越しの規則・Skip・例外の捕まえ方・知らせる時と場所・名指す handler が在るかの判定 | 件の Program の組み立て・内側の handler の付け直し・並列の task | Traverse の答え `Collection` と、件ごとの `ItemOutcome` |
| M4 公開の語彙 | 結末の型 | `ItemOutcome(traverse, label, total, outcome, item)`・`ItemOutcomeKind`・`ProgressBar`・`BarFactory` | なし(公開) | `effects.py`・`effects.pyi`・`handlers.pyi` |
| M5 静的な閉じの検査 | Program と handler の stack に答えの無い効果が残らないか | handler の節が出す効果の読み | なし | doeff-effect-analyzer の `analyze_handler`・doeff-cluster の foundation closure |
| M6 検 | 契約の検 | 期待する結末の列 | なし | `tests/test_item_outcomes.py` ほか(下の表) |

M3 の公開の約束:

- 件の結末が決まるたびに `ItemOutcome` を出す(parallel は終わった順・走らせる件の前の持ち越しは最初の件を走らせる時)。
- 出す所は、Traverse と handler の間の handler(件の Program に付け直す内側の handler)の下で、その外に handler 自身が居て、誰も受けなければ None で答える。handler の外へは出さない。
- 内側の handler のどれかが効果の引数の注記に `ItemOutcome`(か subclass)を名指す時だけ出す。
- 走らせる件の無い Traverse(件 0・持ち越しだけ)は出さない。parallel_fail_fast は最初の失敗で止まり、取り消した件は出さない。

## 変更シナリオと事前の主張

| id | 軸 | 変える要求 | 主張(変わる・変わらない) | 前提 |
|---|---|---|---|---|
| S1 | concurrency | 実行の方針を sequential・parallel(N)・parallel_fail_fast の間で替える(進捗の handler は付けたまま) | 変わるのは M2 の stack の 1 要素だけ。M1・M3・M4 は変わらない。数える件・total・bar の閉じ方は同じ(parallel_fail_fast が止めた Traverse を除く) | 進捗の handler が Traverse の handler の内側に在る |
| S2 | effects | 件の結末の新しい消費者を足す(例: 段ごとに失敗した件と理由を記録する handler、tqdm を別の表示へ替える) | 変わるのは M2(handler を 1 つ足す・stack に 1 要素足す)だけ。M3 の規則(持ち越し・Skip・例外)を M2 へ写さない。M1・M3・M4 は変わらない | 消費者が注記で `ItemOutcome` を名指す |
| S3 | simulation | 決定的な模擬・検で、bar を記録係に替える・結末を記録して比べる | 変わるのは M2 の `make_bar` の差し替え(か記録用の handler)だけ。M3 は変わらない | `make_bar` が `BarFactory` の形 |
| S4 | storage | 対象外 | 保存の層が無い。結末を保存するなら S2 の消費者 1 つになる | — |
| S5 | hardware | 対象外 | 機器に依らない | — |
| S6 | distribution | 対象外 | cluster へ件を配る Traverse の handler は無く、未要求なので作らない | — |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 知らせは M3 の外へ漏れない(M5 の閉じを壊さない) | analyzer の静的な読みを検で固定: 3 つの handler のどの節も `ItemOutcome` を出さず、`ItemOutcome` に答える節を持つ | `packages/doeff-effect-analyzer/tests/python/test_carried_builders.py::test_doeff_traverse_item_outcomes_stay_inside_the_handler` | pytest(日次の全体と手の focused) | analyzer の読みに頼る。新しい Traverse の handler は検の表に足さないと外れる |
| 知らせの中身は Traverse の答えと同じ(分類は M3 の 1 か所) | 検: 知らせた `item` の列が答えた `Collection.all_items` と一致 | `packages/doeff-traverse/tests/test_item_outcomes.py::test_each_item_reports_the_outcome_the_traverse_answers`(sequential・parallel) | pytest | parallel_fail_fast は失敗までの個別の検だけ |
| 名指す handler が無い stack の動きは今のまま | 検 | `test_nothing_is_reported_unless_a_handler_inside_names_item_outcome`・`test_sequential_reads_handlers_once.py`・`test_traverse_effect_type_filter.py` | pytest | 費用は検で測らない |
| M2 は M3 の規則を知らずに数える | 公開の型(`ItemOutcomeKind` の Literal・`BarFactory`・`ProgressBar` の Protocol)と検 | `effects.pyi`・`handlers.pyi`・`test_progress_bars_close_at_total_when_items_fail` | pyright(日次)・pytest | 消費者が Traverse を横取りして f を包む書き方を禁じる静的な規則は無い |
| 受入 R2 | 検 | `test_progress_bars_close_at_total_when_items_fail`(sequential・parallel) | pytest | — |

## 主張の前提(まとめ)

- 進捗の handler(M2)は Traverse の handler(M3)の内側、本体の handler と同じ側に置く。
- 消費者は効果の引数の注記で `ItemOutcome` を名指す。
- Traverse の handler は sequential・parallel・parallel_fail_fast の 3 つだけ。
