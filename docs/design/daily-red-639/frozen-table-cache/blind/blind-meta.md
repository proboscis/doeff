# 盲検 A・B の起動の記録

## 入力(起動の前に固定)

| file | sha256 | 中身 |
| --- | --- | --- |
| `blind-input.md` | `82cdc8cfba70d007a06170c9ad24b3d34dafc5c1eb4490d96f822ca6275e5a6c` | 事前の主張(`../design-before-blind.md`)から、著者の経緯(順序の逸脱)と強制の方法の「限界」の列を除き、読む材料と検の実行方法を足した物。設計者の自己評価・既知の反例・望む結論は入れていない |
| `prompt-a.txt` | `9cd911b8d76457d1856f19b91ab12a56f1f9e5d6612466fd9fb789e700dda24e` | skill の A の依頼文 + `blind-input.md` |
| `prompt-b.txt` | `ed937611fe7bf599b9fd22a5212a195e3b87b974021d12cfba4ba049ab9613a9` | skill の B の依頼文 + `blind-input.md` |

読ませた作業樹は、設計の記録を置いていない別の作業樹 `~/.worktrees/doeff-wt-639-P-blind`(本線 72da0ff1 の detached)。
記録を置いた作業樹(`doeff-wt-639-P`)は読ませていない。

## 第 1 候補: gpt-6-astra・effort low(失敗)

| 項目 | A | B |
| --- | --- | --- |
| 起動口 | `codex exec -m gpt-6-astra -c model_reasoning_effort="low" --sandbox read-only --ephemeral -C <作業樹> -o <返答> -`(stdin = `prompt-a.txt`) | 同じ(stdin = `prompt-b.txt`) |
| profile | `CODEX_HOME=~/.codex/profiles/personal`(個人の profile を明示。会社の profile は使っていない) | 同じ |
| 機体 | この Mac(proboscis-mbp) | 同じ |
| 起動 | 2026-09-25T22:58:07Z | 同じ |
| session id | 01a0daca-53cf-7c32-b054-ff2aa7b33a61 | 01a0daca-53e8-7933-b804-42c131fc7319 |
| 結果 | `401 Unauthorized`(接続の再試行 5 回 × 2 の後)。返答なし | 同じ |
| log | `codex-a-failure.log`(API キーの断片は伏せた) | `codex-b-failure.log` |

各候補 1 回ずつ試し、実行失敗なので第 2 候補へ倒した。

## 第 2 候補: Opus 5.5(実行)

| 項目 | A | B |
| --- | --- | --- |
| 起動口 | Claude Code の Agent tool・subagent_type = general-purpose・model = opus | 同じ |
| 要求したモデル | claude-opus-5-5・effort xhigh | 同じ |
| effort | 起動口に effort の欄が無く、指定できなかった(実際の effort は未確認) | 同じ |
| 観測したモデル | 未確認(子の自己申告も取っていない) | 同じ |
| profile | この会話の Claude Code の profile を継ぐ(Agent tool は子に別の profile を選ばせない) | 同じ |
| 機体 | この Mac(proboscis-mbp) | 同じ |
| 文脈 | 親の会話を fork・resume せず新しい文脈。相手の返答は見せていない(同じ返事の中で同時に起動) | 同じ |
| 起動 | 2026-09-25T22:59:16Z 頃 | 同じ |
| agent id | ae27b854947a5068d | aff50ac6ef3ecacea |
| 所要 | 442.6 秒・tool 35 回 | 940.5 秒・tool 51 回 |
| 返答 | `blind-a-return.md`(未加工) | `blind-b-return.md`(未加工) |

渡した文は `prompt-a.txt` / `prompt-b.txt` と同じ本文に、次の「作業の範囲」の段落を 1 つ足した物(codex では `-C` と
`--sandbox read-only` が同じ役をした):

- A・B 共通: 作業樹 `/Users/kento/.worktrees/doeff-wt-639-P-blind` を読み取りだけで使う・試しの file は /tmp の下・作業樹の外は読まない・
  git は読み取りだけ・焦点の pytest は走らせてよい。
- B だけ: 検査の通過を確かめる時は作業樹を /tmp へ写した先で差分を当ててよい(`.venv` は元の作業樹を指してよい)。

盲検が /tmp に残した再現の材料は `../evidence/blind-a/`・`../evidence/blind-b/` へ写した。
