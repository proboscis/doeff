;;; Executable ADR: koine session surface v0 stage 1 — session.adopt +
;;; turn 打刻 + interactive/adopted 刈り取り免除(安全条項 1 の履行)。
;;;
;;; 改訂註(2026-08-17): 波 1-S1 / ADR-DOE-AGENTS-007 改訂・出自 =
;;; design-conversation-liveness-sessionhost-wave1-2026-08-17.html 付録 4-2 S1・
;;; 採番代行 = integration-lead〔暫定・koine 裁定 6f6a36d7 と同型〕。
;;; 会話 ID(conversation_id)を登録・打刻・行引きの第一級の鍵にする改訂 —
;;; R1(会話 index)/ R2(adopt の会話受理と冪等 3 分岐)/ R5(解決鍵の改組 —
;;; 名前鍵は素の descriptor 限定)/ R7(probe なし行引き口)+ 法 2 本
;;; (stamp-never-crosses-identities / conversation-lookup-never-probes)。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-007
  :title "koine session surface v0 stage 1 に sessionhost が第一実装として参加する — session.adopt(observation-only・実在確認→登記・会話 ID 受理)+ turn 打刻(descriptor 解決 = pane/conversation/素の名前・opaque wait)+ interactive/adopted の無条件刈り取り免除(安全条項 1)+ level-triggered liveness 導出 + 会話 ID 行引き口(波 1-S1 改訂)"
  :status "accepted"
  :scope ["packages/doeff-agents (sessionhost: store / effects / policy / host / adopt / turn)"
          "packages/doeff-agents/conformance (S23-S27)"]
  :problem
    [(fact
       "「今この系で誰が動いているか」に一枚で答える台帳の契約 = koine session surface v0(pavo ADR 0003 v2 の interface-first 裁定)。sessionhost は store・monitor・lifecycle enum・substrate 抽象(tmux/herdr)を実装済みで、欠けているのは adopt(既に生きている pane の事後登記)と turn 打刻のみだった。"
       :evidence "~/repos/koine/surfaces/session/semantics-v0.md; ~/repos/pavo/adr/0003-runtime-unification.md R3/R6")
     (fact
       "安全条項 1(reaper fail-closed・interactive は無条件刈り取り対象外)は実装前の sessionhost で red だった: is-run-to-completion の使用箇所は 4 つ(finalize の pane kill / result-first / turn-end / stall watchdog)のみで、interactive 行を terminal 化する経路が 5 本残っていた — boot watchdog(policy.hy 旧 472-488)/ stale reap(旧 492-505)/ launch timeout(旧 507-524)/ mux 生存確認(旧 526-550)/ zombie reaper(旧 552-568)。"
       :evidence "packages/doeff-agents/conformance/test_s26_interactive_not_reaped.py(TDD red 2026-07-21: 4 経路が interactive 行を terminal 化することを実測)")
     (fact
       "launch timeout 経路は adopt と致命的に干渉する: adopt は観測のみで startup marker を見ないため、adopt 行は「status=running・observed_active_at=None」そのもの — 登記の 60 秒後(既定 knob)に必ず failed 化し、store の terminal guard が再活性を禁止するため、行が死んで pane だけ生き残る孤児化になる。adopt だけを足すと『登記が席を殺す』。"
       :evidence "~/repos/koine/docs/turn-stamp-path.md 突合結果(重大発見の登記 2026-07-21); packages/doeff-agents/src/doeff_agents/sessionhost/store.hy db-upsert-snapshot terminal guard")
     (fact
       "ownership marker 列が存在しなかった(agent_sessions に owner/creator/adopted 列なし・owner_pid は daemon lease 用)ため、条文の opt-in marker + fail-closed 自体が表現できなかった。"
       :evidence "packages/doeff-agents/tests/sessionhost_store_deftests.hy EXPECTED-COLUMNS(TDD red 2026-07-21)")
     (fact
       "会話 ID が対話席用台帳に一切乗っていなかった(32 行中 0 行 — 仕事用は 8,570/12,078 行が持つ)ため、agora 側の読み手(全部が会話 ID 鍵)は台帳を引けず、打刻の到達率は 14.9%(不達 12,245 vs 到達 2,138)だった。さらに名前第二鍵の吸い込み実弾: 死んだ行 integration-lead(旧枠・登録 2026-07-21 の手動一発)が現役席の打刻を吸収し、turn_since が観測中に 3 回前進 — 死んだ会話の行が生きて見える形で読みを腐らせた。一覧の読み出しは 2.3〜4.8 秒/32 行(鏡原則の行ごと同期 probe が原因)で、拍ごとに読む消費者には載らない。"
       :evidence "design-conversation-liveness-sessionhost-wave1-2026-08-17.html 付録 4-3(read-only 実測 4 本: agentd-herdr.sqlite 直読・daemon.status counter・socket 実測)")]
  :context
    [(interpretation
       "契約の正本は koine(github.com/proboscis/koine surfaces/session/semantics-v0.md)であり、本 ADR は実装側変更の登記(cross-repo 原則 — pavo ADR 0003 R8)。sessionhost は surface v0 の subset(session のみ)の第一実装として参加する。安全条項 4 点は契約条文で後退禁止(R4)。")
     (interpretation
       "turn 打刻の書き込み経路は koine docs/turn-stamp-path.md が正本: 書き手は席自身の hook のみ(per-source 単一 writer)、hook は生 socket へ 1 行書いて応答を待たず閉じる(hard timeout ≤200ms・fail-open)。受け側の義務 = hung を作らないこと。席は session id を知らない — descriptor {pane_id, agent_name} を sessionhost が解決し、id 配布機構(env/file)は作らない。")
     (interpretation
       "liveness は導出であって状態ではない: stalled = open turn(holder='agent')のまま閾値超過、を wire 出力のたびに再導出する。turn-open の被覆は部分的(harness 内部再起動起点では発火しないことがある — 所見 3)なので、open/close の対を前提にした edge-triggered 実装は禁止。close 済み(WAIT 待ち)は経過時間によらず非 stalled — 待つのは agora の正常状態。")
     (interpretation
       "波 1-S1 改訂(2026-08-17)の意味論の出自: 会話は宿りより長生きし(ADR-DOE-AGENTS-006 conversation-outlives-incarnation)、行は会話の 1 回の宿りの記録である。よって (1) 会話 ID は行の同一性を運ぶ鍵 — 打刻・行引きは conversation_json.session_id の完全一致でのみ引く(全欄横断の部分一致は『言及』を『登記』と読む)。(2) 下地(pane)の再利用で別会話が同じ substrate.ref に現れたら、それは新しい会話の新しい宿り = 新しい行であり、既存行の会話 identity を書き換えることは無い(store の COALESCE first-write-wins が最終防衛)。(3) 設計 S1 ③ の『名前解決は下地実在行に限定』は、打刻 hot path の substrate 不接触義務(既存法 turn-rpc-is-store-only — 打刻のたびの probe は反例に明文)と両立しないため、同値の store-only 機構で履行する: 識別子(pane_id / conversation_id)を運ぶ descriptor は名前鍵に落ちない。識別子鍵で引けない打刻は正直な no-op(unadopted counter)であり、登録の定期化(波 1 S2 便)が次の拍で行を供給する。")]
  :decision
    [(rule R1 "sessionhost は koine session surface v0 の第一実装として参加する(subset = session のみ・契約の正本は koine)。schema 追加は additive migration の 4 列のみ: adopted(ownership marker・INTEGER NOT NULL DEFAULT 0)/ turn_holder / turn_since / turn_wait_json(opaque)。波 1-S1 改訂: 会話 ID の行引きのための expression index(idx_agent_sessions_conversation — json_extract(conversation_json, '$.session_id'))を additive に足す。列は増やさない — conversation_json は ADR-DOE-AGENTS-006 所有のまま(会話 identity の第 2 の置き場を作らない)。wire は既存封筒に additive(session.adopt / session.turn_open / session.turn_close / session.by_conversation / daemon.status counters / session.list の adopted filter)。koine 由来の新契約は typed 文字列 error_code(adopt_target_not_found)を使う — oracle の数値表(-32000..)は凍結語彙で、新語彙をそこへ足さない。")
     (rule R2 "adopt は observation-only + 順序義務: substrate 実在確認(既存 TmuxHasSession effect — herdr backend では agent.get に解決される substrate 中立 probe)が成功したときのみ登記し、失敗は行を作らず typed error で返す(幻 turn-open の再発防止)。substrate への変異(キー送出・session 作成/破棄・FS 書き・配送)は adopt 経路で構造的に禁止(installed semgrep rule doeff-agents-adopt-must-not-mutate-substrate)。id は sessionhost 採番の不透明 id(uuid4)— 呼び手の名を id に埋め込まない。波 1-S1 改訂: adopt は任意の conversation_id(非空文字列)を受け、conversation_json へ {\"session_id\": <id>}(ADR-006 union の identity 成分)として書く。冪等は会話を考慮した 3 分岐 — (a) 同一 substrate.ref の非終端行に同じ会話が登記済み → 既存行をそのまま返す(書かない)。(b) 会話未登記(NULL)の行 → 会話を補充して返す(同一宿りの登記の完成 — store の COALESCE first-write-wins が最終防衛・event = session_conversation_discovered)。(c) 既存行が別の会話を登記 → 新しい行を鋳造する(下地再利用 = 新会話の新宿り。既存行の会話 identity は決して書き換えない)。conversation_id を運ばない adopt は従来どおり(同一 ref の非終端の最新行を返す)。")
     (rule R3 "刈り取り免除は monitor-session-once の冒頭(booting arm より前)で判定する: 免除 = adopted OR 非 run_to_completion(interactive、および未知 lifecycle は fail-closed で刈らない — reap は run_to_completion の opt-in)。免除行に monitor がしてよいのは観測の記帳のみで、last_observed_at の更新を『monitor は生きて評価した上で刈らなかった』の witness として書く(S26 が assert する観測面)。status の terminal 遷移・finished_at/terminal_cause 書き込み・pane kill・solicitation 送出は禁止。installed semgrep rule doeff-agents-interactive-must-not-be-terminalized が『免除判定より前に :status terminal を書く形』を旧形として禁止する(旧 policy.hy で fire することを確認済み 2026-07-21)。")
     (rule R4 "liveness は level-triggered 導出のみ: stalled = (turn_holder == 'agent') AND (now - turn_since > DOEFF_AGENTD_TURN_STALL_SECS、既定 1800)。store に stalled を書かない・status を変えない(signal only)。導出は wire 出力時(session.get / session.list / session.adopt の応答)に毎回行う。close 済み(holder = wait.who = user/work 等)は経過時間によらず非 stalled。鏡原則(条項 3)も同じ読み出し導出面: 免除・非終端行の wire に substrate_present / substrate_checked_at を毎回 probe して載せ、消滅 pane を exited と裁定せず・行を削除もしない。")
     (rule R5 "turn RPC は substrate に一切触れない: descriptor 解決 + 3 列 UPDATE の単一 actor op のみ。≤200ms fire-and-forget の hook hot path が相手なので、hung を作らないことは受け側の条文義務(installed semgrep rule doeff-agents-turn-rpc-must-not-touch-substrate)。波 1-S1 改訂の解決鍵(descriptor = {pane_id, agent_name, conversation_id} — すべて任意・最低 1 つ): ① pane_id 第一鍵 — descriptor が conversation_id を運ぶ時は、別の会話を登記した行に落ちない(会話一致行 > 会話未登記〔NULL〕行の優先・別会話行は除外)。② conversation_id 第二鍵 — conversation_json.session_id の完全一致で会話の非終端最新行へ(pane 未登記の復活直後窓でも打刻が届く)。③ agent_name 鍵は descriptor が pane_id も conversation_id も運ばない時に限る(installed semgrep rule doeff-agents-turn-name-key-requires-bare-descriptor)— 識別子を運ぶ打刻の名前 fallback は吸い込み実弾(2026-08-17: 死んだ行が現役席の打刻を吸収)の形そのもの。候補が複数なら started_at DESC, session_id ASC の先頭(session.list と同じ全順序)。対象は従来どおり adopt 済み非終端行のみ。未 adopt / 識別子不一致の打刻は正直 no-op + 可視 counter(daemon.status counters.turn_stamp_unadopted — adopt 網羅の計器を兼ねる)で、エラーにも黙殺にもしない。wait は opaque 保存(読むのは holder に写す who のみ — kind 語彙の解釈権威は席側 wait_protocol.py)。turn RPC は監査 event / command を書かない(打刻は高頻度 hot path — 表の無限成長と遅延源を持ち込まない)。")
     (rule R6 "既知限界の明示(条項 4 の裏面): 打刻は自己申告 — 『動いているが未打刻』の席は turn/stalled 面では不可視になり得る。wire は未打刻を field 省略(turn_* 不在)で正直に表現し、偽の open turn を合成しない。boot watchdog(BOOTING 行)は launch pipeline 所有のため、免除判定はそれより前に置くことで interactive の BOOTING 残置も刈らない(launcher 死亡時の BOOTING interactive 行は残置される — stage 2 以降の突合表示課題として登記)。counters は in-memory(永続化は実測需要が出てから)。")
     (rule R7 "会話 ID → 行の行引き口(波 1-S1 改訂・wire method session.by_conversation {conversation_id})は probe なしの速い読み: 解決則 = 非終端の最新行、無ければ最新の terminal 行、無ければ null(全順序 = started_at DESC, session_id ASC — 会話資源契約草案の解決則〔conversationId → 生きた宿り高々 1、無ければ最新 terminal 宿り〕の store 面)。応答は wire snapshot + stalled 導出(store 読みのみ)で、substrate_present / substrate_checked_at は意図的に**不在**(stale な保存値を返すのではなく、field ごと載せない — 読み手は自分の substrate 観測と合成して生死を導出する。一覧 2.3〜4.8s/32 行の原因だった行ごと同期 probe をこの口に持ち込まない)。鏡原則(R4)は session.get / session.list / session.adopt の応答で不変 — この口はその代替ではなく、拍ごとに読む消費者(供給点)専用の素材読みである。adopted に限らない(会話 identity は台帳全域 — launch 起点の行も引ける)。")]
  :laws
    [(law interactive-rows-are-never-reaped
       :statement "monitor_terminalization => only_run_to_completion_rows; adopted_or_non_rtc_rows_never_transition_to_terminal_by_monitor"
       :counterexamples
         [(counterexample "launch timeout / stale reap / zombie / mux 消滅 / boot watchdog のいずれかの arm が lifecycle=interactive または adopted=1 の行を failed/exited にする")
          (counterexample "免除判定を booting arm や stale arm の後ろに置き、先行 arm が interactive 行に到達できる順序にする")])
     (law adopt-verifies-then-registers
       :statement "adopt => substrate_existence_check_precedes_upsert; failed_adopt_leaves_no_row; adopt_never_mutates_substrate"
       :counterexamples
         [(counterexample "行を先に upsert してから実在確認し、失敗時に行を消す(消し損ねが幻 turn-open になる)")
          (counterexample "adopt 中に TmuxSendKeys / TmuxNewSession / TmuxKillSession / FsWriteTextAtomic / DeliverMessage を呼ぶ")])
     (law ledger-mirrors-reality
       :statement "vanished_substrate_of_exempt_row => derived_divergence_display_only; never_terminalize_never_delete"
       :counterexamples
         [(counterexample "免除行の pane 消滅を検知して行を exited にする・行を DELETE する")
          (counterexample "substrate_present を列として保存し、現実でなく保存値を wire に返す")])
     (law liveness-is-level-triggered-derivation
       :statement "stalled => derived_at_read_time_from_turn_holder_and_turn_since; never_stored_never_mutates_status; closed_turn_never_stalled"
       :counterexamples
         [(counterexample "stalled を列や status 値として store に書く")
          (counterexample "turn-open event の受信をトリガに stalled タイマーを張る(open 欠落で永久に沈黙する edge-triggered)")
          (counterexample "close 済み(WAIT 待ち)の行を経過時間で stalled 化する")])
     (law turn-rpc-is-store-only
       :statement "turn_stamp_path => row_lookup_plus_update_in_single_actor_op; no_substrate_effects_no_subprocess_no_delivery"
       :counterexamples
         [(counterexample "turn_close で pane を capture して WAIT 文字列を再 parse する(解釈権威の複製 = 通訳 5 号)")
          (counterexample "打刻のたびに substrate を probe して行の鮮度を『確認』してから書く(≤200ms 契約の遅延源)")])
     (law stamp-never-crosses-identities
       :statement "identity_bearing_stamp => lands_only_on_identity_compatible_row; name_key_requires_bare_descriptor; miss_is_honest_noop_with_counter"
       :counterexamples
         [(counterexample "pane_id を運ぶ打刻が pane 不一致・名前一致の行に落ちる(吸い込み実弾 2026-08-17: 死んだ integration-lead 行が現役席の打刻を吸収し turn_since が前進 — 死んだ会話の行が生きて見える)")
          (counterexample "conversation_id を運ぶ打刻が、別の会話を登記した pane 一致行に落ちる(下地再利用の窓で他会話の記録を汚す)")
          (counterexample "識別子鍵の空振りを黙って落とす、またはエラーにする(正直 no-op + counter が契約 — 計器 = adopt 網羅の測度)")])
     (law conversation-lookup-never-probes
       :statement "session_by_conversation => store_select_plus_derived_stalled_only; substrate_presence_fields_absent_not_stale; resolution_is_newest_active_else_newest_terminal"
       :counterexamples
         [(counterexample "行引きの応答のために substrate へ同期 probe を撃つ(一覧 2.3〜4.8s/32 行の遅さをこの口へ再輸入する)")
          (counterexample "substrate_present を保存値・古い写しから合成して載せる(不在は field 不在で正直に — 読み手の縮退語彙 unknown を潰す)")
          (counterexample "conversation_json の部分一致・全欄横断の照合で行を引く(『言及』を『登記』と読む — 同一性欄の完全一致のみ)")])]
  :enforcement
    [(deftest test-adr-doe-agents-007-reap-exemption-covers-interactive-and-adopted
       ;; 免除述語の機械面: adopted と非 run_to_completion(interactive・未知
       ;; lifecycle = fail-closed)が免除され、素の run_to_completion のみが
       ;; reap の opt-in に残る。
       (import doeff_agents.sessionhost.effects [SessionRow])
       (import doeff_agents.sessionhost.policy [reap-exempt])
       (defn mk [lifecycle adopted]
         (SessionRow :session-id "adr7" :session-name "adr7" :pane-id "%0"
                     :agent-type "claude" :lifecycle lifecycle :status "running"
                     :started-at "2026-07-21T00:00:00+00:00" :adopted adopted))
       (assert (is (reap-exempt (mk "interactive" False)) True))
       (assert (is (reap-exempt (mk "run_to_completion" True)) True))
       (assert (is (reap-exempt (mk "interactive" True)) True))
       (assert (is (reap-exempt (mk "future_lifecycle" False)) True))
       (assert (is (reap-exempt (mk "run_to_completion" False)) False)))
     (defsemgrep adopt-observation-only
       "doeff-agents-adopt-must-not-mutate-substrate"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/adopt.hy"
         "source" "(defk adopt-program [params]\n  (<- _ (tmux-send-keys pane-id \"Enter\" False False))\n  row)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/adopt.hy"
         "source" "(defk adopt-program [params]\n  (<- alive (tmux-has-session session-name))\n  (<- _ (session-store-upsert row))\n  row)\n"}])
     (defsemgrep interactive-never-terminalized
       "doeff-agents-interactive-must-not-be-terminalized"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(defk monitor-session-once [row knobs]\n  (when (= row.status \"booting\")\n    (setv row (replace row :status \"failed\"))\n    (return row))\n  row)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
         "source" "(defk monitor-session-once [row knobs]\n  (when (reap-exempt row)\n    (return row))\n  (when stale\n    (setv row (replace row :status \"failed\")))\n  row)\n"}])
     (defsemgrep turn-rpc-store-only
       "doeff-agents-turn-rpc-must-not-touch-substrate"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy"
         "source" "(deff db-turn-stamp [conn pane-id]\n  (<- alive (tmux-has-session session-name))\n  None)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy"
         "source" "(deff db-turn-stamp [conn pane-id]\n  (.execute conn \"UPDATE agent_sessions SET turn_holder = ? WHERE session_id = ?\")\n  None)\n"}])
     (deftest test-adr-doe-agents-007-stamp-never-crosses-identities
       ;; 法 stamp-never-crosses-identities の機械面(波 1-S1 改訂・実 SQLite):
       ;; 識別子(pane / conversation)を運ぶ打刻は名前鍵に落ちない・
       ;; pane 鍵は別会話を名乗る行に落ちない・素の名前 descriptor だけが
       ;; 名前鍵を使える。吸い込み実弾(2026-08-17)の regression pin。
       (import sqlite3)
       (import doeff_agents.sessionhost.store [db-migrate])
       (import doeff_agents.sessionhost.turn [db-resolve-turn-target])
       (setv conn (sqlite3.connect ":memory:"))
       (db-migrate conn)
       (defn seed [sid name pane conv]
         (.execute conn
           (+ "INSERT INTO agent_sessions (session_id, session_name, pane_id, "
              "agent_type, work_dir, status, backend_kind, backend_ref_json, "
              "started_at, adopted, conversation_json) "
              "VALUES (?, ?, ?, 'claude', '', 'running', 'tmux', '{}', "
              "'2026-08-17T00:00:00+00:00', 1, ?)")
           #(sid name pane conv)))
       ;; 実弾の形: 死んだ行(旧 pane)と同名の現役席が別 pane から打刻する
       (seed "dead-row" "integration-lead" "%old-pane" None)
       (assert (is (db-resolve-turn-target conn "%live-pane" "integration-lead" None)
                   None))
       (assert (is (db-resolve-turn-target conn "%live-pane" "integration-lead" "c-new")
                   None))
       ;; 素の名前 descriptor だけが名前鍵に届く
       (assert (= (db-resolve-turn-target conn None "integration-lead" None)
                  "dead-row"))
       ;; pane 鍵は別会話を名乗る行に落ちない・会話一致なら届く
       (seed "conv-row" "seat-c" "%pane-c" "{\"session_id\":\"c1\"}")
       (assert (is (db-resolve-turn-target conn "%pane-c" None "c2") None))
       (assert (= (db-resolve-turn-target conn "%pane-c" None "c1") "conv-row"))
       ;; conversation 第二鍵: pane 未登記でも会話の行へ届く
       (assert (= (db-resolve-turn-target conn "%moved" None "c1") "conv-row"))
       (.close conn))
     (defsemgrep turn-name-key-requires-bare-descriptor
       "doeff-agents-turn-name-key-requires-bare-descriptor"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy"
         "source" "(deff db-resolve-turn-target [conn pane-id agent-name conversation-id]\n  (when (is-not agent-name None)\n    (setv row (.fetchone (.execute conn base-name-sql)))\n    (when (is-not row None) (return (get row 0))))\n  None)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy"
         "source" "(deff db-resolve-turn-target [conn pane-id agent-name conversation-id]\n  (when (and (is pane-id None) (is conversation-id None) (is-not agent-name None))\n    (setv row (.fetchone (.execute conn base-name-sql)))\n    (when (is-not row None) (return (get row 0))))\n  None)\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_007_koine_session_surface.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(additive 4 列 + adopted filter + 会話 index + db-session-by-conversation〔波 1-S1〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(reap-exempt 免除 arm + turn-stalled 導出)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/adopt.hy(adopt program — observation-only + 会話受理と冪等 3 分岐〔波 1-S1〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy(descriptor 解決 = pane/conversation/素の名前 + 3 列 UPDATE〔波 1-S1〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(RPC dispatch + counters + wire 導出 + session.by_conversation〔波 1-S1〕)"
          "packages/doeff-agents/conformance/test_s23..s27(black-box 検定 — 将来 koine 側へ移設可能。S23/S25 は波 1-S1 改訂を pin)"])
