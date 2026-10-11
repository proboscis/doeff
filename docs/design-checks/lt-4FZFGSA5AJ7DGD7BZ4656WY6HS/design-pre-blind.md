# 設計: codex の利用枠切れ(usageLimitExceeded)を AgentAccountLimit へ写す

依頼 lt-4FZFGSA5AJ7DGD7BZ4656WY6HS・card ki-a59d80e0afa9(通過点 AK)。

## 0. この文書の目的と完了の範囲

- 目的: 読み手が、codex の利用枠切れの事実がどの層で何に変わり、将来の変更がどこに収まるか(収まらないか)を、実コードと検で確かめられる状態にする。
- 完了の範囲: **実装完了まで**。実装は doeff main に着地済み(赤 5957d0bda → 緑 30d01f936c216bdf41951b62df63bf62c97a81cf・L2076)。
- 書いた順: 実装と着地の後に、盲検の前にこの文書を書いた。実装の前に保存した設計文書は無い(実装中の判断を書き起こした)。§4 の主張は盲検 A・B に入力を渡す前に固定し、hash を §8 に記す。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | codex の usageLimitExceeded を doeff-agents の AgentAccountLimit へ写し、口座の替え・待ちの判断が claude と同じ道を通るようにする | 依頼の文 |
| R2 | 失敗ケース先: 録った codex 0.162.1 の行で赤 → 緑。写した出来事が検で確かめられ doeff main に入る | 依頼の文・受入の条 c105690e8 |
| R3 | resets_at = 枠の知らせのうち usedPercent ≥ 100 の窓の resetsAt(epoch 秒)。尽きた窓が複数なら遅い方 | agora 側 card ki-cc58a5f5f67d の担当の頼み(lt-36JQYBYMRTYA6WN4Y5RXQD4PVQ) |
| R4 | 刻を発明しない(名乗られない値は None) | AgentAccountLimit の docstring・agora の account-limit-of の註 |
| R5 | 層の決まり: codex 固有の型と handler は doeff-codex、doeff-agents はその adapter、agora は doeff-agents の公開型だけを読む | card ki-534a081e32eb の「層の決まり」・agora-controllers .semgrep.yaml:271 |

実物の行(codex 0.162.1・`packages/doeff-codex/tests/recorded/codex-0.162.1/usage-limit.stdout.jsonl`)の順: `account/rateLimits/updated`(thread もターンも名乗らない)→ `error`(codexErrorInfo "usageLimitExceeded"・willRetry false)→ `turn/completed`(status failed・同じ error)。

## 2. 責務(module)

| id | 場所 | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|---|
| codex-lines | `packages/doeff-codex/src/doeff_codex/lines.hy` | app-server の stdout の 1 行を型つきの記録へ分ける | codex 0.162.1 の JSON-RPC の形・欄の綴り(camelCase) | JSON・欄の綴り・wire の型 | `classify-line`(str → CodexLine)・記録 `RateLimits`・`RateLimitWindow`・`TurnError`・`TurnEnded` | なし(純粋な分類 — 1 行から 1 記録) | なし(行ごとに状態を持たない) | 形の合わない行は `Unparsed`・知らない値は `UnknownValue`。値を発明しない |
| codex-handler | `packages/doeff-codex/src/doeff_codex/handler.hy` | app-server の process の寿命と、行の記録をターンの記録へ積む | process・pid・stdin/stdout・ターンの id と記録の対応 | process の起動・信号・行の読み | effect `CodexStartTurn`・`CodexReadTurnEvents` ほか | 子 process・読みの thread | thread ごとに 1 process・`CodexCloseSession` か process の終わりまで | 1 thread に走るターンは多くとも 1 つ。ターンを名乗らない通知(枠の知らせ)は走っているターン(無ければ outside)へ |
| codex-fake | `packages/doeff-codex/src/doeff_codex/fake.hy` | 同じ公開 effect に筋書きで答える層 2 の替え玉 | 筋書き(FakeReply) | 記録の作り方 | `fake-codex-handler`・`FakeReply`・`FakeCodexWorld` | 仮想の時計だけ | handler の値の寿命 | 本番と同じ記録の型で出す |
| codex-adapter | `packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy` | doeff-agents の公開 effect を doeff-codex の effect へ写し、行の記録を層 3 の出来事と終わりにする | 利用枠切れの判定(誤りの種類の綴り・尽きた窓の選び・窓の名の表)・ターンごとの枠の重ね方 | doeff-codex の型(呼び手は見ない) | `LaunchEffect` ほか → `AgentTurnFailed.account_limit` | 層 2 の effect・`GetTime` | session の値の寿命。枠の重ねはターンの始めに戻す | usageLimitExceeded の失敗だけを AgentAccountLimit にする。resets_at は尽きた窓の resetsAt だけ(無ければ None) |
| agent-contract | `packages/doeff-agents/src/doeff_agents/effects/agent.py` の `AgentAccountLimit` | 口座の利用枠切れの事実の公開型(claude と codex の adapter が同じ型で出す) | 欄の意味(window・resets_at〔epoch 秒〕・text) | どの CLI か | frozen dataclass | なし(値) | なし(値) | 名乗らない欄は None |
| agora-consumer | agora-controllers `controllers/agent_task/protocol/agent_turn.hy:669`(account-limit-of)・`controllers/agent_task/core/cli_host_rules.hy:1841`(limit-of) | AgentAccountLimit を agora の型へ写し、口座を resets_at まで外す | 口座の名・配る判断 | doeff-codex を知らない(semgrep で import を禁止) | `TurnAccountLimit` → `AccountLimit(:until)` | 記録の書き | agora の配る担当の寿命 | 戻りの刻の無い印は LimitUnknown の待ち |
| test-infra | `scripts/record_app_server_lines.py`・`tests/stub_cli/codex_app_server.py`・`tests/recorded/codex-0.162.1/` | 本物の行を録り、替え玉で流す | 筋書きの目印(USAGE-LIMIT・RATE-LIMIT) | 録りの偽の上流 | 筋書きの名・入力の目印 | 録り: 子 process と HTTP の偽の上流 | 録り 1 回・替え玉はターンごと | 録った行をそのまま流す(id だけ差し替える) |

