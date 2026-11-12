;;; machine の用途の区画の宣言(資源 MachineCompartments — ADR-DOE-CLUSTER-005 R1〜R3)の検。
;;;   - coordinator: 資源の口が行の中と行をまたぐ規則で検めて持ち、版つきで書き直し・消せる。heartbeat の返事は worker の持つ区画を写し
;;;     (名指されない worker は空・名乗っていない worker は欄なし)、GET /state は machine ごとの区画と和、worker ごとの持つ区画を見せる。
;;;     保存(durable の KV と旧い state file)を往復する。
;;;   - worker: 持つ区画を層の memory.max(守る区画は memory.min も)に、値が変わった時だけ書く。区画の持つ jobs の層は、層の宣言
;;;     (WORKER_CGROUP_LAYOUT の memory.max)と見張りの掛け直しが在っても区画の値 1 つに決まる(ADR-DOE-CLUSTER-005 の 1 つの宣言の law)。
;;;     失敗ケース(区画の無い前の形): 見張りが測りから jobs の層の memory.max を拍ごとに書き直し、宣言の値は残らない。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import pytest)
(import dataclasses [replace])
(import doeff [with-handlers])
(import doeff_core_effects [FileFailed SlogEffect WriteText HttpRequest HttpResponse])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster [ClusterTiming WorkerInfo ClusterState Refused])
(import doeff_cluster._coordinator.intent.request_bodies [ResourceBody])
(import doeff_cluster._coordinator.core.resource_policy [create-resource update-resource delete-resource stamp get-resource])
(import doeff_cluster._coordinator.core.cluster_policy [heartbeat-reply state-view resource-version-of])
(import doeff_cluster._coordinator.protocol.replies [heartbeat-reply-json state-view-body])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json])
(import doeff_cluster._shared.intent.compartment_model [HeldCompartment])
(import doeff_cluster._shared.core.compartment_rules [held-compartments-of])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [layer-memory-max])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings EvictionCell eviction-noted COMPARTMENTS-LOG])
(import tests.state_rows [table-from-rows])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
;; machine node-h の宣言: worker w-h の tasks(jobs の層)と services(守る区画)と、doeff の外。
(val NODE-H {"compartments" [{"use" "tasks" "worker" "w-h" "layer" "jobs" "memoryMiB" 8192 "cpuMillis" 6000}
                             {"use" "services" "worker" "w-h" "layer" "services" "memoryMiB" 4096 "memoryLimitMiB" 6144 "cpuMillis" 2000}
                             {"use" "outside" "memoryMiB" 16384 "cpuMillis" 4000}]})


