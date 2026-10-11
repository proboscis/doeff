# codex の道具の item の写し — 設計検証の記録(card ki-77e76c7c07ce・依頼 lt-5AB8Q8DB3YHVBC8WHFP34QVQKY)

この文書の目的: codex の道具の item を doeff-agents の道具の出来事へ写した変更について、盲検 A・B の失敗例を受けて何が崩れ、何を
直し、何がまだ測れていないかを、読み手が実行の記録まで辿れる形で示す。盲検の前の主張は `design-before-blind.md`(sha256
`1dea4cc2cd2a45ed80ceb2ef79aad81da6f15c2e420c4f2da6fcc5382e74c7fc`)で、ここでは書き換えない。報告の JSON は
`design-check-report.json`。

## 1. 完了の範囲

実装完了まで。どちらも doeff の main に入った。

| 便 | 赤 → 緑 | 中身 |
|---|---|---|
| L2078 | 9247f7b39 → af7bd4857 | 道具の item(commandExecution・fileChange・webSearch)→ AgentToolUseEvent・AgentToolResultEvent・AgentToolCallStartedEvent |
| L2083 | 35118d034 → 8b0a1b812 | 盲検 A・B の失敗例の直し(下の 3 節) |

範囲の外(予定にも入れていない): agora-controllers の doeff の pin を上げる事と配備・画面の印の表・mcpToolCall などの写し・
命令の出力の途中(item/commandExecution/outputDelta)の写し。

## 2. 失敗例と、設計者の再現・直し

| 失敗例 | 盲検の主張 | 設計者の再現(記録) | 判定 | 直し(L2083) |
|---|---|---|---|---|
| A-1 漏れ: handler の absorb が「ターンを名乗る記録」を lines の class の列挙で持つ | 新しいターンの記録(例 outputDelta)を lines に足すと、handler の列挙も直す要があり、忘れても検査が止めない | 盲検の録りと差分(`evidence/blind-a/`)を読み、af7bd4857 の handler.hy の absorb の列挙(旧 :164-166)で確かめた | 成立(S1 の範囲の外の隣の変更 — item でない通知 — だが、責務表の「handler は変わらない」を崩す漏れ) | lines に `TurnRecord`(ターンの id を必ず持つ記録の集まり)を置き、handler は `isinstance record lines.TurnRecord` で振り分ける。検 `test_turn_records.hy` が「ターンの id を必ず持つ記録 = TurnRecord」を確かめる |
| A-2 S3 の主張が崩れる: 前のターンで始めて後ろで走った命令の結果が届かない | 命令の終わり(item/completed)は前のターンを名乗って次のターンの間に届き、handler は終わったターンの列へ積み、adapter はもう読まない | 本物の codex 0.162.1 で筋書き background-command を録り(`tests/recorded/codex-0.162.1/background-command.*`)、替え玉で流す検が直す前に赤(`evidence/C-red-before-fix.log` — 結果 0 件) | 成立(S3 の前提の範囲の中) | handler の absorb: 終わったターンを名乗る遅れた記録は走っているターンへ、無ければ `late` に取っておき、次のターンを始めた時にその頭に積む。検が緑(5 回とも — `evidence/C-S3-after-fix-repeated.log`) |
| B 層 3 が自前で codex の JSON を解く実装が検査を全部通る | 層 2 が mcpToolCall を UnknownValue で素通しし、層 3 が defwire で解く。名指しの検・pre-commit・型検査とも通る | 盲検 B の違反の adapter を、規則の無い版(af7bd4857 の .semgrep.yaml)で撃つと 0 件(`evidence/C-S1-blind-b-rejected-by-the-rule.log`) | 成立(R3 の違反を止める静的な規則が無かった) | semgrep 規則 `doeff-agents-decodes-no-wire-json`: doeff-agents の src に defwire・doeff_hy.wire・OpaqueJson・parse-json を書かない。同じ adapter を規則の在る版で撃つと違反の 6 行を断り、実物の doeff-agents の src は 0 件 |
| 副次: 利用枠切れの検の揺れ(盲検 A・B の両方が観測) | turn/start の答えから current-turn を置くまでの間に、ターンを名乗らない枠の知らせが outside へ落ちる | 設計者も main で 4 回中 4 回 1 件の赤を観測(この変更の前から) | この変更の範囲の外(card ki-a59d80e0afa9 に観測を書いた) | 直していない |

