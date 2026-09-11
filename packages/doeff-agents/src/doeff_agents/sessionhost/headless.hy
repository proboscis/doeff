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
;;; claude は 1 手番 1 process なので、次の手番の send は `--resume <sid>` の process を
;;; 同じ session の名で起こし直してから stdin へ書く(温かい = 会話の資源としての行と
;;; events file が続く)。codex の app-server は process が生きたまま turn/start。

(require doeff-hy.macros [defk deff <-])

(import dataclasses [replace])
(import os)

(import doeff_agents.sessionhost.effects [
  SessionRow
  build-headless-launch
  clock-now
  fs-read-text
  headless-deliver
  headless-has-session
  headless-interrupt
  headless-kill
  headless-poll
  headless-spawn
  session-store-get
  session-store-list-active
  session-store-list-cleanup-pending
  session-store-record-event
  session-store-upsert
  wire-result-channel])
(import doeff_agents.sessionhost.headless_protocol [
  HeadlessObservation
  Verdict
  turn-verdict])
(import doeff_agents.sessionhost.launch [
  INTERACTIVE-AGENT-TYPES
  RESULT-PROTOCOL-INSTRUCTION
  admit-launch
  launch-spawn-env
  prepare-launch-workspace
  session-hooks-mode])
(import doeff_agents.sessionhost.policy [
  cause-if-absent
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


;; ---------------------------------------------------------------------------
;; 純粋な小片(path・行の読み)
;; ---------------------------------------------------------------------------

(deff headless-events-path [events-root session-id]
  {:pre [(: events-root str) (: session-id str)]
   :post [(: % str)]}
  "session の events file(stdout の行を 1 行 1 event で追記する実況の正本)の置き場。
   backend_ref.events_path として行に載り、agentd はそこから offset で読む。"
  (os.path.join events-root f"{session-id}.events.jsonl"))


(deff is-headless-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % bool)]}
  (= row.backend-kind HEADLESS-BACKEND-KIND))


(deff headless-backend-ref [session-name pid events-path argv socket-path]
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


(deff events-path-of-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % (| str None))]}
  (setv ref (or row.backend-ref {}))
  (setv path (.get ref "events_path"))
  (if (isinstance path str) path None))


