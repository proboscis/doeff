;;; 記録の service の切片の行(1 行 約 262,260 bytes)を読み書きする要求の列が、接続を開き直さない(card ki-004d4af4d6d3)。
;;;   - 出自: agora-records-twin は 30 秒に 242 本の接続を開き直していた。本文を 256 KiB の切片に割った行(turn-memory-part・worktree-diff)は
;;;     欄の名を足すと 256 KiB をわずかに越え、前の目安(LARGE-CARRY-BYTES = 256 KiB)ではその行を通した接続を返す時に毎回閉じていた。
;;;   - 使い捨ての PostgreSQL(postgres_support/disposable_postgres.py)へ繋ぎ、貸し出しの数え(PostgresConnections.tallies)で開いた接続と
;;;     閉じた訳を読む。目安を越える値を通した接続を閉じる決まりは test_postgres_large_carry.hy が見る。
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

;; 本番の turn-memory-part の行 1 つの大きさ(2026-10-30 に agora-pg で読んだ値 — 本文 256 KiB の切片と欄の名)。
(val PART-BYTES 262260)
(val PART-COUNT 4)
(val READS-PER-PART 10)


(defk part-payload [index]
  {:pre [(: index int)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "切片 index の本文(PART-BYTES 字の ASCII — 切片ごとに違える)を作るため。"
  (val head (.format "{{\"part\":{},\"text\":\"" index))
  (+ head (* (chr (+ 97 index)) (- PART-BYTES (len head) 2)) "\"}"))


(defk part-written [index]
  {:pre [(: index int)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "切片 index を transaction の中で書くため(記録の service の書きの形 — 束ねた往復)。"
  (<- answer (SqlTransaction DB (part-inserted index) None True))
  (assert (isinstance answer SqlRows) answer)
  None)


(defk part-inserted [index]
  {:pre [(: index int)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "切片 index の行を入れる文 1 つを流すため(part-written の transaction の中身)。"
  (<- payload (part-payload index))
  (<- answer (SqlQuery DB "INSERT INTO tally_parts (ledger, key, payload) VALUES (:ledger, :key, :payload)"
                       #((SqlParam :name "ledger" :value "turn-memory-part") (SqlParam :name "key" :value (str index))
                         (SqlParam :name "payload" :value payload))))
  (assert (isinstance answer SqlRows) answer)
  answer)


(defk part-read [index]
  {:pre [(: index int)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "切片 index を 1 行の読みで読むため(記録の service の state の読みの形)。"
  (<- answer (SqlQuery DB "SELECT payload FROM tally_parts WHERE ledger = :ledger AND key = :key"
                       #((SqlParam :name "ledger" :value "turn-memory-part") (SqlParam :name "key" :value (str index)))))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0 0))


(defk part-requests []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "切片を PART-COUNT 個書き、それぞれを READS-PER-PART 回読む要求の列を撃ち、撃った要求の数を返すため。"
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
  ;; 切片の大きさの行を読み書きする 46 の要求の列で、開く接続は 1 本(閉じた接続は 0 本)。前の目安 256 KiB では要求ごとに閉じ、
  ;; 書きと読みの 44 回で開き直した(2026-10-30 の手元の測り — 開いた接続 44 本・閉じた訳 large 44 本)。
  (val connections (PostgresConnections #((PostgresDatabase :name DB :dsn POSTGRES-DSN)) :size 1))
  (try
    (<- requests (with-handler [(postgres-sql-handler connections)] (part-requests)))
    (val tallies (.tallies connections))
    (finally (.close connections)))
  (print "要求" requests "数え" tallies)
  (assert (= requests 46) requests)
  (assert (= tallies (PostgresTallies :opened 1 :broken 0 :pipeline 0 :large 0 :rollback 0)) tallies))
