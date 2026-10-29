;; PostgreSQL の置き場の行の書きの錠を、表の単位と番号の錠に分けた形の検(card ki-f2888153ff62 — pg_sql.hy の頭の註の「書きの錠」)。
;; 前は行の書き全部が行の単位 1 つを transaction の初めから持ち、1 つの表への束の書きが索引の更新の IO 待ちで錠を数十秒持つ間、関わりの
;; 無い表の 1 行の書きが全部その後ろで待った。今は行の表の文を表の単位の錠だけで流し、番号の錠は変更の列に積んでから commit するまで持つ。
;; 本物の PostgreSQL で、別の接続の止め(行の錠・番号の錠)を置いて次を確かめる:
;; - 1 つの表への束の書きが行の表の文で止まった間も、別の表の 1 行の書きは答え、束は止めが外れた後に後ろの番号で積む。
;; - 番号を取ったまま commit していない書き(番号の錠を持つ — 入れ替えの間に重なる旧い版の行の書きと同じ形)が居る間、別の表の書きは番号を
;;   確定せず待ち、変更の列を位置を進めながら読む読み手は番号を取りこぼさない(番号の読みの約束)。
;; - 同じ冪等キーを 2 つの表へ並んで撃った書きは 1 つだけが書け、もう 1 つは別の本文の断り(冪等キーの錠 — 表の単位では並ばない)。
;; env DOEFF_RECORDS_TEST_PG_DSN が無ければ conftest が使い捨ての PostgreSQL を立てて置く(#2830)。立てられなければ理由を名指して skip。
(require doeff-hy.macros [deftest val var])
(import queue)
(import threading)
(import time)
(import psycopg)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.inline_compute [inline-compute-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.admission [key-text])
(import doeff_records [ExpectAbsent ExpectAny Written WrittenRows Refused])
(import doeff_records [PutRow PutRows RowWrite])
(import doeff_records.maintenance [ReadChangeLog])
(import doeff_records._laws [MAKER])
(import doeff_records.pg [pg-records-handler drop-records-tables])
(import doeff_records.pg_sql [ROWS-UNIT table-unit unit-lock-key writer-lock-key])
(import tests.interpreters [session-dsn PG-DSN-VARIABLE pg-skip-reason ORIGIN-HOST postgres-connections fresh-prefix run-sql prepared-store])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; 本物の PostgreSQL の錠を別の thread の接続が待ち始めるのを締め切りつきで待つ(外の process の実時間が対象 — tests/realtime_tests.toml)。
(val pytestmark pytest.mark.realtime)
(val PG-SKIP-REASON (pg-skip-reason))
;; 待ち合わせの期限の秒と、止めの後ろで待つはずの書きが答えていない事を見る窓の秒。
(val DEADLINE-SECONDS 10.0)
(val HELD-WINDOW-SECONDS 1.0)


(defn run-on [connections store program]
  "置き場 store の書き手 maker として program を 1 回走らせる(接続の束 connections の答え手の上 — 検の thread ごとに 1 回)。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler (postgres-sql-handler connections)
                                  (pg-records-handler store MAKER ORIGIN-HOST)]
                                 program))))


(defn in-thread [connections store program]
  "program を別の thread で走らせ、答えが届く列(queue — 答えが出れば 1 つ入る)を返す(止めの後ろで待つ書きを撃つため)。"
  (setv answers (queue.Queue))
  (.start (threading.Thread :target (fn [] (.put answers (run-on connections store program))) :daemon True))
  answers)


(defn answer-of [answers]
  "別の thread の書きの答えを待って返す(期限 DEADLINE-SECONDS)。"
  (.get answers :timeout DEADLINE-SECONDS))


(defn answers-nothing [answers]
  "HELD-WINDOW-SECONDS の間、別の thread の書きが答えない事を確かめる(止めの後ろで待つはずの書き)。"
  (try
    (setv early (.get answers :timeout HELD-WINDOW-SECONDS))
    (assert False (.format "止めの後ろで待つはずの書きが答えた: {!r}" early))
    (except [queue.Empty]
      None)))


