# 設計検証 lt-0BC9J56TE8QA3VH1FZNJD3F4FS — codex のターンを名乗らない知らせをどのターンへ積むか

## この文書の目的

読み終えた人が次の 4 つを言えるようにする。

- doeff-codex で、ターンの id を持たない口座の枠の知らせ(account/rateLimits/updated)を、どの module がどのターンへ積むと決めているか。
- その決め方が、thread の速さの差・codex の版上げ・決定的な検の追加の 3 つの変更に、どこまで狭く収まるか(盲検の前の主張と実測)。
- 盲検 A・B が示した 2 つの失敗例と、それぞれをどう直し、どう確かめたか。
- 残る強制の限界。

## 完了の範囲と commit

完了の範囲: **実装完了まで**。

| 段 | doeff の commit | 着地 |
|---|---|---|
| 競合の直し(読みの thread がターンを立てる) | 42e67d88e(この依頼より前・別の card ki-a59d80e0afa9) | L2086 |
| turn/started の順の検(依頼のする事 1) | 6e09cde279ca8885e2cdace00d79821bfd1bdd0f | L2093 |
| 盲検 A・B を受けた失敗ケース(赤) | 5ed5df176 | この記録と同じ登記 |
| 盲検 A を受けた直し(緑) | f977f0d8c | 同上 |

書いた順:

1. 6e09cde27 を着地させた(依頼の検)。
2. 盲検の前の主張を `design-pre-blind.md` に固定した(sha256 `82c4e64a419f02079de59f6a66d625844bd68bd2f963720c07cc78c5ebe8ea28`・2026-10-11T17:48:12Z)。盲検の入力 `blind-input.md`(sha256 `04ac05be06d00bed5c6d9855ef9417b1d4402c6137ad88b2cdac215596828b4f`)は、その主張の写しに読むだけの材料と実験の決まりを足した物。
3. 盲検 A・B を同時に起こした。
4. 基準の版 6e09cde27 で A・B の失敗例を再現した。
5. 失敗ケース(赤 5ed5df176)→ 直し(緑 f977f0d8c)。
6. 直した版で、正常例と失敗例(変異)を走らせた。agora の結合の検も直した版で走らせた。

## 1. 固定した要件

| # | 要件 | 出所 |
|---|---|---|
| R1 | codex の会話のターンが口座の利用枠で落ちた時、尽きた窓の戻りの刻が毎回 agora まで届く | 依頼の目的 |
| R2 | turn/started の後、走っているターンの印を置く前に読んだ枠の知らせがそのターンの記録に積まれる事を、absorb を順に呼んで決定的に確かめる | 依頼のする事 1・受入の条 c09f307b3 |
| R3 | ターンの id を持たない知らせを、終わったターンや outside に落とさない | 依頼のする事 2 |
| R4 | agora の結合の検 test_codex_usage_limit_on_agent_runtime.hy が負荷の下でも続けて 12 回緑 | 受入の条 cfd9ac1d3 |

依頼の前提(走っているターンの印 `current-turn` は答えを待つ側が置く)は doeff 30d01f936 の形。42e67d88e から、読みの thread が turn/start の答えと turn/started を読んだ時点で `mark-running` する。なので依頼のする事 2(終わっていない最も新しいターンへ落とす代わりの道)は入れない。今は turn/started を読んだ時点で current-turn がそのターンになり、代わりの道に届く順は codex の行に無い。入れると、どのターンかを決める規則が 2 つになる(依頼者は報告 lt-3AW06R0K4ZPPS8XV0V7AJG14DZ でこの判断を受け入れた)。

## 2. 責務(module)

