# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 86d9d1ca1c8c9e15e2303ba8e20f53879d78dbbd7f421c8a9014e15ea04b49f1) | 1 回目 [blind-b-input.md](blind-b-input.md)(sha256 fff143a2bd4fe30a674d3b3cf4a34ef074bab52126a9a0d42766928de958afe1)・2 回目 [blind-b-input-retry.md](blind-b-input-retry.md)(sha256 a830b2d03dbc50b32bd0613c1556a5fd86a72be303bb0495ab4504feb65050a5 — 実験の木の path だけを b2 へ替えた) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 c29dbeb0420f0105cbd76e42bca06433bb74926cd452c1839384a83da0875d35)と ../design-pre-blind.md(sha256 236b93a5c9e77d64b78cc55a44b87fea30c64ce3feab76f1dd733c25b148dc74・2026-10-11T23:41:55Z に固定) | 同じ + [blind-b-checks.md](blind-b-checks.md)(sha256 abccfb5fa6c887d5b78f2802571fe9d379b64117cbe9985406f6981b2bdaa087) |
| 材料と依頼文を固定した commit | doeff の branch wt/design-check-lt-9e0w の bd9f2fc6ce14c267a3aa19ed56d581f7c7286ed4(2026-10-11T23:43:25Z に push — 1 回目を起こす前)・e4a19536ccb3e27a0f71b78097fafd09f9e2f226(2026-10-12T03:02:53Z — 2 回目を起こす前) | 同じ |
| 対象のコードの版 | doeff main 74ebb50b6c3c5de9a652088d11e16371d954abb1 | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low --skip-git-repo-check -s read-only <A の依頼文>` が 401 Unauthorized(Missing bearer or basic authentication)で落ちた — この Pod に codex の資格が無い。[codex-attempt.txt](codex-attempt.txt)・2026-10-11T23:43:47Z〜23:44:10Z・1 回・rc 1 | 同じ 1 回の試しで第 1 候補が使えないと判じ、B では codex を起こしていない |
| 第 2 候補(Agent tool の subagent・subagent_type general-purpose・model 引数 `opus` = Opus 5.5・effort 引数 `xhigh`)の 1 回目 | 2026-10-11T23:44 頃に起こし、すぐ API の 429(週の上限・request id req_011CfwM1PynFvxQUE8MkLEdG)で止まった。返答なし | 同じ時に起こし、26 回の tool の使用・314,001 ms の後に 429(req_011CfwM11vicQe5faeUGrNmZ)で止まった。返った部分は「Pre-change の lint と semgrep を、変える予定の file で測ります。」の 1 行だけ(agentId a5ed7cf3de2411cf5)。実験の木に残した [../evidence/blind-attempt-1/blind-b-partial-pre-lint.txt](../evidence/blind-attempt-1/blind-b-partial-pre-lint.txt) を写した。この試行の木 doeff-wt-blind-b-ki-b0e8 は 2 回目に使っていない |
| 第 2 候補の 2 回目(上限による実行の失敗の後の起こし直し) | 会話が別の資格の置き場で起き直した後(2026-10-12T03:03Z)に、同じ依頼文で新しい文脈に起こした | 同じ時に、実験の木だけを新しい doeff-wt-blind-b2-ki-b0e8 に替えた依頼文で起こした |
| 実 model と effort の効き | 観測していない(tool は model の名を返さない・effort が効いたかは見えない)。要求した値だけを記す。429 の返事の「model sent to the API: claude-opus-5-5」は 1 回目の要求の model | 同じ |
| 識別子(2 回目) | agentId abce2068789bdce7d | agentId a252823029d84c89e |
| 時間(2 回目) | 883,613 ms(tool の使用 34 回・subagent の token 162,091) | 1,941,874 ms(tool の使用 59 回・subagent の token 188,446) |
| 機体・profile | agora の会話 c-NN28KKGW3TAMZW5JKK572H9JRD の Pod(作業の根 /work/agent-workspaces/a-NN28KKGW3TP3F8MA4K2WX3S7AD。2026-10-12 に会話の id を直した — 前は作業の根の id を会話の id と書き誤っていた)。1 回目と 2 回目で資格の置き場が違う(会話が起き直した) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で同時に、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・既知の失敗例・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool の返す字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ) |
| 実験の木と残した物 | `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-b0e8`(detached・74ebb50b6)。blind-artifacts/ と足した検を ../evidence/blind-a-artifacts/ に写した | `/home/ubuntu/.worktrees/doeff-wt-blind-b2-ki-b0e8`(同じ)。blind-artifacts/ を ../evidence/blind-b-artifacts/ に写した |

材料の制限: 読む所を design-pre-blind.md と common-input.md の表の実コード(B は blind-b-checks.md も)に限り、`docs/design-checks/` の下の他の file を読まないように書いた。A は検の書き方を知るために表に無い file を 4 つ読んだと申告した(返答の §5 — 設計の主張の判断には使っていないとの申告)。
