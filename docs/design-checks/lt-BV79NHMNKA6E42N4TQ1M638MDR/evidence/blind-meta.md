# 盲検 A・B の起動の記録(設計検証 lt-BV79NHMNKA6E42N4TQ1M638MDR)

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: この会話の起動手段(Claude Code の Agent tool)が受ける model は sonnet / opus / haiku / fable だけで、gpt-6-astra を選べない | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値・dotfiles の tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話と同じ機体)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md・sha256 be79823d…)と作業木の BLIND_INPUT.md(= ../blind-input.md・sha256 0c3e1759f0562db42ecc3afb6adfad1e159478e49d411297f061832447e711e3) | 同じ(blind-b-prompt.md・sha256 876c3df6…。A の返答は渡していない) |
| 作業の場所 | 使い捨ての作業木 /home/ubuntu/.worktrees/doeff-wt-blind-a-b4c0(10addc647)と /tmp/blind-a-b4c0/(写し = blind-a/) | /home/ubuntu/.worktrees/doeff-wt-blind-b-b4c0(10addc647)と /tmp/blind-b-b4c0/(写し = blind-b/) |
| session の識別子 | agentId a7e8b44a4afdfc9c1 | agentId a174fea40db5986ca |
| 所要時間 | 520,242 ms(道具の呼び 23 回) | 1,028,265 ms(道具の呼び 32 回) |
| 起動の時刻 | 2026-10-12T04:13Z ごろ(A と B を同じ返事で同時に起動) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(harness が付けた行頭の字下げ 2 字だけを外した)。
盲検が /tmp に置いた script は、集め手に拾われないよう名の末尾に .txt を足して blind-a/・blind-b/ に写した(中身は同じ)。
