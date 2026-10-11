# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 4f1462bd0ce35a2ed7ebf3b80924366493ac9c7f5cf30c56039e58103ec07c0a) | [blind-b-input.md](blind-b-input.md)(sha256 a3444f2630420b4829649012b9f9996325840f2b5e8c84d59ee184e2b32a693d) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 2ef1afd7be76731602f6aae055f2e50deed6c7fb2b12a67643a597ed47394ea7)と design.md の §1〜§4(盲検の前の版 sha256 5c3a17786f1a1bc53090d776bd2e05453e541a70057e5ab73a5f3a6e0e335585) | 同じ + [blind-b-checks.md](blind-b-checks.md)(sha256 e5cfd1c5a32c2408baea369a991cef1d626e64d931d51b5fca6ac9ce8350ad6d) |
| 材料と依頼文を固定した commit | 06653af3062071622de76feace74d18510d96cc7(2026-10-11T21:24:55Z に push — 起こす前) | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low --sandbox read-only` が 401 Unauthorized で落ちた(この Pod に codex の資格が無い — [codex-attempt.txt](codex-attempt.txt)・2026-10-11T21:25:00Z〜21:25:21Z・1 回・rc 1・A の依頼文で試した) | A と同じ 1 回の結末から、同じ理由で第 2 候補へ |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model 引数 `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 実 model と effort の効き | 観測していない(tool は model の名を返さず、effort が効いたかも見えない)。要求した値だけを記す | 同じ |
| 識別子 | agentId ad7627ba1da99df5d | agentId a6f7a72c4b07fd24a |
| 時間 | 743,304 ms(tool の使用 41 回) | 1,444,692 ms(tool の使用 61 回) |
| 機体・profile | agora の会話 c-EMMA5DKA98KMJC42EG63ZS8QKS の Pod(作業の根 a-EMMA5DKA98SHR3XZXHKHMJ4TRS・同じ profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で同時に、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・既知の失敗例・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool の返す字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ)。実験の差分と scratch の file は [b-artifacts/](b-artifacts/) に写した |
| 実験の木 | `/home/ubuntu/.worktrees/doeff-wt-blind-A-6ntw`(detached・df9e62756・返答の時点で差分なし) | `/home/ubuntu/.worktrees/doeff-wt-blind-B-6ntw`(detached・df9e62756・機能の差分が未 commit で残る — `b-artifacts/feature.diff`) |

材料の制限: 読む所を design.md の §1〜§4 と実コードに限り、`docs/design-checks/` の下の他の file を読まないように書いた。
B は返答で、design.md の §0 も読んだ事と、grep の出力に他の設計検証の dir の行が数行映った事を申告した(結論には使っていないと申告)。
