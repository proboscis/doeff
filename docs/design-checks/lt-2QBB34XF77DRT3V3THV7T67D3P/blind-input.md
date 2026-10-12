# 盲検の入力 — doeff-linter が機体の検の枠を取る設計(実装済み)

## 背景と利用状況

- 1 台の機体(Kubernetes の Pod・36 core)で、数十の会話(agent)が同時に検(pytest・lint・型検査)を走らせる。検の process 木の同時数を、宣言した K 本(今 8 本)の「機体の検の枠」までに絞りたい。
- 枠の正本は dotfiles の `agent/tests/machine_check_slots.py`(Python・標準 library だけ)と隣の `machine_check_slots.json`(K・置き場の dir・待ちの上限の秒)。pytest は受付の正本が本体の前に枠を取る。pytest の外の入口は `python machine_check_slots.py run [--label <名>] -- <命令…>` で包める。
- doeff-linter は Rust の CLI(doeff repo の packages/doeff-linter・maturin が venv の bin に置く)。撃つ道: 会話の Bash の直の `uv run doeff-linter`、poe の `lint`(`doeff-linter --no-log`)、git の commit-msg の hook(`doeff-linter --commit-hook` — 中で自分を editor-json の子として 2 回起こす)、agora-controllers の scripts/land_lint_gate.hy(subprocess の timeout つき)、scripts/land_focus_gate.hy の `--affected-tests`(timeout 30 秒)、書き込み直後の hook の `--stdin --path`(全体 3 秒)、日次の段、code-quality の中。

## 固定した要件

- R1: 機体の検の枠(宣言の写しで K = 1)が埋まっている間、会話が撃つ道の doeff-linter が走らずに待って保持者を名乗り、空いた拍に走る。
- R2: 枠の値(K・置き場・待ちの上限)を machine_check_slots.json の外に持たない。
- 前提: 1 枠 = 検の process 木 1 つ。枠を持つ process の子孫は 2 つ目を取らない(取ると保持者が子を待ち、子が枠を待って止まる)。値は宣言 1 つで、env で替える口は作らない。

## 設計(実装済みの形)

doeff-linter は `--list-rules` の処理の後・仕事の前に、引数から Mode を組んで `check_slot::enter(check_slot::weight(mode))` を 1 回呼ぶ。
重い mode(既定の text / json・editor-json の repo 全体・`--hook`・`--commit-hook`)で、env `DOEFF_LINTER_CHECK_SLOT` が無く、`$HOME/dotfiles/agent/tests/machine_check_slots.py` が在れば、`uv python find` の python へ自分を exec し(pid は同じ)、env `DOEFF_LINTER_CHECK_SLOT=1` を付けて `python <正本> run --label "doeff-linter <mode> <作業 dir の名>" -- <自分の path> <同じ引数>` で走り直す。正本が枠を取り(埋まっていれば保持者を名乗って待ち、上限で終了 75)、linter を子で走らせ、終われば解く。包みが SIGKILL で死ねば子も死ぬ(Linux の PR_SET_PDEATHSIG)。
軽い mode(`--list-rules`・`--split-rules`・`--affected-tests`・`--stdin`・サブコマンド `fix`)は枠を取らない。正本が無い機体は何もせず走る。python を見つけられない・exec に失敗した時は 1 行名乗って枠を取らずに走る。
日次の段 doeff-lint-whole(land_lint_gate.hy に 65 秒の linter の上限がある)は、段の命令そのものを `run` で包んで、上限の時計の前に枠を取る。

## 責務(module)

| id | 実体 | 持つ知識・判断 | 隠す知識 | 公開 |
|---|---|---|---|---|
| M-canon | dotfiles agent/tests/machine_check_slots.py + .json | 枠の値、取り方(flock・名乗り・待ちの名乗り・上限)、入れ子の判定(/proc の祖先の列・env の印)、`run` の寿命 | lock の file の名と中身・ポーリング・祖先の判定 | `run [--label <名>] -- <命令>`(終了コードは命令のまま・上限で 75)・`holders` |
| M-gate | doeff packages/doeff-linter/src/check_slot.rs | どの mode が重いか、正本の在処、再入の印、python の選び、exec で渡す事 | 枠の値と取り方(持たない) | `weight(Mode) -> Weight`・`enter(Weight)` |
| M-cli | doeff packages/doeff-linter/src/main.rs | 引数の読み、enter を呼ぶ位置 | 枠の事 | CLI |
| M-callers | agora-controllers の commit の hook・poe の lint・land_lint_gate・land_focus_gate・日次の段の宣言・code-quality | 何を lint するか、自分の上限の秒 | 枠の事(日次の段の宣言だけが `run` を名指す) | — |

