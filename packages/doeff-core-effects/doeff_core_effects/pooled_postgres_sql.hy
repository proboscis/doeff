;;; 汎用の SQL の effect(sql_effects.hy)の本物の PostgreSQL の答え手のうち、scheduler を塞がない版 pooled-postgres-sql-handler
;;; (agora-redesign #880 U2・G2)。postgres-sql-handler(postgres_sql.hy)は driver の呼びと接続の空きの待ちを scheduler の thread で同期に行うので、
;;; 1 つの scheduler に要求を並べると遅い問い合わせ 1 つで全部の task が止まる。この版は同じ文・同じ値の写し・同じ transaction の手順
;;; (run-in-transaction)を使い、待ち方だけを変える:
;;;   - 接続の許可の待ち = scheduler の CreateSemaphore / AcquireSemaphore(database ごとに PostgresConnections の size 個)。許可を待つ task の
;;;     横で他の task が回る。
;;;   - driver の I/O(接続を開く・文を流す・COMMIT・ROLLBACK・接続を返す)だけを呼び手の pool の thread へ逃がし、外から完了させる promise
;;;     (CreateExternalPromise + Wait)で待つ(thread_pool_compute.hy と同じ作法)。待つのは撃った task だけ。
;;;   - SqlQuery / SqlInsertRows / SqlEnsureTables = 許可を取り、pool の仕事 1 つで「接続を借りる → 流す → 返す」。
;;;   - SqlTransaction = 許可を取り、接続を 1 本借りて、BEGIN(lock-key が在れば pg_advisory_xact_lock(hashtext(:key)) — postgres-sql-handler と
;;;     同じ文)→ program → COMMIT の各段を pool で流す。中の SqlQuery は transaction の scope の handler が同じ接続で pool へ回す(program 側の
;;;     約束は postgres-sql-handler と同じ)。
;;;   - 取り消し: 待っている task が Cancel されたら、run-in-transaction が ROLLBACK を流し、接続を返し(返す時にも transaction の途中なら
;;;     rollback)、許可を返してから取り消しを通す。始まっていない pool の仕事は外し、取り消しの後に出来た答え(借りた接続)は pool で返す。
;;;     走り中の文は止めない(文が終わるまで ROLLBACK は driver の錠で待つ)。
;;; 契約(組み立ての側が守る):
;;;   - pool(Executor)の worker の数は、この handler に渡す PostgresConnections の宣言した接続の数の合計(database の数 × size)以上にする。
;;;     許可を持つ task の driver の呼びが pool の中で互いを待たないため。pool の持ち主は呼び手(作るのも閉じるのも組み立ての側)。
;;;   - 並び: 外側に scheduled と、session の値の置き場(doeff_core_effects の state)が要る。
;;;   - 同じ PostgresConnections を postgres-sql-handler と分け合ってもよい(接続の上限は PostgresConnections の錠が pool の thread の中で守る)。
(require doeff-hy.macros [defhandler defk <- val var])
(import concurrent.futures [Executor])
(import threading)
(import doeff [Program])
(import doeff_vm [PyVM])
(import doeff_core_effects.scheduler [CreateExternalPromise Wait CreateSemaphore AcquireSemaphore ReleaseSemaphore TaskCancelledError])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SqlUnreachable])
(import doeff_core_effects.sql_transaction [run-in-transaction])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-query postgres-insert postgres-ensure-tables postgres-begin
                                         postgres-control postgres-lease])

;; まだ答えを渡していない印(答えが None の仕事と見分ける)。
(val NOT-DELIVERED (object))


(defn run-driver [program]  ; defk にできない: pool の thread で回す入口(VM の外から新しい VM を起こす)
  "driver を呼ぶ Program(postgres_sql.hy の文の手順)を pool の thread で値にするため。"
  (.run (PyVM) program))