| id | 責務 | 持つ知識 | 隠す物 | 公開の口 |
|---|---|---|---|---|
| codex-lines | stdout の 1 行を型つきの記録へ分ける(純粋)。どの記録がターンを名乗るか(`TurnRecord`)と、どの記録が枠の窓を運ぶか(`LimitRecord` — f977f0d8c で足した)を 1 か所で名乗る | codex 0.162.1 の JSON-RPC の形 | JSON・wire の型 | `classify-line`・記録の型・`TurnRecord`・`LimitRecord` |
| codex-handler | 記録を会話の状態へ積む(`absorb`)。走っているターンを立てる(`mark-running`)。ターンの記録ごとに枠を重ね、終わりに利用枠切れの事実を載せる | どのターンへ積むか・`current-turn`・`outside`・`late` | 子 process・読みの thread・lock | 公開 effect・頁の end(TurnEnded.usage-limit) |
| codex-limits | 枠の知らせの重ね方と、利用枠切れの事実の判定(純粋) | 閾値 100・遅く戻る窓・名乗らない窓は前の値を消さない | 判定の中身 | `merged-limits`・`usage-limit-hit-of` |
| codex-adapter | UsageLimitHit を AgentAccountLimit へ写す | 窓の長さ → 窓の名 | doeff-codex の型 | `AgentTurnFailed.account_limit` |
| agora-consumer | AgentAccountLimit を読んで口座を外す | 口座・配る判断 | — | doeff-agents の公開型だけを読む |
| codex-tests | doeff-codex の検。読みの thread の順を absorb の呼びの順で再現する | 録った行・筋書き | — | pytest の検 |

## 3. 公開契約と所有(直した後)

- `absorb(runtime, record)` は読みの thread が lock の中で 1 行ずつ呼ぶ。ターンの割り当ての判断はここだけ。
- `current-turn` を書くのは `mark-running` だけ。呼ぶのは absorb の 2 か所(ターンの id を名乗る `Response`・`TurnStarted`)だけ。
- `lines.LimitRecord` の記録は、走っているターンの `TurnLog.limits` に `merged-limits` で重ねて出来事にも積む。無ければ outside。
- ターンを名乗る記録(`lines.TurnRecord`)は名乗ったターンへ。ほかのターンを名乗らない記録は走っているターンへ、無ければ outside。
- `TurnLog.limits` はターンごと。ターンをまたいで持ち越さない。利用枠切れの事実は、そのターンの `TurnLog.limits` だけから作る。

## 4. 盲検の前の主張(`design-pre-blind.md` §4 の写し — 書き換えていない)

| id | 軸 | 変更 | 主張 | 予想の範囲(変える) | 変えない |
|---|---|---|---|---|---|
| S1 | concurrency | 読みの thread と答えを待つ thread の速さの差、または codex の行の順の差で、枠の知らせの届く時点がずれる | ターンを立てるのは読みの thread の absorb の中の mark-running だけ。thread の間の時間の差は割り当てを変えず、行の順の差への対応は codex-handler の absorb の 1 か所 | codex-handler | codex-lines・codex-limits・codex-adapter・agora-consumer |
| S2 | effects | codex の版上げで、ターンを名乗らない新しい通知(口座や thread の断面)が増え、ターンの中で届く | 新しい通知は codex-lines に記録を足すだけで走っているターンへ積まれる。ターンの間に届いた枠の知らせは次のターンへ持ち越さない | codex-lines | codex-handler・codex-limits・codex-adapter・agora-consumer |
| S3 | simulation | 新しい行の順の失敗ケースを、thread や時間に頼らず決定的に足す | 読みの thread の順は absorb の呼びの順で決定的に再現できる。足すのは検の file だけ | 検の file | codex-handler・codex-lines・codex-limits・codex-adapter・agora-consumer |
| X-hw | hardware | 対象外 | 行は JSON の文字で機体に依らない | — | — |
| X-st | storage | 対象外 | ターンの記録は process の memory の値で保存しない。保存は agora の側 | — | — |
| X-dist | distribution | 対象外 | codex の process・読みの thread・handler は 1 つの Pod の 1 つの process の中 | — | — |

前提: codex は 1 つの stdout の行を 1 本の読みの thread で順に読む。absorb は lock の中で呼ばれる。