## 将来の変更シナリオと事前の主張

| id | 変更 | 変わると予測する所 | 変わらないと主張する所 | 前提 |
|---|---|---|---|---|
| S1 | 機体ごとに K を変える | machine_check_slots.json の値だけ | M-gate・M-cli・M-callers | 機体ごとの宣言は正本の側の仕事 |
| S2 | 枠の置き場を別の dir へ | json の dir だけ | 同上 | 同上 |
| S3 | 取り方を変える(待ちの順を公平にする・名乗りの形・上限の扱い) | M-canon だけ | M-gate(`run --label -- cmd` と終了コードの意味が保たれる限り) | — |
| S4 | linter に新しい重い mode を足す | M-cli の引数と M-gate の Mode / weight | M-canon・M-callers | 知らない組み合わせは Heavy("lint") に落ちる |
| S5 | 検の中の検(pytest の中の linter・--commit-hook の子・包んだ段の中) | なし | 2 つ目を取らず止まらない | 子孫は /proc の祖先の列で辿れる |
| S8 | 呼び手が時間切れで包みを SIGKILL する | なし | linter は包みと共に死に、枠の外で走り続けない | Linux |

(機体をまたぐ上限と決定的な模擬は対象外: 枠は 1 台の CPU を分ける OS の資源で、doeff の effect を使わない。)

## 検(実体と実行経路)

- doeff `packages/doeff-linter/tests/check_slot.rs`(cargo test・日次の rust の段):
  - `a_linter_beyond_the_slots_waits_names_the_holder_and_runs_when_one_frees` — HOME を一時の dir に向け、本物の正本の写しと K = 1・置き場 = 一時の dir の宣言を置く。別の process が `run` で枠を持つ間、`doeff-linter --no-log .` が待ちを名乗り(保持者の pid と名・待つ者の名)、走り終えず、保持者が解いた後に枠を取って lint して終了 1(当たり 1 件)、終われば枠の名乗りが空。
  - `a_repo_scan_hands_itself_and_its_arguments_to_the_canon_once` — 偽の正本(引数と env を記して `--` の後を exec)で、重い mode が正本を 1 度だけ `run --label "doeff-linter lint repo" -- <自分> <同じ引数>` で通り、印 `DOEFF_LINTER_CHECK_SLOT=1` が付く。
  - `cheap_and_caller_bounded_modes_do_not_go_through_the_canon` — `--list-rules`・`--split-rules`・`--affected-tests`・`--stdin` は正本を通らない。
  - `a_linter_inside_the_gate_or_without_a_canon_runs_directly` — 印の付いた linter と、正本の無い HOME の linter は正本を通らず、枠の名乗りも出さない。
  - `the_linter_keeps_no_copy_of_the_slot_values` — linter の src の全 .rs の code(`//` の後を除く)に、本物の宣言の dir の値・`machine_check_slots.json`・`waitLimitSeconds`・`slot-` が無く、check_slot.rs の code に宣言の slots と waitLimitSeconds の数と同じ綴りの語が無い。
  - src/check_slot.rs の単体 4 本(weight の判定・gate_command の形・label)。
- dotfiles `agent/tests/test_machine_check_slots.py`(pytest): 枠を越えた命令・pytest は待つ・上限で 75・長命の孫が枠を持ち続けない・入れ子は 2 つ目を取らない・包みが SIGKILL で死ねば命令も死ぬ・名乗りに会話の id、ほか。

## 読める実コード(読むだけ・書き換えない)

- doeff(着地の版を含む作業木): `/home/ubuntu/.worktrees/doeff-wt-ki-ce35-dc/packages/doeff-linter/src/check_slot.rs`・`src/main.rs`(`fn main` の付近)・`src/commit_hook.rs`(`run_linter`)・`tests/check_slot.rs`
- dotfiles(着地の版 f333bd2f): `git -C /home/ubuntu/dotfiles show f333bd2f:agent/tests/machine_check_slots.py`・`git -C /home/ubuntu/dotfiles show f333bd2f:agent/tests/machine_check_slots.json`
- agora-controllers(着地の版 4e2d1721): `git -C /home/ubuntu/repos/agora-controllers show 4e2d1721:<path>` で `scripts/commit_hook.hy`(`lint-staged`)・`scripts/land_lint_gate.hy`(`editor-run`)・`scripts/land_focus_gate.hy`(`ask-affected`)・`.agents/land-queue.toml`(`doeff-lint-whole`)・`pyproject.toml`(`[tool.poe.tasks]` の lint)
