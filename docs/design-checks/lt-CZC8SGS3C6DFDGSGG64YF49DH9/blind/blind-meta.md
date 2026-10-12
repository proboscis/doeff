# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 109189ce13f41f5f177d53810fa0c5dd2195dbec01931cad31fd6fdab7004dff) | [blind-b-input.md](blind-b-input.md)(sha256 c5e34ef1cb28756edd67a094f647a86aac7b797f56da0ab29dd2327ac2650e39) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 1fe500d1f971aba445325f0b256049a0075098f7392d8da228bd06769eda1464)= 盲検の前の design.md の §1〜§4(盲検の前の design.md 全体の sha256 76e07015938f4ac7a68c462c943530f4da0cfa643709b9c420f1f78595ca3950 — ../evidence/design-pre-blind.md)。作業木の中に BLIND_INPUT.md として置いた | 同じ(§4 が検査の実体・設定・検出範囲を含む) |
| 材料と依頼文を固定した commit | doeff の branch wt/ki-edab-design-check の 730bf2d8106447fbe4f69cb83593240272cb4cd8(2026-10-12T03:49:06Z commit・03:49:18Z までに push — 起こす前) | 同じ |
| 対象のコードの版 | doeff main 08ddfc707b(直した版) | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low --skip-git-repo-check <A の依頼文>` が 401 Unauthorized(Missing bearer or basic authentication)で落ちた — この Pod に codex の資格が無い。[codex-attempt.txt](codex-attempt.txt)・2026-10-12T03:49:24Z〜03:50:12Z・1 回・rc 1 | A の 1 回の試しで第 1 候補が使えないと判じ、B では codex を起こしていない |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model 引数 `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 観測した model・effort | 観測していない。tool は model の名を返さず、effort が効いたかも見えない。要求した値(`opus`・`xhigh`)だけを記す | 同じ |
| 識別子 | agentId a472f39e557013b4a | agentId affb77bfd675d2466 |
| 時間 | 769,317 ms(tool の使用 39 回・subagent の token 164,243) | 1,438,128 ms(tool の使用 39 回・subagent の token 222,828) |
| 起こした時刻 | 2026-10-12T03:50:19Z の直後(A と B を同じ返事の中で同時に) | 同じ |
| 機体・profile | agora の会話 c-Z48YJMZ0CSH4120XECK1TQWS0P の Pod(作業の根 /work/agent-workspaces/a-Z48YJMZ0CSSPHHEMNVQBWHT1RG・同じ profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・§5 の分かっている限界・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool が付けた行頭の字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ) |
| 実験の木と残した物 | `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-edab`(detached・08ddfc707)。/tmp/blind-a-ki-edab/ の再現の script は ../evidence/blind-a-artifacts/ に写した(`keep/`・`old/` の handle.hy・macros.hy の写しは repo の版の写しなので写していない) | `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-edab`(同じ)。/tmp/blind-b-ki-edab/ の差分と probe は ../evidence/blind-b-artifacts/ に写した |

材料の制限: 読む所を BLIND_INPUT.md と使い捨ての作業木の中に限り、`docs/design-checks/` の下・~/repos/doeff・他の作業木・agora の card や郵便を読まないように書いた。
