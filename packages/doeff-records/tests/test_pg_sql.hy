;; doeff の汎用の SQL の effect に載せ替えた PostgreSQL の置き場が、旧い版(自前の psycopg の host — #880 の前)と
;; 同じ表の形と同じ錠を使うこと。旧い版と新しい版が同じ置き場に重なっても(入れ替えの途中)壊れないための約束:
;;   - 表の用意の DDL は旧い版と同じ字面(tests/pg_ddl_before_880.json = 旧い版の schema-statements の "records_" の答えの写し)。
;;     答え手の方言への書き換え(postgres-statement)を通した後の、driver に渡る字面で比べる。旧い版より後に足した表と索引の文
;;     (ADDED-AFTER-880 — 鍵だけの表 retired_keys・#3022 とその覚えた刻の列・card ki-4ebaefac6668 と、行の書きの冪等キーの表 write_keys とその
;;     覚えの外しの索引・card ki-78c6d663491f と、期限の境と組と変更の列の刻と
;;     状態の欄の索引・#3614 と覚えの外しの索引・等号の欄 × 上から読む欄の組の索引)は旧い版の文の後ろにだけ並び、
;;     旧い版の文の字面と順を変えない。旧い版と字面が違うのは store_epoch の行を作る文の版の初めの値だけ(#3632 — 行を作った時の
;;     server の時計の ms)。ON CONFLICT DO NOTHING なので既に在る置き場の行(旧い版が作った epoch 1 を含む)には触らず、表の形も
;;     変わらない(既に在る置き場の版が 1 のままなことは test_pg_store_epoch.hy が実 PostgreSQL で確かめる)。
;;     宣言した索引の欄の索引だけは旧い版と形が違う(全部の行に張る (ledger, 欄の式) を、card ki-954c58540d0f で表ごとの部分索引
;;     (欄の式) WHERE ledger = '<表>' にし、card ki-2176012d1804 で全部の行の表の欄の式の索引 (表の欄の式) にした・
;;     TABLE-INDEXES)。旧い形の索引(旧い名)と前の部分索引が在る置き場では、表の欄の式の索引を作り終えるまでそれが前の文の形の
;;     読みに答え、作り終えて見積もれるようになった後に索引の用意が外す(pg_sql.hy の頭の註 — test_pg_reconcile_indexes.hy が実
;;     PostgreSQL で確かめる)。外す名の形は旧い 2 つの形だけに当たり、宣言の索引の名には当たらない(retired-index?)。
;;   - 書きの錠と移行の錠の鍵は旧い版の錠の文の引数と同じ字面(接頭辞 + "records-writer" / "records-migrate" — 区切りなし)で、
;;     PostgreSQL の hashtext が同じ整数を返す(= 同じ advisory lock の番号)。
;;   - 旧い版の錠の文(`SELECT pg_advisory_xact_lock(hashtext(%s))`)を別の接続で取っている間、新しい版の書きは待つ
;;     (同じ錠を取り合う — 変更の列の番号に穴が出ない土台)。
;;   - 配列の引数は `IN (:t0, …)` に展げ、空の組は文にしない。
;; 実 PostgreSQL の検は env DOEFF_RECORDS_TEST_PG_DSN の物(無ければ conftest が使い捨ての PostgreSQL を立てて置く — #2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val var <-])
(import json)
(import random)
(import pathlib [Path])
(import threading)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlRows])
(import doeff_core_effects.postgres_sql [postgres-sql-handler postgres-statement])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.values [ExpectAbsent Written])
(import doeff_records.effects [PutRow])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import doeff_records.pg_sql [schema-statements writer-lock-key migrate-lock-key in-list changes-statement terminal-rows-statement RowExpiry
                              declared-indexes index-name table-index-name retired-index? table-shaped-index? table-field-index-name
                              ordered-index-name ordered-within-index-name former-field-index-name])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason DATABASE ORIGIN-HOST open-postgres postgres-connections fresh-prefix run-sql prepared-store])
