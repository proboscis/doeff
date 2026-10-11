# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 25f60b29290d22f5473fd038b54a0bdc0f2e8e0647854b20fadd6e7fed9ef04b) | [blind-b-input.md](blind-b-input.md)(sha256 9d10886285fe22f48619fb001cf9cbfd7df7f155f0fa741f9fbe1c09da405891) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 14796a5fb60a88ced4f3520afd013414a8ff0dec1422cc399735ec25a56af1c7)と ../design.md の §1〜§4(盲検の前の design.md 全体の sha256 37dc1566096772ba8df2d065cd17c3de4f896d118badf70dbb2931456d2a68fb) | 同じ + [blind-b-checks.md](blind-b-checks.md)(sha256 84e6161aeacee3a59775966d071db2b8b8cfedee00b32681bc04da8338ef8006) |
| 材料と依頼文を固定した commit | doeff の branch wt/ki-1294-design-check の e928f2dffea6854667ae7893aaa52590f799df94(2026-10-11T22:37:21Z に push — 起こす前) | 同じ |
| 対象のコードの版 | doeff main 1021b89465ca81caba11336d1f5cf9734b37081b | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low --skip-git-repo-check <A の依頼文>` が 401 Unauthorized(Missing bearer or basic authentication)で落ちた — この Pod に codex の資格が無い。[codex-attempt.txt](codex-attempt.txt)・2026-10-11T22:37:32Z〜22:37:51Z・1 回・rc 1 | 同じ 1 回の試しで第 1 候補が使えないと判じ、B では codex を起こしていない |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model 引数 `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 実 model と effort の効き | 観測していない(tool は model の名を返さない・effort が効いたかは見えない)。要求した値だけを記す | 同じ |
| 識別子 | agentId a49458ab4de672af2 | agentId ae43637282c74363d |
| 時間 | 405,274 ms(tool の使用 43 回・subagent の token 117,773) | 752,693 ms(tool の使用 48 回・subagent の token 161,647) |
| 機体・profile | agora の会話 c-WFT0YCZYCTSC96B1MB2WEK5JB3 の Pod(作業の根 /work/agent-workspaces/a-WFT0YCZYCT6YXG8X5SYH45E4TM・同じ profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で同時に、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・既知の失敗例・望む結論を渡していない | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(加工していない — tool の返す字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(同じ) |
| 実験の木と残した物 | `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki1294`(detached・1021b8946)。/tmp に置いた差分とテストは ../evidence/blind-a-artifacts/ に写した | `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki1294`(同じ)。/tmp の再現の script と pyright の比べは ../evidence/blind-b-artifacts/ に写した |

材料の制限: 読む所を design.md の §1〜§4 と実コードに限り、`docs/design-checks/` の下の他の file を読まないように書いた。
