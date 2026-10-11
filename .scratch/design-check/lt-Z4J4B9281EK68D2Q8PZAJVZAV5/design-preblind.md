# 設計検証 — 素の commit の hook が stage した file に semgrep の規則を当てる(L2051)

- 依頼: lt-Z4J4B9281EK68D2Q8PZAJVZAV5(class investigate・card ki-2ff6e513ee7f / ki-8c0bb496bfab)
- 対象の版: doeff `15e6fc9c4ef0d9de254adc7b70f5395d69a5de31`(L2051 の着地・origin/main の先端)
- 完了の範囲: **実装完了まで**(実装は L2051 で main に入った)。Pod へ効くのは、L2051 の後に準備した worker の Pod から(下の限界 L1)。
- 著者: 会話 c-GPQPMMK7CZKZE1Z0WVXRKDYS92・claude-opus-5-5・effort high(自己申告)

## 1. 固定した要件

- R1 汎用の package(doeff-cluster・doeff-records・doeff-claude-code・doeff-codex など)の註・docstring・文書に使い手の系の名
  (agora・agora-controllers・acp・kanban)を足す commit を、**commit の時に**止める(semgrep の規則
  `doeff-packages-have-no-application-vocabulary` ほか `.semgrep.yaml` の規則)。直す前は、agora の担当の Pod の commit が通り、
  翌日の日次の `test_semgrep_findings_match_baseline_ratchet` で初めて赤になっていた(10-11 の 5 回)。
- R2 着地の門(マージの列)は衝突の印の走査だけ(operator 2026-10-02 の裁定)なので、止める所は著述機の commit の hook。
- R3 Pod は pre-commit の枠組みを据え付けず、素の hook(`scripts/git-hooks/pre-commit` の写し)を入れる。
- R4 作業木に `.venv` が無くても走る(Pod の作業木・make sync をしていない作業木)。
- R5 stage した断面にだけ当てる。既存の所見(基点)で塞がない — 新しい所見だけを止める。

## 2. 責務(module)

| id | 実体 | 責務 | 持つ知識 | 隠す知識 |
| --- | --- | --- | --- | --- |
| plain-hook | `scripts/git-hooks/pre-commit` | commit の時の入口。作り直し中は判定しない・(1) semgrep の項 → (2) 台帳の突合 の順に撃ち、赤なら止める | 撃つ順・止め方・古い断面を塞がない判断 | どの file にどの規則を当てるか・道具の版 |
| hook-items | `.pre-commit-config.yaml` の semgrep の 4 項(`semgrep`・`semgrep-hy`・`semgrep-reading-plane`・`semgrep-application-vocabulary`、どれも別名 `semgrep-rules`) | どの file 種にどの semgrep の設定を当てるかの**唯一の定義点** | files / types / exclude の選別・Python は基点と比べる | 機体に枠組みが据わっているか |
| locked-entry | `scripts/precommit_locked.py`・`scripts/semgrep_locked.py` | uv.lock の版の pre-commit / semgrep を uv の道具の置き場から呼ぶ | 版の読み(uv.lock) | 作業木の .venv・探し道の道具 |
| rules | `.semgrep.yaml` | 何が違反か | 規則の pattern と paths | どこから撃たれるか |
| baseline | `scripts/hook_finding_baseline.py`・`scripts/hook_finding_baseline/*.json` | Python の既存の所見と比べ、増えた所見だけを止める | 規則 × file の基点 | — |
| pod-installer | agora `deploy/agent-worker/worker-prepare.sh` の 5. | Pod の準備の時に doeff の clone へ hook の写しを入れる | いつ・どこへ写すか | hook の中身・規則 |
| wiring-tests | `tests/test_lint_entrypoint_wiring.py` の `test_plain_hook_*` | 素の hook の振る舞いと別名の配線を検で固定する | 検の repo の組み立て(偽の uv.lock・偽の基点) | — |

