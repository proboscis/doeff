# 盲検の共通の材料: doeff-hy の for/do の重ねた From

## 読める物と、やってよい事

- 対象の版: doeff の commit 6ee49cc246aae1ab9aa6b3aa3d6e9494c5d5527e。読み取り専用の写しが `/home/ubuntu/.worktrees/doeff-at-6ee49cc2-blind` に在る(.venv は用意済み)。
- 写しの file を変えない。実験の file は `/tmp/blind-<自分の役 a か b>/` の下にだけ置く。
- 実験の走らせ方の例(写しの .venv を使う):
  - Hy の program: `cd /home/ubuntu/.worktrees/doeff-at-6ee49cc2-blind && uv run --no-sync hy /tmp/blind-a/x.hy`
  - pytest(file を名指す・dir 全体や repo 全体は走らせない): `cd /home/ubuntu/.worktrees/doeff-at-6ee49cc2-blind && uv run --no-sync pytest -q <file>`
  - 型検査のための展開を pyright strict で読む: `cd /home/ubuntu/.worktrees/doeff-at-6ee49cc2-blind && uv run --no-sync doeff-hy-check --root /tmp/blind-a --json --no-cache --strict /tmp/blind-a/probe.hy`
- 写しの `docs/design-checks/` は読まない。git の log・他の会話への連絡・repo 全体の検査はしない。

## 固定した要件

- R1: for/do に From を 2 つ重ねると値ではなく内側の Traverse が返る、という報告の再現の手順を書く。
- R2: 不具合なら失敗ケースの検と直しを doeff の main に入れる。
- 仕様の読み: for/do の docstring は From を「generator bind (like SQL FROM / Haskell <- on list)」、doeff-traverse の README は
  「Multiple From binds nest (like SQL CROSS JOIN)」、specs/features/ADR-TRAVERSE-001-… は「Generator bind (list monad <-)」と書く。
  重ねた From の答えは組ごとに 1 件(list の内包の平らな列)と読んだ。

## 直す前の振る舞い(版 4b254ec2f)

`(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b))` を sequential() の下で走らせると valid_values が
`[Traverse(…), Traverse(…)]`。期待は `[11, 21, 12, 22]`。

## 直しの形(版 6ee49cc2)

- packages/doeff-hy/src/doeff_hy/macros.hy の `_gen-traverse-body` が「外の From の件の関数の中か」(in-step)を運び、
  `_place-traverse` が From の Traverse を置く所で形を決める:
  - 一番外で後ろに From が無い: Traverse の effect の式のまま(From が 1 つの for/do は直す前と同じ)。
  - 件の関数の中: Traverse を実行し(実行時は `yield`、型検査のための展開は `_doeff_perform`)、答えの Collection を件の値にする。
  - 後ろに From が残る From: 答え(Collection の Collection)を `Collection.flatten` で 1 段平らにする。一番外なら、展開の時に
    作った Traverse を実行して平らにする Program(`_doeff_do` で包んだ関数)を書き手の `<-` に渡す。
- packages/doeff-traverse/doeff_traverse/collection.py(.pyi)に `Collection.flatten` を足した: 内側の件は値と失敗を保ち、
  履歴は外の件の履歴に続け、落ちた外の件(When の skip・失敗)は 1 件のまま外の位置に残し、番号は順に振り直す。
  有効な外の件の値が Collection でなければ TypeError。型は `flatten(self: Collection[Collection[W]]) -> Collection[W]`。

## 責務(モジュール)

