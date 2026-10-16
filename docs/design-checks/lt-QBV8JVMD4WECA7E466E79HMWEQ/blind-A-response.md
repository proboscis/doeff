<!-- 盲検 A の未加工の返答(Agent tool の返り値の本文。harness が付けた各行の字下げだけを外した)。
     起動: Agent tool・subagent_type general-purpose・model=opus(claude-opus-5-5 の階層)・effort=xhigh・新しい文脈(fork / resume なし)・
     機体 = この会話の Pod・agentId a1e6bb3b9b6d8f4d1・所要 857218 ms・tool の呼び 36 回・subagent_tokens 156011。
     実際に走った model は Agent tool の答えから観測できない(要求値のみ)。 -->

失敗例を 1 件見つけました。将来、期限つきで頼み直す新しい状態を coordinator に足すと、その機能の持ち主の module に加えて、止まりの判断の M1(`api_policy.clocks-shifted`)も書き換えないと、止まりの直後にその期限が切れます。M1 が「隠す知識」として持つ「ずらす欄の一覧」は、実際には各機能の持ち主の知識の写しで、漏れを止める仕組みがありません。同じ形の漏れを、既存の drain と盤の TTL の行で実測しました。

## 1. 変更要求と、それが要る理由

- **要求**: worker に「新しい置き先だけを割り当てない印(cordon)」を期限つきで付ける口を足す(`POST/DELETE /workers/<名>/cordon {ttlSeconds}`、頼み手が期限の内に頼み直す)。
- **理由**: 2026-10-13 の atlas のように機体に異常の兆しがある時や保守の前に、その機体へ新しい task やターンを置くのは止めたい。ただし動いているターンは最後まで走らせたい。drain は入れ替えの Service を移し、他の Service を止めるので強すぎます。期限を付けるのは drain の until-ms と同じ理由で、忘れた印が枠を空けたままにしないためです。Kubernetes の cordon と drain の区別と同じで、運用として普通に求められる変更です。
- **自然な実装**: 既存の drain の形をなぞります。
  - `cluster_model` に Cordon(worker・since-ms・until-ms・actor)と `ClusterState.cordons` を足す
  - `reconcile` で期限切れを掃く
  - `sweep-due` に until-ms を足す
  - `place-jobs` と `place-tasks` で cordon 中の worker を除く
  - `durable_kv` に `cordon/<名>` の鍵を足す
  - `GET /workers/<名>` に表示を足す

## 2. 関係する主張と前提、予想に反して変わる所

**関係する主張**
- M1 の「所有する知識: ずらす物の一覧(worker の最後の連絡と印・task の lease・Rollout の段の起点・入れ替えの期限・盤の lease・alive-ms)」と、「隠す知識: ずらす欄の一覧」。
- §4 の限界の欄「全部の期限の欄を網羅したかは test で縛っていない」。
- R8 と stall-of の註「止まっていた長さは経過・沈黙に数えない」。

**前提は守っています**
- coordinator は 1 台、置き場は atlas、agora-controllers は変えない、止まりは 3 分(lease を越える)、閾は lease-ms。どれも変えません。
- 変更シナリオ S1〜S3 のどの軸でもなく(機能の追加の軸)、除外した S4〜S6 にも当たりません。
- 新しい口と鍵はこの機能の公開契約の拡張で、止まりの設計が「変えない」とした既存の口と鍵の形は変えません。
- したがって適用範囲内の例です。

**予想に反して変わる所**
- M1 の `clocks-shifted` に `:cordons` の until-ms のずらしを足す必要があります。
- 足さないと、止まりの直後の歩で worker は生きたまま(ずらされる)なのに、cordon だけが切れます。その結果、同じ歩の `place-*` が保守中の worker に新しい置き先を置きます。
- 一覧を共有しているので、M4(起き直し)の振る舞いも同時に変わります。M1 の判断の test にも 1 件要ります。

## 3. どの知識がどこへ漏れるか

| 種類 | 中身 | 判定 |
|---|---|---|
| 意図した公開契約の拡張 | cordon の口、`cordon/<名>` の鍵、`GET /workers/<名>` の欄 | 機能の拡張(欠陥ではない) |
| 配線の変更 | respond の振り分け、`reconcile` の掃除、`place-*` の除外、`sweep-due` の期限 | 期待どおり |
| 共同の不変条件 | 「止まっていた長さを、頼み直しで延びる期限に数えない」「ずらしは後ろへだけ(C4)」。起き直しと止まりで共有する | 共有は正当。ただし各持ち主にずらしを出させる契約が無い |
| **漏れ** | 「until-ms は頼み手が coordinator 越しに延ばす期限なのでずらす。since-ms は表示用の事実なのでずらさない」という cordon の持ち主の判断を、M1 が書くことになる | 隠すはずの知識の漏洩 |

