# 設計(盲検の前に固定した主張)— doeff-linter が機体の検の枠を取る(依頼 lt-2QBB34XF77DRT3V3THV7T67D3P・card ki-ce35bf38508d)

完了の範囲: **実装完了まで**(3 repo の main に着地済み)。この file は盲検 A・B を起こす前に書いた主張で、盲検の後に書き換えない。
盲検の後の結論・修正は design.md に書く。

## 対象の版

| repo | 変更の前(基準) | 着地 | 中身 |
|---|---|---|---|
| doeff | b62dd053306076a272e9dc6b8e6eefb13b40d85a | 86ecd134449adb6d33e3a2ce348c9bc01d96ba02(L2149) | packages/doeff-linter/src/check_slot.rs・main.rs・lib.rs・tests/check_slot.rs・.agents/code-quality.d/doeff-linter-1828.json |
| dotfiles | fb9dde8a8(master) | f333bd2ffc46d6c9c2d6036be1da58bbd6b113e1(L5630) | agent/tests/machine_check_slots.py(run_held の die_with_wrapper・command_label)・test_machine_check_slots.py |
| agora-controllers | 92d06f055 | 4e2d1721f2ad98d13074ed00b238b1ca975ba782(L6327) | pyproject.toml の doeff-min-commit・.agents/land-queue.toml の段 doeff-lint-whole |

## 固定した要件(card ki-ce35bf38508d の受入)

- R1(ca12666c6): 機体の検の枠(宣言の写しで K = 1)が埋まっている間、会話が撃つ道の doeff-linter が走らずに待って保持者を名乗り、空いた拍に走る検が main で緑。
- R2(ce7f38cb1): 枠の値(K・置き場・待ちの上限)を machine_check_slots.json の外に持たない(写しの値が 0 か所である事を検で示す)。
- 前提(親 ki-aabe38bf85ef の決め): 1 枠 = 検の process 木 1 つ。枠を持つ process の子孫は 2 つ目を取らない。値は宣言 1 つ・env で替える口は作らない。

## 責務(module)

- M-canon(dotfiles agent/tests/machine_check_slots.py + machine_check_slots.json): 枠の値(本数・置き場・待ちの上限)、取り方(flock・名乗り・待ちの名乗り・上限で 75)、入れ子の判定(/proc の祖先の列・env の印)、命令の包み `run` の寿命(命令を子で走らせ、終われば解く・包みが SIGKILL で死ねば命令も死ぬ)。隠す知識: lock の file の名・中身の形・ポーリング・祖先の判定。公開: `run [--label <名>] -- <命令…>`(終了コード = 命令のまま・上限で 75)・`holders`。
- M-gate(doeff packages/doeff-linter/src/check_slot.rs): どの mode が重いか(Weight::Light / Heavy(名))、正本の在処(HOME の下の相対 path)、再入の印(env DOEFF_LINTER_CHECK_SLOT)、正本を走らせる python の選び(`uv python find`)、自分を exec で渡す事。隠す知識: 枠の値と取り方(持たない)。公開: `weight(Mode) -> Weight`・`enter(Weight)`(渡せたら戻らない・渡せなければ 1 行名乗って戻る)。
- M-cli(doeff packages/doeff-linter/src/main.rs): 引数を読み、`--list-rules` の後・仕事の前に Mode を組んで enter を 1 回呼ぶ。枠の事は何も判じない。
- M-callers(agora-controllers の commit の hook scripts/commit_hook.hy・poe の lint・scripts/land_lint_gate.hy・scripts/land_focus_gate.hy・日次の段の宣言 .agents/land-queue.toml・dotfiles の doeff_linter_agentcli.py・code-quality): 何を lint するかと自分の上限の秒だけを持つ。枠は知らない。例外 = 日次の段 doeff-lint-whole の宣言は、上限の時計(land_lint_gate の 65 秒)より前に枠を取るため `run` で段を包む。

## 将来の変更シナリオと事前の主張

