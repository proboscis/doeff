# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5) | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | Agent tool の引数 `effort: xhigh` を渡した。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話と同じ Pod・worker zeus) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId a5b5d91781f6024a2 | agentId a1f63cec05b2a6b2b |
| 起こした刻 | 2026-10-12 03:04 UTC 台(入力を固定した commit 5ddb4e482 の後・作業木の準備を終えた刻 03:04:07 UTC) | 同じ |
| 所要 | 678 秒(tool の使用 37 回) | 463 秒(tool の使用 33 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文(作業木の path を絶対 path に書き、blind-common.md を読む指示を含む)に、「Python は uv を通す」「最後の返答に 1〜5 を全部書く(実験の file の path も)」の 2 文を足した prompt | blind-b-input.md の本文に同じ 2 文を足した prompt |
| 読ませた版 | `/home/ubuntu/.worktrees/doeff-blind-a-lt0YB1`(版 5ddb4e482 そのもの・A 専用・`.venv` を作り済み) | `/home/ubuntu/.worktrees/doeff-blind-b-lt0YB1`(同じ版・B 専用) |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げと返し手の枠の行だけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/lt0YB1-blind-a/`・`/tmp/lt0YB1-blind-b/` に置いた。記録へ写したのは置き場の file の全部で、
linter と検の集め手に拾われないよう名の末尾に `.txt` を付けた: `a-files/`・`b-files/`。
専用の作業木に A・B が当てた差分は、返答の後に設計者が `git diff` で写した: `a-files/worktree.diff.txt`(macros.hy に defbench・
検の file に検体 1 行と require)・`b-files/worktree.diff.txt`(declarations.hy に索引と登録)。作業木は commit されていない。
