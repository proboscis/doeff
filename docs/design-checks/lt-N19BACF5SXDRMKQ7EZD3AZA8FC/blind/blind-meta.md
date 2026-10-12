# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5) | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | Agent tool の引数 `effort: xhigh` を渡した。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話と同じ Pod) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId abffa0d880c757b17 | agentId ae72353c1fd2be19c |
| 起こした刻 | 2026-10-12 00:20 UTC 台(入力を固定した commit aa1125f00 の直後・commit の刻 00:20:41 UTC) | 同じ |
| 所要 | 482 秒(tool の使用 27 回) | 694 秒(tool の使用 34 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文(blind-common.md を読む指示を含む)に、「Python は uv を通す」「最後の返答に 1〜5 を全部書く(実験の file の path も)」の 2 文を足した prompt | blind-b-input.md の本文に同じ 2 文を足した prompt |
| 読ませた版 | `/home/ubuntu/.worktrees/doeff-blind-d4c8d7ce9`(版 d4c8d7ce9 そのもの・`.venv` を作り済み) | 同じ |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げと返し手の枠の行だけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/ln19-blind-a/`・`/tmp/ln19-blind-b/` に置いた(どちらも今回の起動のために名を決めた dir)。
記録へ写したのは返答が名指した file のうち script・出力・差分で、linter と検の集め手に拾われないよう名の末尾に `.txt` を付けた: `a-files/`・`b-files/`。
A の検体は n65 と class65 の tools.hy だけを写した(他の N は `many_tools.py` が作る同じ形)。B の写しの木 `/tmp/ln19-blind-b/tree` と macros.hy の 4 つの版は写していない(差分は `b-files/violation.diff.txt`、S1 の当て方は `b-files/apply_s1.py.txt`)。
読ませた作業木は、返答の後も `git status` が空(設計者が確かめた)。