(defk declared [spec]
  {:pre [(: spec dict)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker w-h と w-x(どの行も名指さない)を名簿に持ち、machine node-h の区画 spec を資源の口(POST /resources/MachineCompartments と同じ
   create-resource — 版は stamp が振る)で宣言した状態を作るため。"
  (<- workers object (table-from-rows {"w-h" (WorkerInfo "w-h" #("net") 0) "w-x" (WorkerInfo "w-x" #("net") 0)}))
  (val base (ClusterState :workers workers))
  (stamp base (create-resource base "MachineCompartments" (ResourceBody :name "node-h" :spec spec) "site" 0) "site" 0 T))


(deftest test-the-declaration-is-copied-to-the-named-worker-only
  (<- s ClusterState (declared NODE-H))
  (assert (is-not (resource-version-of s "MachineCompartments/node-h") None))
  ;; w-h は 2 つの区画を層の名の順に受け、w-x は空(区画を持たない)、名乗っていない worker は欄なし。
  (<- named object (heartbeat-reply s "w-h" T))
  (<- unnamed object (heartbeat-reply s "w-x" T))
  (<- unknown object (heartbeat-reply s "w-new" T))
  (assert (= named.compartments
             #((HeldCompartment :use "tasks" :layer "jobs" :memory-mib 8192 :memory-limit-mib 8192 :cpu-millis 6000)
               (HeldCompartment :use "services" :layer "services" :memory-mib 4096 :memory-limit-mib 6144 :cpu-millis 2000)))
          named.compartments)
  (assert (= unnamed.compartments #()))
  (assert (is unknown.compartments None))
  ;; 返事の JSON を worker の読みで読み直すと同じ区画。欄なしの返事は compartments を書かない。
  (<- spelled dict (heartbeat-reply-json named))
  (<- read (| tuple None) (held-compartments-of (json.loads (json.dumps (get spelled "compartments")))))
  (assert (= read named.compartments) read)
  (<- bare dict (heartbeat-reply-json unknown))
  (assert (not-in "compartments" bare))
  ;; GET /state: machine ごとの宣言と和、worker ごとの持つ区画。
  (<- view object (state-view s 0 T))
  (<- body dict (state-view-body view))
  (val machine (get body "machineCompartments" "node-h"))
  (assert (= #((get machine "memoryMiB") (get machine "memoryLimitMiB") (get machine "cpuMillis")) #(28672 30720 12000)) machine)
  (assert (= (get machine "compartments") (get NODE-H "compartments")) machine)
  (assert (= (sorted (get body "workerCompartments")) ["w-h" "w-x"]) (get body "workerCompartments"))
  (assert (= (len (get body "workerCompartments" "w-h")) 2))
  (assert (= (get body "workerCompartments" "w-x") [])))


(deftest test-the-resource-api-checks-rows-and-versions
  (<- s ClusterState (declared NODE-H))
  ;; 行の中の規則: tasks に memoryLimitMiB・1 つの worker に tasks 2 つ・知らない use。
  (for [bad [{"compartments" [{"use" "tasks" "worker" "w-y" "layer" "jobs" "memoryMiB" 1 "memoryLimitMiB" 2 "cpuMillis" 0}]}
             {"compartments" [{"use" "tasks" "worker" "w-y" "layer" "a" "memoryMiB" 1 "cpuMillis" 0}
                              {"use" "tasks" "worker" "w-y" "layer" "b" "memoryMiB" 1 "cpuMillis" 0}]}
             {"compartments" [{"use" "cache" "worker" "w-y" "layer" "a" "memoryMiB" 1 "cpuMillis" 0}]}]]
    (with [caught (pytest.raises Refused)]
      (create-resource s "MachineCompartments" (ResourceBody :name "node-y" :spec bad) "site" 0))
    (assert (= caught.value.status 400) caught.value.body))
  ;; 行をまたぐ規則: w-h は node-h の下に在るので node-z の下に書けない。
  (with [caught (pytest.raises Refused)]
    (create-resource s "MachineCompartments"
                     (ResourceBody :name "node-z" :spec {"compartments" [{"use" "tasks" "worker" "w-h" "layer" "other" "memoryMiB" 1 "cpuMillis" 0}]})
                     "site" 0))
  (assert (= caught.value.status 400) caught.value.body)
  ;; 書き直しは版つき(古い版は 409)。書き直した値が次の返事に写る。
  (val version (resource-version-of s "MachineCompartments/node-h"))
  (val smaller {"compartments" [{"use" "tasks" "worker" "w-h" "layer" "jobs" "memoryMiB" 4096 "cpuMillis" 6000}]})
  (with [caught (pytest.raises Refused)]
    (update-resource s "MachineCompartments" "node-h" (ResourceBody :name "node-h" :spec smaller :resource-version (+ version 99)) "site"))
  (assert (= caught.value.status 409) caught.value.body)
  (val rewritten (stamp s (update-resource s "MachineCompartments" "node-h" (ResourceBody :name "node-h" :spec smaller :resource-version version) "site")
                        "site" 0 T))
  (<- reply object (heartbeat-reply rewritten "w-h" T))
  (assert (= (tuple (gfor h reply.compartments h.memory-limit-mib)) #(4096)) reply.compartments)
  ;; 消すと、名指されていた worker は空の区画を受ける。
  (val gone (stamp rewritten (delete-resource rewritten "MachineCompartments" "node-h" {} 0 T) "site" 0 T))
  (<- after object (heartbeat-reply gone "w-h" T))
  (assert (= after.compartments #())))


(deftest test-the-declaration-survives-both-stores
  (<- s ClusterState (declared NODE-H))
  (<- kv dict (full-kv s))
  (assert (in "compartments/node-h" kv) (sorted kv))
  (<- from-kv ClusterState (state-from-kv kv 0))
  (<- data dict (state-to-json s))
  (<- from-file ClusterState (state-from-json (json.loads (json.dumps data)) 0))
  (for [again [from-kv from-file]]
    (assert (= (.row again.machine-compartments "node-h") (.row s.machine-compartments "node-h")) again.machine-compartments)))


;; --- worker: 区画を cgroup の層へ写す ---------------------------------------------------------------

(val GIB (* 1024 MIB))
;; 層の宣言は jobs に memory.max=10752M を持つ(区画の前の形 — 見張りの掛け直しの静的な上限)。
(val LAYOUT "worker:memory.min=256M,cpu.weight=1000 services:memory.min=256M,cpu.weight=1000 jobs:memory.max=10752M,cpu.weight=100")
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val OWN (+ PODS "/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-1.scope"))
(val JOBS (+ OWN "/jobs"))
(val SERVICES (+ OWN "/services"))
(val TOKEN "/sa/token")
(val NODE-URL "https://api.test/api/v1/nodes/node-a")
(val STAT "anon {}\nfile 0\ninactive_file 0\n")
(val PODS-USED (* 12 GIB))
(val JOBS-USED (* 2 GIB))
(val HELD #((HeldCompartment :use "tasks" :layer "jobs" :memory-mib 4096 :memory-limit-mib 4096 :cpu-millis 4000)
            (HeldCompartment :use "services" :layer "services" :memory-mib 2048 :memory-limit-mib 3072 :cpu-millis 1000)))


(defhandler node-a-api []
  "node-a の Node の宣言を答える偽の API server(追い出しの線の読み)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (HttpRequest [method url]
    :when (= url NODE-URL)
    (val text (json.dumps {"status" {"capacity" {"memory" "29662296Ki"} "allocatable" {"memory" "20749400Ki"}}}))
    (resume (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))))


(defeffect ReadLayerWrites
  "層の memory.max・memory.min への書きを、書いた順に #(path 値) で問う — テストだけの effect。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler layer-writes-kept []
  "層の memory の上限の file への書きを溜め(書き自体は外の模擬の file へ渡す)、log の行の名も溜めて ReadLayerWrites に答える答え手。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var writes #())
  (WriteText [path text]
    :when (or (.endswith path "/memory.max") (.endswith path "/memory.min"))
    (:= writes (+ writes #(#(path (int text)))))
    (<- answer (| FileFailed None) effect)
    (resume answer))
  (SlogEffect []
    (:= writes (+ writes #(#("log" effect.msg))))
    (resume None))
  (ReadLayerWrites []
    (resume writes)))


(defk layer-files []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の cgroup の file(kubepods・root・jobs と services の層 — page cache 0)を作るため。"
  (val files #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str (- (* 29662296 1024) (* 7 GIB)))))
               (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str PODS-USED)))
               (MemoryFile :path (+ PODS "/memory.stat") :content (.encode (.format STAT PODS-USED)))
               (MemoryFile :path (+ HOST "/memory.stat") :content (.encode (.format STAT (+ PODS-USED (* 2 GIB)))))
               (MemoryFile :path (+ JOBS "/memory.current") :content (.encode (str JOBS-USED)))
               (MemoryFile :path (+ JOBS "/memory.stat") :content (.encode (.format STAT JOBS-USED)))
               (MemoryFile :path (+ JOBS "/memory.max") :content b"max\n")
               (MemoryFile :path (+ SERVICES "/memory.max") :content b"max\n")
               (MemoryFile :path TOKEN :content b"token\n")))
  (val parents (sfor f files d (gfor i (range 2 (+ 1 (len (.split f.path "/")))) (.join "/" (cut (.split f.path "/") 0 i)))
                     :if (!= d f.path) d))
  (MemoryFiles :dirs (tuple (sorted parents)) :files files))


(defk ticked [held]
  {:pre [(: held (| tuple None))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの回の頭(eviction-noted)を 5 秒おきに 3 回まわすため(held = 持つ区画 — None は区画を受けていない前の形)。2 回目の後に区画が
   変わらない返事をもう 1 度受ける。答え = #(書きの列 最後の jobs の memory.max)。"
  (<- cap (| int None) (layer-memory-max LAYOUT "jobs"))
  (val settings (EvictionSettings :api "https://api.test" :token-file TOKEN :node "node-a" :pods-dir PODS :host-root HOST :jobs-dir JOBS
                                  :jobs-cap-bytes cap :layers-dir OWN))
  (val cell (EvictionCell))
  (when (is-not held None)
    (setv cell.compartments held))
  (for [now-ms #(5000 10000 15000)]
    (when (and (is-not held None) (= now-ms 15000))
      (setv cell.compartments (tuple held)))
    (<- (eviction-noted settings cell True now-ms)))
  (<- writes tuple (ReadLayerWrites))
  (<- files MemoryFiles (ReadMemoryFiles))
  #(writes (.decode (. (next (gfor f files.files :if (= f.path (+ JOBS "/memory.max")) f)) content) "utf-8")))


(defk run-ticks [held]
  {:pre [(: held (| tuple None))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の file と Node の宣言の上で ticked を回すため。"
  (<- files MemoryFiles (layer-files))
  (<- answer tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (memory-file-handler files) (node-a-api)
                                   (layer-writes-kept)]
                     (ticked held)))
  answer)


(deftest test-a-held-layer-limit-has-one-declaration
  ;; 層の宣言(jobs:memory.max=10752M)と見張りの掛け直しが在っても、区画の持つ jobs の層の上限は区画の値 4096 MiB 1 つに決まり、見張りは
  ;; その層を書かない。守る区画 services は memory.max = memoryLimitMiB と memory.min = memoryMiB。書きは最初の回の 1 度だけ(値が同じ
  ;; 返事を受け直しても書かない)。
  (<- answer tuple (run-ticks HELD))
  (val writes (get answer 0))
  (val last (get answer 1))
  (val limits (tuple (gfor #(path value) writes :if (!= path "log") #(path value))))
  (assert (= limits #(#((+ JOBS "/memory.max") (* 4096 MIB)) #((+ SERVICES "/memory.max") (* 3072 MIB))
                      #((+ SERVICES "/memory.min") (* 2048 MIB))))
          limits)
  (assert (= (int last) (* 4096 MIB)) last)
  (assert (= (.count (lfor #(path value) writes :if (= path "log") value) COMPARTMENTS-LOG) 1) writes))


(deftest test-without-compartments-the-watch-rewrites-the-jobs-layer-every-round
  ;; 失敗ケース(区画の無い前の形): 見張りが jobs の層の memory.max を回ごとに測りから書き直し、宣言の 1 つの値は無い。
  (<- answer tuple (run-ticks None))
  (val writes (get answer 0))
  (val last (get answer 1))
  (val jobs-writes (lfor #(path value) writes :if (= path (+ JOBS "/memory.max")) value))
  (assert (= (len jobs-writes) 3) writes)
  (assert (!= (int last) (* 4096 MIB)) last))
