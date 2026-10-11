# 盲検 A の返答(未加工)

- 起動: Agent tool(subagent_type general-purpose・新しい文脈・fork / resume なし)・要求 model `opus`(claude-opus-5-5)・effort `xhigh`
- 識別子: agentId a323b6dfa7b17caad・所要 673242 ms・tool の呼び 42 回
- 機体: doeff-worker-zeus-5d6d78459d-zl8pw(親と同じ Pod)・profile: 親と同じ agora の会話の資格(子へ別の資格は渡していない)
- 優先 1 の gpt-6-astra(low)は、この環境の subagent の起動手段(Agent tool の model は sonnet / opus / haiku / fable)で起こせないため、優先 2 の opus(xhigh)で起こした。実際に応答した model の名は観測できていない(要求値だけ)。
- 入力: blind-input.md(sha256 9e29e210d34b45e36a86bbeafb5225c45ea8bc64bd7525694a4adbd753904385)と、skill の参照文書の A の依頼文(本文へ直に貼った)
- 起動の刻: 2026-10-11T16:23Z 頃(B と同時)

---- 返答 ----

## 結論

責務分離が崩れる例が 1 つ見つかりました。対象の版は doeff 30d01f93 と agora-controllers の origin/main 836fd11f です。共有の source は変えていません。作業木の差分は、始める前から在った `.scratch/` だけです。

起点は S1(fake に利用枠切れのターンを出す欄を足す)を含む、ある変更要求です。この欄に意味のある値を入れるには、adapter の判定を逆向きに知る必要があります。adapter の中だけで決めてよいとされた知識(窓の名の表・使った割合 100 の閾値・誤りの種類の綴り)が、adapter の外に 2 つ目の定義として要ります。その結果、S2 と S4 の「adapter の 1 か所だけ直せばよい」が成り立たなくなります。

## 1. 変更要求と、それが必要になる理由

