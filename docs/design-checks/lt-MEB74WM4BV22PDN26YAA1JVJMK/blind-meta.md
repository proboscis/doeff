# 盲検 A・B の起こし方の記録

| 項目 | A | B |
|---|---|---|
| 要求した model・effort(skill の優先順 1) | `gpt-6-astra`・`low` | `gpt-6-astra`・`low` |
| 優先順 1 を使わなかった訳 | この会話(agora の cluster の Pod・Claude Code)の Agent tool が起こせるのは Claude の model だけで、`gpt-6-astra` を起こす手段が無い | 同じ |
| 実際に頼んだ model・effort(優先順 2) | Agent tool の `model=opus`(Opus 5.5)・`effort=xhigh` | 同じ |
| 観測した model | 未確認(子の返答に model の自己申告は無い。Agent tool の要求値だけ) | 未確認(同じ) |
| 起こし方 | Agent tool・subagent_type `general-purpose`・新しい文脈(fork・resume なし)・A と B を同じ返事の中で同時に起こし、互いの返答を見せない | 同じ |
| 機体・profile | agora の cluster の Pod(会話 c-2AVPGRVP07N3MJ59B23MA57R43 の Pod)・会話と同じ口座 | 同じ |
| 識別子 | agentId `a193964282327e10a` | agentId `a80de2aa20ab6354c` |
| 所要 | 401 秒(tool の呼び 31 回) | 2896 秒(tool の呼び 66 回) |
| 渡した入力 | `blind-input.md`(sha256 6f54c259a42c712da28f223e16b2925398297235541209ea9f902a0383356a0d・commit f8356ec98)と、作業木 `/home/ubuntu/.worktrees/doeff-wt-ki-b884-design-check`(doeff main d6ebc3db9)の読み取り | 同じ |
| 渡さなかった物 | `design-pre-blind.md`(強制方法の限界を含む)・設計者の自己評価・この会話の履歴 | 同じ |
| 依頼文 | `references/blind-counterexamples.md` の「A へ渡す依頼文」に、材料の path と読み取りだけの約束を足した物 | 同じく「B へ渡す依頼文」 |
| fallback・再試行 | なし | なし |
| 返答 | `blind-A-response.md`(加工なし) | `blind-B-response.md`(加工なし) |
| 子が残した物 | `/tmp/blind-a.WnD0`(再現用 — probe は `evidence/blind-A-marked_root_probe.py.txt` に写した) | `/tmp/blind-b.5Kq7`(差分は `evidence/blind-B-change.diff.txt`・probe は `evidence/blind-B-probe.hy.txt` に写した) |