(defn lease-now [#^ PostgresConnections connections #^ str database]  ; defk にできない: pool の thread で回す入口
  "pool の thread で接続を 1 本借りるため(開けなければ SqlUnreachable)。"
  (run-driver (postgres-lease connections database)))


(defn return-abandoned [#^ PostgresConnections connections #^ str database leased]  ; defk にできない: pool の thread で回す後始末の入口
  "取り消された待ち手に届かなかった接続を返すため(借りられなかった答え SqlUnreachable は返す物が無い)。"
  (when (not (isinstance leased SqlUnreachable))
    (.release connections database leased)))


(defn with-lease [#^ PostgresConnections connections #^ str database work]  ; defk にできない: pool の thread で回す入口
  "pool の仕事 1 つで接続を借り、work(接続 → Program)を流し、必ず返すため(文 1 つの effect の答え)。"
  (setv leased (lease-now connections database))
  (if (isinstance leased SqlUnreachable)
      leased
      (try
        (run-driver (work leased))
        (finally (.release connections database leased)))))


(defn keep-nothing [_value]  ; defk にできない: pool の thread で回す後始末の入口(何も持たない答えの後始末)
  "取り消された待ち手に届かなかった答えが資源を持たない時の後始末(何もしない)。"
  None)


(defclass Handoff []
  "pool の仕事 1 つの答えを promise へ渡す係(scheduler の thread と pool の thread の間で錠を取って触る)。待ち手が取り消されたら、始まって
   いない仕事は外し、取り消しの前後に出来た答えは abandon(pool で回す後始末 — 借りた接続を返す等)へ 1 度だけ回す。"

  (defn __init__ [self #^ Executor pool abandon job]  ; defk にできない: 資源の class の初期化
    (setv self.pool pool
          self.abandon abandon
          self.job job
          self.lock (threading.Lock)
          self.abandoned False
          self.delivered NOT-DELIVERED))

  (defn deliver [self promise future]  ; defk にできない: pool の thread から呼ばれる完了の callback(VM の外)
    "仕事の終わりを promise へ渡すため(取り消された後に出来た答えは後始末へ回す)。"
    (with [_ self.lock]
      (cond
        (.cancelled future) None
        (is-not (.exception future) None) (when (not self.abandoned) (.fail promise (.exception future)))
        self.abandoned (.submit self.pool self.abandon (.result future))
        True (do (setv self.delivered (.result future))
                 (.complete promise self.delivered)))))

  (defn give-up [self]  ; defk にできない: scheduler の取り消しの callback(on_cancel — VM の外)と、待ちが取り消しで抜けた時の後始末
    "待ち手が取り消されたら、始まっていない仕事を外し、もう渡した答えを後始末へ回すため(何度呼んでも後始末は 1 度)。"
    (with [_ self.lock]
      (when (not self.abandoned)
        (setv self.abandoned True)
        (.cancel self.job)
        (when (is-not self.delivered NOT-DELIVERED)
          (.submit self.pool self.abandon self.delivered))))))


(defk offloaded [pool call abandon]
  {:pre [(: pool Executor) (: call "() → 値 の callable") (: abandon "(値) → None の callable")] :post [(: % "call の答え")]
   :tags {:context "sql" :role "foundation"}}
  "call を pool の thread で回し、この task だけが外から完了させる promise で待つため(scheduler の他の task は回り続ける)。待ちが取り消され
   たら、始まっていない仕事は外し、届かなかった答えは abandon で後始末する(完了の値が届く前の取り消しも、届いた後で再開の前の取り消しも)。"
  (<- promise (CreateExternalPromise))
  (val handoff (Handoff pool abandon (.submit pool call)))
  (.on-cancel promise (fn [] (.give-up handoff)))
  (.add-done-callback handoff.job (fn [done] (.deliver handoff promise done)))
  (try
    (<- outcome (Wait promise.future))
    (except [TaskCancelledError]
      (.give-up handoff)
      (raise)))
  outcome)


(defk permit-for [permits database size]
  {:pre [(: permits dict) (: database str) (: size int)] :post [(: % "scheduler の Semaphore")]
   :tags {:context "sql" :role "foundation"}}
  "database の接続の許可(scheduler の semaphore)を引くため(まだ無ければ size 個の許可で作る)。"
  (if (in database permits)
      (get permits database)
      (do (<- created (CreateSemaphore size))
          created)))


(defk permitted [permit program]
  {:pre [(: permit "scheduler の Semaphore") (: program Program)] :post [(: % "program の答え")]
   :tags {:context "sql" :role "foundation"}}
  "接続の許可を scheduler で待って取り、program を回し、必ず許可を返すため。"
  (<- (AcquireSemaphore permit))
  (try
    (<- answer program)
    answer
    (finally (<- (ReleaseSemaphore permit)))))


(defk pooled-transaction [connections pool database program lock-key]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: lock-key (| str None))]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "接続 1 本を借りて program を 1 つの transaction で回し、必ず接続を返すため(各段の driver の I/O は pool で — 頭の註)。"
  (<- leased (offloaded pool (fn [] (lease-now connections database)) (fn [value] (return-abandoned connections database value))))
  (if (isinstance leased SqlUnreachable)
      leased
      (try
        (<- answer (run-in-transaction database program
                                       (fn [request] (offloaded pool (fn [] (run-driver (postgres-query leased request))) keep-nothing))
                                       (fn [request] (offloaded pool (fn [] (run-driver (postgres-insert leased request))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-driver (postgres-begin leased lock-key))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-driver (postgres-control leased "COMMIT"))) keep-nothing))
                                       (fn [] (offloaded pool (fn [] (run-driver (postgres-control leased "ROLLBACK"))) keep-nothing))))
        answer
        (finally
          (<- (offloaded pool (fn [] (.release connections database leased)) keep-nothing))))))


(defhandler pooled-postgres-sql-handler [#^ PostgresConnections connections #^ Executor pool]
  ;; 引数に残す理由: 接続の貸し出しは組み立ての側が作って閉じる資源で(DSN と資格を持つ)、答える database の名で ClickHouse の答え手と
  ;; 同じ組に並べ分ける。pool も組み立ての側が作って閉じる資源(worker の数の契約は頭の註)。
  ;; permits = database の名 → 接続の許可(scheduler の semaphore — 初めて使う拍に作る)(session の値)。
  "scheduler を塞がない本物の PostgreSQL の答え手(頭の註)。connections が宣言した database の名にだけ答える(他の名は外側へ回す)。"
  {:tags {:context "sql" :role "foundation"}}
  (session var permits {})
  (SqlQuery [database statement params] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (val request (SqlQuery database statement params))
    (<- answer (permitted (get permits database)
                          (offloaded pool (fn [] (with-lease connections database (fn [leased] (postgres-query leased request))))
                                     keep-nothing)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (permitted (get permits database)
                          (offloaded pool (fn [] (with-lease connections database (fn [leased] (postgres-insert leased request))))
                                     keep-nothing)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (<- answer (permitted (get permits database)
                          (offloaded pool (fn [] (with-lease connections database (fn [leased] (postgres-ensure-tables leased tables))))
                                     keep-nothing)))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (<- answer (permitted (get permits database) (pooled-transaction connections pool database program lock-key)))
    (resume answer)))
