# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5) | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | Agent tool の引数 `effort: xhigh` を渡した。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話 c-5W1G85A5FKZJ4GEJ7AXJD5QT77 と同じ Pod) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId a7b9cde043dc6356e | agentId af7ea2885adc74799 |
| 起こした刻 | 2026-10-11 23:25:33 UTC(入力を固定した commit 9d1e02a36 の直後) | 同じ |
| 所要 | 675 秒(tool の使用 33 回) | 676 秒(tool の使用 41 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文(blind-common.md を読む指示を含む・sha256 61bf540f…)に、「Python は uv を通す」「最後の返答に 1〜5 を全部書く(実験の file の path も)」の 2 文を足した prompt | blind-b-input.md の本文(sha256 f2e48a91…)に同じ 2 文を足した prompt |
| 読ませた版 | `/home/ubuntu/.worktrees/doeff-blind-3293bc4cc`(版 3293bc4cc そのもの・`.venv` を作り済み) | 同じ |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げだけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/kie3a9-blind-a/`・`/tmp/kie3a9-blind-b/` に置いた(どちらも今回の起動のために名を決めた dir で、他の会話の file は無い)。
記録へ写したのは返答が名指した file で、linter と検の集め手に拾われないよう名の末尾に `.txt` を付けた: `a-files/`・`b-files/`。
B が作った写しの木 `/tmp/kie3a9-blind-b/tree`(`git archive HEAD` の写しに `failure_budget.diff` を当てた物)は写していない(差分は `b-files/failure_budget.diff.txt`)。
A・B とも、読ませた作業木を変えていない(どちらの返答も `git status` が空と申告し、設計者の再現の時も変更 0 行 — `evidence/a/A-repro-before.txt` の頭)。
