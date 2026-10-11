# 盲検 A・B の起動の記録(lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP)

| 項目 | A | B |
| --- | --- | --- |
| 要求した model・effort(第 1 候補) | gpt-6-astra / low | 同左 |
| 第 1 候補の結果 | 使わなかった: この Pod の `codex`(/memory-scratch/claude-home/bin/codex)は `codex login status` が `Not logged in`(資格が無い・2026-10-11T18:5xZ に確かめた)。Agent tool は Claude の model だけを起こせる | 同左 |
| 起こした model・effort(第 2 候補) | Agent tool の `model: opus`・`effort: xhigh` を指定 | 同左 |
| 実際に観測した model | 未確認。subagent の自己申告は「Claude Opus 5.5(claude-opus-5-5)」 | 自己申告は「Claude Opus 5.5(claude-opus-5-5)」 |
| effort の受け付け | Agent tool の引数として渡した。実際に効いたかは観測していない(未確認) | 同左 |
| 1 回目の試み | 2026-10-11T18:53Z 頃に起こした。API の 429(週の上限・model claude-opus-5-5)で途中で切れ、返答なし。途中で mktemp でない /tmp/doeff002-blind-A に probe の file 4 つを作っていた(再試行の前に消した) | 同左(/tmp/doeff002-blind-B は空) |
| 2 回目の試み(採用) | 会話の資格が替わった後、2026-10-11T18:58Z 頃に新しい文脈で起こし直した | 同左 |
| 起動の方法 | Agent tool(subagent_type general-purpose)の新しい文脈。会話の fork・resume は使わない。A と B は 1 つの返事の中で同時に起こし、互いの返答を見ない | 同左 |
| 渡した物 | prompt = 入力の file の path と守る事(読むだけ・mktemp -d の dir の下だけに書く・repo 全体に検査を当てない・他へ連絡しない・設計検証の置き場の他の file を読まない・uv で python を走らせる・model の自己申告を書く)。入力 = blind-a-input.md・blind-common.md | 入力 = blind-b-input.md・blind-common.md |
| 入力の hash(起こす前・18:52:41Z) | blind-common.md 05e9f2f31a4ff1242222dd7e489ba19ec39ae8954a93a527d41ac1ad1334bf6a・blind-a-input.md e3b98a86e087764803b6da91c9014ab6bf50c6d46b79bf1b9ba6bcc42438fc11(commit 6ff7d8f82) | blind-b-input.md 653bb4d18387bb1fc9b309504123708d2905b779ca286a645fec7eb7d80244f3 |
| 機体・profile | agora の会話の Pod(依頼 lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP の会話)・この会話の Claude の資格 | 同左 |
| agent id | a7d2f84c1d1d714db | a957b88c29207ff80 |
| 時間 | 653.8 秒(tool の呼び 45 回) | 446.4 秒(tool の呼び 44 回) |
| fallback の理由 | 第 1 候補の資格が無い | 同左 |
| 返答の保存 | 加工せず blind-a-return.md(harness の行頭の字下げだけ外した) | 同左 blind-b-return.md |
| 読める範囲の外に触れた事(返答の自己申告) | 断面の置き場 ~/.cache/doeff-linter-snapshots の dir の名と input_key を一覧した | 一時の dir /tmp/tmp.lgT0CD25H6/ を再現のために残した |
