# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: この環境の Agent tool が受ける model は sonnet / opus / haiku / fable だけ(codex の資格も無く、login しない決まり) | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値・tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話 c-23BFZS4N25RQD7J19THT3D8NCA と同じ機体)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md)と作業木の BLIND_INPUT.md(= blind-input.md) | 同じ(blind-b-prompt.md。A の返答は渡していない) |
| 渡さなかった物 | 設計者の自己評価・実装の前の構成のレビューの所見(README.md の節)・望む結論。盲検の作業木からは docs/design-checks/lt-07HRM3MVKZXK2MYM4DEQ872YC1 を消した | 同じ |
| 対象の木に含まれた物 | a4abf5218 の code と検・ADR-DOE-HY-008 の R7(書き足した文)・records.py の注釈。git の歴史と commit の文は読まないよう求めた | 同じ |
| 作業の場所 | 使い捨ての作業木 /work/toolset/worktrees/doeff-wt-blind-a-lt07hr(a4abf5218・detached・make sync)・作業 file は /tmp/blind-a-lt07hr/ | /work/toolset/worktrees/doeff-wt-blind-b-lt07hr・/tmp/blind-b-lt07hr/ |
| session の識別子 | agentId ab18cbef3a4d2136e | agentId ab92531ee71023337 |
| 所要時間 | 566,859 ms(道具の呼び 38 回) | 1,240,815 ms(道具の呼び 48 回) |
| 起動の時刻 | 2026-10-12T10:40Z ごろ(A と B を同じ返事で同時に起動) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(harness が付けた行頭の字下げ 2 字だけを外した)。
盲検が /tmp に置いた script・差分・log は blind-a-files/・blind-b-files/ に写した(使い手の pkg の一時の木は写していない)。
