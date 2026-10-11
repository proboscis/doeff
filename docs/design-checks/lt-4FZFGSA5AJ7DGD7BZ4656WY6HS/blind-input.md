# 失敗例探しの材料: codex の利用枠切れ(usageLimitExceeded)を AgentAccountLimit へ写す

## 読む対象(読み取りだけ)

- doeff の作業木 `/home/ubuntu/.worktrees/doeff-wt-codex-usage-limit`(版 30d01f936c216bdf41951b62df63bf62c97a81cf)。読むのは `packages/`・`.semgrep.yaml`・`.pre-commit-config.yaml` だけ。`.scratch/` は読まない。
- agora-controllers の読み取り用の clone `/home/ubuntu/repos/agora-controllers`(`git -C … show origin/main:<path>` で読む)— 使い手の側の参照だけ。
- どの file も書き換えない。commit・push・他の会話への連絡・全数の検査はしない。検を走らせるなら、下の「検」の命令を対象を絞って 1 回ずつ。

## 要件

1. codex(CLI 0.162.1 の app-server)が口座の利用枠切れで断ったターンを、doeff-agents の公開型 `AgentAccountLimit`(window・resets_at〔epoch 秒〕・text)として `AgentTurnFailed.account_limit` に載せる。claude の adapter と同じ型・同じ意味で、使い手(agora)は claude と同じ道で口座を外し、resets_at まで待つ。
2. resets_at は、codex が名乗った枠の窓のうち使った割合が 100 以上の窓の戻りの刻。尽きた窓が複数なら遅い方。名乗られない値は None(刻を作らない)。
3. 層: codex 固有の型と process の handler は doeff-codex、doeff-agents はその adapter、agora は doeff-agents の公開型だけを読む。

実物の行(codex 0.162.1 を偽の上流につないで録った物): `packages/doeff-codex/tests/recorded/codex-0.162.1/usage-limit.stdout.jsonl`。順は `account/rateLimits/updated`(thread もターンも名乗らない)→ `error`(codexErrorInfo "usageLimitExceeded")→ `turn/completed`(status failed)。

## 責務

| id | 場所 | 責務 | 持つ知識 | 隠す知識 | 公開の口 |
|---|---|---|---|---|---|
| codex-lines | `packages/doeff-codex/src/doeff_codex/lines.hy` | stdout の 1 行を型つきの記録へ分ける(純粋) | codex の JSON-RPC の形と綴り | JSON・wire の型 | `classify-line`・記録 `RateLimits`・`RateLimitWindow`・`TurnError`・`TurnEnded` |
| codex-handler | `packages/doeff-codex/src/doeff_codex/handler.hy` | app-server の process の寿命と、記録をターンの記録へ積む | process・ターンの id と記録の対応 | process の起動・行の読み | `CodexStartTurn`・`CodexReadTurnEvents` ほかの effect |
| codex-fake | `packages/doeff-codex/src/doeff_codex/fake.hy` | 同じ effect に筋書きで答える層 2 の替え玉 | 筋書き | 記録の作り方 | `fake-codex-handler`・`FakeReply` |
| codex-adapter | `packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy` | doeff-agents の公開 effect と doeff-codex の effect の写し・層 3 の出来事と終わり | 利用枠切れの判定(誤りの種類の綴り・尽きた窓の選び・窓の名の表 WINDOW-NAMES)・ターンごとの枠の重ね方(merged-limits・account-limit-of) | doeff-codex の型 | `LaunchEffect` ほか → `AgentTurnFailed.account_limit` |
| agent-contract | `packages/doeff-agents/src/doeff_agents/effects/agent.py` の `AgentAccountLimit` | 利用枠切れの事実の公開型(claude と codex で共通) | 欄の意味 | どの CLI か | frozen dataclass |
| agora-consumer | agora-controllers `controllers/agent_task/protocol/agent_turn.hy`(account-limit-of)・`controllers/agent_task/core/cli_host_rules.hy`(limit-of) | AgentAccountLimit を agora の型へ写し、口座を resets_at まで外す | 口座・配る判断 | doeff-codex を知らない | `TurnAccountLimit` → `AccountLimit(:until)` |
| test-infra | `packages/doeff-codex/scripts/record_app_server_lines.py`・`packages/doeff-codex/tests/stub_cli/codex_app_server.py`・`packages/doeff-codex/tests/recorded/codex-0.162.1/` | 本物の行を録り、替え玉で流す | 入力の目印 USAGE-LIMIT・RATE-LIMIT | 偽の上流 | 筋書きの名・入力の目印 |

