;;; Executable ADR for doeff-agentd's tmux/TUI boundary correction loops.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-002
  :title "agentd TUI boundary: bounded result solicitation + interactive-prompt watchdog"
  :status "accepted"
  :scope ["doeff-agentd" "agentd.monitor" "agentd.result-contract" "agentd.tui-boundary"]
  :problem
    [(fact
       "2026-07-04 01:55 JST、無人 codex attend session が rate-limit 確認メニュー(`Approaching rate limits / Switch to gpt-5.4-mini …? › 1. Switch… 2. Keep current model … Press enter to confirm`)で停止した。ハードブロックではなく確認メニューであり、Enter を押す者がいないだけで session が死んだ。"
       :evidence "ACP argus dashboard attend #1 capture; ~/.codex/sessions/2026/07/04/rollout-…019f28e8….jsonl")
     (fact
       "2026-07-03 17:06 UTC、acp:issue:argus-git-mergeability-refresh の impl agent が起動 20 秒で turn-end に達し、invocation が `session reached turn-end without reporting a result via report_result` で失敗した。制御プレーンは 6 分後に WorkItem を reap し、dedup condition が残って issue が永久に再宣言不能になった(dogfood e2e の実ブロッカー)。"
       :evidence "proboscis-agent-control-plane/.acp/event-store.sqlite events sequence 1963559..1965600")
     (fact
       "ACP の investigation-agent も独立に同一クラスを診断済み: 「実装エージェントが実装結果を返さず終了した」(acp:issue:issue-defect-bef7dcf947e98cdb)。missing-result は namespace を跨いで再発している。"
       :evidence "acp:issue:issue-defect-bef7dcf947e98cdb resourcePayload.body")
     (fact
       "codex のメニューは idle REPL プロンプトと同じ `› ` グリフで描画される(`output_has_agent_idle_prompt` は `\\n› ` にマッチし、メニュー行 `› 1. Switch…` もマッチする)。現行ヒューリスティックではブロッキングメニューと turn-end を区別できない。"
       :evidence "packages/doeff-agentd/src/main.rs codex_update_dialog_selected_option / output_has_agent_idle_prompt")
     (fact
       "turn-end で結果が無い場合は『agentd never re-prompts (hard rule 7 / ADR 0035 R4)』として即 fail していた。これは決定的失敗(schema-invalid payload, -32002)と観測(turn-end 時点で result が無い)の混同である。ADR 0035 が retries_used の再プロンプト経路を削除した履歴が示す通り、この区別は明文化しないと再び消される。"
       :evidence "packages/doeff-agentd/src/main.rs monitor_once turn-end branch (旧コメント)")
     (fact
       "`awaiting_response` latch はデーモン再起動時に一括クリアされる(main() の UPDATE)。bounded ループの回数を latch に載せると再起動で消える。回数は専用の永続カラムに置くしかない。"
       :evidence "packages/doeff-agentd/src/main.rs main() awaiting_response reset")
     (fact
       "idle プロンプトに一致しない停止画面(ログインプロンプト、pager、未知のダイアログ)は turn-end に到達せず、pane capture が変わらなくても last_observed_at は毎 tick 更新されるため、既存のどの watchdog も発火しない。session はスロットを永久に占有する。"
       :evidence "packages/doeff-agentd/src/main.rs monitor_once (last_observed_at は capture 成否のみで更新)")]
  :context
    [(interpretation
       "agentd が tmux/TUI 境界の唯一の所有者である。ピクセルとキー入力として現れるものはすべて agentd が観測・操作し、上流(doeff-agents / ACP)には型付き status と report_result データチャネルだけが見える。TUI 上の異常への対処を上流や人間に漏らすのは境界違反である。")
     (interpretation
       "ADR 0012 / ACP hard rule 7 の分類は agentd 内部にも適用される: 決定的失敗(schema-invalid payload)は自動再試行しない。観測(turn-end 時に result が無い、pane が凍結している)は永続カウンタ付き bounded ループで再観測・是正する。バウンド超過は型付き terminal failure で終わる — 無限待ちは常に禁止。")]
  :decision
    [(rule R1 "contract session が valid な報告結果なしに turn-end へ達した事象は観測であり、決定的失敗ではない。agentd は『report_result を今すぐ 1 回だけ呼べ』という是正メッセージを送信し、次の turn-end を再観測する(solicitation)。")
     (rule R2 "solicitation は永続・再起動生存のカウンタ(result_solicitations_used 列)で bounded(既定 2)。超過時は既存の terminal-without-result 失敗で終端し、ACP 4 値 discriminator は変えない。")
     (rule R3 "schema-invalid な report_result(-32002)は決定的なまま: 即時拒否し、同一 payload を自動再検証しない。ただしその後 valid な結果無しで turn-end に達すれば R1 の solicitation ループに入る(agent は payload を直して再報告できる)。")
     (rule R4 "solicitation 中の session status は non-terminal のまま。report_result はいつ着地しても result-first 読みで勝つ。solicitation 中に status を terminal にすることは禁止(-32003 で報告が弾かれるため)。")
     (rule R5 "startup 完了後の run_to_completion session で、pane 内容が T 秒(既定 180)不変・active-work マーカー無し・idle REPL プロンプト無しのとき、設定可能な小型 LLM judge に pane capture を渡し、strict JSON {blocked, keys, reason} を得る。blocked なら keys をホワイトリスト検証の上 tmux で送出する。試行は永続カウンタ(prompt_unblock_attempts 列)で bounded(既定 3)。")
     (rule R6 "turn-end-without-result の判定点では solicitation より先に judge を通す。codex メニューは idle プロンプトのグリフを描画するため、メニューへ solicitation を貼り付けると Enter が任意の選択肢を確定してしまう。")
     (rule R7 "stall 判定点(R5)で judge 超過・parse 失敗・judge 利用不能なら、型付きで loud に失敗する(status=failed、last_validation_error は 'interactive-prompt-blocked:' で始まり、TerminalCause は interactive_prompt_blocked)。無限待ちは禁止。turn-end 判定点(R6)で judge 利用不能なら solicitation へ degrade する(bounded であり、hang にはならない)。")
     (rule R8 "ACP 側 4 値 discriminator(ready / terminal-without-result / not-ready / session-gone)は不変。新しい失敗詳細は last_validation_error と TerminalCause(内部/監査用)で運ぶ。")
     (rule R9 "既知ダイアログの決定的 fast-path dismisser は許容されるが、唯一の機構にしてはならない(既知プロンプトの設定列挙だけで抑止する案は棄却済み)。一般経路は judge である。")]
  :laws
    [(law missing-result-is-observation-not-determinism
       :statement "turn_end_without_valid_result => bounded_solicitation before terminal_without_result"
       :counterexamples
         [(counterexample "turn-end で result が無い session を solicitation なしで即 failed にする")
          (counterexample "solicitation を『再試行』と呼んで hard rule 7 を理由に削除する(削除するなら本 ADR の supersede が先)")])
     (law invalid-payload-stays-deterministic
       :statement "schema_invalid_report_result => immediate_-32002 and no_automatic_revalidation_of_same_payload"
       :counterexamples
         [(counterexample "-32002 の場で agentd が同一 payload を再検証・再試行する")
          (counterexample "validation 失敗を solicitation カウンタ消費なしの自動 re-prompt に変える")])
     (law correction-counters-are-durable-and-bounded
       :statement "solicitation_count and unblock_attempts => dedicated_persistent_columns surviving daemon_restart"
       :counterexamples
         [(counterexample "カウンタを awaiting_response のような再起動時クリアされる latch に載せる")
          (counterexample "カウンタをメモリ上にだけ持ち、agentd 再起動で solicitation が無限に再開する")])
     (law solicitation-keeps-session-non-terminal
       :statement "during_solicitation => status_non_terminal so report_result_can_land"
       :counterexamples
         [(counterexample "solicitation 送信と同時に status を failed にして、届いた report_result が -32003 で弾かれる")])
     (law menu-glyph-is-not-turn-end
       :statement "turn_end_decision_point => judge_before_solicitation"
       :counterexamples
         [(counterexample "codex rate-limit メニューへ solicitation テキストを貼り、Enter が選択中オプションを確定する")
          (counterexample "idle プロンプト検出だけを根拠に pane が REPL であると断定する")])
     (law frozen-pane-terminates-typed
       :statement "pane_unchanged_beyond_T and not_active and not_idle_prompt => bounded_judge_then_typed_failure never_infinite_wait"
       :counterexamples
         [(counterexample "judge が利用不能なので何もせず session を running のまま放置する")
          (counterexample "judge を毎 tick 無制限に呼び続ける")])
     (law discriminator-stability
       :statement "new_failure_kinds => last_validation_error_and_terminal_cause not_new_discriminator_values"
       :counterexamples
         [(counterexample "interactive-prompt-blocked を ACP discriminator の第 5 値として追加する")])]
  :enforcement
    [(defsemgrep no-blanket-never-reprompts-claim
       :languages ["generic"]
       :pattern "agentd never re-prompts"
       :message "『agentd never re-prompts』という無限定の主張は ADR-DOE-AGENTS-002 で偽になった。決定的失敗(-32002)は再検証しない/turn-end の missing result は bounded solicitation、と限定して書くこと。"
       :bad ["// Deterministic failure — agentd never re-prompts (hard rule 7 / ADR 0035 R4)."]
       :good ["// Schema rejection is final for that payload (no automatic revalidation, ADR 0035 R4);\n// a missing result at turn-end enters the bounded solicitation loop (ADR-DOE-AGENTS-002 R1)."])]
  :plans ["docs/adr/defadr_doeff_agents_002_agentd_tui_boundary_contracts.hy"
          "packages/doeff-agentd/src/main.rs (monitor_once solicitation + prompt watchdog; cargo test)"])
