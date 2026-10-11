# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 402f5aa88450a4c55a41532ffc3b53acda9c6a805064f24eca0244ea5ce48c22) | [blind-b-input.md](blind-b-input.md)(sha256 06511c1736a5c9dc27f1857a9b449c1c13bd2356416299a333567a720825cefd) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 c3c72bca4d9ce7a6e98ef6f4256f5820f281750d2fd43e3cd54d619ccbb72ab6) | 同じ + [blind-b-checks.md](blind-b-checks.md)(sha256 e2261088f03f360482cb1b2c3e4f1dede394995f1b2ccea3cd31d0707e67ae2c) |
| 材料と依頼文を固定した commit | 7e2aea54a(2026-10-11T18:24Z に push — 起こす前) | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | codex exec が 401 Unauthorized で落ちた(この Pod に codex の資格が無い — [codex-attempt.txt](codex-attempt.txt)・2026-10-11T18:20:32Z・1 回) | 同じ(同じ 1 回の試し) |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 実 model と effort の効き | 観測していない(tool が model の名を返さない・effort が効いたかは見えない) | 同じ |
| 識別子 | agentId a0c3a8c1dd373e392 | agentId a94c3a3bfbf3968eb |
| 時間 | 1,082,811 ms(tool の使用 29 回) | 1,353,313 ms(tool の使用 40 回) |
| 機体・profile | zeus の Pod(agora の会話 a-P3XK61KTD6P4A1FJZ3Y6RKN9QY の Pod・同じ profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で同時に、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・既知の失敗例・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool の返す字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ) |
| probe | /tmp/blind-A/s1probe/ の script を [../evidence/blind-probes/a/](../evidence/blind-probes/a/) に写した | /tmp/blind-B/ のうちこの検証の file(loader_hooks.diff・probe_store.py・tools/apply_diff.py)を [../evidence/blind-probes/b/](../evidence/blind-probes/b/) に写した。/tmp/blind-B は別の会話の盲検と共用の dir だった(B が返答で申告) |

材料の制限: 読む所を doeff-hy の照らしの source と検に限り、`docs/design-checks/` の下(この検証と別の検証の記録)を読まないように書いた。
B は返答で、指定の外の `code_store.py` を読んだと申告した(置き場の鍵の作り方を知るため)。
