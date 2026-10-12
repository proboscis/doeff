# 盲検 A・B の起動の記録

## 1 番目の候補(gpt-6-astra・low)— 返答が無く、2 番目の候補へ替えた

| 項目 | A | B |
|---|---|---|
| 要求したモデル・effort | gpt-6-astra・low(skill の 1 番目) | 同左 |
| 起動手段 | `agora conv open --agent model=gpt-6-astra effort=low --title … --first <入力 + 返し方>`(別の会話。fork・resume は使っていない) | 同左 |
| 識別子 | 会話 c-8HQ97AP4T4J5V62T0Q7B7H488V・最初のメッセージ lt-XJKRQM1713RV1VETP42F2VA9RN | 会話 c-Z1TARJ6B5M8XYGSDYA9MG2VEB1・最初のメッセージ lt-FTVZAESKFJBTKB2R49AVE43D4B |
| 起動の刻 | 2026-10-12T08:30:28Z | 2026-10-12T08:30:34Z |
| 入力 | blind/blind-a-prompt.md(hash は evidence/pre-blind.sha256)+ 末尾に「返し方」(返答を `agora tell --to <この設計者の会話> --kind note` で 1 通・他は何もしない)。返し方は hash の対象の外に足した | blind/blind-b-prompt.md(同左) |
| 結末 | 配達は済み(`agora tell status` = delivered・実行が受け取った時刻 08:30Z)。**09:21Z まで 51 分、返答の郵便が 1 通も届かなかった** | 同左 |
| 実際に観測できたモデル | 返答が無いので観測なし | 同左 |

返答が無かった理由は分かっていない。確かめた事: 配達済み・Pod 名に会話 id は出ず kubectl で見えない。見込み: 起こした会話が機体の枠待ちで最初のターンが始まっていない
(同じ時刻に check-slot が「機体の検の枠 8 本が埋まっている」と待たせる表示を出していた)。確かめる所: 会話 2 つの最初のターンの開始の刻と、その時刻の枠の数。

## 替えた理由と手順

- 20 分を超えた待ち(08:50Z)を配り手 m-JMAH2ATPWCCNBDF31QXCNTMACZ へ報告した(`agora tell` lt-FDNT8TF5MS5RZFRYH1BQT1KFBH)。利用者の規則「頼まれていない待ちを 15 分を超えてしない」による。
- reference の「利用不可・上限到達・実行失敗時だけ fallback」に、返答の無い 20 分超の待ちが当たるかは判断が分かれる。**低い評価や都合の悪い失敗例を理由に替えたのではない**(返答自体が無かった)。
  この読みが誤りなら、先の 2 会話が後で答えた時に別に記録する(結果の混ぜ合わせはしない)。**09:21Z の時点で、先の 2 会話から返答は届いていない。**
- 先の 2 会話は止めていない(会話を止める手段を持たない)。後から届く返答は、この記録の A・B の返答とは別の物として扱う。

## 2 番目の候補(opus 階層・xhigh)— 実行した盲検 A・B

| 項目 | A | B |
|---|---|---|
| 要求したモデル・effort | opus 階層(Agent tool の model = opus)・xhigh | 同左 |
| 起動手段 | Claude Code の Agent tool(subagent_type general-purpose・同期)。新しい文脈で起こし、fork・resume は使っていない。A と B は 1 つの返事の中に並べて同時に起こし、互いの返答は見ていない | 同左 |
| 機体・profile | agora の会話 Pod(親の会話の資格のまま。親の API key は渡していない) | 同左 |
| 識別子 | agentId a404cf1c8e6ae92ef | agentId aecdbd0dc181d2c9c |
| 実際に観測できたモデル | Agent tool の返りに model の名は出ない — 要求値 opus(claude-opus-5-5 に解く見込み)と区別して**未確認**。effort も要求値のみで、起動手段が受け付けたかは未確認 | 同左 |
| 所要時間 | 499 秒(tool 32 回・subagent_tokens 134520) | 659 秒(tool 40 回・subagent_tokens 151219) |
| 起動の刻 | 2026-10-12T08:51Z 頃 | 同左 |
| 入力 | /tmp/ki-d153/blind-in/input-a.md(blind/blind-a-prompt.md と sha256 が同じ — aa2e8780…)。設計の本文・証拠の入った dir の外に置き、読む対象をその file と `git -C ~/repos/doeff show 3682f28c8:<path>` に限った | /tmp/ki-d153/blind-in/input-b.md(blind/blind-b-prompt.md と同じ — 885e194e…) |
| 返答 | blind/blind-a-reply.md(加工なし — 道具の返りの字下げだけを外した) | blind/blind-b-reply.md(同左) |

注: 子は同じ Pod の中で動くので、盲検の入力から設計の本文・証拠の dir を外したが、その dir を読めなくしてはいない(読まないよう指示した)。子の返答は、
その dir の中身に触れた形跡が無い(A は材料の本文・3682f28c8 の file・自分の /tmp/blind-A、B は /tmp/blind-B の写しの木だけを挙げている)。
この環境の Agent tool が受け付ける model は sonnet / opus / haiku / fable のみで、gpt-6-astra は Agent tool では起こせない — 1 番目の候補は別の会話として起こした。