**変更要求**: agora の模擬の目録 `controllers/agora_sim/peers/agent_cli_account_ends.hy`(#3983 の子 #4224)に、「codex の会話が 5 時間の窓で利用枠切れになる」行を 1 つ足します。そして本番と同じ組(codex の adapter と doeff-codex の fake)で、「その口座を戻りの刻まで外し、別の口座で配り直す」ことを確かめます。前段として S1 を入れます。

**理由**:
- 要件 1 は、agora が claude と同じ道で codex の口座を外すことを求めています。目録の頭の註も「目録に無い形が本番で出たら行を足し、失敗ケースを 1 本足す」と決めています。
- agora は既に codex の会話を fake で走らせています(`controllers/foundation/agent_runtime.hy` の `fake-codex-process-layer`、`controllers/agora_sim/tests/test_codex_turn_on_agent_runtime.hy`)。
- 今、codex の利用枠切れを最初から最後まで通す検は、doeff の stub の上だけです。`/home/ubuntu/.worktrees/doeff-wt-codex-usage-limit/packages/doeff-agents/tests/test_headless_codex_adapter.hy` の利用枠切れの検は STUB と PUBLIC-STUB だけで、FAKE がありません。fake の `FakeReply` の欄も `pieces` と `hold` だけです(実測 E1)。
- claude には同じ道が既にあります。agora の `account-limit-hit-of`(agent_runtime.hy:635)は、`doeff_claude_code.lines.AccountLimitHit` へ欄を 1 対 1 で写すだけです。claude では層 2 が利用枠切れの型を持ち、判定も層 2 の中にあるからです。

## 2. 関係する主張・前提と、予想に反して変わる物

| 主張 | 前提 | この例で前提は守られているか |
|---|---|---|
| S1: fake だけ変える | fake が lines の記録の型を使える | 守られている。fake.hy は lines の型を import 済みで、RateLimits・RateLimitWindow・TurnEnded は lines の公開の型 |
| S2: 窓の名の表は adapter の 1 か所 | lines は窓の長さをそのまま運ぶ | 守られている |
| S4: 綴りの変化は adapter の定数だけ | 綴りが文字列の形のまま | 守られている |

どの前提も変えていないので、契約の適用範囲の中の例です。

**fake に要る逆向きの値**: 「5 時間の窓が尽き、T に戻る」を fake の欄で表すには、次の値が要ります。
- 窓の長さ 300 分(`WINDOW-NAMES` の逆向き)
- 使った割合 100 以上
- 終わりの状態 FAILED と、誤りの種類 `"usageLimitExceeded"`
- 枠の知らせを、失敗するのと同じターンの、終わりより前に置くこと

**この逆向きの写しを置ける場所は 3 つで、どれも主張のどれかを破ります。**
- **(a) agora の `agent_runtime.hy` に置く**(claude の `account-limit-hit-of` の codex 版)。
  - agora が codex の綴りと窓の長さ(分)を持つことになります。
  - codex の版上げで綴りが変わると(S4)、別の repo の agora-controllers も直す必要があります。
  - 窓の名の表を変えると(S2)、agora 側の逆向きの表も直す必要があります。
- **(b) doeff-codex の fake に置く**(欄を意味で受ける形)。
  - fake は層が下なので doeff-agents を import できず、`"usageLimitExceeded"` と閾値 100 を自分で書き写すことになります。今 doeff-codex の中でこの綴りが出てくるのは、lines.hy:315 の docstring だけです。
  - S4 は adapter と fake の 2 か所の変更になります。
  - 窓を名(five_hour)で受けるなら、codex の層 2 が claude の綴りを持つことになります。
- **(c) doeff-agents の adapter の隣に、逆向きに写す公開の口を足す**。
  - 定義は 1 つに保てますが、S1 の「codex-adapter は変えない」に反します。
  - 「adapter の中で自由に決めてよい」とされた窓の名の表と閾値が、公開の契約になります。

## 3. どの知識がどこへ漏れるか(種類の区別)

| 変更 | 種類 |
|---|---|
| `FakeReply` に欄を足す | 意図した公開契約の拡張(S1 そのもの) |
| `headless_compose` が新しい型を再 export する・agora_sim が codex の会話に fake を選ぶ・目録に 1 行足す | 配線の変更 |
| 枠の知らせは、失敗するターンの頁に終わりより前に載っていなければならない | 共同の不変条件(下記) |
| 窓の名の表の逆向き・閾値 100・綴り `usageLimitExceeded` を adapter の外で知る | 隠すはずの知識の漏洩 |

**共同の不変条件の中身**: この条件は 3 か所が一緒に守っています。
- handler の absorb(枠の知らせを走っているターンへ置く)
- fake が記録を出す順
- adapter の `reset-turn`(ターンの始めに枠の知らせを捨てる。headless_codex.hy:124-130)

ところが契約の文は「届いた順に載る」としか書いておらず、「そのターンの間に届いた知らせだけが判断に使われる」ことが書かれていません。

**根本の原因**: codex では「利用枠切れ」という概念が層 3(adapter)にしかなく、層 2 にその型がありません。claude は層 2 に `AccountLimitHit` を持っています。そのため、層 2 の替え玉を層 3 の言葉で動かそうとする人は、誰でも adapter の判定を逆向きに持つことになります。

**今ある検ではどれも止まりません**:
- doeff の `doeff-packages-have-no-application-vocabulary` は agora などの業務の語しか見ません。
- agora の S6 の規則は `agent_runtime.hy` を対象から外しています。
- agora の S3 の規則が禁じるのは起動の引数の語だけです。

## 4. 最小の入力例・再現手順・観測(実測)

- **実験の file**: `/work/agent-workspaces/a-BKM85J4QVX1ESWDR66C128P6RH/scratch/probe_codex_limit.hy`(共有の source の外に置いた物)
- **命令**: `cd /home/ubuntu/.worktrees/doeff-wt-codex-usage-limit && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync hy <上の file>`
- **やり方**: S1 の欄の代わりに、終わらないターン(hold)を開け、fake の世界のそのターンへ記録を直に差し込みます(`FakeThread.add`)。adapter・fake・模擬の時計は、本物の組み立ての部品(`headless_compose`)を使います。

**E1**: `FakeReply` の欄は `('pieces', 'hold')` だけ。今の fake では利用枠切れのターンを出せません。

**E2**: 「5 時間の窓が尽き、T に戻る」を書き方ごとに `account-limit-of` へ渡した結果。

| 書き方 | 結果 |
|---|---|
| 綴り `usageLimitExceeded`・300 分・100% | window five_hour、resets_at T(正しく出る) |
| 使った割合 99% | window None、resets_at None |
| 窓 360 分 | window None、resets_at T |
| 綴り `usage_limit_reached`(上流の 429 の綴り。record_app_server_lines.py:10) | None(利用枠切れにならない) |

**E3**(adapter と fake を通した実行)

| 場合 | 結果 |
|---|---|
| C1: 枠の知らせと失敗が同じターン | `AgentTurnFailed.account_limit` = five_hour / T |
| C2: 枠の知らせは前の(完了した)ターンにだけあり、失敗のターンには無い | `account_limit` = (None, None) |

**観測すべき結果**: 逆向きの写しが 1 か所でもずれると、目録の期待 `EndsOnLimit` を満たしません。S4 を模すなら、2 つ目の定義を残したまま adapter の定数だけを変えれば、E2 の 4 行目と同じく None になります。

**agora 側の帰結**(コードを読んだだけで、実行はしていません):
- resets_at が None だと、`limit-of` → `rate-limited` で `LimitUnknown` になります(dispatch_table.hy:1705)。
- `LimitUnknown` を解くのは `ProfileExhaustionChanged` だけです。その書き手の見回りは「codex は預かり所に usage の口が無いので cli で外す」として codex を対象から外しています。
- したがって codex の口座は、配る担当が手元に持つ状態が消えるまで外れたままになる、と読めます。

## 5. 未確認の前提・不足する情報

- **実測した物**: E1、E2、E3 の C1・C2・C3。判定の検(`test_headless_codex_account_limit.hy`)は 4 passed。
- **推測・未実行の物**:
  - agora 側の帰結。コードを読んだだけです。
  - 目録に codex の行を足す計画(card)があるかは確かめていません。
  - S1 の欄を生の記録で受けるか意味で受けるかは決まっていないので、(a)(b)(c) の 3 通りで論じました。
  - agora の目録の検(`test_account_end_shapes.hy`)は走らせていません。
- **本物の codex での振る舞い**:
  - 本物の上流で利用枠切れになった時、同じターンに枠の知らせを出すかは未確認です。録った行は偽の上流が header で枠を名乗った物です。出さないなら、本番で C2 の形(戻りの刻が None)が起きます。
  - limit-id の違う枠の知らせを、同じターンに続けて出すかも未確認です。出すなら `merged-limits` が尽きた窓を別の枠の知らせで上書きして失います。ただしこれは adapter の中で直せるので、責務分離の問題ではありません。

**付記(S3 に当たる観測。実験の C3)**:
- 「ターンの外や前のターンで届いた知らせを持ち越す」を handler だけで入れた頁を模しました。戻りの刻が 1000(過去)の古い知らせを、失敗のターンへ載せた形です。
- adapter は `AgentAccountLimit(resets_at=1000)` を返しました(実測)。`account-limit-of` は時計を読まず、知らせの古さを判断しません。
- agora の配置は戻りの刻を過ぎた尽きを「尽きていない」と読む(placement.hy の `release-at`)ので、同じ口座へすぐ配り直すと読めます(静的に読んだだけ)。
- 持ち越しを正しく入れるには、adapter が古さを判断する(S3 の「adapter は変えない」に反する)か、handler が窓の意味を知る(漏洩)かのどちらかになります。S3 の前提「枠の知らせの記録の型は変えない」は守っています。

WAIT: work — 呼び手の次の依頼