(defn until [predicate]
  "predicate が真になるまで待つ(期限 DEADLINE-SECONDS を越えれば検の失敗)。
   ポーリングの訳: 待つ物は別の接続が PostgreSQL の錠を待ち始めた事で、PostgreSQL は錠の待ちの始まりを知らせない(pg_locks を読むだけ)。"
  (setv deadline (+ (time.monotonic) DEADLINE-SECONDS))
  (while (not (predicate))
    (assert (< (time.monotonic) deadline) "待ち合わせの期限を越えた")
    (time.sleep 0.01)))


(defn advisory-count [observer #^ str key #^ bool granted]
  "鍵 key の advisory lock を持つ(granted 真)か待つ(偽)接続の数を読む(本番の書きと同じ hashtext の鍵)。"
  (get (.fetchone (.execute observer (+ "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted = %s "
                                        "AND objid = (hashtext(%s)::bigint & 4294967295)::oid")
                            #(granted key)))
       0))


(defn row-lock-waiters [observer]
  "行の錠(transaction の錠・tuple の錠)を待つ接続の数を読む。"
  (get (.fetchone (.execute observer "SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype IN ('transactionid', 'tuple')")) 0))


(defn change-sequences [connections store]
  "変更の列の全部の番号を番号の順に読む(ReadChangeLog — 表で絞らない読み)。"
  (setv log (run-on connections store (ReadChangeLog :after 0 :limit 1000)))
  (tuple (gfor change log.changes change.sequence)))


(deftest test-a-stalled-batch-on-one-table-does-not-hold-a-row-write-on-another
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 束の書き(表 parts の 2 行)が行の表の文で止まった間(表の単位の錠を持ったまま — 本番では索引の更新の IO 待ち。ここでは別の接続が
  ;; 行 p1 の行の錠を持つ)に、表 tickets の 1 行の書きが答える。前の形(行の書きが全部 1 つの錠の単位を transaction の初めから持つ)では
  ;; tickets の書きが束の後ろで待ち、「束より先に答えた」が赤になる。束は止めが外れた後に tickets より後ろの番号で積む。
  (val connections (postgres-connections 3))
  (val store (prepared-store connections (fresh-prefix)))
  (val observer (psycopg.connect PG-DSN :autocommit True))
  (val blocker (psycopg.connect PG-DSN))
  (val parts-key (run (unit-lock-key store.prefix (run (table-unit "parts")))))
  (try
    (val born (run-on connections store (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent))))
    (assert (isinstance born Written) (repr born))
    (.execute blocker (.format "SELECT version FROM {}state_rows WHERE ledger = 'parts' AND key = %s FOR UPDATE" store.prefix)
              #((key-text #("p1"))))
    (val batch
         (in-thread connections store (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "b"}) (ExpectAny))
                                                 (RowWrite "parts" #("p2") (FrozenMap {"label" "c"}) (ExpectAbsent))))))
    (until (fn [] (and (= (advisory-count observer parts-key True) 1) (>= (row-lock-waiters observer) 1))))
    (val other (run-on connections store (PutRow "tickets" #("g" "t1") (FrozenMap {"state" "open"}) (ExpectAbsent))))
    (assert (isinstance other Written) (repr other))
    (answers-nothing batch)
    (.commit blocker)
    (val rows (answer-of batch))
    (assert (isinstance rows WrittenRows) (repr rows))
    (val later (tuple (gfor item rows.items item.position.sequence)))
    (assert (= later (tuple (sorted later))) (repr later))
    (assert (> (get later 0) other.position.sequence) (repr #(later other)))
    (assert (= (cut (change-sequences connections store) -3 None) (+ #(other.position.sequence) later))
            (repr (change-sequences connections store)))
    (finally
      (.close blocker)
      (.close observer)
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-a-row-write-waits-for-an-earlier-number-in-flight-and-a-reader-skips-none
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 止め = 番号を取ったまま commit していない行の書き(別の接続で置き場全体を共有・番号の錠を排他で持ち、変更の列に parts の変更を 1 つ
  ;; 積んだ transaction — 入れ替えの間に重なる旧い版の行の書きと同じ錠)。その間、表 tickets の書きは番号の錠を DB で待って答えず、変更の
  ;; 列を位置を進めながら読む読み手は止めの番号より後ろを読まない。止めが commit した後、読み手が読んだ番号は変更の列の全部と同じ
  ;; (取りこぼし無し)。番号の錠を取らずに表の単位だけで積む形では、tickets の書きが止めの番号の後ろの番号で先に commit し、読み手は
  ;; 位置を止めの番号の先へ進めて止めの変更を取りこぼす(seen と whole が違って赤になる)。
  (val connections (postgres-connections 3))
  (val store (prepared-store connections (fresh-prefix)))
  (val observer (psycopg.connect PG-DSN :autocommit True))
  (val flight (psycopg.connect PG-DSN))
  (val rows-key (run (unit-lock-key store.prefix ROWS-UNIT)))
  (try
    (val born (run-on connections store (PutRow "parts" #("p0") (FrozenMap {"label" "a"}) (ExpectAbsent))))
    (assert (isinstance born Written) (repr born))
    (.execute flight "SELECT pg_advisory_xact_lock_shared(hashtext(%s))" #((run (writer-lock-key store.prefix))))
    (.execute flight "SELECT pg_advisory_xact_lock(hashtext(%s))" #(rows-key))
    (val epoch (get (.fetchone (.execute flight (.format "SELECT epoch FROM {}store_epoch" store.prefix))) 0))
    (val taken (get (.fetchone (.execute flight (.format (+ "INSERT INTO {}row_changes (ledger, key, version, payload, at, epoch) "
                                                           "VALUES ('parts', %s, 1, '{{\"label\":\"x\"}}', 0, %s) RETURNING seq")
                                                        store.prefix)
                                         #((key-text #("p-flight")) epoch)))
                    0))
    (val writer (in-thread connections store (PutRow "tickets" #("g" "t1") (FrozenMap {"state" "open"}) (ExpectAbsent))))
    (until (fn [] (>= (advisory-count observer rows-key False) 1)))
    (answers-nothing writer)
    (var seen (change-sequences connections store))
    (assert (= seen #(born.position.sequence)) (repr #(seen taken)))
    (.commit flight)
    (val written (answer-of writer))
    (assert (isinstance written Written) (repr written))
    (assert (> written.position.sequence taken) (repr #(written taken)))
    (val after (get seen -1))
    (val log (run-on connections store (ReadChangeLog :after after :limit 1000)))
    (:= seen (+ seen (tuple (gfor change log.changes change.sequence))))
    (val whole (change-sequences connections store))
    (assert (= whole #(born.position.sequence taken written.position.sequence)) (repr whole))
    (assert (= seen whole) (.format "読み手が位置を進めながら読んだ番号 {} が変更の列の全部 {} と違う(取りこぼし)" seen whole))
    (finally
      (.close flight)
      (.close observer)
      (run-sql connections (drop-records-tables store))
      (.close connections))))


(deftest test-one-write-key-sent-to-two-tables-at-once-writes-once
  {:skip-if (not PG-DSN) :skip-reason PG-SKIP-REASON}
  ;; 同じ冪等キーで別の表(parts と tickets — 表の単位では並ばない)へ同時に撃った 2 つの書きは、冪等キーの錠で 1 本ずつになり、1 つだけが
  ;; 書けて後の方は別の本文の断り(Refused — 覚えを読んで判じる)。鍵の錠が無いと 2 つとも覚えの無い断面を読んで書け、後の覚えは黙って
  ;; 捨てられる(「1 つだけ書けた」が赤になる)。
  (val connections (postgres-connections 3))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (for [round (range 10)]
      (val key (.format "race-key-{}" round))
      (val start (threading.Barrier 2 :timeout DEADLINE-SECONDS))
      (val answers (queue.Queue))
      (defn attempt [program]
        (.wait start)
        (.put answers (run-on connections store program)))
      (for [program #((PutRow "parts" #((.format "p{}" round)) (FrozenMap {"label" "a"}) (ExpectAbsent) :idempotency-key key)
                      (PutRow "tickets" #("g" (.format "t{}" round)) (FrozenMap {"state" "open"}) (ExpectAbsent) :idempotency-key key))]
        (.start (threading.Thread :target attempt :args #(program) :daemon True)))
      (val found #((answer-of answers) (answer-of answers)))
      (assert (= (sorted (gfor answer found (. (type answer) __name__))) ["Refused" "Written"]) (repr found)))
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
