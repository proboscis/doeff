# 盲検 A・B の起動の記録(lt-6N1ZY7HZZKH7KMPEH2TS5XV3YQ)

| 項目 | A | B |
|---|---|---|
| 要求した model | 第 1 候補 gpt-6-astra(low)は、この環境の Agent tool で起こせない → 第 2 候補 opus 階層(claude-opus-5-5)・effort xhigh | 同じ |
| 起動の手段 | Claude Code の Agent tool(subagent_type general-purpose・model opus・effort xhigh を引数で指定)・同期の呼び出し | 同じ |
| 観測した model | 未確認(subagent の返答に model の名乗りは無い。起動の引数は opus) | 同じ |
| effort | 引数で xhigh を指定した。子の側で実際に効いたかは観測できない(未確認) | 同じ |
| 機体・profile | zeus の worker の Pod doeff-worker-zeus-5d6d78459d-zl8pw・agora の会話 m-FVZXNCQFHM72WN7A0414TTXB20 の資格のまま(親の API key を渡していない) | 同じ |
| 識別子 | agentId ae97ff00894d69ca3 | agentId a0655d9ae5eb9663d |
| 所要 | 831 秒・道具 43 回 | 702 秒・道具 66 回 |
| 文脈 | 新しい文脈(fork・resume なし)。親の会話・自己評価・既知の失敗例は渡していない。A と B は互いの返答を見ていない(同じ返事の中で並べて起こした) | 同じ |
| 渡した入力 | blind/blind-common.md と blind/blind-a-input.md(path と読みの決まりだけを prompt に書いた) | blind/blind-common.md と blind/blind-b-input.md |
| fallback の理由 | gpt-6-astra の起動の手段がこの環境に無い(Agent tool の model は sonnet・opus・haiku・fable だけ) | 同じ |

返答は加工せずに blind/blind-a-return.md・blind/blind-b-return.md に置いた(harness が行の頭に足す 2 字の字下げだけを外した)。