## 3. 強制の手段(直した後)

| 守る責務 | 強制の手段 | 実装の置き場 | 実行の経路 | 限界 |
|---|---|---|---|---|
| doeff-agents は外の CLI の行の JSON を解かない(R3) | semgrep `doeff-agents-decodes-no-wire-json`(defwire・doeff_hy.wire・OpaqueJson・parse-json) | .semgrep.yaml・検 tests/semgrep/test_doeff_agents_wire_json_rule.py(fixture の失敗の 4 行と、実物の adapter の 0 件) | pre-commit(semgrep-hy・Python の semgrep)・日次 | `json.loads` を直に書いて dict を読む形は捕まえない(doeff-agents は他の用途で json を 24 file で使う)。語の綴りの規則で、意味の判定ではない |
| どの記録がターンを名乗るかは lines が決める | 型の集まり `lines.TurnRecord` と、handler が class を列挙しない形・検 `test_turn_records.hy`(ターンの id を必ず持つ記録と一致) | packages/doeff-codex/src/doeff_codex/lines.hy・handler.hy・tests/test_turn_records.hy | 手元の focused・日次 | 「必ず持つ」は既定値の無い欄 turn_id で判じる(欄の名を変えると外れる) |
| 終わったターンの遅れた記録を落とさない | handler の absorb と start-turn(late) | handler.hy | 検 test-headless-codex-background-command-result-reaches-a-tool-result-event-stub | 替え玉の拍で「走っているターンへ」と「late に取って次の頭へ」のどちらを通ったかを分けて測っていない |
| 道具の中身の形と閉じた語彙 | defwire(形の違う中身は Unparsed)・defenum ToolStatus・型の union ToolItem | lines.hy | 実行時の parse・型検査 | 型検査は日次の全体の回だけ(足した行に手元の doeff-hy-check の所見は 0) |
| doeff-codex は上の系を名指さない | semgrep `doeff-packages-have-no-application-vocabulary` | .semgrep.yaml | pre-commit・日次 | 語の一覧に無い語は捕まえない |

## 4. 実装の証拠

- 直す前の赤: `evidence/C-red-before-fix.log`(規則の検 = 断る行 0 件・後ろで走った命令の結果 0 件・TurnRecord の ImportError)。
- 直した後: `evidence/C-focused-after-fix.log`(doeff-codex の検・doeff-agents の codex の検・規則の検・台帳の検 68 passed)・
  `evidence/C-S3-after-fix-repeated.log`(5 回)・`evidence/C-S1-turn-record-mutation.log`(ItemDone を TurnRecord から外すと検が
  名指しで赤・戻すと緑)・`evidence/C-S1-blind-b-rejected-by-the-rule.log`・`evidence/C-S2-unknown-status-and-missing-field.log`
  (`evidence/probe_unknown_status.py.txt` — 撃った時の名は `.py`。中身は変えずに名だけを変えた)・
  `evidence/C-S1-scope-of-adding-tool-kinds.log`(道具の種類 3 つの追加は lines・adapter・替え玉と録り・検・文書だけで、handler・
  公開 effect・出来事の型は変わっていない)。
- 通常の変更時の経路: 規則は pre-commit の semgrep-hy と Python の semgrep(規則の paths が doeff-agents の src)。検は手元の focused と
  日次の全体の回。

## 5. 未確認と限界

- 盲検の model: 第 1 候補 gpt-6-astra / low は起動できず(codex が login していない)、opus / xhigh で代用した。effort が効いたかと
  実 model は未観測(自己申告だけ)— `blind/blind-meta.md`。
- 規則が捕まえない形: `json.loads` を直に書く形・層 2 の記録の欄の型を広げて生の値を運ぶ形(B の層 2 の半分)。後者は層 2 の型の
  変更として review で見る。
- late に取って次のターンの頭に積む道は、替え玉の拍でどちらを通ったかを区別して測っていない。
- 本物の上流(偽でない model)での道具の呼びと後ろで走る命令は走らせていない(録りは偽の上流と本物の binary)。
- 利用枠切れの検の揺れ(ターンを名乗らない記録の取りこぼし)はこの変更の外で、直していない。
- 盲検 A が残した作業木 `/work/toolset/worktrees/doeff-wt-ki77e7-blind-A`(detached・clean)は回収の手順に任せた。
