;; worker の drain(2026-09-25 — drain_policy): coordinator の純粋な判断の段ごとの検と、preStop / readinessProbe の Program の検。
;;   並べる(surge)・並べた先の Ready を待つ・付け替えて旧を宣言から外す・移す先が無ければ並べず旧を止めない・取り消し・
;;   入れ替えでない Service は止めて移す・drain 中の worker に新しい置き先を割り当てない・別の世代の heartbeat と期限で解ける・
;;   保存と読み直し。時刻は純粋な now の引数(Program の検は doeff-time の SimClock)。
(require doeff-hy.macros [deftest defhandler <-])
(import dataclasses [replace])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.cluster_model [ClusterTiming ClusterState Request Drain])
(import doeff_cluster.cluster_policy [jobs-for])
(import doeff_cluster.worker_model [spec-hash])
(import doeff_cluster.api_policy [respond tick])
(import doeff_cluster.durable_kv [full-kv state-from-kv DRAIN SURGE])
(import doeff_cluster.drain_client [CoordinatorCall await-drained worker-ready drain-outcome ready-of])
(import doeff_cluster.handlers [CoordinatorLink write-ready-file])
(import os)
(import subprocess)
(import doeff_cluster.drain_main [read-boot])
(import pathlib [Path])
(import pytest)

(setv T (ClusterTiming))
(setv K3S {"kind" "k3s"})
(defn #^ dict service [#^ (| dict None) [extra None]]
  (| {"revision" "r1" "requires" K3S "readiness" {"windowSeconds" 10} "update" "handoff"
      "run" {"kind" "service" "factory" "m:f" "env" "m:e" "config" {}}}
     (or extra {})))


