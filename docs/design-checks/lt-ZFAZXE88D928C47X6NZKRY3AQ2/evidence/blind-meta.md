# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: この環境の Agent tool が受ける model は sonnet / opus / haiku / fable だけ。codex の CLI(codex-cli 0.162.1)は在るが `codex login status` の答えは「Not logged in」で、この環境は login をしない決まり(資格は起動の時に渡された物だけ) | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値 — tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い。終わりの検査の助言は「この環境の Agent tool は effort を指定できない」と言う) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話と同じ機体)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md)と作業木の BLIND_INPUT.md(= blind-input.md) | 同じ(blind-b-prompt.md。A の返答は渡していない) |
| 渡さなかった物 | 設計者の自己評価・既知の残る穴(着地の報告に書いた範囲の外の所見)・望む結論・design-pre-blind.md。盲検の作業木からは docs/design-checks/lt-ZFAZXE88D928C47X6NZKRY3AQ2 を消した | 同じ |
| 対象の木に含まれた物 | 0d4c5e9f7 の code と検と規則(.semgrep.yaml・検体)。git の歴史と commit の文は読まないよう求めた | 同じ |
| 作業の場所 | 使い捨ての作業木 /work/toolset/worktrees/doeff-wt-blind-a-ki069f(0d4c5e9f7・detached・make sync)・作業 file は /tmp/blind-a-ki069f/ | /work/toolset/worktrees/doeff-wt-blind-b-ki069f・/tmp/blind-b-ki069f/ |
| session の識別子 | agentId a5a7eaa27d415f76a | agentId ad2ba02e9272ddd16 |
| 所要時間 | 1,928,546 ms(道具の呼び 55 回・349,707 token) | 1,407,033 ms(道具の呼び 53 回・310,730 token) |
| 起動の時刻 | 2026-10-12T21:17Z ごろ(A と B を同じ返事で同時に起動) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

入力の hash(起動の前に固定・0d4c5e9f7 に commit 済み):

| file | sha256 |
| --- | --- |
| blind-input.md(作業木の BLIND_INPUT.md と同じ) | 300906b98a0bdcf05377cef20843282bfe5cbb4830c246df215c7f5d0d920c9b |
| blind-a-prompt.md | 6e42c82b764dc9fd1fa885a87eda66bee622fcad1937965b13ed71724fbb05cd |
| blind-b-prompt.md | e72bf2a9cbafd0614e3f37b1bd3396d4828d9f0802139c993306f31922dd8346 |
| design-pre-blind.md(盲検には渡していない) | ec89e3054178aea6f60f382e5e68ee9a10ae000d62b09066e48b66f73ab45621 |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(会話の記録の Agent tool の結果から機械で取り出し、harness が付けた
行頭の字下げ 2 字と、枠の頭の 1 行・末尾の agentId と usage の行だけを外した)。盲検が /tmp に置いた file は blind-a-files/・
blind-b-files/ に写した(検の名の file と script は末尾に .txt を付けた — 設計の記録の dir に検の名の file を置かない決まり。
B の main.modified.hy と orig/main.hy は main.hy.patch と同じ中身なので写していない)。
