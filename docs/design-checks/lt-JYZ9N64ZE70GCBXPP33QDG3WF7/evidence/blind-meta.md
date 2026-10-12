# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: この環境の Agent tool が受ける model は sonnet / opus / haiku / fable だけ(この会話には codex の資格も無く、login しない決まり) | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値・dotfiles の tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話 c-PX52E1C685SM9V7R2V7NRZFVXD と同じ機体)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md・sha256 47c7591a64db8119cc053b80b2c8491b6b9e4a9946a0b941a4aeab8ab1b0bca0)と作業木の BLIND_INPUT.md(= blind-input.md・sha256 d8eff69bd712ab1769dc2911e59290087a0530fe20fa99c443a68f5f68e98eef) | 同じ(blind-b-prompt.md・sha256 f7447143ca4799d3cc41cc0039d7625b2548ced0d1651c330ccea68e75fce30b。A の返答は渡していない) |
| 渡さなかった物 | 設計者の自己評価・既知の限界(design-pre-blind.md の「限界」の節)・望む結論 | 同じ |
| 対象の木に含まれた物 | 0de52de26 の ADR-DOE-HY-008 の事実(直す前の 5 つの失敗ケースの実測 — 対象の設計記録そのもの)と commit の文。依頼文で git の歴史と commit の文は読まないよう求めた | 同じ |
| 作業の場所 | 使い捨ての作業木 /work/toolset/worktrees/doeff-wt-blind-a-eafa(0de52de26・venv は make sync で作った) | /work/toolset/worktrees/doeff-wt-blind-b-eafa(0de52de26) |
| 作業の場所の外に書いた物 | 親の会話の作業 dir /work/agent-workspaces/a-PX52E1C685CV4A7JJJC2F3A6GD/blind-a/(scripts・仮の Hy の wheel・log)。作業木の中に pytest の .pyc と .pytest_cache が残った(hook が消すのを止めた)。共有の source は書き換えていない(sed で戻し、`git diff` は空と申告) | 同じ作業 dir の blind-b/。作業木の差分は `git apply -R` で戻し、`git status` は BLIND_INPUT.md だけと申告 |
| session の識別子 | agentId a570635453d193d26 | agentId a311440dace9d4bff |
| 所要時間 | 832,776 ms(道具の呼び 44 回) | 1,489,907 ms(道具の呼び 59 回) |
| 起動の時刻 | 2026-10-12T07:36Z ごろ(A と B を同じ返事で同時に起動) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(harness が付けた行頭の字下げ 2 字だけを外した)。
盲検が作業 dir に置いた file のうち script・差分・log は blind-a-files/・blind-b-files/ に写した。仮の Hy の wheel は写していない
(作り手の script が在る。sha256: B の local-state 4cdc8c0ec45762dd066a451cfd686841ebcd5c4f1efff92f8f97753934d388ea・plain
678e3b689dd31d694fb2f2a91af264e8cd03ed39aa39948523e5a67b89ebb5ea)。
