# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 要求した model(第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補の結末 | 起動できず: Agent tool が受ける model は sonnet / opus / haiku / fable だけ。機体に codex CLI(0.162.1)は在るが `codex login status` = "Not logged in" で資格が無い(この環境では login しない決まり) | 同じ |
| 実際に要求した model(第 2 候補) | `opus`(Agent tool の値・dotfiles の tiers.opus = claude-opus-5-5)・effort `xhigh`(Agent tool の effort 引数に渡した) | 同じ |
| 観測した model | 未確認(返答に model の自己申告なし。Agent tool の結果も model を名乗らない) | 未確認 |
| effort が効いたか | 未確認(引数は受け付けられたが、実際の effort を観測する手段が無い) | 未確認 |
| 起動手段・機体・profile | Agent tool(subagent_type general-purpose・同期)・agora の会話の Pod(この会話と同じ機体)・この会話の資格のまま(資格や key は渡していない) | 同じ |
| 文脈の分離 | 新しい文脈(fork・resume なし)。親の会話・B の返答は渡していない。渡した物 = 依頼文(blind-a-prompt.md・sha256 5c36d044…)と作業木の BLIND_INPUT.md(= blind-input.md・sha256 f0116d60b5c597ef480d63160abbfd98421747bbcffd52d776e88a08d97456c2) | 同じ(blind-b-prompt.md・sha256 343990bf…。A の返答は渡していない) |
| 渡さなかった物 | 設計者の自己評価・既知の限界(design-pre-blind.md の「限界」の節)・望む結論 | 同じ |
| 作業の場所 | 使い捨ての作業木 /home/ubuntu/.worktrees/doeff-wt-blind-a-2fa4(fe951a108) | /home/ubuntu/.worktrees/doeff-wt-blind-b-2fa4(fe951a108) |
| 作業の場所の外に書いた物 | 親の会話の作業 dir /work/agent-workspaces/a-R6ECWFH17Q0S5X4EKT02N5XNQB の scripts/・wheels/(仮の Hy の wheel)と、uv の cache に仮の wheel `hy-1.3.1+blindframe`。共有の source は書き換えていない | 同じ作業 dir の scratch/ |
| session の識別子 | agentId a176e2f4606243be4 | agentId a3c378c2e9ee0bb43 |
| 所要時間 | 886,412 ms(道具の呼び 30 回) | 1,230,250 ms(道具の呼び 47 回) |
| 起動の時刻 | 2026-10-12T03:2xZ ごろ(A と B を同じ返事で同時に起動・終わりは 03:4xZ) | 同じ |
| fallback の理由 | 第 1 候補を起こす手段がこの環境に無い(評価の高低で替えたのではない) | 同じ |

返答は blind-a-reply.md・blind-b-reply.md に加工せず保存した(harness が付けた行頭の字下げ 2 字だけを外した)。
盲検が作業 dir に置いた file のうち再現に使った物は blind-a-files/・blind-b-files/ に写した(仮の Hy の wheel は 1.3 MB 未満だが、
build_fake_hy.py から作り直せるので写していない — sha256 64ad0cef607932f91760f79efd1e66d4285af3f56b293b950a4dd793c44b7cc1)。
