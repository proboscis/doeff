# 反例の再現と修正(設計者)

盲検の返答は blind/A-raw.md・blind/B-raw.md(未加工)。ここは設計者が自分で撃った再現と、それを受けた設計の修正。
対象版はどれも 9df950b5(`/tmp/ki08ec-proto/tree` = その `git archive`)。実 pytest は /tmp の模型の木と、
実際の repo の 1 file(1 秒未満)だけで、package の全数は走らせていない。

## A: package の赤の失敗名が repo の根からの相対にならない — **成立**

- 主張された事実: package の dir から pytest を走らせると、要約の `FAILED` 行は cwd からの相対になり、日次の道具の
  失敗名が package をまたいで衝突する。F7(失敗名は一意)は誤り。
- 再現 1(実際の repo・evidence/A-real-repo-summary-path.log): `packages/doeff-time/tests/test_get_time.py` を `-rA` で
  (1) package の dir から → `PASSED tests/test_get_time.py::...` (2) repo の根から → `PASSED packages/doeff-time/tests/test_get_time.py::...`。
  要約の行は pytest の `config.cwd_relative_nodeid` を通るので、`FAILED` 行も同じ形になる。
- 再現 2(模型・evidence/proto/e2_loop_model.log): package `a`・`b` に同名の失敗 `tests/test_cli.py::test_fail`。
  今の loop → rc 2・`a` だけ走り、日次の道具の抽出(`land.failed_test_diagnostics`)は `(1, ['tests/test_cli.py::test_fail'])`。
  提案の loop(repo の根から `pytest packages/<p>/tests`)→ rc 2・`a`・`b`・`c` を全部訪ね、抽出は
  `(2, ['packages/a/tests/test_cli.py::test_fail', 'packages/b/tests/test_cli.py::test_fail'])`・最後に `test-packages failed: a b`。
- F7 の誤りの原因: 設計者は `--collect-only` の node id(rootdir 相対)を測り、要約の行(cwd 相対)を測っていなかった。
- 分類: **隠すはずの知識の漏洩ではなく、持ち主の無い共同の不変条件**(A の 3.)。日次の道具は「失敗名は repo の根から
  pytest へそのまま渡せる」を前提にしている(land.py `_failed_name_pytest` の註)。
- 修正(design.md 改訂 1):
  1. `make-test-packages` の公開契約に「出す失敗名は repo の根から pytest へ渡せる node id」を足し、その持ち主にする。
     実装 = `cd` をやめ、repo の根から `$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e"`。
  2. 固定のテストは cwd を見ず、**出力の失敗名**を検める: 小さい模型の木(tmp_path)に同名の失敗を 2 package 置き、
     本物の Makefile の `test-packages` を本物の pytest で走らせ、`FAILED packages/a/tests/...` と `FAILED packages/b/tests/...`
     の 2 行が出ることを見る(A の「偽の runner が cwd で package を見分ける = Makefile の細部の漏洩」を解く)。
  3. cwd を package の dir から repo の根へ変える影響: package のテストの静的な候補(`Path.cwd()`・`os.getcwd()`・
     cwd 相対の `Path("tests/…")` 等)は 25 行で、全部が偽の session の作業 dir に cwd を渡すだけ(package の dir で
     あることに頼らない)。e2e の支え 1 本は log の置き場が変わるだけ。全数での確認は日次の最初の走行が行う(未確認として残す)。
- A の副次 1(package が自分の `[tool.pytest.ini_options]` を持つと root の ini と conftest が効かなくなる): 実測は
  していないが pytest の rootdir の規則どおり。現在そうした package は 0。**固定のテストに足す**(持った瞬間に赤)。
- A の副次 2(`[test-admission] full_sessions = 1` と走行数の食い違い): **不成立**。受付は 2026-09-21 に「断る」から
  「警告だけ」へ変わり、`POLICY_KEYS = ("per_run_max",)` で `full_sessions` はもう読まれない
  (~/dotfiles/agent/tests/broad_run_admission.py 1〜60 行)。
  - ただし調べる途中で別の危険を見つけた: 1 回の pytest が `per_run_max`(doeff は 400)件以下だと「変更箇所の検証」と
    判じ、60 秒の時間上限(`RUN_BUDGET_S`)を据える。日次の package の段は package ごとの session なので、400 件以下で
    60 秒を超える package は `local-timeout` で切られ得る(受付の正本が在る宿 = 手元へ倒れた日)。直す場所は受付の側
    (宣言された全体検証の中では変更箇所用の上限を据えない)で、doeff で迂回しない。**後続の card にする**。

## B: Makefile だけで母集団を縮めても固定のテストが通る — **成立**

