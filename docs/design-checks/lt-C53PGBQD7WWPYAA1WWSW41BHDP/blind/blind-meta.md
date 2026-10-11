# 盲検 A・B の起動の記録

| 項目 | 盲検 A | 盲検 B |
|---|---|---|
| 入力 | blind-a-input.md(sha256 5459ddf1114c8e67f9681a030aa5425c83ef686cb14620d36f2a6398af065b69) | blind-b-input.md(sha256 5011fb778d66db0d14434375e155cc8d2b310ffc6d41a3ba10b6c8e4e5005682) |
| 共通の材料 | blind-common.md(sha256 4cd31720b4c2a279df6b276831b9a99f3d9fc556d0e3f7f09e0841d93f6ab50c) — 入力の後半はこの file と同じ文(作業 dir の名だけ A・B で違う) | 同左 |
| 読ませた版 | commit cf27cee9896a83edeb2a9547d274afe7d9f43911 の写し `/tmp/ki3497/snap-cf27cee98/`(git archive) | 同左 |
| 返答(未加工) | blind-a-return.md | blind-b-return.md |
| 返答と一緒に置かれた file | a-files/(差分・log・例 ex10) | b-files/(差分・道具) |
| skill の第 1 候補 | gpt-6-astra / effort low — **起こせなかった**: この環境の Agent tool の model は sonnet・opus・haiku・fable だけで、gpt-6-astra を選べない | 同左 |
| 要求した model(代用) | opus(skill の第 2 候補 = tiers.opus = claude-opus-5-5) | 同左 |
| 要求した effort | xhigh(Agent tool の effort の欄で渡した。受け付けられたが、実際に効いた effort は観測できない — 未確認) | 同左 |
| 観測した model | 未確認(返答に model の自己申告は無い。Agent tool の要求は opus) | 同左 |
| 起動の手段 | Agent tool(subagent_type general-purpose)・新しい文脈・会話の fork や resume は使わない・A と B を同じ返事の中で同時に起こし、互いの返答を見せない | 同左 |
| 機体・profile | cluster の Pod doeff-worker-zeus-5d6d78459d-zl8pw・profile archmage(親の会話 c-YAFJJ0FTRF4AV9NQAHV3AC8N95 と同じ) | 同左 |
| 識別子 | agentId a8525df26af243a4b | agentId a0f0867005cbf97b9 |
| 時間 | 起動 2026-10-11T23:18:22Z・696.9 秒(tool の申告 duration_ms 696873) | 起動 2026-10-11T23:18:22Z・674.0 秒(duration_ms 674034) |
| fallback の理由 | 第 1 候補を起こす手段が無い(上)。低い評価や都合の悪い返答を理由に替えていない | 同左 |
| 再試行 | なし | なし |

渡さなかった物: 設計者の自己評価、design-before-blind.md の「限界」の列、既知の失敗例、望む結論、親の会話。
