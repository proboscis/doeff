;; PostgreSQL の置き場の回収(SweepExpired)が読んで値を解く行は、保持の期限を過ぎた終端の行だけである事。
;; 2026-10-22 の本番: 手入れの回収 1 回が、期限の在る表の終端の行を期限の前の行も含めて全部、値ごと読んだ。SQL の pool の thread が結果を
;; Python の値へ解く間 5〜9 秒、続いて scheduler の thread が 1 行ずつ値を解いて期限を判じる間 約 8 秒、それぞれ CPU 1 本分に張り付き、
;; scheduler の 1 歩が終わるまで他の要求の task は始まらなかった — 手入れの周期(60 秒 + 所要)ごとに、軽い読み 1 本が 7〜10 秒待った。
;; 性質: 期限を過ぎた終端の行 EXPIRED-ROWS 行・期限の前の終端の行 FRESH-ROWS 行・終端でない行 OPEN-ROWS 行の置き場で SweepExpired を
;; 1 回撃つと、回収の候補の読み(state_rows を終端の状態で引く文 — 錠の外と錠の中)が返す行はどれも EXPIRED-ROWS 行で、消えるのは期限を
;; 過ぎた行だけ(期限の前の終端の行と終端でない行は読める)。
;; 反例(前の形 — 候補の読みの文に最後に書いた刻の条件が無い): 候補の読みが EXPIRED-ROWS + FRESH-ROWS 行を返し、赤。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [defeffect defhandler deftest defk <- val var])
(import doeff [run with_handlers])
(import doeff_hy._frozen [FrozenMap])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_core_effects.sql_effects [SqlQuery SqlRows])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_records [Row Missing ExpectAbsent Written])
(import doeff_records [PutRow ReadRow])
(import doeff_records.maintenance [SweepExpired Swept])
(import doeff_records._laws [MAKER TICKET-KEEP-SECONDS])
(import doeff_records [pg-records-handler]
        doeff_records._pg [drop-records-tables])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason ORIGIN-HOST postgres-connections fresh-prefix run-sql
                            prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))

;; 置き場の行: 期限を過ぎる終端の行・期限の前の終端の行・終端でない行の数(期限の前の終端の行を多く置き、前の形の読みとの差を大きくする)。
(val EXPIRED-ROWS 3)
(val FRESH-ROWS 30)
(val OPEN-ROWS 5)
;; 回収の候補の読みの文の頭(pg_sql.terminal-rows-statement — 接頭辞の後ろ)。
(val CANDIDATE-HEAD "SELECT key, payload, version, updated_at FROM ")


(defeffect ReadTally
  "rows-tally が控えた #(文 答えた行の数) の列(流した順)を読む。"
  {:fields [] :answer tuple :tags {:context "records" :role "program"}})


(defhandler rows-tally
  "置き場の handler が流した文ごとに、答えた行の数を控えるため(回収の候補の読みが期限の前の行を返していないかを数で読む)。答えは
   そのまま返す。答えが行でない文(届かない・失敗)は行の数の代わりに答えの型の名を控える。"
  {:tags {:context "records" :role "program"}}
  (session var tally #())
  (SqlQuery [database statement params]
    (<- answer effect)
    (:= tally (+ tally #(#(statement (if (isinstance answer SqlRows) (len answer.rows) (. (type answer) __name__))))))
    (resume answer))
  (ReadTally []
    (resume tally)))


(defk put-tickets [head count state]
  {:pre [(: head str) (: count int) (: state str)] :post [(: % None)]
   :tags {:context "records" :role "program"}}
  "表 tickets に状態 state の行を count 行(鍵 #(\"g\" head1) …)書くため(書いた刻は今の仮想の時計)。"
  (for [n (range 1 (+ count 1))]
    (<- written (PutRow "tickets" #("g" (.format "{}{}" head n)) (FrozenMap {"state" state}) (ExpectAbsent)))
    (assert (isinstance written Written) (repr written)))
  None)


(defk sweep-after-expiry []
  {:pre [] :post [(: % dict)]
   :tags {:context "records" :role "program"}}
  "期限を過ぎる終端の行を書き、保持の秒の後に期限の前の終端の行と終端でない行を書いてから SweepExpired を 1 回撃ち、
   {名 値} で回収の答え・3 種の行の読み・回収が流した文の控え(rows-tally — 回収の前の控えは除く)を返すため。"
  (<- (put-tickets "old-" EXPIRED-ROWS "done"))
  (<- (Delay (+ TICKET-KEEP-SECONDS 1)))
  (<- (put-tickets "fresh-" FRESH-ROWS "done"))
  (<- (put-tickets "open-" OPEN-ROWS "open"))
  (<- written tuple (ReadTally))
  (<- swept (SweepExpired))
  (<- tally tuple (ReadTally))
  (<- old (ReadRow "tickets" #("g" "old-1")))
  (<- fresh (ReadRow "tickets" #("g" (.format "fresh-{}" FRESH-ROWS))))
  (<- open (ReadRow "tickets" #("g" "open-1")))
  {"swept" swept "old" old "fresh" fresh "open" open "sweep-statements" (cut tally (len written) None)})


(deftest test-a-sweep-reads-and-decodes-only-the-terminal-rows-past-their-retention
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (val outcome (run (scheduled (with_handlers [(state) (sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                 rows-tally (pg-records-handler store MAKER ORIGIN-HOST)]
                                                (sweep-after-expiry)))))
    ;; 回収は期限を過ぎた行だけを消し、期限の前の終端の行と終端でない行は読める。
    (assert (= (get outcome "swept") (Swept EXPIRED-ROWS 0)) (repr outcome))
    (assert (isinstance (get outcome "old") Missing) (repr outcome))
    (assert (isinstance (get outcome "fresh") Row) (repr outcome))
    (assert (isinstance (get outcome "open") Row) (repr outcome))
    ;; 回収の候補の読み(錠の外の 1 本 — 錠の中の読み直しが控えに見えれば、それも)が返した行は、どれも期限を過ぎた行だけ。
    (val candidate-reads (tuple (gfor #(statement rows) (get outcome "sweep-statements")
                                      :if (.startswith (.strip statement) (+ CANDIDATE-HEAD store.prefix "state_rows"))
                                      rows)))
    (assert candidate-reads (repr outcome))
    (assert (all (gfor rows candidate-reads (= rows EXPIRED-ROWS))) (repr candidate-reads))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
