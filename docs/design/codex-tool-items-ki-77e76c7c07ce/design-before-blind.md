# codex の道具の item の写し — 盲検の前の主張(card ki-77e76c7c07ce・依頼 lt-5AB8Q8DB3YHVBC8WHFP34QVQKY)

盲検 A・B を起動する前に固定した主張。hash は `design-before-blind.sha256`。盲検の後に結論を書き換えない(後の結論は `design.md`)。

## 完了の範囲

実装完了まで。doeff main に赤 9247f7b39 → 緑 af7bd4857(L2078)で入った。対象版 = `af7bd4857a0475f94e27ddd7642cde0ddb1f1172`。
agora-controllers の doeff の pin を上げる事と配備はこの範囲の外(予定にも入れていない)。

## 要件(固定)

- R1: codex 0.162.1 の app-server が出す道具の item(commandExecution・fileChange・webSearch)を、doeff-agents の公開の出来事
  AgentToolUseEvent(ToolCall の列)・AgentToolResultEvent(ToolAnswer の列)へ写す。出来事の型と欄は claude の adapter と同じで、
  agora-controllers の absorb-event を変えずに受けられる。
- R2: 録った本物の codex 0.162.1 の行で、写しを確かめる検が直す前に赤・直した後に緑で main に入る。
- R3: 層の決まり: codex の行(JSON)の形を知るのは doeff-codex(層 2)だけ。doeff-agents(層 3)は層 2 の型だけを読む。process の
  寿命は層 3 に出さない。agora は doeff_codex を import しない。

## 責務(module)

| id | 置き場 | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|---|
| lines | packages/doeff-codex/src/doeff_codex/lines.hy | stdout の 1 行 → 型つきの記録(JSON の境目の 1 か所) | codex の method の名・欄の綴り(camel・PatchChangeKind だけ snake)・item の種類の名・状態の綴り | JSON の形 | `classify-line`・記録の型(ItemStarted/ItemDone の `tool` = CommandRun・FilePatch・WebSearch・None、ToolStatus、UnknownValue、Unparsed) | なし(純関数) | なし(行ごとの呼び) | 知らない綴りは UnknownValue で運び落とさない。使う欄の無い道具の item は method と訳つきの Unparsed。道具でない種類は tool None |
| handler | packages/doeff-codex/src/doeff_codex/handler.hy | app-server の process を持ち、記録をターンの出来事の列へ積む | process・読み手の thread・ターンごとの列 | 子 process・信号 | 公開 effect(CodexStartTurn・CodexReadTurnEvents ほか) | 子 process・thread | 会話(thread)ごと | ターンを名乗る記録は名乗ったターンの列へ積む |
| adapter | packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy | 層 2 の記録 → 層 3 の出来事 | 写しの決まり(道具の名 = item の種類の名・id = item の id・input の欄・誤りの印 = 状態が completed でない・webSearch は終わりで呼びと結果) | 層 2 の型を呼び手から | headless-codex-handler(LaunchEffect・EventsEffect ほか)→ AgentEventPage | GetTime と Codex* の effect だけ(I/O を直に持たない) | session(CodexHeadlessSession) | seq は 0 から欠けずに続く。どの呼びもその結果より前。ToolCall.input は凍った JSON の object |
| events | packages/doeff-agents/src/doeff_agents/effects/agent.py | 公開の出来事の型 | AgentToolUseEvent・AgentToolResultEvent・AgentToolCallStartedEvent・ToolCall・ToolAnswer(doeff_claude_code.lines の型) | なし | 型そのもの | なし | 値 | 変えない |
| consumer | agora-controllers agent_task/protocol/agent_turn.hy absorb-event・agora の WebUI tool-view.ts | 出来事を記録し画面の道具カードにする | name と input と answers をそのまま運ぶ。画面は input.command が文字列なら shell の card | — | — | — | — | 変えない(この repo の外) |
| doubles | packages/doeff-codex/tests/stub_cli/codex_app_server.py・tests/recorded/codex-0.162.1/・scripts/record_app_server_lines.py | 録った本物の行を替え玉が流す | 録りの筋書き・目印 → 録りの名(REPLAYED) | — | 目印 TOOL-CALLS | 録りは本物の codex と偽の上流 | 検ごと | 録った行は加工しない(id だけ差し替える) |

