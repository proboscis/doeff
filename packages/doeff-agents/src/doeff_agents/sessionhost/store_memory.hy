;;; session の行の置き場(SessionStore* の effect)の I/O なしの答え手 memory-session-store — SQLite の store(store.hy の
;;; sqlite-session-store)の fake。判断は SQLite の store と同じ defk を呼ぶ:
;;;   行の値     SQLite と同じ値の並び(stored-values — STORED-COLUMNS の順・JSON の列は文字列)で持ち、読みは同じ snapshot-from-db-row
;;;   書き       merged-snapshot(読んだ時点から変わった欄だけを重ねる)→ reactivation-refusal(terminal の行を active へ戻さない)→
;;;              overlaid-values(OVERLAY-RULES — SQLite の ON CONFLICT 節を作る表と同じ表)
;;;   出来事     event-payload(書いた行の wire 形・行が無ければ渡された行の欄)
;;; 一覧の絞りと順は SQLite の SQL と同じ意味をここで書く(同じ答えになることは契約テスト test_session_store_contract.hy が確かめる)。
;;; 置き場(MemorySessionRows)は session の値に持つ — doeff_core_effects の state が外側に要る。ReadMemorySessionRows で今の中身を読める。
;;; 契約の外(effect ではなく SQLite の store の actor の op だけが持つ物): 命令の監査の履歴・lease・report_result の直の UPDATE・起動時の
;;; latch の解除・履歴の刈り取り・出来事の時刻と通し番号。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace :as with-fields])
(import doeff [EffectBase])
(import doeff_agents.sessionhost.effects [
  SessionRow
  SessionStoreListActive
  SessionStoreListCleanupPending
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES TERMINAL-STATUSES])
(import doeff_agents.sessionhost.store [
  event-payload
  merged-snapshot
  overlaid-values
  reactivation-refusal
  snapshot-from-db-row
  snapshot-to-policy-row
  stored-values])


