# 盲検 A・B の起動の記録

| 項目 | A | B |
|---|---|---|
| 要求したモデル・effort | 1 番目 gpt-6-astra・low → この会話の Pod に起こす手段が無い(Agent tool の model は sonnet / opus / haiku / fable だけ・`claude -p` は環境の決まりで使わない)ので未試行。2 番目 opus 階層・xhigh を使った | 同左 |
| 起動手段 | Claude Code の Agent tool(subagent_type general-purpose・model opus・effort xhigh)。新しい文脈で起こし、fork・resume は使っていない。A と B は 1 つの返事の中で並べて同時に起こし、互いの返答は見ていない | 同左 |
| 機体・profile | agora の会話 Pod(親の会話の資格のまま。親の API key は渡していない) | 同左 |
| 識別子 | agentId a1057aadf18df47e7 | agentId a6b21b8a51afbecaf |
| 実際に観測できたモデル | Agent tool の返りに model の名は出ない — 要求値 opus(claude-opus-5-5 に解く)と区別して未確認 | 同左 |
| 所要時間 | 636 秒(tool 42 回) | 892 秒(tool 44 回) |
| 起動の刻 | 2026-10-12T02:54Z 頃(盲検の前の版 54a945270 を push した直後) | 同左 |
| 入力 | blind/blind-a-prompt.md(固定の依頼文 + 材料の path)・blind/material.md | blind/blind-b-prompt.md・blind/material.md |
| 返答 | blind/blind-a-reply.md(加工なし — 道具の返りの字下げだけを外した) | blind/blind-b-reply.md(同左) |

入力の hash は evidence/pre-blind.sha256(盲検の前の commit 54a945270 に在る)。