## 変更シナリオと主張(盲検の前)

| id | 軸 | 適用 | 変える要求 | 主張 | 変わると予想する module | 変わらないと予想する module・契約 |
|---|---|---|---|---|---|---|
| S1 | effects | 適用 | codex の別の道具の item(例 mcpToolCall)も写す | 種類 1 つの追加は、lines に wire の型・記録・`tool-of` の枝を、adapter に input と結果の枝を足すだけで済む | lines・adapter | handler・events・consumer(公開の出来事の型と agora の受けは変えない) |
| S2 | effects | 適用 | codex の版が上がり、道具の item の状態の綴りが増える(例 paused) | 変える code は無い。知らない綴りは UnknownValue で運ばれ、結果は誤りの印つきで届く(行も落ちない) | なし | lines・adapter・handler・events・consumer |
| S3 | concurrency | 適用 | 道具の item が turn/start の答えの直後に一気に届く(替え玉が録った行を間を置かずに流す)・別の会話が同じ機体で並ぶ | 道具の item は自分のターンの id を名乗るので、handler は名乗ったターンの列へ積み、取り落とさない。写しは session ごとの状態だけを使う | なし | 全部 |
| S4 | hardware | 対象外 | — | 写しは純関数で、機体の形に依らない。codex の binary の版と形の固定は呼び出しの包み(機体の命令)の責務で、この変更は触らない | — | — |
| S5 | storage | 対象外 | — | 出来事は session の memory の中だけにあり、残すのは agora(この変更の外)。保存の形を持たない | — | — |
| S6 | distribution | 対象外 | — | 写しは会話の worker の process の中で走り、process をまたぐ形(agora の absorb の protocol)は変えていない | — | — |
| S7 | simulation | 対象外 | — | 決定的な模擬の層(doeff-codex の fake)は変えていない。fake は道具の item を出さず、写しの検は録った本物の行の替え玉で撃つ | — | — |

前提: codex の道具の item は item/started と item/completed で届き、params に threadId と turnId を持つ(codex 0.162.1 の
schema と録った行で確かめた形)。webSearch の始まりの item は検索の語を持たない(録った行)。

## 強制の手段

| 守る責務 | 強制の手段 | 実装の置き場 | 実行の経路 | 限界 |
|---|---|---|---|---|
| doeff-codex は上の系を名指さない | semgrep `doeff-packages-have-no-application-vocabulary` | .semgrep.yaml:333 | pre-commit の semgrep-hy・日次 | 語の一覧に無い語は捕まえない |
| agora は doeff_codex を import しない | agora-controllers の .semgrep.yaml の規則 | agora-controllers(この repo の外) | agora-controllers の hook | この repo からは撃たない |
| 道具の中身の形と閉じた語彙 | defwire(形の違う中身は Malformed → Unparsed)・defenum ToolStatus・型の union ToolItem | lines.hy | 実行時の parse・型検査(doeff-hy-check / pyright) | 型検査は日次の全体の回だけ |
| 写しの決まり | 検 test_tool_items.hy(行の分類)・test-headless-codex-tool-items-reach-tool-use-and-tool-result-events-stub(録った行 → 出来事) | packages/doeff-codex/tests・packages/doeff-agents/tests | 手元の focused・日次の全体 | 録った 3 種類の道具だけ |
| process の寿命を層 3 に出さない | adapter は doeff_codex.effects・values・lines・rpc の型だけを import する(書き方の決まり) | headless_codex.hy | 読み手の review | 静的な規則は無い |
