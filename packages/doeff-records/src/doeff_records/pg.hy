;;; PostgreSQL の handler — 公開 effect 6 つに PostgreSQL の表で答える(本番の置き場)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ(memory の handler と同じ関数)。
;;; 文は pg_sql.hy(純粋)で、流すのはこの file の PgRecordsHost だけ。接続は composition root が開いて渡す(psycopg 3・
;;; 自動 commit の接続 — 書きは host が transaction を開く)。psycopg はこの package の依存に無い(extra "pg")。
;;; 接続の失敗(psycopg の OperationalError / InterfaceError)は Unreachable の答えに写す。それ以外の例外は実装の誤りとして上がる。
(require doeff-hy.macros [defhandler])
(import importlib)
(import json)
(import socket)
(import typing [NamedTuple])
(import doeff [Pure])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [GetTime])
(import doeff_records.values [RecordsSchema KeepFor Row Missing Page Written RowChanged RowRemoved Changes Appended Event
                              Events Reset WatchCursor ListCursor Refused Unreachable])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.admission [AppendReplay judge-expect judge-put judge-append row-expired? where-refusal listed-row
                                 key-text key-from-text canonical-json next-watch-sequence refuse-every-approval epoch-ms])
(import doeff_records.watching [wait-for-changes])
(import doeff_records.pg_sql [DEFAULT-PREFIX checked-prefix schema-statements drop-statements lock-statement
                              store-head-statement read-row-statement lock-row-statement list-rows-statement
                              terminal-rows-statement upsert-row-statement delete-row-statement append-change-statement
                              changes-statement advance-epoch-statement forget-changes-statement find-event-statement
                              insert-event-statement read-events-statement expire-events-statement])

(setv DEFAULT-POLL-SECONDS 0.2)


(defn #^ tuple unreachable-errors []
  "接続の失敗の型(psycopg を読み込めない process では空 — その時は接続も無い)。"
  (try
    (setv psycopg (importlib.import-module "psycopg"))
    #(psycopg.OperationalError psycopg.InterfaceError)
    (except [ImportError] #())))


(defclass PgRecordsHost []
  "PostgreSQL の置き場 1 つ: connection = 自動 commit の psycopg の接続 / schema = 宣言 / prefix = 表の名の接頭辞 /
   origin-host = 行に刻む機体の名 / approval-check = 承認の確かめ方 / poll-seconds = WatchChanges の読み直しの間隔。
   作る時に表を用意する(何度でも同じ)。"
  (defn __init__ [self connection #^ RecordsSchema schema * [prefix DEFAULT-PREFIX] [origin-host None]
                  [approval-check refuse-every-approval] [poll-seconds DEFAULT-POLL-SECONDS]]
    (when (not (getattr connection "autocommit" False))
      (raise (ValueError "PgRecordsHost の接続は自動 commit(psycopg.connect(..., autocommit=True))— 書きの transaction は host が開く")))
    (setv self.connection connection
          self.schema schema
          self.prefix (checked-prefix prefix)
          self.origin-host (or origin-host (socket.gethostname))
          self.approval-check approval-check
          self.poll-seconds poll-seconds
          self.errors (unreachable-errors))
    (for [statement (schema-statements self.prefix schema)]
      (self.execute statement)))

  (defn execute [self statement]
    (.execute self.connection statement.text statement.params))

  (defn fetch-all [self statement]
    (.fetchall (self.execute statement)))

  (defn fetch-one [self statement]
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


(defn #^ Row row-of [record]
  "state_rows の行(key payload version …)→ Row。"
  (Row (key-from-text (get record 0)) (decoded-value (get record 1)) (int (get record 2))))


(defn guarded [#^ PgRecordsHost host action]
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


(defn #^ None purge-expired [#^ PgRecordsHost host #^ int now-ms]
  "期限を過ぎた行を消して変更の列に「消えた」を積み、期限を過ぎた出来事を捨てる。候補が無ければ lock を取らない。"
  (when (expired-rows host now-ms)
    (with [(.transaction host.connection)]
      (host.execute (lock-statement host.prefix))
      (setv epoch (. (store-head host) epoch))
      (for [expired (expired-rows host now-ms)]
        (setv #(text _ version) (cut expired.record 3))
        (host.execute (delete-row-statement host.prefix expired.table text (int version)))
        (host.execute (append-change-statement host.prefix expired.table text (int version) None now-ms epoch)))))
  (for [#(name decl) (sorted (.items host.schema.streams))]
    (when (isinstance decl.retention KeepFor)
      (host.execute (expire-events-statement host.prefix name (- now-ms (int (* 1000 decl.retention.seconds)))))))
  None)


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
    (setv verdict (judge-put decl writer current ask.key ask.value ask.approval host.approval-check))
    (when (isinstance verdict Refused) (return verdict))
    (setv version (if (is current None) 1 (+ current.version 1))
          payload (canonical-json verdict.value))
    (host.execute (upsert-row-statement host.prefix ask.table text payload version now-ms writer host.origin-host epoch))
    (host.execute (append-change-statement host.prefix ask.table text version payload now-ms epoch)))
  (Written version verdict.value))


(defn #^ object change-of [record]
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

(defn #^ Event event-of [#^ str stream record]
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


;; --- handler ------------------------------------------------------------------------------------------------

(defhandler pg-records-handler [host writer]
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
    (resume (pg-advance-epoch host))))
