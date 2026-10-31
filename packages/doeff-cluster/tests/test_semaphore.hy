;; lock は scheduler の Semaphore の effect で扱う。業務の Program は同じまま、handler の組だけで
;; (1) scheduled だけ(手元)・(2) 1 つの VM の名前の表・(3) cluster の lease(共有の保存 + 時計)を切り替える。
;; 同じ Program を組ごとに回す契約テストは deftest の :interpreters で handler を差し替える(組み立ては coordinator_contract_handlers.hy —
;; scheduled・named-semaphore-local・cluster-semaphore(fake の盤 board_fake.hy の上)・cluster-semaphore-http(本物の coordinator の
;; /leases の上))。契約:
;;   * 1 つの handle を分け合う task は 1 つずつ入り、待つ task は先の task が返してから入る(cluster は空き待ちの 1 周期の内)
;;   * permits = 2 の handle は同時に 2 つまで入れ、3 つ目は先の 1 つが返してから入る
;;   * 名前を見る組(名前の表・cluster)では、別々に名前で作った同じ名前は同じ lock
;; 片方の組だけの性質は素の deftest のまま: scheduled だけでは別々に作った同じ名前は別物・cluster の worker を越えた排他と延長・
;; 死んだ worker の lease の期限の後の引き取り・失った lease の知らせ・書きの柵・lease の立場。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [with_handlers Program run])
(import doeff_core_effects [Spawn Gather Wait Cancel TaskCancelledError AcquireSemaphore ReleaseSemaphore Semaphore Task])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import tests.clock_fixtures [clock-at clock-ms])
(import tests.board_fake [board-handlers])
(import doeff_cluster [CreateNamedSemaphore ClusterSemaphore LeaseLost])
(import doeff_cluster [semaphore-key]
        doeff_cluster._shared.core.lease_rules [claim renew release])
(import doeff_cluster [named-semaphore-local cluster-semaphore SemaphoreSession])


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
  (<- sem Semaphore (CreateNamedSemaphore "turn-lock" permits))
  (<- (critical sem who log hold))
  None)


