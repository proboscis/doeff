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
;;;
;;; 改訂註 2(2026-08-17・幽霊 running 行の根治): 出自 =
;;; design-sessionhost-adopted-reconciler-2026-08-17.html(integration-lead
;;; 検分合格・条件 2 = 供給断 skip / 起動猶予・波 1 席と語彙突合 1 往復完了)。
;;; adopted 行の終端の書き手の不在(R3 の設計どおり)が幽霊 running 行の
;;; 単調増加を生んだ(実測 2026-08-17: 台帳 running/adopted 152 vs herdr
;;; 生存 88・幽霊 63・1 時間弱で +20 増)ため、終端追随の書き手を宣言的
;;; reconciler 1 本だけ新設する — R8(reconciler)/ R9(語彙: 行の終端印 =
;;; 宿りの終端・会話の ended はどの記帳からも導出しない)/ R10(adopted 行への
;;; cancel / cleanup の typed 拒否)+ 法 3 本(transient-absence-never-
;;; terminalizes / rename-revival-links-successor-not-end / reconciler-
;;; observes-never-mutates-substrate)+ 既存法 2 本の改訂。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-007
  :title "koine session surface v0 stage 1 に sessionhost が第一実装として参加する — session.adopt(observation-only・実在確認→登記・会話 ID 受理)+ turn 打刻(descriptor 解決 = pane/conversation/素の名前・opaque wait)+ interactive/adopted の無条件刈り取り免除(安全条項 1)+ level-triggered liveness 導出 + 会話 ID 行引き口(波 1-S1 改訂)+ adopted 行 reconciler(終端追随の単一書き手 — 幽霊 running 行の根治・2026-08-17 改訂 2)"
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
       "終端の書き手の不在(R3 の設計どおり)の帰結として、herdr 側の席の消滅・改名(R55 鋳造名化)に adopted 行が追随せず、幽霊 running 行が単調増加した。実測(2026-08-17 read-only 突合): 台帳 running/adopted 152 行 vs herdr 生存 agent 88(会話 ID 保有 86)— 生存一致 87・幽霊(痕跡なし)63・同一性不確かの保留 2・同一会話の二重 running 1 組。増加率 = 1 時間弱で +20 行(134→154)。実弾 = coupling-core-review(w3R:p0・7/21 一括登記・会話 ID なし世代)— pane 消滅後 8/3 に s-c137bf0bab へ改名復活したが行は running のまま残置。launch 容量の恒久拒否(PR #591 で母数側を修理)と読み手 4 系統の偽 live の根はこの台帳の腐りにある。"
       :evidence "design-sessionhost-adopted-reconciler-2026-08-17.html 付録 4-4(read-only 突合 probe・agentd-herdr.sqlite mode=ro + herdr agent.list)")
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
       "改訂 2(reconciler)の語彙(波 1 席 s-789abbc5fe と突合 1 往復完了 2026-08-17): 行の終端印は宿り(1 回の実行体)の終端記帳であって、会話 4 値(live/dormant/ended/unknown — M0① 会話契約 v0)の ended へは決して写像しない。ended の唯一の源 = agora の会話終端簿(D567)— 不変量 end-is-recorded-not-derived(終端は第一級の記帳・宿りの不在から『終わった』を導出しない)を reconciler は破らない: reconciler が終端した行の会話は読み手の導出で dormant に落ちる(dormant の導出は許容 — 禁じるのは ended の導出のみ)。unknown と dormant の弁別 = 『下地の観測が成立したか』(読めない ≠ 不在)— 供給断の周期に不在を記帳しない条件①はこの弁別の書き込み面。判定できない間の倒れ先は常に非終端のまま(= 読み手の unknown/dormant 側)であって終端側ではない(D566 — 一過性不在を『会話終了の機械証明』に使った実弾の pin)。")
     (interpretation
       "波 1-S1 改訂(2026-08-17)の意味論の出自: 会話は宿りより長生きし(ADR-DOE-AGENTS-006 conversation-outlives-incarnation)、行は会話の 1 回の宿りの記録である。よって (1) 会話 ID は行の同一性を運ぶ鍵 — 打刻・行引きは conversation_json.session_id の完全一致でのみ引く(全欄横断の部分一致は『言及』を『登記』と読む)。(2) 下地(pane)の再利用で別会話が同じ substrate.ref に現れたら、それは新しい会話の新しい宿り = 新しい行であり、既存行の会話 identity を書き換えることは無い(store の COALESCE first-write-wins が最終防衛)。(3) 設計 S1 ③ の『名前解決は下地実在行に限定』は、打刻 hot path の substrate 不接触義務(既存法 turn-rpc-is-store-only — 打刻のたびの probe は反例に明文)と両立しないため、同値の store-only 機構で履行する: 識別子(pane_id / conversation_id)を運ぶ descriptor は名前鍵に落ちない。識別子鍵で引けない打刻は正直な no-op(unadopted counter)であり、登録の定期化(波 1 S2 便)が次の拍で行を供給する。")]
  :decision
    [(rule R1 "sessionhost は koine session surface v0 の第一実装として参加する(subset = session のみ・契約の正本は koine)。schema 追加は additive migration の 4 列のみ: adopted(ownership marker・INTEGER NOT NULL DEFAULT 0)/ turn_holder / turn_since / turn_wait_json(opaque)。波 1-S1 改訂: 会話 ID の行引きのための expression index(idx_agent_sessions_conversation — json_extract(conversation_json, '$.session_id'))を additive に足す。列は増やさない — conversation_json は ADR-DOE-AGENTS-006 所有のまま(会話 identity の第 2 の置き場を作らない)。wire は既存封筒に additive(session.adopt / session.turn_open / session.turn_close / session.by_conversation / daemon.status counters / session.list の adopted filter)。koine 由来の新契約は typed 文字列 error_code(adopt_target_not_found)を使う — oracle の数値表(-32000..)は凍結語彙で、新語彙をそこへ足さない。")
     (rule R2 "adopt は observation-only + 順序義務: substrate 実在確認(既存 TmuxHasSession effect — herdr backend では agent.get に解決される substrate 中立 probe)が成功したときのみ登記し、失敗は行を作らず typed error で返す(幻 turn-open の再発防止)。substrate への変異(キー送出・session 作成/破棄・FS 書き・配送)は adopt 経路で構造的に禁止(installed semgrep rule doeff-agents-adopt-must-not-mutate-substrate)。id は sessionhost 採番の不透明 id(uuid4)— 呼び手の名を id に埋め込まない。波 1-S1 改訂: adopt は任意の conversation_id(非空文字列)を受け、conversation_json へ {\"session_id\": <id>}(ADR-006 union の identity 成分)として書く。冪等は会話を考慮した 3 分岐 — (a) 同一 substrate.ref の非終端行に同じ会話が登記済み → 既存行をそのまま返す(書かない)。(b) 会話未登記(NULL)の行 → 会話を補充して返す(同一宿りの登記の完成 — store の COALESCE first-write-wins が最終防衛・event = session_conversation_discovered)。(c) 既存行が別の会話を登記 → 新しい行を鋳造する(下地再利用 = 新会話の新宿り。既存行の会話 identity は決して書き換えない)。conversation_id を運ばない adopt は従来どおり(同一 ref の非終端の最新行を返す)。")
     (rule R3 "刈り取り免除は monitor-session-once の冒頭(booting arm より前)で判定する: 免除 = adopted OR 非 run_to_completion(interactive、および未知 lifecycle は fail-closed で刈らない — reap は run_to_completion の opt-in)。免除行に monitor がしてよいのは観測の記帳のみで、last_observed_at の更新を『monitor は生きて評価した上で刈らなかった』の witness として書く(S26 が assert する観測面)。status の terminal 遷移・finished_at/terminal_cause 書き込み・pane kill・solicitation 送出は禁止。installed semgrep rule doeff-agents-interactive-must-not-be-terminalized が『免除判定より前に :status terminal を書く形』を旧形として禁止する(旧 policy.hy で fire することを確認済み 2026-07-21)。改訂 2(2026-08-17): この禁止は monitor 経路の禁止であって、adopted 行が永遠に終端しないことの約束ではない — 免除行(adopted)の終端の書き手は R8 の reconciler 1 本のみが正規に持つ(monitor の全腕・adopt・turn・RPC からの終端は引き続き禁止)。")
     (rule R4 "liveness は level-triggered 導出のみ: stalled = (turn_holder == 'agent') AND (now - turn_since > DOEFF_AGENTD_TURN_STALL_SECS、既定 1800)。store に stalled を書かない・status を変えない(signal only)。導出は wire 出力時(session.get / session.list / session.adopt の応答)に毎回行う。close 済み(holder = wait.who = user/work 等)は経過時間によらず非 stalled。鏡原則(条項 3)も同じ読み出し導出面: 免除・非終端行の wire に substrate_present / substrate_checked_at を毎回 probe して載せ、消滅 pane を exited と裁定せず・行を削除もしない。")
     (rule R5 "turn RPC は substrate に一切触れない: descriptor 解決 + 3 列 UPDATE の単一 actor op のみ。≤200ms fire-and-forget の hook hot path が相手なので、hung を作らないことは受け側の条文義務(installed semgrep rule doeff-agents-turn-rpc-must-not-touch-substrate)。波 1-S1 改訂の解決鍵(descriptor = {pane_id, agent_name, conversation_id} — すべて任意・最低 1 つ): ① pane_id 第一鍵 — descriptor が conversation_id を運ぶ時は、別の会話を登記した行に落ちない(会話一致行 > 会話未登記〔NULL〕行の優先・別会話行は除外)。② conversation_id 第二鍵 — conversation_json.session_id の完全一致で会話の非終端最新行へ(pane 未登記の復活直後窓でも打刻が届く)。③ agent_name 鍵は descriptor が pane_id も conversation_id も運ばない時に限る(installed semgrep rule doeff-agents-turn-name-key-requires-bare-descriptor)— 識別子を運ぶ打刻の名前 fallback は吸い込み実弾(2026-08-17: 死んだ行が現役席の打刻を吸収)の形そのもの。候補が複数なら started_at DESC, session_id ASC の先頭(session.list と同じ全順序)。対象は従来どおり adopt 済み非終端行のみ。未 adopt / 識別子不一致の打刻は正直 no-op + 可視 counter(daemon.status counters.turn_stamp_unadopted — adopt 網羅の計器を兼ねる)で、エラーにも黙殺にもしない。wait は opaque 保存(読むのは holder に写す who のみ — kind 語彙の解釈権威は席側 wait_protocol.py)。turn RPC は監査 event / command を書かない(打刻は高頻度 hot path — 表の無限成長と遅延源を持ち込まない)。")
     (rule R6 "既知限界の明示(条項 4 の裏面): 打刻は自己申告 — 『動いているが未打刻』の席は turn/stalled 面では不可視になり得る。wire は未打刻を field 省略(turn_* 不在)で正直に表現し、偽の open turn を合成しない。boot watchdog(BOOTING 行)は launch pipeline 所有のため、免除判定はそれより前に置くことで interactive の BOOTING 残置も刈らない(launcher 死亡時の BOOTING interactive 行は残置される — stage 2 以降の突合表示課題として登記)。counters は in-memory(永続化は実測需要が出てから)。")
     (rule R7 "会話 ID → 行の行引き口(波 1-S1 改訂・wire method session.by_conversation {conversation_id})は probe なしの速い読み: 解決則 = 非終端の最新行、無ければ最新の terminal 行、無ければ null(全順序 = started_at DESC, session_id ASC — 会話資源契約草案の解決則〔conversationId → 生きた宿り高々 1、無ければ最新 terminal 宿り〕の store 面)。応答は wire snapshot + stalled 導出(store 読みのみ)で、substrate_present / substrate_checked_at は意図的に**不在**(stale な保存値を返すのではなく、field ごと載せない — 読み手は自分の substrate 観測と合成して生死を導出する。一覧 2.3〜4.8s/32 行の原因だった行ごと同期 probe をこの口に持ち込まない)。鏡原則(R4)は session.get / session.list / session.adopt の応答で不変 — この口はその代替ではなく、拍ごとに読む消費者(供給点)専用の素材読みである。adopted に限らない(会話 identity は台帳全域 — launch 起点の行も引ける)。")
     (rule R8 "adopted 行の終端追随の書き手 = 宣言的 reconciler 1 本のみ(sessionhost/reconcile.hy・走らせ場 = daemon 内 monitor-loop の独立周期・既定 60s)。突合素材 = 新 substrate effect TmuxListSessions(herdr backend: agent.list 1 RPC — 名前・pane・会話 ID〔agent_session kind=id の value のみ・agmsg / ai_queue の agent_sid と同一導出〕/ tmux backend: list-panes -a・会話 ID なし)を周期あたり 1 回だけ観測する — 行ごとの probe 連打(一覧 2.3〜4.8s/32 行の病)を持ち込まない。同一性照合は stamp-never-crosses-identities と同軸: 会話 ID 第一鍵(一致 = 生存・pane 違いは alive_moved)・会話なし世代は name+pane の縮退鍵(同 pane の別名 = R55 改名として追随)・弁別不能は保留(何も書かない)。書き込みは 4 種の guarded UPDATE のみ — ①presence(streak クリア + 改名追随 = session_name 更新・event session_renamed)②absence(substrate_absent_since first-write-wins + substrate_absent_checks 加算)③supersede(同一会話の非終端の後継行が実在する時のみ: status=exited + terminal_cause=superseded〔retryable=false・凍結表へ追加〕+ successor_session_id + event session_superseded)④vanish(checks ≥ 3〔knob〕 ∧ now−since ≥ 900s〔knob〕 ∧ 会話の live 痕跡なし ∧ 同一会話の非終端の別行なし: status=exited + terminal_cause=vanished + event session_vanished)。前提条件は SQL の WHERE に彫る(db-reconcile-* — 条件を満たさない書き込みは構造的に 0 行)。供給断(integration-lead 条件①): 一覧の取得失敗・空一覧の周期は不在を 1 回も記帳せず周期ごと skip(counter reconcile_supply_cut)— 一覧は必ず最初の effect(供給断の周期に書き込みゼロは構造)。起動猶予(条件②): vanish は供給断でない成功周期を 1 回以上経た後のみ(daemon 停止中に窓が経過した行の再起動直後 vanish の封鎖)。schema 追加は additive 3 列(substrate_absent_since / substrate_absent_checks / successor_session_id)— upsert の INSERT/SET には載せない(reconcile の guarded UPDATE だけが書く)。kill switch = DOEFF_AGENTD_RECONCILE_DISABLED=1。counters は daemon.status へ additive(in-memory)。")
     (rule R9 "語彙(波 1 席と突合済み): 行の終端印 = 宿りの終端記帳であって会話の終了ではない。会話の ended の唯一の源 = agora の会話終端簿(D567)— SessionHost のどの書き込み(vanished / superseded 含む)も会話 ended へ写像されない。M0① 会話契約 v0 の 4 値導出(live = 非終端行 ∧ substrate_present / dormant = 宿りなし・終端印なし / ended = 終端簿のみ / unknown = 素材が読めない)は不変 — reconciler は live 導出の母数から幽霊を除くだけ。superseded 行は by_conversation 解決(R7 — 非終端最新が先)で後継の陰に正しく隠れる。判定未満の行(不在の窓の中・同一性不確か)は非終端のまま = 倒れ先は unknown/dormant 側。")
     (rule R10 "adopted 行への session.cancel / session.cleanup は typed 拒否(error_code adopted_session_not_owned — R1 と同じく数値表には足さない)。SessionHost は adopted 行の substrate を作っておらず、kill は外部の実席の巻き添え殺し(fold 判定 2026-08-17 — 推進役起票の隣接欠陥: 現行 main の herdr backend では外部実席を実際に殺し、tmux backend では今も殺せる)。拒否は require-session-row 直後・substrate 接触より前。単一掃き取り(ADR-010 R5)は adopted=0 filter で既に除外済み — この rule はその RPC 面の対。")]
  :laws
    [(law interactive-rows-are-never-reaped
       :statement "monitor_terminalization => only_run_to_completion_rows; adopted_or_non_rtc_rows_never_transition_to_terminal_by_monitor; adopted_terminal_writes_only_via_reconciler_guarded_updates"
       :counterexamples
         [(counterexample "launch timeout / stale reap / zombie / mux 消滅 / boot watchdog のいずれかの arm が lifecycle=interactive または adopted=1 の行を failed/exited にする")
          (counterexample "免除判定を booting arm や stale arm の後ろに置き、先行 arm が interactive 行に到達できる順序にする")
          (counterexample "reconciler 以外の経路(monitor arm / adopt / turn / RPC / 手 SQL)が adopted 行へ終端を書く — 終端の書き手は R8 の guarded UPDATE(db-reconcile-vanish / db-reconcile-supersede)のみ(改訂 2)")])
     (law transient-absence-never-terminalizes
       :statement "reconcile_terminal_requires_sustained_absence; checks_gte_min AND since_older_than_window AND no_live_conversation_trace AND no_successor_row; supply_cut_cycle_records_no_absence; first_cycle_after_daemon_start_never_vanishes; undecidable_falls_to_nonterminal_unknown_side"
       :counterexamples
         [(counterexample "単発の不在観測・窓未満・checks 未満のどれかで終端を書く(D566 実弾: fleet の数十秒の不在が『会話終了の機械証明』に使われた形の再来)")
          (counterexample "供給断の周期(生存一覧の取得失敗・締切超過・空一覧)で不在を加算する — herdr 再起動中の数分で checks が積み上がり、窓が既に開いている旧行が復旧直後に vanish する(integration-lead 条件①)")
          (counterexample "daemon 再起動後の最初の周期で vanish を許す — since/checks は永続するため、停止中に窓が経過した行が substrate 側の名簿がまだ部分的な時に即終端される(同 条件②)")
          (counterexample "終端条件を program 側の分岐だけに置き、SQL の書き込み点に彫らない(係のバグ・将来の別コードが条件を飛ばして終端を書ける)")])
     (law rename-revival-links-successor-not-end
       :statement "same_conversation_alive_or_registered_elsewhere => vanish_forbidden; supersede_requires_live_same_conversation_successor_row; row_terminal_mark_is_incarnation_end_never_conversation_end"
       :counterexamples
         [(counterexample "同一会話が別 pane に生存している行(改名復活 R55 の痕跡)へ vanished を書く — 会話は続いている(実弾: coupling-core-review → s-c137bf0bab の改名復活)")
          (counterexample "後継行が実在しないのに supersede する・別会話の行を successor に紐づける(識別子越えの紐づけ = stamp-never-crosses-identities の書き込み版)")
          (counterexample "vanished / superseded の終端印を会話の ended として下流が読む形を作る(ended の唯一の源 = agora 終端簿 D567 — end-is-recorded-not-derived)")])
     (law reconciler-observes-never-mutates-substrate
       :statement "reconcile_path_substrate_effects => list_observation_only; no_kill_no_send_no_fs_write_no_delivery_no_capture; store_writes_only_via_guarded_updates_never_upsert"
       :counterexamples
         [(counterexample "reconcile 経路で TmuxKillSession / TmuxSendKeys / TmuxNewSession / TmuxCapture / FsWriteTextAtomic / DeliverMessage / ProcRun を呼ぶ(観測者が substrate に触った瞬間、鏡は現実を変える手になる)")
          (counterexample "reconcile 経路で session-store-upsert を使う(guarded UPDATE の前提条件を素通しできる汎用書き込み口 — merge 経路が streak / successor を巻き戻す)")
          (counterexample "session.cancel / session.cleanup が adopted 行の substrate を kill する(R10 — SessionHost が作っていない外部の実席の巻き添え殺し。typed 拒否 adopted_session_not_owned が契約)")])
     (law adopt-verifies-then-registers
       :statement "adopt => substrate_existence_check_precedes_upsert; failed_adopt_leaves_no_row; adopt_never_mutates_substrate"
       :counterexamples
         [(counterexample "行を先に upsert してから実在確認し、失敗時に行を消す(消し損ねが幻 turn-open になる)")
          (counterexample "adopt 中に TmuxSendKeys / TmuxNewSession / TmuxKillSession / FsWriteTextAtomic / DeliverMessage を呼ぶ")])
     (law ledger-mirrors-reality
       :statement "vanished_substrate_of_exempt_row => derived_divergence_display_only_at_wire; ledger_convergence_only_via_reconciler_hysteresis; never_delete"
       :counterexamples
         [(counterexample "免除行の pane 消滅の単発検知(wire 導出面・monitor 観測)で行を exited にする・行を DELETE する — 台帳の収束は R8 の複数回観測 + 時間窓の hysteresis 付き終端のみ(改訂 2: never_terminalize は『瞬時の観測からは決して』に精密化 — 書き手は reconciler 1 本)")
          (counterexample "substrate_present を列として保存し、現実でなく保存値を wire に返す(substrate_absent_since/checks は『不在観測の記帳』であって presence の保存キャッシュではない — wire の substrate_present は従来どおり毎回 probe の導出)")])
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
         "source" "(deff db-resolve-turn-target [conn pane-id agent-name conversation-id]\n  (when (and (is pane-id None) (is conversation-id None) (is-not agent-name None))\n    (setv row (.fetchone (.execute conn base-name-sql)))\n    (when (is-not row None) (return (get row 0))))\n  None)\n"}])
     (deftest test-adr-doe-agents-007-reconcile-hysteresis-gates
       ;; 法 transient-absence-never-terminalizes の機械面(実 SQLite):
       ;; vanish の前提条件(checks ≥ K ∧ since ≤ cutoff ∧ 同一会話の非終端
       ;; 別行なし)は SQL に彫られ、どれが欠けても 0 行 = 終端しない。
       ;; D566(一過性不在の終了潰し)の regression pin。
       (import json)
       (import sqlite3)
       (import doeff_agents.sessionhost.store [db-migrate db-session-get
                                               db-reconcile-mark-absent
                                               db-reconcile-vanish])
       (setv conn (sqlite3.connect ":memory:"))
       (db-migrate conn)
       (defn seed [sid conv]
         (.execute conn
           (+ "INSERT INTO agent_sessions (session_id, session_name, pane_id, "
              "agent_type, work_dir, status, backend_kind, backend_ref_json, "
              "started_at, adopted, conversation_json) "
              "VALUES (?, ?, '%p', 'claude', '', 'running', 'tmux', '{}', "
              "'2026-08-17T00:00:00+00:00', 1, ?)")
           #(sid sid (if (is conv None) None (json.dumps {"session_id" conv})))))
       (seed "ghost" "conv-a")
       ;; 単発の不在観測では終端しない(checks 1 < 3)
       (db-reconcile-mark-absent conn "ghost" "2026-08-17T12:00:00+00:00")
       (assert (= (db-reconcile-vanish conn "ghost" "2026-08-17T13:00:00+00:00"
                                       "2026-08-17T12:30:00+00:00" 3) 0))
       ;; checks 3・窓未達(since > cutoff)でも終端しない
       (db-reconcile-mark-absent conn "ghost" "2026-08-17T12:01:00+00:00")
       (db-reconcile-mark-absent conn "ghost" "2026-08-17T12:02:00+00:00")
       (assert (= (db-reconcile-vanish conn "ghost" "2026-08-17T12:03:00+00:00"
                                       "2026-08-17T11:59:00+00:00" 3) 0))
       ;; 同一会話の非終端の別行(改名復活の痕跡)が在る間も終端しない
       (seed "revived" "conv-a")
       (assert (= (db-reconcile-vanish conn "ghost" "2026-08-17T13:00:00+00:00"
                                       "2026-08-17T12:30:00+00:00" 3) 0))
       ;; 全条件充足で初めて vanished(宿りの終端 — 会話の ended ではない)
       (.execute conn "UPDATE agent_sessions SET status='exited' WHERE session_id='revived'")
       (assert (= (db-reconcile-vanish conn "ghost" "2026-08-17T13:00:00+00:00"
                                       "2026-08-17T12:30:00+00:00" 3) 1))
       (setv snap (db-session-get conn "ghost"))
       (assert (= (get snap "status") "exited"))
       (assert (= (get (get snap "terminal_cause") "category") "vanished"))
       (.close conn))
     (deftest test-adr-doe-agents-007-rename-revival-links-successor-not-end
       ;; 法 rename-revival-links-successor-not-end の機械面(実 SQLite):
       ;; supersede は生きた同一会話の後継行にのみ紐づく(別会話 = 0 行)。
       ;; 終端印は superseded(retryable=false)+ successor_session_id で、
       ;; vanished とは別の語 — 会話の継続が読める。
       (import json)
       (import sqlite3)
       (import doeff_agents.sessionhost.store [db-migrate db-session-get
                                               db-reconcile-supersede])
       (setv conn (sqlite3.connect ":memory:"))
       (db-migrate conn)
       (defn seed [sid conv started]
         (.execute conn
           (+ "INSERT INTO agent_sessions (session_id, session_name, pane_id, "
              "agent_type, work_dir, status, backend_kind, backend_ref_json, "
              "started_at, adopted, conversation_json) "
              "VALUES (?, ?, '%p', 'claude', '', 'running', 'tmux', '{}', "
              "?, 1, ?)")
           #(sid sid started (json.dumps {"session_id" conv}))))
       (seed "old" "conv-c" "2026-08-16T00:00:00+00:00")
       (seed "succ" "conv-c" "2026-08-17T00:00:00+00:00")
       (seed "stranger" "conv-z" "2026-08-17T00:00:00+00:00")
       ;; 別会話の行への紐づけは構造的に 0 行(識別子越えの紐づけ禁止)
       (assert (= (db-reconcile-supersede conn "old" "stranger"
                                          "2026-08-17T12:00:00+00:00") 0))
       (assert (= (db-reconcile-supersede conn "old" "succ"
                                          "2026-08-17T12:00:00+00:00") 1))
       (setv snap (db-session-get conn "old"))
       (assert (= (get snap "status") "exited"))
       (assert (= (get snap "successor_session_id") "succ"))
       (assert (= (get (get snap "terminal_cause") "category") "superseded"))
       (assert (= (get (get snap "terminal_cause") "retryable") False))
       ;; 後継の行は無傷(running のまま)
       (assert (= (get (db-session-get conn "succ") "status") "running"))
       (.close conn))
     (defsemgrep reconcile-observation-only
       "doeff-agents-reconcile-must-not-mutate-substrate"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/reconcile.hy"
         "source" "(defk reconcile-row-once [row]\n  (<- _ (tmux-kill-session row.session-name))\n  \"vanished\")\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/reconcile.hy"
         "source" "(defk reconcile-cycle [allow-vanish window-seconds min-checks]\n  (<- live (tmux-list-sessions))\n  (<- n (session-store-reconcile-vanish sid observed-at cutoff-iso min-checks))\n  summary)\n"}])
     (defsemgrep reconcile-guarded-updates-only
       "doeff-agents-reconcile-terminal-only-via-guarded-updates"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/reconcile.hy"
         "source" "(defk reconcile-row-once [row]\n  (setv row (replace row :status \"exited\"))\n  (<- _ (session-store-upsert row))\n  \"vanished\")\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/reconcile.hy"
         "source" "(defk reconcile-row-once [row]\n  (<- n (session-store-reconcile-vanish row.session-id observed-at cutoff-iso min-checks))\n  \"vanished\")\n"}])]
  :plans ["docs/adr/defadr_doeff_agents_007_koine_session_surface.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy(additive 4 列 + adopted filter + 会話 index + db-session-by-conversation〔波 1-S1〕+ additive 3 列 + db-reconcile-* guarded UPDATE 群〔改訂 2〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy(reap-exempt 免除 arm + turn-stalled 導出 + superseded category〔改訂 2〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/adopt.hy(adopt program — observation-only + 会話受理と冪等 3 分岐〔波 1-S1〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy(descriptor 解決 = pane/conversation/素の名前 + 3 列 UPDATE〔波 1-S1〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/reconcile.hy(adopted 行 reconciler — 分類・後継解決・宣言的 1 pass〔改訂 2〕)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy(RPC dispatch + counters + wire 導出 + session.by_conversation〔波 1-S1〕+ reconcile 周期・起動猶予・R10 typed 拒否〔改訂 2〕)"
          "packages/doeff-agents/tests/sessionhost_reconcile_deftests.hy(合成検体 3 態 = 消滅・改名・一過性不在 + 供給断 + 起動猶予〔改訂 2〕)"
          "packages/doeff-agents/conformance/test_s23..s27(black-box 検定 — 将来 koine 側へ移設可能。S23/S25 は波 1-S1 改訂を pin)"])
