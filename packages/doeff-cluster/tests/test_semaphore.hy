;; lock は scheduler の Semaphore の effect で扱う。業務の Program は同じまま、handler の組だけで
;; (1) scheduled だけ(手元)・(2) 1 つの VM の名前の表・(3) cluster の lease(共有の保存 + 時計)を切り替える。
(require doeff-hy.macros [deftest defk <-])
(import doeff [with_handlers])
(import doeff_core_effects.scheduler [Spawn Gather AcquireSemaphore ReleaseSemaphore Semaphore])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.clock [now-epoch-ms])
(import tests.clock_fixtures [clock-at clock-ms])
(import doeff_cluster.shared_handlers [shared-memory])
(import doeff_cluster.semaphore_model [CreateNamedSemaphore ClusterSemaphore LeaseLost
                                            semaphore-key claim renew release])
(import doeff_cluster.semaphore_handlers [named-semaphore-local cluster-semaphore SemaphoreSession])


;; --- 業務の形の Program(scheduler の effect だけを使う) --------------------------------------

(defk critical [sem who log hold]
  {:pre [(: sem Semaphore) (: who str) (: log list) (: hold (| int float))] :post [(: % (type None))]}
  (<- (AcquireSemaphore sem))
  (<- entered int (now-epoch-ms))
  (.append log #("in" who entered))
  (<- (Delay hold))
  (<- left int (now-epoch-ms))
  (.append log #("out" who left))
  (<- (ReleaseSemaphore sem))
  None)

(defk named-user [who log hold permits]
  {:pre [(: who str) (: log list) (: hold (| int float)) (: permits int)] :post [(: % (type None))]}
  ;; 各自が名前で作る(同じ名前 = 同じ lock であってほしい)。
  (<- sem (CreateNamedSemaphore "turn-lock" permits))
  (<- (critical sem who log hold))
  None)


(defk run-all [programs]
  {:pre [(: programs list)] :post [(: % list)]}
  (setv tasks [])
  (for [program programs]
    (<- task (Spawn program))
    (.append tasks task))
  (<- done (Gather #* tasks))
  done)

(defk shared-handle [log hold]
  {:pre [(: log list) (: hold (| int float))] :post [(: % list)]}
  ;; 1 つの handle を 2 つの task で分け合う。
  (<- sem (CreateNamedSemaphore "turn-lock"))
  (<- done (run-all [(critical sem "a" log hold) (critical sem "b" log hold)]))
  done)


(defn intervals [log]
  "log → {who: [(入った時刻 出た時刻) …]}"
  (setv opened {} spans {})
  (for [#(kind who at) log]
    (if (= kind "in")
        (setv (get opened who) at)
        (.append (.setdefault spans who []) #((.pop opened who) at))))
  spans)

(defn max-concurrency [log]
  (setv inside 0 peak 0)
  (for [#(kind _ _) log]
    (+= inside (if (= kind "in") 1 -1))
    (setv peak (max peak inside)))
  peak)


;; --- (1) scheduled だけ -----------------------------------------------------------------------

(deftest test-named-semaphore-is-a-plain-local-semaphore-under-scheduled-alone
  ;; 同じ handle を 2 つの task で分け合えば、scheduled だけで排他が効く(新しい effect は要らない)。
  (setv log [] clock (SimClock))
  (<- (with_handlers [(sim-time-handler :clock clock)]
        (shared-handle log 2)))
  (assert (= (max-concurrency log) 1))
  (assert (= (clock-ms clock) 4000)))


(deftest test-separately-created-names-do-not-exclude-without-a-name-table
  ;; scheduled だけの下では、名前で別々に作った semaphore は別物(名前を見る handler が無いので)。
  (setv log [] clock (SimClock))
  (<- (with_handlers [(sim-time-handler :clock clock)]
        (run-all [(named-user "a" log 2 1) (named-user "b" log 2 1)])))
  (assert (= (max-concurrency log) 2)))


;; --- (2) 1 つの VM の名前の表 ------------------------------------------------------------------

(deftest test-name-table-makes-the-same-name-the-same-local-semaphore
  (setv log [] clock (SimClock))
  (<- (with_handlers [(sim-time-handler :clock clock) (named-semaphore-local {})]
        (run-all [(named-user "a" log 2 1) (named-user "b" log 2 1)])))
  (assert (= (max-concurrency log) 1))
  (assert (= (clock-ms clock) 4000)))


;; --- (3) cluster(共有の保存の lease) ----------------------------------------------------------

(defn on-worker [session program]
  "1 つの worker に見立てる: その worker の cluster-semaphore だけを被せる(保存と時計は共有)。"
  (with_handlers [(cluster-semaphore session)] program))


(deftest test-cluster-semaphore-excludes-across-workers-and-renews-past-the-ttl
  ;; 2 つの worker が同じ名前を取り合う。持つ時間(20 秒)は TTL(15 秒)より長いので、延長が効かなければ b が割り込む。
  (setv log [] clock (SimClock) store {})
  (setv sa (SemaphoreSession "worker-a" :ttl-seconds 15.0 :poll-seconds 0.5)
        sb (SemaphoreSession "worker-b" :ttl-seconds 15.0 :poll-seconds 0.5))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store)]
        (run-all [(on-worker sa (named-user "a" log 20 1)) (on-worker sb (named-user "b" log 20 1))])))
  (assert (= (max-concurrency log) 1))
  (setv spans (intervals log))
  (setv #(a-in a-out) (get spans "a" 0) #(b-in b-out) (get spans "b" 0))
  (assert (= #(a-in a-out) #(0 20000)))
  ;; b は a が返してから待ちの 1 周期(0.5 秒)以内に入る。
  (assert (<= 20000 b-in 20500))
  ;; 返した後の行は担い手なし。
  (assert (= (get store (semaphore-key "turn-lock") "holders") {})))


(deftest test-cluster-semaphore-takes-over-an-expired-lease-of-a-dead-worker
  ;; 死んだ worker の lease(期限 15 秒)が残っている。期限までは待ち、切れたら取る。
  (setv log [] clock (SimClock) store {(semaphore-key "turn-lock") {"permits" 1 "holders" {"dead/1" 15000}}})
  (setv sb (SemaphoreSession "worker-b" :ttl-seconds 15.0 :poll-seconds 0.5))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store)]
        (on-worker sb (named-user "b" log 1 1))))
  (setv #(b-in b-out) (get (intervals log) "b" 0))
  (assert (<= 15000 b-in 15500)))


(deftest test-cluster-semaphore-reports-a-lost-lease-at-release
  ;; 持っている間に行から token が消えた(期限切れの後に他へ移った形)。返す時に LeaseLost で知らせる。
  (setv log [] clock (SimClock) store {} key (semaphore-key "turn-lock"))
  (setv sa (SemaphoreSession "worker-a" :ttl-seconds 15.0 :poll-seconds 0.5))
  (defk steal []
    {:pre [] :post [(: % (type None))]}
    (<- (Delay 1))
    (setv (get store key) {"permits" 1 "holders" {"thief/1" 999999}})
    None)
  (defk guarded []
    {:pre [] :post [(: % str)]}
    (try
      (<- (named-user "a" log 10 1))
      "no-error"
      (except [error LeaseLost] "lost")))
  (<- outcome (with_handlers [(sim-time-handler :clock clock) (shared-memory store)]
                (run-all [(on-worker sa (guarded)) (steal)])))
  (assert (= (get outcome 0) "lost"))
  ;; 他の担い手の行はそのまま(失った側は消さない)。
  (assert (= (get store key "holders") {"thief/1" 999999})))


(deftest test-cluster-semaphore-with-two-permits-admits-two-at-a-time
  (setv log [] clock (SimClock) store {})
  (setv sessions (lfor w ["a" "b" "c"] (SemaphoreSession (+ "worker-" w) :ttl-seconds 15.0 :poll-seconds 0.5)))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store)]
        (run-all (lfor #(w s) (zip ["a" "b" "c"] sessions) (on-worker s (named-user w log 5 2))))))
  (assert (= (max-concurrency log) 2))
  (assert (= (len (intervals log)) 3)))


;; --- 純粋な判断 --------------------------------------------------------------------------------

(defn test-claim-renew-release-are-pure-row-transitions []
  (setv row (claim None 1 "a/1" 0 15000))
  (assert (= row {"permits" 1 "holders" {"a/1" 15000}}))
  ;; 満ちている間は取れない・期限が切れたら取れる(切れた担い手は落ちる)。
  (assert (is (claim row 1 "b/1" 10000 15000) None))
  (assert (= (claim row 1 "b/1" 15000 15000) {"permits" 1 "holders" {"b/1" 30000}}))
  ;; 同じ名前の permits が食い違えば断る。
  (try (claim row 2 "b/1" 0 15000) (assert False) (except [ValueError] None))
  ;; 延長: 行に在る間だけ。消えていたら None(= 失った)。
  (assert (= (renew row "a/1" 20000 15000) {"permits" 1 "holders" {"a/1" 35000}}))
  (assert (is (renew row "b/1" 0 15000) None))
  ;; 返す: 在れば落として True・無ければ行はそのままで False。
  (assert (= (release row "a/1" 0) #({"permits" 1 "holders" {}} True)))
  (assert (= (release row "b/1" 0) #(row False))))


(defn test-named-semaphore-refuses-an-empty-or-slashed-name []
  (for [bad ["" "a/b"]]
    (try (CreateNamedSemaphore bad) (assert False) (except [ValueError] None)))
  (assert (= (. (CreateNamedSemaphore "x" 3) permits) 3))
  (assert (isinstance (ClusterSemaphore "x" 1) Semaphore)))


;; --- 書きの柵(lease-fence)— 旧版が lease を失った後は書けない ---------------------------------------

(import dataclasses [dataclass])
(import doeff [EffectBase])
(require doeff-hy.macros [defhandler])
(import doeff_cluster.shared_model [ReadShared WriteShared])
(import doeff_cluster.semaphore_model [HeldLease WriteFenced LeaseOp fence-verdict])
(import doeff_cluster.semaphore_handlers [lease-fence])

(defclass [(dataclass :frozen True)] FakeWrite [EffectBase]
  "柵の向こうの書き(本番では業務の書き先への書き)。"
  (#^ str who))

(defhandler written-log [#^ list log]
  ;; 柵を通った書きだけが届く「書き先」。届いた時刻と書き手を記録する。
  (FakeWrite [who]
    (<- now int (now-epoch-ms))
    (.append log #(who now))
    (resume True)))

(defhandler cut-off-at [clock #^ int at]
  ;; この worker から共有の保存へ届かなくなる(tailnet の途絶・coordinator の停止)。書き先への書きの道は生きている。
  (ReadShared [prefix] :when (>= (clock-ms clock) at) (raise (ConnectionError "保存へ届かない")))
  (WriteShared [key value expect ttl-seconds] :when (>= (clock-ms clock) at) (raise (ConnectionError "保存へ届かない")))
  (LeaseOp [name op token permits ttl-ms] :when (>= (clock-ms clock) at) (raise (ConnectionError "保存へ届かない"))))

(defk lease-writer [who attempts every until [acquire True]]
  {:pre [(: who str) (: attempts list) (: every (| int float)) (: until int) (: acquire bool)] :post [(: % (type None))]}
  ;; lease を取り(返さない = kill された process と同じ)、every 秒ごとに書こうとする。断られても止まらずに試し続ける。
  (when acquire
    (<- sem (CreateNamedSemaphore "writer-a"))
    (<- (AcquireSemaphore sem)))
  (while True
    (<- now int (now-epoch-ms))
    (when (>= now until) (return None))
    (try
      (<- (FakeWrite who))
      (.append attempts #(who now "ok"))
      (except [WriteFenced]
        (.append attempts #(who now "fenced"))))
    (<- (Delay every))))

(defn fenced-worker [session program [cut None] [clock None]]
  "1 つの worker: (途絶) → cluster-semaphore → 書きの柵(一番内側)。"
  (setv inner [(cluster-semaphore session) (lease-fence "writer-a" #(FakeWrite) 2000)])
  (with_handlers (if (is cut None) inner (+ [(cut-off-at clock cut)] inner)) program))

(defn times-of [attempts who outcome]
  (lfor #(w at o) attempts :if (and (= w who) (= o outcome)) at))


(deftest test-fence-stops-an-old-holder-cut-off-from-the-store-before-the-new-holder-writes
  ;; 旧版 A が lease を持って書いている。3 秒目に A から保存へ届かなくなる(延長できない)が、書き先への書きの道は生きている。
  ;; 新版 B は 0 秒から lease を待つ。A は最後に書けた期限(15 秒)の 2 秒前で書けなくなり、B は期限の後に lease を取って書く。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0 :poll-seconds 0.5)
        sb (SemaphoreSession "new" :ttl-seconds 15.0 :poll-seconds 0.5))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store) (written-log written)]
        (run-all [(fenced-worker sa (lease-writer "a" attempts 1 40000) :cut 3000 :clock clock)
                  (fenced-worker sb (lease-writer "b" attempts 1 40000))])))
  (setv a-ok (times-of attempts "a" "ok") a-fenced (times-of attempts "a" "fenced") b-ok (times-of attempts "b" "ok"))
  ;; A は期限 15000 の余裕 2000 の手前まで書け、そこから先は 1 回も書けない(40 秒まで試し続けた)。
  (assert (= (max a-ok) 12000) a-ok)
  (assert (= (min a-fenced) 13000) a-fenced)
  (assert (>= (max a-fenced) 39000))
  ;; B は A の期限の後に lease を取った。
  (assert (<= 15000 (min b-ok) 15500) b-ok)
  ;; 「書き先」に届いた書きは A の塊と B の塊に分かれ、重ならない(A が lease を失った後の A の書きは 0 件)。
  (setv a-landed (lfor #(w at) written :if (= w "a") at) b-landed (lfor #(w at) written :if (= w "b") at))
  (assert (< (max a-landed) (min b-landed)))
  (assert (= (len a-landed) (len a-ok)))
  (assert (not (any (gfor at a-landed (>= at 13000))))))


(deftest test-fence-stops-an-old-holder-as-soon-as-its-renewal-sees-the-lease-taken
  ;; 期限より前でも、延長の係が「行から自分の token が消えた」を見た時点で失ったと分かり、柵が締まる。
  (setv clock (SimClock) store {} attempts [] written [] key (semaphore-key "writer-a"))
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0 :poll-seconds 0.5))
  (defk steal []
    {:pre [] :post [(: % (type None))]}
    (<- (Delay 4))
    (setv (get store key) {"permits" 1 "holders" {"thief/1" 999999999}})
    None)
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store) (written-log written)]
        (run-all [(fenced-worker sa (lease-writer "a" attempts 1 12000)) (steal)])))
  ;; 延長は TTL の 1/3 = 5 秒目。そこで失ったと分かり、5 秒目以降の書きは断られる。
  (assert (= (max (times-of attempts "a" "ok")) 4000) attempts)
  (assert (= (min (times-of attempts "a" "fenced")) 5000) attempts)
  (assert (= (lfor #(_ at) written at) [0 1000 2000 3000 4000])))


(deftest test-fence-refuses-writes-before-the-lease-is-taken
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "w" :ttl-seconds 15.0 :poll-seconds 0.5))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store) (written-log written)]
        (fenced-worker sa (lease-writer "a" attempts 1 3000 :acquire False))))
  (assert (= (times-of attempts "a" "fenced") [0 1000 2000]))
  (assert (= written [])))


(defhandler cut-between [clock #^ int start #^ int end]
  ;; 仮想の時計が start〜end の間、保存(盤・lease)へ届かない。
  (ReadShared [prefix] :when (<= start (clock-ms clock) end) (raise (ConnectionError "保存へ届かない")))
  (WriteShared [key value expect ttl-seconds] :when (<= start (clock-ms clock) end) (raise (ConnectionError "保存へ届かない")))
  (LeaseOp [name op token permits ttl-ms] :when (<= start (clock-ms clock) end) (raise (ConnectionError "保存へ届かない"))))


(deftest test-renewal-survives-a-short-cut-and-keeps-the-lease
  ;; 保存へ届かない時間が期限より短ければ、延長の係は試し続けて戻った後に延ばす(係が例外で消えない)。
  ;; 途絶の間(期限の余裕の内側)は書けて、戻った後も書ける。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "w" :ttl-seconds 15.0 :poll-seconds 0.5))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store) (written-log written)]
        (with_handlers [(cut-between clock 4000 9000) (cluster-semaphore sa) (lease-fence "writer-a" #(FakeWrite) 2000)]
          (lease-writer "a" attempts 1 40000))))
  (assert (= (times-of attempts "a" "fenced") []) attempts)
  (assert (= (len (times-of attempts "a" "ok")) 40)))


(defn test-fence-verdict-is-a-pure-check-of-the-hold-and-the-clock []
  (assert (is (fence-verdict {"token" "t" "expiresMs" 15000} 12999 2000) None))
  (assert (is-not (fence-verdict {"token" "t" "expiresMs" 15000} 13000 2000) None))
  (assert (is-not (fence-verdict None 0 2000) None)))


;; --- lease の立場(待機の process の書きを捨てる — 2026-09-24)---------------------------------------------

(import doeff_cluster.semaphore_model [LeaseStanding STANDBY HELD LOST])

(defk standing-story [log]
  {:pre [(: log list)] :post [(: % (type None))]}
  (<- a (LeaseStanding "turn-lock"))
  (<- sem (CreateNamedSemaphore "turn-lock" 1))
  (<- (AcquireSemaphore sem))
  (<- b (LeaseStanding "turn-lock"))
  (<- (ReleaseSemaphore sem))
  (<- c (LeaseStanding "turn-lock"))
  (.extend log [a b c])
  None)

(deftest test-lease-standing-is-standby-until-held-and-lost-after
  (setv log [] store {} clock (clock-at 1000))
  (<- (with_handlers [(sim-time-handler :clock clock) (shared-memory store) (cluster-semaphore (SemaphoreSession "w"))]
        (standing-story log)))
  (assert (= log [STANDBY HELD LOST]) log))
