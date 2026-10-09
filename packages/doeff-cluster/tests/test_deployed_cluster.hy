;;; 配備の cluster の handler(shared/entry/deployed_cluster.hy の deployed-cluster-answers — #3294・ADR-DOE-CLUSTER-001 R8 の追補 (3))の検。
;;;
;;; 相手 = 本物の coordinator の判断を httpx の MockTransport の後ろに置いた MemoryCoordinator(tests/detached_rig.hy)・HTTP の答え手 =
;;; transport-http(tests/transport_http.hy — 本番の答え手と同じく届かない失敗を値で返す)・時計 = 仮想の時計。配備の handler は worker を
;;; 起こさないので、この相手には worker が居ない — 宣言した Service は Ready にならず、job の process も名乗られない(待つ effect は期限の
;;; 後に時間切れを値で返す)。worker の居る配備で Ready まで通すのは O の確かめ(cluster の上)で行う。
;;;   (a) 宣言の前は Missing・Redeclare は宣言した Service の名を返し、coordinator がその Service を数える(Missing でなくなる)・
;;;       Ready を待つと時間切れ(最後に読んだ状態つき)・job の process を待つと時間切れ。
;;;   (b) 壊す effect(Crash・KillWorker・StopWorker・StopCoordinator・CrashCoordinator)は DeployedCannotAnswer で effect の名を出して止まる。
;;;       失敗ケース: Crash に「落とした数 0」と答える壊した答え手を内側に置くと名指しの止まりが無くなり、同じ判じ(refusal-of)が None になる。
;;;   (c) /state の job の pid の引き(job-pids-of)は、どの worker の上かを問わず、pid の無い行(起こす前・終わった後)を外す。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import datetime [datetime timezone])
(import httpx)
(import pytest)
(import doeff [run with-handlers EffectBase Program])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf ReadinessWaitExpired AwaitReadiness AwaitJobProcess
                                                     JobProcessWaitExpired Redeclare Crash KillWorker StopWorker StopCoordinator
                                                     CrashCoordinator])
(import doeff_cluster.shared.entry.deployed_cluster [DeployedCluster DeployedCannotAnswer deployed-cluster-answers])
(import doeff_cluster.shared.protocol.coordinator_reads [job-pids-of])
(import doeff_cluster.shared.protocol.detached [service-facts-of-json])
(import tests.transport_http [transport-http COORDINATOR-URL])
(import tests.detached_rig [MemoryCoordinator])
(import tests.fixtures.machine_app [pings machine-foundation])
(import tests.fixtures.replicas [with-replicas])

;; 配備の cluster の値(宛先 = 検の coordinator の口・送り手の名・宣言の版 — 版の木の道なので実行環境は無し・版の識別は宣言する
;; process の物)。
(val TARGET (DeployedCluster :url COORDINATOR-URL :actor "deployed-test" :revision (* "a" 40) :runtime-env None :versions-read None))
;; 配備の worker の版の識別の代役(宣言する process の版と違う値 — target の読みが宣言に載ることを見分けるため)。
(val PINNED-VERSIONS {"doeff-cluster" "deployed-worker-pin"})
;; 仮想の時計の起点(epoch 秒 1790380800 = 2026-09-26)。
(val START (datetime.fromtimestamp 1790380800 timezone.utc))


