# 設計検証 lt-F2VNSACTHQE41RVE9CC16NPBR3 — 盲検の前の主張(package の loop の検の一巡の共有と sh の偽の runner)

この文書の目的: 盲検 A・B を起こす前に、設計者の主張・予測した変更範囲・強制方法を固定する。盲検の後に書き換えない
(盲検の後の結論は design.md に書く)。

## 完了の範囲

実装完了まで。実装は 2 つの commit:

| commit | 中身 | 状態 |
|---|---|---|
| 4bb2e090c | 2 本の検がこの repo の一巡を module の fixture で共有し、偽の runner を sh にした | main に着地(L2181) |
| c854bccaf | 「全部緑なら rc 0」を模型の木で見る検を足し、make を回す部分を `_run_test_packages` 1 つにした | branch wt/dc-make-test-packages-once(着地の前) |

基準の版(変更の前)= 10addc6473923419ef6acdb18dcb9044b87c7602。設計検証は実装の後に始めた(依頼 class=investigate の
設計検証の要求を、着地の後に終わりの検査で知った)。主張は c854bccaf の版について書く。

## 要件(固定)

card acp:kanban-issue:ki-e8f54a86b0a8 の本文と受入の条:

- R1: `tests/test_daily_test_population.py` の 2 本(`test_make_test_packages_visits_every_package_after_a_red`・
  `test_make_test_packages_lists_every_slow_test`)は、本物の `make test-packages` を偽の runner で回す一巡を 1 回だけ回し、
  その記録を module の fixture で共に読む。
