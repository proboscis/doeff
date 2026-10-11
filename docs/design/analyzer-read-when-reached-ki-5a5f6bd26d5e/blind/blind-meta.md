# 盲検 A・B の起動の記録

- 入力: `blind-input.md`(sha256 `8c0538029e00acef3abdc2e4cc709708fed38290374a93189e9524a69d12ba97`)と `change.patch`(sha256 `b3129f09728d9b33b5b9502398584bb3fb4ac7d4a599cd230eaee83823db87da` — doeff `915aa1b96..646a3e986` の差分。commit の本文は含めない)。
  依頼文は skill `design-change-validation/references/blind-counterexamples.md` の A・B の文をそのまま使い、その後ろに材料の在り処と、材料の「5. 実験の制限」に従う事(実験は `/tmp/ki5a5f-blind-<A|B>/` の下・git の log と commit の本文と `docs/design/` の下の他の file と agora の card や会話を読まない・analyzer の package の検と自分の script だけ)と、model の名乗りの依頼を足した。
  設計者の自己評価・測りの結果・既知の穴(盲検の前の本文 `../design-before-blind.md` の「限界」の列)は入力に入れていない。
- 起動した機体: この会話の宿(agora の cluster の Pod・Linux・`/home/ubuntu`)。起動口: Claude Code の Agent tool(subagent_type general-purpose)。A・B は同じ返事の中で並べて起動し、互いの返答も親の会話も見ない新しい文脈で走った(fork・resume は使っていない)。profile はこの会話と同じ(子へ資格は渡していない)。
- 起動の開始: 2026-10-11T15:05:45Z(`launch-start.txt`)。

| 順 | 要求した model / effort | 結果 |
| --- | --- | --- |
| 1 | `gpt-6-astra` / `low` | 起動できなかった。この Pod の `codex`(`/memory-scratch/claude-home/bin/codex`・codex-cli 0.162.1)は `codex login status` が `Not logged in` で、この環境は login をしない決まり。Agent tool は codex の model を選べない |
| 2 | opus 段(`claude-opus-5-5`)/ `xhigh` | A・B とも完了。起動口に model `opus`・effort `xhigh` を渡した(起動口が effort を受けたかの観測は無く、実際の effort は未確認)。返答の先頭で両方が「Opus 5.5(claude-opus-5-5)」と自己申告した(実 model の観測は自己申告だけ) |

| 役 | agent id | 所要 | tool 呼び出し | subagent の token |
| --- | --- | --- | --- | --- |
| A | `ae0afb55f8b0dae3a` | 930,324 ms | 45 | 186,137 |
| B | `a9ff8fa154f400a5a` | 655,607 ms | 37 | 170,062 |

- 返答は加工せず `blind-a-return.md`・`blind-b-return.md` に保存した(harness の字下げだけ外した)。
- 盲検が使った script と入力は `../evidence/blind-a-scripts/`・`../evidence/blind-b-scripts/` に写した(元は `/tmp/ki5a5f-blind-A/`・`/tmp/ki5a5f-blind-B/`)。
