# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段(codex の CLI)が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5)・effort `xhigh` を指定 | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | `xhigh` を Agent tool の引数で渡した。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話 c-NGKHQ411KPEGNX0GF9PR5KYN9J と同じ Pod) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId a48878fede6dfcdbc | agentId a3b58fc6e4e5d3f33 |
| 起こした刻 | 2026-10-11 19:56:36 UTC 以降(入力の commit の直後) | 同じ |
| 所要 | 386 秒(tool の使用 22 回) | 893 秒(tool の使用 34 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md(blind-common.md を読む指示を含む) | blind-b-input.md(同) |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げだけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/blind-a/`・`/tmp/blind-b/` に置いた。この 2 つの dir は機体の共有の /tmp で、他の会話の file も在った
(例 `/tmp/blind-a/probe_store_password.py`・`/tmp/blind-b/mirror/…` — 刻が今回の起動より前で、返答も名指していない)。
記録へ写したのは、返答が名指した file で、刻が起動の後(20:00〜20:10 UTC)の物だけ: `a-files/`・`b-files/`。
