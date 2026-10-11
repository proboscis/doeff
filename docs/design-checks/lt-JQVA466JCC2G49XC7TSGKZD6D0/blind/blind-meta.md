# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5) | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | Agent tool の引数 `effort: xhigh` を渡した。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話 c-Y5P7KVN6T97TTWKY40S4E3ERDH と同じ Pod) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId a1e3f1ec6644bc942 | agentId a80f52aae814c923d |
| 起こした刻 | 2026-10-11 20:35 UTC 以降(入力の commit 7548af7da の直後) | 同じ |
| 所要 | 552 秒(tool の使用 27 回) | 501 秒(tool の使用 39 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文(blind-common.md を読む指示を含む)に、「Python は uv を通す」「最後の返答に 1〜5 を全部書く」の 2 文を足した prompt | blind-b-input.md の本文に同じ 2 文を足した prompt |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げだけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/ki043e-blind-a/`・`/tmp/ki043e-blind-b/` に置いた(どちらも今回の起動のために名を決めた dir で、他の会話の file は無い)。
記録へ写したのは返答が名指した file で、linter と検の集め手に拾われないよう名の末尾に `.txt` を付けた: `a-files/`・`b-files/`。
B が検を走らせるために作った写しの木 `/tmp/ki043e-blind-b/tree`(`git archive HEAD` の写し)は写していない。
