# 盲検 A・B の起こし方(lt-4KTRWD3K1PH2VG5W5X294EZJP4)

| 項目 | A | B |
|---|---|---|
| 要求した model の順 | 1. gpt-6-astra(low)→ 2. opus 階層(claude-opus-5-5・xhigh) | 同じ |
| 1 番目を使わなかった訳 | この会話の起こし手(Claude Code の Agent tool)が選べる model は sonnet・opus・haiku・fable だけで、gpt-6-astra を起こす口が無い | 同じ |
| 起こした model | Agent tool の model=opus(claude-opus-5-5 の見込み — 子の自己申告は取っていない・観測は未確認) | 同じ |
| effort | Agent tool の effort=xhigh を渡した(子の側で効いたかは未確認) | 同じ |
| 起こし方 | Agent tool の subagent_type=general-purpose を新しく起こす(会話の fork・resume は使わない)。A と B は 1 つの返事の中で同時に起こし、互いの返答を見ない | 同じ |
| 識別子 | agentId a36da9c4f6371c802 | agentId aeba96e06c4961dd5 |
| 所要 | 996,820 ms・tool 40 回 | 1,705,834 ms・tool 75 回 |
| 機体・profile | zeus の k3s の Pod(job turn-host-zeus)・この会話(c-JX54TF8S2WEQYEMSSA5J92B8D6)の profile | 同じ |
| 渡した入力 | blind-a-prompt.md(skill の A の依頼文そのまま + 材料の場所 + 一時の file と python の決まり)・blind-input.md・design-pre-blind.md | blind-b-prompt.md(B の依頼文そのまま + 同じ)・同じ 2 file |
| 渡さなかった物 | 著者の自己評価・先に済んだ査読(Fable 5.1 の所見)・既知の失敗例・望む結論・親の会話 | 同じ |
| 返答 | blind-a-reply.md(加工なし) | blind-b-reply.md(加工なし) |
| fallback | 無し(1 回で返答を得た) | 無し |

入力の hash(盲検の前に commit f207e4d36 で保存):

| file | sha256 |
|---|---|
| design-pre-blind.md | df7d5c847e9c516cf643d3aa8dcfeee9b4cc98fce5dba48de64a98b6477d0dea |
| blind-input.md | 82b93bb717589969aa23c5750a1fa03ac7c64db539b0f303fa690a7c7407f3c9 |
| blind-a-prompt.md | 74d3ef48ff218d558b24b823f300aeffee70ae245d8563ed68724f8d8859ee2e |
| blind-b-prompt.md | 0b65622fdd4c4415b40e8920a441740f9dd2144c33bf68556a8137d60812f760 |

A と B の Agent tool への依頼文の末尾には、上の prompt に加えて「一時の file は mktemp -d の自分の dir の下だけ・機体の python3 を使わない・
作業木の file を編集しない」の決まりを足した(rulebook の subagent-scratch-stays-in-its-own-dir)。B には、source を変えた写しを mktemp の dir に作る
形も書いた。
