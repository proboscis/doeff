# 盲検 A・B の起動の記録

| 項目 | A | B |
| --- | --- | --- |
| 依頼文 | [blind-a-input.md](blind-a-input.md)(sha256 cd04d0b99335615de0172278a624dedeb5f757c512517d4f9374ebc1d7467e1c)— skill の参照文書 `blind-counterexamples.md` の「Aへ渡す依頼文」の文面そのまま([prompt-a.txt](prompt-a.txt) sha256 b4a660ca…) | [blind-b-input.md](blind-b-input.md)(sha256 6ca6dc9ddb70fa462ae8e688068d369bb43749237d3162f62ba0d87bcc71fd62)— 「Bへ渡す依頼文」の文面そのまま([prompt-b.txt](prompt-b.txt) sha256 c15bd75f…) |
| 渡した材料 | [common-input.md](common-input.md)(sha256 fe960970acedb7b373f11cb8204d02afb960b4e464f15fc2937e1d4e6782ea65)= 盲検の前の design.md の §1〜§4(盲検の前の design.md 全体の sha256 86689ca81d6ac7445ed253b9c9afa171c7265820d8738808b2525248c3207e85 — [../evidence/design-pre-blind.md](../evidence/design-pre-blind.md))。作業木の中に `BLIND_INPUT.md` として置いた | 同じ(§4 が検査の実体・設定・検出範囲・実行コマンド) |
| 材料と依頼文を固定した commit | doeff の branch `wt/ki-1618-design-check` の d241eb465(2026-10-12T09:15:10Z に push — 起こす前) | 同じ |
| 対象のコードの版 | doeff main `1410ce508e3baec632ea994184c884ba28ca0ee9`(直した版) | 同じ |
| 要求した model・effort(skill の第 1 候補) | `gpt-6-astra`・`low`(codex) | 同じ |
| 第 1 候補の結末 | `codex exec -m gpt-6-astra -c model_reasoning_effort=low --skip-git-repo-check <A の依頼文>` が 401 Unauthorized(Missing bearer or basic authentication)で落ちた — この Pod に codex の資格が無い。[codex-attempt.txt](codex-attempt.txt)(sha256 20413ee4…)・2026-10-12T09:15:44Z〜09:16:00Z・1 回・rc 1。同じ Pod の前回の設計検証(lt-CZC8SGS3C6DFDGSGG64YF49DH9)も同じ理由で落ちている | A の 1 回の試しで第 1 候補が使えないと判じ、B では再試行しなかった(同じ Pod・同じ資格なので結果は同じ。「各候補を 1 回試して」の 1 回を A の依頼文で兼ねた) |
| 代わりに起こした物(第 2 候補) | この会話の Agent tool の subagent(subagent_type general-purpose・model 引数 `opus` = Opus 5.5・effort 引数 `xhigh`) | 同じ |
| 観測した model・effort | 観測していない。tool は model の名を返さず、effort が効いたかも見えない。要求した値(`opus`・`xhigh`)だけを記す。この環境の Agent tool が起こせる model は sonnet / opus / haiku / fable だけで、第 1 候補は起こせない(代用の理由) | 同じ |
| 識別子 | agentId a6fcaad5e9105f407 | agentId a41c6aa56c267f44e |
| 時間 | 1,215,735 ms(tool の使用 52 回・subagent の token 247,017) | 1,681,325 ms(tool の使用 73 回・subagent の token 267,021) |
| 起こした時刻 | 2026-10-12T09:16:00Z の直後(A と B を同じ返事の中で同時に) | 同じ |
| 機体・profile | agora の会話 c-MPWEY1GKS4FAWWR2JS2EDG5SAS の Pod(作業の根 /work/agent-workspaces/a-MPWEY1GKS465V5TRYW5KFKWW49・この会話の profile) | 同じ |
| 文脈を共有しない起こし方 | A と B を同じ返事の中で、別々の新しい文脈で起こした(会話の fork・resume を使っていない)。互いの返答・設計者の自己評価・§5 の分かっている限界・既知の失敗例・望む結論を渡していない。起こす文は「BLIND_INPUT.md を読んでその依頼文に従い、日本語で返答する」と読む所・再現 script の置き場の指定だけ | 同じ |
| 返答 | [blind-a-return.md](blind-a-return.md)(sha256 33f68f5c63702c0f8c2e253eac161d196db0764ec2908587a7373b55671626cb — 加工していない。tool が付けた行頭の字下げだけを外した) | [blind-b-return.md](blind-b-return.md)(sha256 027be1e7e3f3b271cf251c18f627739ea30d46c14452d889097279f710b3ef5c — 同じ) |
| 実験の木と残した物 | `/home/ubuntu/.worktrees/doeff-wt-blind-a-ki-1618`(detached・1410ce508・`docs/design-checks/` を消した木)。/tmp/blind-a-ki-1618/ の再現の script は [../evidence/blind-a-artifacts/](../evidence/blind-a-artifacts/) に写した | `/home/ubuntu/.worktrees/doeff-wt-blind-b-ki-1618`(同じ)。/tmp/blind-b-ki-1618/ の script と差分は [../evidence/blind-b-artifacts/](../evidence/blind-b-artifacts/) に写した(overlay の複写は repo の版の写しなので写していない) |

材料の制限: 読む所を BLIND_INPUT.md と使い捨ての作業木の中に限り、`docs/design-checks/` の下(作業木からは消した)・git の履歴・~/repos・他の作業木・agora の card や郵便を読まないように書いた。subagent は読み取りの制限を守ったと自己申告している(B は「作業木の差分は、準備の時に消された `docs/design-checks/` と `BLIND_INPUT.md` だけ」と述べた)。守ったかどうかの外からの確認はしていない。

fallback の理由: 第 1 候補(codex・`gpt-6-astra`・`low`)の資格が Pod に無い(401)。低い評価や都合の悪い失敗例を理由にした model の入れ替えはしていない。A・B とも同じ第 2 候補。
