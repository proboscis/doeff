# 盲検 A・B の起動の記録(依頼 lt-XNEJ6RS2KTXW2VWP165DW6FEMD)

| 項目 | A | B |
|---|---|---|
| 要求した model(skill の優先 1) | gpt-6-astra・effort low | gpt-6-astra・effort low |
| 優先 1 を使わなかった理由 | この会話の Agent tool は model に sonnet・opus・haiku・fable しか受けず、gpt-6-astra を起こす手段が無い。`agora conv open` は別の会話(担当)を作る道で、盲検の subagent ではないので使わなかった | 同左 |
| 使った model(優先 2) | Agent tool の model=opus(Opus 5.5 — claude-opus-5-5 に解ける) | 同左 |
| effort | Agent tool の引数 effort=xhigh で要求した(skill の優先 2 の指定)。実際に効いた effort は観測できず、未確認 | 同左 |
| 観測した model | 自己申告を求めなかったので未確認。要求値は opus | 同左 |
| 起動の手段 | Agent tool・subagent_type=general-purpose・新しい文脈(fork・resume なし)。A と B は同じ返事の中で並べて起動し、互いの返答を見ていない | 同左 |
| 機体・profile | agora の cluster の会話の Pod(会話 c-NM47RDG011Q0AG8DS4F117PXDD・profile は会話のもの) | 同左 |
| 識別子 | agentId aaf4e3aa8fe6f337b | agentId a8842983290ce9284 |
| 所要 | 684 秒(tool 25 回・subagent_tokens 173,105) | 971 秒(tool 43 回・subagent_tokens 257,465) |
| 起動の時刻 | 2026-10-12T10:10:34Z の直後 | 同左 |
| 入力 | blind-input.md(sha256 b1161f9678a0bca27ed0b84470c620e06e4f26ffe4e4c6bc0b4b37d9c458d679)と、skill の A の依頼文そのまま。repo を読むだけに限り、repo の file の書き換え・pytest・型検査・semgrep・uv sync・連絡を禁じた | 同左(依頼文は skill の B) |
| 渡さなかった物 | claims-before-blind.md の強制方法の表の「予定」の行(まだ存在しない検)、自己評価、成功の報告、既知の失敗例(1 回目の着地が別名の `Pass` で壊れた事)、親の会話。事前の予想の変更範囲は blind-input.md の §4 に入れて渡した | 同左 |
| 返答 | blind-A-response.md(受け取った文面のまま。harness が各行に付けた 2 文字の字下げだけ外した) | blind-B-response.md(同左) |
| fallback | 優先 1 の起動手段なし → 優先 2 を 1 回。失敗なし | 同左 |
| 一時の file | 申告なし | repo の外に置いた補助の script を 1 つ申告(削除済みと返答) |

注: 起動の prompt に「一時の file は mktemp -d の自分専用の dir だけに作る」「機体の python3 を使わない」の 2 行を入れ忘れた(rulebook subagent-scratch-stays-in-its-own-dir)。どちらも repo を読むだけの依頼で、B が申告した一時 script 以外に file の作成は申告されていない。

盲検の前の主張は claims-before-blind.md に残した(sha256 10af8665a8a4f03458882595b218d13886edd515779f25508a620ac1370f1709・input-hashes.txt に起動前の値を控えた)。
