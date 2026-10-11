# 設計検証: codex の利用枠切れ(usageLimitExceeded)を AgentAccountLimit へ写す

依頼 lt-4FZFGSA5AJ7DGD7BZ4656WY6HS・card ki-a59d80e0afa9(通過点 AK)。

## 0. この文書の目的と完了の範囲

- **目的**: 読み手が次の 3 つを、実コードと実行記録で確かめられるようにする。
  - codex の利用枠切れの事実が、どの層で何に変わるか。
  - 将来の変更がどこに収まるか。
  - 盲検の失敗例を受けて何を直したか。
- **完了の範囲**: 実装完了まで。

| 段 | main の commit | 着地 |
|---|---|---|
| 最初の実装 | 赤 5957d0bda → 緑 30d01f936 | L2076 |
| 盲検を受けた修正 | 赤 f26a34ffb → 緑 42e67d88e63b978def59f2afa5c6fbc7ed7907a9 | L2086 |

- **書いた順**:
  1. 最初の実装を着地させた。
  2. 盲検の前の主張を `design-pre-blind.md` に固定した(sha256 70ced04d92029c52f9a173770f22d5998eaf98b856fb245e9d96f9dc943bb628・2026-10-11T16:23:15Z)。
  3. 盲検 A・B を起こした。
  4. 再現した。
  5. 直して着地させた。
  6. 着地した版で検証した。
- 実装の前に保存した設計文書は無い。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | codex の usageLimitExceeded を doeff-agents の AgentAccountLimit へ写し、口座の替えと待ちの判断が claude と同じ道を通るようにする | 依頼の文 |
| R2 | 失敗ケースを先に置く。録った codex 0.162.1 の行で赤 → 緑にし、写した出来事を検で確かめて doeff main に入れる | 依頼の文・受入の条 c105690e8 |
| R3 | resets_at は、枠の知らせの窓のうち usedPercent ≥ 100 の窓の resetsAt(epoch 秒)。尽きた窓が複数なら遅い方 | agora 側 card ki-cc58a5f5f67d の担当の頼み(lt-36JQYBYMRTYA6WN4Y5RXQD4PVQ) |
| R4 | 刻を発明しない(名乗られない値は None) | AgentAccountLimit の docstring |
| R5 | 層の向き: codex 固有の型と handler は doeff-codex、doeff-agents はその adapter、agora は doeff-agents の公開型だけを読む | card ki-534a081e32eb の層の決まり・agora-controllers の .semgrep.yaml |

実物の行(`packages/doeff-codex/tests/recorded/codex-0.162.1/usage-limit.stdout.jsonl`)は次の順に届く。
1. `account/rateLimits/updated`(thread もターンも名乗らない)
2. `error`(codexErrorInfo "usageLimitExceeded")
3. `turn/completed`(status failed)

## 2. 責務(修正後 — 42e67d88e)

| id | 場所 | 責務 | 持つ知識 | 隠す知識 |
|---|---|---|---|---|
| codex-lines | `packages/doeff-codex/src/doeff_codex/lines.hy` | stdout の 1 行を型つきの記録へ分ける(純粋)。利用枠切れの事実の型 `UsageLimitHit` と、誤りの種類の綴り `USAGE-LIMIT-KIND` を定義する | codex の JSON-RPC の形と綴り | JSON・wire の型 |
| codex-limits | `packages/doeff-codex/src/doeff_codex/limits.hy`(新) | ターンの中の枠の知らせを枠の名ごとに重ね(`merged-limits`)、利用枠切れで終わったターンの事実を作る(`usage-limit-hit-of`)。純粋 | 使った割合の閾値 100・尽きた窓の選び(遅い方)・名乗らない窓は前の値を消さない事 | 判定の中身(上の層は事実だけを読む) |
| codex-handler | `packages/doeff-codex/src/doeff_codex/handler.hy` | process の寿命と、記録をターンへ積む事 | どのターンへ積むか(turn/start の答えと turn/started を読んだ時点で走っているターンを立てる `mark-running`・遅れた記録 late・ターンの外 outside)・ターンの記録の枠の重ね(`TurnLog.limits`) | process・行の読み |
| codex-fake | `packages/doeff-codex/src/doeff_codex/fake.hy` | 同じ公開 effect に筋書きで答える層 2 の替え玉。`FakeReply.usage-limit`(UsageLimitHit)が在れば、同じ事実を運ぶ誤りと失敗の終わりを出す | 筋書き | 記録の作り方 |
| codex-adapter | `packages/doeff-agents/src/doeff_agents/handlers/headless_codex.hy` | 公開 effect と層 2 の写し。`account-limit-of` が UsageLimitHit を AgentAccountLimit へ写す | 窓の長さ → 窓の名の表 `WINDOW-NAMES`(claude の CLI の綴り) | doeff-codex の型 |
| agent-contract | `packages/doeff-agents/src/doeff_agents/effects/agent.py` の `AgentAccountLimit` | 利用枠切れの事実の公開型(claude と codex で共通) | 欄の意味 | どの CLI か |
| agora-consumer | agora-controllers `controllers/agent_task/protocol/agent_turn.hy`(account-limit-of)・`controllers/agent_task/core/cli_host_rules.hy`(limit-of) | AgentAccountLimit を agora の型へ写し、口座を resets_at まで外す | 口座・配る判断 | doeff-codex を知らない |
| test-infra | `scripts/record_app_server_lines.py`・`tests/stub_cli/codex_app_server.py`・`tests/recorded/codex-0.162.1/` | 本物の行を録り、替え玉で流す | 入力の目印 USAGE-LIMIT・RATE-LIMIT | 偽の上流 |

