# 盲検 A・B の起動の記録

| 欄 | A | B |
|---|---|---|
| 要求した model(skill の第 1 候補) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 第 1 候補を起こせなかった理由 | この環境(agora の会話の Pod)の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、gpt-6-astra を起こす手段が無い | 同じ |
| 代わりに起こした model(skill の第 2 候補) | Agent tool の model `opus`(dotfiles の tiers.opus = claude-opus-5-5) | 同じ |
| 観測した model | 未確認(Agent tool の返答は model の名を返さない。subagent の自己申告も取っていない) | 同じ |
| effort | Agent tool の引数 `effort: xhigh` を渡した(skill の第 2 候補の指定)。実際に効いた effort は未確認 | 同じ |
| 起動の手段 | Agent tool・subagent_type general-purpose・新しい文脈(会話の fork・resume ではない)。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体 | agora の cluster の会話の Pod(親の会話と同じ Pod) | 同じ |
| profile | 親の会話の資格のまま(子へ API key を渡していない) | 同じ |
| session の識別子 | agentId a9a51dbaee9eb7b64 | agentId a7f436cff44ee78f6 |
| 起こした刻 | 2026-10-12 00:27 UTC 以降(入力の commit 3c95fa479 の直後) | 同じ |
| 所要 | 665 秒(tool の使用 40 回) | 776 秒(tool の使用 41 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文そのまま | blind-b-input.md の本文そのまま |
| 入力の hash(sha256) | blind-a-input.md 4099bd5cafb766711c3dedcb465ed75ccb51b33696c8f3e0bffc446c89567446・blind-common.md 65c74d8937b063b68754ee226cba57313857074772df3e225bb72931d36db179・design-before-blind.md 58c3c56aad1df88f1dbb342f05005299cf4089172366df02159753c66b3e1342 | blind-b-input.md 17a0e6929976b4827b66495dc513a6c665ad40c623244f66ccdfb62fe334d58d・blind-common.md・design-before-blind.md は A と同じ |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げだけ外した) | blind-b-return.md(同) |

## 渡さなかった物

著者の自己評価、実装の時の変異の結果(M1〜M11 の赤)、card の発言、依頼者の所見の後の議論、親の会話。
blind-common.md は、`docs/design-checks/` の下のこの依頼の外の記録と git log の本文を読まないよう指示した。

## 実験の file

blind-common.md は実験の木を `/tmp/ki-f8a5-blind-<a|b>/tree` に切るよう書いたが、機体の hook(worktree-placement-guard)が止めたので、
A・B とも `/work/toolset/worktrees/doeff-wt-ki-f8a5-blind-<a|b>`(86ecd1344 の detached)に切った(返答の §4・§5)。
記録へ写したのは返答が名指した file で、linter と検の集め手に拾われないよう、名の末尾を `.txt` にした: `a-files/`・`b-files/`。
