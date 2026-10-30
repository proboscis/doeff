;;; 文 1 つの SQL の effect(SqlQuery・SqlInsertRows・SqlEnsureTables)を答える offloaded-statement(postgres_sql.hy — postgres-sql-handler と
;;; pooled-postgres-sql-handler が共に使う)の取り消しの検(agora-redesign #2792)。実 PostgreSQL を使わず、接続の貸し出しを偽物に替える:
;;; 許可は本物と同じ database ごとの thread の間の錠で数え、接続の代わりに札を貸す。実 PG の検は DSN が無いと skip されるので、この検は
;;; それに頼らない。
;;;   - 場面(record の DB が 30 秒止まった形 — 時刻は順序だけで写す): 許可 4 本・読み 12 件。最初の 4 件の文は DB の止まりで返らず、残り
;;;     8 件は許可を待つ。要求の上限(10 秒)で 12 件とも取り消し、その後で止まりが解けて許可が戻る。数える物 = 許可を待っている間に
;;;     取り消された要求が、許可を取った後に流した文の数。直す前は 8(許可が取れると取り消しを見ずに文を流した)・直した後は 0。走り出して
;;;     いた 4 件の文は止めない(直しの外)。
;;;   - 借りと文の仕事が始まる前に取り消された要求は、仕事が外れて接続を借りない(返す物も無い)。
;;;   - 文 1 つは借りと文と返却を pool の仕事 1 つで答える(agora card ki-5c3e73834e73 — 以前は借りと文の 2 つ)。
(require doeff-hy.macros [deftest defk deff <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import queue [Queue])
(import concurrent.futures [Future])
(import doeff_core_effects._offloaded_call [ReusedThreads])
(import doeff_core_effects [PostgresConnections PostgresDatabase]
        doeff_core_effects._postgres_sql [DRIVER-THREADS offloaded-statement])
(import doeff_core_effects [SqlRows])
(import doeff_core_effects [CreateExternalPromise Wait Spawn Cancel TaskCancelledError])

(val DB "store")
;; database ごとの許可の数(record の要求用の名と同じ 4 本)と、同時に来る読みの数。
(val PERMITS 4)
(val READS 12)
;; 検の殻の待ちの上限(秒)。筋書きが崩れた時に、pytest の上限(60 秒)まで固まらずに名指しで落とす。
(val SETTLE-SECONDS 20.0)


(defrecord FakeLease
  "偽の貸し出しが接続の代わりに貸す札。granted-after-cancel = 許可が取れた時に、取り消しの印が既に立っていたか。"
  (#^ bool granted-after-cancel))


(defclass Countdown []
  "thread の間で起きた事を数え、total 件目で done を 1 度だけ呼ぶ係(検の殻 — 資源なので値の型ではない)。"

  (deff __init__ [self total done]  ; defk にできない: 検の殻の資源の初期化
    {:pre [(: self Countdown) (: total int) (: done "() → None の callable")] :post [(: % None)]}
    "数える件数と、揃った時の呼びを受けるため。"
    (setv self.lock (threading.Lock)
          self.left total
          self.done done))

  (deff tick [self]  ; defk にできない: driver の thread から呼ぶ口(VM の外)
    {:pre [(: self Countdown)] :post [(: % None)]}
    "1 件を数え、total 件目なら done を呼ぶため。"
    (with [_ self.lock]
      (setv self.left (- self.left 1))
      (when (= self.left 0)
        (self.done)))))


(defclass StalledConnections [PostgresConnections]
  "接続の貸し出しの偽物(頭の註)。許可は本物の acquire と同じ database ごとの thread の間の錠(PERMITS 本)で数え、接続の代わりに
   FakeLease を貸す。arrivals = 借りに来た件を数える係・cancelled = 取り消しの印・returned = 返った件を数える係。"

  (deff __init__ [self arrivals cancelled returned]  ; defk にできない: 検の殻の資源の初期化
    {:pre [(: self StalledConnections) (: arrivals Countdown) (: cancelled threading.Event) (: returned Countdown)] :post [(: % None)]}
    "数える係と取り消しの印を受け、database 1 つ・許可 PERMITS 本の貸し出しにするため。"
    (PostgresConnections.__init__ self #((PostgresDatabase :name DB :dsn "")) :size PERMITS)
    (setv self.arrivals arrivals
          self.cancelled cancelled
          self.returned returned))

  (deff acquire [self name]  ; defk にできない: 本物の acquire と同じ口(driver の thread で blocking に待つ)
    {:pre [(: self StalledConnections) (: name str)] :post [(: % FakeLease)]}
    "借りに来た事を数えてから許可を待ち、取れた時に取り消しの印が立っていたかを札に書いて貸すため。"
    (.tick self.arrivals)
    (.acquire (get self.permits name))
    (FakeLease :granted-after-cancel (.is-set self.cancelled)))

  (deff release [self name connection]  ; defk にできない: 本物の release と同じ口(driver の thread で呼ぶ)
    {:pre [(: self StalledConnections) (: name str) (: connection FakeLease)] :post [(: % None)]}
    "許可を返し、返った事を数えるため。"
    (.release (get self.permits name))
    (.tick self.returned)))


(defclass HoldingFirstCall [ReusedThreads]
  "渡された仕事を ReusedThreads と同じく thread で回し、1 つ目の仕事(文 1 つの借りと文と返却の仕事)だけは始めずに持つ Executor(検の殻 —
   資源なので値の型ではない)。持った時に、持った Future で holding を完了させる。持った仕事は走らない。"

  (deff __init__ [self holding]  ; defk にできない: 検の殻の資源の初期化
    {:pre [(: self HoldingFirstCall) (: holding "scheduler の ExternalPromise")] :post [(: % None)]}
    "持った事を知らせる promise を受け、他の仕事を回す ReusedThreads の欄も置くため。"
    (ReusedThreads.__init__ self)
    (setv self.holding holding
          self.lock (threading.Lock)
          self.calls 0))

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self HoldingFirstCall) (: call "(引数) → 値 の callable") (: args tuple)] :post [(: % Future)]}
    "1 つ目の仕事だけを始めない Future にして持ち、他は ReusedThreads と同じく回すため。"
    (with [_ self.lock]
      (setv self.calls (+ self.calls 1)
            nth self.calls))
    (match nth
      1 (do (setv held (Future))
            (.complete self.holding held)
            held)
      _ (ReusedThreads.submit self call #* args))))


(defclass CountingPool [ReusedThreads]
  "渡された仕事を ReusedThreads と同じく thread で回し、渡された仕事の数を数える Executor(検の殻 — 資源なので値の型ではない)。"

  (deff __init__ [self]  ; defk にできない: 検の殻の資源の初期化
    {:pre [(: self CountingPool)] :post [(: % None)]}
    "数える欄と、他の仕事を回す ReusedThreads の欄を置くため。"
    (ReusedThreads.__init__ self)
    (setv self.lock (threading.Lock)
          self.calls 0))

  (deff submit [self call #* args]  ; defk にできない: Executor の口(concurrent.futures の約束 — VM の外)
    {:pre [(: self CountingPool) (: call "(引数) → 値 の callable") (: args tuple)] :post [(: % Future)]}
    "渡された仕事を数えてから ReusedThreads と同じく回すため。"
    (with [_ self.lock]
      (setv self.calls (+ self.calls 1)))
    (ReusedThreads.submit self call #* args)))


(defk read-on [lease stalls stall ran]
  {:pre [(: lease FakeLease) (: stalls Countdown) (: stall threading.Event) (: ran Queue)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "program"}}
  "偽の接続で読みを 1 つ流すため(driver の thread の上で走る)。流した札を ran へ書く。取り消しの印より前に借りた読みは、止まった事を
   数え、DB の止まり(stall)が解けるまで返らない。"
  (.put ran lease)
  (when (not lease.granted-after-cancel)
    (.tick stalls)
    (.wait stall SETTLE-SECONDS))
  (SqlRows :rows #() :rowcount 0))


(defk spawned-reads [connections pool work count]
  {:pre [(: connections PostgresConnections) (: pool ReusedThreads) (: work "(札) → Program の callable") (: count int)]
   :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "読みの要求を count 件、それぞれ別の task で offloaded-statement に渡すため(答え = task の並び)。"
  (match count
    0 #()
    _ (do (<- task (Spawn (offloaded-statement connections pool DB work)))
          (<- rest (spawned-reads connections pool work (- count 1)))
          (+ #(task) rest))))


(defk ended-by-cancel [task]
  {:pre [(: task "scheduler の Task")] :post [(: % bool)]
   :tags {:context "sql" :role "program"}}
  "取り消した task を待ち、取り消しで終わったかを答えるため。"
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk within [promise what]
  {:pre [(: promise "scheduler の ExternalPromise") (: what str)] :post [(: % "promise の答え")]
   :tags {:context "sql" :role "program"}}
  "外から完了させる promise を SETTLE-SECONDS まで待つため(過ぎたら what を名指して落とす)。"
  (val watchdog (threading.Timer SETTLE-SECONDS (fn [] (.fail promise (AssertionError (.format "{} が {} 秒で揃わない" what SETTLE-SECONDS))))))
  (.start watchdog)
  (try
    (<- answer (Wait promise.future))
    answer
    (finally (.cancel watchdog))))


(deftest test-reads-cancelled-while-waiting-for-a-permit-send-no-statement
  ;; 場面(頭の註): 許可 4 本・読み 12 件・最初の 4 件の文が止まる・10 秒で 12 件とも取り消す・止まりが解けて許可が戻る。
  (<- ready (CreateExternalPromise))
  (<- settled (CreateExternalPromise))
  (val cancelled (threading.Event))
  (val stall (threading.Event))
  (val ran (Queue))
  ;; 揃う = 12 件が借りに来て、最初の 4 件の文が止まった(残り 8 件は許可を待っている)。
  (val arrivals (Countdown (+ READS PERMITS) (fn [] (.complete ready None))))
  (val connections (StalledConnections arrivals cancelled (Countdown READS (fn [] (.complete settled None)))))
  (<- tasks (spawned-reads connections (ReusedThreads) (fn [lease] (read-on lease arrivals stall ran)) READS))
  (<- (within ready "12 件の借りと 4 件の止まった文"))
  ;; t=10: 要求の上限で 12 件とも取り消す。
  (.set cancelled)
  (for [task tasks]
    (<- (Cancel task)))
  (for [task tasks]
    (<- ended (ended-by-cancel task))
    (assert ended task))
  ;; t=30: DB の止まりが解け、走っていた 4 件の文が終わって許可が戻る。12 件の借りが全部返るまで待つ。
  (.set stall)
  (<- (within settled "12 件の返却"))
  (val statements (tuple ran.queue))
  (val before-cancel (len (lfor lease statements :if (not lease.granted-after-cancel) lease)))
  (val after-cancel (len (lfor lease statements :if lease.granted-after-cancel lease)))
  ;; 走り出していた 4 件の文は止めない(直しの外)。
  (assert (= before-cancel PERMITS) statements)
  ;; 許可を待っている間に取り消された 8 件は、許可が取れても文を流さない(直す前は 8)。
  (assert (= after-cancel 0) (.format "許可を待っている間に取り消された要求が流した文 {} 件" after-cancel)))


(deftest test-a-read-cancelled-before-its-work-starts-leases-nothing
  ;; 借りと文の仕事が始まる前に取り消された要求: 仕事は外れ、接続を借りず、文も流れない(借りが無いので返す物も無い)。
  (<- holding (CreateExternalPromise))
  (val cancelled (threading.Event))
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val arrivals (Countdown 1 (fn [] None)))
  (val returned (Countdown 1 (fn [] None)))
  (val connections (StalledConnections arrivals cancelled returned))
  (<- tasks (spawned-reads connections (HoldingFirstCall holding) (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran)) 1))
  (<- held (within holding "借りと文の仕事"))
  (.set cancelled)
  (<- (Cancel (get tasks 0)))
  (<- ended (ended-by-cancel (get tasks 0)))
  (assert ended tasks)
  (assert (.cancelled held) held)
  (assert (= arrivals.left 1) "始まる前に取り消された仕事が接続を借りた")
  (assert (= returned.left 1) "借りていない接続を返した")
  (assert (= (tuple ran.queue) #()) (tuple ran.queue)))


(deftest test-a-statement-is-one-pool-job
  ;; 文 1 つの effect は、借りと文と返却を pool の仕事 1 つで答える(scheduler の thread が外から完了させる promise の往復を文ごとに
  ;; 1 度だけ払う — agora card ki-5c3e73834e73)。借りた接続は答えの後に返っている。
  (<- settled (CreateExternalPromise))
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val connections (StalledConnections (Countdown 1 (fn [] None)) (threading.Event) (Countdown 1 (fn [] (.complete settled None)))))
  (val pool (CountingPool))
  (<- answer (offloaded-statement connections pool DB (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran))))
  (assert (= answer (SqlRows :rows #() :rowcount 0)) answer)
  (<- (within settled "借りた接続の返却"))
  (assert (= pool.calls 1) (.format "文 1 つの pool の仕事が {} 個" pool.calls))
  (assert (= (len (tuple ran.queue)) 1) (tuple ran.queue)))


(deftest test-statements-in-a-row-reuse-one-thread
  ;; 続けて流す文は、前の文の仕事を回した thread を使い回し、文ごとに thread を起こさない(agora card ki-8fc4f9017572 — 起こす待ちが
  ;; 預かり所の普段の CPU の 2 割だった)。postgres-sql-handler の Executor もこの形。
  (val statements 20)
  (val stall (threading.Event))
  (.set stall)
  (val ran (Queue))
  (val connections (StalledConnections (Countdown statements (fn [] None)) (threading.Event) (Countdown statements (fn [] None))))
  (val pool (ReusedThreads))
  (for [_ (range statements)]
    (<- (offloaded-statement connections pool DB (fn [lease] (read-on lease (Countdown 1 (fn [] None)) stall ran)))))
  (assert (= (len (tuple ran.queue)) statements) (tuple ran.queue))
  (assert (= pool.started 1) (.format "文 {} 個に thread を {} 本起こした" statements pool.started))
  (assert (isinstance DRIVER-THREADS ReusedThreads) DRIVER-THREADS))
