;;; headless backend の program(agora-redesign #37・段 2 lane 2d)— tui の pane を持たない
;;; session の launch / send / interrupt / cancel / cleanup / capture / monitor。
;;;
;;; host.hy の RPC の語彙は tui と同じ(launch / send / get / capture / cancel / cleanup)で、
;;; backend=headless の host は各動詞をこの module の program に写す(run-hosted の handler
;;; stack に headless-substrate と headless-argv-impl を挿す)。加えて `session.interrupt`
;;; (走っている手番だけを止め、session は残す — headless = SIGINT / turn/interrupt・
;;; tmux = Escape)を両 backend に足す。
;;;
;;; tui の launch-session(launch.hy)と共有するもの: admission(admit-launch)・作業場と
;;; identity の準備(prepare-launch-workspace)・session hook の宣言(session-hooks-mode)・
;;; 実効 env(launch-spawn-env)・result channel の配線・行の欄(SessionRow)。持たない
;;; もの: ready gate・paste の confirm ループ・pane の marker 分類 — headless には
;;; 画面が無く、prompt は stdin へ書き、手番の終わりは stdout の行(claude の result /
;;; codex の turn/completed)で読む。
;;;
;;; 判断は純関数 1 点: 手番の次の 1 手は headless_protocol.turn_verdict(器の観測 ×
;;; 手番の途中か)。この program は effect を並べ、行の欄に写すだけ(substrate-clean)。
;;; 温かい session(ADR-DOE-AGENTS-012 R10): multi_turn の行は手番の終わりで status を
;;; 倒さず turn_ended_at を刻む(policy.hy の monitor と同じ level-triggered の欄)。
;;; codex の process は手番の間も生きる(温かい)。claude は **1 手番 1 process**(段 12 lane 12e・
;;; agora-redesign #517・card ki-ec55c1318483): 手番の終わり(result の行)= 対話の終わり = process の
;;; 終わりで、器(headless_process の retire)が result の行で stdin に EOF を出して降ろす — CLI は
;;; result の後も生きていると自分の background task / Monitor の完了で model を手番の外で起こし直し
;;; tool を撃つ(実弾 2026-09-17 19:4x・同じ会話の 2 つの process が本番に作用・記録に載らない行動)ので、
;;; 手番の境界の所有者は host ちょうどにする(段 8 lane 4x の温かい claude は退役)。process が降りて
;;; いれば(手番の終わり・SIGINT で止めた・idle で退いた)次の手番の send は `--resume <sid>` の process を
;;; 同じ session の名で起こし直してから stdin へ書く(温かい = 会話の資源としての行と events file が続く)。
;;;
;;; host の再起動の後の復帰(段 10 lane 10h・agora-redesign #84・既知の形 = kubelet の node 再起動後の
;;; container の生死の観測): registry は host の process と共に消え、launchd の kickstart は子 process も
;;; 道連れにするので、手番の途中(awaiting)のまま残った行は誰も終端に倒さない — 会話が永久に「動いている」
;;; になり、agentd は defer を返し続けた(実弾 2026-09-14 14:35〜18:5x)。host は accept を始める前に
;;; recover-headless-rows で各行の backend を観測(HeadlessLiveness = pid の存在 + registry の所有)し、
;;; 判断 headless_protocol.recovery_verdict の 1 点で「手番の途中 ∧ backend が死んでいる」行だけを
;;; exited + cause vanished(ADR-DOE-AGENTS-009: 証拠つき死亡の語彙・reason に pid と観測の文)にして
;;; session_exited を刻む。idle の温かい行は触らない(次の send が --resume で同じ session を起こし直す)。
;;; 起動時の awaiting latch の全 clear(store.hy db-clear-awaiting-latches — tui の物理)は headless の行を
;;; 対象にしない: headless の latch は「手番の途中」の事実そのもので、消すと復帰が判断できない。
;;;
;;; 割り込みの本文(段 8 lane 4x・agora-redesign #56): session.send の mode = interrupt は
;;; 走っている手番へ本文を注入する(HeadlessInject — claude は user の行を CLI が次の tool の
;;; 境界で読む・codex は turn/interrupt → 同じ thread へ turn/start)。走っている手番が無ければ
;;; 器は引き受けず、host は型付きに断る(誰の job でもない手番を起こさない — 呼び手の agentd は
;;; 割り込みを行に残し、Messaging が queued へ積み直す)。

(require doeff-hy.macros [defk <-])

(import dataclasses [replace])
(import os)

(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  build-headless-launch
  clock-now
  fs-read-text
  headless-deliver
  headless-escalate
  headless-has-session
  headless-inject
  headless-interrupt
  headless-kill
  headless-kill-all
  headless-liveness
  headless-poll
  headless-spawn
  session-store-get
  session-store-list-active
  session-store-list-cleanup-pending
  session-store-record-event
  session-store-upsert
  wire-result-channel])
;; 段 11 lane 11n 便 C(agora-redesign #179): provider の限度の族の表は impls/markers.hy の
;; 1 点(ADR-DOE-AGENTS-008 R1 の観測形式の家・pane の路と同じ表)。ここは表を写さず、
;; 手番の終わりの文へ当てるだけ。
(import doeff_agents.sessionhost.impls.markers [is-api-limit-refusal])
(import doeff_agents.sessionhost.headless_protocol [
  BackendLiveness
  HeadlessObservation
  RecoveryVerdict
  Verdict
  recovery-verdict
  stop-verdict
  turn-verdict])
(import doeff_agents.sessionhost.launch [
  INTERACTIVE-AGENT-TYPES
  RESULT-PROTOCOL-INSTRUCTION
  admit-launch
  claude-settings-declaration
  launch-spawn-env
  prepare-launch-workspace
  session-hooks-mode])
(import doeff_agents.sessionhost.policy [
  carry-launch-flags
  cause-if-absent
  overlay-without-turn-auth
  event-type-for-status
  is-multi-turn
  is-run-to-completion
  is-terminal-status
  iso-format
  make-cause
  reap-exempt
  tail-chars])


(setv HEADLESS-BACKEND-KIND "headless")
;; interrupt の監査 event(tmux / headless 共通の語)。
(setv EVENT-SESSION-INTERRUPTED "session_interrupted")
(setv EVENT-SESSION-TURN-ENDED "session_turn_ended")
;; 割り込みの本文を走っている手番へ注入した監査 event(段 8 lane 4x)。
(setv EVENT-SESSION-INJECTED "session_injected")
;; 停止の合図(段 10 lane 10n)の監査 event。
(setv EVENT-SESSION-ESCALATED "session_interrupt_escalated")
;; session.send の mode(閉語彙): turn = 次の手番の本文(既定)/ interrupt = 割り込みの本文。
(setv SEND-MODE-TURN "turn")
(setv SEND-MODE-INTERRUPT "interrupt")
(setv SEND-MODES #{SEND-MODE-TURN SEND-MODE-INTERRUPT})


;; ---------------------------------------------------------------------------
;; 純粋な小片(path・行の読み)
;; ---------------------------------------------------------------------------

(defk headless-events-path [events-root session-id]
  {:pre [(: events-root str) (: session-id str)]
   :post [(: % str)]}
  "session の events file(stdout の行を 1 行 1 event で追記する実況の正本)の置き場。
   backend_ref.events_path として行に載り、agentd はそこから offset で読む。"
  (os.path.join events-root f"{session-id}.events.jsonl"))


(defk is-headless-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % bool)]}
  (= row.backend-kind HEADLESS-BACKEND-KIND))


(defk headless-backend-ref [session-name pid events-path argv socket-path]
  {:pre [(: session-name str) (: pid int) (: events-path str) (: argv list) (: socket-path str)]
   :post [(: % dict)]}
  "行の backend_ref(oracle の {session_name, pane_id, command} に当たる headless の欄)。
   events_path は agentd が実況を読む鍵、pid と argv は診断の写し、socket_path は続きの
   process の result channel の配線に要る host の socket。"
  {"session_name" session-name
   "pid" pid
   "events_path" events-path
   "argv" (list argv)
   "socket_path" socket-path})


(defk events-path-of-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % (| str None))]}
  (setv ref (or row.backend-ref {}))
  (setv path (.get ref "events_path"))
  (if (isinstance path str) path None))


