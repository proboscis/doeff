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
| session の識別子 | agentId a18f0c67c5a3f99b4 | agentId a0947cbdcbe6de3e8 |
| 起こした刻 | 2026-10-12T05:29:10Z(入力を固定した commit f95de2618 の直後・commit の刻 05:28:52 UTC) | 同じ |
| 所要 | 907 秒(tool の使用 50 回) | 1016 秒(tool の使用 54 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md を読む指示(blind-common.md を読む指示を含む)と、守る事(読む木を変えない・/tmp/ki973b-blind-a の下だけで試す・uv を通す・全数の検をしない・他へ書かない)・「最後の返答に 1〜5 を全部書く」 | blind-b-input.md を読む指示と、同じ守る事(置き場は /tmp/ki973b-blind-b) |
| 読ませた版 | `/home/ubuntu/.worktrees/doeff-blind-ki973b-d8db309`(版 d8db309f3 そのもの・`.venv` を作り済み) | 同じ |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げと返し手の枠の行だけ外した) | blind-b-return.md(同) |

## 実験の file

A・B は実験の file を `/tmp/ki973b-blind-a/`・`/tmp/ki973b-blind-b/` に置いた(どちらも今回の起動のために名を決めた dir)。
記録へ写したのは返答が名指した file のうち script・検体・差分で、linter と検の集め手に拾われないよう、元の path の `/` を `_` にし、名の末尾に `.txt` を付けた: `a-files/`・`b-files/`。
写しの木(`tree/`)と、A の `orig/`・`patched/`・`patched_e2/`(macros.hy などの元と変更後の丸ごとの写し)は写していない(差分は `a-files/patches_*.diff.txt`)。
読ませた作業木は、返答の後も `git status --short` が空で HEAD は d8db309f3 のまま(設計者が確かめた)。B は、写しの `.git` を退かす前に写しの中で `git status` を 1 回走らせ、読む木の worktree の index の stat の記録を書き換えたと申告している(中身は変わっていない)。