## 3. 公開契約(修正後)

- **codex-lines → 上の層**: `TurnEnded.usage-limit: UsageLimitHit | None`。
  - `UsageLimitHit` の欄は minutes(窓の長さ〔分〕)・resets-at(epoch 秒)・text(codex の誤りの文)。
  - 行の分類では None。handler(と fake)が埋める。
  - 窓は長さで名乗る。窓の名は上の層の語なので、層 2 は持たない。
- **codex-handler → codex-adapter**: `CodexReadTurnEvents` の頁の end(TurnEnded)が、そのターンの利用枠切れの事実を運ぶ。
- **codex-adapter → 使い手**: `AgentTurnFailed.account_limit: AgentAccountLimit | None`(claude と同じ型・同じ意味)。
- **内部で自由**:
  - limits.hy の重ね方と選び方
  - handler の積み方
  - adapter の窓の名の表
- **agora の変更は無い**(account_limit の在る失敗 = 口座の利用枠切れ、の道のまま)。

## 4. 盲検の前の主張(`design-pre-blind.md` の §4 の写し — 書き換えていない)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない |
|---|---|---|---|---|---|
| S1 | simulation | 層 2 の fake に、利用枠切れで失敗するターンを出す欄を足す | 判定は adapter にあり、fake は本番と同じ記録の型を出すだけなので、fake の変更で済む | codex-fake | lines・handler・adapter・contract・agora |
| S2 | effects | 上流が新しい長さの窓(1440 分)を足し、その名を出す | 窓の名の表は adapter の 1 か所 | codex-adapter | lines・handler・fake・contract・agora |
| S3 | concurrency | ターンを名乗らない枠の知らせを、どのターンへ積むかを変える | 割り当ては handler の absorb の 1 か所 | codex-handler | lines・adapter・contract・agora |
| S4 | effects | codex の版上げで、利用枠切れの誤りの種類の綴りが変わる | lines は綴りをそのまま運ぶので、adapter の定数だけ | codex-adapter | lines・handler・contract・agora |
| X-hw | hardware | 対象外 | 行は JSON の文字で、機体に依らない | — | — |
| X-st | storage | 対象外 | 枠の重ねはターンの間の memory の値で、保存しない。保存は agora の側 | — | — |
| X-dist | distribution | 対象外 | codex の process は 1 つの Pod の中。事実はターンの終わりの値として運ばれ、機体をまたぐ共有の状態は無い | — | — |

## 5. 盲検 A・B

- **起動の仕方**: Agent tool の新しい文脈(fork / resume なし)・要求 model `opus`(claude-opus-5-5)・effort `xhigh`。A と B は同じ返事の中で同時に起こした。
- **優先 1 の gpt-6-astra(low)を使わなかった訳**: この環境の subagent の起動手段(Agent tool の model は sonnet / opus / haiku / fable)では起こせない。
- **実際に応答した model**: 観測できていない(要求値だけ)。
- **入力**: `blind-input.md`(sha256 9e29e210d34b45e36a86bbeafb5225c45ea8bc64bd7525694a4adbd753904385)。
- **未加工の返答**: `blind-A-response.md`・`blind-B-response.md`。

| | 識別子 | 所要 | 失敗例の要点 |
|---|---|---|---|
| A | a323b6dfa7b17caad | 673 秒 | fake に利用枠切れのターンを足すには、adapter の判定の逆(窓の名 → 長さ・閾値 100・綴り)が adapter の外に要る。根本の原因は、codex では利用枠切れの概念が層 3 にしか無く、層 2 に型が無い事(claude は層 2 に AccountLimitHit を持つ)。付記: ターンをまたいで知らせを持ち越すと、古い刻(1000)がそのまま使われる |
| B | a7e0d383bd985e9cb | 965 秒 | S1 のふつうの実装(claude の fake を手本に窓の名で受ける)で、窓の名の逆の表 `{"five_hour" 300 "seven_day" 10080}` が doeff-codex の fake に入る。提示の検を全部通したまま、S2 が「fake も直す」になる(1440 分は KeyError) |

