# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 0c257105410374754dae30aded476a14177e66f0bf9345cff2e29bf27393f6be) | [blind-b-input.md](blind-b-input.md)(sha256 7790fdf52cbf57ee404fe2ae65e7053ba098534c10ddb9b861971a81634653ca) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 de5622ef16227956c51881f743fd671bfc5216e7db3268de20ff41b32c4f16ad)と design.md の §1〜§4(sha256 cb01abcc3cb1012f9cac2892826813bf37dc3e8fb690c73525fb30fb9c3d9622) | 同じ + [blind-b-checks.md](blind-b-checks.md)(sha256 d2aa2d9e44ffc5313276aeeb54c6a58f2c36147177899c7c32886480cefe05cf) |
| 材料と依頼文を固定した commit | 8f4e8b692b69be5687c6c96dd52961ea2f5c9fdb(2026-10-11T20:05:52Z に push — 起こす前) | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low` が 401 Unauthorized で落ちた(この Pod に codex の資格が無い — [codex-attempt.txt](codex-attempt.txt)・2026-10-11T20:06:04Z・1 回・rc 1) | 同じ(同じ 1 回の試し) |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 実 model と effort の効き | 観測していない(tool が model の名を返さない・effort が効いたかは見えない)。要求した値だけを記す | 同じ |
| 識別子 | agentId a8a95561d38631041 | agentId abdb118b20ef06709 |
| 時間 | 1,152,990 ms(tool の使用 46 回) | 960,761 ms(tool の使用 67 回) |
| 機体・profile | agora の会話 c-BKEK98T5QAWA57PJWN398PDX1B の Pod(作業の根 a-BKEK98T5QASEYVKTV8D1RTZARV・同じ profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で同時に、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・既知の失敗例・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool の返す字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ) |
| 実験の木 | `/tmp` の木は置き場の規律の hook に止められ、`/work/toolset/worktrees/doeff-wt-blind-a-ex09`(detached・2cff14d6)で行った(返答で申告) | `/work/toolset/worktrees/doeff-wt-blind-b-ex09`(同じ) |

材料の制限: 読む所を design.md の §1〜§4 と実コードに限り、`docs/design-checks/` の下の他の file を読まないように書いた。A は §1〜§4 だけを読んだと返答で申告した。