- **M1 は他の module の欄を名指しで持っています**: `WorkerInfo.last-seen-ms` の `min now`、`TaskRecord.lease-until-ms`、`HandoffPhase.WAITING`、盤の `SEMAPHORE-PREFIX` です。
- **持ち主に任せている種類もあります**: Rollout は `rollout_policy.shift-clocks`、semaphore は `lease_rules.holders-shifted` で、持ち主がずらしを出し、M1 は呼ぶだけです。この形なら、新しい種類での M1 の変更は 1 行の配線で済みます。
- **期限の一覧が 2 つあります**:
  - `cluster_policy` の `sweep-due`・`task-due`・`liveness-due`。ADR-002 の法 a-sleeping-stand-in-changes-no-decision の差の検で縛られています。
  - `api_policy.clocks-shifted`。縛りがありません。
  - 機能の作者は前者を足すよう押されますが、後者は押されないので、黙って漏れます。
- **既存の証拠**: drain の until-ms と盤の TTL の行の expires-ms は `sweep-due` にあるのに、`clocks-shifted` にありません。下の 4 で実測しました。warm の until-ms と、切り離した task の結果の保持(finished-ms + retain-ms)も、code を読んだ限り同じ形です(未実行)。

## 4. 最小の入力・再現手順・観測すべき結果

cordon はまだ無いので、同じ形の期限を持つ既存の drain を代わりに使って実測しました。

- **手順**: `cd /home/ubuntu/.worktrees/doeff-wt-stall-design-check && uv run python /tmp/tmp.q1n0oOUIX6/repro_stall_deadlines.py`
- **入力**:
  1. 刻 1000 に w の heartbeat、`POST /workers/w/drain {ttlSeconds 60}`、`PUT /board/presence-x {ttlSeconds 60}`、`POST /leases/turn-x {claim, ttlMs 60000}`
  2. 刻 3000 に tick
  3. `stall-of(StepPace 3000/3005/11001, now=183000)` で止まりの長さ 180000 を得る
  4. `clocks-shifted` の後に、刻 183000 で tick
- **結果(実測)**:
  - 止まる前の `sweep-due` は `DueAt(61000)` で、ADR-002 の期限の一覧には drain が入っています。
  - WorkerGone は出ず、`silent=[]` でした。ずらしは効いています。
  - semaphore の担い手の期限は 61000 → 241000 にずれ、残りました(一覧の中)。
  - drains は `['w']` → `[]` になり、盤の行 presence-x も消えました。
  - `GET /workers/w` は `ready False`(drain は untilMs 61000・phase Drained)から `ready True`(drain None)に変わりました。
  - 起き直しの道(`resume-after-downtime`、gap 180000)でも同じ結果でした。
- **cordon の場合(推測・未実行)**: 同じ形で、`clocks-shifted` に cordon を足さない限り、止まりの直後に cordon が消えます。
  - 確かめる test: `test_coordinator_stall.hy` の 6 本目と同じ形で、cordon の在る状態を 3 分止め、`tick(clocks-shifted …)` の後に cordon が残ることを assert します。足す前は赤になるはずです。
  - 本番で見る物: 止まりの log の行(`STALL-LOG`)が出た歩の直後に、`GET /workers/<名>` の cordon が消え、その worker への置き先が出来事の列に出ます。
- 指定の 2 file の test は 8 passed でした(実測)。どちらの test もこの漏れには掛かりません。drain・盤の TTL の行・新しい期限を含む状態を作らないからです。

## 5. 未確認の前提と不足

- cordon は未実装で、drain の形をなぞって作られるというのは推測です。drain の変種として作っても、drain の until-ms 自体がずらされないので、M1 の変更が要るという結論は同じです。
- drain・盤の TTL の行・warm・結果の保持がずらされないのが、意図した除外かは分かりません。読んだ範囲では、註にも ADR にも除外の訳は見当たりませんでした。仮に意図した除外でも、「どの期限をずらすか」の判断が M1 にあるという点は変わりません。
- ADR-002 の test(`test_idle_skip`)が `sweep-due` の書き忘れを捕まえるのは、その機能の筋書きがある時だけだと推測しています(未実行)。
- drain が切れた後の本番の害(preStop の頼み直しとの前後関係、置き直し)は模擬で回していません。実測したのは純粋な判断の段だけです。
- warm は runtimeEnv の形が要るので実行せず、読んだだけです。
- doeff-linter は当てていません。全数の test は指示どおり走らせていません。

**別件(責務分離の失敗例ではない・参考)**: 次の起きる予定が前の歩から lease 近く先(planned 11001)の時、止まりが 12〜19 秒だと `stall-of` は止まりと見なさず(None)、WorkerGone が出ました。21 秒で初めて止まりと判定されました(実測、`/tmp/tmp.q1n0oOUIX6/side_band.py`)。設計の「lease-ms 以下の止まりは見つけない」という文は、実際には最大でおよそ 2×lease まで見つけないことを意味します。法の式とは合っていますが、前提の文は狭く読めます。

作業木は書き換えていません(git status に変化なし、HEAD は e8d948145 のまま)。作った file は `/tmp/tmp.q1n0oOUIX6/` の script 2 本だけで、再実行用に残してあります。

status: done — 責務分離の失敗例 1 件を実測つきで返した
WAIT: work — 依頼元(lt-QBV8JVMD4WECA7E466E79HMWEQ の検証)の次の依頼