(defk require-headless-row [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session 行の必須読み(host.hy require-session-row と同文言)+ backend の照合。"
  (<- row (session-store-get session-id))
  (when (is row None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (when (not (is-headless-row row))
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
  (setv effective (dict params))
  (setv (get effective "session_hooks") session-hooks)
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
  (setv effective-env (launch-spawn-env identity session-env))
  (setv events-path (headless-events-path events-root session-id))
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
              :backend-ref (headless-backend-ref session-name pid events-path argv
                                                 (str (.get params "socket_path" "")))
              :launch-overlay {"session_env" session-env
                               "model" (.get params "model")
                               "effort" (.get params "effort")
                               "mcp_servers" (or (.get params "mcp_servers") {})}
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
    (<- delivered (headless-deliver session-name full-prompt))
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


(defk continue-headless-process [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionRow)]}
  "claude の次の手番(1 手番 1 process): 行の会話 identity で `--resume <sid>` の process を
   同じ session の名で起こし直す(events file は同じ path に追記)。会話の id が無い行は
   続けられない(発明しない — 型付きに断る)。戻り値: backend_ref を更新した行。"
  (when (is row.conversation None)
    (raise (RuntimeError
             (+ f"session.send: session {row.session-id} has no conversation identity — "
                "the next headless turn cannot be resumed"))))
  (setv overlay (or row.launch-overlay {}))
  (setv params {"agent_type" row.agent-type
                "work_dir" row.work-dir
                "model" (.get overlay "model")
                "effort" (.get overlay "effort")
                "mcp_servers" (or (.get overlay "mcp_servers") {})
                "expected_result" row.expected-result})
  (setv ref (or row.backend-ref {}))
  (<- built (headless-launch-args params row.effective-identity row.conversation "resume"
                                  (str (.get ref "socket_path" "")) row.session-id))
  (setv argv (get built "argv"))
  (setv effective-env (launch-spawn-env row.effective-identity
                                        (dict (or (.get overlay "session_env") {}))))
  (setv events-path (or (events-path-of-row row)
                        (raise (RuntimeError
                                 f"session {row.session-id} has no events_path in backend_ref"))))
  (<- pid (headless-spawn row.session-name row.work-dir effective-env argv events-path
                          (get built "dialogue")))
  (setv next-ref (dict ref))
  (.update next-ref (headless-backend-ref row.session-name pid events-path argv
                                          (str (.get ref "socket_path" ""))))
  (replace row :backend-ref next-ref))


;; ---------------------------------------------------------------------------
;; RPC の program(send / interrupt / cancel / cleanup / capture)
;; ---------------------------------------------------------------------------

(defk headless-send-program [session-id message awaiting]
  {:pre [(: session-id str) (: message str) (: awaiting bool)]
   :post [(: % SessionRow)]}
  "session.send(headless): 次の手番の本文を stdin へ。process が次の手番を受けられる
   (codex の生きた app-server)ならそのまま、受けられない(claude の降りた process)なら
   `--resume` で起こし直してから書く。awaiting(agentd の温かい手番)は latch を立て、
   turn_ended_at を None に戻す(次の手番が走り出した — level-triggered の欄)。"
  (<- row (require-headless-row session-id))
  (when (is-terminal-status row.status)
    (raise (RuntimeError f"session {session-id} is {row.status}; cannot send to a terminal session")))
  (<- observed (headless-poll row.session-name))
  (setv accepts (and (isinstance observed HeadlessObservation) observed.accepts-turn))
  (when (not accepts)
    (<- continued (continue-headless-process row))
    (setv row continued))
  (<- delivered (headless-deliver row.session-name message))
  (when (not delivered)
    (raise (RuntimeError
             f"session.send: headless process of {session-id} is not accepting input")))
  (<- now (clock-now))
  (setv row (replace row :last-observed-at (iso-format now)))
  (when awaiting
    (setv row (replace row :awaiting-response True
                           :awaiting-response-since (iso-format now)
                           :turn-ended-at None)))
  (<- _ (session-store-upsert row))
  (<- _ (session-store-record-event session-id "session_sent" row))
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


(deff tail-lines [text lines]
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
  (setv path (events-path-of-row row))
  (setv text "")
  (when (is-not path None)
    (<- raw (fs-read-text path))
    (setv text (tail-lines (or raw "") lines)))
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

(defk observe-headless-row [row]
  {:pre [(: row SessionRow)]
   :post [(: % SessionRow)]}
  "1 行の level-triggered 再導出: 器の観測(HeadlessPoll)→ 次の 1 手(turn_verdict の
   純関数 1 点)→ 行の欄に写す。
   running = 観測の刻印だけ / turn-ended = multi_turn は turn_ended_at を刻み awaiting を
   下ろす(status は倒さない・session は残す)、run_to_completion は done(走行器が失敗と
   名乗れば failed)/ failed = 型付きの失敗・手番の途中の process の死 → failed + cause /
   gone = 器に process が無いのに手番の途中(host の再起動)→ failed + cause vanished /
   idle = 温かい・何もしない。会話の id(codex の thread)は見つけた拍に行へ写す。"
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
          (setv row (replace row :turn-ended-at (or row.turn-ended-at observed-at)))
          (<- _ (session-store-upsert row))
          (<- _ (session-store-record-event row.session-id EVENT-SESSION-TURN-ENDED row)))
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
    (when (is-headless-row row)
      (try
        (<- updated (observe-headless-row row))
        (setv (get outcomes row.session-id) updated.status)
        (except [e Exception]
          (setv (get outcomes row.session-id)
                f"error:{(. (type e) __name__)}")))))
  (<- pending (session-store-list-cleanup-pending))
  (for [row (sorted pending :key (fn [r] r.session-id))]
    (when (and (is-headless-row row) (not (reap-exempt row)))
      (try
        (<- _ (cleanup-headless-terminal-once row))
        (except [e Exception]
          (setv (get outcomes f"cleanup:{row.session-id}")
                f"error:{(. (type e) __name__)}")))))
  outcomes)