(import doeff_records.main [store-pressure-pg])
(import doeff_records.store_choice [StorePressure])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; env が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)— 無いのは立てられなかった時で、その理由を名指す。
(val PG-SKIP-REASON (pg-skip-reason))
(val BEFORE-880-DDL (json.loads (.read-text (/ (. (Path __file__) parent) "pg_ddl_before_880.json") :encoding "utf-8")))
;; 旧い版の文のうち、store_epoch の行を作る文だけは版の初めの値を替えた(頭の註・#3632)。旧い版の写しの json はそのまま残し、
;; 替えた 1 文だけをここで名指す(他の文の字面と順は旧い版のまま)。
(val BEFORE-880-EPOCH-ROW "INSERT INTO records_store_epoch (id, epoch, floor) VALUES (1, 1, 0) ON CONFLICT (id) DO NOTHING")
(val UNIQUE-EPOCH-ROW (+ "INSERT INTO records_store_epoch (id, epoch, floor)\n"
                         "           VALUES (1, (extract(epoch from clock_timestamp()) * 1000)::bigint, 0) ON CONFLICT (id) DO NOTHING"))
(val EPOCH-ROW-AT (.index BEFORE-880-DDL BEFORE-880-EPOCH-ROW))
;; 旧い版の宣言した索引の欄の索引(全部の行に張る — "records_ix_" で始まる 3 文)は、表の欄の式の索引に替えた(頭の註)。
(val BEFORE-880-FIELD-INDEXES (lfor text BEFORE-880-DDL :if (.startswith text "CREATE INDEX IF NOT EXISTS records_ix_") text))
(val TABLE-INDEXES
  ["CREATE INDEX IF NOT EXISTS records_ft_6dd0fe800114_909e6bf0ca53 ON records_state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'color') END), updated_at)"
   "CREATE INDEX IF NOT EXISTS records_ft_64c65374dbab_909e6bf0ca53 ON records_state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'label') END), updated_at)"
   "CREATE INDEX IF NOT EXISTS records_ft_579233b2c479_d370d9cd5625 ON records_state_rows ((CASE WHEN ledger = 'tickets' THEN ((payload::jsonb) -> 'owner') END), updated_at)"])
(val EXPECTED-DDL (+ (lfor text (cut BEFORE-880-DDL EPOCH-ROW-AT) :if (not-in text BEFORE-880-FIELD-INDEXES) text)
                     [UNIQUE-EPOCH-ROW]
                     (lfor text (cut BEFORE-880-DDL (+ EPOCH-ROW-AT 1) None) :if (not-in text BEFORE-880-FIELD-INDEXES) text)
                     TABLE-INDEXES))
;; 旧い版より後に足した表と索引の文("records_" の答え・旧い版の文の後ろに足した順)。索引は #3614: 列の期限の境 (ledger, at)・
;; 組で数える列 pairs の区切り「:」の組の名の式・頭で引ける列 trails の区切り「#」の頭の式・変更の列の刻・保持の期限の在る表 tickets の状態の欄 state(表 tickets の索引の欄に無い —
;; 表の欄の式の索引)・表 parts の上から読む欄 note の並べる式の索引(pg_sql.hy の頭の註の上から読む欄の索引)・表 parts の等号の欄 color ×
;; 上から読む欄 note の組の索引(pg_sql.hy の頭の註の等号の欄 × 上から読む欄の組の索引)・鍵の綴りの順の索引
;; (照合順 "C" の (ledger, key) — 頁読みの順。pg_sql.hy の頭の註)。
(val ADDED-AFTER-880
  ["CREATE TABLE IF NOT EXISTS records_retired_keys (
           ledger text NOT NULL, idempotency_key text NOT NULL, seq bigint NOT NULL, body_digest text NOT NULL,
           PRIMARY KEY (ledger, idempotency_key))"
   "ALTER TABLE records_retired_keys
           ADD COLUMN remembered_at bigint NOT NULL DEFAULT (extract(epoch from now()) * 1000)::bigint"
   "CREATE TABLE IF NOT EXISTS records_write_keys (
           idempotency_key text PRIMARY KEY, body_digest text NOT NULL, answer text NOT NULL, last_seq bigint NOT NULL,
           remembered_at bigint NOT NULL)"
   "CREATE INDEX IF NOT EXISTS records_append_rows_ledger_at ON records_append_rows (ledger, at)"
   "CREATE INDEX IF NOT EXISTS records_append_rows_group_05a79f06cf3f ON records_append_rows (ledger, (CASE WHEN strpos(((payload::jsonb) ->> 'idempotencyKey'), ':') > 0
                 THEN substr(((payload::jsonb) ->> 'idempotencyKey'), strpos(((payload::jsonb) ->> 'idempotencyKey'), ':') + 1)
                 ELSE ((payload::jsonb) ->> 'idempotencyKey') END))"
   "CREATE INDEX IF NOT EXISTS records_append_rows_head_d08f88df745f ON records_append_rows (ledger, split_part(((payload::jsonb) ->> 'idempotencyKey'), '#', 1), seq)"
   "CREATE INDEX IF NOT EXISTS records_row_changes_at ON records_row_changes (at)"
   "CREATE INDEX IF NOT EXISTS records_ft_aa4a5f8125f2_d370d9cd5625 ON records_state_rows ((CASE WHEN ledger = 'tickets' THEN ((payload::jsonb) -> 'state') END), updated_at)"
   "CREATE INDEX IF NOT EXISTS records_op_c51048b7325d_909e6bf0ca53 ON records_state_rows ((CASE WHEN ledger = 'parts' THEN (CASE WHEN jsonb_typeof(((payload::jsonb) -> 'note')) = 'number' AND (((payload::jsonb) -> 'note'))::text ~ '^-?[0-9]+$' THEN (((payload::jsonb) -> 'note'))::text::numeric END) END) DESC NULLS LAST, key COLLATE \"C\" DESC) WHERE (CASE WHEN ledger = 'parts' THEN (CASE WHEN jsonb_typeof(((payload::jsonb) -> 'note')) = 'number' AND (((payload::jsonb) -> 'note'))::text ~ '^-?[0-9]+$' THEN (((payload::jsonb) -> 'note'))::text::numeric END) END) IS NOT NULL"
   "CREATE INDEX IF NOT EXISTS records_wp_0b920c6b8d1d_909e6bf0ca53 ON records_state_rows ((CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'color') END), (CASE WHEN ledger = 'parts' THEN (CASE WHEN jsonb_typeof(((payload::jsonb) -> 'note')) = 'number' AND (((payload::jsonb) -> 'note'))::text ~ '^-?[0-9]+$' THEN (((payload::jsonb) -> 'note'))::text::numeric END) END) DESC NULLS LAST, key COLLATE \"C\" DESC) WHERE (CASE WHEN ledger = 'parts' THEN (CASE WHEN jsonb_typeof(((payload::jsonb) -> 'note')) = 'number' AND (((payload::jsonb) -> 'note'))::text ~ '^-?[0-9]+$' THEN (((payload::jsonb) -> 'note'))::text::numeric END) END) IS NOT NULL"
   "CREATE INDEX IF NOT EXISTS records_state_rows_key_c ON records_state_rows (ledger, key COLLATE \"C\")"
   "CREATE INDEX IF NOT EXISTS records_retired_keys_remembered ON records_retired_keys (ledger, remembered_at)"
   "CREATE INDEX IF NOT EXISTS records_write_keys_remembered ON records_write_keys (remembered_at)"])