## 5. 強制方法(直した後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 枠の知らせは turn/start の答えの後のターンへ積まれる | 検(absorb を読みの順で呼ぶ) | `packages/doeff-codex/tests/test_limits.hy` test-a-rate-limit-update-read-right-after-the-turn-start-answer-joins-that-turn | doeff の pytest(マージの列の検・日次) | 答えの側の mark-running だけを見る |
| 同じく turn/started の後のターンへ積まれ、終わった前のターンにも outside にも落ちない | 検 | 同 file test-a-rate-limit-update-read-right-after-turn-started-joins-that-turn(6e09cde27) | 同上 | turn/started の側の mark-running だけを見る |
| 利用枠切れの事実はそのターンの知らせだけから作り、前のターンの刻を持ち越さない(**盲検 B を受けて新設**) | 検(続きの会話の 2 ターン) | 同 file test-a-usage-limit-turn-reads-only-the-notices-of-its-own-turn(5ed5df176) | 同上 | 知らせが無い・窓を名乗らない、の 2 つの筋書き |
| 枠の窓を運ぶ記録は lines の LimitRecord が名乗り、handler は記録の class を並べない(**盲検 A を受けて新設**) | 型の集まり `lines.LimitRecord` + 一致の検 | `lines.hy` の LimitRecord・`handler.hy` absorb の `(isinstance record lines.LimitRecord)`(f977f0d8c)・`test_limits.hy` test-every-record-that-carries-limit-windows-is-a-limit-record(5ed5df176) | 同上 | 欄の型に RateLimitWindow・RateLimits を書いた記録だけを見る(素の `tuple` や dict で窓を運ぶ欄は拾わない)。handler が LimitRecord を使わず class の枝を足す事は止めない |
| どの記録がターンを名乗るかは lines の 1 か所 | 検 | `test_turn_records.hy` | 同上 | 既存 |
| 答えを待つ側が current-turn を書かない | 静的な規則は無い | — | — | 型や lint では言えない。上の 2 つの順の検が、absorb の呼びだけで割り当てが決まる事を確かめる |
| agora まで刻が届く | 結合の検 | agora-controllers `controllers/agora_sim/tests/test_codex_usage_limit_on_agent_runtime.hy` | 印 real_world — 日次の pytest-edge | 壁の時計・本物の子 process |

## 6. 盲検 A・B

- **起動の仕方**: Agent tool(subagent_type general-purpose)の新しい文脈(fork・resume なし)。A と B は 1 つの返事の中で同時に起こし、互いの返答と親の会話を渡していない。
- **要求した model**: `opus`(claude-opus-5-5)。**要求した effort**: `xhigh`(Agent tool の effort の引数で渡した)。
- **優先 1 の gpt-6-astra(low)を使わなかった訳**: この環境の Agent tool の model は sonnet / opus / haiku / fable だけで、起こせない。
- **実際に応答した model と effort**: 観測できていない(要求値だけ)。
- **機体・profile**: agora の cluster の Pod(会話 c-VDDSNTS4APS65DMNJPC808V7XE の作業場)・profile kento。
- **入力**: `blind-input.md`(sha256 上記)。依頼文は skill design-change-validation の references/blind-counterexamples.md の A・B の文そのままに、入力の path と「design-pre-blind.md 以外の同じ dir の file・git の log を読まない」を足した。
- **未加工の返答**: `blind-A-response.md`・`blind-B-response.md`。盲検が作った probe・差分・log は `evidence/blind-A-*`・`evidence/blind-B-*` に写した。

| | 識別子 | 所要 | 失敗例の要点 |
|---|---|---|---|
| A | a9b0c0179a93f75b2 | 1568 秒 | S2: codex が枠の断面をまとめた新しい通知(窓を運ぶ記録 AccountSnapshot)を出すと、lines に記録を足すだけではターンの枠に重ならず、利用枠切れの戻りの刻が黙って None になる。枠の材料をどの記録が運ぶかを handler が class の枝 `(lines.RateLimits)` で持つため(TurnRecord と違って集まりが無い) |
| B | a0c2128612ac6d36f | 542 秒 | S2 の不変条件(持ち越さない): handler が会話の一生で枠を重ね、終わりの事実をそこから作る差分は、doeff-codex の検 23 件を全部通したまま、続きのターン u1 の事実に前のターン u0 の古い刻を載せる |

## 7. 再現と修正

### 7.1 盲検 A を基準の版で再現した

