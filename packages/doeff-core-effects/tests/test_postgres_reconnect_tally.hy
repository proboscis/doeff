;;; A sequence of requests that reads and writes rows of recording-service chunks (about 262,260 bytes per row) does not reopen the connection.
;;;   - Origin: agora-records-twin was reopening 242 connections every 30 seconds. For the rows whose body is split into 256 KiB chunks (turn-memory-part, worktree-diff),
;;;     adding the field names takes them slightly over 256 KiB, and with the former threshold (LARGE-CARRY-BYTES = 256 KiB) the connection was closed every time it was returned after passing such a row.
;;;   - Connect to a disposable PostgreSQL (postgres_support/disposable_postgres.py) and read the connections opened and the reasons for closing from the lending tally (PostgresConnections.tallies).
;;;     The rule that a connection that passed a value over the threshold is closed is checked by test_postgres_large_carry.hy.
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import doeff_core_effects [SqlQuery SqlTransaction SqlEnsureTables SqlParam SqlRows SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects._postgres_sql [PostgresTallies])
(import disposable_postgres [session-dsn session-postgres-skip-reason])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))

(val PARTS (SqlTable :name "tally_parts"
                     :columns #((SqlColumn :name "ledger" :type SqlColumnType.TEXT) (SqlColumn :name "key" :type SqlColumnType.TEXT)
                                (SqlColumn :name "payload" :type SqlColumnType.TEXT))
                     :primary-key #("ledger" "key")))

;; The size of one row of turn-memory-part in production (the value read on agora-pg on 2026-10-30 -- a 256 KiB chunk of body plus the field names).
(val PART-BYTES 262260)
(val PART-COUNT 4)
(val READS-PER-PART 10)


(defk part-payload [index]
  {:pre [(: index int)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "Makes the body of chunk index (ASCII of PART-BYTES characters -- different per chunk)."
  (val head (.format "{{\"part\":{},\"text\":\"" index))
  (+ head (* (chr (+ 97 index)) (- PART-BYTES (len head) 2)) "\"}"))


(defk part-written [index]
  {:pre [(: index int)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "Writes chunk index inside a transaction (the shape of a write of the recording service -- a bundled round trip)."
  (<- answer (SqlTransaction DB (part-inserted index) None True))
  (assert (isinstance answer SqlRows) answer)
  None)


(defk part-inserted [index]
  {:pre [(: index int)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "Runs the one statement that inserts the row of chunk index (the contents of the part-written transaction)."
  (<- payload (part-payload index))
  (<- answer (SqlQuery DB "INSERT INTO tally_parts (ledger, key, payload) VALUES (:ledger, :key, :payload)"
                       #((SqlParam :name "ledger" :value "turn-memory-part") (SqlParam :name "key" :value (str index))
                         (SqlParam :name "payload" :value payload))))
  (assert (isinstance answer SqlRows) answer)
  answer)


(defk part-read [index]
  {:pre [(: index int)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "Reads chunk index with a one-row read (the shape of a state read of the recording service)."
  (<- answer (SqlQuery DB "SELECT payload FROM tally_parts WHERE ledger = :ledger AND key = :key"
                       #((SqlParam :name "ledger" :value "turn-memory-part") (SqlParam :name "key" :value (str index)))))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0 0))


(defk part-requests []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "Fires a sequence of requests that writes PART-COUNT chunks and reads each READS-PER-PART times, and returns the number of requests fired."
  (<- (SqlEnsureTables DB #(PARTS)))
  (<- (SqlQuery DB "DELETE FROM tally_parts" #()))
  (for [index (range PART-COUNT)]
    (<- (part-written index)))
  (for [index (range PART-COUNT)]
    (<- expected (part-payload index))
    (for [_ (range READS-PER-PART)]
      (<- seen (part-read index))
      (assert (= seen expected) (len seen))))
  (+ 2 PART-COUNT (* PART-COUNT READS-PER-PART)))


(deftest test-postgres-reuses-connections-across-part-sized-rows
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; A sequence of 46 requests that read and write rows of chunk size opens 1 connection (0 closed connections). With the former threshold of 256 KiB it closed on every request,
  ;; and reopened over the 44 writes and reads (measured locally on 2026-10-30 -- 44 connections opened, 44 closed for reason large).
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn POSTGRES-DSN)) :size 1))
  (try
    (<- requests (with-handler [(postgres-sql-handler connections)] (part-requests)))
    (val tallies (.tallies connections))
    (finally (.close connections)))
  (print "requests" requests "tally" tallies)
  (assert (= requests 46) requests)
  (assert (= tallies (PostgresTallies :opened 1 :broken 0 :pipeline 0 :large 0 :rollback 0)) tallies))