## 公開契約

- codex-lines → codex-adapter: `RateLimits(limit-id, primary, secondary)`・`RateLimitWindow(used-percent, minutes, resets-at)`・`TurnEnded(status, error-message, error-kind, http-status)`。error-kind は codex の綴りをそのまま運ぶ。
- codex-handler → codex-adapter: `CodexReadTurnEvents` の頁に、そのターンの記録と、ターンを名乗らない通知(枠の知らせ)が届いた順に載る。
- codex-adapter → 使い手: `AgentTurnFailed.account_limit: AgentAccountLimit | None`。
- 内部で自由: 枠の重ね方・窓の名の表・尽きた窓の選び方(adapter の中)。

## 将来の変更についての主張(前提つき)

| id | 変更 | 主張 | 変える module | 変えない module・契約 | 前提 |
|---|---|---|---|---|---|
| S1 | 層 2 の fake に、利用枠切れで失敗するターンを出す欄を足す | 判定は adapter にあり、fake は本番と同じ記録の型を出すだけなので fake の変更で済む | codex-fake | codex-lines・codex-handler・codex-adapter・agent-contract・agora-consumer | fake が lines の記録の型を使える |
| S2 | 上流が新しい長さの窓(例 1440 分)を足し、その名を出したい | 窓の名の表は adapter の 1 か所 | codex-adapter | codex-lines・codex-handler・codex-fake・agent-contract・agora-consumer | lines は長さをそのまま運ぶ |
| S3 | ターンを名乗らない枠の知らせを、どのターンへ積むかを変える(複数の thread を 1 つの process で走らせる・ターンの外で届いた知らせを持ち越す) | 割り当ては handler の absorb の 1 か所、adapter は自分のターンの頁だけを読む | codex-handler | codex-lines・codex-adapter・agent-contract・agora-consumer | 枠の知らせの記録の型は変えない |
| S4 | codex の版上げで利用枠切れの誤りの種類の綴りが変わる | lines は綴りをそのまま運ぶので adapter の定数だけ | codex-adapter | codex-lines・codex-handler・agent-contract・agora-consumer | 綴りが文字列の形のまま |

## 検(実体・設定・検出範囲)

| 検 | 実体 | 対象 | 呼ばれ方 |
|---|---|---|---|
| 型つきの記録 | defwire(`:unknown :ignore`・必須の欄)・defrecord(frozen・型つきの欄) | lines.hy | 分類の時に解く |
| 実行時の型の検め | defk の `:pre` / `:post` | lines.hy・headless_codex.hy の defk | 呼ぶたび |
| 行の分類の検 | `packages/doeff-codex/tests/test_lines.hy` | 録った行と手で作った行 | `uv run --no-sync pytest packages/doeff-codex/tests/test_lines.hy -q` |
| adapter の通しの検 | `packages/doeff-agents/tests/test_headless_codex_adapter.hy`(本番の handler + 替え玉の app-server・fake の handler・公開の口の組) | 録った行 → 出来事と終わり | `uv run --no-sync pytest packages/doeff-agents/tests/test_headless_codex_adapter.hy -q` |
| 判定の検 | `packages/doeff-agents/tests/test_headless_codex_account_limit.hy` | account-limit-of・merged-limits | `uv run --no-sync pytest packages/doeff-agents/tests/test_headless_codex_account_limit.hy -q` |
| .pyi の一致 | `packages/doeff-codex/tests/test_generated_stubs.py` | doeff-codex の .hy と .pyi | pytest |
| semgrep | doeff `.semgrep.yaml`(pre-commit の semgrep・semgrep-hy・semgrep-other-files)— 例 `doeff-packages-have-no-application-vocabulary` | 変えた file | commit の hook |
| agora の境界 | agora-controllers `.semgrep.yaml`(agora の code に doeff_codex の import・app-server の語を書かない) | agora の code | agora の commit の hook |

命令は作業木 `/home/ubuntu/.worktrees/doeff-wt-codex-usage-limit` の中で走らせる(`.venv` は在る)。
