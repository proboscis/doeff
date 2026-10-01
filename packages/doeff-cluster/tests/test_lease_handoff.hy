;; 名前付きの lease の担い手の名乗りと、終わった process の lease の外しが同じ定義であること(2026-09-29)。
;;
;; 担い手の名 = lease_rules.lease-holder(<job>/<process の世代の名>)・token = holder-tokens-prefix(担い手) + 番号。子の土台
;; (cluster_foundation.lease-holder-of → SemaphoreSession.next-token)が名乗り、worker(本番の handlers.release-leases・sim の
;; local.release-leases)が worker_policy の ReleaseLeases(job 世代の名)から同じ定義で頭を作って外す。
;;
;; 反例(直す前): 名乗りは <job>/<世代>、外しは <worker>/<世代>/ の頭だったので、終わった process の lease は外れず期限まで残った —
;; 入れ替え(handoff)で旧い版が lease を持ったまま止まると、新しい版は lease の期限(TTL)まで取れなかった。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_time [Delay])
(import doeff_cluster.cluster_foundation [lease-holder-of])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.shared.core.semaphore_handlers [SemaphoreSession])
(import doeff_cluster.shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import doeff_cluster.worker.intent.worker_model [ReleaseLeases])
(import doeff_cluster.sim.local [sim-cluster Redeclare ProcessesOf SharedRows])
(import tests.fixtures.lease_programs [lease-sim-foundation lease-writers lease-writers-v2 HOLDER-ROW])

;; 担い手(cluster-semaphore の SemaphoreSession)の既定の TTL(秒)— 外しが効かなければ、新しい版はこの期限まで取れない。
(val TTL-SECONDS 15.0)


(deftest test-the-holder-a-child-names-is-the-one-the-worker-drops
  ;; 子が名乗った担い手の token は、worker が同じ job と世代から作る頭で外れる(別の job の同じ世代の名・同じ job の別の世代は残る)。
  (val ctx (RunContext "http://coordinator:8080" "w1" "abc" "writer" :instance "2-7f3a"))
  (<- holder str (lease-holder-of ctx))
  (assert (= holder (lease-holder "writer" "2-7f3a")) holder)
  (val token (.next-token (SemaphoreSession holder)))
  (val other-job (.next-token (SemaphoreSession (lease-holder "reader" "2-7f3a"))))
  (val other-instance (.next-token (SemaphoreSession (lease-holder "writer" "3-b0c1"))))
  (val row {"permits" 1 "holders" {token 99 other-job 88 other-instance 77}})
  (val released (ReleaseLeases "writer" "2-7f3a"))
  (val kept (drop-holders row (holder-tokens-prefix (lease-holder released.job released.instance))))
  (assert (is-not kept None) "他の担い手が残るので行は残る")
  (assert (= (get kept "holders") {other-job 88 other-instance 77}) kept)
  ;; 反例: worker の名で始まる頭(直す前の外し)は、子の名乗った token に当たらない。
  (assert (is (drop-holders row (.format "{}/{}/" ctx.worker ctx.instance)) None)))


(defk redeclare-and-read [wait]
  {:pre [(: wait float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 旧い版が lease を取るまで待って盤を読み、版 2 で宣言し直し(handoff)、wait 秒待ってから盤と process の列を読むため。
   答え = #(入れ替える前の盤の行 入れ替えた後の盤の行 process の列)。"
  (<- (Delay 8.0))
  (<- before dict (SharedRows HOLDER-ROW))
  (<- (Redeclare (lease-writers-v2 lease-sim-foundation)))
  (<- (Delay wait))
  (<- after dict (SharedRows HOLDER-ROW))
  (<- processes tuple (ProcessesOf "writer"))
  #(before after processes))


(deftest test-after-a-handoff-the-new-version-takes-the-lease-the-old-one-held-without-waiting-for-its-expiry
  ;; handoff: 新しい版は待機で Ready と報告し、旧い版は lease を返さずに止まる(止めの合図 -15)。worker が終わった旧い版の lease を
  ;; 返すので、新しい版は旧い版が止まってから TTL より十分短い間に lease を取る(直す前は外しが当たらず、期限まで取れなかった)。
  (<- answer tuple (sim-cluster (lease-writers lease-sim-foundation) (redeclare-and-read 25.0)))
  (setv #(before after processes) answer)
  (val old (next (gfor p processes :if (= p.instance (get before HOLDER-ROW "instance")) p)))
  (assert (= old.exit-code -15) processes)
  (val new-holder (get after HOLDER-ROW))
  (assert (!= (get new-holder "instance") old.instance) #(before after))
  (val new (next (gfor p processes :if (= p.instance (get new-holder "instance")) p)))
  (assert (is new.exit-code None) processes)
  ;; 新しい版は旧い版と並んで起き(handoff)、旧い版が止まってから lease を取った。
  (assert (< new.started-ms old.ended-ms) processes)
  (val waited-ms (- (get new-holder "at") old.ended-ms))
  (assert (>= waited-ms 0) #(new-holder old))
  (assert (< waited-ms (* 1000 (/ TTL-SECONDS 3))) #(waited-ms new-holder old)))
