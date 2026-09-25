# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 入力 | `blind-a-input.md`(sha256 a2543eb93aa20af207ac745d414aef04098a3b11a117debe9b3fd730b5456c58) | `blind-b-input.md`(sha256 53cabfdc3893307ca51971e89afac3c8fc07a2f9b17232dda456ade129148738) |
| 渡した文(包みの 1 行・逐語) | `次の file を Read で読み、その本文の依頼に従ってください。この依頼の外の文脈はありません: /home/kento/.local/state/doeff/m1-roles-blind/a/blind-a-input.md` | 同じ形で `…/m1-roles-blind/b/blind-b-input.md` |
| 読ませた file | 上の入力の写し(同じ sha256)。作業樹は専用の `~/.worktrees/doeff-wt-639-adr012-blind-a`(b7f836b0 の detached checkout) | 同 `…-blind-b` |
| 返答 | `blind-a-return.md`(返答の本文を加工せず保存 — 道具の枠が足す行頭の 2 字下げだけ除いた) | `blind-b-return.md`(同) |
| 識別子 | Agent tool の agentId a2665bab7e0e91556 | agentId ad763b1869c58ca52 |
| 要求した model・effort | 第 1 候補 gpt-6-astra / low(skill の順)→ 起動口が受け付けず、第 2 候補 opus 段(claude-opus-5-5)/ xhigh | 同 |
| 観測した model | Agent tool の model 引数 `opus` で起こした(claude-opus-5-5 と読める・返答の中の自己申告は無い)。実 model の観測は未確認 | 同 |
| effort | Agent tool は effort を受け付けないので指定できていない(xhigh の要求は未反映・既定の effort) | 同 |
| 機体・profile | agentd-pool-1(pod)・この会話の Claude の資格(個人) | 同 |
| 文脈の共有 | 新しい文脈の subagent(fork・resume なし)。A と B は同じ返事の中で並べて起こし、互いの返答は見ていない。親の会話・自己評価・既知の反例は渡していない | 同 |
| 時刻 | 起こした 2026-09-25T22:03:42Z 以後・所要 1107.9 秒(道具の実測) | 同じ拍に起こした・所要 957.7 秒 |
| fallback の理由 | この pod の `cx`(codex)は「宿名を宣言していない — 会社階級で provider を呼ばない」で起動口が断り、personal の CODEX_HOME も無い(`cx --help` の出力で確かめた)。skill の第 2 候補へ落とした | 同 |
