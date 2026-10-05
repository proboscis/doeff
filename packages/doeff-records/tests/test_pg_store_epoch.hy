;; PostgreSQL の置き場の版(store_epoch の epoch)の初めの値(#3632): 置き場を空から作るたびに、版は行を作った時の server の時計の ms で
;; 始まる(pg_sql.hy の頭の註の store_epoch)。性質:
;;   - 同じ接頭辞の置き場を消して空から作り直すと、版が前の置き場の版と違う。
;;   - 前の置き場で読んだ位置 (epoch, sequence) で、作り直した置き場を WatchChanges で読むと Reset になる — 作り直した置き場の変更の
;;     番号が古い位置を越えた後でも。版が両方 1 で始まると、番号が越えた後は Reset にならず、別の置き場の変更が位置の続きとして混ざる。
;;   - store_epoch の行が既に在る置き場(旧い版の文 tests/pg_ddl_before_880.json で作った epoch 1 の置き場)に表の用意の文を流し直しても、
;;     版は 1 のまま(ON CONFLICT DO NOTHING — 既に在る置き場の版に触らない)。
;; 反例: 版を 1 で始める文(#3632 の前の pg_sql.hy)では、1 つ目と 2 つ目の検が赤になる(版が同じ・Changes が返る)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import pathlib [Path])
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Reset WatchCursor])
(import doeff_records.effects [PutRow ListRows WatchChanges])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [PreparedStore pg-records-handler drop-records-tables prepare-records-store])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST open-postgres postgres-connections fresh-prefix])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))
;; 旧い版の表の用意の文("records_" の答えの写し — 版を 1 で始める)。
(val BEFORE-880-DDL (json.loads (.read-text (/ (. (Path __file__) parent) "pg_ddl_before_880.json") :encoding "utf-8")))


(defk prepared-on [connections prefix]
  {:pre [(: connections PostgresConnections) (: prefix str)] :post [(: % PreparedStore)]
   :tags {:context "records" :role "foundation"}}
  "接頭辞 prefix の置き場の表を用意し(prepare-records-store)、置き場を返すため。"
  (<- store (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)]
                           (prepare-records-store DATABASE LAW-SCHEMA prefix)))
  store)


(defk dropped-on [connections store]
  {:pre [(: connections PostgresConnections) (: store PreparedStore)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "置き場 store の表を消すため(空から作り直す前と、検の後片付け)。"
  (<- (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)] (drop-records-tables store)))
  None)


(defk on-store [connections store program]
  {:tp [T]
   :pre [(: connections PostgresConnections) (: store PreparedStore) (: program (| (get Program #(T object)) (get EffectBase T)))]
   :post [(: % T)]
   :tags {:context "records" :role "foundation"}}
  "program(Program か effect)を置き場 store の書き手 maker の handler の下で走らせ、その答え(型の引数 T)を返すため。"
  (<- answer (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                             (pg-records-handler store MAKER ORIGIN-HOST)]
                            program))
  answer)


(deftest test-a-store-built-again-from-empty-starts-at-another-epoch
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 1))
  (val prefix (fresh-prefix))
  (<- first (prepared-on connections prefix))
  (<- first-page (on-store connections first (ListRows "parts")))
  (<- (dropped-on connections first))
  (<- again (prepared-on connections prefix))
  (try
    (<- again-page (on-store connections again (ListRows "parts")))
    (assert (!= again-page.epoch first-page.epoch) (repr #(first-page again-page)))
    (finally
      (<- (dropped-on connections again))
      (.close connections))))


(deftest test-a-position-from-before-a-rebuild-is-reset-after-the-rebuilt-numbers-pass-it
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 1))
  (val prefix (fresh-prefix))
  ;; 前の置き場に変更を 2 つ積み、読み手が覚える位置 (epoch, 2) を読む。
  (<- before (prepared-on connections prefix))
  (for [key ["a1" "a2"]]
    (<- (on-store connections before (PutRow "parts" #(key) {"label" key} (ExpectAbsent)))))
  (<- seen (on-store connections before (ListRows "parts")))
  (val position (WatchCursor seen.epoch seen.sequence))
  ;; 置き場を空から作り直し、変更の番号を古い位置より先へ進める(3 つ積む — 番号 3 > 2)。
  (<- (dropped-on connections before))
  (<- rebuilt (prepared-on connections prefix))
  (try
    (for [key ["b1" "b2" "b3"]]
      (<- (on-store connections rebuilt (PutRow "parts" #(key) {"label" key} (ExpectAbsent)))))
    (<- head (on-store connections rebuilt (ListRows "parts")))
    (assert (> head.sequence position.sequence) (repr #(position head)))
    (<- answer (on-store connections rebuilt (WatchChanges #("parts") position)))
    (assert (= answer (Reset head.epoch 0)) (repr #(position head answer)))
    (finally
      (<- (dropped-on connections rebuilt))
      (.close connections))))


(deftest test-preparing-a-store-whose-epoch-row-is-1-keeps-epoch-1
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 旧い版の文で作った置き場の代役: 旧い版の DDL の接頭辞を検の接頭辞に替えて流す(版の行は epoch 1)。
  (val connections (postgres-connections 1))
  (val prefix (fresh-prefix))
  (val connection (open-postgres))
  (for [text BEFORE-880-DDL]
    (.execute connection (.replace text "records_" prefix)))
  (<- store (prepared-on connections prefix))
  (try
    (<- written (on-store connections store (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
    (<- (prepared-on connections prefix))
    (<- page (on-store connections store (ListRows "parts")))
    (assert (= #(page.epoch page.sequence) #(1 1)) (repr #(written page)))
    (val row (.fetchone (.execute connection (.format "SELECT epoch, floor FROM {}store_epoch WHERE id = 1" prefix))))
    (assert (= (tuple row) #(1 0)) (repr row))
    (finally
      (.close connection)
      (<- (dropped-on connections store))
      (.close connections))))
