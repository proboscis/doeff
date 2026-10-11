# 盲検 A・B の起動の記録

- 入力: `blind-input.md`(sha256 `afa6e1dc34aa58d1d2a6bcde41745f0a9cd56b2d88fb1edced4f4193b3555b75`)と `change.patch`(sha256
  `8a3812da745f28d8e5343c9b805448e52852cd1bcef0ff230065507c43a5de6d` — `git diff 4ccab04ac 15db080f3 -- packages/doeff-hy`)。hash は
  `blind-input.sha256`。依頼文は skill `design-change-validation/references/blind-counterexamples.md` の A・B の文をそのまま使い、その
  後ろに材料の在り処(この 2 file だけ)・材料の「6. 読む物と実験の制限」に従う事・この dir の他の file を読まない事・model の名乗りの
  依頼を足した。
- 入れなかった物: 設計者の自己評価・強制の手段の「限界」の列(`../design-before-blind.md` の表)・既知の揺れ(展開の置き場が冷えた回の
  記録が次の回に一部合わない件)・測りの数・親の会話。
- 起動した機体: この会話の宿(agora の cluster の Pod `doeff-worker-zeus-…`・Linux・`/home/ubuntu`)。起動口: Claude Code の Agent tool
  (subagent_type general-purpose)。A・B は同じ返事の中で並べて起動し、互いの返答も親の会話も見ない新しい文脈で走った(fork・resume は
  使っていない)。profile はこの会話と同じ(子へ資格は渡していない)。
- 起動の開始: 2026-10-11T19:09:44Z(`launch-start.txt`)・両方の返答を受けた刻: 2026-10-11T19:29:40Z(`returned-at.txt`)。

| 順 | 要求した model / effort | 結果 |
|---|---|---|
| 1 | `gpt-6-astra` / `low` | 起動できなかった。この Pod の `codex` は `codex login status` が `Not logged in` で、この環境は login をしない決まり。Agent tool は codex の model を選べない |
| 2 | opus 段(`claude-opus-5-5`)/ `xhigh` | A・B とも完了。起動口に model `opus`・effort `xhigh` を渡した(起動口が effort を受けたかの観測は無く、実際の effort は未確認)。返答の先頭で両方が「Opus 5.5(claude-opus-5-5)」と自己申告した(実 model の観測は自己申告だけ) |

| 役 | agent id | 所要 | tool 呼び出し | subagent の token |
|---|---|---|---|---|
| A | `a9149f8494e1c68ec` | 695,665 ms | 39 | 186,907 |
| B | `ae2a3fab8bb007bdc` | 1,106,625 ms | 61 | 213,147 |

- 返答は加工せず `blind-a-return.md`・`blind-b-return.md` に保存した(harness の字下げだけ外した)。
- 盲検が残した差分・script・入力のうち再現に使った物は `../evidence/repro-a/`・`../evidence/repro-b/` に写した(元は
  `/tmp/ki1475-blind-A/scripts`・`/tmp/ki1475-blind-A/proj`・`/tmp/ki1475-blind-B/scripts`・`/tmp/ki1475-blind-B/demo/proj`)。Python と
  Hy の file は repo の lint の対象に入れないため、中身を変えずに名を `.txt` で終わらせた。
- 制限からの逸脱(盲検の自己申告): B は semgrep と pyright の起動で作業木 `doeff-wt-design-check-ki1475` の `.venv/site-packages` に
  できた .pyc 11 個を自分の物と判じて消した(`git status` で作業木が元どおりと確かめた — 追跡する file は変えていない)。A は写しの
  `records.py` を測った後に元の中身へ戻した。共有の source の変更・git の作業木の作成・全数の検・他の会話への連絡はどちらも無い。