(defk asked [coordinator clock step]
  {:pre [(: coordinator MemoryCoordinator) (: clock SimClock) (: step (| EffectBase Program))] :post [(: % "step の答え")]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの effect 1 つを、既定の配備の値 TARGET で答えさせるため(asked-with の既定の宛先)。"
  (<- answer (asked-with TARGET coordinator clock step))
  answer)


(defk asked-with [target coordinator clock step]
  {:pre [(: target DeployedCluster) (: coordinator MemoryCoordinator) (: clock SimClock) (: step (| EffectBase Program))]
   :post [(: % "step の答え")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの effect 1 つを、配備の値 target の配備の handler(内側)と検の coordinator の HTTP の答え手(外側)の組で答えさせるため
   (coordinator の状態は MemoryCoordinator が effect をまたいで持つ)。"
  (run (scheduled (with-handlers [(sim-time-handler :clock clock) slog-discard-handler
                                  (transport-http (httpx.MockTransport coordinator.handle))
                                  (deployed-cluster-answers target)]
                    step))))


(defk service-spec [coordinator name]
  {:pre [(: coordinator MemoryCoordinator) (: name str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の coordinator が持つ Service name の spec を、本番と同じ資源の口(GET /resources/Service/<名>)で読むため。"
  (val response (coordinator.handle (httpx.Request "GET" (+ COORDINATOR-URL "/resources/Service/" name))))
  (get (.json response) "spec"))


(defk pinned-versions []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "配備の worker の環境の版を読む Program の代役として、PINNED-VERSIONS を答えるため。"
  PINNED-VERSIONS)


(deftest test-the-deployed-handler-carries-replicas-and-the-target-versions
  ;; #3487: 取り下げ(job の :replicas 0 の系の宣言し直し)と配備の worker の版(target の versions-read の答え)が coordinator の書きへ
  ;; 届く — 系の台数 0 は Service の replicas になり、宣言の run.versions には宣言する process の版ではなく target の読みの答えが載る。
  (val clock (SimClock START))
  (val coordinator (MemoryCoordinator clock))
  (val target (DeployedCluster :url COORDINATOR-URL :actor "deployed-test" :revision (* "a" 40) :runtime-env None
                               :versions-read (pinned-versions)))
  (<- withdrawn (with-replicas (pings machine-foundation) 0))
  (<- declared tuple (asked-with target coordinator clock (Redeclare withdrawn)))
  (assert (= declared #("ping")) declared)
  (<- spec dict (service-spec coordinator "ping"))
  (assert (= (get spec "replicas") 0) spec)
  (assert (= (get spec "run" "versions") PINNED-VERSIONS) spec)
  ;; 起こし: 元の系(job の :replicas 1)で宣言し直すと Service の replicas は 1 に戻る(台数は系の値が必ず持つ — 省略で 0 を保つ
  ;; 取り違えが起きない)。
  (<- _again tuple (asked-with target coordinator clock (Redeclare (pings machine-foundation))))
  (<- woken dict (service-spec coordinator "ping"))
  (assert (= (get woken "replicas") 1) woken))


(deftest test-the-deployed-handler-declares-and-reads-readiness-from-the-coordinator
  (val clock (SimClock START))
  (val coordinator (MemoryCoordinator clock))
  ;; 宣言の前: coordinator は Service を数えていない。
  (<- before ServiceReadiness (asked coordinator clock (ReadinessOf "ping")))
  (assert (= before.state "Missing") before)
  ;; 宣言: 答えは宣言した Service の名・coordinator はその Service を数える(worker が居ないので Ready ではない)。
  (<- declared tuple (asked coordinator clock (Redeclare (pings machine-foundation))))
  (assert (= declared #("ping")) declared)
  (<- after ServiceReadiness (asked coordinator clock (ReadinessOf "ping")))
  (assert (!= after.state "Missing") after)
  (assert (!= after.state "Ready") after)
  ;; 待つ effect は期限の後に時間切れを値で返す(黙って待ち続けない)。
  (<- ready-wait (asked coordinator clock (AwaitReadiness "ping" "Ready" 3.0)))
  (assert (isinstance ready-wait ReadinessWaitExpired) ready-wait)
  (assert (= ready-wait.last.state after.state) ready-wait)
  (<- process-wait (asked coordinator clock (AwaitJobProcess "ping" #() 3.0)))
  (assert (isinstance process-wait JobProcessWaitExpired) process-wait))


(defk listed-revisions [coordinator]
  {:pre [(: coordinator MemoryCoordinator)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検査用の coordinator の Service の一覧(本番と同じ GET /resources/Service)を、ReadServices の本番の handler と同じ関数
   (service-facts-of-json)で (名, 宣言の版) の列にするため。"
  (val response (coordinator.handle (httpx.Request "GET" (+ COORDINATOR-URL "/resources/Service"))))
  (<- facts (service-facts-of-json (.json response)))
  (lfor fact facts #(fact.name fact.revision)))


(deftest test-the-real-coordinator-list-carries-each-services-declared-revision
  ;; #2718 の子 S2a: 宣言の道具が、別の Service を宣言し直す前に「どの Service がどの版で動くか」を Service の一覧の読み(ReadServices)で
  ;; 照らす。本物の coordinator の一覧の行の spec に宣言の版が在り、別の版で宣言し直すと新しい版になる(一覧の頭の revision は
  ;; coordinator の状態の版で、宣言の版ではない)。
  (val clock (SimClock START))
  (val coordinator (MemoryCoordinator clock))
  (<- _first tuple (asked coordinator clock (Redeclare (pings machine-foundation))))
  (<- first list (listed-revisions coordinator))
  (assert (= first [#("ping" TARGET.revision)]) first)
  (val later (DeployedCluster :url COORDINATOR-URL :actor "deployed-test" :revision (* "b" 40) :runtime-env None :versions-read None))
  (<- _second tuple (asked-with later coordinator clock (Redeclare (pings machine-foundation))))
  (<- second list (listed-revisions coordinator))
  (assert (= second [#("ping" (* "b" 40))]) second))


(defhandler crash-answered
  ;; 失敗ケースの壊した答え手: Crash に「落とした数 0」と答える(配備の cluster で壊す effect に答えてしまう handler の代役)。
  (Crash [name]
    (resume 0)))


(defk refusal-of [coordinator clock step broken]
  {:pre [(: coordinator MemoryCoordinator) (: clock SimClock) (: step (| EffectBase Program)) (: broken bool)] :post [(: % (| str None))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "壊す effect を出した時に配備の handler が名指しで止めたかを判じるため: 止めた訳(DeployedCannotAnswer の文)か、止めずに答えたら None。
   broken = 壊した答え手 crash-answered を配備の handler の内側に置く(失敗ケース)。"
  (val handlers (if broken [crash-answered] []))
  (try
    (! (asked coordinator clock (with-handlers handlers step)))
    None
    (except [refused DeployedCannotAnswer]
      (str refused))))


(deftest test-the-deployed-handler-refuses-every-breaking-effect-by-name
  (val clock (SimClock START))
  (val coordinator (MemoryCoordinator clock))
  (for [#(effect named) [#((Crash "ping") "Crash(ping)") #((KillWorker "w1") "KillWorker(w1)") #((StopWorker "w1") "StopWorker(w1)")
                         #((StopCoordinator 1.0) "StopCoordinator") #((CrashCoordinator 1.0) "CrashCoordinator")]]
    (<- refused (| str None) (refusal-of coordinator clock effect False))
    (assert (and (is-not refused None) (in named refused)) #(named refused)))
  ;; 失敗ケース: Crash に答える壊した答え手が内側に在ると、名指しの止まりが無い(判じが None)。
  (<- answered (| str None) (refusal-of coordinator clock (Crash "ping") True))
  (assert (is answered None) answered))


(deftest test-job-pids-are-read-from-every-worker-and-pidless-rows-are-left-out
  (val state {"statuses" {"w1" {"jobs" [{"name" "ping" "pid" 41} {"name" "other" "pid" 7}]}
                          "w2" {"jobs" [{"name" "ping" "pid" 52} {"name" "ping"}]}
                          "w3" {}}})
  (<- pids (get tuple #(int ...)) (job-pids-of state "ping"))
  (assert (= (sorted pids) [41 52]) pids)
  (<- none (get tuple #(int ...)) (job-pids-of state "missing"))
  (assert (= none #()) none))
