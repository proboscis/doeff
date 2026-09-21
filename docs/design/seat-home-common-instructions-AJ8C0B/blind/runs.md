# 盲検 A・B の起動記録

skill `design-change-validation` / `references/blind-counterexamples.md` の手順。
A・B は互いの返答を見ない**別の新規文脈**で起動した(会話履歴の fork / resume は使っていない)。
渡した入力は `blind/input-common.md` ただ 1 つ(sha256
`28affd5272950cc67d3f0f7f49e5396d7753128d40cdaa90cb90801753d62c49` / 57,273 byte)。
著者の自己評価・成功報告・**既知の反例**(`claims-before-blind.md` の「自分で見えている穴」節)・
望む結論・親会話は**渡していない**(その節を機械的に切り落として組んだ)。

## モデルの選択と fallback

operator 指定の優先順は ① `gpt-6-astra` / `low` ② `claude-fable-5-1` / `xhigh`
③ `claude-opus-5` / `xhigh`。各候補を 1 回ずつ試した記録:

| 順 | 要求モデル / effort | 起動口 | 結果 |
| --- | --- | --- | --- |
| ① | `gpt-6-astra` / `low` | codex | **利用不可**。この機体に `codex` の実行体が無い(`command -v codex` = 不在)。profile の口 `cx` も断る: 逐語 `cx: この機体は宿名を宣言していない(/home/kento/.local/state/agora/host-id が無い…)— 会社階級 'cyberagent' で provider を呼ばない` |
| ② | `claude-fable-5-1` / `xhigh` | Agent 口(新規文脈) | **上限到達**。A・B とも起動直後に HTTP 429 `You've reached your Fable limit.`(request id A = `req_011CfG286daFyGuVDdX7tFL7` / B = `req_011CfG28fZyAv31tirkjQx4P`・API へ送られたモデル `claude-fable-5-1`) |
| ③ | `claude-opus-5` / `xhigh` | Agent 口(新規文脈) | **実行**(下の表) |

⚠ **effort の正直登記**: この機体の Agent 口は effort を引数に取らない。
要求は `xhigh` だが、**子へ effort を渡す口が無い**ので実効 effort は未確認
(黙って別 effort へ落としたのではなく、指定を受け付ける口が無い)。

## 実行

| 役 | 要求モデル | 起動口 / 機体 | 起動 | 返答の保存先 |
| --- | --- | --- | --- | --- |
| A(将来変更で責務が波及する反例) | `claude-opus-5` | Agent 口(新規文脈)/ pod `agora` の席 `c-AJ8C0BK9RF29HQ92ZQ986FXQVT` の子 | 2026-09-21T05:3xZ | `blind/A-raw.md` |
| B(静的検査を通る責務違反の反例) | `claude-opus-5` | 同上 | 2026-09-21T05:3xZ | `blind/B-raw.md` |

⚠ **実モデルの観測**: 起動口は要求モデルを受け付けたが、子の手番で API が名乗った
モデルをこの会話から直に読む口は無い。**要求値と区別して未確認**と記す
(② の失敗だけは API の error 本文がモデル名を名乗ったので実測)。

返答は**加工せずに**保存する(不成立の反例も消さない)。設計者の再現と修正は
`counterexamples.md` に書く。