| id | 責務 | 持つ知識・判断 | 隠す知識 | 公開の契約 | effects | 不変条件 |
|---|---|---|---|---|---|---|
| M1 | for/do・traverse の展開(macros.hy の _gen-traverse-body・_binds-from?・_place-traverse・_traverse-form・_skip-form) | From / When / `<-` の CPS の置き方・From の Traverse の置き分け・平らにする段 | 実行時の展開と型検査のための展開の形の違い・件の関数を @do で包む回数 | `(<- x (for/do …))` の x は Collection[本体の答え]。From が 1 つなら式は Traverse の effect、2 つ以上なら Program。書き手が import するのは `_doeff_do`・`_doeff_traverse_Traverse`(When を使う時だけ `_doeff_traverse_Skip`) | なし | 本体の値は組ごとに 1 件。From が 1 つの展開は直す前と同じ |
| M2 | Collection(packages/doeff-traverse/doeff_traverse/collection.py・.pyi) | 件ごとの結果の列(値・失敗・履歴・番号)・flatten の継ぎ方 | 件の列の持ち方 | valid_values・errors・all_items・valid_items・failed_items・flatten | なし | flatten は値を変えず、件の順(外の順 → 内の順)を保つ |
| M3 | Traverse の handler(packages/doeff-traverse/doeff_traverse/handlers.py の sequential・parallel・parallel_fail_fast) | 実行の順・並列の度合い・件の失敗の扱い・Skip の受け | 件の program の組み立て | Traverse の答えは Collection[f の答え]。件の program を自分の下で走らせる | 件の program を走らせる・Spawn / Gather | 件の結果は元の番号の順 |
| M4 | 型検査のための補助(packages/doeff-hy/src/doeff_hy/static_types.pyi・static_view.py) | `<-` の答えの型・件の型の読み直し | なし | `_doeff_perform(Program[T, …]) -> T`・`traverse_item(Iterable[T], object) -> T` | なし | 展開の型は実行時の答えの型と同じ |
| M5 | Traverse の effect(packages/doeff-traverse/doeff_traverse/effects.py・.pyi) | f・items・label | なし | `Traverse(f, items, label) : EffectBase[Collection[A]]` | なし | — |

この変更で変えたのは M1 と M2。M3・M4・M5 は変えていない。

## 変更シナリオと主張

| id | 軸 | 変える要求 | 主張 | 予想の範囲 |
|---|---|---|---|---|
| S1 | effects | Traverse を答える handler を 1 つ足す(例: 件をまとめて外の API へ投げる batch の handler・件ごとに retry する handler) | 新しい handler は「Traverse に Collection で答え、件の program を自分の下で走らせる」の既存の契約(M3)だけを守れば、From を重ねた for/do も正しく答える。重ねの知識は M1 と M2 に閉じていて、handler は重ねを知らない | 変わる: 新しい handler だけ。変わらない: M1・M2・M4・M5・既存の M3 |
| S2 | effects | 平らにした件の記録の決まりを変える(例: 内側の件に外の件の番号を持たせる・落ちた外の件を答えから外す) | 継ぎ方は M2 の flatten の 1 点に在り、M1 の展開も M3 の handler も変えずに変えられる | 変わる: M2 の flatten とその検。変わらない: M1・M3・M4・M5 |
| S3 | concurrency | 重ねた From を parallel(concurrency) の handler の下で走らせる・並列の度合いを変える | 平らにした答えの順は件の番号の順で、件が終わった順に依らない。並列の度合いを変えても M1・M2 は変わらない | 変わる: なし。変わらない: M1〜M5 |

除外した軸: storage(Collection は記憶の中の値)・hardware(機体の資源に依らない)・distribution(件を別の機体で走らせるのは handler の選び方で S1 に含まれる)・simulation(展開と flatten は時間・乱数を使わない)。

主張の前提:
- P1: Traverse の handler は Traverse に Collection で答える。
- P2: Traverse の handler は件の program を自分の下で走らせる。
- P3: 書き手は for/do の答えを `<-` で受ける。

## 検査(実体・設定・検出範囲)

| 検査 | 実体 | 走らせ方 | 見る範囲 |
|---|---|---|---|
| 実行の検 | tests/test_for_do_runtime.py(From 1 つの値・skip・失敗・label・件の関数が @do 1 つ、From 2 つ・3 つの値、外の When と内の失敗の位置と履歴、parallel の下の値) | `uv run --no-sync pytest -q tests/test_for_do_runtime.py` | sequential と parallel の handler。式は Hy の文字列を module で評価する |
| 型の検 | packages/doeff-hy/tests/test_static_check_for_do.py(From 1 つ・2 つの defk を doeff-hy-check --strict で読む: 赤 0・件や答えの値の型の取り違えが赤・実行の答え) | `uv run --no-sync pytest -q packages/doeff-hy/tests/test_static_check_for_do.py` | pyright strict(型検査のための展開) |
| Collection の型 | collection.pyi の flatten の self の型 | doeff-hy-check / pyright | Collection の Collection でない列への flatten の呼び |
| 実行時の確かめ | collection.py の flatten が、有効な外の件の値が Collection でなければ TypeError | 実行時 | flatten に渡った列 |
