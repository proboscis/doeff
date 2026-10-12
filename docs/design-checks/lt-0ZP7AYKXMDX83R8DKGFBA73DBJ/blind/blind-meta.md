# 盲検 A・B の起動の記録

| 項目 | 盲検 A | 盲検 B |
|---|---|---|
| 入力 | blind-a-input.md(sha256 82d5ca6f661a90c8cf37011bea95819bdb44e3aa7999dadd61ecf1c5b700c3bf) | blind-b-input.md(sha256 b8d349c0d64057963be6749ee89a3f40eeae5c6f8a6bb222750f5454532a5f02) |
| 共通の材料 | blind-common.md(sha256 9e387b10f191477a3a33903f1b693bcf100c6821ac2b923211877c63d75ecad0)— 入力の後半はこの file の `{WORKTREE}` を A・B の作業木に置き換えた文 | 同左 |
| 入力を固定した commit | 51e419aa7(起動の前に branch へ push した) | 同左 |
| 読ませた版 | commit 6a7f25105e14b7c4dd52a76652edf078661eb46c を detached で切った専用の作業木 `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-fe7a`(`uv sync --group dev` 済み・中の変更は許した) | `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-fe7a`(同左) |
| 返答(未加工) | blind-a-return.md(harness が付けた行頭の字下げだけを外した) | blind-b-return.md(同左) |
| 返答と一緒に置かれた file | a-files/(`/tmp/blindA/` の写し — 末尾に .txt を足した) | b-files/(`/tmp/blindb-ki-fe7a/` の写し — 同左) |
| skill の第 1 候補 | gpt-6-astra / effort low — **起こせなかった**: この環境の Agent tool の model は sonnet・opus・haiku・fable だけで、gpt-6-astra を選べない | 同左 |
| 要求した model(代用) | opus(skill の第 2 候補 = tiers.opus = claude-opus-5-5) | 同左 |
| 要求した effort | xhigh(Agent tool の effort の欄で渡した。受け付けられたが、実際に効いた effort は観測できない — 未確認) | 同左 |
| 観測した model | 未確認(返答に model の自己申告は無い。Agent tool への要求は opus) | 同左 |
| 起動の手段 | Agent tool(subagent_type general-purpose)・新しい文脈・会話の fork や resume は使わない・A と B を同じ返事の中で同時に起こし、互いの返答を見せない | 同左 |
| 機体・profile | cluster の Pod doeff-worker-zeus-5d6d78459d-zl8pw(親の会話 c-S2QG21HHGXF766JAJD3S3FF50W と同じ Pod・同じ資格) | 同左 |
| 識別子 | agentId a40409d14b8161fab | agentId a04939ea464a10158 |
| 時間 | 起動 2026-10-12T03:32Z 頃・1023.4 秒(tool の申告 duration_ms 1023377) | 起動 2026-10-12T03:32Z 頃・1008.3 秒(duration_ms 1008310) |
| fallback の理由 | 第 1 候補を起こす手段が無い(上)。低い評価や都合の悪い返答を理由に替えていない | 同左 |
| 再試行 | なし | なし |

渡さなかった物: 設計者の自己評価、design-before-blind.md の §6「予想する限界」、設計者が既に試した変異(c1〜c3)とその結果、望む結論、親の会話。

後始末: 盲検 B は作業木に ex10 の 2 file を intent-to-add で残した(返答 §5)。記録は b-files/ の ex10.patch.txt に在る。作業木 2 つは設計者が
再現を終えてから外す。
