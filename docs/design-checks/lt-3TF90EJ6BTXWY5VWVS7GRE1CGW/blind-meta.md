# 盲検 A・B の起動の記録(設計検証 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW・card ki-9743c30d49bc)

| 項目 | A | B |
|---|---|---|
| 役 | 将来の変更で複数の責務へ変更が波及する失敗例探し | 検査を通るのに責務分離を破る実装探し |
| 要求した model・effort | opus・xhigh | opus・xhigh |
| 観測した model | `claude-opus-5-5`(Agent tool の結果の `resolvedModel`) | `claude-opus-5-5`(同) |
| 観測した effort | 未確認(結果に出ない。要求値は xhigh) | 同じ |
| 起動手段 | Claude Code の Agent tool(`subagent_type` general-purpose・前景)。親の会話の fork・resume ではなく、新しい文脈。A と B は同じ返事の中で並べて起こし、互いの返答を見ない | 同じ |
| 機体・profile | agora の cluster の、この会話の Pod(親と同じ機体・同じ資格の profile) | 同じ |
| 識別子(agentId) | `a9d82cd763462f529` | `a5fe1e34eacdbe846` |
| 起動〜返答 | 2026-10-12T11:25:44Z〜11:46:59Z(1,274.6 秒) | 11:25:52Z〜11:47:52Z(1,320.6 秒) |
| tool の呼び・token | 39 回・203,953 token | 36 回・163,940 token |
| 渡した入力 | `blind-input.md`(sha256 `c30f3ced4e4f8202006fdd02ad3653120336e4121a45f62e3b9d5a714bbe1d8f`)と、依頼文 `blind-A-prompt.md`(sha256 `f1f7390f3feff584304a2ef806015f96895282cacf22a766d4936231fe11be67`・記録から加工せず書き出した)。対象の版 = doeff main `c225cef6f2d688b3d2133a7d88a89a68984becfe` | `blind-input.md`(同じ file)と依頼文 `blind-B-prompt.md`(sha256 `eec7b685bb0082f070c815b74122d250315915044d5c0a11dd43eda11f2fa55c`)。提示する検査の実体・命令・検出範囲は `blind-input.md` の第 4 節 |
| 事前の主張の固定 | `design-pre-blind.md`(sha256 `4392dca38b0637f7b180c98466b8a43088af35d5c16f5ffc1738b0642466f02c`)を commit `41b33a3d9033eda6c78c47ac4ad4f5d0e67b5ac9`(committer 日時 2026-10-12T11:25:25Z)に固定した。起動はその 19 秒後。origin の branch `wt/dc-lt3tf` はこの commit を指す(push の時刻は記録していない) | 同じ |
| 渡さなかった物 | 設計者の自己評価・検の結果・既知の失敗例・親の会話・依頼の本文(card の「何が起きているか」)・望む結論 | 同じ |
| 返答 | `blind-A-response.md`(sha256 `ef0d8f7814c2c56db1d55d121b5c59317c27d6d3e931b0b3ca954ec8c0075cca`・加工なし) | `blind-B-response.md`(sha256 `8e3f3b3f39f15c6882631efee9d345c567545e93956e6835dcbf6e40c3765e30`・加工なし) |
| fallback・再試行 | 無し | 無し |

## 起動で守らせた決まり(依頼文と blind-input.md に書いた)

- 共有の source を書き換えない。実験は一時 dir へ写した `packages/doeff-linter` と `packages/doeff-indexer` の上で行う。build の置き場も一時 dir。
- 本物の cache の置き場(`/work/toolset/cache/doeff-linter`・`~/.cache/doeff-linter`)に触らない。`DOEFF_LINTER_CACHE_DIR` を一時 dir にする。
- python は `uv run` を通す。全数検査・他の会話への連絡・network への送信はしない。
- 実験した事と読んだだけの事を、返答で分ける。失敗例が見つからなければ探索した範囲と不足を述べ、安全とは結論しない。
- B には、提示した検査を写しの上で実際に実行して「通る」事を確かめさせた。

## 起動の欠け(正直に)

- A・B が使った実行環境の model の実際の effort は読めない(結果に出ない)。`resolvedModel` は読めた。
- 盲検の返答は、どちらも「あなたの会話の次の指示」を待つ旨の `WAIT:` 行で終わっている(この会話の約束事を、依頼文の外で子が真似たもの)。返答は加工せずに残した。
- A・B は写しを `/tmp` の下に残した(`/tmp/dc-lt3tf-CbuZ`・`/tmp/dc-mod.Mp43FS`・`/tmp/dc-orig.EglefR`・`/tmp/dc-lt3tf-scripts/`)。共有の木・`~/repos` は変えていない(それぞれ自己申告で `git status` 空。設計者は B の差分と A の実験の script を、自分の実験用の写しへ取り込んで再現した)。A の `exp.sh`・`exp_touch.sh` は `evidence/tools/` に写した。
- 構成レビュー(盲検 A・B 以外の設計の筋のレビュー)は起こしていない。この依頼は skill の A・B の盲検までが必須で、構成レビューの指定が無かった。

## 返答の扱い(設計者の読み)

- A の返答の失敗例(配置の知識が M-sweep に漏れる)と実験 2(読んでも更新時刻が動かず、「10 分の内は消さない」の言い方と食い違う)は、どちらも設計者が元の木で再現した(`evidence/A1`〜`A3`)。成立した。
- B の返答の失敗例(表を書き直す場で前の事実を消す)は、提示された検査が全部通ったまま不変条件と R1 を破る事を、設計者が元の木で再現した(`evidence/B1`・`B2`)。成立した。
- 「読んだだけで検証していない別の候補」は、設計者が個別に扱いを決めた。扱いは `design.md` の第 7 節。
