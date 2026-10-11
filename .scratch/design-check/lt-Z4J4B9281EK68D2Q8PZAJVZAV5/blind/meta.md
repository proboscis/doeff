# 盲検 A・B の起動の記録

| 項目 | A(波及の失敗例) | B(検査を通る責務違反) |
| --- | --- | --- |
| 依頼文 | references/blind-counterexamples.md の「Aへ渡す依頼文」を逐語 + 材料の段(下) | 同「Bへ渡す依頼文」を逐語 + 材料の段(下) |
| 渡した入力 | blind-input.md(sha256 45cac21ba3e14461d3e5728203b71f4b9645a7d6fca7d9d74c85f52d36f4fea9)と、その 6. に挙げた実体(doeff 15e6fc9c4 の作業木・読み取りだけ) | 同じ |
| 渡さなかった物 | 設計者の自己評価・既知の限界(design-preblind.md の 5.)・成功の報告・親の会話 | 同じ |
| 第 1 候補 | gpt-6-astra・effort low(codex CLI 0.162.1) | 同じ |
| 第 1 候補の結末 | 起動しなかった — `codex login status` = `Not logged in`(2026-10-11 この Pod)。この環境に codex の資格が無い | 同じ |
| 起こした物 | Claude Code の Agent tool・subagent_type general-purpose・model = `opus`(要求)・effort = `xhigh`(要求) | 同じ |
| 観測した model | 未観測(subagent に model の自己申告を求めていない・tool の返答に model の欄が無い)。要求値は opus = claude-opus-5-5 | 同じ |
| effort | 要求 xhigh を tool の引数で渡した。実際に効いたかは未確認 | 同じ |
| 文脈 | 新しい文脈(fork・resume なし)。A と B は 1 つの返事の中で同時に起こし、互いの返答を見ていない | 同じ |
| 識別子 | agentId ad275e2706bc756a4 | agentId a17ce5a1b301b6149 |
| 所要 | 406 秒(tool_uses 25) | 528 秒(tool_uses 28) |
| 機体・profile | この会話の Pod(agora の worker・c-GPQPMMK7CZKZE1Z0WVXRKDYS92)・profile の切り替えなし | 同じ |
| 返答 | blind/A-reply.md(未加工) | blind/B-reply.md(未加工) |
| 実験の写し | blind/A-exp.sh(/tmp/dc-vocab-bes750/bin/exp.sh) | blind/B-hook.patch・blind/B-future.patch・blind/B-driver.py(/tmp/dc-semgrep-K8QL) |
| 守られなかった制約 | なし(返答の申告) | Python の一部を uv を通さず共有の venv の python で直に走らせた(返答の末尾の申告)。共有の file は変えていない(返答の `git status` の申告・設計者も `git status` で確かめた) |

## 依頼文に足した材料の段(A・B 共通の骨。B は検査の実体の 1 文と `git clone --local` の許しが加わる)

```text
---
材料(固定済みの要件・責務・公開契約・将来変更についての主張・実コードの所在):
- まず …/blind-input.md を読んでください(sha256 45cac21b…)。その「6. 読める実体」に挙げた file だけを読み取りで参照できます。
- 作業の制約:
  - /home/ubuntu/.worktrees/doeff-wt-design-check-staged-semgrep と ~/repos 以下の file を変更・作成・削除しないでください(git の add・commit・stash・checkout・worktree の操作もしない)。
  - 実験をするなら、自分で作る新しい dir(例 mktemp -d で /tmp の下)に必要な file を写して行ってください。全体の pytest や日次の全体検査は走らせないでください。1 回の実験は数十秒以内にしてください。
  - agora・ai・kubectl・gh の命令は使わないでください(他の会話・系への連絡をしない)。
  - Python を走らせる時は uv を通してください(例 `uv run --no-project --python 3.12 python …`)。
- 返答の最後に、実際に実行したコマンドと終了の値の一覧(実行していなければ「実行なし」)を付けてください。
```
