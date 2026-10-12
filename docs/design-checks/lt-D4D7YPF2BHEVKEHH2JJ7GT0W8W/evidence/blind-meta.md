# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: この会話の起動手段(Claude Code の Agent tool)が受ける model は sonnet / opus / haiku / fable だけで、gpt-6-astra を選べない | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値・dotfiles の tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話と同じ機体 zeus)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md・sha256 5a19c6f3…)と作業木の BLIND_INPUT.md(= blind-input.md と同じ sha256 3a8c301e9c6de9276f5b750d4cec487019565c1c135e9153e0711a185e6894df) | 同じ(blind-b-prompt.md・sha256 777397b4…。A の返答は渡していない) |
| 作業の場所 | 使い捨ての作業木 /home/ubuntu/.worktrees/doeff-wt-blind-a-d4d7(2bf226a96)と /tmp/blind-a-d4d7/ | /home/ubuntu/.worktrees/doeff-wt-blind-b-d4d7(2bf226a96)と /tmp/blind-b-d4d7/ |
| session の識別子 | agentId acaa9fe68ec0075f5 | agentId acb35524589f9e68e |
| 所要時間 | 1,111,306 ms(道具の呼び 60 回) | 786,544 ms(道具の呼び 50 回) |
| 起動の時刻 | 2026-10-11T23:48Z ごろ(A と B を同じ返事で同時に起動) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(harness が付けた行頭の字下げ 2 字だけを外した)。
