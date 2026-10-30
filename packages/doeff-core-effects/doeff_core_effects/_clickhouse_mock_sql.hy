;;; ClickHouse の約束を持つ模擬の SQL の答え手 clickhouse-mock-sql-handler(card ki-1c9c7903dfc4 — moneta の帳簿を ClickHouse へ移す
;;; 書き直しを、模擬の test で確かめるための前提)。sqlite の答え手(sqlite_sql.hy)の道を包み、本物の clickhouse-http-sql-handler
;;; (clickhouse_http_sql.hy)と同じ約束だけを足す。sqlite と同じく I/O なし・実 process なし。
;;;   - SqlTransaction は本物と同じ値(_clickhouse-transaction-refusal — SqlFailed(0A000))で断る。program は走らせない。
;;;   - SqlQuery の SqlRows.rowcount は None(HTTP が数えない)。SqlInsertRows は本物と同じく書いた行の数(空の rows は 0・bytes の値は
;;;     ValueError)。
;;;   - SqlEnsureTables は本物と同じく一意の索引を SqlFailed(0A000)で断る(clickhouse-schema-statements の検めを通す)。
;;;   - SqlBatch には答えない(本物も答えない — 外側へ回る)。
;;;   - SetSqlOutage・SQL の失敗の写しは sqlite の答え手のまま(SqlFailed の sqlstate は sqlite の類。ClickHouse の None ではない)。
;;; 方言の差は埋めない: 呼び手は sqlite と ClickHouse の両方で通る文を書く。本物の ClickHouse の意味(型・engine・関数)を持つ chdb を
;;; 使う道は取らなかった訳は card の発言に書いた。
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
  "SqlQuery の答えの rowcount を None にするため(頭の註 — 失敗はそのまま)。"
  (if (isinstance answer SqlRows)
      (SqlRows :rows answer.rows :rowcount None)
      answer))


(defk clickhouse-mock-insert [connection unreachable request]
  {:pre [(: connection sqlite3.Connection) (: unreachable tuple) (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows に本物の答え手と同じ前置きの約束(空は 0 行・bytes は ValueError)を通してから sqlite へ渡すため。"
  (when (not request.rows)
    (return (SqlRows :rows #() :rowcount 0)))
  (<- (checked-rows request.columns request.rows))
  (for [row request.rows]
    (when (any (gfor value row (isinstance value bytes)))
      (raise (ValueError (.format "ClickHouse の JSON の投入は bytes の値を運べない(表 {})" request.table)))))
  (<- answer (sqlite-answer-insert connection unreachable request))
  answer)


(defk clickhouse-mock-tables [connection unreachable database tables]
  {:pre [(: connection sqlite3.Connection) (: unreachable tuple) (: database str) (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlEnsureTables に本物の答え手と同じ検め(一意の索引は SqlFailed 0A000)を通してから sqlite へ渡すため。"
  (<- checked (clickhouse-schema-statements tables))
  (when (isinstance checked SqlFailed)
    (return checked))
  (<- answer (sqlite-answer-tables connection unreachable database tables))
  answer)


(defhandler clickhouse-mock-sql-handler [#^ (get tuple #(str ...)) databases]
  ;; 引数に残す理由: 答える database の名で本物の答え手と同じ組に並べ分ける(Ask では組の中の区別が付かない)。
  "ClickHouse の約束を持つ模擬の SQL の答え手(頭の註)。databases = 答える database の名の tuple(他の名の effect は外側へ回す)。"
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
