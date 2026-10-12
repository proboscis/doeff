# 盲検 A・B の起動の記録(と、その前の予備の評価 0)

## 盲検 A・B(最終版 91b9b8bbc66d082d8bd357558e312690d1fef7aa に対して)

| 項目 | 盲検 A | 盲検 B |
|---|---|---|
| 入力 | blind-a-input.md(sha256 4c9796d4a4c7bd3f85fce87af2a0f73c485a1157659136711751ebc99165d283)= blind-a-prompt.md(skill の A の依頼文そのまま)+ blind-common.md の `{WORKTREE}` を置き換えた文 | blind-b-input.md(sha256 2b7efd3d4aeb30d8edb068ef55cc30b7f122e01d1e6a3ad2b3c428578a96b56b)= blind-b-prompt.md + blind-common.md |
| 共通の材料・依頼文の hash | blind-common.md 14662e115bf4655ca9de052b4d9d37b6a6932ad8a49c197ab76d0d02b4018780・blind-a-prompt.md e33fbface5debfbc82059d0ce1af67a5d8fa64e3d9fa64c3626301ee627881fa | blind-b-prompt.md 6c32841e8ddd341a4b3059a66c086c41d55d45f9599c6ce2748a7a91346242be |
| 読ませた版 | doeff commit 91b9b8bbc66d082d8bd357558e312690d1fef7aa を detached で切った専用の作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-b7c4`(`make sync` 済み・中の変更は許した) | `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-b7c4`(同左) |
| 返答(未加工) | blind-a-return.md(harness が付けた行頭の字下げだけを外した) | blind-b-return.md(同左) |
| skill の第 1 候補 | gpt-6-astra / effort low — **起こせなかった**: この環境の Agent tool の model は sonnet・opus・haiku・fable だけで、gpt-6-astra を選べない | 同左 |
| 要求した model(代用) | opus(skill の第 2 候補 = tiers.opus = claude-opus-5-5) | 同左 |
| 要求した effort | xhigh(Agent tool の effort の欄で渡した。受け付けられたが、実際に効いた effort は観測できない — 未確認) | 同左 |
| 観測した model | 未確認(返答に model の自己申告は無い。Agent tool への要求は opus) | 同左 |
| 起動の手段 | Agent tool(subagent_type general-purpose)・新しい文脈・会話の fork や resume は使わない・A と B を同じ返事の中で同時に起こし、互いの返答を見せない | 同左 |
| 機体・profile | cluster の worker zeus の Pod(親の会話 c-N8D3D11TB04Y5BH5AE1NRMH2ZD と同じ Pod・同じ資格)。会社 profile の API は使っていない | 同左 |
| 識別子 | agentId a26b9264a73c72f67 | agentId ac434073816d854ee |
| 時間・量 | 1052.3 秒(duration_ms 1052263)・tool 45 回・subagent_tokens 168277 | 1880.9 秒(duration_ms 1880952)・tool 65 回・subagent_tokens 211845 |
| fallback の理由 | 第 1 候補を起こす手段が無い(上)。低い評価や都合の悪い返答を理由に替えていない | 同左 |
| 再試行 | なし | なし |
| 後始末 | 返答に依れば作業木の変更は元へ戻した(git status --short が空)。材料は `/tmp/blind-a-work/` | 同左(材料は `/tmp/ki-b7c4-blindb/`・作業木の `__pycache__` の pyc を `/tmp/ki-b7c4-blindb/moved-out/` へ移した) |

渡さなかった物: 設計者の自己評価、予備の評価 0 の返答と指摘、設計者が既に試した変異とその結果、design-before-blind.md の「予想する限界」、望む結論、親の会話。
注意: 盲検の作業木は commit 91b9b8bbc を切った物で、その commit の message(git log)には予備の評価 0 の指摘の要約が入っている。入力で
「git の履歴は読まない」と指示した。B は「git の履歴は読んでいません」と返答した。A は触れていない(A の返答の失敗例は履歴に無い物 — hy.R による委譲 —
だったので、履歴に引かれた形跡は無い)。

## 予備の評価 0(盲検 A・B の手順に入る前・版 ec89495d4 に対して)

盲検 A・B ではない。1 つの文脈に両方の役(責務の波及する失敗例・検査を通る責務違反)を頼んだ予備の評価で、設計者が自分の見立ての穴を先に塞ぐために立てた。
- 入力: eval0-input.md(設計者が書いた依頼文。要件 R1〜R4 と、探してほしい事を渡した。履歴・自己評価・既知の失敗例は渡していない)。
- 返答(未加工): eval0-return.md。
- 起動: Agent tool(general-purpose)・model opus・effort は指定せず(既定 — 観測不能)・agentId a62c0e15ded8e205a・1567.9 秒・tool 78 回・subagent_tokens 230487。
- この評価が成立させた欠陥 4 つ(入口が `_hy_compiler` の macro を包めない・自己検が今の macro の値を決め打つ・examples/ の下の lib が検の外・
  包む関数が例の path を閉じ込めて bytecode の記録が増える)は直した(commit 91b9b8bbc)。直した後の版に対して、盲検 A・B を新たに立てた。