## 3. 公開契約と内部で自由な所

- codex-lines → codex-adapter: `RateLimits(limit-id, primary, secondary)`・`RateLimitWindow(used-percent, minutes, resets-at)`・`TurnEnded(status, error-message, error-kind, …)`。error-kind は codex の綴りをそのまま運ぶ(`str | UnknownValue | None`)。
- codex-handler → codex-adapter: `CodexReadTurnEvents` の頁に、そのターンの記録と、ターンを名乗らない通知(枠の知らせ)が届いた順に載る。
- codex-adapter → 呼び手(agora): `AgentTurnFailed.account_limit: AgentAccountLimit | None`。claude の adapter と同じ型・同じ意味。
- 内部で自由: 枠の重ね方(merged-limits)・窓の名の表(WINDOW-NAMES)・尽きた窓の選び方(account-limit-of)。どれも adapter の中の 1 か所。
- 組み立て点から消えた判断: agora は codex の行を読まずに、claude と同じ欄だけで口座を外す(agora の変更は無い)。

## 4. 変更シナリオと事前の主張(盲検の前に固定)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない | 前提 |
|---|---|---|---|---|---|---|
| S1 | simulation | 層 2 の fake(FakeReply)に、利用枠切れで失敗するターン(枠の知らせ・誤り・失敗の終わり)を出す欄を足す(agora 側の検が fake で回したい) | 判定は adapter にあり、fake は本番と同じ記録の型を出すだけなので、fake の変更だけで adapter の判定がそのまま働く | codex-fake(と、その検) | codex-lines・codex-handler・codex-adapter・agent-contract・agora-consumer | fake が lines の記録の型を使える |
| S2 | effects | 上流が新しい長さの窓(例 1440 分)を足し、その名を出したい | 窓の名の表は adapter の 1 か所なので、表の 1 行で済む | codex-adapter(WINDOW-NAMES) | codex-lines・codex-handler・codex-fake・agent-contract・agora-consumer | lines は長さ(分)をそのまま運ぶ |
| S3 | concurrency | ターンを名乗らない枠の知らせを、どのターンへ積むかを変える(例: 1 つの process で複数の thread を走らせ、走っている全ターンへ配る・ターンの外で届いた知らせを次のターンへ持ち越す) | ターンへの割り当ては handler の absorb の 1 か所で決まり、adapter は自分のターンの頁だけを読むので、handler だけが変わる | codex-handler | codex-lines・codex-adapter・agent-contract・agora-consumer | 枠の知らせの記録の型は変えない |
| S4 | effects | codex の版上げで、利用枠切れの誤りの種類の綴りが変わる(例 usageLimitExceeded → usageLimitReached) | lines は綴りをそのまま運ぶので、adapter の定数 USAGE-LIMIT-KIND だけが変わる | codex-adapter(USAGE-LIMIT-KIND) | codex-lines・codex-handler・agent-contract・agora-consumer | 綴りが文字列の形のまま(object の形に変わるなら lines の ErrorInfoWire も変わる — 主張の外) |
| X-hw | hardware | — | 対象外: 行は JSON の文字で、機体・CPU・GPU に依らない | — | — | — |
| X-st | storage | — | 対象外: 枠の重ねはターンの間の memory の値で、保存しない。保存は agora の側(TurnAccountLimit)で、この設計の外 | — | — | — |
| X-dist | distribution | — | 対象外: codex の process は 1 つの worker の Pod の中で走り、事実はターンの終わりの値として agora へ運ばれる。機体をまたいで共有する状態を持たない | — | — | — |