(defk run-all [programs]
  {:pre [(: programs list)] :post [(: % list)]}
  (setv tasks [])
  (for [program programs]
    (<- task Task (Spawn program))
    (.append tasks task))
  (<- done list (Gather #* tasks))
  done)

(defk shared-handle [log hold]
  {:pre [(: log list) (: hold (| int float))] :post [(: % list)]}
  ;; 1 つの handle を 2 つの task で分け合う。
  (<- sem Semaphore (CreateNamedSemaphore "turn-lock"))
  (<- done (run-all [(critical sem "a" log hold) (critical sem "b" log hold)]))
  done)


(defk intervals [log]
  {:pre [(: log list)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log → {who: [(入った時刻 出た時刻) …]}"
  (val opened {})
  (val spans {})
  (for [#(kind who at) log]
    (if (= kind "in")
        (setv (get opened who) at)
        (.append (.setdefault spans who []) #((.pop opened who) at))))
  spans)

(defk max-concurrency [log]
  {:pre [(: log list)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の入った・出たの並びから、同時に中に居た数の最大を数えるため。"
  (var inside 0)
  (var peak 0)
  (for [#(kind _ _) log]
    (:= inside (+ inside (if (= kind "in") 1 -1)))
    (:= peak (max peak inside)))
  peak)


;; --- 契約(3 つの組で同じ Program)---------------------------------------------------------------------

;; 待つ task が空きを見つけるまでの遅れの上限(ミリ秒): cluster-semaphore は空き待ちを poll の間隔(0.5 秒)で問い直す(先着順の保証も無い)。
;; scheduled と名前の表は返した時刻にすぐ入る。
(val WAKE-MS 500)
(val HOLD-SECONDS 2)
(val HOLD-MS (* 1000 HOLD-SECONDS))


(defk spans-from [log start]
  {:pre [(: log list) (: start int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "log の入った・出た時刻を、Program の始まり start からのミリ秒に直した {who: [#(入 出) …]} にするため(組ごとに時計の起点が違う)。"
  (<- by-who dict (intervals log))
  (dfor #(who spans) (.items by-who)
        who (lfor #(entered left) spans #((- entered start) (- left start)))))


(deftest test-tasks-sharing-one-handle-enter-one-at-a-time
  {:interpreters ["scheduled" "named-semaphore-local" "cluster-semaphore" "cluster-semaphore-http"]}
  ;; 同じ handle を 2 つの task で分け合えば、どの組でも排他が効く(scheduled だけでも — 新しい effect は要らない)。
  (val log [])
  (<- start int (now-epoch-ms))
  (<- (shared-handle log HOLD-SECONDS))
  (<- spans dict (spans-from log start))
  (assert (= (! (max-concurrency log)) 1) log)
  (assert (= (get spans "a") [#(0 HOLD-MS)]) spans)
  (val b (get spans "b" 0))
  (assert (<= HOLD-MS (get b 0) (+ HOLD-MS WAKE-MS)) spans)
  (assert (= (- (get b 1) (get b 0)) HOLD-MS) spans))


(deftest test-a-handle-with-two-permits-admits-two-at-a-time
  {:interpreters ["scheduled" "named-semaphore-local" "cluster-semaphore" "cluster-semaphore-http"]}
  (val log [])
  (<- start int (now-epoch-ms))
  (<- sem Semaphore (CreateNamedSemaphore "turn-lock" 2))
  (<- (run-all (lfor who ["a" "b" "c"] (critical sem who log HOLD-SECONDS))))
  (<- spans dict (spans-from log start))
  (assert (= (! (max-concurrency log)) 2) log)
  (assert (= (sorted spans) ["a" "b" "c"]) spans)
  ;; 3 つ目は、先の 2 つが返してから(空き待ちの 1 周期の内に)入る。
  (val third (max (lfor spans-of (.values spans) (get spans-of 0)) :key (fn [span] (get span 0))))
  (assert (<= HOLD-MS (get third 0) (+ HOLD-MS WAKE-MS)) spans))


(deftest test-the-same-name-is-the-same-lock-where-a-handler-reads-names
  {:interpreters ["named-semaphore-local" "cluster-semaphore" "cluster-semaphore-http"]}
  (val log [])
  (<- (run-all [(named-user "a" log HOLD-SECONDS 1) (named-user "b" log HOLD-SECONDS 1)]))
  (assert (= (! (max-concurrency log)) 1) log))


;; --- (1) scheduled だけ -----------------------------------------------------------------------

(deftest test-separately-created-names-do-not-exclude-without-a-name-table
  ;; scheduled だけの下では、名前で別々に作った semaphore は別物(名前を見る handler が無いので — 名前を見る組の契約の反例)。
  (setv log [] clock (SimClock))
  (<- (with_handlers [(sim-time-handler :clock clock)]
        (run-all [(named-user "a" log 2 1) (named-user "b" log 2 1)])))
  (assert (= (! (max-concurrency log)) 2)))


;; --- (3) cluster(共有の保存の lease) ----------------------------------------------------------

(defk on-worker [session program]
  {:pre [(: session SemaphoreSession) (: program Program)] :post [(: % "program の答え(型は program ごと)")]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "1 つの worker に見立てる: その worker の cluster-semaphore だけを被せて program を走らせる(保存と時計は共有)。"
  (<- answer (with_handlers [(cluster-semaphore session)] program))
  answer)


(deftest test-cluster-semaphore-excludes-across-workers-and-renews-past-the-ttl
  ;; 2 つの worker が同じ名前を取り合う。持つ時間(20 秒)は TTL(15 秒)より長いので、延長が効かなければ b が割り込む。
  (setv log [] clock (SimClock) store {})
  (setv sa (SemaphoreSession "worker-a" :ttl-seconds 15.0)
        sb (SemaphoreSession "worker-b" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
        (run-all [(on-worker sa (named-user "a" log 20 1)) (on-worker sb (named-user "b" log 20 1))])))
  (assert (= (! (max-concurrency log)) 1))
  (<- spans (intervals log))
  (setv #(a-in a-out) (get spans "a" 0) #(b-in b-out) (get spans "b" 0))
  (assert (= #(a-in a-out) #(0 20000)))
  ;; b は a が返してから待ちの 1 周期(0.5 秒)以内に入る。
  (assert (<= 20000 b-in 20500))
  ;; 返した後の行は担い手なし。
  (assert (= (get store (semaphore-key "turn-lock") "holders") {})))


(deftest test-cluster-semaphore-takes-over-an-expired-lease-of-a-dead-worker
  ;; 死んだ worker の lease(期限 15 秒)が残っている。期限までは待ち、切れたら取る。
  (setv log [] clock (SimClock) store {(semaphore-key "turn-lock") {"permits" 1 "holders" {"dead/1" 15000}}})
  (setv sb (SemaphoreSession "worker-b" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
        (on-worker sb (named-user "b" log 1 1))))
  (setv #(b-in b-out) (get (! (intervals log)) "b" 0))
  (assert (<= 15000 b-in 15500)))


(deftest test-cluster-semaphore-reports-a-lost-lease-at-release
  ;; 持っている間に行から token が消えた(期限切れの後に他へ移った形)。返す時に LeaseLost で知らせる。
  (setv log [] clock (SimClock) store {} key (semaphore-key "turn-lock"))
  (setv sa (SemaphoreSession "worker-a" :ttl-seconds 15.0))
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
  (<- outcome list (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
                (run-all [(on-worker sa (guarded)) (steal)])))
  (assert (= (get outcome 0) "lost"))
  ;; 他の担い手の行はそのまま(失った側は消さない)。
  (assert (= (get store key "holders") {"thief/1" 999999})))


;; --- 純粋な判断 --------------------------------------------------------------------------------

(deftest test-claim-renew-release-are-pure-row-transitions
  (val row (claim None 1 "a/1" 0 15000))
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


(deftest test-named-semaphore-refuses-an-empty-or-slashed-name
  (for [bad ["" "a/b"]]
    (try (CreateNamedSemaphore bad) (assert False) (except [ValueError] None)))
  (assert (= (. (CreateNamedSemaphore "x" 3) permits) 3))
  (assert (isinstance (ClusterSemaphore "x" 1) Semaphore)))


;; --- 書きの柵(leases-fence)— 旧版が lease を失った後は書けない ---------------------------------------

(import dataclasses [dataclass])
(import doeff [EffectBase])
(require doeff-hy.macros [defhandler])
(import doeff_cluster [ReadShared WriteShared])
(import doeff_cluster [HeldLease WriteFenced LeaseOp AwaitLeaseFree LeaseAnswer])
(import doeff_cluster._shared.core.lease_rules [fence-verdict])
(import doeff_cluster [leases-fence])

(defclass [(dataclass :frozen True)] FakeWrite [EffectBase]
  "柵の向こうの書き(本番では業務の書き先への書き)。"
  (#^ str who))

(defk writer-a-lease [write]
  {:pre [(: write FakeWrite)] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "書き手が 1 人の筋書きで、どの書きも lease writer-a だけで守るため(柵に渡す関数)。"
  #("writer-a"))

(defhandler written-log [#^ list log]
  ;; 柵を通った書きだけが届く「書き先」。届いた時刻と書き手を記録する。
  (FakeWrite [who]
    (<- now int (now-epoch-ms))
    (.append log #(who now))
    (resume True)))

(defhandler cut-off-at [#^ SimClock clock #^ int at]
  ;; この worker から共有の保存へ届かなくなる(tailnet の途絶・coordinator の停止)。書き先への書きの道は生きている。
  (ReadShared [prefix] :when (>= (run (clock-ms clock)) at) (raise (ConnectionError "保存へ届かない")))
  (WriteShared [key value expect ttl-seconds] :when (>= (run (clock-ms clock)) at) (raise (ConnectionError "保存へ届かない")))
  (LeaseOp [name op token permits ttl-ms] :when (>= (run (clock-ms clock)) at) (raise (ConnectionError "保存へ届かない"))))

(defk lease-writer [who attempts every until [acquire True]]
  {:pre [(: who str) (: attempts list) (: every (| int float)) (: until int) (: acquire bool)] :post [(: % (type None))]}
  ;; lease を取り(返さない = kill された process と同じ)、every 秒ごとに書こうとする。断られても止まらずに試し続ける。
  (when acquire
    (<- sem Semaphore (CreateNamedSemaphore "writer-a"))
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

(defk fenced-worker [session program [cut None] [clock None]]
  {:pre [(: session SemaphoreSession) (: program Program) (: cut (| int None)) (: clock (| SimClock None))]
   :post [(: % "program の答え(型は program ごと)")] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 つの worker: (途絶) → cluster-semaphore → 書きの柵(一番内側)の下で program を走らせる。"
  (val inner [(cluster-semaphore session) (leases-fence writer-a-lease #(FakeWrite) 2000 0)])
  (val handlers (cond
                  (is cut None) inner
                  (is clock None) (raise (ValueError "途絶(cut)には仮想の時計(clock)が要る"))
                  True (+ [(cut-off-at clock cut)] inner)))
  (<- answer (with_handlers handlers program))
  answer)

(defk times-of [attempts who outcome]
  {:pre [(: attempts list) (: who str) (: outcome str)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "書きの試しの記録から、書き手 who の結果 outcome の時刻だけを並べるため。"
  (lfor #(w at o) attempts :if (and (= w who) (= o outcome)) at))


(deftest test-fence-stops-an-old-holder-cut-off-from-the-store-before-the-new-holder-writes
  ;; 旧版 A が lease を持って書いている。3 秒目に A から保存へ届かなくなる(延長できない)が、書き先への書きの道は生きている。
  ;; 新版 B は 0 秒から lease を待つ。A は最後に書けた期限(15 秒)の 2 秒前で書けなくなり、B は期限の後に lease を取って書く。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0)
        sb (SemaphoreSession "new" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (run-all [(fenced-worker sa (lease-writer "a" attempts 1 40000) :cut 3000 :clock clock)
                  (fenced-worker sb (lease-writer "b" attempts 1 40000))])))
  (setv a-ok (! (times-of attempts "a" "ok")) a-fenced (! (times-of attempts "a" "fenced")) b-ok (! (times-of attempts "b" "ok")))
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
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0))
  (defk steal []
    {:pre [] :post [(: % (type None))]}
    (<- (Delay 4))
    (setv (get store key) {"permits" 1 "holders" {"thief/1" 999999999}})
    None)
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (run-all [(fenced-worker sa (lease-writer "a" attempts 1 12000)) (steal)])))
  ;; 延長は TTL の 1/3 = 5 秒目。そこで失ったと分かり、5 秒目以降の書きは断られる。
  (assert (= (max (! (times-of attempts "a" "ok"))) 4000) attempts)
  (assert (= (min (! (times-of attempts "a" "fenced"))) 5000) attempts)
  (assert (= (lfor #(_ at) written at) [0 1000 2000 3000 4000])))


(deftest test-fence-refuses-writes-before-the-lease-is-taken
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "w" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (fenced-worker sa (lease-writer "a" attempts 1 3000 :acquire False))))
  (assert (= (! (times-of attempts "a" "fenced")) [0 1000 2000]))
  (assert (= written [])))


;; --- 書きの柵が守る lease の名を書きの effect から決める(#4072 の D-1 — 区画ごとの lease)-----------------------------------

(defclass [(dataclass :frozen True)] KeyedWrite [EffectBase]
  "区画 key の lease だけで守る書き。"
  (#^ str key))

(defclass [(dataclass :frozen True)] GatedWrite [EffectBase]
  "入口の lease と区画 key の lease の両方で守る書き。"
  (#^ str key))

(defhandler keyed-store
  ;; 柵を通った書きが届く「書き先」(書けたと答えるだけ)。
  (KeyedWrite [key]
    (resume True))
  (GatedWrite [key]
    (resume True)))

(defk leases-of-write [write]
  {:pre [(: write (| KeyedWrite GatedWrite))] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "書きの effect が名指す lease の名の組を答えるため(柵に渡す関数): 区画の書き = その区画の lease・入口つきの書き = 入口の lease と
   その区画の lease の両方。"
  (match write
    (KeyedWrite) #((+ "key-" write.key))
    (GatedWrite) #("gate" (+ "key-" write.key))))

(defk outcome-of [write]
  {:pre [(: write (| KeyedWrite GatedWrite))] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "書きを 1 つ試し、柵が通したか(ok)断ったか(fenced)を答えるため。"
  (try
    (<- written bool write)
    "ok"
    (except [WriteFenced]
      "fenced")))

(defk writes-under-held-leases []
  {:pre [] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "区画 a の lease だけを持つ間に 3 つの書きを試し、入口の lease も取った後に入口つきの書きをもう 1 度試すため。"
  (<- key-a Semaphore (CreateNamedSemaphore "key-a"))
  (<- (AcquireSemaphore key-a))
  (<- a-written str (outcome-of (KeyedWrite "a")))
  (<- b-written str (outcome-of (KeyedWrite "b")))
  (<- gated-without-gate str (outcome-of (GatedWrite "a")))
  (<- gate Semaphore (CreateNamedSemaphore "gate"))
  (<- (AcquireSemaphore gate))
  (<- gated-with-both str (outcome-of (GatedWrite "a")))
  #(a-written b-written gated-without-gate gated-with-both))


(deftest test-fence-names-the-leases-from-the-write-effect
  ;; 柵は書きの effect が名指す lease の組の全部を持つ間だけ書きを通す(#4072 の D-1)。process は初め区画 a の lease だけを持つ:
  ;; 区画 a の書きは通り、区画 b の書きは断られ(b の lease を持たない)、区画 a の入口つきの書きも断られる(入口の lease を持たない)。
  ;; 入口の lease も取った後は、入口つきの書きが通る。失敗ケース: 柵が固定の名 1 つしか見ないと、区画ごとの書きを分けて守れない。
  (val clock (SimClock))
  (val store {})
  (val session (SemaphoreSession "w" :ttl-seconds 15.0))
  (<- outcomes (get tuple #(str ...))
      (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) keyed-store]
        (with_handlers [(cluster-semaphore session) (leases-fence leases-of-write #(KeyedWrite GatedWrite) 2000 0)]
          (writes-under-held-leases))))
  (assert (= outcomes #("ok" "fenced" "fenced" "ok")) outcomes))


(defhandler cut-between [#^ SimClock clock #^ int start #^ int end]
  ;; 仮想の時計が start〜end の間、保存(盤・lease)へ届かない。空きの待ち(AwaitLeaseFree — 本番は coordinator の GET /watch の長い待ち)は、
  ;; 途絶の間に待ち始めるか、途絶の間に答えが来ると届かない(本番は待ちの送り直しが期限を越えて RouteUnreachable)。
  (ReadShared [prefix] :when (<= start (run (clock-ms clock)) end) (raise (ConnectionError "保存へ届かない")))
  (WriteShared [key value expect ttl-seconds] :when (<= start (run (clock-ms clock)) end) (raise (ConnectionError "保存へ届かない")))
  (LeaseOp [name op token permits ttl-ms] :when (<= start (run (clock-ms clock)) end) (raise (ConnectionError "保存へ届かない")))
  (AwaitLeaseFree [name]
    (when (<= start (run (clock-ms clock)) end) (raise (ConnectionError "保存へ届かない")))
    (<- seen bool effect)
    (when (<= start (run (clock-ms clock)) end) (raise (ConnectionError "保存へ届かない")))
    (resume seen)))


(deftest test-renewal-survives-a-short-cut-and-keeps-the-lease
  ;; 保存へ届かない時間が期限より短ければ、延長の係は試し続けて戻った後に延ばす(係が例外で消えない)。
  ;; 途絶の間(期限の余裕の内側)は書けて、戻った後も書ける。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "w" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (with_handlers [(cut-between clock 4000 9000) (cluster-semaphore sa) (leases-fence writer-a-lease #(FakeWrite) 2000 0)]
          (lease-writer "a" attempts 1 40000))))
  (assert (= (! (times-of attempts "a" "fenced")) []) attempts)
  (assert (= (len (! (times-of attempts "a" "ok"))) 40)))


(deftest test-a-patient-fence-holds-writes-through-a-cut-longer-than-the-margin
  ;; 4〜30 秒に保存へ届かない(coordinator の作り直し — 柵の余裕を越える長さ)。延ばす係は試し直し続け、届いた後に延ばしが通る(盤は
  ;; 持ち手の token を残している)。待つ柵は、期限が近い間の書きを外へ出さずに待ち、延ばしが通った後に通す — 断られた書きは 0。
  ;; 待っている間に外へ着いた書きは 0(13 秒 = 最後に延ばせた期限 15 秒 − 余裕 2 秒 の後、延ばしが通るまで)。直す前は 13〜30 秒の書きを断った。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "w" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (with_handlers [(cut-between clock 4000 30000) (cluster-semaphore sa) (leases-fence writer-a-lease #(FakeWrite) 2000 60000)]
          (lease-writer "a" attempts 1 40000))))
  (assert (= (! (times-of attempts "a" "fenced")) []) attempts)
  (setv landed (lfor #(w at) written at))
  (assert (not (any (gfor at landed (< 13000 at 30000)))) landed)
  (assert (any (gfor at landed (>= at 30000))) landed))


(deftest test-a-patient-fence-refuses-after-the-lease-is-taken-and-lands-nothing-late
  ;; 旧 A は 3 秒目から保存へ届かないまま(延ばせず、失ったとも分からない)。新 B は期限の後に lease を取って書く。待つ柵の A は、待った
  ;; 末(待ちの上限 20 秒)に断り、A の書きは 13 秒より後に 1 本も着かない — 待つ事は、断る判断が通さない書きを通さない。試しの時刻は
  ;; 書く前に読むので、13 秒の書きが 33 秒まで待って断られ、次の試しは 34 秒。直す前は 13 秒から毎秒断った。
  (setv clock (SimClock) store {} attempts [] written [])
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0)
        sb (SemaphoreSession "new" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (written-log written)]
        (run-all [(with_handlers [(cut-off-at clock 3000) (cluster-semaphore sa) (leases-fence writer-a-lease #(FakeWrite) 2000 20000)]
                    (lease-writer "a" attempts 1 60000))
                  (fenced-worker sb (lease-writer "b" attempts 1 60000))])))
  (setv a-fenced (! (times-of attempts "a" "fenced")) b-ok (! (times-of attempts "b" "ok")))
  (assert (and (>= (len a-fenced) 2) (= (get a-fenced 0) 13000) (>= (get a-fenced 1) 34000)) a-fenced)
  (assert (<= 15000 (min b-ok) 15500) b-ok)
  (assert (not (any (gfor #(w at) written (and (= w "a") (> at 13000))))) written))


(defk release-in-a-cut [log]
  {:pre [(: log list)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "lease writer-a を取り、5 秒後に返して、返し終えた時刻を log に残すため(返す時に保存へ届かない筋書きの旧い持ち手)。"
  (<- sem Semaphore (CreateNamedSemaphore "writer-a"))
  (<- (AcquireSemaphore sem))
  (<- (Delay 5))
  (<- (ReleaseSemaphore sem))
  (<- at int (now-epoch-ms))
  (.append log #("released" at))
  None)


(defk take-after-a-second [log]
  {:pre [(: log list)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 秒後から lease writer-a を待って取り、取れた時刻を log に残して返すため(次の持ち手)。"
  (<- (Delay 1))
  (<- sem Semaphore (CreateNamedSemaphore "writer-a"))
  (<- (AcquireSemaphore sem))
  (<- at int (now-epoch-ms))
  (.append log #("took" at))
  (<- (ReleaseSemaphore sem))
  None)


(deftest test-a-release-cut-off-from-the-store-lets-the-lease-lapse-without-raising
  ;; 返す時に保存へ届かない(coordinator の作り直しの最中 — 4〜9 秒の途絶の中の 5 秒目に返す)。返しは例外を投げずに終わり、延ばす係は
  ;; 止まっているので lease は最後に延ばせた期限(15 秒)で解け、待っていた次の持ち手がその後に取る。直す前は返しが届かない例外を
  ;; 投げ、呼び手(lease を返して進む常駐の process)ごと落ちた。
  (setv clock (SimClock) store {} log [])
  (setv sa (SemaphoreSession "old" :ttl-seconds 15.0)
        sb (SemaphoreSession "new" :ttl-seconds 15.0))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
        (run-all [(with_handlers [(cut-between clock 4000 9000) (cluster-semaphore sa)] (release-in-a-cut log))
                  (with_handlers [(cluster-semaphore sb)] (take-after-a-second log))])))
  (setv released (lfor #(kind at) log :if (= kind "released") at)
        took (lfor #(kind at) log :if (= kind "took") at))
  (assert (= released [5000]) log)
  (assert (and (= (len took) 1) (<= 15000 (get took 0) 15500)) log))


(defk take-after [seconds]
  {:pre [(: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 秒後に lease writer-a を取りに行き、取れた時刻(仮想の時計の ms)を答えて返すため(保存へ届かない間に取りに行く持ち手)。"
  (<- (Delay seconds))
  (<- sem Semaphore (CreateNamedSemaphore "writer-a"))
  (<- (AcquireSemaphore sem))
  (<- at int (now-epoch-ms))
  (<- (ReleaseSemaphore sem))
  at)


(deftest test-a-claim-cut-off-from-the-store-takes-the-lease-once-the-store-is-back
  ;; 取りに行く時に保存へ届かない(coordinator の作り直しの最中 — 4〜9 秒の途絶の中の 5 秒目に取りに行く。2026-10-11 02:45 JST に、
  ;; lease を取りに行った常駐の process が届かない例外で落ちた実例)。
  ;; 取りは例外を投げずに retry-seconds(0.5 秒)ごとに試し直し、保存が戻った後(9 秒より後・試し直しの間の内)に取る。直す前は取りが
  ;; 届かない例外を投げ、呼び手の task ごと終わった。
  (val clock (SimClock))
  (val store {})
  (val sa (SemaphoreSession "late" :ttl-seconds 15.0))
  (<- took int (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
                 (with_handlers [(cut-between clock 4000 9000) (cluster-semaphore sa)] (take-after 5.0))))
  (assert (< 9000 took 9600) took))


(defk hold-for [seconds]
  {:pre [(: seconds float)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "起点で lease writer-a を取り、seconds 秒持ってから返すため(次の持ち手が空きを待つ間の前の持ち手 — 持つ間は延ばす)。"
  (<- sem Semaphore (CreateNamedSemaphore "writer-a"))
  (<- (AcquireSemaphore sem))
  (<- (Delay seconds))
  (<- (ReleaseSemaphore sem))
  None)


(deftest test-a-lease-wait-cut-off-from-the-store-takes-the-lease-once-it-is-free
  ;; 空きを待っている間に保存へ届かなくなる(2026-10-12 01:00 JST ごろの本番 — 常駐の process が lease を取りに行った task が、coordinator の
  ;; 空きの待ち GET /watch の ConnectTimeout(RouteUnreachable)で終わった。その lease は前の持ち手が返した後も取られず、05:15 に process が
  ;; 退く時に取りを取り消して Wait した所で、残っていたその例外で process が落ちた)。
  ;; 旧 A が起点から 12 秒持つ。新 B は 1 秒に取りに行き、断られて空きを待つ。B の機体からは 4〜9 秒に保存へ届かない — 5 秒の A の延ばしで
  ;; 起きた B の待ちの答えは届かない。待ちは例外を投げずに retry-seconds(0.5 秒)おいて取り直し、A が返した 12 秒の後(試し直しの間の内)に
  ;; 取る。直す前は待ちが届かない例外を投げ、取りに行った task ごと終わった。
  (val clock (SimClock))
  (val store {})
  (val sa (SemaphoreSession "old" :ttl-seconds 15.0))
  (val sb (SemaphoreSession "new" :ttl-seconds 15.0))
  (<- done list (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
                  (run-all [(with_handlers [(cluster-semaphore sa)] (hold-for 12.0))
                            (with_handlers [(cut-between clock 4000 9000) (cluster-semaphore sb)] (take-after 1.0))])))
  (val took (get done 1))
  (assert (<= 12000 took 12500) took))


(defk cancel-a-waiting-taker []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 秒に lease writer-a を取りに行く task を始め、3 秒(空きを待っている途中)に取り消して、取り消しが終わった刻(#(\"cancelled\" 刻))か
   取れた刻(#(\"took\" 刻))を答えるため。"
  (<- task Task (Spawn (take-after 1.0)))
  (<- (Delay 3))
  (<- (Cancel task))
  (try
    (<- took int (Wait task))
    #("took" took)
    (except [cancelled TaskCancelledError]
      (<- at int (now-epoch-ms))
      #("cancelled" at))))


(deftest test-a-cancel-reaches-a-taker-waiting-for-the-lease
  ;; 壊さない事の検(直す前も後も緑): 空きを待っている取りの task を取り消すと、取り消しはその場で task を終える(待ちの失敗を試し直す
  ;; 取りが、取り消し TaskCancelledError を届かない失敗として飲んで待ち直さない)。旧 A が起点から 20 秒持ち、B の取りは 1 秒から空きを待つ。
  ;; 3 秒に取り消す — 取り消しの終わりは 3 秒で、B は lease を取らない。飲んだ場合は A が返す 20 秒に取れて赤。
  (val clock (SimClock))
  (val store {})
  (val sa (SemaphoreSession "old" :ttl-seconds 15.0))
  (val sb (SemaphoreSession "new" :ttl-seconds 15.0))
  (<- done list (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
                  (run-all [(with_handlers [(cluster-semaphore sa)] (hold-for 20.0))
                            (with_handlers [(cluster-semaphore sb)] (cancel-a-waiting-taker))])))
  (val ended (get done 1))
  (assert (= ended #("cancelled" 3000)) ended))


(defhandler claim-reply-held-back [#^ int seconds]
  ;; 引数に残す理由: seconds は筋書きの返事を遅らせる秒(筋書きごとに違う)。
  ;; claim を盤に通した後、返事を seconds 秒遅らせる(本番は返事が失われて送り直す間 — claim は盤に通っているのに、取りはまだ知らない)。
  (LeaseOp [name op token permits ttl-ms]
    :when (= op "claim")
    (<- answer LeaseAnswer effect)
    (<- (Delay seconds))
    (resume answer)))


(defk cancel-a-taker-before-its-claim-reply []
  {:pre [] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 秒に lease writer-a を取りに行く task を始め、3 秒(claim は盤に通ったが返事を待っている途中)に取り消して、取り消しの終わりを待つため。"
  (<- task Task (Spawn (take-after 1.0)))
  (<- (Delay 3))
  (<- (Cancel task))
  (try
    (<- _took int (Wait task))
    (except [cancelled TaskCancelledError]
      None))
  None)


(deftest test-a-cancelled-claim-whose-reply-is-late-gives-the-lease-back
  ;; 取りの claim が盤に通った後、返事を待っている間に取りが取り消される(常駐の process の入れ替え・drain の時点)。
  ;; B は 1 秒に取りに行き、claim は盤に通るが返事が 20 秒遅れる。3 秒に B の取りを取り消す。
  ;; 次の持ち手 C は 4 秒に取りに行き、B の取り消しが同じ token で返した後なので、4 秒(試し直しの間の内)に取る。
  ;; 直す前: 取りは取り消しをそのまま上げ、盤に通った B の token が期限(1 秒 + 15 秒)まで残り、C は 16 秒の後に取った。
  (val clock (SimClock))
  (val store {})
  (val sb (SemaphoreSession "b" :ttl-seconds 15.0))
  (val sc (SemaphoreSession "c" :ttl-seconds 15.0))
  (<- done list (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store)]
                  (run-all [(with_handlers [(claim-reply-held-back 20) (cluster-semaphore sb)] (cancel-a-taker-before-its-claim-reply))
                            (with_handlers [(cluster-semaphore sc)] (take-after 4.0))])))
  (val took (get done 1))
  (assert (<= 4000 took 4500) took))


(deftest test-fence-verdict-is-a-pure-check-of-the-hold-and-the-clock
  (assert (is (fence-verdict {"token" "t" "expiresMs" 15000} 12999 2000) None))
  (assert (is-not (fence-verdict {"token" "t" "expiresMs" 15000} 13000 2000) None))
  (assert (is-not (fence-verdict None 0 2000) None)))


;; --- lease の立場(待機の process の書きを捨てる — 2026-09-24)---------------------------------------------

(import doeff_cluster [LeaseStanding STANDBY HELD LOST])

(defk standing-story [log]
  {:pre [(: log list)] :post [(: % (type None))]}
  (<- a (LeaseStanding "turn-lock"))
  (<- sem Semaphore (CreateNamedSemaphore "turn-lock" 1))
  (<- (AcquireSemaphore sem))
  (<- b (LeaseStanding "turn-lock"))
  (<- (ReleaseSemaphore sem))
  (<- c (LeaseStanding "turn-lock"))
  (.extend log [a b c])
  None)

(deftest test-lease-standing-is-standby-until-held-and-lost-after
  (setv log [] store {} clock (! (clock-at 1000)))
  (<- (with_handlers [(sim-time-handler :clock clock) #* (board-handlers store) (cluster-semaphore (SemaphoreSession "w"))]
        (standing-story log)))
  (assert (= log [STANDBY HELD LOST]) log))
