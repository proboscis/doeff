# 盲検 A・B の起動の記録

- 入力: `blind-input.md`(sha256 `7ef7578f91e3ecd05489562db9aa89fddf574c935e674dbc4f8aa3b77bf79be6`)と、
  worktree `/home/kento/.worktrees/doeff-wt-ki-b02c57db9de8-design` に当てた提案の差分
  (`../evidence/FINAL-tracked.patch`・sha256 `506c6e5e8cd15860283e4eb72920e97797c033815d3fb92ec0a38068fd9aefbe`)。
  依頼文は skill `design-change-validation/references/blind-counterexamples.md` の A・B の文をそのまま使い、
  その後ろに材料の在り処・読んではいけない dir・実験の場所の制限・model の名乗りの依頼を足した。
  設計者の自己評価・実験の結果・既知の穴(関数単位の固定の割れ・doeff-hy の値の漏れの実測)は入力に入れていない。
- 起動した機体: この会話の宿(Linux・`/home/kento`)。起動口: Claude Code の Agent tool(subagent_type
  general-purpose)。A・B は同じ返事の中で並べて起動し、互いの返答も親の会話も見ない新しい文脈で走った
  (fork・resume は使っていない)。

| 順 | 要求した model / effort | 結果 |
| --- | --- | --- |
| 1 | `gpt-6-astra` / `low` | 起動できなかった。この pod の `cx` は「この機体は宿名を宣言していない — 会社階級 'cyberagent' で provider を呼ばない」と断る(会社の資格の境界の門)。Agent tool は codex の model を選べない |
| 2 | `claude-fable-5-1` / `xhigh` | A・B とも 2026-09-23T20:11:30Z 頃に API の上限で途中終了(`You're out of usage credits` HTTP 429・request id `req_011CfLyp19xJ91XEmcRznQSV`(A)・`req_011CfLypVCutZ6mgBxMWQRaL`(B))。返答なし |
| 3 | opus 段(dotfiles `agent/route/config.json` の `tiers.opus` = `claude-opus-5-5`)/ `xhigh` | A・B とも完了。**effort は起動口に指定の欄が無く、指定できなかった**(実際の effort は未確認)。model は起動口に `opus` を渡し、返答の先頭で両方が「Opus 5.5」と自己申告した(実 model の観測は自己申告だけ) |

| 役 | agent id | 所要 | tool 呼び出し | subagent の token |
| --- | --- | --- | --- | --- |
| A | `ac99a6619c1065cd9` | 690,409 ms | 53 | 127,220 |
| B | `a454f7e4793a8b240` | 633,609 ms | 38 | 122,438 |

- 起動の開始: 2026-09-23T20:11:55Z(opus 段)。profile: この会話と同じ(子へ資格は渡していない)。
- 返答は加工せず `blind-a-return.md`・`blind-b-return.md` に保存した(harness の字下げだけ外した)。
