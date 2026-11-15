;; 依る Service の戻りの合図は、宣言の版が動いているかを問わず「今その Service の process が答えているか」で出る(Service の
;; status.serving — resource_policy.service-serving。AwaitServiceReady と service-back はこの欄を読む)。
;;
;; 出自: 依る service が短く止まって戻った時、その Service が新しい版で宣言し直されていて新しい版が組めない(準備が失敗し続ける)間、
;; 旧い版の process が Ready を報告して答え続けていても、戻りの合図は宣言の版の process しか数えない status.ready を読んでいたので、
;; 新しい版が起きるまで(10 分)戻りを知らせなかった。
;;
;; 模擬の cluster(本物の coordinator と worker を仮想の時計で回す sim-cluster)で、新しい版のコードの木の準備が恒久の失敗で終わる
;; worker(SimWorker.unpreparable)の上に筋書きを置く:
;; - 入れ替え(handoff)と recreate の Service を Ready にした後、組めない版で宣言し直す。旧い版の process は動き続けて Ready を報告し、
;;   status.ready は NotReady(版が違う)のまま。その間に撃った戻りの待ち(service-back)は旧い版の process の報告で待たずに答える。
;;   失敗ケース: status.ready を読む待ち(直す前)は、新しい版が起きないので上限まで答えない。
;; - ready を名乗る process が 1 つも無い間(旧い版の担い手が落ち、置き直した先でも新しい版が組めない)は答えない。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming System])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [service-back-within])
(import doeff_cluster [ServiceReady])
(import doeff_cluster [sim-cluster SimWorker ReadCoordinator ProcessesOf PreparationsOf Redeclare KillWorker])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons beacons-v2 handoff-beacons handoff-beacons-v2 NET])

;; 組めない新しい版(宣言し直しの版 — worker はこの版のコードの木を取り寄せられない)。
(val BROKEN "sim-2")
;; 置ける worker 2 台(どちらも BROKEN の版を組めない — 担い手が落ちて置き直されても新しい版は起きない)。
(val WORKERS #((SimWorker :name "w1" :provides NET :unpreparable (frozenset [BROKEN]))
               (SimWorker :name "w2" :provides NET :unpreparable (frozenset [BROKEN]))))
;; 戻りを待つ上限(秒)— 直す前の待ちは、ここまで答えずに None を返す。
(val PATIENCE 30.0)


(defrecord StuckSeen
  "組めない版へ宣言し直した筋書きの読み: before / during / after = 宣言し直しの前・後(待つ前)・待った後の Service の status・answer =
   戻りの待ち(service-back-within)の答え・waited-ms = 待った仮想の ms・processes = beacon の process の列・failed = 組めない版の
   準備の失敗の数(担い手の worker の準備の記録)。"
  (#^ dict before)
  (#^ dict during)
  (#^ (| ServiceReady None) answer)
  (#^ int waited-ms)
  (#^ dict after)
  (#^ tuple processes)
  (#^ int failed))


(defk status-of [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の coordinator の Service name の status を読むため。"
  (<- view dict (ReadCoordinator (+ "/resources/Service/" name)))
  (get view "status"))


(defk holder-of [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Service name の置き先の worker の名を読むため。"
  (<- state dict (ReadCoordinator "/state"))
  (get (get (get state "placements") name) "worker"))


(defk failed-preparations [worker]
  {:pre [(: worker str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の準備の記録のうち、組めない版の準備が失敗で終わった数を数えるため。"
  (<- preparations tuple (PreparationsOf worker))
  (len (lfor p preparations :if (and (= p.key BROKEN) (is-not p.failure None)) p)))


(defk stuck-redeclare [next-system kill]
  {:pre [(: next-system System) (: kill bool)] :post [(: % StuckSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 落ち着いた後(beacon が Ready)に、組めない版 BROKEN で next-system を宣言し直し、新しい版の準備が失敗するまで 8 秒待つ。
   kill なら担い手の worker を落として 8 秒待つ(準備の報告の窓 5 秒を過ぎる — 旧い版の報告は窓の外)。それから戻りを上限 PATIENCE 秒
   まで待つ。"
  (<- (Delay 12.0))
  (<- before dict (status-of "beacon"))
  (<- holder str (holder-of "beacon"))
  (<- (Redeclare next-system :revision BROKEN))
  (<- (Delay 8.0))
  (<- failed int (failed-preparations holder))
  (when kill
    (<- _killed int (KillWorker holder))
    (<- (Delay 8.0)))
  (<- during dict (status-of "beacon"))
  (<- started int (now-epoch-ms))
  (<- answer (| ServiceReady None) (service-back-within "beacon" PATIENCE))
  (<- at int (now-epoch-ms))
  (<- after dict (status-of "beacon"))
  (<- processes tuple (ProcessesOf "beacon"))
  (StuckSeen :before before :during during :answer answer :waited-ms (- at started) :after after :processes processes :failed failed))


(deftest test-the-old-version-still-answering-brings-the-service-back-while-the-new-version-cannot-be-built
  ;; 失敗ケース: 戻りの待ちが status.ready(宣言の版の process だけを数える)を読むと、新しい版が組めない間は上限 PATIENCE まで答えず None。
  (for [#(first-system next-system) [#(handoff-beacons handoff-beacons-v2) #(beacons beacons-v2)]]
    (<- seen StuckSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (first-system sim-foundation)
                                    (stuck-redeclare (next-system sim-foundation) False) :workers WORKERS))
    (assert (= (get seen.before "ready") "Ready") seen)
    ;; 新しい版は組めず(準備が失敗で終わった)、起きた process は最初の 1 つだけで、動き続けている。
    (assert (>= seen.failed 1) seen)
    (assert (= (len seen.processes) 1) seen.processes)
    (assert (is (. (get seen.processes 0) exit-code) None) seen.processes)
    ;; 宣言の版は動いていない(Rollout と版の判定の答えは今どおり NotReady)が、旧い版の process が答えているので戻る。
    (assert (= (get seen.during "ready") "NotReady") seen)
    (assert (is-not seen.answer None) "旧い版が答えている間は、新しい版を待たずに戻る")
    (assert (= seen.answer.name "beacon") seen)
    (assert (< seen.waited-ms (* 1000 PATIENCE)) seen)
    (assert (= (get seen.after "ready") "NotReady") seen)
    (assert (is (get seen.before "serving") True) seen)
    (assert (is (get seen.during "serving") True) seen)))


(deftest test-no-process-claiming-ready-keeps-the-service-from-coming-back
  ;; 旧い版の担い手が落ち、置き直した先でも新しい版が組めない: ready を名乗る process が 1 つも無いので、上限まで答えない(None)。
  ;; 失敗ケース: 窓の外の報告や、落ちた世代の報告を数えて serving と答えると、何も答えていない Service を戻ったと読む。
  (<- seen StuckSeen (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (handoff-beacons sim-foundation)
                                  (stuck-redeclare (handoff-beacons-v2 sim-foundation) True) :workers WORKERS))
  (assert (is (get seen.before "serving") True) seen)
  (assert (all (gfor p seen.processes (is-not p.exit-code None))) seen.processes)
  (assert (is (get seen.during "serving") False) seen)
  (assert (is seen.answer None) seen)
  (assert (= seen.waited-ms (int (* 1000 PATIENCE))) seen)
  (assert (is (get seen.after "serving") False) seen))
