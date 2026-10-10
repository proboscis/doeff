;;; 汎用の SQL の effect(sql_effects.hy)の本物の PostgreSQL の答え手のうち、scheduler を塞がない版 pooled-postgres-sql-handler
;;; (agora-redesign #880 U2・G2)。postgres-sql-handler(postgres_sql.hy)も #1215 から scheduler を塞がない(呼び 1 つに thread 1 本・許可は
;;; thread の間の錠)。この版は同じ文・同じ値の写し・同じ transaction の手順(run-in-transaction)を使い、thread の数を呼び手の pool で
;;; 抑えるために、待ち方を次のように変える:
;;;   - 接続の許可の待ち = scheduler の CreateSemaphore / AcquireSemaphore(database ごとに PostgresConnections の size 個)。許可を待つ task の
;;;     横で他の task が回る。
;;;   - driver の I/O(接続を開く・文を流す・COMMIT・ROLLBACK・接続を返す)だけを呼び手の pool の thread へ逃がし、外から完了させる promise
;;;     (CreateExternalPromise + Wait)で待つ(thread_pool_compute.hy と同じ作法)。待つのは撃った task だけ。
;;;   - SqlQuery / SqlInsertRows / SqlEnsureTables = 許可を取り、pool の仕事 1 つで「接続を借りる → 流す → 返す」。
;;;   - SqlTransaction = 許可を取り、接続を 1 本借りて、BEGIN(lock-key が在れば pg_advisory_xact_lock(hashtext(:key)) — postgres-sql-handler と
;;;     同じ文)→ program → COMMIT の各段を pool で流す。中の SqlQuery は transaction の scope の handler が同じ接続で pool へ回す(program 側の
;;;     約束は postgres-sql-handler と同じ)。batched = True を選んだ transaction だけは postgres-sql-handler と同じ往復のまとめ方(BEGIN と錠は
;;;     最初の文と同じ往復・commit の束は COMMIT と同じ往復 — 往復 1 回 = pipeline 1 つ・#3605)で、借りる・全部の往復と間の program・返すを
;;;     pool の仕事 1 つで回す(往復の間に scheduler へ戻らない — postgres_sql.hy の offloaded-batched-transaction)。transaction の外の SqlBatch は名指して断る
;;;     (stray-batch)。
;;;   - 通知(SqlNotify・SqlHangNotice・SqlDropNotice — agora-redesign #3073・名で絞る形は #3688)は postgres-sql-handler と同じ手順
;;;     (postgres_sql.hy の notified・hung-notice — 名の重なる呼び鈴だけを、接続を返した後に鳴らす)。transaction の外の SqlNotify は接続の
;;;     許可を取ってから流す。呼び鈴を掛けるのは待ち受けの接続で、許可を使わない。
;;;   - 取り消し: 待っている task が Cancel されたら、run-in-transaction が ROLLBACK を流し、接続を返し(返す時にも transaction の途中なら
;;;     rollback)、許可を返してから取り消しを通す。始まっていない pool の仕事は外し、取り消しの後に出来た答え(借りた接続)は pool で返す。
;;;     走り中の文は止めない(文が終わるまで ROLLBACK は driver の錠で待つ)。
;;; 契約(組み立ての側が守る):
;;;   - pool(Executor)の worker の数は、この handler に渡す PostgresConnections の宣言した接続の数の合計(database の数 × size)以上にする。
;;;     許可を持つ task の driver の呼びが pool の中で互いを待たないため。pool の持ち主は呼び手(作るのも閉じるのも組み立ての側)。
;;;   - 並び: 外側に scheduled と、session の値の置き場(doeff_core_effects の state)が要る。
;;;   - 同じ PostgresConnections を postgres-sql-handler と分け合ってもよい(接続の上限は PostgresConnections の錠が pool の thread の中で守る)。
;;;   - 仕組みの置き場: 外から完了させる promise で待つ係(offloaded・取り消しの後始末 Handoff)は offloaded_call.hy、接続を借りて流して返す
;;;     仕事(offloaded-statement)と transaction の段(offloaded-transaction)は postgres_sql.hy — postgres-sql-handler と共に使う(#1215)。この file が
;;;     持つのは許可の待ち方(scheduler の semaphore)と、呼び手の pool を使うことだけ。
(require doeff-hy.macros [defhandler defk <- val var])
(import concurrent.futures [Executor])
(import doeff [Program])
(import doeff_core_effects.scheduler [CreateSemaphore AcquireSemaphore ReleaseSemaphore])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice])
(import doeff_core_effects.sql_transaction [stray-batch])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-query postgres-insert postgres-ensure-tables offloaded-statement
                                         offloaded-transaction notified hung-notice])


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
                          (offloaded-statement connections pool database (fn [leased] (postgres-query leased request)))))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (permitted (get permits database)
                          (offloaded-statement connections pool database (fn [leased] (postgres-insert leased request)))))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (<- answer (permitted (get permits database)
                          (offloaded-statement connections pool database (fn [leased] (postgres-ensure-tables leased tables)))))
    (resume answer))
  (SqlTransaction [database program lock-key batched] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (<- answer (permitted (get permits database) (offloaded-transaction connections pool database program lock-key batched)))
    (resume answer))
  ;; 束は transaction の中の往復をまとめる物 — transaction の中では SqlTransaction の scope が答え、ここへ来るのは外で出した束だけ。
  (SqlBatch [database queries commit] :when (in database (.names connections))
    (<- refusal (stray-batch database))
    (raise refusal))
  (SqlNotify [database channel topics] :when (in database (.names connections))
    (<- created (permit-for permits database connections.size))
    (:= permits (| {database created} permits))
    (<- answer (permitted (get permits database) (notified connections pool database effect)))
    (resume answer))
  (SqlHangNotice [database channel topics] :when (in database (.names connections))
    (<- answer (hung-notice connections pool database channel topics))
    (resume answer))
  (SqlDropNotice [database channel bell] :when (in database (.names connections))
    (.drop (.listener connections database channel) bell)
    (resume None)))