- 記録: `evidence/repro-A-base.log`。命令: `uv run --no-sync --project <作業木> hy <写した probe>`(probe = `evidence/blind-A-probe_aggregate.hy.txt`)。対象版 6e09cde27・rc 0。
- 観測: 今の形(RateLimits)では u1 の事実は (300, 1893456000)。AccountSnapshot を lines の和に足すだけだと、u1.limits は空で事実は (None, None)。merged-limits に渡すと型の前提で断られる。turn_id を持たないので test_turn_records の規則は拾わない。
- 判断: **成立**。ただし変わる所は 2 種類に分かれる。
  - limits の重ね方が新しい形を受けるのは、判定の持ち主の契約の拡張で正当。
  - handler の absorb に枝を足すのは、「どの記録が枠の窓を運ぶか」という codex の行の形の知識が handler に在るための漏れ。
- 直し(f977f0d8c): lines に `LimitRecord`(今は RateLimits だけ)を置き、absorb の class の枝をその集まりの判定へ移した。一致の検(5ed5df176)は、欄の型に RateLimitWindow か RateLimits を持つ CodexLine の記録の集まりが LimitRecord と同じ事を確かめる。

### 7.2 盲検 B を基準の版で再現した

- 記録: `evidence/repro-B.log`。命令: 基準の版を `git archive` で写し、`evidence/blind-B-lt0bc9-account-limits.diff.txt` を当てて `make sync` → `pytest test_limits.hy test_turn_records.hy test_scenarios.hy`、続けて probe `evidence/blind-B-lt0bc9-probe.hy.txt` を写しと基準の木の両方で走らせた。
- 観測: 差分の上で 23 passed(rc 0)。probe の筋書き 1 で u1 の事実は、差分の上で (300, 1893456000)(u0 の古い刻)、基準の木で (None, None)。
- 判断: **成立**。契約(ターンごと・持ち越さない)は実装で守られていたが、強制が無かった。
- 直し(5ed5df176): 検 test-a-usage-limit-turn-reads-only-the-notices-of-its-own-turn。

### 7.3 直した版での失敗例(変異)

- B の変異を直した版へ移した差分(`evidence/mutant-B-on-f977f0d8c.diff.txt`)→ `evidence/N-B-on-f977f0d8c.log`。新しい検 1 件だけが赤(u1 が (300, 1893456000) を運ぶ)、ほかの 24 件は緑。
- A の変異: 窓を運ぶ記録 AccountSnapshot を CodexLine に足し、LimitRecord に入れ忘れた(`evidence/mutant-A-forgotten-limit-record.diff.txt`)→ `evidence/N-A-on-f977f0d8c.log`。一致の検が「窓を運ぶのに LimitRecord に無い: ['AccountSnapshot']」で赤。
- A の正常例: 同じ記録を LimitRecord に入れ、limits の merged-limits が AccountSnapshot を受けるようにした。handler.hy は変えていない(`evidence/change-A-received-in-lines-and-limits.diff.txt` — 変えた file は lines.hy・limits.hy だけ)。結果は `evidence/P-A-on-f977f0d8c.log`。probe `evidence/probe-P-A.hy.txt` で u1 の事実は (300, 1893456000)・AgentAccountLimit(five_hour, 1893456000)。検 25 件が緑。

## 8. 検証の記録

