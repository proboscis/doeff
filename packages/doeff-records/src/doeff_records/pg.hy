;;; PostgreSQL の handler — 公開 effect 6 つに PostgreSQL の表で答える(本番の置き場)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ(memory の handler と同じ関数)。
;;; 文は pg_sql.hy(純粋)で、流すのはこの file の PgRecordsHost だけ。接続は composition root が開いて渡す(psycopg 3・
;;; 自動 commit の接続 — 書きは host が transaction を開く)。psycopg はこの package の依存に無い(extra "pg")。
;;; 接続の失敗(composition root が渡す型 — psycopg なら OperationalError / InterfaceError)は Unreachable の答えに写す。
;;; それ以外の例外は実装の誤りとして上がる。
(require doeff-hy.macros [defhandler defk <-])
(import json)
(import socket)
(import collections.abc [Callable])
(import typing [NamedTuple Protocol TypeVar])
(import doeff [Pure])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetTime])
(import doeff_records.values [RecordsSchema KeepFor Row Missing Page Written RowChanged RowRemoved Changes Appended Event
                              Events Reset WatchCursor ListCursor Refused Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.maintenance [SweepExpired PruneChanges Swept Pruned])
(import doeff_records.admission [AppendReplay judge-expect judge-put judge-append row-expired? where-refusal listed-row
                                 key-text key-from-text canonical-json next-watch-sequence epoch-ms])
(import doeff_records.watching [wait-for-changes])
(import doeff_records.pg_sql [Statement DEFAULT-PREFIX checked-prefix schema-statements drop-statements lock-statement
                              store-head-statement read-row-statement lock-row-statement list-rows-statement
                              terminal-rows-statement upsert-row-statement delete-row-statement append-change-statement
                              changes-statement advance-epoch-statement forget-changes-statement prune-changes-statement find-event-statement
                              insert-event-statement read-events-statement expire-events-statement])

(setv DEFAULT-POLL-SECONDS 0.2)
(setv T (TypeVar "T"))


(defclass PgConnection [Protocol]
  "PgRecordsHost が使う接続の面(psycopg 3 の Connection がこれを満たす — psycopg はこの package の依存に無いので型で名指さない)。"
  (#^ bool autocommit)
  (#^ bool closed)
  (#^ bool broken)
  (defn #^ object execute [self #^ str query #^ tuple params] "文を流し、cursor を返す。" ...)
  (defn #^ object transaction [self] "with で使う transaction を開く。" ...)
  (defn #^ None close [self] "接続を閉じる。" ...))


(defclass PgRecordsHost []
  "PostgreSQL の置き場 1 つ: connection = 自動 commit の psycopg の接続 / schema = 宣言 /
   unreachable-errors = 接続の失敗の例外の型(接続を開いた composition root が渡す — psycopg なら
   #(psycopg.OperationalError psycopg.InterfaceError))/ prefix = 表の名の接頭辞 /
   origin-host = 行に刻む機体の名 / poll-seconds = WatchChanges の読み直しの間隔。
   作る時に表を用意する(何度でも同じ)。"
  (defn #^ None __init__ [self #^ PgConnection connection #^ RecordsSchema schema * #^ tuple unreachable-errors
                          #^ str [prefix DEFAULT-PREFIX]
                          #^ (| str None) [origin-host None]
                          #^ float [poll-seconds DEFAULT-POLL-SECONDS]]
    (when (not (getattr connection "autocommit" False))
      (raise (ValueError "PgRecordsHost の接続は自動 commit(psycopg.connect(..., autocommit=True))— 書きの transaction は host が開く")))
    (setv self.connection connection
          self.schema schema
          self.prefix (checked-prefix prefix)
          self.origin-host (or origin-host (socket.gethostname))
          self.poll-seconds poll-seconds
          self.errors unreachable-errors)
    (for [statement (schema-statements self.prefix schema)]
      (self.execute statement)))

  (defn #^ object execute [self #^ Statement statement]
    (.execute self.connection statement.text statement.params))

  (defn #^ list fetch-all [self #^ Statement statement]
    (.fetchall (self.execute statement)))

  (defn #^ (| tuple None) fetch-one [self #^ Statement statement]
    (.fetchone (self.execute statement))))


(defn #^ None drop-records-tables [#^ PgRecordsHost host]
  "検の後片付け: この host の接頭辞の表を消す(検の解釈器だけが呼ぶ)。"
  (for [statement (drop-statements host.prefix)]
    (host.execute statement))
  None)


(defclass StoreHead [NamedTuple]
  "置き場の頭: epoch = 置き場の版 / floor = 変更の列の最も古い番号の手前(これより前の位置は Reset)/ head = 最新の変更の番号。"
  (#^ int epoch)
  (#^ int floor)
  (#^ int head))


(defclass ExpiredRow [NamedTuple]
  "保持の期限を過ぎた行 1 つ: table = 表の名 / record = state_rows の行(key payload version updated_ms)。"
  (#^ str table)
  (#^ tuple record))


(defn #^ StoreHead store-head [#^ PgRecordsHost host]
  (setv #(epoch floor head) (host.fetch-one (store-head-statement host.prefix)))
  (StoreHead (int epoch) (int floor) (int head)))


(defn #^ FrozenMap decoded-value [#^ str payload]
  "state_rows / row_changes の payload(JSON の綴り)→ 行の値(深く凍らせた写像)。DB から読む境界はここ 1 か所。"
  (frozen-json-object (json.loads payload) "state_rows の payload"))


(defn #^ Row row-of [#^ tuple record]
  "state_rows の行(key payload version …)→ Row。"
  (Row (key-from-text (get record 0)) (decoded-value (get record 1)) (int (get record 2))))


(defn #^ (| T Unreachable) guarded [#^ PgRecordsHost host #^ (get Callable [] T) action]
  "action() を流し、接続の失敗を Unreachable の答えに写す。"
  (try
    (action)
    (except [error host.errors]
      (Unreachable (.format "PostgreSQL に届かない: {}" error)))))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defn #^ list expired-rows [#^ PgRecordsHost host #^ int now-ms]
  "期限を過ぎた行(ExpiredRow)の列(表の名の順・鍵の順)。判断は admission.row-expired?。"
  (lfor #(name decl) (sorted (.items host.schema.tables))
        :if (isinstance decl.retention KeepFor)
        record (host.fetch-all (terminal-rows-statement host.prefix name decl.state-field decl.terminal))
        :if (row-expired? decl (decoded-value (get record 1)) (int (get record 3)) now-ms)
        (ExpiredRow name (tuple record))))


(defn #^ int purge-expired [#^ PgRecordsHost host #^ int now-ms]
  "期限を過ぎた行を消して変更の列に「消えた」を積み、期限を過ぎた出来事を捨てる。候補が無ければ lock を取らない。
   答え = 消した行の数。"
  (setv removed 0)
  (when (expired-rows host now-ms)
    (with [(.transaction host.connection)]
      (host.execute (lock-statement host.prefix))
      (setv epoch (. (store-head host) epoch))
      (for [expired (expired-rows host now-ms)]
        (setv #(text _ version) (cut expired.record 3))
        (host.execute (delete-row-statement host.prefix expired.table text (int version)))
        (host.execute (append-change-statement host.prefix expired.table text (int version) None now-ms epoch))
        (+= removed 1))))
  (for [#(name decl) (sorted (.items host.schema.streams))]
    (when (isinstance decl.retention KeepFor)
      (host.execute (expire-events-statement host.prefix name (- now-ms (int (* 1000 decl.retention.seconds)))))))
  removed)


;; --- 行 --------------------------------------------------------------------------------------------------

(defn #^ object pg-read-row [#^ PgRecordsHost host #^ ReadRow ask]
  (host.schema.table ask.table)
  (setv record (host.fetch-one (read-row-statement host.prefix ask.table (key-text ask.key))))
  (if (is record None) (Missing) (row-of record)))


(defn #^ object pg-list-rows [#^ PgRecordsHost host #^ ListRows ask]
  (setv decl (host.schema.table ask.table)
        #(epoch _ head) (store-head host))
  (when (and (is-not ask.cursor None) (!= ask.cursor.epoch epoch))
    (return (Reset epoch)))
  (setv refusal (where-refusal decl ask.where))
  (when refusal (return refusal))
  (setv records (host.fetch-all (list-rows-statement host.prefix ask.table
                                                     (if (is ask.cursor None) None ask.cursor.after-key)
                                                     (dfor #(name value) (.items ask.where) name (canonical-json value))
                                                     ask.limit))
        taken (cut records ask.limit)
        rows (tuple (gfor record taken (listed-row decl ask.fields (row-of record)))))
  (Page rows
        (if (> (len records) ask.limit) (ListCursor epoch (get (get taken -1) 0)) None)
        epoch
        head))


(defn #^ object pg-put-row [#^ PgRecordsHost host #^ str writer #^ PutRow ask #^ int now-ms]
  (setv decl (host.schema.table ask.table)
        text (key-text ask.key))
  (with [(.transaction host.connection)]
    (host.execute (lock-statement host.prefix))
    (setv epoch (. (store-head host) epoch)
          record (host.fetch-one (lock-row-statement host.prefix ask.table text))
          current (if (is record None) None (row-of record))
          conflict (judge-expect ask.expect current))
    (when conflict (return conflict))
    (setv verdict (judge-put decl writer current ask.key ask.value :operators host.schema.operators))
    (when (isinstance verdict Refused) (return verdict))
    (setv version (if (is current None) 1 (+ current.version 1))
          payload (canonical-json verdict.value))
    (host.execute (upsert-row-statement host.prefix ask.table text payload version now-ms writer host.origin-host epoch))
    (host.execute (append-change-statement host.prefix ask.table text version payload now-ms epoch)))
  (Written version verdict.value))


(defn #^ (| RowChanged RowRemoved) change-of [#^ tuple record]
  "row_changes の行(seq ledger key version payload)→ RowChanged | RowRemoved。"
  (setv #(seq table text version payload) record)
  (if (is payload None)
      (RowRemoved table (key-from-text text) (int seq))
      (RowChanged table (key-from-text text) (int version) (decoded-value payload) (int seq))))


(defn #^ object pg-watch-scan [#^ PgRecordsHost host #^ WatchChanges ask]
  (for [name ask.tables] (host.schema.table name))
  (setv #(epoch floor head) (store-head host)
        cursor ask.cursor)
  (when (or (!= cursor.epoch epoch) (< cursor.sequence floor) (> cursor.sequence head))
    (return (Reset epoch)))
  (setv items (tuple (gfor record (host.fetch-all (changes-statement host.prefix cursor.sequence head ask.tables ask.limit))
                           (change-of record))))
  (Changes items (WatchCursor epoch (next-watch-sequence items ask.limit head))))


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defn #^ Event event-of [#^ str stream #^ tuple record]
  (setv #(seq at payload) record
        decoded (json.loads payload))
  (Event stream (int seq) (get decoded "idempotencyKey") (get decoded "body") (get decoded "writer") (int at)))


(defn #^ object pg-append [#^ PgRecordsHost host #^ str writer #^ AppendEvent ask #^ int now-ms]
  (setv decl (host.schema.stream ask.stream))
  (with [(.transaction host.connection)]
    (host.execute (lock-statement host.prefix))
    (setv epoch (. (store-head host) epoch)
          found (host.fetch-one (find-event-statement host.prefix ask.stream ask.idempotency-key))
          verdict (judge-append decl writer ask.body (if (is found None) None (event-of ask.stream found))))
    (when (isinstance verdict Refused) (return verdict))
    (when (isinstance verdict AppendReplay) (return (Appended verdict.sequence)))
    (setv payload (canonical-json {"idempotencyKey" ask.idempotency-key "writer" writer "body" ask.body})
          sequence (int (get (host.fetch-one (insert-event-statement host.prefix ask.stream now-ms payload host.origin-host epoch))
                             0))))
  (Appended sequence))


(defn #^ Events pg-read-events [#^ PgRecordsHost host #^ ReadEvents ask]
  (host.schema.stream ask.stream)
  (setv items (tuple (gfor record (host.fetch-all (read-events-statement host.prefix ask.stream ask.after ask.limit))
                           (event-of ask.stream record))))
  (Events items (if items (. (get items -1) sequence) ask.after)))


(defn #^ int pg-advance-epoch [#^ PgRecordsHost host]
  (with [(.transaction host.connection)]
    (host.execute (lock-statement host.prefix))
    (setv epoch (int (get (host.fetch-one (advance-epoch-statement host.prefix)) 0)))
    (host.execute (forget-changes-statement host.prefix)))
  epoch)


;; --- 手入れ ------------------------------------------------------------------------------------------------

(defk pg-prune-changes [host ask now-ms]
  {:pre [(: host PgRecordsHost) (: ask PruneChanges) (: now-ms int)] :post [(: % Pruned)]}
  "変更の列が際限なく伸びないように、keep-seconds より古い変更を消して floor を上げる(書きの lock の中 — 読み手の断面と揃える)。"
  (with [(.transaction host.connection)]
    (host.execute (lock-statement host.prefix))
    (setv #(floor removed) (host.fetch-one (prune-changes-statement host.prefix (- now-ms (int (* 1000 ask.keep-seconds)))))))
  (Pruned (int floor) (int removed)))


;; --- handler ------------------------------------------------------------------------------------------------

(defhandler pg-records-handler [#^ PgRecordsHost host #^ str writer]
  (ReadRow [table key]
    (<- now (GetTime))
    (resume (guarded host (fn [] (purge-expired host (epoch-ms now)) (pg-read-row host effect)))))
  (ListRows [table where fields cursor limit]
    (<- now (GetTime))
    (resume (guarded host (fn [] (purge-expired host (epoch-ms now)) (pg-list-rows host effect)))))
  (PutRow [table key value expect approval]
    (<- now (GetTime))
    (resume (guarded host (fn [] (purge-expired host (epoch-ms now)) (pg-put-row host writer effect (epoch-ms now))))))
  (WatchChanges [tables cursor timeout limit]
    (<- answer (wait-for-changes (fn [now-ms] (Pure (guarded host (fn [] (purge-expired host now-ms) (pg-watch-scan host effect)))))
                                 host.poll-seconds timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- now (GetTime))
    (resume (guarded host (fn [] (purge-expired host (epoch-ms now)) (pg-append host writer effect (epoch-ms now))))))
  (ReadEvents [stream after limit]
    (<- now (GetTime))
    (resume (guarded host (fn [] (purge-expired host (epoch-ms now)) (pg-read-events host effect)))))
  (AdvanceStoreEpoch []
    (resume (pg-advance-epoch host)))
  (SweepExpired []
    (<- now (GetTime))
    (resume (guarded host (fn [] (Swept (purge-expired host (epoch-ms now)))))))
  (PruneChanges [keep-seconds]
    (<- now (GetTime))
    (try
      (<- pruned (pg-prune-changes host effect (epoch-ms now)))
      (except [error host.errors]
        (setv pruned (Unreachable (.format "PostgreSQL に届かない: {}" error)))))
    (resume pruned)))