(defk pid-of-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % (| int None))]}
  "行の backend_ref の pid(headless-backend-ref が書いた int・無ければ None — 発明しない)。"
  (setv ref (or row.backend-ref {}))
  (setv pid (.get ref "pid"))
  (if (and (isinstance pid int) (not (isinstance pid bool))) pid None))


(defk require-headless-row [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session 行の必須読み(host.hy require-session-row と同文言)+ backend の照合。"
  (<- row (session-store-get session-id))
  (when (is row None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (when (not (! (is-headless-row row)))
    (raise (RuntimeError
             (+ f"session {session-id} is a {row.backend-kind} session — "
                "this host speaks the headless backend"))))
  row)


;; ---------------------------------------------------------------------------
;; 起動(launch)と続きの process(claude の次の手番)
;; ---------------------------------------------------------------------------

(defk headless-launch-args [params identity conversation resume-mode socket-path session-id]
  {:pre [(: params dict) (: identity (| dict None)) (: conversation (| dict None))
         (: resume-mode (| str None)) (: socket-path str) (: session-id str)]
   :post [(: % dict)]}
  "kind の headless の起動(argv と作法)を per-kind impl に組ませる材料: launch params 相当 +
   result channel(expected_result が在る時)+ conversation + resume_mode。"
  (setv agent-type (get params "agent_type"))
  (<- session-hooks (session-hooks-mode))
  ;; card ki-7b52bb76aa6e(ADR-004 R13): 席の settings の宣言は tui と同じ 1 点(launch.hy)で読む — 起動の拍ごと。
  (<- claude-settings (claude-settings-declaration agent-type))
  (setv effective (dict params))
  (setv (get effective "session_hooks") session-hooks)
  (when (is-not claude-settings None)
    (setv (get effective "claude_settings") claude-settings))
  (when (and (is-not (.get params "expected_result") None)
             (in agent-type INTERACTIVE-AGENT-TYPES))
    (<- channel (wire-result-channel agent-type session-id socket-path))
    (setv (get effective "result_channel") channel))
  (when (is-not conversation None)
    (setv (get effective "conversation") conversation))
  (when (is-not resume-mode None)
    (setv (get effective "resume_mode") resume-mode))
  (.pop effective "prompt" None)
  (<- built (build-headless-launch agent-type effective))
  built)


(defk headless-launch-session [params]
  {:pre [(: params dict)]
   :post [(: % SessionRow)]}
  "headless の 1 session の launch。params は launch-session と同じ(host が backend_kind =
   headless・events_root・socket_path を注入)。順序: admission(admit-launch・共有)→ 同名の
   process の重複 → 作業場と identity(prepare-launch-workspace・共有)→ kind の argv と作法
   (BuildHeadlessLaunch)→ process の起動 → running の行(実況の正本 = events file)→
   prompt を stdin へ(Dialogue.turn)。ready gate は無い: 子 process は起きた瞬間から
   stdin を読む。戻り値: 永続化済みの running SessionRow。"
  (setv session-id (get params "session_id"))
  (setv session-name (get params "session_name"))
  (setv agent-type (get params "agent_type"))
  (setv lifecycle (get params "lifecycle"))
  (setv session-env (.get params "session_env" {}))
  (setv expected-result (.get params "expected_result"))
  (setv work-dir (get params "work_dir"))
  (setv events-root (get params "events_root"))
  (when (.strip (or (.get params "command") ""))
    (raise (RuntimeError
             "session.launch: the headless backend builds the agent argv itself — "
             "an explicit `command` override is a tui-transport escape hatch and is "
             "not accepted here")))
  (when (not-in agent-type INTERACTIVE-AGENT-TYPES)
    (raise (RuntimeError
             f"session.launch: the headless backend supports agent_type 'claude' or 'codex' (got: {agent-type})")))

  (<- _ (admit-launch params))
  (<- exists (headless-has-session session-name))
  (when exists
    (raise (RuntimeError f"headless session already exists: {session-name}")))
  (<- prepared (prepare-launch-workspace params))
  (setv identity (get prepared "identity"))
  (setv minted-conversation (get prepared "conversation"))
  (setv resume-context (.get params "resume_context"))

  ;; 会話 identity: resume は親会話・fork は None(事後発見)・fresh の claude は鋳造済み
  ;; UUID(--session-id)・fresh の codex は None(thread/start の応答で知る)。
  (setv row-conversation
        (cond
          (is-not resume-context None)
            (if (= (get resume-context "mode") "resume")
                (get resume-context "conversation")
                None)
          (= agent-type "claude") minted-conversation
          True None))
  (setv resume-mode (when (is-not resume-context None) (get resume-context "mode")))
  (<- built (headless-launch-args params identity
                                  (if (is-not resume-context None)
                                      (get resume-context "conversation")
                                      minted-conversation)
                                  resume-mode
                                  (.get params "socket_path" "")
                                  session-id))
  (setv argv (get built "argv"))
  (<- effective-env (launch-spawn-env identity session-env))
  (<- events-path (headless-events-path events-root session-id))
  (<- pid (headless-spawn session-name work-dir effective-env argv events-path
                          (get built "dialogue")))

  ;; --- 行の登録(process の直後): headless に booting の窓は無い — 起きた process は
  ;; すぐ stdin を読むので、prompt の配送と同じ拍に running へ。awaiting latch は prompt を
  ;; 配送する launch だけ立てる(見かけの手番の終わりを評価しない印)。
  (setv prompt (or (.get params "prompt") ""))
  (setv awaiting (bool (.strip prompt)))
  (<- now (clock-now))
  (setv row (SessionRow
              :session-id session-id
              :session-name session-name
              :pane-id f"headless:{session-name}"
              :agent-type agent-type
              :lifecycle lifecycle
              :status "running"
              :started-at (iso-format now)
              :awaiting-response awaiting
              :awaiting-response-since (when awaiting (iso-format now))
              :expected-result expected-result
              :effective-identity identity
              :work-dir work-dir
              :backend-kind HEADLESS-BACKEND-KIND
              :backend-ref (! (headless-backend-ref session-name pid events-path argv
                                                    (str (.get params "socket_path" ""))))
              ;; 追補 2(実弾 #92): 手番ごとの資格の札は行に残さない — 再開はその手番の送りが運ぶ env で起こす
              ;; 会話の圧縮の閾値(設計記録 docs/design/auto-compact-window): 起こす旗も行の意図に残す — 残さないと、
              ;; 降りた process の続き(continue-headless-process)が行だけを読んで
              ;; argv を組み直すので、**その腕だけ**走行係の床へ戻る。
              :launch-overlay (carry-launch-flags
                                params
                                {"session_env" (! (overlay-without-turn-auth session-env))
                                 "model" (.get params "model")
                                 "effort" (.get params "effort")
                                 "mcp_servers" (or (.get params "mcp_servers") {})})
              :launch-attribution (.get params "launch_attribution")
              :conversation row-conversation
              :generation (if (is resume-context None)
                              1
                              (get resume-context "generation"))
              :resumed-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "resumed_from_session_id"))
              :forked-from-session-id
                (when (is-not resume-context None)
                  (.get resume-context "forked_from_session_id"))))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id "session_started" row))

  (when awaiting
    (setv full-prompt
          (if (is-not expected-result None)
              (+ prompt RESULT-PROTOCOL-INSTRUCTION)
              prompt))
    ;; 段 10 lane 10o(agora-redesign #96): 起こす腕は郵便を 1 手番目に畳む(first-turn-carries-inputs)ので、
    ;; その郵便の添付もこの 1 手番に載る(綴りは Dialogue.turn)。
    (setv launch-attachments (tuple (.get params "attachments" #())))
    (<- delivered (headless-deliver session-name full-prompt launch-attachments))
    (when (not delivered)
      (<- fail-now (clock-now))
      (setv failed-row (replace row
                                :status "failed"
                                :finished-at (iso-format fail-now)
                                :last-observed-at (iso-format fail-now)
                                :awaiting-response False))
      (setv failed-row (cause-if-absent
                         failed-row
                         (make-cause "prompt_undelivered"
                                     "headless process exited before the prompt could be written"
                                     (iso-format fail-now))))
      (<- _ (session-store-upsert failed-row))
      (<- _ (session-store-record-event session-id "session_failed" failed-row))
      (<- _ (headless-kill session-name))
      (raise (RuntimeError
               (+ f"session.launch: {agent-type} headless process exited before the "
                  "prompt could be written (prompt_undelivered); see the "
                  f"stderr file next to {events-path}")))))
  row)


(defk continue-headless-process [row turn-env]
  {:pre [(: row SessionRow) (: turn-env (| dict None))]
   :post [(: % SessionRow)]}
  "降りた process の次の手番: 行の会話 identity で `--resume <sid>` の process を同じ session の
   名で起こし直す(events file は同じ path に追記)。会話の id が無い行は続けられない(発明
   しない — 型付きに断る)。戻り値: backend_ref を更新した行。

   段 10 lane 10d 便 2 の追補 2(実弾 #92): 資格の env は**この手番の送りが運ぶ値**(turn-env)を重ねる。
   誕生時の env は行に札を残さない(overlay-without-turn-auth)ので、更新で回って revoke された札で
   起こすことは構造的に無い。turn-env が無い呼び(operator の救援等)は行の非 auth の意図だけで起きる。"
  (when (is row.conversation None)
    (raise (RuntimeError
             (+ f"session.send: session {row.session-id} has no conversation identity — "
                "the next headless turn cannot be resumed"))))
  (setv overlay (or row.launch-overlay {}))
  ;; 会話の圧縮の閾値(設計記録 docs/design/auto-compact-window): 行に残した起こす旗を続きの手番へも運ぶ(policy.LAUNCH-FLAG-KEYS)。
  (setv params (carry-launch-flags
                 overlay
                 {"agent_type" row.agent-type
                  "work_dir" row.work-dir
                  "model" (.get overlay "model")
                  "effort" (.get overlay "effort")
                  "mcp_servers" (or (.get overlay "mcp_servers") {})
                  "expected_result" row.expected-result}))
  (setv ref (or row.backend-ref {}))
  (<- built (headless-launch-args params row.effective-identity row.conversation "resume"
                                  (str (.get ref "socket_path" "")) row.session-id))
  (setv argv (get built "argv"))
  (<- effective-env (launch-spawn-env row.effective-identity
                                      (| (dict (or (.get overlay "session_env") {}))
                                         (dict (or turn-env {})))))
  (setv events-path (or (! (events-path-of-row row))
                        (raise (RuntimeError
                                 f"session {row.session-id} has no events_path in backend_ref"))))
  (<- pid (headless-spawn row.session-name row.work-dir effective-env argv events-path
                          (get built "dialogue")))
  (setv next-ref (dict ref))
  (.update next-ref (! (headless-backend-ref row.session-name pid events-path argv
                                             (str (.get ref "socket_path" "")))))
  (replace row :backend-ref next-ref))


;; ---------------------------------------------------------------------------
;; RPC の program(send / interrupt / cancel / cleanup / capture)
;; ---------------------------------------------------------------------------

(defk headless-inject-program [session-id message ref [attachments #()]]
  {:pre [(: session-id str) (: message str) (: ref str) (: attachments tuple)]
   :post [(: % SessionRow)]}
  "session.send の mode = interrupt(headless・段 8 lane 4x): 割り込みの本文を走っている手番へ
   注入する(HeadlessInject)。器が引き受けなかった(process が無い / 降りている / 走っている
   手番が無い / 手番の終わりを読んだ後)時は型付きに断る — 新しい手番を起こさない(その本文は
   呼び手が queued として次の手番に運ぶ)。行の awaiting は触らない(手番は走ったまま)。
   ref = 注入の行の名(段 10 lane 10n・claude の uuid — 空なら器が鋳造)。"
  (<- row (require-headless-row session-id))
  (when (is-terminal-status row.status)
    (raise (RuntimeError f"session {session-id} is {row.status}; cannot inject into a terminal session")))
  (<- accepted (headless-inject row.session-name message ref attachments))
  (when (not accepted)
    (raise (RuntimeError
             (+ f"session.send: no turn of {session-id} is in flight to interrupt — "
                "the text was not delivered (send it as the next turn)"))))
  (<- now (clock-now))
  (setv row (replace row :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id EVENT-SESSION-INJECTED row))
  row)


(defk headless-send-program [session-id message awaiting turn-env [attachments #()]]
  {:pre [(: session-id str) (: message str) (: awaiting bool) (: turn-env (| dict None))
         (: attachments tuple)]
   :post [(: % SessionRow)]}
  "session.send(headless・mode = turn): 次の手番の本文を stdin へ。process が次の手番を
   受けられる(生きた温かい process — codex)ならそのまま、受けられない(降りた process —
   claude は手番の終わりで必ず降りている・段 12 lane 12e #517)なら `--resume` で起こし直してから書く。awaiting(agentd の温かい手番)は latch を立て、
   turn_ended_at を None に戻す(次の手番が走り出した — level-triggered の欄)。
   turn-env = **この手番の** env(段 10 lane 10d 便 2 追補 2・実弾 #92): 起こし直す時に重ねる
   (預かり所の貸与の札はここで来る — 行に残った誕生の札では起こさない)。"
  (<- row (require-headless-row session-id))
  (when (is-terminal-status row.status)
    (raise (RuntimeError f"session {session-id} is {row.status}; cannot send to a terminal session")))
  (<- observed (headless-poll row.session-name))
  (setv accepts (and (isinstance observed HeadlessObservation) observed.accepts-turn))
  (when (not accepts)
    (<- continued (continue-headless-process row turn-env))
    (setv row continued))
  (<- delivered (headless-deliver row.session-name message attachments))
  (when (not delivered)
    (raise (RuntimeError
             f"session.send: headless process of {session-id} is not accepting input")))
  (<- now (clock-now))
  (setv row (replace row :last-observed-at (iso-format now)))
  (when awaiting
    (setv row (replace row :awaiting-response True
                           :awaiting-response-since (iso-format now)
                           :turn-ended-at None
                           :turn-error None)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id "session_sent" row))
  row)


(defk headless-escalate-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.escalate(headless・段 10 lane 10n): 注入した本文を model が期限まで読まなかった時の
   停止の合図(claude = control_request interrupt — 走っている道具 / 生成を止め、注入の行が同じ
   session の次の手番として即座に走る。codex は注入の段が無いので出す物が無い)。判断は
   Dialogue.escalate の 1 点(queued の注入が無い・既に出した・手番が走っていない → 出さない)。
   出す物が無かった時は型付きに断る(呼び手が『止めた』と記録しないため)。行の awaiting は触らない
   (host から見た手番は続く — 器は止めた段の result を手番の終わりとして報告しない)。"
  (<- row (require-headless-row session-id))
  (when (is-terminal-status row.status)
    (raise (RuntimeError f"session {session-id} is {row.status}; cannot escalate a terminal session")))
  (<- signalled (headless-escalate row.session-name))
  (when (not signalled)
    (raise (RuntimeError
             (+ f"session.escalate: nothing to escalate for {session-id} — "
                "no unread interrupt is queued on a running turn (or the signal was already sent)"))))
  (<- now (clock-now))
  (setv row (replace row :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id EVENT-SESSION-ESCALATED row))
  row)


(defk headless-interrupt-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.interrupt(headless): 走っている手番を止める合図(claude = SIGINT・codex =
   turn/interrupt)。session は残す — 手番の終わりは monitor が stdout の行(result /
   turn/completed)か process の終了で読む。合図を出せなかった(process が無い =
   手番は走っていない)時も断らない(冪等)。"
  (<- row (require-headless-row session-id))
  (<- signalled (headless-interrupt row.session-name))
  (<- now (clock-now))
  (setv row (replace row :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id EVENT-SESSION-INTERRUPTED row))
  row)


(defk headless-cancel-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cancel(headless): process を降ろし stopped + cause cancelled(first-write-wins)。
   tui の cancel-program と同じ意味(終端)。"
  (<- row (require-headless-row session-id))
  (<- _ (headless-kill row.session-name))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated (replace row :status "stopped"
                             :finished-at now-str
                             :last-observed-at now-str
                             :awaiting-response False))
  (setv updated (cause-if-absent
                  updated (make-cause "cancelled" "session.cancel requested" now-str)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cancelled" updated))
  updated)


(defk headless-cleanup-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cleanup(headless): process を降ろし、非終端なら stopped + cause cancelled、
   finished_at は既存優先、cleaned_at 刻印(tui の cleanup-program と同じ意味)。"
  (<- row (require-headless-row session-id))
  (<- _ (headless-kill row.session-name))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated row)
  (when (not (is-terminal-status updated.status))
    (setv updated (replace updated :status "stopped" :awaiting-response False))
    (setv updated (cause-if-absent
                    updated
                    (make-cause "cancelled"
                                "session.cleanup stopped a non-terminal session"
                                now-str))))
  (when (is updated.finished-at None)
    (setv updated (replace updated :finished-at now-str)))
  (setv updated (replace updated :cleaned-at now-str
                                 :last-observed-at now-str))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cleaned" updated))
  updated)


(defk tail-lines [text lines]
  {:pre [(: text str) (: lines int)]
   :post [(: % str)]}
  (setv rows (.splitlines text))
  (.join "\n" (cut rows (max 0 (- (len rows) lines)) None)))


(defk headless-capture-program [session-id lines]
  {:pre [(: session-id str) (: lines int) (> lines 0)]
   :post [(: % str)]}
  "session.capture(headless): 実況の断面 = events file の末尾 LINES 行(1 行 1 event の
   JSON)。tui の capture(pane の画面)と同じ動詞・同じ欄(snippet / last_observed_at /
   session_captured)。実況を読む正規の口は agentd の events の読み(offset)で、capture は
   眺め。"
  (<- row (require-headless-row session-id))
  (<- path (events-path-of-row row))
  (setv text "")
  (when (is-not path None)
    (<- raw (fs-read-text path))
    (<- text (tail-lines (or raw "") lines)))
  (<- now (clock-now))
  (setv updated (replace row
                         :output-snippet (tail-chars (or text " ") 500)
                         :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_captured" updated))
  text)


;; ---------------------------------------------------------------------------
;; monitor(手番の終わり・process の死・会話の発見)
;; ---------------------------------------------------------------------------

(defk headless-turn-error-of [verdict]
  {:pre [(: verdict Verdict)]
   :post [(: % (| str None))]}
  "手番の終わりの verdict → 行の turn-error(依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB・D2): ok なら None、失敗なら走行器が
   名乗った文(空なら閉じた既定の文 — 失敗の旗を空文字で消さない)。"
  (cond
    verdict.ok None
    (and (isinstance verdict.detail str) (.strip verdict.detail)) (.strip verdict.detail)
    True "turn ended with an error"))


(defk headless-turn-limit-cause [verdict observed-at]
  {:pre [(: verdict Verdict) (: observed-at str)]
   :post [(: % (| TerminalCause None))]}
  "段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 案 c′): 手番の終わりが
   provider の限度の断りだったか —— **当てる 1 点**。None = 限度ではない(手番の普通の終わり)。

   材料は verdict(turn-verdict が返す turn-ended の ok / detail = CLI が名乗った文 /
   api-error-status = CLI が構造で名乗った HTTP の status)で、判定は impls/markers.hy の
   is-api-limit-refusal ちょうど —— **構造が先・文が後**(agora-redesign #513): status を
   名乗る終わりは 429 が限度、名乗らない終わりは文の族の表 has-api-limit-marker(pane の路の
   policy.action-terminal-cause / failed-output-cause が PaneObservation 経由で引く**同じ表**・
   ADR-DOE-AGENTS-008 R1 の家)。当たったら category は rate_limited(policy の
   TERMINAL-CAUSE-CATEGORIES の 1 語・pane の路と同じ語彙)。限度の種類(5 時間 / 週の窓・
   individual spend・group の上限・credit 切れ)では枝を分けない —— どれも「その口座が枯れた」の
   1 事実で、解く手は配置の付け替えちょうど(operator 指示 2026-09-17)。

   ⚠ 限度の断りは **session ごと終える**(この cause を持つ行は status failed)—— 限度は
   口座 × model のもので、同じ profile の次の手番も断られる(実弾 2026-09-15 13:2x: operator の
   会話が btc で 5 回続けて断られた)。配車は model 別の枯渇(段 11 lane 11m)で別の profile へ
   移り、profile が変われば器はどうせ作り直しになる(restartOn = model・profile)。"
  (if (and (not verdict.ok) (isinstance verdict.detail str)
           (is-api-limit-refusal verdict.detail verdict.api-error-status))
      (make-cause "rate_limited" verdict.detail observed-at)
      None))


(defk observe-headless-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionRow)]}
  "1 行の level-triggered 再導出: 器の観測(HeadlessPoll)→ 次の 1 手(turn_verdict の
   純関数 1 点)→ 行の欄に写す。
   running = 観測の刻印だけ / turn-ended = multi_turn は turn_ended_at を刻み awaiting を
   下ろす(status は倒さない・session は残す)、run_to_completion は done(走行器が失敗と
   名乗れば failed)/ failed = 型付きの失敗・手番の途中の process の死 → failed + cause /
   gone = 器に process が無いのに手番の途中(host の再起動)→ failed + cause vanished /
   idle = 温かい・何もしない。会話の id(codex の thread)は見つけた拍に行へ写す。
   段 11 lane 11y 便 3(agora-redesign #140): 導出は**行の今の値**から — 引数の row は名指しで、
   中身は読み直す。monitor の拍は最初に全行を列挙してから 1 行ずつ器を観測する(1 拍に数秒)ので、
   列挙の写しは古く、その間に届いた session.send(awaiting True・turn_ended_at None)を写しで
   上書きすると、次の手番の終わりの印が前の手番の値に戻り、agentd の job-step-of は
   turn_ended_at ≤ floor で observe を続ける(実弾 2026-09-16 07:00 JST・job aj-W9WT…: 手番の終わりから
   6 分 Running のまま・割り込み 4 通が走っていない手番に置かれたまま・session の idle の掃き取りで
   ようやく Ended)。"
  (<- row (require-headless-row row.session-id))
  (<- observed (headless-poll row.session-name))
  (setv verdict (turn-verdict observed row.awaiting-response))
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  (setv entry-status row.status)
  (setv row (replace row :last-observed-at observed-at))
  (when (and (isinstance observed HeadlessObservation) observed.records)
    (setv row (replace row :last-output-change-at observed-at
                           :observed-active-at observed-at)))
  (when (and (isinstance observed HeadlessObservation)
             (is-not observed.conversation None)
             (is row.conversation None))
    (setv row (replace row :conversation (dict observed.conversation)))
    (<- _ (session-store-record-event row.session-id "session_conversation_discovered" row)))
  (cond
    (= verdict.kind "turn-ended")
    (do
      (setv row (replace row :awaiting-response False :awaiting-response-since None))
      (cond
        (is-multi-turn row.lifecycle)
        (do
          ;; 段 11 lane 11y 便 3: 手番の終わりの印は**この観測の時刻**。turn-ended の verdict は器が溜めた
          ;; ended(観測が空にする)= 新しい 1 つの手番の終わりで、同じ終わりを 2 度観測することは無い。
          ;; 旧形 `(or row.turn-ended-at observed-at)` は、send の reset が失われた行で前の手番の印を
          ;; 永久に保ち、agentd に「まだ終わっていない」と読ませた(上の実弾)。
          (setv row (replace row :turn-ended-at observed-at))
          ;; 依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 手番の終わりの ok / detail を捨てない — 失敗で終わった手番は
          ;; 走行器が名乗った文を turn-error に写す(成功なら None)。session は今日どおり生かす(温かい席を殺さない)。
          ;; 読み手 = agentd の手番の終わりの判断(judgment.turn-output-condition-of が条件の文に運ぶ)。
          (setv row (replace row :turn-error (! (headless-turn-error-of verdict))))
          ;; 段 11 lane 11n 便 C: provider が限度で断った手番は器ごと終える(判断は
          ;; headless-turn-limit-cause の 1 点)。温かいままにすると同じ profile の次の手番も
          ;; 断られ、行には何も残らない(実弾 2026-09-15 13:2x の 5 連敗)。
          (<- limit (headless-turn-limit-cause verdict observed-at))
          (if (is-not limit None)
              (do
                (setv row (replace row :status "failed"
                                       :finished-at (or row.finished-at observed-at)
                                       :last-validation-error verdict.detail))
                (setv row (cause-if-absent row limit))
                (<- _ (session-store-upsert row))
                (<- _ (session-store-record-event row.session-id "session_failed" row)))
              (do
                (<- _ (session-store-upsert row))
                (<- _ (session-store-record-event row.session-id EVENT-SESSION-TURN-ENDED row)))))
        (is-run-to-completion row.lifecycle)
        (do
          (setv outcome-ok (and verdict.ok
                                (or (is row.expected-result None)
                                    (is-not row.result-payload None))))
          (setv row (replace row :status (if outcome-ok "done" "failed")
                                 :finished-at (or row.finished-at observed-at)))
          (when (not outcome-ok)
            (setv reason (if verdict.ok
                             "agent terminated without producing the expected result envelope"
                             f"headless turn ended with an error: {verdict.detail}"))
            (setv row (replace row :last-validation-error reason))
            (setv row (cause-if-absent row (make-cause "run_failed" reason observed-at))))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id (event-type-for-status row.status) row)))
        True
        (do
          ;; interactive は手番の終わりを刻まない(刈り取り免除の観測だけ)
          (<- _ (session-store-upsert row)))))
    (in verdict.kind #{"failed" "gone"})
    (do
      (setv category (if (= verdict.kind "gone") "vanished" "run_failed"))
      (setv row (replace row :status "failed"
                             :finished-at (or row.finished-at observed-at)
                             :awaiting-response False
                             :last-validation-error verdict.detail))
      (setv row (cause-if-absent row (make-cause category verdict.detail observed-at)))
      (<- _ (session-store-upsert row))
      (<- _ (session-store-record-event row.session-id "session_failed" row)))
    True
    (<- _ (session-store-upsert row)))
  (when (and (!= entry-status row.status) (not (is-terminal-status row.status)))
    (<- _ (session-store-record-event row.session-id (event-type-for-status row.status) row)))
  row)


(defk cleanup-headless-terminal-once [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionRow)]}
  "終端 1 行の substrate cleanup(issue #568 / ADR-DOE-AGENTS-010 R5 の headless 面):
   process が登記されていれば降ろし session_cleaned を記帳、いずれの経路でも cleaned_at を
   刻む(掃き取りの witness)。"
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  (<- killed (headless-kill row.session-name))
  (setv row (replace row :cleaned-at (or row.cleaned-at observed-at)))
  (<- _ (session-store-upsert row))
  (when killed
    (<- _ (session-store-record-event row.session-id "session_cleaned" row)))
  row)


(defk observe-backend-liveness [row]
  {:pre [(: row SessionRow)]
   :post [(: % BackendLiveness)]}
  "行の backend の生死の観測(段 10 lane 10h): pid の存在と registry の所有。判断は持たない。"
  (<- liveness (headless-liveness row.session-name (! (pid-of-row row))))
  liveness)


(defk recover-headless-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionRow)]}
  "host の起動時の復帰の 1 行(段 10 lane 10h・agora-redesign #84): backend を観測し、判断
   (recovery_verdict の 1 点)が backend-dead なら exited + cause vanished(証拠つき死亡 —
   reason に pid と観測の文)・awaiting を下ろし・session_exited を刻む。keep はそのまま。"
  (<- liveness (observe-backend-liveness row))
  (setv verdict (recovery-verdict (is-terminal-status row.status) row.awaiting-response liveness))
  (when (!= verdict.kind "backend-dead")
    (return row))
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  (setv row (replace row :status "exited"
                         :finished-at (or row.finished-at observed-at)
                         :last-observed-at observed-at
                         :awaiting-response False
                         :awaiting-response-since None
                         :last-validation-error verdict.detail))
  (setv row (cause-if-absent row (make-cause "vanished" verdict.detail observed-at)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event row.session-id "session_exited" row))
  row)


(defk recover-headless-rows []
  {:pre []
   :post [(: % dict)]}
  "host の起動時の復帰(段 10 lane 10h): 非終端の headless 行を 1 行ずつ recover-headless-row へ。
   accept を始める前・awaiting latch の clear より前に 1 度だけ走る。per-session 隔離(1 行の例外は
   捕捉して次へ)。戻り値: {session-id: 処理後 status | \"error:<ExceptionType>\"}(exited に倒した行と
   keep の行の両方 — 呼び手が log に数を出す)。tui の行には触れない。"
  (<- rows (session-store-list-active))
  (setv outcomes {})
  (for [row (sorted rows :key (fn [r] r.session-id))]
    (when (! (is-headless-row row))
      (try
        (<- recovered (recover-headless-row row))
        (setv (get outcomes row.session-id) recovered.status)
        (except [e Exception]
          (setv (get outcomes row.session-id)
                f"error:{(. (type e) __name__)}")))))
  outcomes)


(defk stop-headless-row [row reason]
  {:pre [(: row SessionRow) (: reason str)]
   :post [(: % SessionRow)]}
  "host の停止の前の 1 行(段 10 lane 10h 便 2): 判断(stop_verdict の 1 点)が turn-cut なら stopped +
   cause cancelled(reason = host の停止と信号)・awaiting を下ろし・session_cancelled を刻む。keep はそのまま。"
  (setv verdict (stop-verdict (is-terminal-status row.status) row.awaiting-response))
  (when (!= verdict "turn-cut")
    (return row))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv detail (+ f"sessionhost stopped ({reason}) while the turn was running — the headless process "
                  "goes down with the host, so the turn is cut here (the next turn resumes the session)"))
  (setv row (replace row :status "stopped"
                         :finished-at (or row.finished-at now-str)
                         :last-observed-at now-str
                         :awaiting-response False
                         :awaiting-response-since None
                         :last-validation-error detail))
  (setv row (cause-if-absent row (make-cause "cancelled" detail now-str)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event row.session-id "session_cancelled" row))
  row)


(defk stop-headless-rows [reason]
  {:pre [(: reason str)]
   :post [(: % dict)]}
  "host の停止(TERM)の腕(段 10 lane 10h 便 2・agora-redesign #84): 非終端の headless 行を 1 行ずつ
   stop-headless-row へ(手番の途中の行は stopped・idle の温かい行は触らない)、それから登記の全 process を
   並列の猶予で降ろす(HeadlessKillAll — launchd の process group の kill に任せて黙って落とさない)。
   行を先に倒す(停止の途中で SIGKILL されても、残りは次の起動の復帰が拾う)。per-session 隔離。
   戻り値: {session-id: 処理後 status | \"error:<ExceptionType>\", \"killed\": 降ろした数}。"
  (<- rows (session-store-list-active))
  (setv outcomes {})
  (for [row (sorted rows :key (fn [r] r.session-id))]
    (when (! (is-headless-row row))
      (try
        (<- stopped (stop-headless-row row reason))
        (setv (get outcomes row.session-id) stopped.status)
        (except [e Exception]
          (setv (get outcomes row.session-id)
                f"error:{(. (type e) __name__)}")))))
  (<- killed (headless-kill-all))
  (setv (get outcomes "killed") killed)
  outcomes)


(defk headless-monitor-cycle []
  {:pre []
   :post [(: % dict)]}
  "headless の monitor cycle: 非終端の headless 行の level-triggered 再導出 + 終端行の
   単一掃き取り。per-session 隔離(1 行の例外は捕捉して次へ)。戻り値:
   {session-id: 処理後 status | \"error:<ExceptionType>\"}。tui の行(backend が違う —
   共有 DB に残った旧行)には触れない。"
  (<- rows (session-store-list-active))
  (setv outcomes {})
  (for [row (sorted rows :key (fn [r] r.session-id))]
    (when (! (is-headless-row row))
      (try
        (<- updated (observe-headless-row row))
        (setv (get outcomes row.session-id) updated.status)
        (except [e Exception]
          (setv (get outcomes row.session-id)
                f"error:{(. (type e) __name__)}")))))
  (<- pending (session-store-list-cleanup-pending))
  (for [row (sorted pending :key (fn [r] r.session-id))]
    (when (and (! (is-headless-row row)) (not (reap-exempt row)))
      (try
        (<- _ (cleanup-headless-terminal-once row))
        (except [e Exception]
          (setv (get outcomes f"cleanup:{row.session-id}")
                f"error:{(. (type e) __name__)}")))))
  outcomes)
