# 盲検 A・B の起動の記録(lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97)

| 項目 | A | B |
|---|---|---|
| 第 1 候補(gpt-6-astra・effort low) | 試した: `codex exec --skip-git-repo-check --sandbox read-only -m gpt-6-astra -c model_reasoning_effort=low "Reply with exactly: ok"`(この Pod の `codex` = codex-cli 0.162.1)が `401 Unauthorized: Missing bearer or basic authentication in header` で返った(資格が無い)。起こせないので第 2 候補へ | 同じ(1 回の試しを A・B の両方の判断に使った) |
| 起こした model | 第 2 候補 opus 階層(Agent tool の `model: opus` = claude-opus-5-5)・effort xhigh を引数で指定 | 同じ |
| 起動の手段 | Claude Code の Agent tool(subagent_type general-purpose)・同期の呼び出し | 同じ |
| 観測した model | 未確認(返答に model の名乗りは無い)。1 回目の A の失敗の文に `model sent to the API: claude-opus-5-5` とある | 未確認(起動の引数は opus) |
| effort | 引数で xhigh を指定した。子の側で実際に効いたかは観測できない(未確認) | 同じ |
| 機体・profile | zeus の worker の Pod・agora の会話 a-C2HCCSNSQKS46Z9QWQBW90G46D(c-C2HCCSNSQKN5E5JTMZ2M5AWJRH)の資格のまま(親の API key を渡していない) | 同じ |
| 1 回目 | A と B を同じ返事の中で並べて起こした。A は起動の直後に API の誤りで止まった: `Failed to authenticate: OAuth token revoked … (error type authentication_failed, HTTP 401, request id req_011CfwgwMwxTgcxTWpXWoF3i, model sent to the API: claude-opus-5-5)`。返答なし | agentId acd84212fb894aeb1・996 秒・道具 59 回。返答あり |
| 2 回目(A だけ) | 同じ候補(opus・xhigh)・同じ入力の file で 1 回だけ起こし直した(実行の失敗で、返答の中身による起こし直しではない)。agentId aeb68e42f45787972・718 秒・道具 23 回。返答あり | — |
| 文脈 | 新しい文脈(fork・resume なし)。親の会話・自己評価・既知の失敗例・設計の前に頼んだ見直しの結果は渡していない。A と B は互いの返答を見ていない(A の 2 回目は B の返答の後に起こしたが、B の返答は渡していない) | 同じ |
| 渡した入力 | blind/blind-common.md・blind/blind-a-input.md・../design-before-blind.md(prompt には file の path と守る事だけを書いた) | blind/blind-common.md・blind/blind-b-input.md・../design-before-blind.md |
| prompt の差 | 2 回目の A の prompt の守る事に 1 行を足した: 「環境変数 DOEFF_HY_CODE_STORE は本番の共有の保存先を指している。Hy の module を import して試す時は、必ず自分の dir か `off` へ上書きする」(本番の保存先を試しで書き換えさせないための安全の行。失敗例の手がかりは含まない) | — |

返答は加工せずに blind/blind-a-return.md(2 回目の A)・blind/blind-b-return.md に置いた(harness が行の頭に足す 2 字の字下げだけを外した)。