## 5. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| lines が JSON を 1 か所で解く・値を発明しない | defwire の型(`:unknown :ignore` / 必須の欄)と defrecord(frozen・型つきの欄)。形の合わない行は Unparsed | `lines.hy` の RateLimitWindowWire・RateLimitSnapshotWire・RateLimitsParamsWire・RateLimits・RateLimitWindow | 検 `packages/doeff-codex/tests/test_lines.hy::test-a-usage-limit-names-its-windows-and-its-kind`(録った行・形の合わない行) | 名乗らなくてよい欄(windowDurationMins・resetsAt)の綴りが変わると、黙って None になる(版の食い違いを型で拾えない) |
| 判定は adapter の 1 か所 | defk の `:pre`/`:post`(実行時の型の検め)・判定の関数を名で公開して検で撃つ | `headless_codex.hy` の merged-limits・account-limit-of | 検 `packages/doeff-agents/tests/test_headless_codex_account_limit.hy`(境目 4 件) | 他の module が error-message の文を読んで刻を作る事を静的に止める規則は無い |
| 録った行 → AgentAccountLimit の通し | 契約の検(本番の handler + 替え玉の app-server・公開の口の組) | `packages/doeff-agents/tests/test_headless_codex_adapter.hy` の usage-limit 2 件と rate-limit 1 件 | pytest(着地の前に手で・全体テストで) | 本物の上流の body と header の値は偽物(名は binary の文字列で確かめた) |
| agora が doeff-codex を読まない | semgrep(agora-controllers `.semgrep.yaml:271` 付近 — agora の code に doeff_codex の import と app-server の語を書かない) | agora-controllers の `.semgrep.yaml` | agora の commit の hook | text の文を解析して刻を作る事は止めない |
| doeff-codex が業務の系を名指さない | semgrep `doeff-packages-have-no-application-vocabulary` | doeff `.semgrep.yaml:338` | pre-commit の semgrep-hy(実際にこの変更で 1 件止めて直した) | 語の一覧の外の語は通る |
| .pyi が .hy と一致 | `test_generated_stubs.py::test_generated_stubs_are_what_the_tool_makes` | `packages/doeff-codex/tests/test_generated_stubs.py` | pytest | 型検査そのものは全体テストの段だけ |
| 記録の union の網羅 | なし(adapter の match は `_` で語彙の外を空にする) | — | — | 新しい記録の型を足しても、どこが読むべきかを静的に知らせない |

## 6. 盲検 A・B

§8 の入力一式を、文脈を共有しない新しい subagent 2 つへ渡す(結果は §9)。

## 7. 検証の計画(各適用シナリオに正常例と失敗例)

| シナリオ | 正常例 | 失敗例 |
|---|---|---|
| S1 | fake.hy だけを変えた最小の実験で、fake の筋書きの利用枠切れのターンが AgentAccountLimit になる(adapter は変えない) | 枠の知らせを出さずに usageLimitExceeded で失敗させると、resets_at と window は None(刻を発明しない) |
| S2 | 表に 1440 分の行を足すと window が名を持つ(adapter の 1 行) | 表に無い長さは window None(今の版) |
| S3 | 走っているターンが在る時、枠の知らせはそのターンの記録に積まれる | 走っているターンが無い時は outside に積まれ、どのターンにも載らない(adapter は刻を名乗らない) |
| S4 | adapter の定数を変えると、綴りの変わった行が AgentAccountLimit になる(lines は変えない) | 今の版では、綴りの変わった行は lines が綴りのまま運び、adapter は account_limit None(失敗としては届く) |
