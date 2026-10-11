# 失敗例探しの材料 — codex の道具の item を doeff-agents の道具の出来事へ写す

## 1. 固定した要件

- R1: codex 0.162.1 の `codex app-server`(JSON-RPC・stdio)が出す道具の item(commandExecution・fileChange・webSearch)を、
  doeff-agents の公開の出来事 AgentToolUseEvent(ToolCall の列)・AgentToolResultEvent(ToolAnswer の列)へ写す。出来事の型と欄は
  claude の adapter(packages/doeff-agents/src/doeff_agents/handlers/headless.hy)と同じで、上の利用者(別 repo の agora-controllers の
  absorb-event)を変えずに受けられる。
- R2: 録った本物の codex 0.162.1 の行で写しを確かめる検がある。
- R3: 層の決まり: codex の行(JSON)の形を知るのは doeff-codex(層 2)だけ。doeff-agents(層 3)は層 2 の型だけを読む。process の
  寿命(子 process・信号・pid)は層 3 に出さない。上の利用者は doeff_codex を import しない。

利用状況: 上の利用者は、会話の worker の process の中で doeff-agents の headless の codex の adapter を組み、EventsEffect で出来事の
頁を読み、ToolCall の name・input と ToolAnswer の text・is_error を記録して画面の道具カードにする(画面は input.command が文字列なら
shell のカードにする・ほかの name は欄の表)。

## 2. 責務と公開契約

| id | 置き場(doeff の repo の中) | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|---|
| lines | packages/doeff-codex/src/doeff_codex/lines.hy | stdout の 1 行 → 型つきの記録(JSON の境目の 1 か所) | codex の method の名・欄の綴り・item の種類の名・状態の綴り | JSON の形 | `classify-line`・記録の型(ItemStarted/ItemDone の `tool` = CommandRun・FilePatch・WebSearch・None、ToolStatus、UnknownValue、Unparsed) | なし(純関数) | なし | 知らない綴りは UnknownValue で運び落とさない。使う欄の無い道具の item は method と訳つきの Unparsed。道具でない種類は tool None |
| handler | packages/doeff-codex/src/doeff_codex/handler.hy | app-server の process を持ち、記録をターンの出来事の列へ積む | process・読み手の thread・ターンごとの列 | 子 process・信号 | 公開 effect(packages/doeff-codex/src/doeff_codex/effects.hy) | 子 process・thread | 会話(thread)ごと | ターンを名乗る記録は名乗ったターンの列へ積む |
| adapter | packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy | 層 2 の記録 → 層 3 の出来事 | 写しの決まり(道具の名 = item の種類の名・id = item の id・input の欄・誤りの印 = 状態が completed でない・webSearch は始まりで AgentToolCallStartedEvent、終わりで呼びと結果) | 層 2 の型を呼び手から | headless-codex-handler(LaunchEffect・EventsEffect ほか)→ AgentEventPage | GetTime と doeff-codex の effect だけ | session ごと | seq は 0 から欠けずに続く。どの呼びもその結果より前。ToolCall.input は凍った JSON の object |
| events | packages/doeff-agents/src/doeff_agents/effects/agent.py | 公開の出来事の型 | AgentToolUseEvent・AgentToolResultEvent・AgentToolCallStartedEvent・ToolCall・ToolAnswer | なし | 型 | なし | 値 | 変えない |
| consumer | 別 repo(agora-controllers の absorb-event・WebUI) | 出来事を記録し道具カードにする | name・input・answers を運ぶ | — | — | — | — | 変えない |
| doubles | packages/doeff-codex/tests/stub_cli/codex_app_server.py・packages/doeff-codex/tests/recorded/codex-0.162.1/・packages/doeff-codex/scripts/record_app_server_lines.py | 録った本物の行を替え玉の app-server が流す | 入力の目印 → 録りの名(REPLAYED) | — | 目印 TOOL-CALLS | 録りは本物の codex と偽の上流 | 検ごと | 録った行は id だけ差し替える |

## 3. 将来の変更についての主張(前提つき)

