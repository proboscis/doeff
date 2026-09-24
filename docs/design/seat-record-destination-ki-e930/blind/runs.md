# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model・effort | 優先 1 `gpt-6-astra` / `low` → **起動口が無い**ので優先 2 へ | 同じ |
| fallback の理由 | この pod(`agentd-pool-0`)の PATH に `codex` 本体が無い(`which codex` で見つからず、`cx` の wrapper だけが在る)。codex の起動口が無いので 1 度も撃てない | 同じ |
| 実際の起動口 | Claude Code の Agent tool・`subagent_type=general-purpose`・`model=opus`(= Opus 5.5 `claude-opus-5-5`) | 同じ |
| effort | Agent tool は effort の引数を受けない。要求は `xhigh` だが**実際の effort は未確認**(起動口の既定) | 同じ |
| 実際の model の観測 | 起動口の応答に model 名は出ない。要求値 `opus` と区別して**未確認** | 同じ |
| 機体・profile | pool pod `agentd-pool-0`・この会話の口座(personal) | 同じ |
| session 識別子 | agentId `af454b99b590b9397` | agentId `acd3dd9aedb901f26` |
| 文脈の独立 | 新しい文脈(fork / resume なし)。親会話・設計本文・著者の評価は渡さず、`blind/input-common.md` だけを入力にし、同じ dir の他の file を読まないよう指示した。A と B は同じ応答の中で並べて起動し、互いの返答を見ていない | 同じ |
| 起動 | 2026-09-24T15:52Z ごろ | 同じ |
| 所要 | 605.7 秒・tool 44 回 | 964.1 秒・tool 49 回 |
| 入力 | `blind/input-common.md`(sha256 `876ab0c11ee2cfb41966077f94d6a5cd49a2d41f7e333545c7a3b48022c6416d`)+ A / B の定型の依頼文(skill の `references/blind-counterexamples.md` の逐語)+ 読むだけの制約 | 同じ |
| 返答 | `blind/A-raw.md`(harness の字下げを外しただけ・本文は無加工) | `blind/B-raw.md`(同じ)。B が /tmp に置いた差分と witness は `counterexamples/B_counterexample.diff`・`counterexamples/B_witness_continue.py` に写した |