(defclass Coord []
  "coordinator の純粋な判断(api_policy.respond / tick)を、手で送る heartbeat と readiness の報告で回す。"
  (defn #^ None __init__ [self #^ tuple [workers #("atlas" "zeus")]]
    (setv self.now 5000000 self.state (ClusterState :started-ms 0) self.boots {})
    (for [w workers] (self.beat w))
    None)

  (defn #^ object call [self #^ str method #^ str path #^ (| dict None) [body None] #^ (| str None) [actor "c-test"]
            #^ (| int None) [expect 200]]
    (setv #(state status reply) (respond self.state (Request method path {} body :actor actor) self.now T))
    (when (is-not expect None) (assert (= status expect) #(method path status reply)))
    (setv self.state state)
    reply)

  (defn #^ (| int None) gen-on [self #^ str worker #^ str name]
    "worker に置かれた(置き先か並べた置き先の)世代。無ければ None。"
    (for [a [(.get self.state.placements name) (.get self.state.surges name)]]
      (when (and a (= a.worker worker)) (return a.generation)))
    None)

  (defn #^ dict row [self #^ str worker #^ str name]
    (setv job (next (gfor j self.state.jobs :if (= j.spec.name name) j)) gen (self.gen-on worker name))
    {"name" name "phase" "running" "runningRevision" job.spec.revision "desiredRevision" job.spec.revision
     "instance" (.format "{}-{}-g{}" worker name gen) "specHash" (spec-hash job.spec) "placement" gen "attempts" 1})

  (defn #^ object beat [self #^ str worker #^ (| list None) [running None] #^ dict [labels K3S] #^ (| str None) [boot None]]
    "heartbeat。running = この worker が動かしていると報告する job の名(置かれている物)。"
    (setv (get self.boots worker) (or boot (.get self.boots worker "b1")))
    (self.call "POST" "/heartbeat" {"name" worker "labels" labels "capacity" 10 "versions" {}
                                    "boot" (get self.boots worker)
                                    "statuses" (lfor n (or running []) (self.row worker n))}
               :actor None))

  (defn #^ object ready [self #^ str worker #^ str name #^ bool [ready True] #^ str [role "standby"]]
    (setv row (self.row worker name))
    (self.call "POST" (.format "/resources/Service/{}/readiness" name)
               {"worker" worker "pid" 1 "revision" (get row "runningRevision") "instance" (get row "instance") "attempt" "1"
                "specHash" (get row "specHash") "placement" (get row "placement") "ready" ready "role" role}
               :actor None))

  (defn #^ None advance [self #^ int [seconds 1]]
    (+= self.now (* 1000 seconds))
    (setv self.state (tick self.state self.now T))
    None)

  (defn #^ str placed [self #^ str name] (. (get self.state.placements name) worker))
  (defn #^ (| str None) surge [self #^ str name] (if (in name self.state.surges) (. (get self.state.surges name) worker) None))
  (defn #^ list names-for [self #^ str worker] (lfor s (jobs-for self.state worker) s.name)))


(defn #^ Coord running-writer []
  "書き手 w が atlas で動き Ready(active)。zeus も生きている。"
  (setv c (Coord))
  (c.call "POST" "/resources/Service" {"name" "w" "spec" (service)} :expect 201)
  (assert (= (c.placed "w") "atlas"))
  (c.beat "atlas" ["w"])
  (c.ready "atlas" "w" :role "active")
  (c.beat "zeus")
  c)


(deftest test-drain-surges-a-handoff-writer-then-moves-it-only-after-the-new-process-is-ready
  (setv c (running-writer))
  (setv view (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas"))
  ;; 並べる: 置き先は atlas のまま、zeus へ並べた置き先(世代 +1)。zeus の heartbeat の返事に w が載る。atlas にも載ったまま。
  (assert (= #((c.placed "w") (c.surge "w")) #("atlas" "zeus")) c.state.surges)
  (assert (in "w" (c.names-for "zeus")))
  (assert (in "w" (c.names-for "atlas")))
  (assert (= (get view "drain" "phase") "Draining") view)
  (assert (= (get view "drain" "moving") {"w" "zeus"}) view)
  (assert (not (get view "drain" "drained")))
  (assert (not (get view "ready")))
  ;; zeus の新しい process が起きただけ(準備できたの報告がまだ)では付け替えない。
  (c.beat "zeus" ["w"])
  (c.advance 5)
  (assert (= (c.placed "w") "atlas"))
  ;; 準備できていない(ready 偽)の報告でも付け替えない。
  (c.ready "zeus" "w" :ready False)
  (c.advance)
  (assert (= (c.placed "w") "atlas"))
  ;; standby の Ready → 付け替える。atlas の宣言から外れる(atlas の worker は次の拍で旧を止め、lease を返す)。
  (c.ready "zeus" "w")
  (c.advance)
  (assert (= (c.placed "w") "zeus"))
  (assert (is (c.surge "w") None))
  (assert (not-in "w" (c.names-for "atlas")))
  (assert (in "w" (c.names-for "zeus")))
  ;; 付け替えた先は並べた時の世代のまま(zeus の process は起こし直さない — その世代で Ready と数え続ける)。
  (assert (= (get (c.call "GET" "/resources/Service/w") "status" "ready") "Ready"))
  ;; 旧が止まる前は、atlas の上にまだ w が動いている = drain は終わっていない。
  (c.beat "atlas" ["w"])
  (assert (= (get (c.call "GET" "/workers/atlas") "drain" "remaining") ["w"]))
  ;; 旧が止まった報告 → drained。
  (c.beat "atlas" [])
  (setv view (c.call "GET" "/workers/atlas"))
  (assert (= #((get view "drain" "phase") (get view "drain" "drained")) #("Drained" True)) view)
  ;; 出来事の記録: 並べた・付け替えた。
  (setv notes (lfor e c.state.events :if (in "drain" e) (get e "drain")))
  (assert (any (gfor n notes (in "並べて置いた" n))) notes)
  (assert (any (gfor n notes (in "付け替えた" n))) notes))


(deftest test-no-target-means-no-surge-and-the-old-keeps-running-until-a-target-appears
  (setv c (running-writer))
  ;; zeus が沈黙(死んだ)。
  (c.advance 12)
  (c.beat "atlas" ["w"])
  (setv view (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas"))
  (assert (is (c.surge "w") None))
  (assert (= (c.placed "w") "atlas"))
  (assert (in "w" (c.names-for "atlas")))
  (assert (= (get view "drain" "phase") "Blocked") view)
  (assert (in "移す先が無い" (get view "drain" "blocked" "w")) view)
  ;; 時間が経っても旧を外さない(空白を作らない)。
  (for [_ (range 30)] (c.beat "atlas" ["w"]) (c.advance))
  (assert (= (c.placed "w") "atlas"))
  ;; zeus が戻ると次の拍で並べる。
  (c.beat "zeus")
  (assert (= (c.surge "w") "zeus"))
  (assert (= (get (c.call "GET" "/workers/atlas") "drain" "phase") "Draining")))


(deftest test-a-target-that-is-draining-or-not-eligible-is-not-used
  (setv c (running-writer))
  ;; zeus も drain 中 → 移す先が無い。
  (c.call "POST" "/workers/zeus/drain" {} :actor "drain@zeus")
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (assert (is (c.surge "w") None))
  (assert (= (get (c.call "GET" "/workers/atlas") "drain" "phase") "Blocked"))
  ;; Mac(kind mac)は条件を満たさないので移す先にならない。
  (c.beat "newmac" :labels {"kind" "mac"})
  (c.advance)
  (assert (is (c.surge "w") None))
  (c.call "DELETE" "/workers/zeus/drain" :actor "c-test")
  (assert (= (c.surge "w") "zeus")))


(deftest test-cancelling-the-drain-drops-the-surge-and-keeps-the-old
  (setv c (running-writer))
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (c.beat "zeus" ["w"])
  (setv view (c.call "DELETE" "/workers/atlas/drain" :actor "c-test"))
  (assert (not (get view "draining")))
  (assert (get view "ready"))
  (assert (is (c.surge "w") None))
  (assert (= (c.placed "w") "atlas"))
  (assert (not-in "w" (c.names-for "zeus")))
  ;; 並べた先の process が Ready を報告しても(取り消しの後)付け替えない。
  (c.advance)
  (assert (= (c.placed "w") "atlas")))


(deftest test-a-recreate-service-is-stopped-and-moved-only-when-another-worker-can-take-it
  (setv c (Coord))
  (c.call "POST" "/resources/Service" {"name" "r" "spec" (service {"update" "recreate"})} :expect 201)
  (assert (= (c.placed "r") "atlas"))
  (c.beat "atlas" ["r"])
  ;; zeus が死んでいる間の drain: 移す先が無いので外さない。
  (c.advance 12)
  (c.beat "atlas" ["r"])
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (assert (= (c.placed "r") "atlas"))
  (assert (= (get (c.call "GET" "/workers/atlas") "drain" "phase") "Blocked"))
  ;; zeus が戻る → 外す(止めて移す)。旧が止め終えるまで zeus には置かない。
  (c.beat "zeus")
  (assert (not-in "r" c.state.placements))
  (assert (not-in "r" (c.names-for "atlas")))
  (c.beat "atlas" ["r"])
  (assert (not-in "r" c.state.placements))
  (c.beat "atlas" [])
  (assert (= (c.placed "r") "zeus")))


(deftest test-a-draining-worker-gets-no-new-placements-or-tasks
  (setv c (Coord))
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (c.call "POST" "/resources/Service" {"name" "n" "spec" (service)} :expect 201)
  (assert (= (c.placed "n") "zeus"))
  (c.call "POST" "/tasks" {"env" "m:e" "blob" "x" "revision" "r1" "versions" {} "requires" K3S} :actor "c-test")
  (assert (= (. (get c.state.tasks "t1") worker) "zeus"))
  ;; drain 中の worker しか置ける先が無い task は失敗にせず待つ。
  (c.call "POST" "/workers/zeus/drain" {} :actor "drain@zeus")
  (c.call "POST" "/tasks" {"env" "m:e" "blob" "x" "revision" "r1" "versions" {} "requires" K3S} :actor "c-test")
  (assert (= (. (get c.state.tasks "t2") phase) "queued")))


(deftest test-a-new-boot-or-the-deadline-ends-the-drain
  (setv c (running-writer))
  (c.call "POST" "/workers/zeus/drain" {"ttlSeconds" 30} :actor "drain@zeus")
  (assert (= (. (get c.state.drains "zeus") boot) "b1"))
  ;; 同じ世代の heartbeat では解けない。頼み直しは始めた時刻を変えず、期限だけ延ばす。
  (setv since (. (get c.state.drains "zeus") since-ms))
  (c.advance 10)
  (c.beat "zeus")
  (c.call "POST" "/workers/zeus/drain" {"ttlSeconds" 30} :actor "drain@zeus")
  (assert (= (. (get c.state.drains "zeus") since-ms) since))
  (assert (= (. (get c.state.drains "zeus") until-ms) (+ c.now 30000)))
  (assert (not (get (c.call "GET" "/workers/zeus") "ready")))
  ;; 作り直した Pod の worker(別の世代)の heartbeat → 解ける。Ready。
  (c.beat "zeus" :boot "b2")
  (assert (not-in "zeus" c.state.drains))
  (assert (get (c.call "GET" "/workers/zeus") "ready"))
  ;; 期限を過ぎた drain は調停が消す。
  (c.call "POST" "/workers/zeus/drain" {"ttlSeconds" 5} :actor "drain@zeus")
  (c.advance 6)
  (assert (not-in "zeus" c.state.drains))
  ;; 出来事の記録に誰が drain を頼んだかが残る(Worker の資源の status.drain)。
  (assert (any (gfor e c.state.audit (and (= (get e "kind") "Worker") (= (get e "actor") "drain@zeus"))))))


(deftest test-the-old-pod-drain-does-not-drain-the-new-generation-of-the-same-name
  ;; 2026-09-27 04:44 JST の実測: Deployment の worker の Pod を消すと、旧 Pod は preStop で drain を頼み直し
  ;; ながら heartbeat を送り続け、新 Pod の worker は同じ名で名乗る。drain の印が約 2 秒ごとに付いて(旧の drain)消えた(新の
  ;; heartbeat)。旧い世代の頼みと heartbeat は、新しい世代の置き場を止めない。
  (setv c (running-writer))
  (c.beat "zeus" :boot "old")
  (c.call "POST" "/workers/zeus/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@zeus")
  (assert (= (. (get c.state.drains "zeus") boot) "old"))
  ;; 新 Pod の worker が名乗る → 旧い世代の drain は解ける(今までの規則)。
  (c.advance 11)
  (c.beat "zeus" :boot "new")
  (assert (not-in "zeus" c.state.drains))
  ;; 旧 Pod は drain を頼み直し、heartbeat を送り続ける。どちらも新しい世代に drain を付け直さない。
  (for [_ (range 3)]
    (c.advance 1)
    (setv view (c.call "POST" "/workers/zeus/drain" {"ttlSeconds" 150 "boot" "old"} :actor "drain@zeus"))
    (assert (not-in "zeus" c.state.drains) "旧い世代の drain の頼みが新しい世代に drain を付けた")
    (setv old-reply (c.beat "zeus" :boot "old"))
    (assert (not-in "zeus" c.state.drains) c.state.drains)
    ;; 旧 Pod への答え: 退いた世代で、その世代に置いた task は無い = 空いた(preStop はここで終わる)。
    (assert (get view "drain" "superseded") view)
    (assert (get view "drain" "drained") view)
    (assert (get old-reply "superseded"))
    (assert (get old-reply "draining"))
    (setv new-reply (c.beat "zeus" :boot "new"))
    (assert (not (get new-reply "draining")))
    (assert (get (c.call "GET" "/workers/zeus") "ready")))
  ;; 新しい世代には新しい task を置ける。
  (c.call "POST" "/tasks" {"env" "m:e" "blob" "x" "revision" "r1" "versions" {} "requires" {"kind" "k3s"}}
          :actor "c-test")
  (assert (= #((. (get c.state.tasks "t1") phase) (. (get c.state.tasks "t1") worker)) #("assigned" "zeus"))))


(deftest test-the-old-pod-drain-waits-for-the-detached-tasks-of-its-own-generation
  ;; 退いた世代の preStop は、その世代に置いた切り離した task が終わるまで待つ(走らせ直さない task を途中で殺さない)。
  (setv c (Coord #("zeus")))
  (c.beat "zeus" :boot "old")
  (c.call "PUT" "/detached/job-24" {"env" "m:e" "blob" "B" "versions" {} "revision" "r" "requires" K3S "leaseSeconds" 60}
          :actor "c-test")
  (setv task (next (gfor t (.values c.state.tasks) :if (= t.key "job-24") t)))
  (assert (= #(task.worker task.boot) #("zeus" "old")))
  (c.advance 1)
  (c.beat "zeus" :boot "new")
  (setv view (c.call "POST" "/workers/zeus/drain" {"boot" "old"} :actor "drain@zeus"))
  (assert (get view "drain" "superseded") view)
  (assert (not (get view "drain" "drained")) view)
  (assert (= (get view "drain" "remaining") [(+ "task/" task.id)]))
  ;; 新しい世代には drain を付けない。
  (assert (not-in "zeus" c.state.drains))
  (c.call "POST" "/heartbeat" {"name" "zeus" "labels" K3S "capacity" 10 "versions" {} "boot" "old"
                               "statuses" [{"name" (+ "task/" task.id) "phase" "finished" "result" "R" "detail" ""}]}
          :actor None)
  (assert (= (. (get c.state.tasks task.id) phase) "finished"))
  (assert (get (c.call "POST" "/workers/zeus/drain" {"boot" "old"} :actor "drain@zeus") "drain" "drained")))


(deftest test-drain-requests-need-an-actor-and-a-known-worker
  (setv c (Coord))
  (c.call "POST" "/workers/atlas/drain" {} :actor None :expect 400)
  (c.call "POST" "/workers/ghost/drain" {} :expect 404)
  (c.call "GET" "/workers/ghost" :expect 404)
  (c.call "POST" "/workers/atlas/drain" {"ttlSeconds" 0} :expect 400)
  (c.call "POST" "/workers/atlas/drain" {"ttlSeconds" True} :expect 400)
  (assert (get (c.call "GET" "/workers/atlas") "ready")))


(deftest test-drains-and-surges-survive-a-restart-and-an-old-store-reads-as-empty
  (setv c (running-writer))
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (setv kv (full-kv c.state))
  (assert (in (+ DRAIN "atlas") kv))
  (assert (in (+ SURGE "w") kv))
  (setv again (state-from-kv kv c.now))
  (assert (= again.drains c.state.drains))
  (assert (= again.surges c.state.surges))
  ;; drain の鍵を知らない置き場(2026-09-25 より前)は空として読む。
  (setv old (state-from-kv (dfor #(k v) (.items kv) :if (not (or (.startswith k DRAIN) (.startswith k SURGE))) k v) c.now))
  (assert (= #(old.drains old.surges) #({} {})))
  (assert (= (. (get old.placements "w") worker) "atlas"))
  ;; GET /state に drain の進みと並べた置き先が載る。
  (setv view (c.call "GET" "/state"))
  (assert (= (get view "drains" "atlas" "moving") {"w" "zeus"}) (get view "drains"))
  (assert (= (get view "surges" "w" "worker") "zeus")))


;; --- preStop と readinessProbe の Program(drain_client)----------------------------------------------

(defhandler scripted-coordinator [#^ list answers #^ list calls]
  ;; 答えの台本を前から 1 つずつ返す(尽きたら最後の答えを繰り返す)。送られた要求を calls に積む。
  (CoordinatorCall [method path body]
    (.append calls #(method path body))
    (resume (if (> (len answers) 1) (.pop answers 0) (get answers 0)))))

(defn #^ dict drained [#^ bool flag] {"status" 200 "body" {"ready" False "drain" {"drained" flag}}})

(deftest test-await-drained-asks-again-until-drained
  (setv calls [] clock (SimClock))
  (<- result ((sim-time-handler :clock clock)
              ((scripted-coordinator [{"error" "ConnectError"} {"status" 503 "body" {}} (drained False) (drained True)] calls)
               (await-drained "atlas" 90.0 2.0))))
  (assert (= (get result "outcome") "drained") result)
  (assert (= (len calls) 4))
  ;; 頼み直すたびに期限つき(上限 + 余裕)で頼む。間は 2 秒(仮想の時計)。
  (assert (all (gfor #(m p b) calls (and (= m "POST") (= p "/workers/atlas/drain") (= (get b "ttlSeconds") 150.0)))))
  (assert (= (get result "elapsed") 6.0) result))

(deftest test-await-drained-gives-up-at-the-deadline-and-stops-on-unknown-worker
  (<- result ((sim-time-handler :clock (SimClock))
              ((scripted-coordinator [(drained False)] []) (await-drained "atlas" 10.0 2.0))))
  (assert (= (get result "outcome") "timeout") result)
  (assert (>= (get result "elapsed") 10.0))
  (<- gone ((sim-time-handler :clock (SimClock))
            ((scripted-coordinator [{"status" 404 "body" {}}] []) (await-drained "ghost" 10.0 2.0))))
  (assert (= (get gone "outcome") "unknown-worker")))

(deftest test-drain-outcome-and-readiness-are-pure
  (assert (is (drain-outcome {"error" "x"} 5.0 90.0) None))
  (assert (= (drain-outcome {"status" 400 "body" {}} 0.0 90.0) "refused"))
  (assert (= (drain-outcome {"error" "x"} 90.0 90.0) "timeout"))
  (assert (ready-of {"status" 200 "body" {"ready" True "boot" "b2"}} "b2"))
  (assert (not (ready-of {"status" 200 "body" {"ready" False "boot" "b2"}} "b2")))
  (assert (not (ready-of {"error" "ConnectError"} "b2")))
  ;; 前の Pod の世代(同じ名)を見ている間・自分の世代をまだ知らない間は Ready でない。
  (assert (not (ready-of {"status" 200 "body" {"ready" True "boot" "b1"}} "b2")))
  (assert (not (ready-of {"status" 200 "body" {"ready" True "boot" "b1"}} None))))

(defhandler coordinator-of [#^ Coord coord]
  ;; 読みの要求を Coord の純粋な判断へそのまま渡す(状態は変えない)。
  (CoordinatorCall [method path body]
    (setv #(_ status reply) (respond coord.state (Request method path {} body :actor "drain@atlas") coord.now T))
    (resume {"status" status "body" reply})))

(deftest test-worker-ready-reads-the-coordinator-view
  (setv c (Coord))
  (<- before ((coordinator-of c) (worker-ready "atlas" "b1")))
  (assert before)
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (<- during ((coordinator-of c) (worker-ready "atlas" "b1")))
  (assert (not during)))


(deftest test-the-old-pod-prestop-sends-its-boot-and-ends-once-the-new-generation-has-named-itself
  ;; preStop の Program(await-drained)は頼みに自分の世代を載せる。同じ名の新しい世代が名乗った後は、退いた世代の答え
  ;; (その世代の task が無い = drained)で終わる — 新しい世代に drain を付けて 90 秒待たない。
  (setv calls [])
  (<- sent ((sim-time-handler :clock (SimClock))
            ((scripted-coordinator [(drained True)] calls) (await-drained "atlas" 90.0 2.0 "old"))))
  (assert (= (get sent "outcome") "drained"))
  (assert (= (get (get (get calls 0) 2) "boot") "old"))
  (setv c (Coord))
  (c.beat "atlas" :boot "old")
  (c.beat "atlas" :boot "new")
  (<- result ((sim-time-handler :clock (SimClock)) ((coordinator-of c) (await-drained "atlas" 90.0 2.0 "old"))))
  (assert (= (get result "outcome") "drained") result)
  (assert (= (get result "elapsed") 0.0) result)
  (assert (get result "last" "body" "drain" "superseded")))


(deftest test-a-new-pod-is-not-ready-while-the-coordinator-still-sees-the-previous-pod
  ;; 2026-09-25 の配備の実弾: DaemonSet は前の Pod の終了を待たずに同じ node へ新しい Pod を作る。新しい Pod の worker は node の dir の
  ;; lock を待つ間 heartbeat を送らないので、coordinator の見る "atlas" はまだ前の Pod(世代 b1)。前の Pod が preStop で drain を
  ;; 頼む前の数秒に新しい Pod の readinessProbe が撃つと、名だけで判じる形は Ready と答えて DaemonSet を次の node へ進めた。
  (setv c (Coord))
  (c.beat "atlas" :boot "b1")
  (<- early ((coordinator-of c) (worker-ready "atlas" "b2")))
  (assert (not early) "前の Pod の生存を自分の物と読んでいる")
  ;; 新しい Pod の worker が heartbeat を送り始めたら(前の Pod は終わっている — node の dir の lock)Ready。
  (c.beat "atlas" :boot "b2")
  (<- late ((coordinator-of c) (worker-ready "atlas" "b2")))
  (assert late))


(defn #^ None test-the-worker-writes-its-boot-where-the-readiness-probe-reads-it [#^ Path tmp-path #^ pytest.MonkeyPatch monkeypatch]
  ;; worker の世代は起動の時に Pod の中の file(DOEFF_WORKER_BOOT_FILE)へ書かれ、readinessProbe の入口(drain_main.read-boot)が
  ;; 同じ値を読む — 書く口と読む口の綴りが割れると probe は永久に NotReady になる。
  (setv path (/ tmp-path "doeff-worker-boot"))
  (assert (is (read-boot (str path)) None) "起動の前(file が無い)は世代を知らない")
  (.setenv monkeypatch "DOEFF_WORKER_BOOT_FILE" (str path))
  (setv link (CoordinatorLink "http://127.0.0.1:1" "atlas" {} 1 30000 :task-dir (str (/ tmp-path "tasks"))))
  (assert (= (read-boot (str path)) link.boot)))


(deftest test-the-heartbeat-reply-names-whether-the-worker-is-draining
  ;; worker は返事の draining を Pod の中の ready の file へ写す(readinessProbe は sh でそれを読む)。
  (setv c (Coord))
  (setv before (c.beat "atlas"))
  (assert (is (get before "draining") False) before)
  (c.call "POST" "/workers/atlas/drain" {} :actor "drain@atlas")
  (setv during (c.beat "atlas"))
  (assert (is (get during "draining") True) during))


(defn #^ None test-the-readiness-probe-reads-the-file-the-worker-writes [#^ Path tmp-path]
  ;; 書く口(handlers.write-ready-file)と読む口(boot.sh の ROLE=ready — sh だけ・hy を起こさない)の往復。
  (setv path (str (/ tmp-path "doeff-worker-ready"))
        boot-sh (str (/ (. (Path __file__) parent parent) "deploy" "boot.sh")))
  (defn #^ int probe [#^ dict [extra None]]
    (. (subprocess.run ["sh" boot-sh] :env (| {"PATH" (os.environ.get "PATH" "") "ROLE" "ready" "DOEFF_WORKER_READY_FILE" path}
                                              (or extra {}))
                       :capture-output True) returncode))
  (assert (= (probe) 1) "worker がまだ書いていない(lock 待ち)は NotReady")
  (write-ready-file path False)
  (assert (= (probe) 0))
  (write-ready-file path True)
  (assert (= (probe) 1) "drain 中は NotReady")
  (write-ready-file path False)
  (os.utime path #(1 1))
  (assert (= (probe {"READY_MAX_AGE" "30"}) 1) "heartbeat が途絶えた(file が古い)は NotReady"))
