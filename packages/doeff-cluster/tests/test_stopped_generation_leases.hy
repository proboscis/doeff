;; 止まった世代の process の担い手(#3770 の続き — 2026-11-05 の node-h): 同じ名の新しい世代は、さらに古い世代がまだ退いている間に起きると
;; 重なりを名乗る(overlapsElder — 起動の script が自分の外のどれかの世代の dir の錠を持たれていると見た)。その名乗りは直前の世代の
;; 終わりの印でない(AL-D2e)ので、名乗りの時には担い手を外さない。直す前は、直前の世代が突然止まっていても(機体の memory の見張りに
;; 殺された)、その process の担い手を外す道がほかに無く、担い手は期限の切れたまま盤の行に残った。行から担い手が外れるのを待つ新しい
;; process の待ち(GET /watch?lease=<名>&vacate=<担い手>)は起きず、その lease を永久に取れなかった(担い手 101 本が 2 時間を越えて残った)。
;; coordinator は、退いた世代が lease の窓(ClusterTiming.lease-ms)を越えて黙った時にその世代を止まったと判じ、その世代の最新の報告の
;; process の担い手を外す(名乗りの 1 回の出来事に結ばない)。生きて報告し続ける退いた世代の担い手は外さず、延長も通す(AL-D2e)。
;;   1 A が退いている間に B が突然止まり、C が重なりを名乗る → B が黙って lease の窓を越えた拍に B の担い手が外れ、vacate の待ちが起きる。
;;     報告し続ける A の担い手は残り、延長も通る。A も後で黙れば、同じ判じで A の担い手も外れる。
;;   2 C の名乗りが B の沈黙の窓より後なら、名乗りの歩の中で外れる。
;;   3 次に起きる刻(tick-due)は、退いた世代の沈黙の窓が切れる刻を数える(静かな区間でも、その刻に起きて外す)。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.test_drain [Coord T])
(import tests.test_elder_process [generation-beat])
(import doeff_cluster [http-request])
(import doeff_cluster [lease-holder holder-tokens-prefix])
(import doeff_cluster [ClusterState])
(import doeff_cluster [tick tick-due])
(import doeff_cluster._shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster._coordinator.intent.cluster_model [Watcher WatchRefusal WatchStep])
(import doeff_cluster._coordinator.core.watch_policy [watch-of settle-watch])

(val LEASE-TTL-MS 30000)
;; A(退いている世代)・B(突然止まる世代)・C(重なりを名乗る新しい世代)の process の行。
(val A-ROWS [{"name" "w" "phase" "running" "pid" 100 "instance" "1-aaa"}])
(val B-ROWS [{"name" "w" "phase" "running" "pid" 200 "instance" "2-bbb"}])
(val C-ROWS [{"name" "w" "phase" "elder-runs"}])


