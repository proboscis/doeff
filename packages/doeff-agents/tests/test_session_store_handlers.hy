;;; session の行の保存の handler の検(agora-redesign #608)。
;;;
;;; 受入: memory と PostgreSQL の handler で同じ検が緑。法の本体は
;;; `session_store_laws.hy` に 1 つだけ置き、ここでは handler の組ごとに同じ法を並べる:
;;;   - memory(`InMemoryAgentSessionRepository`・検と模擬の既定)
;;;   - JSONL(既存の file の repository を記憶の中の file 系で)
;;;   - SQLite(`:memory:` と file)
;;;   - PostgreSQL(環境変数 DOEFF_AGENTS_TEST_PG_DSN が在る時。無ければ skip と表示する —
;;;     緑とは数えない)。例: docker run -d -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:17-alpine
;;;     → DOEFF_AGENTS_TEST_PG_DSN=postgresql://postgres:pw@127.0.0.1:55432/postgres
;;;     psycopg は doeff-agents の依存に無い(接続は配備の composition root が開く)ので、
;;;     `uv run --with 'psycopg[binary]' pytest …` で足して走らせる。

(require doeff-hy.macros [deftest <-])

(import importlib)
(import os)
(import sqlite3)
(import uuid)
(import pathlib [Path])

(import doeff [run])
(import doeff_agents.effects [AgentSessionQuery GetAgentSession PutAgentSession])
(import doeff_agents.io_fake [FakeIoWorld run-fake-io])
(import doeff_agents.session_store [InMemoryAgentSessionRepository JsonlAgentSessionRepository])
(import doeff_agents.session_store_sql [
  COLUMN-NAMES
  POSTGRES
  QUERY-COLUMNS
  SQLITE
  checked-table-name
  select-many-statement
  session-table-ddl
  upsert-statement])
(import doeff_agents.handlers.session_store [SqlAgentSessionRepository agent-session-store])
(import session_store_laws [LAWS LAW-NAMES full-snapshot minimal-snapshot])


(setv PG-DSN-VARIABLE "DOEFF_AGENTS_TEST_PG_DSN")
(setv PG-DSN (os.environ.get PG-DSN-VARIABLE))


(defn run-law [repository law-name]
  "法 1 つを repository の handler の下で回す。"
  ((agent-session-store repository) ((get LAWS law-name))))


(defn assert-every-law-holds [open-repository]
  "法を 1 つずつ新しい repository で回し、破れた法を名と理由で全部並べる。
   (doeff-adr の Hy の file の収集は :params を展開しないので、法ごとの分け方はここで持つ)"
  (setv broken {})
  (for [law-name LAW-NAMES]
    (try
      (run (run-law (open-repository) law-name))
      (except [error Exception]
        (setv (get broken law-name) f"{(. (type error) __name__)}: {error}"))))
  (assert (= broken {}) f"破れた法: {broken}"))


(defn jsonl-repository []
  (setv world (FakeIoWorld))
  (JsonlAgentSessionRepository (Path "/store/sessions")
                               :io-root (fn [program] (run-fake-io world program))))


(defn open-postgres []
  ;; psycopg は doeff-agents の依存に無い(配備の composition root が持つ)ので、
  ;; PostgreSQL の検を走らせる時だけ名で読む。
  (setv psycopg (importlib.import-module "psycopg"))
  (psycopg.connect PG-DSN))


(defn unique-table []
  f"agent_runtime_sessions_t{(cut (. (uuid.uuid4) hex) 0 12)}")


(defn drop-table [connection table]
  (.execute connection f"DROP TABLE IF EXISTS {(checked-table-name table)}")
  (.commit connection))


;; ---------------------------------------------------------------------------
;; 同じ法を handler の組ごとに
;; ---------------------------------------------------------------------------

(deftest test-session-store-laws-hold-on-memory
  (assert-every-law-holds InMemoryAgentSessionRepository)
  None)


(deftest test-session-store-laws-hold-on-jsonl-files
  (assert-every-law-holds jsonl-repository)
  None)


(deftest test-session-store-laws-hold-on-sqlite
  (setv connections [])
  (defn open-sqlite []
    (setv connection (sqlite3.connect ":memory:"))
    (.append connections connection)
    (SqlAgentSessionRepository connection SQLITE))
  (try
    (assert-every-law-holds open-sqlite)
    (finally
      (for [connection connections] (.close connection))))
  None)


(deftest test-session-store-laws-hold-on-postgres
  {:skip-if (not PG-DSN)
   :skip-reason "DOEFF_AGENTS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  ;; 法ごとに新しい表(名は乱数)を同じ接続に作り、最後に消す。
  (setv connection (open-postgres))
  (setv tables [])
  (defn open-pg []
    (setv table (unique-table))
    (.append tables table)
    (SqlAgentSessionRepository connection POSTGRES table))
  (try
    (assert-every-law-holds open-pg)
    (finally
      (for [table tables] (drop-table connection table))
      (.close connection)))
  None)


;; ---------------------------------------------------------------------------
;; 永続の置き場だけの性質: 接続を開き直しても行が残る・表の用意は何度でも同じ
;; ---------------------------------------------------------------------------

(defn assert-rows-survive-reconnect [open-connection dialect table]
  (setv snapshot (full-snapshot "s-durable"))
  (setv first-connection (open-connection))
  (run ((agent-session-store (SqlAgentSessionRepository first-connection dialect table))
        (PutAgentSession snapshot)))
  (.close first-connection)
  (setv second-connection (open-connection))
  ;; 2 度目の repository は同じ表をもう一度用意する(IF NOT EXISTS で何も壊さない)。
  (setv found (run ((agent-session-store (SqlAgentSessionRepository second-connection dialect table))
                    (GetAgentSession "s-durable"))))
  (.close second-connection)
  (assert (= found snapshot) f"開き直した後に読めない: {found !r}"))


(deftest test-sqlite-file-rows-survive-reconnect [tmp-path]
  (setv path (str (/ tmp-path "sessions.sqlite3")))
  (assert-rows-survive-reconnect (fn [] (sqlite3.connect path)) SQLITE "agent_runtime_sessions")
  None)


(deftest test-postgres-rows-survive-reconnect
  {:skip-if (not PG-DSN)
   :skip-reason "DOEFF_AGENTS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (setv table (unique-table))
  (try
    (assert-rows-survive-reconnect open-postgres POSTGRES table)
    (finally
      (setv connection (open-postgres))
      (drop-table connection table)
      (.close connection)))
  None)


;; ---------------------------------------------------------------------------
;; SQL の判断(純粋な関数)
;; ---------------------------------------------------------------------------

(deftest test-every-query-field-has-a-sql-column
  ;; 欄が増えて写しが無いと SQL の答えが黙って広がる。import の時の検めの反例も確かめる。
  (import dataclasses [fields])
  (assert (= (sfor field (fields AgentSessionQuery) field.name) (set QUERY-COLUMNS)))
  (for [#(column _) (.values QUERY-COLUMNS)]
    (assert (in column COLUMN-NAMES) f"query の列が表に無い: {column}"))
  None)


(deftest test-list-statement-filters-only-the-fields-the-query-sets
  (setv statement (select-many-statement POSTGRES "agent_runtime_sessions"
                                         (AgentSessionQuery :caller-ref "agent-01" :node "node-a")))
  (assert (in "WHERE caller_ref = %s AND node = %s" statement.text) statement.text)
  (assert (= statement.params #("agent-01" "node-a")))
  (setv everything (select-many-statement SQLITE "agent_runtime_sessions" None))
  (assert (not (in "WHERE" everything.text)) everything.text)
  (assert (= everything.params #()))
  None)


(deftest test-table-name-is-checked-before-it-reaches-sql
  (for [bad ["Sessions" "x; DROP TABLE y" "" "1abc" "a-b"]]
    (try
      (checked-table-name bad)
      (raise (AssertionError f"通ってはいけない表の名が通った: {bad !r}"))
      (except [ValueError] None)))
  (try
    (session-table-ddl SQLITE "bad name")
    (raise (AssertionError "DDL が不正な表の名を通した"))
    (except [ValueError] None))
  None)


(deftest test-sql-refuses-times-without-a-zone
  ;; 時差の無い時刻は PostgreSQL の timestamptz では接続の時刻帯で意味が変わるので、置く前に断る。
  (import datetime [datetime])
  (setv naive (.with-update (minimal-snapshot "s-naive") :started-at (datetime 2026 9 25 12 0 0)))
  (try
    (upsert-statement SQLITE "agent_runtime_sessions" naive "put" {})
    (raise (AssertionError "時差の無い時刻が通った"))
    (except [ValueError] None))
  None)


(defn assert-failed-write-leaves-the-store-usable [connection dialect table]
  ;; 書きが DB に断られたら取引を取り消す。前の行は残り、同じ接続で次の操作ができる
  ;; (PostgreSQL は取り消さないと、以後の文が全部「取引が中断している」で断られる)。
  (setv repository (SqlAgentSessionRepository connection dialect table))
  (setv kept (full-snapshot "s-kept"))
  (.record-snapshot repository "put" kept)
  (setv refused False)
  (try
    ;; session_name は NOT NULL の列。型の無い値で DB 自身に断らせる。
    (.record-snapshot repository "put" (.with-update kept :session-id "s-bad" :session-name None))
    (except [Exception]
      (setv refused True)))
  (assert refused "NOT NULL の列への空が断られなかった")
  (assert (= (.get-session repository "s-kept") kept))
  (assert (is (.get-session repository "s-bad") None))
  (setv later (full-snapshot "s-after"))
  (.record-snapshot repository "put" later)
  (assert (= (.get-session repository "s-after") later)))


(deftest test-sqlite-failed-write-leaves-the-store-usable
  (setv connection (sqlite3.connect ":memory:"))
  (assert-failed-write-leaves-the-store-usable connection SQLITE "agent_runtime_sessions")
  (.close connection)
  None)


(deftest test-postgres-failed-write-leaves-the-store-usable
  {:skip-if (not PG-DSN)
   :skip-reason "DOEFF_AGENTS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (setv connection (open-postgres))
  (setv table (unique-table))
  (try
    (assert-failed-write-leaves-the-store-usable connection POSTGRES table)
    (finally
      (drop-table connection table)
      (.close connection)))
  None)


(deftest test-session-store-modules-know-nothing-of-acp
  ;; 受入「ACP に kind を足さない」の構造の側: 保存の effect・判断・handler の module は
  ;; ACP の client も sessionhost の ACP の腕も import しない(行の置き場は ACP の外)。
  (setv package (. (Path (. (__import__ "doeff_agents") __file__)) parent))
  (for [relative ["session_store_sql.hy" "handlers/session_store.hy" "session_store.py"]]
    (setv imports (lfor line (.splitlines (.read-text (/ package relative) :encoding "utf-8"))
                        :if (in "import" line)
                        line))
    (setv acp-imports (lfor line imports :if (in "acp" (.lower line)) line))
    (assert (= acp-imports []) f"{relative} が ACP を import している: {acp-imports}"))
  None)