- 主張された事実: 固定のテスト(設計 §3.4)が「期待する package の集合」を Makefile の実走から取るので、Makefile が
  root の testpaths に在る 5 package を飛ばすと、訪ねた集合も期待の集合も一緒に縮み、宣言の pin・挙動・完全性の 3 つとも
  緑のまま。root の収集エラー 1 本で 5 package の赤がどこにも載らない。
- 再現(実際の木の写し・evidence/proto/e4_pins.log の (iii)): 設計 §3.4 どおりに書いた試作を、B の差分を当てた Makefile
  (evidence/proto/makefile-cex-B-vs-proposed.diff)に掛けた。
  - 修正前の試作(期待 = Makefile の訪問先): B の報告どおり緑(B の模型 /tmp/ki08ec-cex で `pin2/3 OK visited=[pb,pc]`)。
  - 修正後の試作(期待 = file system から独立に数える): **pin2 が赤** — 「欠け ['doeff-adr', 'doeff-domain', 'doeff-time',
    'doeff-vm', 'doeff-vm-core']」「最初の package doeff-adr を赤にしたのに rc 0」「失敗の package を名指していない」。
- 分類: 設計の欠陥(固定のテストが実装から期待値を取っていた = 自己参照で、Q2 を守れない)。「glob を写さない」は
  定義点の二重化を恐れた判断だったが、テストの期待値は実装から独立でなければ意味が無い。
- 修正(design.md 改訂 1): 期待する package の集合は、テストが file system から数える(`packages/<p>/tests` の下に
  `fixtures` 以外の `test_*.py` が在る p の全部)。Makefile はそれを**ちょうど**訪ねなければならない。root の testpaths に
  在る 5 package の二重実行は、Q1(母集団は互いに独立)の費用として受け入れる(日次に 1〜2 分)。
- B の副次 1(失敗名が cwd 相対)は A と同じ事実。上で修正済み。
- B の副次 2(母集団の外は 39 本ではなく 41 本 — `docs/design/seat-home-common-instructions-AJ8C0B/model/test_*.py` 4 本):
  **成立**。F6 の数え方の誤り(`docs/` を丸ごと root 扱いにしていた)。正しくは 41 本 = `conformance/` 32・
  `docs/design/` 4・テストでない file 5(evidence/out-of-population-files.log)。F6 の「34 file」も 32 の誤り。
  除外の表は dir の接頭辞を受ける形にし、`docs/design/`(設計の検証の模型と実験)を理由つきで載せる。
  (evidence/proto/e4_pins.log の (v): 外すと pin3 が 4 本を名指して赤。)

## 修正後の試作の総当たり(evidence/proto/e4_pins.log・e4_pins_i_rerun.log)

| 場合 | 宣言の pin | loop の挙動 | 完全性 |
|---|---|---|---|
| (i) 今の本線(1 本の文字列・今の loop) | 赤(列でない) | 赤(最初の `doeff-adr` の 1 回で止まり 26 package 未訪問・失敗を名指さない) | 緑 |
| (ii) 提案(4 段・直した loop) | 緑 | 緑 | 緑 |
| (iii) B の反例 | 緑 | 赤(5 package の欠け) | 緑 |
| (iv) 除外の表から `conformance/` を外す | 緑 | 緑 | 赤(32 本) |
| (v) 除外の表から `docs/design/` を外す | 緑 | 緑 | 赤(4 本) |

試作の欠陥 1 つ: 初回の (i) で偽の runner が `tests/`(末尾 `/`)を照合できず「rc 0」と出た。照合を直して (i) だけ
撃ち直した(e4_pins_i_rerun.log — 呼ばれた回数 1 = 最初の package で止まる)。

## 処理ステージの方策の実測(evidence/proto/e3_stages.log)

提案の宣言(evidence/proto/gate-full.proposed.toml — 今の 1 本の文字列から make_proposed_decl.py で機械的に組んだ)を
日次の道具の `land_config.full_stages` に通すと受け付けられる(4 段・`build` だけ `blocks_rest`)。同じ形の 4 段を偽の命令で
`land.run_full_stages(stop_on_first_failure=False)` に掛けると:
- root が赤 → `build` pass・`root` red・`packages` pass・`rust` pass(後ろの 2 段は実行された)。畳んだ結果 red。
- build が赤 → `build` red・残り 3 段は `unexecuted`(理由「前提の処理ステージ build が成功で終わらなかったので撃っていない」)。

対照: 断面 f271ae39 の実際の台帳(evidence/daily-f271ae39.json)は処理ステージ `full` の 1 つだけで、`make test-packages`
と `make test-rust` が 1 本も走っていないのに `coverage = {"complete": true, "unexecuted": []}`。