(defk token-of [job instance]
  {:pre [(: job str) (: instance str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "job の process(世代の名 instance)の子の土台が名乗る最初の token を求めるため(SemaphoreSession.next-token と同じ綴り)。"
  (+ (holder-tokens-prefix (lease-holder job instance)) "1"))


(defn #^ bool leased [#^ Coord c #^ str lock #^ str op #^ str token]  ; defk にできない: 模擬の世界の method を呼ぶ(状態を進める)
  "lease lock への操作 op を token で 1 つ当て、答えの ok を返す(期限は LEASE-TTL-MS)。"
  (get (c.call "POST" (+ "/leases/" lock) {"op" op "token" token "permits" 1 "ttlMs" LEASE-TTL-MS} :actor None) "ok"))


(defn #^ tuple holders-of [#^ Coord c #^ str lock]  ; defk にできない: 模擬の世界の状態を読む(leased と組で使う)
  "盤の lease lock の行の担い手の token(並べた列 — 行が無ければ空)。"
  (setv row (.row c.state.board (+ "semaphore/" lock)))
  (if (is row None) #() (tuple (sorted (get row.value "holders")))))


(defk vacate-woke [c lock holder]
  {:pre [(: c Coord) (: lock str) (: holder str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "今の状態で GET /watch?lease=<lock>&vacate=<holder> の待ちを出した時、その歩で起きるかを知るため。"
  (<- request (http-request "GET" "/watch" {"lease" lock "vacate" holder} None))
  (<- watcher (| Watcher WatchRefusal None) (watch-of request c.now T))
  (assert (isinstance watcher Watcher) watcher)
  (<- step WatchStep (settle-watch watcher c.state c.now T))
  (and (is-not step.answer None) step.answer.changed))


(defk elder-retiring-while-b-holds []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "A(boot a)が w の process 1-aaa を走らせ、elder-lock を持つ。B(boot b)が A の退いている間に重なりを名乗り、w の process 2-bbb が
   writer-lock を持つ。A は退いた世代として報告を続ける。答え = #(模擬の世界 B の最後の報告の刻)。"
  (val c (Coord #()))
  (generation-beat c "a" A-ROWS)
  (assert (leased c "elder-lock" "claim" (! (token-of "w" "1-aaa"))))
  (c.advance 1)
  (generation-beat c "b" B-ROWS :overlaps True)
  (val heard-b c.now)
  (generation-beat c "a" A-ROWS)
  (assert (leased c "writer-lock" "claim" (! (token-of "w" "2-bbb"))))
  (assert (= (. (.row c.state.workers "node-a") boot) "b"))
  #(c heard-b))


(defk beats-of-a-and-c [c seconds]
  {:pre [(: c Coord) (: seconds int)] :post [(: % (| None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 秒の間、1 秒ごとに A(退いた世代)と C(今の世代)が報告し、A の process が elder-lock を延ばす(B は黙ったまま)状態へ進めるため。"
  (val token (! (token-of "w" "1-aaa")))
  (for [_ (range seconds)]
    (c.advance 1)
    (generation-beat c "a" A-ROWS)
    (generation-beat c "c" C-ROWS :overlaps True)
    (assert (leased c "elder-lock" "renew" token) "報告を続ける退いた世代の process の延長は通る"))
  None)


(deftest test-a-suddenly-stopped-generation-loses-its-holders-once-silent-even-when-the-successor-overlaps-an-older-one
  ;; 失敗ケース(2026-11-05 07:51 JST の node-h): B が突然止まった後、C が A の退いている間に重なりを名乗る。直す前は B の担い手が
  ;; 期限の切れたまま行に残り、C の process の vacate の待ちが起きなかった。
  (<- #(c _) tuple (elder-retiring-while-b-holds))
  (c.advance 1)
  ;; B は止まった(以後報告しない)。C が名乗る — B の最後の報告から lease の窓の内。
  (generation-beat c "c" C-ROWS :overlaps True)
  (assert (= (. (.row c.state.workers "node-a") boot) "c"))
  (assert (= (holders-of c "writer-lock") #((! (token-of "w" "2-bbb")))) "名乗りの時はまだ外さない(B の沈黙が窓の内)")
  (assert (not (! (vacate-woke c "writer-lock" (lease-holder "w" "3-ccc")))))
  ;; B の沈黙が lease の窓を越えるまで A と C が報告を続ける。
  (! (beats-of-a-and-c c (+ (// T.lease-ms 1000) 2)))
  (assert (= (holders-of c "writer-lock") #()) #("止まった B の process の担い手が外れる" (holders-of c "writer-lock")))
  (assert (! (vacate-woke c "writer-lock" (lease-holder "w" "3-ccc"))) "C の process の vacate の待ちが起きる")
  (assert (= (holders-of c "elder-lock") #((! (token-of "w" "1-aaa")))) "報告を続ける A の担い手は残る")
  (assert (leased c "writer-lock" "claim" (! (token-of "w" "3-ccc"))) "C の process が取れる")
  ;; A も突然止まる: A の沈黙が窓を越えれば、同じ判じで A の担い手も外れる。
  (for [_ (range (+ (// T.lease-ms 1000) 2))]
    (c.advance 1)
    (generation-beat c "c" C-ROWS :overlaps True))
  (assert (= (holders-of c "elder-lock") #()) #("止まった A の process の担い手も外れる" (holders-of c "elder-lock")))
  (assert (= (holders-of c "writer-lock") #((! (token-of "w" "3-ccc")))) "今の世代 C の担い手は外さない"))


(deftest test-a-successor-naming-after-the-silence-window-frees-the-stopped-generation-holders-in-the-same-step
  ;; C が B の沈黙の窓より後に名乗れば、名乗りの歩の調停で外れる(次の拍を待たない)。
  (<- #(c _) tuple (elder-retiring-while-b-holds))
  (for [_ (range (+ (// T.lease-ms 1000) 2))]
    (+= c.now 1000)
    (generation-beat c "a" A-ROWS))
  (generation-beat c "c" C-ROWS :overlaps True)
  (assert (= (holders-of c "writer-lock") #()) (holders-of c "writer-lock"))
  (assert (= (holders-of c "elder-lock") #((! (token-of "w" "1-aaa")))) "報告を続ける A の担い手は残る"))


(deftest test-the-next-wake-counts-the-silence-window-of-a-retired-generation
  ;; 静かな区間(要求の無い間)でも、退いた世代の沈黙の窓が切れる刻に起きて外す: tick-due はその刻を答える。
  (<- #(c heard-b) tuple (elder-retiring-while-b-holds))
  (c.advance 1)
  (generation-beat c "c" C-ROWS :overlaps True)
  (<- due (| DueAt DueNow DueNever) (tick-due c.state c.now T))
  (assert (isinstance due DueAt) due)
  (assert (<= due.at (+ heard-b T.lease-ms 1)) #(due heard-b))
  (setv c.now (+ heard-b T.lease-ms 1))
  (<- ticked ClusterState (tick c.state c.now T))
  (setv c.state ticked)
  (assert (= (holders-of c "writer-lock") #()) (holders-of c "writer-lock")))