(defrecord StoredEvent
  "memory の置き場の出来事 1 つ(SQLite の agent_session_events の行のうち、session・種類・載せた行)。"
  (#^ str session-id)
  (#^ str event-type)
  (#^ dict payload))


(defrecord MemorySessionRows
  "memory の置き場の中身(rows = 行の値の並び(stored-values の形)を初めて書いた順に・events = 出来事を記録した順に)。"
  (setv #^ (get tuple #(tuple ...)) rows #())
  (setv #^ (get tuple #(StoredEvent ...)) events #()))


(defclass [(dataclass :frozen True)] ReadMemorySessionRows [EffectBase]
  "memory の置き場の今の中身(MemorySessionRows)を読む(検と筋書きが置き場を覗くため — memory-session-store だけが答える)。")


(defk stored-snapshot [store session-id]
  {:pre [(: store MemorySessionRows) (: session-id str)]
   :post [(: % (| dict None))]
   :tags {:context "session-store" :role "foundation"}}
  "session の行を SQLite の読みと同じ snapshot dict で読むため(無ければ None)。"
  (val found (lfor values store.rows :if (= (get values 0) session-id) values))
  (if found (snapshot-from-db-row (get found 0)) None))


(defk stored-row [store session-id]
  {:pre [(: store MemorySessionRows) (: session-id str)]
   :post [(: % (| SessionRow None))]
   :tags {:context "session-store" :role "foundation"}}
  "session の行を SessionStoreGet の答えと同じ SessionRow で読むため(無ければ None — 検が置き場の行を覗く口)。"
  (<- snap (stored-snapshot store session-id))
  (if (is snap None) None (snapshot-to-policy-row snap)))


(defk stored-rows [store]
  {:pre [(: store MemorySessionRows)]
   :post [(: % dict)]
   :tags {:context "session-store" :role "foundation"}}
  "置き場の全部の行を session id → SessionRow で読むため(初めて書いた順 — 検が置き場の行を覗く口)。"
  (dfor values store.rows
        (get values 0) (snapshot-to-policy-row (snapshot-from-db-row values))))


(defk with-row [store row]
  {:pre [(: store MemorySessionRows) (: row SessionRow)]
   :post [(: % MemorySessionRows)]
   :tags {:context "session-store" :role "foundation"}}
  "SessionStoreUpsert の書きを置き場へ重ねるため: SQLite の store の db-merge-policy-row → db-upsert-snapshot と同じ順に、
   merged-snapshot で書く行を決め、在る行なら reactivation-refusal で断るかを見て、OVERLAY-RULES で列を重ねる。"
  (val found (lfor values store.rows :if (= (get values 0) row.session-id) values))
  (val existing (if found (snapshot-from-db-row (get found 0)) None))
  (<- merged dict (merged-snapshot existing row))
  (<- incoming tuple (stored-values merged))
  (when (is existing None)
    (return (with-fields store :rows (+ store.rows #(incoming)))))
  (<- refusal (reactivation-refusal row.session-id (get existing "status") (get merged "status")))
  (when (is-not refusal None)
    (raise refusal))
  (<- overlaid tuple (overlaid-values (get found 0) incoming))
  (with-fields store :rows (tuple (gfor values store.rows
                                        (if (= (get values 0) row.session-id) overlaid values)))))


(defk with-event [store session-id event-type row]
  {:pre [(: store MemorySessionRows) (: session-id str) (: event-type str) (: row SessionRow)]
   :post [(: % MemorySessionRows)]
   :tags {:context "session-store" :role "foundation"}}
  "SessionStoreRecordEvent の出来事を置き場へ足すため(載せる行は SQLite の store と同じ event-payload で決める)。"
  (<- snap (stored-snapshot store session-id))
  (<- payload dict (event-payload snap row))
  (with-fields store :events (+ store.events #((StoredEvent :session-id session-id :event-type event-type :payload payload)))))


(defk snapshots-of [store]
  {:pre [(: store MemorySessionRows)] :post [(: % list)] :tags {:context "session-store" :role "foundation"}}
  "置き場の全部の行を snapshot dict で読むため(一覧の絞りの材料)。"
  (lfor values store.rows (snapshot-from-db-row values)))


(defk active-rows [store]
  {:pre [(: store MemorySessionRows)] :post [(: % list)] :tags {:context "session-store" :role "foundation"}}
  "SessionStoreListActive の答えを作るため: 非終端の行だけを、SQLite の一覧と同じ順(started_at の新しい順・同じなら session id の順)に。"
  (<- snaps list (snapshots-of store))
  (val by-id (sorted (gfor s snaps :if (in (get s "status") ACTIVE-STATUSES) s) :key (fn [s] (get s "session_id"))))
  (lfor s (sorted by-id :key (fn [s] (get s "started_at")) :reverse True) (snapshot-to-policy-row s)))


(defk cleanup-pending-rows [store]
  {:pre [(: store MemorySessionRows)] :post [(: % list)] :tags {:context "session-store" :role "foundation"}}
  "SessionStoreListCleanupPending の答えを作るため: 終端 ∧ cleaned_at 未刻印 ∧ run_to_completion ∧ 非 adopted の行を、SQLite と同じ順
   (started_at の古い順・同じなら session id の順)に。"
  (<- snaps list (snapshots-of store))
  (lfor s (sorted snaps :key (fn [s] #((get s "started_at") (get s "session_id"))))
        :if (and (in (get s "status") TERMINAL-STATUSES)
                 (is (get s "cleaned_at") None)
                 (= (get s "lifecycle") "run_to_completion")
                 (not (get s "adopted")))
        (snapshot-to-policy-row s)))


(defk known-conversation-ids [store]
  {:pre [(: store MemorySessionRows)] :post [(: % list)] :tags {:context "session-store" :role "foundation"}}
  "SessionStoreKnownConversationIds の答えを作るため: 終端を含む全部の行の会話 ID を 1 度ずつ、昇順に。"
  (<- snaps list (snapshots-of store))
  (sorted (sfor s snaps
                :setv conversation (get s "conversation")
                :if (and (isinstance conversation dict) (isinstance (.get conversation "session_id") str))
                (get conversation "session_id"))))


(defhandler memory-session-store [#^ MemorySessionRows initial]
  ;; 引数に残す理由: 初めの行は筋書きごとに違う値(設定ではなく模擬の世界そのもの)。
  (session var store initial)
  (SessionStoreListActive []
    (<- rows list (active-rows store))
    (resume rows))
  (SessionStoreListCleanupPending []
    (<- rows list (cleanup-pending-rows store))
    (resume rows))
  (SessionStoreGet [session-id]
    (<- row (stored-row store session-id))
    (resume row))
  (SessionStoreUpsert [row]
    (<- written MemorySessionRows (with-row store row))
    (:= store written)
    (resume None))
  (SessionStoreResultPayload [session-id]
    (<- snap (stored-snapshot store session-id))
    (resume (if (is snap None) None (get snap "result_payload"))))
  (SessionStoreKnownConversationIds []
    (<- ids list (known-conversation-ids store))
    (resume ids))
  (SessionStoreRecordEvent [session-id event-type row]
    (<- written MemorySessionRows (with-event store session-id event-type row))
    (:= store written)
    (resume None))
  (ReadMemorySessionRows []
    (resume store)))