| id | シナリオ | 種類 | 期待 | 観測 | 証拠 |
|---|---|---|---|---|---|
| c-s1-pos-unit | S1 | 正常例 | 直した版で順の検が緑 | test_limits.hy 9 passed × 5 回 | `evidence/S1-S3-mutations-5-runs.log` |
| c-s1-pos-agora | S1 | 正常例 | 直した版の doeff で、agora の結合の検が負荷の下で 12 回緑 | 12 回とも 1 passed・loadavg 87〜125(36 core)。前の版 1db4cb7bf でも 15 回とも緑(`evidence/agora-integration-15-runs-doeff-1db4cb7bf.log`) | `evidence/agora-integration-12-runs-doeff-f977f0d8c.log` |
| c-s1-neg-started | S1 | 失敗例 | turn/started の側の mark-running を外すと、turn/started の順の検だけが赤 | 5 回とも test_a_rate_limit_update_read_right_after_turn_started_joins_that_turn が赤・8 passed | `evidence/S1-S3-mutations-5-runs.log` |
| c-s1-neg-answer | S1 | 失敗例 | 答えの側の mark-running を外すと、答えの順の検だけが赤 | 5 回とも test_a_rate_limit_update_read_right_after_the_turn_start_answer_joins_that_turn が赤・8 passed | 同上 |
| c-s2-pos-other | S2 | 正常例 | 窓を運ばない新しい通知(Other)は、handler を変えずにターンの中なら走っているターンへ、ターンの間なら outside へ | u0 の出来事に Other・u1 には無し・outside に 1 つ | `evidence/S2-other-notice.log` |
| c-s2-pos-limit-record | S2 | 正常例 | 窓を運ぶ新しい記録は、lines(記録と LimitRecord)と limits(重ね方)だけの変更で事実の刻を運ぶ | handler.hy は変えずに u1 (300, 1893456000)・AgentAccountLimit(five_hour)・検 25 passed | `evidence/P-A-on-f977f0d8c.log` |
| c-s2-neg-forgotten | S2 | 失敗例 | 窓を運ぶ記録を LimitRecord に入れ忘れると、一致の検が名指しで赤 | 「窓を運ぶのに LimitRecord に無い: ['AccountSnapshot']」・1 failed 9 passed | `evidence/N-A-on-f977f0d8c.log` |
| c-s2-neg-carry | S2 | 失敗例 | 会話の一生で枠を重ねる変異は、持ち越さない検が赤 | test_a_usage_limit_turn_reads_only_the_notices_of_its_own_turn だけが赤・24 passed | `evidence/N-B-on-f977f0d8c.log` |
| c-s3-pos | S3 | 正常例 | thread も時間も使わない absorb の呼びの順の検が、何度走らせても同じ緑 | 5 回とも 9 passed | `evidence/S1-S3-mutations-5-runs.log` |
| c-s3-neg | S3 | 失敗例 | 変異の赤も、何度走らせても同じ検が同じ訳で赤(負荷の下の揺れに頼らない) | 変異 2 つとも 5 回とも同じ 1 件が赤 | 同上 |

触った file の検(直した版): doeff-codex の検と doeff-agents の codex の adapter の検 2 file で 74 passed(`evidence/fixed-touched-tests.log`)。

## 9. 予測と実測の差

| 事 | 予測 | 実測 | 扱い |
|---|---|---|---|
| S1 の範囲 | codex-handler | 割り当ての 2 か所はどちらも codex-handler の absorb の中で、それぞれ別の検が赤にする。盲検は S1 の失敗例を出さなかった(A は turn/completed の後に知らせが届く順を候補に挙げた。ただ lines.hy の註が「枠の知らせは誤りの通知より先」と書いており、順が変わる根拠は無いとして退けた) | 変更なし |
| S2 の範囲(窓を運ばない通知) | codex-lines | lines さえ変えずに Other として走っているターンへ積まれた | 予測どおり |
| S2 の範囲(窓を運ぶ通知) | codex-lines | 直す前: lines・limits・handler(盲検 A)。直した後: lines・limits | limits は判定の持ち主の契約の拡張(正当)。handler は漏れ → LimitRecord で直した。予測の範囲を広げて合格にはしていない — 主張は §4 のまま |
| S2 の不変条件(持ち越さない) | 守られる | 実装は守っていたが、破る実装が検を全部通った(盲検 B) | 検を足した |
| S3 の範囲 | 検の file だけ | 6e09cde27・5ed5df176 とも検の file だけ | 予測どおり |

## 10. 強制の限界と、前提の外

- LimitRecord の一致の検は欄の型の式だけを見る。窓を素の `tuple` や dict で運ぶ欄は拾わない。
- handler が LimitRecord を使わず、新しい記録の class の枝を足す事は止めない(その時は handler の変更になる)。
- 答えを待つ側が current-turn を書く事を止める静的な規則は無い。順の検 2 つが、absorb の呼びだけで割り当てが決まる事を確かめる。
- 前提の外(未確認): codex の multiAgentMode で子 thread の turn/started が同じ stdout に来るなら、absorb は thread の id を見ずに走っているターンを立てる(盲検 A の付記)。子 thread の行が同じ stdout に来るかは確かめていない。§4 の前提「1 つの会話に 1 本の読みの thread」の外に当たる。
- 盲検の実際の model と effort は観測していない。
