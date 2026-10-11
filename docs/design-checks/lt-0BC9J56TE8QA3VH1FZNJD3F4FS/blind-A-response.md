S2 の主張には失敗例が作れました。codex の版上げで、口座の枠の断面を何本もまとめた新しい通知が来る場合です。S2 が予想する「codex-lines に記録を足すだけ」で受けると、R1(戻りの刻が毎回 agora に届く)が何のエラーも出さずに破れます。直すには codex-handler か codex-limits のどちらかを必ず変えることになり、S2 の「変えない」に反します。元の主張の前提は守っていて、契約の適用範囲内の例です。

## 1. 変更要求と、それが必要になる理由
- **要求:** codex の版上げで、利用枠の断面が今の `account/rateLimits/updated`(1 通に断面 1 つ)ではなく、口座の全ての枠の断面を 1 通にまとめた新しい通知で届くようになる。method 名は仮に `account/snapshot/updated` とし、params に枠ごとの断面の列を持つ。旧い通知は止まるか、尽きた窓を名乗らなくなる。それでも agora は R1 を守るため、この通知から利用枠切れの事実を作り続けたい。
- **理由:**
  - 録った 0.162.1 の行(実測)では、断面がすでに `limitId`・`limitName`・`normalModelSlug`・`credits`・`individualLimit`・`spendControlReached`・`planType`・`rateLimitReachedType` を持っています。1 つの口座に枠が複数あります。
  - S2 自身が「口座の断面」の新しい通知を変更の軸に挙げています。

## 2. 関係する主張・前提と、予想に反して変わるもの
- **主張 S2:** 新しい通知は codex-lines に記録を足すだけで、走っているターンへ積まれる。予想の範囲は codex-lines、変えないのは codex-handler・codex-limits・codex-adapter・agora-consumer。
- **前提は守っています:**
  - 読みの thread は 1 本で、absorb は lock の中で呼ぶ。probe も absorb を読みの順に 1 本で呼びます。
  - 通知は turn/started と turn/completed の間に届く(S2 の条件どおり)。
  - ターンをまたいだ持ち越しは要りません(§3「TurnLog.limits はターンごと」も守る)。
- **予想に反して変わるもの:** codex-handler の absorb と、codex-limits の公開の口 `merged-limits`(今は RateLimits 1 つしか受けない)。どの道を選んでも、このどちらかが変わります。
- **変わらないもの:** 頁の end(TurnEnded.usage-limit)・adapter・doeff-agents の公開型・agora-consumer・fake。上の層への公開契約は守られていて、ここは設計どおりです。

## 3. どの知識がどこへ漏れているか
- **漏れている所:** 「どの記録が利用枠切れの判定の材料か」と「その形(1 記録 = 枠 1 つ)」が、absorb の具体の class の枝 `(lines.RateLimits)` に書かれています(`/home/ubuntu/.worktrees/doeff-wt-dc-lt0bc9/packages/doeff-codex/src/doeff_codex/handler.hy` L184-190)。
  - 本来これは codex-lines(記録の形)と codex-limits(判定の材料)が持つ知識です。
  - 同じ種類の判断「どの記録がターンを名乗るか」は、lines の `TurnRecord` に集めてあり、handler は class を並べません(test_turn_records が強制)。判定の材料には、この集める仕組みがありません。
- **何が起きるか:** 足した記録は absorb の既定の枝に落ちます。出来事としては積まれるので S2 の「積まれる」は成り立ちますが、`TurnLog.limits` には入りません。
- **逃げ道が無いこと:** `classify-line` は 1 行から 1 記録しか作れません(lines.hy L718-734、handler の on-line L216-223)。そのため、枠が 2 つ以上ある断面は RateLimits 1 つにできません。
  - (a) RateLimits に残りの断面を入れ子で持たせる → limits の重ね方と判定が変わります。lines の記録の形も、handler の class の振り分けに合わせて曲がります。
  - (b) lines が断面を 1 つ選ぶ → 「遅く戻る方を取る」という limits の判定が lines へ漏れます。
- **変更の区別:**

| 変更 | 区別 |
|---|---|
| lines に wire の型と記録を足す | S2 の予想どおり |
| `merged-limits` が断面の列や、全部を置き換える断面を受けるよう広げる | codex-limits の公開契約の拡張(判定の中身なので責務どおり) |
| absorb に枝を足す | 予想外。漏れた知識(材料の選び)のための配線の変更 |
| fake と本番で事実が同じという共同の不変条件 | 影響なし(fake は事実を直に持つ) |
| 強制方法 | 穴あり。足した記録は turn_id を持たないので test_turn_records は拾わない |

