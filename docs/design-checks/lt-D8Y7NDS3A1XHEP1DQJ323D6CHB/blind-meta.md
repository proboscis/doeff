# 盲検 A・B の起動の記録

| 欄 | A(変更の波及) | B(検を通る責務違反) |
|---|---|---|
| 要求した model・effort(skill の優先 1) | `gpt-6-astra`・`low` | 同左 |
| 優先 1 を使わなかった訳 | この会話(agora の headless の会話・Claude Code)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす口が無い(試行 0 回 — 起動の手段が無い) | 同左 |
| 代わりに起こした model・effort(skill の優先 2) | Agent tool `model: opus`(Opus 5.5)・`effort: xhigh` を引数で渡した | 同左 |
| 観測した model | 未確認(返答に model の自己申告は無い。Agent tool の引数の値だけを記す) | 同左 |
| effort が効いたか | 未確認(子の側で効いた値を観測する口が無い) | 同左 |
| 起動の手段 | Agent tool・subagent_type `general-purpose`・同期(新しい文脈 — 会話の履歴の fork・resume を使わない)。A と B は 1 つの返事の中に並べて同時に起こし、互いの返答を見せていない | 同左 |
| 機体・profile | 親の会話 c-GNREBBW4Q45RWFTFXDK2D4B0EJ の Pod・親の資格のまま(資格を子へ別に渡していない) | 同左 |
| agentId | a9ddf6eebac992960 | a00d7c1fba812c4b3 |
| 所要 | 1090908 ms(tool の使用 47 回) | 1749777 ms(tool の使用 55 回) |
| 起動の刻 | 2026-10-12 17:5x JST(入力の blind-input.md を書いた直後) | 同左 |
| 渡した入力 | prompt = skill の references/blind-counterexamples.md の「A へ渡す依頼文」をそのまま + 材料の path(blind-input.md・sha256 e6895c91ab91efbaf472b0321225e843d3c503f2548988a90843aaa93a7f88ad)+ 作業の決まり(共有 file を書かない・git の書く操作をしない・一時 file は mktemp -d・uv の python・pytest は 1 本ずつ・kubectl/agora/ai と他の会話への連絡を使わない) | 同じ形で「B へ渡す依頼文」。差分を試すなら作業木を mktemp -d へ写す手順を添えた |
| 渡していない物 | 設計者の自己評価・強制方法の表の「限界」の列・既知の弱み・望む結論・親の会話 | 同左 |
| fallback・再試行 | なし(1 回で検証できる失敗例が返った) | 同左 |

返答は blind-A-response.md・blind-B-response.md に加工せずに置いた(Agent tool が返した本文から、harness の字下げだけを外した)。
blind-input.md は design-pre-blind.md から、完了の範囲の 1 行と強制方法の表の「限界」の列を外し、読んでよい実コードの path と検査の実行方法を足した物。
