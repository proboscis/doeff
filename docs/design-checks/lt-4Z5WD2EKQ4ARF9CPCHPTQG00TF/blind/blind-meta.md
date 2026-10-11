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
| session の識別子 | agentId a2b251ba1649d8bcb | agentId a258839c8c7b1c29d |
| 起こした刻 | 2026-10-11 21:18 UTC 以降(入力の commit 3769c3e85 の直後) | 同じ |
| 所要 | 621 秒(tool の使用 17 回) | 735 秒(tool の使用 36 回) |
| fallback の理由 | 第 1 候補が起こせない(上) | 同じ |
| 渡した入力 | blind-a-input.md の本文そのまま(blind-common.md を読む指示と、「Python は uv を通す」「最後の返答に 1〜5 を全部書く」の 2 文を含む) | blind-b-input.md の本文そのまま(同じ) |
| 入力の hash(sha256) | blind-a-input.md 50c3184c4370fbcd7409487613995d0af2cf36edc060178652653baade67f61e・blind-common.md ab2c14d0e0ada8eb0201856a10c38fcf93452575f8133f1dac78c35fd3716e8d・design-before-blind.md 3f2b0c4eb8ba5ffc1974e5c083ed6c808f0fdd06a3892ed0446ab7861f58e555 | blind-b-input.md 8c40e51c419a609135e9e7f6bd758f47c7b24a4c69cf1f171de70b75e61a83e0・blind-common.md・design-before-blind.md は A と同じ |
| 返答 | blind-a-return.md(未加工・Agent tool の字下げだけ外した) | blind-b-return.md(同) |

## 渡さなかった物

著者の自己評価、実装の時の検の結果(c1〜c4 の赤・別の path の木の緑)、範囲の外の所見、依頼者の検収、親の会話。
blind-common.md は、`docs/design-checks/` の下のこの依頼の外の記録を読まないよう指示した。

## 実験の file

A・B は実験の file を `/tmp/lt4z5w-blind-a/`・`/tmp/lt4z5w-blind-b/` に置いた(どちらも今回の起動のために名を決めた dir)。
記録へ写したのは返答が名指した file で、linter と検の集め手に拾われないよう、名の末尾を `.txt` にした: `a-files/`・`b-files/`。
写しの木(`/tmp/lt4z5w-blind-*/tree`)と A の `baseline/`・`probe/` は写していない。