## 4. 最小の入力と再現の手順
仮の行(method 名と形は仮):
```
{"method":"account/snapshot/updated","params":{"rateLimitsByLimitId":{
  "codex":{"limitId":"codex","primary":{"usedPercent":37,"windowDurationMins":10080,"resetsAt":1893888000}},
  "codex_other":{"limitId":"codex_other","primary":{"usedPercent":100,"windowDurationMins":300,"resetsAt":1893456000}}}}}
```
行の順: turn/start の答え(u1)→ turn/started(u1)→ 上の行 → turn/completed(u1・failed・usageLimitExceeded)

- **probe:** `/tmp/blind-A/probe_aggregate.hy`。木の file は変えていません。lines が足すはずの記録 `AccountSnapshot` を probe の中で作り、`lines.CodexLine` に足しました。handler・limits・adapter は木のままです。
- **実行:** `uv run --no-sync --project /home/ubuntu/.worktrees/doeff-wt-dc-lt0bc9 hy /tmp/blind-A/probe_aggregate.hy`
- **出力:** `/tmp/blind-A/probe_aggregate.log`

観測した結果(実測):

| 場合 | u1 の出来事 | outside | u1.limits | end.usage-limit | agora へ渡るもの |
|---|---|---|---|---|---|
| A 今の形 | RateLimits あり | () | 300 分の窓 | (300, 1893456000) | AgentAccountLimit(five_hour, 1893456000) |
| B S2 の予想の変更だけ | AccountSnapshot あり | () | () | (None, None) | AgentAccountLimit(None, None) |

- **C:** `merged-limits` に AccountSnapshot を渡すと、型の前提条件で断られます(`fresh expected RateLimits`)。
- **D:** handler 側で断面の列を `merged-limits` に重ねると、事実は (300, …) に戻ります。直す置き場は absorb です。
- **E:** AccountSnapshot は turn_id を持たず TurnRecord にも入らないので、今の強制方法では赤になりません。
- 基準として、指定の `test_limits.hy` を木のまま走らせ、7 passed でした。

## 5. 未確認の前提と、足りない情報
- **推測:**
  - codex が断面を 1 通にまとめること、旧い通知を止めること、method 名と形。codex の protocol の source は見ていません。
  - 新しい版で usage-limit の行を録り直せば、test_scenarios の stub の検が赤になって気づける見込みです(未実行)。録り直さない限り、今の検では気づけません。
- **この失敗が起きない条件:**
  - 新しい通知の断面が 1 つだけなら、lines が RateLimits に写せるので S2 は成り立ちます。失敗は、断面が 2 つ以上か、RateLimits の形に無い事実を運ぶ時に限ります。
  - 旧い `account/rateLimits/updated` が並んで届き続けるなら、R1 は今のまま保たれます。
- **試していないこと:**
  - probe は lines の変更を、実行中に union へ足して模しただけです。classify-line が新しい method を分ける道と、写した木で make sync して走らせる道は試していません。
  - agora が resets_at None をどう扱うかは、agora-controllers を読んでいないので分かりません。

**主例にしなかった候補:**
- **S1(枠の知らせが turn/completed の後に届く順):** absorb だけでは決定的に直せません(end を時間で待つか、終わった後に事実を直す公開契約が要る)。ただ lines.hy が 0.162.1 の順を「枠の知らせは誤りの通知より先」と書いており、順が変わる根拠を得られませんでした。
- **口座の断り(AgentAccountRefusal)を codex でも出す:** 材料が turn/completed の誤りだけなら、adapter が層 2 の判定を呼べば済み、lines と handler は変わりません。
- **rateLimitReachedType を判定に使う・usageLimitExceeded が object の形で来る:** どちらも lines と limits で済み、設計どおりです。
- **multiAgentMode の子 thread の turn/started:** absorb は thread の id を見ずに走っているターンを立てるので、割り当てが崩れ得ます。ただ直すのは handler だけで、複数の責務にまたがる例ではありません。子 thread の行が同じ stdout に来るかは未確認・未実測です。

WAIT: work — 呼び手の次の依頼
