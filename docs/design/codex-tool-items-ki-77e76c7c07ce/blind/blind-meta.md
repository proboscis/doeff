# 盲検 A・B の起動の記録

- 入力: `blind-input.md`(sha256 `d27c493d51e43bc0eba4c87a91ce914284530c448fb7f823912e6787d3287de6`)と `change.patch`(sha256
  `b3c622d89856b4fb7a0d5d62fa3b038fded21db9317c7ff1b9bfeea508a4472a` — doeff `fca21e823..af7bd4857` の差分。commit の本文は含めない)。
  hash は `blind-input.sha256`。依頼文は skill `design-change-validation/references/blind-counterexamples.md` の A・B の文をそのまま
  使い、その後ろに材料の在り処(この 2 file だけ)と、材料の「5. 実験の制限」に従う事と、model の名乗りの依頼を足した。
- 入れなかった物: 設計者の自己評価・強制の手段の限界の列(`../design-before-blind.md` の表の「限界」)・既知の揺れ(利用枠切れの検)・
  親の会話。
- 起動した機体: この会話の宿(agora の cluster の Pod・Linux・`/home/ubuntu`)。起動口: Claude Code の Agent tool(subagent_type
  general-purpose)。A・B は同じ返事の中で並べて起動し、互いの返答も親の会話も見ない新しい文脈で走った(fork・resume は使っていない)。
  profile はこの会話と同じ(子へ資格は渡していない)。
- 起動の開始: 2026-10-11T16:31:28Z(`launch-start.txt`)。

| 順 | 要求した model / effort | 結果 |
|---|---|---|
| 1 | `gpt-6-astra` / `low` | 起動できなかった。この Pod の `codex`(codex-cli 0.162.1)は `codex login status` が `Not logged in` で、この環境は login をしない決まり。Agent tool は codex の model を選べない |
| 2 | opus 段(`claude-opus-5-5`)/ `xhigh` | A・B とも完了。起動口に model `opus`・effort `xhigh` を渡した(起動口が effort を受けたかの観測は無く、実際の effort は未確認)。返答の先頭で両方が「Opus 5.5(claude-opus-5-5)」と自己申告した(実 model の観測は自己申告だけ) |

| 役 | agent id | 所要 | tool 呼び出し | subagent の token |
|---|---|---|---|---|
| A | `a5744a0a5096bee9f` | 1,316,457 ms | 74 | 257,620 |
| B | `adbf8608a0bd38f57` | 998,646 ms | 74 | 207,129 |

- 返答は加工せず `blind-a-return.md`・`blind-b-return.md` に保存した(harness の字下げだけ外した)。
- 盲検が残した差分・録った行・script は `../evidence/blind-a/`・`../evidence/blind-b/` に写した(元は `/tmp/ki77e7-blind-A/saved`・
  `rec-bg`・`rec-slow`・`/tmp/ki77e7-blind-B/`)。Python の script は repo の lint の対象に入れないため、中身を変えずに名を `.py.txt` に
  した。
- 制限からの逸脱(盲検の自己申告): A・B とも `/tmp` の下への `git worktree add` は機体の検査に止められた。A は
  `/work/toolset/worktrees/doeff-wt-ki77e7-blind-A`(detached・af7bd4857・変更を戻した clean な状態)に作り、削除も検査に止められて
  残した(回収は `ai worktree reap` に任せる)。B は `/tmp` の下に完全な local clone を作り、終わった後に消した。A は材料に無い実験として
  本物の codex の binary を走らせた(schema の出力と録り — 出力は `/tmp/ki77e7-blind-A` の下)。