## 6. 再現(設計者が実際に走らせた物)

### 6.1 盲検 A を再現した(基準の版 30d01f936)

- 記録: `evidence/repro-A-base.log`
- 命令: `uv run --no-sync hy evidence/blind-A-probe_codex_limit.hy.txt`(実験の作業木で走らせた)
- 結果: A の E1〜E3 を同じに観測した。
  - E1: FakeReply の欄は pieces・hold だけ
  - E2: 99% は刻 None
  - E3 C2: 前のターンの知らせだけでは (None, None)
  - E3 C3: 持ち越した古い知らせなら resets_at=1000

### 6.2 盲検 B を再現した(基準の版 30d01f936)

- 記録: `evidence/repro-B-base.log`・`evidence/repro-B-applied.diff`(当てた差分)
- B の差分を当てると、窓の名の知識が 2 か所に在った。
  - adapter の `WINDOW-NAMES`
  - fake の `USAGE-LIMIT-WINDOW-MINUTES`
- **B の申告(全部緑)とは違う観測**: 4 回走らせて 3 回、stub の利用枠切れの検が赤になった(`evidence/repro-B-base-pytest-run1.log`・`-run2.log`)。
  - 赤になった検: `test_headless_codex_usage_limit_carries_the_account_limit_stub` か `test_the_public_codex_handlers_carry_the_account_limit_stub`
  - 答えは `AgentAccountLimit(window=None, resets_at=None, …)`
  - これらの検は B の差分の fake を使わない。

### 6.3 競合を見つけた(基準の版 30d01f936 — 最初の実装の不具合)

- **赤の形**: 6.2 の赤は、枠の知らせがそのターンに届かず、刻が None になった形だった。
- **単独では緑**: その検だけを 6 回走らせると、6 回とも緑(`evidence/flaky-base.log`)。
- **原因(code を読んで突き止めた)**:
  - 読みの thread は、turn/start の答えを answers に置くだけ。
  - 走っているターン(current-turn)を立てるのは、答えを待つ handler の側。
  - その間に読んだ、ターンを名乗らない枠の知らせは `outside` に積まれる。
- **決定的な再現**: 読みの thread と同じ順で absorb を呼んだ(`evidence/probe_race.hy.txt` → `evidence/repro-race-base.log`)。結果は `current-turn: None`・`outside: ('RateLimits',)`・枠の知らせはターン u1 に載らない。
- **主張との関係**: この競合は S3 の主張(どのターンへ積むかは handler の 1 か所)の持ち主の中の不具合で、境界の漏洩ではない。最初の実装の検(stub)は負荷の下で時々赤になる状態で着地していた。

## 7. 修正(L2086 — 赤 f26a34ffb → 緑 42e67d88e)

1. **判定を層 2 へ移した**(claude と同じ形 — 層 2 が事実を持ち、adapter は写すだけ)。
   - lines.hy に `UsageLimitHit`・`TurnEnded.usage-limit`・`USAGE-LIMIT-KIND`(綴りの定義の 1 か所)を足した。
   - limits.hy(新)に `merged-limits`・`usage-limit-hit-of` を置いた。
   - adapter から、綴り・閾値・重ね方・ターンの枠の状態を外した。
2. **fake を事実で受けるようにした**。`FakeReply.usage-limit`(UsageLimitHit — 窓は長さ)を足した。fake は adapter の窓の名の表の逆を持たない。
3. **競合を直した**。handler の absorb が、turn/start の答え(ターンの id を名乗る)と turn/started を読んだ時点で、走っているターンを立てる(`SessionRuntime.mark-running`)。
   - 先に main に入った 8b0a1b812 の遅れた記録(late)をターンの頭に積む処理も、この 1 か所へ移した。
4. **検を足した**。
   - 層 2 の判定の境目(`test_limits.hy`)
   - 競合の決定的な検
   - 層 2 が窓の名を持たない事の検(`test-the-codex-layer-names-windows-by-their-length`)
   - fake と stub で同じ事実(`test_scenarios.hy`)
   - adapter の fake・公開の口の fake
5. **枠の名(limit-id)ごとに重ねるようにした**。A の付記(別の limit-id の知らせが尽きた窓を上書きして失う)を受け、どの枠の尽きた窓も数える。

## 8. シナリオの結果(着地した版 42e67d88e で実測 — 記録は `evidence/at-42e67d88e-*.log`)