公開の契約:
- plain-hook: git の pre-commit hook の契約(引数なし・cwd は作業木の根・exit 0 = 通す・非 0 = 止める)。
- hook-items → plain-hook: `pre-commit run semgrep-rules`(別名 1 つ)。素の hook は項の名を並べない。
- locked-entry: `uv run --no-project --python 3.12 python scripts/precommit_locked.py <pre-commit の引数…>` → pre-commit の終了値をそのまま返す。

## 3. 将来の変更と主張(盲検の前に固定)

| id | 軸 | 変更 | 主張 | 予想の波及 |
| --- | --- | --- | --- | --- |
| S-hardware | hardware | 別の CPU・OS の機体で hook を走らせる | **対象外**: hook は機器を使わない。機体の違いは uv の python の解き(`--python 3.12`・既存の宣言 ki-a28a482132ab)に閉じ、この変更は触らない。Linux/x86_64 の Pod の外は試せない | — |
| S-storage-venv | storage | 作業木に `.venv` が無い・uv の道具の置き場が初めて | hook は作業木の .venv を読まず、uv.lock の版を uv の置き場から呼ぶ。置き場の違いは locked-entry に閉じる | locked-entry だけ(plain-hook・hook-items は不変) |
| S-storage-index | storage | stage した断面と作業木の中身が違う(一部だけ stage) | 当てるのは stage した断面だけ。作業木の未 stage の変更は判定に入らず、hook の後も残る | 変更なし(pre-commit の stash の契約に依る) |
| S-effects-tool | effects | 道具を走らせられない(uv が無い・取り寄せに失敗) | 走らせられない時は commit を止め、名指す(黙って通さない) | 変更なし(plain-hook の `|| exit 1`) |
| S-distribution-rules | distribution | `.semgrep.yaml` に規則を足す・規則を変える | 規則の変更は作業木の版で効き、各 Pod に入った hook の写しを替えなくてよい | rules だけ(plain-hook・pod-installer は不変) |
| S-distribution-items | distribution | 新しい file 種へ semgrep の項を足す | 項に別名 `semgrep-rules` を書けば、枠組みの機体と素の hook の機体の両方に届く。書き忘れは wiring-tests が赤にする | hook-items だけ(plain-hook は不変) |
| S-concurrency | concurrency | 同じ Pod の同じ clone の別の作業木で、2 つの会話が同時に commit する | hook は commit ごとの process で、共有する可変の状態は uv の置き場(uv が鍵を持つ)だけ。結論はそれぞれの stage で決まる | 変更なし |
| S-simulation | simulation | 道具を偽に差し替えた検の repo で hook を決定的に検める | 偽の uv.lock・偽の基点・本物の semgrep で、止める例と通す例を検で再現できる | wiring-tests だけ |

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 規則の選別は hook-items の 1 か所 | semgrep を撃つ項はどれも別名 `semgrep-rules` を持つ・素の hook は `run semgrep-rules` を含む | `test_plain_hook_runs_every_semgrep_hook_of_the_config` | doeff の pytest(日次・変えた file の検) | 項の entry に `semgrep` の語が無い semgrep の撃ち方は数えない |
| 業務の語を足した stage を止める | 本物の semgrep で素の hook を撃つ検(Hy の註・Python の docstring・文書) | `test_plain_hook_stops_staged_application_vocabulary` | 同上 | 検の repo は規則を 1 つに絞る |
| 所見の無い stage は通す | 同じ検の正の例 | `test_plain_hook_passes_staged_file_without_findings` | 同上 | — |
| 道具の版は uv.lock | locked-entry だけが版を読む | `scripts/semgrep_locked.py`(既存 #2906) | hook の各項 | — |
| 全体の後ろ盾 | 日次の基点の ratchet | `tests/test_semgrep_gate.py::test_semgrep_findings_match_baseline_ratchet` | 日次の全体検証 | 翌日まで分からない |

## 5. 既知の限界(盲検の前から分かっている物)

- L1 Pod の hook は Pod の準備の時に本線の先端から写す。L2051 の前に準備した Pod の写しは古い(台帳だけを見る)。worker の Pod は値の変更で作り直さない(card ki-b52157bbc19d)。
- L2 `git commit --no-verify` は止めない(日次が後ろ盾)。
- L3 素の hook は doeff-linter を撃たない(この card の外)。
