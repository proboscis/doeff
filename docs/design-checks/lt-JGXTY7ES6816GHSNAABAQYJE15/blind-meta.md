# 盲検 A・B の起動の記録 — 依頼 lt-JGXTY7ES6816GHSNAABAQYJE15

## 起動の手段と、確かめた事・確かめていない事

| 項目 | 盲検 A(責務が波及する失敗例) | 盲検 B(検査を通る責務違反) |
|---|---|---|
| 要求した model・effort | opus・xhigh | opus・xhigh |
| 起動の手段 | Agent tool(subagent_type = general-purpose)・新しい文脈 | 同左 |
| 識別子 | a6a18415360dbfaba | a25eb02db9b552c10 |
| 所要時間 | 644 秒 | 743 秒 |
| 返答の保存 | blind-A-response.md(未加工) | blind-B-response.md(未加工) |
| 渡した入力 | blind-input.md(共通)と、役ごとの依頼文 | 同左 |

- 渡した入力は `blind-input.md`(sha256 c60c35de9f5214a7fe0d27b5431e8d43da9252ec35b6edf81fe4bd7175e3b0bf)。盲検の前に保存した設計 `design-pre-blind.md`(sha256 d3ae9c99b9d00a0bab513aeb087cd1bf57d96ad2ba1b226eaba3135a2e1106d1)の本文そのものは渡さず、概要・受入条件・責務と公開の口・将来の変更の主張(事前の予測)・設計者が置いた検査を `blind-input.md` に写して渡した(盲検が主張を壊す対象にする為)。
- 設計者が探して見つけていた穴や、実測の結果は渡していない。読み取りだけの作業木の場所は入力に書いた。

## 要求どおりにならなかった点

1. **model**: 当初は盲検に gpt-6-astra を使う予定だったが、この会話の Agent tool の model の語彙は sonnet・opus・haiku・fable だけで、gpt-6-astra を起こせなかった。opus・xhigh で代えた。
2. **実モデルは未確認**: Agent tool は、起こした子が実際に使った model・effort を返さない。上の「opus・xhigh」は設計者が要求した値であり、子の自己申告以外に裏付けが無い。盲検の独立性(設計者と別の model であること)は、要求した値が守られたと仮定した上での話。
3. **子の一時 file の決まり**: 起動の依頼文に、「子が作る一時 file の置き場と掃除」の決まりを入れ忘れた。子の返答には、読み取り以外の命令を走らせたという申告は無い。盲検に読ませた作業木 `doeff-wt-scoped-definition` は、確認した時点で `git status` が空・HEAD が b52770e03 のまま(読み取りだけの約束は守られた)。子の一時 file が `/tmp` に残ったかは、`/tmp` が他の会話と共有で子の file を見分けられず、確かめられていない(設計者の作った `/tmp/sd-design` は設計者が後で片付ける)。

## 返答の扱い

- 返答は設計者が書き換えていない(`blind-*-response.md` の頭の 1 行の注記だけ設計者が足した)。
- 指摘の成立・不成立の判断と、実測(失敗例の再現・変異・修正前後の比較)は `design.md` の「盲検 A・B の結果」の表と `evidence/` に残した。盲検の「推測」と明記された指摘(表の 8・9)は、測っていない事を未確認として残し、成立とは扱わない。