;; 旧い版の錠の文と鍵の字面(pg_sql.hy の lock-statement・migrate-lock-statement の写し — #880 の前)。
(val BEFORE-880-LOCK-TEXT "SELECT pg_advisory_xact_lock(hashtext(%s))")
(val BEFORE-880-WRITER-KEY (fn [prefix] (+ prefix "records-writer")))
(val BEFORE-880-MIGRATE-KEY (fn [prefix] (+ prefix "records-migrate")))


(deftest test-the-ddl-reaches-the-driver-with-the-text-of-the-version-before-880
  (<- statements (schema-statements "records_" LAW-SCHEMA))
  (var texts [])
  (for [statement statements]
    (<- bound (postgres-statement statement.text statement.params))
    (.append texts bound.text))
  (assert (= (len BEFORE-880-FIELD-INDEXES) 3) (repr BEFORE-880-FIELD-INDEXES))
  (assert (= (cut texts (len EXPECTED-DDL)) EXPECTED-DDL) (repr texts))
  (assert (= (cut texts (len EXPECTED-DDL) None) ADDED-AFTER-880) (repr texts)))


(deftest test-only-the-two-former-index-forms-are-retired-and-no-declared-index-is
  ;; 旧い形の索引を外す(pg_sql.hy の頭の註): 外す名は旧い版の 2 つの形(index-name の全部の行の (ledger, 欄の式)・table-index-name の
  ;; 前の部分索引)だけ。宣言の索引(表の欄の式・上から読む欄・置き場の形の索引 — LAW-SCHEMA はどれも持つ)の名に当たると、索引の
  ;; 用意が宣言の索引を外して次の回が作り直す輪になるので、1 つも当たらない。
  (<- legacy (index-name "records_" "role"))
  (<- partial (table-index-name "records_" "parts" "role"))
  ;; 2026-10-19 の本番の置き場に在った 2 つの形の名の写しも当たる。
  (for [name [legacy partial "records_ix_8dca46428d00" "records_ix_12cb5b79d6a2_a8c39f3520e2"]]
    (<- hit (retired-index? "records_" name))
    (assert hit name))
  ;; 別の接頭辞の置き場の名・主キー・形の違う手の索引の名は当たらない。
  (for [name ["records_state_rows_pkey" "records_ix_8dca46428d0" "records_ix_8dca46428d00_note" "records_hand_ix_8dca46428d00"]]
    (<- missed (retired-index? "records_" name))
    (assert (not missed) name))
  (<- elsewhere (retired-index? "app_" legacy))
  (assert (not elsewhere) legacy)
  (<- declared (declared-indexes "records_" LAW-SCHEMA))
  (assert (>= (len declared) 10) (repr declared))
  (for [index declared]
    (<- declared-hit (retired-index? "records_" index.name))
    (assert (not declared-hit) index.name)))


(deftest test-the-table-shaped-index-names-are-the-three-forms-the-declaration-makes
  ;; 宣言から外した表ごとの索引を外す(pg_sql.hy の頭の註): 当たる名は宣言が作る 3 つの形(表の欄の式 ft_・上から読む欄 op_・等号の欄 ×
  ;; 上から読む欄の組 wp_)と、表の欄の式の索引の前の形(fx_)と上から読む索引の前の形(ox_・ow_)だけ。索引の用意はこの形で今の宣言に無い物を外すので、宣言の置き場の形の索引(append_rows_・row_changes_・
  ;; state_rows_key_c など)と手の索引と旧い形の名に当たると、宣言の外の索引を外してしまう。
  (<- field (table-field-index-name "records_" "parts" "role"))
  (<- former (former-field-index-name "records_" "parts" "role"))
  (<- ordered (ordered-index-name "records_" "parts" "role"))
  (<- within (ordered-within-index-name "records_" "parts" "color" "role"))
  ;; 2026-10-23 の本番の置き場の使われない表ごとの索引の名の写しと、表の欄の式の索引の前の形(式 1 つの索引 fx_)・上から読む索引の
  ;; 前の形(全部の行の索引 ox_・ow_)の名の写しも当たる(今の宣言は作らないので、索引の用意が外す)。
  (assert (.startswith field "records_ft_") field)
  (assert (.startswith former "records_fx_") former)
  (for [name [field former ordered within "records_fx_3150ecd5e029_8a35885a0e07" "records_fx_8dca46428d00_2c17ecd3ddd1"
              "records_ox_27e90dfa57c3_6f9b9af3cd6e" "records_ow_bd51f9a00add_6f9b9af3cd6e"]]
    (<- hit (table-shaped-index? "records_" name))
    (assert hit name))
  (<- legacy (index-name "records_" "role"))
  (<- partial (table-index-name "records_" "parts" "role"))
  (for [name [legacy partial "records_state_rows_pkey" "records_fx_8dca46428d00" "records_fx_8dca46428d00_2c17ecd3ddd" "records_hand_fx_8dca46428d00_2c17ecd3ddd1"
              "records_px_8dca46428d00_2c17ecd3ddd1"]]
    (<- missed (table-shaped-index? "records_" name))
    (assert (not missed) name))
  (<- elsewhere (table-shaped-index? "app_" field))
  (assert (not elsewhere) field)
  ;; 宣言の置き場の形の索引(fixed-index)の名には当たらず、宣言の表ごとの索引の名には全部当たる。
  (<- declared (declared-indexes "records_" LAW-SCHEMA))
  (val fixed (lfor index declared :if (not index.sampled) index.name))
  (val per-table (lfor index declared :if index.sampled index.name))
  (assert (>= (len fixed) 5) (repr fixed))
  (assert (>= (len per-table) 5) (repr per-table))
  (for [name fixed]
    (<- fixed-hit (table-shaped-index? "records_" name))
    (assert (not fixed-hit) name))
  (for [name per-table]
    (<- per-table-hit (table-shaped-index? "records_" name))
    (assert per-table-hit name)))


(deftest test-the-lock-keys-have-the-text-of-the-version-before-880
  (for [prefix ["records_" "t0123456789ab_" "app_"]]
    (<- writer (writer-lock-key prefix))
    (<- migrate (migrate-lock-key prefix))
    (assert (= writer (BEFORE-880-WRITER-KEY prefix)))
    (assert (= migrate (BEFORE-880-MIGRATE-KEY prefix)))))


(deftest test-arrays-expand-into-in-lists-and-an-empty-list-is-refused
  (<- expanded (in-list "t" #("a" "b" "c")))
  (assert (= expanded.text ":t0, :t1, :t2"))
  (assert (= (lfor p expanded.params #(p.name p.value)) [#("t0" "a") #("t1" "b") #("t2" "c")]))
  (<- changes (changes-statement "records_" 0 9 #("parts" "tickets") 50 #()))
  (<- bound (postgres-statement changes.text changes.params))
  (assert (in "ledger IN (%(t0)s, %(t1)s)" bound.text) bound.text)
  ;; 保持の期限の在る表の境(#3561)は、終端の語を表ごとの頭(x0 …)の付いた IN に展げる(頼んだ表の IN と名が混ざらない)。
  (<- hiding (changes-statement "records_" 0 9 #("parts" "tickets") 50
                                #((RowExpiry :table "tickets" :state-field "state" :terminal #("done") :before-at 5))))
  (<- hidden (postgres-statement hiding.text hiding.params))
  (assert (in "ledger IN (%(t0)s, %(t1)s)" hidden.text) hidden.text)
  (assert (in "IN (%(x0t0)s::jsonb)" hidden.text) hidden.text)
  (<- terminal (terminal-rows-statement "records_" "parts" "state" #("closed") False 5))
  (assert (in "IN (:t0::jsonb)" terminal.text) terminal.text)
  ;; 回収の候補の読みは、最後に書いた刻が境の刻以下の終端の行だけを返す(期限の前の終端の行を値ごと返さない — 読みの文と同じ境)。
  (assert (in "AND updated_at <= :before_at" terminal.text) terminal.text)
  (assert (in (SqlParam :name "before_at" :value 5) terminal.params) terminal.params)
  ;; 表の状態の欄の表の欄の式の索引が在れば、表の欄の式の IN だけで照らす(ledger = :ledger を置かない — pg_sql の頭の註)。
  (<- scoped (terminal-rows-statement "records_" "parts" "state" #("closed") True 5))
  (assert (in "WHERE (CASE WHEN ledger = 'parts' THEN ((payload::jsonb) -> 'state') END) IN (:t0::jsonb) AND updated_at <= :before_at"
              scoped.text)
          scoped.text)
  (assert (in (SqlParam :name "before_at" :value 5) scoped.params) scoped.params)
  (assert (not (in ":ledger" scoped.text)) scoped.text)
  ;; 空の組は `IN ()`(構文の誤り)にしない — 組み立てが断り、呼び手の枝(pg.hy)が文を流さない。
  (var refused False)
  (try
    (<- (in-list "t" #()))
    (except [AssertionError] (:= refused True)))
  (assert refused))


(deftest test-postgres-hashes-the-old-and-new-lock-keys-to-the-same-number
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  (val connections (postgres-connections 1))
  (val connection (open-postgres))
  (try
    (for [prefix ["records_" "t0123456789ab_"]]
      (for [#(new-key old-key) [#((run (writer-lock-key prefix)) (BEFORE-880-WRITER-KEY prefix))
                                #((run (migrate-lock-key prefix)) (BEFORE-880-MIGRATE-KEY prefix))]]
        ;; 新しい版の道: 中立の記法の文を答え手が流す。
        (val answer (run-sql connections (SqlQuery DATABASE "SELECT hashtext(:k)" #((SqlParam :name "k" :value new-key)))))
        (assert (isinstance answer SqlRows) (repr answer))
        ;; 旧い版の道: psycopg に `%s` の文を直に流す。
        (val old-number (get (.fetchone (.execute connection "SELECT hashtext(%s)" #(old-key))) 0))
        (assert (= (get answer.rows 0 0) old-number) (repr #(new-key (get answer.rows 0 0) old-key old-number)))))
    (finally
      (.close connection)
      (.close connections))))


(deftest test-the-readyz-pressure-counts-a-connection-waiting-on-a-held-lock
  {:marks ["realtime"]
   :skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; #1858: 1 つの接続が advisory lock を持ったまま transaction を開いて止まり(idle in transaction)、別の接続が同じ錠を待つ間、
  ;; /readyz の詰まりの読み(store-pressure-pg)は錠を待つ本数 1 以上と idle in transaction の秒を答える。錠を放した後は待ちが 0。
  (val connections (postgres-connections 2))
  (val key (random.randint 1 2000000000))
  (val holder (open-postgres))
  (val waiter (open-postgres))
  (defn wait-for-lock []  ; defk にできない: thread の target
    (.execute waiter "BEGIN")
    (.execute waiter "SELECT pg_advisory_xact_lock(%s)" #(key))
    (.execute waiter "COMMIT"))
  (try
    (val quiet-before (run-sql connections (store-pressure-pg)))
    (assert (= quiet-before.lock-waiters 0) quiet-before)
    (.execute holder "BEGIN")
    (.execute holder "SELECT pg_advisory_xact_lock(%s)" #(key))
    (val blocked (threading.Thread :target wait-for-lock))
    (.start blocked)
    (var busy None)
    (val deadline (+ (time.monotonic) 10))
    (while (and (or (is busy None) (< busy.lock-waiters 1)) (< (time.monotonic) deadline))
      (time.sleep 0.05)
      (:= busy (run-sql connections (store-pressure-pg))))
    (assert (isinstance busy StorePressure) busy)
    (assert (>= busy.lock-waiters 1) busy)
    (assert (> busy.idle-in-transaction-max-seconds 0.0) busy)
    (.execute holder "COMMIT")
    (.join blocked 10)
    (val quiet-after (run-sql connections (store-pressure-pg)))
    (assert (= quiet-after.lock-waiters 0) quiet-after)
    (finally
      (.close holder)
      (.close waiter)
      (.close connections))))


(deftest test-a-write-waits-while-the-version-before-880-holds-the-writer-lock
  {:marks ["realtime"]
   :skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 旧い版の process の代役: 別の接続で旧い版の錠の文を流して transaction を開いたままにする。新しい版の PutRow は同じ錠を待ち、
  ;; 旧い版が commit した後で書く。
  (val connections (postgres-connections 2))
  (val store (prepared-store connections (fresh-prefix)))
  (val old (open-postgres))
  (val answers [])
  (defn write []  ; defk にできない: thread の target
    (.append answers (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)
                                                     (pg-records-handler store MAKER ORIGIN-HOST)]
                                                    (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent)))))))
  (try
    (.execute old "BEGIN")
    (.execute old BEFORE-880-LOCK-TEXT #((BEFORE-880-WRITER-KEY store.prefix)))
    (val writer (threading.Thread :target write))
    (.start writer)
    ;; 新しい版の書きが advisory lock を待つまで(pg_locks に未許可の advisory の行が現れるまで)見る。
    (var waiting 0)
    (val deadline (+ (time.monotonic) 10))
    (while (and (= waiting 0) (< (time.monotonic) deadline))
      (:= waiting (get (.fetchone (.execute old "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")) 0))
      (time.sleep 0.05))
    (assert (= waiting 1) waiting)
    (assert (= answers []) (repr answers))
    (.execute old "COMMIT")
    (.join writer 10)
    (assert (= (lfor a answers (. (type a) __name__)) ["Written"]) (repr answers))
    (finally
      (.close old)
      (run-sql connections (drop-records-tables store))
      (.close connections))))