| id | 軸 | 変える要求 | 主張 | 変わると予想 | 変わらないと予想 |
|---|---|---|---|---|---|
| S1 | effects | codex の別の道具の item(例 mcpToolCall)も写す | 種類 1 つの追加は、lines に wire の型・記録・`tool-of` の枝を、adapter に input と結果の枝を足すだけで済む | lines・adapter | handler・events・consumer |
| S2 | effects | codex の版が上がり、道具の状態の綴りが増える(例 paused) | 変える code は無い。知らない綴りは UnknownValue で運ばれ、結果は誤りの印つきで届く | なし | 全部 |
| S3 | concurrency | 道具の item が turn/start の答えの直後に一気に届く・別の会話が同じ機体で並ぶ | 道具の item は自分のターンの id を名乗るので、handler は名乗ったターンの列へ積み、取り落とさない。写しは session ごとの状態だけを使う | なし | 全部 |
| S4〜S7 | hardware・storage・distribution・simulation | 対象外(写しは純関数で機体に依らない・保存を持たない・process をまたぐ形を変えていない・決定的な模擬の層は変えていない) | — | — | — |

前提: codex の道具の item は item/started と item/completed で届き、params に threadId と turnId を持つ(codex 0.162.1 の schema と
録った行)。webSearch の始まりの item は検索の語を持たない(録った行)。

## 4. 実コードと検査

- 対象版: doeff の repo の commit `af7bd4857a0475f94e27ddd7642cde0ddb1f1172`(この変更を含む main)。変更の差分は同じ dir の
  `change.patch`(`fca21e823..af7bd4857`)。
- 実コード(読み取り専用 — 主 checkout `/home/ubuntu/repos/doeff` は古い事があるので、版は `git -C /home/ubuntu/repos/doeff show
  af7bd4857:<path>` で読む):
  - packages/doeff-codex/src/doeff_codex/lines.hy(記録の型と `classify-line`・`tool-of`・`item-started-of`・`item-done-of`)
  - packages/doeff-codex/src/doeff_codex/handler.hy(`absorb` — 記録をどのターンの列へ積むか)
  - packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy(`tool-input-of`・`tool-call-of`・`tool-answer-of`・
    `tool-events-of`・`events-of`・`pull`)
  - packages/doeff-agents/src/doeff_agents/effects/agent.py(出来事の型)・packages/doeff-claude-code/src/doeff_claude_code/lines.hy
    (ToolCall・ToolAnswer)
  - packages/doeff-codex/tests/recorded/codex-0.162.1/tool-calls.stdout.jsonl(録った本物の行)
- 検査の実体と検出範囲:
  - 検: `uv run --no-sync pytest packages/doeff-codex/tests/test_tool_items.hy packages/doeff-agents/tests/test_headless_codex_adapter.hy`
    (行の分類と、録った行 → 出来事)。
  - pre-commit: `uvx pre-commit run --files <file…>` — semgrep(.semgrep.yaml の規則・Python は基点より増えた所見だけ)・
    semgrep-hy(Hy の規則 — 例 `doeff-packages-have-no-application-vocabulary` は doeff-codex に上の系の語を書かせない)・
    doeff-linter(Python・基点より増えた error だけ)。
  - 型: `uv run --no-sync doeff-hy-check <.hy の file…>`(pyright を Hy へ当てる。commit ごとには走らず日次の全体の回で走る)。

## 5. 実験の制限

- 共有の source(`/home/ubuntu/repos/doeff`・`/home/ubuntu/.worktrees/` の下)を変えない。code を書いて試すなら、自分専用の作業木を
  `git -C /home/ubuntu/repos/doeff worktree add --detach /tmp/ki77e7-blind-<A|B>/wt af7bd4857` で作り、その中だけを変える。
  環境は作業木の中で `uv sync --group dev`(環境変数 UV_CACHE_DIR などは上書きしない)。終わったら
  `git -C /home/ubuntu/repos/doeff worktree remove --force /tmp/ki77e7-blind-<A|B>/wt`。
- 検は名指した file だけを走らせる(全数・全体の検はしない)。
- git の log・commit の本文・`docs/design/` の下の他の file・会話や作業ボードは読まない。他の会話へ連絡しない。
