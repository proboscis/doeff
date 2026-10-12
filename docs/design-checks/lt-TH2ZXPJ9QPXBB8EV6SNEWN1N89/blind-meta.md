# 盲検 A・B の起動の記録

| 項目 | A | B |
|---|---|---|
| 依頼文 | skill design-change-validation の references/blind-counterexamples.md の「Aへ渡す依頼文」をそのまま + 材料の在処 | 同「Bへ渡す依頼文」をそのまま + 材料の在処 |
| 材料 | `blind-input.md`(sha256 e4d8b4244f02f968385950b1d8d79024f55d4e5435fcf54225ea8249561e3b2e)と、そこから辿る `design-pre-blind.md`(sha256 67b8a588492a36fbd8aacfa260006122d367ad630e54ac383e73e439166475b5)・doeff cc8f9e7df の実コード | 同じ |
| 固定した commit | doeff 7e2493ee6(盲検の前に材料を commit して origin へ送った・2026-10-12 06:12 UTC) | 同じ |
| 要求した model・effort | 1 番目 `gpt-6-astra` / `low` → 起こせない(この環境の Agent tool の model は sonnet・opus・haiku・fable だけ)。2 番目 opus 階層 `claude-opus-5-5` / `xhigh` を要求 | 同じ |
| 起動手段 | Claude Code の Agent tool(subagent_type general-purpose・model=opus・effort=xhigh・同期)。会話の fork・resume は使わない新しい文脈。A と B は同じ message で並べて起こし、互いの返答を見ない | 同じ |
| 機体・profile | agora の cluster の会話 c-1KH799YNWBY5JY4AM7CVB92MRT の Pod(zeus の worker)・会話の profile のまま | 同じ |
| 識別子 | agentId a5bc8ac3caf22b006 | agentId afb9802ade41f2cf0 |
| 観測した model | 未確認(Agent tool は子の実 model を返さない — 要求値 opus と区別する) | 未確認(同じ) |
| effort | 要求 xhigh。子が受けた値は未確認 | 同じ |
| 所要 | 506.6 秒・tool 27 回 | 676.7 秒・tool 55 回 |
| fallback の理由 | 1 番目の model を起こす手段が無い(上の行) | 同じ |
| 渡さなかった物 | 設計者の自己評価・既知の失敗例・試す予定の変異・望む結論・親の会話 | 同じ |
| 返答 | `blind-A-response.md`(加工なし) | `blind-B-response.md`(加工なし) |