| id | 軸 | 変更 | 主張(変わる / 変わらない) | 局所化の理由・前提 |
|---|---|---|---|---|
| S1 | hardware | 機体ごとに K を変える(36 core の zeus と小さい機体) | 変わる: machine_check_slots.json の値だけ。変わらない: M-gate・M-cli・M-callers | linter は値を持たず正本の `run` に渡すだけ(R2)。前提: 機体ごとの宣言は正本の側の仕事 |
| S2 | storage | 枠の置き場を /tmp から別の dir へ | 変わる: json の dir だけ。変わらない: M-gate ほか | 同上 |
| S3 | effects | 取り方を変える(待ちの順を公平にする・名乗りの形を変える・上限の扱いを変える) | 変わる: M-canon だけ。変わらない: M-gate(契約 `run --label -- cmd` と終了コードの意味が保たれる限り) | linter は取り方を写していない |
| S4 | effects | linter に新しい重い mode(例: --watch・新しい repo 全体の出力形式)を足す | 変わる: M-cli(引数)と M-gate の Mode / weight の 1 行。変わらない: M-canon・M-callers | 重さの判定は M-gate の 1 か所。知らない組み合わせは Heavy("lint") に落ちる(取る側へ倒れる) |
| S5 | concurrency | 検の中の検(pytest の中の linter・--commit-hook の子の linter・包んだ段の中の linter) | 変わらない: どれも 2 つ目を取らず止まらない。入れ子の判定は M-canon(祖先の列)、linter 自身の子は M-gate の再入の印 | 前提: 子孫は祖先の列で辿れる(Linux の /proc) |
| S6 | distribution | 機体をまたいだ枠(cluster 全体の上限) | 不適用(excluded) | 枠は 1 台の CPU を分ける物で、機体をまたぐ上限は coordinator の task の枠(PER-HOST)が持つ |
| S7 | simulation | 決定的な模擬の環境 | 不適用(excluded) | 枠は OS の資源(flock・/proc・exec)で doeff の effect を使わない。agora_sim は linter の process を起こさない |
| S8 | effects | 呼び手が時間切れで包みを SIGKILL する(subprocess の timeout) | 変わらない: 命令(linter)は包みと共に死に、枠の外で走り続けない(M-canon の die_with_wrapper) | 前提: Linux の PR_SET_PDEATHSIG |

## 強制の方法(実装済み)

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 値は宣言 1 つ(R2) | 検: linter の src の code(註を除く)に、本物の宣言の dir・slots・waitLimitSeconds の値と宣言の file の名・`slot-` が無い | doeff tests/check_slot.rs::the_linter_keeps_no_copy_of_the_slot_values | doeff の cargo test(日次の rust の段 make test-rust) | 数の値(8・1200)は check_slot.rs だけを見る(他の module の偶然の一致を避けるため)。値を別の綴りで書いた写し(例 8 を 4+4)は見ない |
| 枠を越えた linter は待つ(R1) | 検: 正本の写し・K = 1 の HOME で、保持者の間は待って名乗り、空いた拍に走る | tests/check_slot.rs::a_linter_beyond_the_slots_waits_names_the_holder_and_runs_when_one_frees | 同上 | 正本の無い機体ではこの検は赤(未実行を合格にしない) |
| 重い mode だけが正本を 1 度通る・同じ引数 | 検 + 型(Weight の enum・Mode の struct) | tests/check_slot.rs の 3 本・src/check_slot.rs の単体 4 本 | 同上 | main.rs が enter より前に返す新しい mode を足すと、その mode は門を通らない(呼ぶ位置は型で縛っていない) |
| 包みの死で命令も死ぬ | 検(直しを外すと赤) | dotfiles agent/tests/test_machine_check_slots.py::test_a_command_dies_with_its_killed_wrapper | dotfiles の pytest | Linux だけ |
| 段の上限の前に枠 | 宣言(段の命令の頭)と註 | agora-controllers .agents/land-queue.toml の doeff-lint-whole | 日次 | 新しい「上限つきの呼び手」が段を包むかは慣習(静的な検は無い) |
