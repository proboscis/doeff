# 盲検 A・B の起動の記録

| 項目 | A | B |
|---|---|---|
| 依頼文 | references/blind-counterexamples.md の「Aへ渡す依頼文」をそのまま + 材料の場所 | 同「Bへ渡す依頼文」をそのまま + 材料の場所 |
| 入力 | blind-input.md(sha256 f74ed3b79693deb554692cdb711fee69733acf48f31f51a57ff9ca04e934ce9a)と、読むだけの木 doeff 8a759a421 | 同じ |
| 要求した model | opus(Agent tool の model=opus → Opus 5.5) | 同じ |
| 要求した effort | xhigh(Agent tool の effort 引数で指定) | 同じ |
| 観測した model | 未確認(子の自己申告も取っていない) | 未確認 |
| 起動手段 | Claude Code の Agent tool(subagent_type=general-purpose)・同期・新しい文脈(fork・resume なし)・A と B は 1 つの返事の中で同時に起動し、互いの返答を見ない | 同じ |
| 識別子 | agentId a4de92f8813a0fe89 | agentId ad88d9c0f715d4469 |
| 機体・profile | agora の会話 c-4J8GWQ55ER3HK35P4XN7V8HPGW の Pod(cluster の隔離された環境)・会話の資格のまま | 同じ |
| 所要 | 739694 ms・道具 42 回 | 945209 ms・道具 70 回 |
| 第 1 候補を使わなかった訳 | gpt-6-astra はこの環境の Agent tool が起こせる model(sonnet / opus / haiku / fable)に無い | 同じ |
| 渡さなかった物 | 設計者の自己評価・強制方法の表の「限界」の列・既知の失敗例・親の会話 | 同じ |
| 返答 | blind-a-response.md(加工なし) | blind-b-response.md(加工なし) |
