;;; session の行の保存の法(agora-redesign #608)— handler に依らない検の本体。
;;;
;;; 各法は `PutAgentSession` / `GetAgentSession` / `ListAgentSessions` だけを出す
;;; Program で、どの repository の handler の下でも同じ答えになることを確かめる。
;;; 同じ法を memory・JSONL(記憶の中の file 系)・SQLite・PostgreSQL の handler で回す
;;; のは `test_session_store_handlers.hy`。
;;;
;;; 一覧の順は契約に無い(memory は入れた順・SQL は session id の順)ので、法は
;;; session id で並べてから比べる。

(require doeff-hy.macros [defk <-])

(import datetime [datetime timedelta timezone])
(import pathlib [Path])

(import doeff_agents.adapters.base [AgentSessionLifecycle AgentType])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.effects [
  AgentSessionQuery
  AgentSessionSnapshot
  GetAgentSession
  ListAgentSessionsEffect
  PutAgentSession
  TranscriptRef
  TurnRef])


;; 時差のある時刻(置き場所が UTC に揃えても同じ瞬間として読めること)。
(setv JST (timezone (timedelta :hours 9)))
(setv STARTED (datetime 2026 9 25 21 30 0 123456 :tzinfo JST))


(defn full-snapshot [session-id #** changes]
  "欄を全部埋めた snapshot(足した 4 欄を含む)。"
  (.with-update
    (AgentSessionSnapshot
      :session-id session-id
      :session-name f"name-{session-id}"
      :agent-type AgentType.CLAUDE
      :work-dir (Path "/work/agora")
      :status SessionStatus.RUNNING
      :lifecycle AgentSessionLifecycle.INTERACTIVE
      :backend-kind "headless"
      :backend-ref {"cli_session_id" f"cli-{session-id}" "profile" "p1"}
      :started-at STARTED
      :last-observed-at (+ STARTED (timedelta :seconds 30))
      :finished-at None
      :cleaned-at None
      :output-snippet "手番の出力の断片"
      :caller-ref "agent-01"
      :node "node-a"
      :last-turn (TurnRef :turn-id "turn-7" :attempt 1)
      :transcript-ref (TranscriptRef :node "node-a" :path "/home/a/.claude/projects/x.jsonl"))
    #** changes))


(defn minimal-snapshot [session-id]
  "任意の欄を空にした snapshot。"
  (AgentSessionSnapshot
    :session-id session-id
    :session-name session-id
    :agent-type AgentType.CODEX
    :work-dir (Path ".")
    :status SessionStatus.PENDING
    :started-at STARTED))


(defn by-session-id [snapshots]
  (tuple (sorted snapshots :key (fn [snapshot] snapshot.session-id))))


(defk list-sessions [query]
  {:pre [(: query AgentSessionQuery)]
   :post [(: % tuple)]}
  (<- found (ListAgentSessionsEffect :query query))
  (by-session-id found))


(defk put-all [snapshots]
  {:pre [(: snapshots tuple)]
   :post [(: % tuple)]}
  (setv stored [])
  (for [snapshot snapshots]
    (<- answer (PutAgentSession snapshot))
    (.append stored answer))
  (tuple stored))


;; ---------------------------------------------------------------------------
;; 法
;; ---------------------------------------------------------------------------

(defk law-put-then-get-returns-the-same-snapshot []
  {:pre [] :post [(: % (| bool None))]}
  "書いた snapshot を session id で読むと、足した 4 欄を含めて同じ値が返る。"
  (setv snapshot (full-snapshot "s-full"))
  (<- stored (PutAgentSession snapshot))
  (assert (= stored snapshot) f"書きの答えが書いた値と違う: {stored !r}")
  (<- found (GetAgentSession "s-full"))
  (assert (= found snapshot) f"読んだ値が違う: {found !r} != {snapshot !r}")
  (assert (= found.last-turn (TurnRef :turn-id "turn-7" :attempt 1)))
  (assert (= found.transcript-ref.path "/home/a/.claude/projects/x.jsonl"))
  None)


(defk law-optional-fields-stay-empty []
  {:pre [] :post [(: % (| bool None))]}
  "任意の欄を空にした snapshot は空のまま読める(空が別の値に化けない)。"
  (setv snapshot (minimal-snapshot "s-min"))
  (<- _ (PutAgentSession snapshot))
  (<- found (GetAgentSession "s-min"))
  (assert (= found snapshot) f"読んだ値が違う: {found !r}")
  (assert (is found.caller-ref None))
  (assert (is found.node None))
  (assert (is found.last-turn None))
  (assert (is found.transcript-ref None))
  (assert (= found.backend-ref {}))
  None)


(defk law-absent-session-reads-as-none []
  {:pre [] :post [(: % (| bool None))]}
  "書いていない session id は None で答える(例外にしない)。"
  (<- found (GetAgentSession "s-never-written"))
  (assert (is found None) f"無い行が読めた: {found !r}")
  (<- listed (list-sessions (AgentSessionQuery)))
  (assert (= listed #()) f"空の保存先の一覧が空でない: {listed !r}")
  None)


(defk law-put-replaces-the-row-with-the-same-session-id []
  {:pre [] :post [(: % (| bool None))]}
  "同じ session id の書きは行を置き換える(行は 1 つのまま・最後の書きが読める)。"
  (setv first (full-snapshot "s-replace"))
  (setv later (.with-update first
                :status SessionStatus.DONE
                :finished-at (+ STARTED (timedelta :minutes 5))
                :node "node-b"
                :last-turn (TurnRef :turn-id "turn-8" :attempt 2)
                :transcript-ref None
                :backend-ref {"cli_session_id" "cli-renewed"}))
  (<- _ (put-all #(first later)))
  (<- found (GetAgentSession "s-replace"))
  (assert (= found later) f"最後の書きが読めない: {found !r}")
  (<- listed (list-sessions (AgentSessionQuery)))
  (assert (= listed #(later)) f"行が 1 つでない: {listed !r}")
  None)


;; 絞りの検に使う行。どの欄も、合う行と合わない行が両方ある。
(setv FILTER-ROWS
  #((full-snapshot "s-1")
    (full-snapshot "s-2" :status SessionStatus.DONE :caller-ref "agent-02")
    (full-snapshot "s-3" :agent-type AgentType.CODEX :node "node-b" :backend-kind "terminal")
    (full-snapshot "s-4" :lifecycle AgentSessionLifecycle.RUN_TO_COMPLETION :caller-ref None :node None)
    (minimal-snapshot "s-5")))

(setv FILTER-QUERIES
  #((AgentSessionQuery)
    (AgentSessionQuery :status SessionStatus.RUNNING)
    (AgentSessionQuery :status SessionStatus.DONE)
    (AgentSessionQuery :agent-type AgentType.CODEX)
    (AgentSessionQuery :backend-kind "headless")
    (AgentSessionQuery :lifecycle AgentSessionLifecycle.INTERACTIVE)
    (AgentSessionQuery :caller-ref "agent-01")
    (AgentSessionQuery :caller-ref "agent-02")
    (AgentSessionQuery :node "node-a")
    (AgentSessionQuery :node "node-b")
    (AgentSessionQuery :caller-ref "agent-01" :node "node-a" :status SessionStatus.RUNNING)
    (AgentSessionQuery :caller-ref "agent-01" :node "node-b")
    (AgentSessionQuery :caller-ref "nobody")))


(defk law-list-answers-by-the-query-definition []
  {:pre [] :post [(: % (| bool None))]}
  "一覧の答えは、query の定義(`AgentSessionQuery.matches`)で全行を絞った結果と同じ。"
  (<- _ (put-all FILTER-ROWS))
  (for [query FILTER-QUERIES]
    (setv expected (by-session-id (gfor row FILTER-ROWS :if (.matches query row) row)))
    (<- listed (list-sessions query))
    (assert (= listed expected)
            f"query {query !r} の答えが違う: {(lfor s listed s.session-id)} != {(lfor s expected s.session-id)}"))
  None)


(defk law-caller-ref-finds-the-sessions-of-one-caller []
  {:pre [] :post [(: % (| bool None))]}
  "呼び手の識別子で引くと、その呼び手の行だけが全部返る(agora が agent の id から
   前の session を探す口)。"
  (<- _ (put-all #((full-snapshot "s-a1" :caller-ref "agent-x")
                   (full-snapshot "s-a2" :caller-ref "agent-x" :status SessionStatus.DONE)
                   (full-snapshot "s-b1" :caller-ref "agent-y"))))
  (<- listed (list-sessions (AgentSessionQuery :caller-ref "agent-x")))
  (assert (= (tuple (gfor s listed s.session-id)) #("s-a1" "s-a2")) f"答えが違う: {listed !r}")
  None)


;; 名 → 法。検の file はこの表を params に並べる。
(setv LAWS
  {"put-then-get-returns-the-same-snapshot" law-put-then-get-returns-the-same-snapshot
   "optional-fields-stay-empty" law-optional-fields-stay-empty
   "absent-session-reads-as-none" law-absent-session-reads-as-none
   "put-replaces-the-row-with-the-same-session-id" law-put-replaces-the-row-with-the-same-session-id
   "list-answers-by-the-query-definition" law-list-answers-by-the-query-definition
   "caller-ref-finds-the-sessions-of-one-caller" law-caller-ref-finds-the-sessions-of-one-caller})

(setv LAW-NAMES (sorted LAWS))
