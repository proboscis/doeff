# 盲検 A・B の起動の記録

| 項目 | A | B |
|---|---|---|
| 要求した model の 1 番目 | gpt-6-astra・effort low(codex exec) | 同左 |
| 1 番目の結末 | 起こせない — `codex exec -m gpt-6-astra -c model_reasoning_effort=low` が 401 Unauthorized(evidence/blind-model-probe-gpt-6-astra.log・2026-10-12T04:4xZ) | 同左(1 回の試しを両方に当てた) |
| 代わりに起こした model | Opus 5.5(Agent tool の model=opus — operator の決定 2026-09-24 で代わりは全部 Opus 5.5・skill の表の 2 番目) | 同左 |
| 観測した model | 未確認(Agent tool は返りに model の名を出さない。自己申告も求めていない) | 同左 |
| effort | Agent tool の effort=xhigh を指定した。実際に効いた effort は未確認 | 同左 |
| 起動の手段 | Agent tool(subagent_type=general-purpose)— 新しい文脈。親の会話の fork・resume ではない | 同左 |
| A と B の独立 | 同じ返事の中で 2 つ同時に起こし、互いの返答を渡していない | 同左 |
| 機体・profile | zeus の agora の会話 c-RVHCXR3QG19HDMN7RP6A2M3TM1 の Pod(会話の資格のまま。API key を渡していない) | 同左 |
| agent の id | ad2df8f45a956df35 | a9c1c33c58b448613 |
| 起こした時刻 | 2026-10-12T04:53:14Z の直後 | 同左 |
| 所要時間 | 607,971 ms | 721,010 ms |
| 道具の使用 | 17 回 | 12 回 |
| 渡した入力 | /tmp/doeff-blind-input-lt-F2VN.md(= blind-input.md・sha256 e3b64f20cc20a6c66946630ac032c0bb366fd0721504ef9084f021dfea5d0804)と /tmp/doeff-blind-c854bccaf/(git archive c854bccaf — この記録の dir を含まない) | 同左 |
| 依頼文 | skill の references/blind-counterexamples.md の「A へ渡す依頼文」逐語 + 材料と制約(読む範囲・file を変えない・検を走らせない・連絡しない) | 「B へ渡す依頼文」逐語 + 材料(提示する検査 = 強制方法の表・検 3 本・ruff check)と同じ制約 |
| 渡さなかった物 | 設計者の自己評価・強制方法の「限界」の列・設計者の自己点検の変異の記録(G1)・盲検の前の主張の file(design-pre-blind.md)そのもの | 同左 |
| fallback の理由 | 1 番目が認証の不在で起こせない(低い評価や都合で model を替えていない) | 同左 |
| 返答 | blind-A-response.md(未加工・Agent tool の返りの枠の外の本文) | blind-B-response.md(同) |