| id | 盲検の前の予想 | 実測(元の設計) | 実測(修正後) | 波及の訳 |
|---|---|---|---|---|
| S1 | codex-fake だけ | fake だけに入れると、adapter の窓の名の表の逆が fake に要った(B・6.2) | 修正の中で `FakeReply.usage-limit` を足した。fake は lines の型(UsageLimitHit・USAGE-LIMIT-KIND)だけを使い、窓の名を持たない(S1-positive)。B の逆の表を入れる変異は検が拒む(S1-negative) | 元の設計では隠すはずの知識(窓の名の表・閾値・綴り)が漏れた。修正で判定を層 2 へ移した |
| S2 | codex-adapter だけ | B の差分の上では fake も直す必要があった(KeyError) | adapter の `WINDOW-NAMES` の 1 行で、fake の筋も `one_day` を出す(S2-positive — 変えた file は headless_codex.hy だけ)。表に無い長さは None(S2-negative) | 修正後は予想どおり。赤になる adapter 自身の表の検は、同じ module の検で、表と一緒に直す |
| S3 | codex-handler だけ | 割り当ての持ち主の中に競合があった(6.3) | 答えの直後の知らせがそのターンに載る(S3-positive)。ターンの外・前のターンの知らせは次のターンに使わない(S3-negative — u2 は刻 None) | 予想どおり handler の中で直り、lines・adapter・agora は変えていない(修正の差分の handler の分) |
| S4 | codex-adapter の定数だけ | 予想どおり adapter の定数だけ(A の E2 の 4 行目・2 つ目の定義が無い限り) | lines.hy の `USAGE-LIMIT-KIND` の 1 行で、handler・fake・adapter が新しい綴りに従う(S4-positive)。今の版では新しい綴りは利用枠切れにしない(S4-negative) | 修正で綴りの持ち主を adapter から lines へ移した。変える module は 1 つのまま(adapter → lines) |

## 9. 強制方法(修正後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 判定は層 2 の 1 か所 | 判定の関数を limits.hy に置き、adapter は `UsageLimitHit` だけを受ける(defk の `:pre` が型を検める)。adapter は綴り・閾値・知らせの型を import しない | `limits.hy`・`headless_codex.hy` の `account-limit-of` | 検 `test_limits.hy`・`test_headless_codex_account_limit.hy` | adapter に判定を書き戻す事を静的に止める規則は無い(検は出力を見る) |
| 層 2 は窓の名(上の層の語)を持たない | 検 `test-the-codex-layer-names-windows-by-their-length`(doeff-codex の src の .hy・.py・.pyi に five_hour・seven_day が無い) | `packages/doeff-codex/tests/test_limits.hy` | pytest(手で・全体テスト) | 一覧の 2 語以外の名(例 one_day)は拾わない |
| fake と本番が同じ事実を出す | 同じ筋書きの Program を fake と stub(本番の handler + 録った行)で走らせる検 | `test_scenarios.hy::test-a-usage-limited-turn-ends-failed-with-the-usage-limit-fact` | pytest | 本物の上流の値は偽物(名は binary の文字列で確かめた) |
| ターンを名乗らない知らせは、答えを読んだターンへ | absorb の `mark-running`・決定的な検(読みの thread の順で absorb を呼ぶ) | `handler.hy`・`test_limits.hy::test-a-rate-limit-update-read-right-after-the-turn-start-answer-joins-that-turn` | pytest | 本物の app-server で答えより先に知らせが来る順は未観測 |
| 刻を発明しない | `usage-limit-hit-of` の境目の検(尽きた窓が無い・刻を名乗らない・知らせが無い → None) | `test_limits.hy` | pytest | 古い刻(過ぎた刻)を拒む判断は無い(ターンをまたいで持ち越さない事で避けている) |
| agora は doeff-codex を読まない | agora-controllers の .semgrep.yaml(doeff_codex の import と app-server の語の禁止) | agora-controllers | agora の commit の hook | text の文を解いて刻を作る事は止めない |
| doeff-codex は業務の系を名指さない | semgrep `doeff-packages-have-no-application-vocabulary` | doeff `.semgrep.yaml` | pre-commit の semgrep-hy(この変更で 1 件止めて直した) | 一覧の外の語は通る |
| .pyi が .hy と一致 | `test_generated_stubs.py` | doeff-codex | pytest | 型検査そのものは全体テストの段だけ(この変更では走らせていない) |

## 10. 残る限界(完了を止めない物 — 本文に記すだけ)

- **本物の上流で未確認**: 利用枠切れの時に、同じターンに枠の知らせが来るかは確かめていない。録りは偽の上流が header で名乗った物で、来なければ刻は None になる。
- **resets_at が None の時の agora**: agora は LimitUnknown で待つ。agora 側の担当の知らせ(lt-36JQYBYMRTYA6WN4Y5RXQD4PVQ)によれば、利用枠を周期で読み直すプログラムの reconcile(`ProfileExhaustionChanged` を書く係)は codex を対象にしていない。agora 側 card ki-cc58a5f5f67d の範囲。
- **型検査**: 走らせていない。全体テストの段で測る。
- **doeff-agents の検の全体**: 走らせていない(触った 2 file だけ)。
