# 設計検証: for/do の重ねた From(依頼 lt-BD5XYC8Z64P5YMHZDEAPKXQXK5・card ki-7e6a11f56bd5)— 盲検の前の設計と主張

この文書は盲検 A・B を起こす前に保存した。盲検の後に主張を書き換えない(後の結論は design.md に書く)。

## 完了の範囲

実装完了まで。直しは doeff main 6ee49cc246aae1ab9aa6b3aa3d6e9494c5d5527e(L2105)に着地済み。
失敗ケースの検は a814c89de47e078bef2fe6dcd514158fdd7bbd85、直しの前の版は 4b254ec2f7463a4c78645ea4aff0b976865e0e02。
この設計文書は実装の後・盲検の前に書いた(設計を先に書いてから実装した、ではない)。

## 固定した要件

- R1: 再現の手順(最小の Hy の program・期待の値・実の値)を card に 1 つ書く。
- R2: 不具合なら失敗ケースの検と直しを doeff の main に入れ、緑にする。仕様どおりならその訳を書く。
- 仕様の読み(R2 の判断の元): for/do の docstring は From を「generator bind (like SQL FROM / Haskell <- on list)」、
  doeff-traverse の README は「Multiple From binds nest (like SQL CROSS JOIN)」、ADR-TRAVERSE-001 は「Generator bind (list monad <-)」と書く。
  どれも、重ねた From の答えは組ごとに 1 件(list の内包の平らな列)を言う。

## 現状の障害(直す前)

`(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))` を sequential() の下で走らせると valid_values が
`[Traverse(…), Traverse(…)]`。macros.hy の _gen-traverse-body は From を全て「Traverse の effect の式を置く」で展開し、
2 つ目からの From は外の件の関数の return の位置に置かれて実行されなかった。

## 責務(モジュール)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の契約 | effects | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M1 | for/do・traverse の展開(packages/doeff-hy/src/doeff_hy/macros.hy の _gen-traverse-body・_binds-from?・_place-traverse・_traverse-form・_skip-form) | From / When / `<-` の CPS の置き方。From の Traverse を「一番外 = 式のまま / 件の関数の中 = 実行」に置き分け、後ろに From が残る From の答えを平らにする | 実行時の展開と型検査のための展開(_static-view?)の形の違い・件の関数を @do で包む回数 | `(<- x (for/do …))` の x は Collection[本体の答え]。From が 1 つなら式は Traverse の effect そのもの、2 つ以上なら Program。書き手が import するのは `_doeff_do`・`_doeff_traverse_Traverse`(When を使う時だけ `_doeff_traverse_Skip`) | なし(展開の時に form を作るだけ) | なし(macro の展開) | 本体の値は組ごとに 1 件。From が 1 つの展開は直す前と同じ |
| M2 | Collection(packages/doeff-traverse/doeff_traverse/collection.py・.pyi) | 件ごとの結果の列(値・失敗・履歴・番号)。flatten の継ぎ方 = 内側の件は値と失敗を保ち、履歴は外の件の履歴に続け、落ちた外の件は 1 件のまま残し、番号は順に振り直す | 件の列の持ち方(_items) | valid_values・errors・all_items・valid_items・failed_items・flatten(型 `flatten(self: Collection[Collection[W]]) -> Collection[W]`)。有効な外の件の値が Collection でなければ TypeError | なし(値を作るだけ) | なし | flatten は値を変えず、件の順(外の順 → 内の順)を保つ |
| M3 | Traverse の handler(packages/doeff-traverse/doeff_traverse/handlers.py の sequential・parallel・parallel_fail_fast) | 実行の順・並列の度合い・件の失敗の扱い・Skip の受け | 件の program の組み立て(内側の handler と自分を被せ直す) | Traverse の答えは Collection[f の答え]。件の program を自分の下で走らせる(件の中の Traverse も同じ handler が受ける) | 件の program を走らせる・Spawn / Gather(parallel) | handler の install の間 | 件の結果は元の番号の順(parallel は番号で並べ直す) |
| M4 | 型検査のための補助(packages/doeff-hy/src/doeff_hy/static_types.pyi の _doeff_perform・traverse_item、static_view.py の TRAVERSE_STATIC_NAMES) | `<-` の答えの型・件の型の読み直し | なし | `_doeff_perform(Program[T, …]) -> T`・`traverse_item(Iterable[T], object) -> T` | なし | なし | 展開の型は実行時の答えの型と同じ |
| M5 | Traverse の effect(packages/doeff-traverse/doeff_traverse/effects.py・.pyi) | f・items・label | なし | `Traverse(f: Callable[[I], Program[A, …]], items: Iterable[I], label) : EffectBase[Collection[A]]` | なし | なし | — |

