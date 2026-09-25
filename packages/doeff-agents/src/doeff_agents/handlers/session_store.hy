;;; agent runtime の session の行の保存の handler(agora-redesign #608)。
;;;
;;; 既知の形: algebraic effects。`PutAgentSession` / `GetAgentSession` /
;;; `ListAgentSessions` の 3 つの effect を、composition root が選んだ repository
;;; (`AgentSessionRepository` の形)で果たす。handler は答えを素通しするだけで、
;;; 判断は持たない。
;;;
;;; 配備ごとの組:
;;;   - 検・模擬: `(agent-session-store (InMemoryAgentSessionRepository))`
;;;   - クラスタ: `(agent-session-store (SqlAgentSessionRepository <psycopg の接続> POSTGRES))`
;;;     — agora の状態の正本の PostgreSQL に別の表(operator 8.1.5 の 5f)。
;;;   - 手元で残したい時: `(SqlAgentSessionRepository (sqlite3.connect <path>) SQLITE)`
;;;
;;; 同じ effect を別の handler(TmuxAgentHandler / DaemonAgentHandler の節)も
;;; 答えるので、どれを内側に置くかは composition root が 1 つ選ぶ。
;;;
;;; I/O の家: SQL の文を接続へ流すのはこの module の `SqlAgentSessionRepository`
;;; ちょうど 1 つ。何を流すか(DDL・書き・読み・行の写し)は
;;; `doeff_agents.session_store_sql` の純粋な関数が決める(ADR-DOE-AGENTS-013 R4)。

(require doeff-hy.handle [defhandler])

(import threading)

(import doeff_agents.effects [
  AgentSessionQuery
  AgentSessionSnapshot
  GetAgentSessionEffect
  ListAgentSessionsEffect
  PutAgentSessionEffect])
(import doeff_agents.session_store_sql [
  DEFAULT-TABLE
  SqlDialect
  checked-table-name
  row-to-snapshot
  select-many-statement
  select-one-statement
  session-table-ddl
  upsert-statement])


;; handler が書く時の出来事の名(repository の record_snapshot の event_type)。
(setv PUT-EVENT "put")


(defclass SqlAgentSessionRepository []
  "SQL の表 1 つに session の行を置く repository(PostgreSQL と SQLite)。

   connection は DB-API 2 の接続(psycopg 3 の `Connection` か `sqlite3.Connection`)で、
   開く・閉じるは composition root が持つ。操作ごとに 1 取引で流して commit し、
   失敗は rollback してから投げ直す。1 つの接続を lock で直列に使う。
   作る時に表と索引を用意する(何度でも同じ)。"

  (defn __init__ [self connection dialect [table DEFAULT-TABLE]]
    (when (not (isinstance dialect SqlDialect))
      (raise (TypeError f"dialect は SqlDialect: {dialect !r}")))
    (setv self.connection connection)
    (setv self.dialect dialect)
    (setv self.table (checked-table-name table))
    (setv self._lock (threading.Lock))
    (._transact self (session-table-ddl dialect self.table))
    None)

  (defn record-snapshot [self event-type snapshot * [details None]]
    (when (not (isinstance snapshot AgentSessionSnapshot))
      (raise (TypeError f"snapshot は AgentSessionSnapshot: {snapshot !r}")))
    (._transact self #((upsert-statement self.dialect self.table snapshot event-type
                                         (dict (or details {})))))
    snapshot)

  (defn get-session [self session-id]
    (setv rows (._transact self #((select-one-statement self.dialect self.table session-id))))
    (if rows (row-to-snapshot (get rows 0)) None))

  (defn list-sessions [self [query None]]
    (when (not (isinstance query (| AgentSessionQuery None)))
      (raise (TypeError f"query は AgentSessionQuery か None: {query !r}")))
    (setv rows (._transact self #((select-many-statement self.dialect self.table query))))
    (tuple (gfor row rows (row-to-snapshot row))))

  (defn _transact [self statements]
    "文の並びを 1 取引で流し、最後の文が返した行を返す。"
    (with [self._lock]
      (setv cursor (.cursor self.connection))
      (try
        (for [statement statements]
          (.execute cursor statement.text statement.params))
        (setv rows (if (is cursor.description None) [] (list (.fetchall cursor))))
        (.commit self.connection)
        (except [Exception]
          (.rollback self.connection)
          (raise))
        (finally
          (.close cursor))))
    rows))


(defhandler agent-session-store [repository]
  "session の行の保存の effect を repository で果たす。"

  (PutAgentSessionEffect [snapshot]
    (resume (.record-snapshot repository PUT-EVENT snapshot)))

  (GetAgentSessionEffect [session-id]
    (resume (.get-session repository session-id)))

  (ListAgentSessionsEffect [query]
    (resume (.list-sessions repository query))))
