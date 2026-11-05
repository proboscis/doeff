;;; clickhouse-mock-sql-handler, a mock SQL responder with the ClickHouse conventions (a premise for verifying, with mock tests, the rewrite that moves moneta's ledger
;;; to ClickHouse). It wraps the route of the sqlite responder (sqlite_sql.hy) and adds only the same conventions as the real clickhouse-http-sql-handler
;;; (clickhouse_http_sql.hy). Like sqlite, no I/O and no real process.
;;;   - SqlTransaction is refused with the same value as the real one (_clickhouse-transaction-refusal -- SqlFailed(0A000)). The program is not run.
;;;   - SqlRows.rowcount of SqlQuery is None (HTTP does not count). SqlInsertRows returns the number of rows written as the real one does (0 for empty rows;
;;;     ValueError for bytes values).
;;;   - SqlEnsureTables refuses a unique index with SqlFailed(0A000) as the real one does (passes the check of clickhouse-schema-statements).
;;;   - SqlBatch is not answered (the real one does not answer either -- it passes outward).
;;;   - SetSqlOutage and the copy of SQL failures stay as in the sqlite responder (the sqlstate of SqlFailed is sqlite's kind, not ClickHouse's None).
;;; Dialect differences are not filled in: callers write statements that pass on both sqlite and ClickHouse. The reason the route of using chdb, which has real ClickHouse semantics
;;; (types, engines, functions), was not taken is written in the card's comment.
(require doeff-hy.macros [defhandler defk <- val var])
(import sqlite3)
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlTable checked-rows checked-identifier])
(import doeff_core_effects._clickhouse_http_sql [clickhouse-schema-statements _clickhouse-transaction-refusal])
(import doeff_core_effects._sqlite_sql [with-connection connection-of outage-marked sqlite-answer-query sqlite-answer-insert
                                       sqlite-answer-tables])


(defk without-count [answer]
  {:pre [(: answer (| SqlRows SqlFailed SqlUnreachable))] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To make the rowcount of the SqlQuery answer None (see the header comment -- failures pass as they are)."
  (if (isinstance answer SqlRows)
      (SqlRows :rows answer.rows :rowcount None)
      answer))


(defk clickhouse-mock-insert [connection unreachable request]
  {:pre [(: connection sqlite3.Connection) (: unreachable tuple) (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To pass SqlInsertRows through the same preliminary conventions as the real responder (0 rows for empty, ValueError for bytes) and then hand it to sqlite."
  (when (not request.rows)
    (return (SqlRows :rows #() :rowcount 0)))
  (<- (checked-rows request.columns request.rows))
  (for [row request.rows]
    (when (any (gfor value row (isinstance value bytes)))
      (raise (ValueError (.format "ClickHouse JSON insertion cannot carry bytes values (table {})" request.table)))))
  (<- answer (sqlite-answer-insert connection unreachable request))
  answer)


(defk clickhouse-mock-tables [connection unreachable database tables]
  {:pre [(: connection sqlite3.Connection) (: unreachable tuple) (: database str) (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To pass SqlEnsureTables through the same check as the real responder (a unique index is SqlFailed 0A000) and then hand it to sqlite."
  (<- checked (clickhouse-schema-statements tables))
  (when (isinstance checked SqlFailed)
    (return checked))
  (<- answer (sqlite-answer-tables connection unreachable database tables))
  answer)


(defhandler clickhouse-mock-sql-handler [#^ (get tuple #(str ...)) databases]
  ;; Why it stays an argument: it sorts into the same group as the real responder by the names of the databases it answers (Ask cannot distinguish within a group).
  "A mock SQL responder with the ClickHouse conventions (see the header comment). databases = a tuple of the names of the databases it answers (effects for other names pass outward)."
  {:tags {:context "sql" :role "foundation"}}
  (session var connections #())
  (session var unreachable #())
  (SetSqlOutage [database down] :when (in database databases)
    (<- marked (outage-marked unreachable database down))
    (:= unreachable marked)
    (resume None))
  (SqlQuery [database statement params] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (sqlite-answer-query connection unreachable (SqlQuery database statement params)))
    (<- counted (without-count answer))
    (resume counted))
  (SqlInsertRows [database table columns rows] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (clickhouse-mock-insert connection unreachable (SqlInsertRows database table columns rows)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (clickhouse-mock-tables connection unreachable database tables))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (in database databases)
    (<- refusal (_clickhouse-transaction-refusal database))
    (resume refusal)))
