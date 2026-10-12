# 盲検 A・B の起動の記録(設計検証 lt-W8F3HD41FWBR4PS26EDSEJDJMT)

| 項目 | A | B |
|---|---|---|
| 要求した model・effort | 第 1 候補 gpt-6-astra・low → 起こせず、第 2 候補 opus(Opus 5.5)・xhigh | 同じ |
| 第 1 候補を使わなかった訳 | この環境の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra は起こす手段が無い | 同じ |
| 起動手段 | Claude Code の Agent tool(subagent_type general-purpose・model opus・effort xhigh・前景)。会話の fork・resume を使わない新しい文脈。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 観測した model | 未確認(subagent の実際の model を tool の答えから読めない。要求値は opus) | 同じ |
| 観測した effort | 未確認(要求値 xhigh を tool が受けたかを読めない) | 同じ |
| 機体・profile | agora の cluster の会話の Pod(親と同じ Pod・同じ profile の資格) | 同じ |
| 識別子 | agentId a05dff9bcf0cc95a2 | agentId ab08f7b0eefd18faf |
| 所要 | 478.8 秒・tool の呼び 25 回 | 924.4 秒・tool の呼び 66 回 |
| 入力 | blind-input.md(sha256 c74f792e447043dbd3e1912a83c225453fcb1f49f2ef01e199e2c2839b223120)と、そこから指す design-pre-blind.md(sha256 1b116db1f5613325265f4f683ad3c918f42d95399b5748189ea01ca17950f81e)・実コード(c8fd2c26a)。依頼文は skill の A・B の文のまま。2026-10-12T09:25:20Z に入力を固定した後に起こした | 同じ |
| 渡さなかった物 | 設計者の自己評価・検の結果・既知の失敗例・親の会話 | 同じ |
| 返答 | blind-A-response.md(加工なし) | blind-B-response.md(加工なし。返答の中の patch の path は依頼側が evidence/blind-B-violation.patch.txt へ移した) |
| fallback・再試行 | 無し | 無し |