- R2: 偽の runner は sh。path は cd と `pwd -P` で解く(`realpath -m` は Mac に無い)。
- R3: root の束の 2 本の合計が load 65〜70 で 2 秒以下(直す前と並べて card に書く)。
- R4: 赤を見逃さない(package を 1 つ飛ばす・秒の flag を落とす変異が赤)。
- R5(設計者が足した): 一巡の共有の前に検が見ていた性質を 1 つも失わない。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects | 寿命 |
|---|---|---|---|---|---|---|
| M-loop | Makefile の target `test-packages`(検の対象・この変更では変えない) | 母集団の選び(packages/*/tests に検の file・PACKAGE_EXTRA_TEST_ROOTS)・赤の後も続ける・最後に `test-packages failed:` で名指す・PACKAGE_PYTEST_DURATIONS | session の起こし方の中身(PACKAGE_UV_RUN に委ねる) | make の変数 PACKAGE_UV_RUN・PACKAGE_PYTEST_DURATIONS・PACKAGE_EXTRA_TEST_ROOTS・rc・要約の行 | process の起動(session ごと) | make の 1 回 |
| M-expect | 期待の導出(`_expected_packages`・`_package_extra_test_roots`・`_DAILY_DURATIONS_FLAGS`・`_stage_run`・`_named_failed_packages`)— 変えない | 期待の package の集合を file system から(Makefile の実走からは取らない)・外の根は Makefile に聞く・秒の flag・要約の行の読み方 | なし | 関数 | file system を読む・make を 1 回(外の根を聞く) | 検ごと |
| M-runner | 偽の runner `_FAKE_RUNNER`(sh) | 受けた cwd と引数を 1 行(タブ区切り)で記録に足す・引数の 1 つが DOEFF_FAKE_RUNNER_FAIL の dir に物理の path で当たる時だけ rc 1 | path の解き方(cd -- と pwd -P・CDPATH を外す)・記録の形 | env DOEFF_FAKE_RUNNER_RECORD・DOEFF_FAKE_RUNNER_FAIL・rc | 記録の file へ追記 | session の 1 回 |
| M-make | `_run_test_packages` と `_MakeRound` | 本物の Makefile を `-f` で名指し `-C <木>` で回す・runner と記録を tmp に置く・記録を (cwd, 引数) の列に読む・120 秒で打ち切る | 記録の形(検には `_MakeRound.calls: list[tuple[str, list[str]]]` だけを渡す) | `_run_test_packages(repo, tmp, fail, *overrides) -> _MakeRound` | subprocess(make)を doeff の subprocess_handler で 1 回・tmp に file を書く | 呼びごと |
| M-round | fixture `packages_round` と `_PackagesRound` | この repo の一巡を module で 1 回・赤にする package(期待の集合の名前順で最初) | なし | `_PackagesRound(expected, first, made)` | M-make を 1 回 | module(scope="module") |
| M-checks | 検 3 本の判定 | 訪ねた集合 = 期待・外の根を訪ねた・赤の時 rc≠0 と名指し = [first](visit)/ 全 session に秒の flag(flags)/ 全部緑の模型の木で全部を訪ね rc 0・名指し無し(green) | なし | pytest の検 | なし(記録を読むだけ・green は M-make を 1 回) | 検ごと |
| M-route | 検の実行経路 | root の pyproject の `[[tool.doeff.contract-tests]] prefix = ""`(make test-changed が変更ごとにこの file を走らせる)・日次の段 root(`pytest -m 'not e2e'`) | なし | 設定 | なし | 設定 |

## 変更シナリオと事前の主張

| id | 軸 | 変える要求 | 主張 | 予測する変更範囲 |
|---|---|---|---|---|
| S-hw | hardware | Mac で走らせる(BSD の userland・/bin/sh は bash の POSIX mode・tmp は /var → /private/var の symlink の下・`realpath -m` 無し) | 機体に依るのは M-runner だけで、POSIX の sh の builtin(`cd --`・`pwd -P`・`[ -d ]`・printf)だけを使う。比べるのは物理の path なので、symlink の下の tmp や repo でも同じ dir を同じと数える | なし(M-runner も変えない) |
| S-st | storage | 記録の形を変える(引数にタブや改行が入る・記録を file でなく fd へ出す) | 記録の形を知るのは書き手 M-runner と読み手 M-make だけで、検は `_MakeRound.calls` の (cwd, 引数) の列しか見ない | M-runner・M-make |
| S-eff | effects | Makefile の loop が runner の起こし方を変える(package の dir へ cd して `tests` を渡す・引数を足す・母集団の loop を足す・要約の行の書き方を変える) | M-loop の変更に M-runner・M-make・M-round を合わせる必要は無く、退行(package を飛ばす・赤で止める・赤を飲む・外の根を飛ばす・秒の flag を落とす・緑で rc≠0)は M-checks のどれかが赤にする。cd してから相対で渡す形も、runner が自分の cwd から解くので同じ package と数える | M-loop だけ |
| S-conc | concurrency | (a) root の pytest を xdist で並べ、3 本が別の worker に載る (b) Makefile の loop が package を並べて走らせ、runner の呼びが重なる | (a) fixture は worker ごとに 1 回で、正しさは変わらず、費用は共有の検を受けた worker の数(≤ 2 = 直す前と同じ)まで。tmp は worker ごとに別 (b) runner は 1 呼び 1 行を printf 1 回で O_APPEND の file に足すので行が混ざらず、検は順に依らない(集合・all) | (a) なし (b) M-loop だけ |
| S-sim | simulation | 新しい検が別の模擬の結末を要る(rc 5「集めた検 0 件」の package・赤 2 つ・時間切れ) | 模擬の結末は M-runner が env から決める。新しい結末は M-runner(と env の名 1 つ)と、それを使う新しい検が `_run_test_packages` で自分の一巡を回す形で足し、共有の一巡(最初の package だけ赤)と既存の検は変えない | M-runner・新しい検 |
| X-dist | distribution | 対象外 | 検は 1 機体の 1 つの pytest の process の中で、自分の checkout(REPO_ROOT = `__file__` から)を読む。機体をまたぐ状態を持たない。日次の段は自分の作業木で走る | — |

主張の前提:

- P1: M-loop は session を全部 `$(PACKAGE_UV_RUN)` 経由で起こす(経由しない session は検から見えない)。
- P2: 引数に改行とタブを含まない(Makefile の今の引数 `pytest <dir> -m "not e2e" --durations=0 --durations-min=10`)。
- P3: 赤にする package は期待の集合の名前順で最初で、それは M-loop の glob の順でも最初(どちらでも検は順に依らない)。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 記録の形は M-runner と M-make だけが知る | frozen dataclass `_MakeRound`(検は calls の列を受ける) | tests/test_daily_test_population.py `_MakeRound`・`_run_test_packages` | 慣習(Python の型は実行時に強制しない・tests/ の型検査は日次) | 検が record の file を自分で開く形は静的に拒まない |
| M-loop の退行が赤になる | 振る舞いの検 3 本(visit・flags・green) | 同 file の 3 本 | make test-changed(contract-tests prefix "" — どの変更でも走る)・日次の段 root | session を PACKAGE_UV_RUN の外で起こす形は見えない(P1) |
| 一巡は module で 1 回 | pytest の fixture の scope="module" | `packages_round` | 同上 | 新しい検が自分で 2 つ目の一巡を回す形(費用の退行)を拒む検は無い。2 秒の予算を守る検も無い(load に依る) |
| runner は Python を起こさない | sh の script の文字 | `_FAKE_RUNNER` | 同上 | runner を Python に戻す形を拒む検は無い(費用だけの退行) |