この変更で変えたのは M1(展開)と M2(flatten を足した)だけ。M3・M4・M5 は変えていない。

## 変更シナリオと主張(盲検の前)

| id | 軸 | 適用 | 変える要求 | 主張 | 予想の範囲(変わる / 変わらない) |
|---|---|---|---|---|---|
| S1 | effects | 適用 | Traverse を答える handler を 1 つ足す(例: 件をまとめて外の API へ投げる batch の handler・件ごとに retry する handler) | 新しい handler は「Traverse に Collection で答え、件の program を自分の下で走らせる」の既存の契約(M3)だけを守れば、From を重ねた for/do も正しく答える。重ねの知識(内側を実行して平らにする)は M1 と M2 に閉じていて、handler は重ねを知らない | 変わる: 新しい handler だけ。変わらない: M1・M2・M4・M5・既存の M3 |
| S2 | effects | 適用 | 平らにした件の記録の決まりを変える(例: 内側の件に外の件の番号を持たせる・落ちた外の件を答えから外す) | 継ぎ方は M2 の flatten(と _spliced)の 1 点に在り、M1 の展開も M3 の handler も変えずに変えられる | 変わる: M2 の flatten とその検。変わらない: M1・M3・M4・M5 |
| S3 | concurrency | 適用 | 重ねた From を parallel(concurrency) の handler の下で走らせる・並列の度合いを変える | 平らにした答えの順は件の番号の順で、件が終わった順に依らない。並列の度合いを変えても M1・M2 は変わらない | 変わる: なし(handler の引数だけ)。変わらない: M1〜M5 |
| S4 | storage | 除外 | — | Collection は記憶の中の値で、保存の仕方を持たない。保存は呼び手の責務で、この変更は触らない | — |
| S5 | hardware | 除外 | — | macro の展開と記憶の中の列の継ぎ方は機体の資源に依らない | — |
| S6 | distribution | 除外 | — | 件を別の機体で走らせる仕組みは handler(M3)の選び方で、この変更は handler を変えない。分散の handler は S1 の「handler を足す」に含まれる | — |
| S7 | simulation | 除外 | — | 展開と flatten は時間・乱数を使わず決定的。決定的な再生は handler の選び方(sequential)の性質で、この変更は変えない | — |

主張の前提:
- P1: Traverse の handler は Traverse に Collection で答える(effects.pyi の EffectBase[Collection[A]])。
- P2: Traverse の handler は件の program を自分の下で走らせる(sequential は install_self、parallel は _program_handler(handler) で被せ直す)。
- P3: 書き手は for/do の答えを `<-` で受ける(式を値として保存して後で実行する使い方は契約の外 — 一番外の Traverse の effect を作る時機は直す前と同じく式を評価した時)。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 重ねた From の答えは組ごとに 1 件(M1) | 実行の検(値・3 段・When と失敗の位置と履歴・parallel) | tests/test_for_do_runtime.py の test_two_froms_give_one_value_per_pair・test_three_froms_give_one_value_per_triple・test_skips_and_failures_of_both_froms_keep_their_places・test_two_froms_under_the_parallel_handler_give_the_same_values | pytest(日次の全体テスト・手の focused) | 試した handler は sequential と parallel だけ。parallel_fail_fast と第三者の handler は検に入っていない |
| 答えの型が本体の答えの型(M1 + M4) | pyright strict で、From 2 つの defk の答えが tuple[int, ...] に入る(赤 0)・有効な値に文字列を足すと赤 | packages/doeff-hy/tests/test_static_check_for_do.py の test_two_froms_module_has_no_strict_error・test_two_froms_answer_used_as_the_wrong_type_is_red・test_two_froms_run_to_one_value_per_pair | pytest(pyright の在る機体) | pyright が無い機体では skip |
| flatten は Collection の Collection だけに当てる(M2) | 型: `flatten(self: Collection[Collection[W]])` — Collection[int] に呼ぶと pyright の赤。実行時: 有効な外の件の値が Collection でなければ TypeError | collection.pyi・collection.py の _spliced | pyright(日次の型検査)・実行時 | 実行時の TypeError は、第三者の handler が Collection でない値で答えた時に初めて出る |
| From が 1 つの展開は変わらない(M1) | 既存の実行の検(Traverse に渡る関数が @do で包んだ 1 つの関数)・静的な検(件の補助の名) | tests/test_for_do_runtime.py の test_the_step_is_one_do_function_per_traverse ほか・test_static_check_for_do.py | pytest | — |

handler が P2 を守る事(件の program を自分の下で走らせる)を静的に強いる検査は無い。守らない handler では、件の中の Traverse は外側の handler の組に任され、受け手が無ければ未処理の effect で落ちる(予想・盲検の後に確かめる)。
