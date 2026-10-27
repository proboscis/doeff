;; worker の枠(capacity・task-reserve)を枠の file から読み直す(card ki-6828eda880ce)。
;; 枠を起動の引数だけで受けると、値を変えるたびに worker の Pod の template が変わって作り直され、走っている job と task が切れた(常駐の job を
;; 1 つ足すたびに枠を変える — 2026-10-25 の差し戻し「枠の変更に再起動を要する設計を治す。p0」)。
;;   (1) 枠の file の自分の行を読み、JSON でない・行が無い・整数でない・範囲の外は malformed と訳にする
;;   (2) coordinator への口は file を読み直して heartbeat の capacity・taskReserve を file の値にし、file が無い・崩れた時は前に名乗った値の
;;       まま roomFile で状態を名乗る。file を読まない worker は roomFile を名乗らない
;;   (3) 起動の入口は file の値を先に使い、読めなければ引数、引数の task-reserve も無ければ断る
;;   (4) coordinator は heartbeat の新しい枠で Worker の行を書き直し、枠を使っている数より下げても置いた job と task を退かさず、新しく
;;       置かないだけ。roomFile が ok でない間だけ Worker の status に出す
;; 失敗ケース = 起動の時の値を名乗り続ける(file の変更が効かない)・崩れた file で既定の値や 0 を名乗る・下げた枠で走っている job を外す。
(require doeff-hy.macros [deff defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import dataclasses [replace])
(import httpx)
(import pytest)
(import doeff_cluster.coordinator.protocol.cluster_json [naming-from-json])
(import doeff [run with-handlers])
(import doeff_core_effects.os_file [os-file-handler])
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.worker.core.room_rules [RoomReading RoomState WorkerRoom room-of-text room-read-due room-line])
(import doeff_cluster.worker.entry.main [boot-room boot-warm-services])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterJob ClusterState Placement WorkerInfo ComponentVersion TaskRecord
                                                       TaskUnplacedKind])
(import doeff_cluster.coordinator.core.cluster_policy [register-heartbeat load-of job-room-of task-room-of task-unplaced-text
                                                       jobs-placed unplaced-jobs])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.core.resource_policy [worker-row])
(import tests.state_rows [table-from-rows])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val ROOMS {"zeus" {"capacity" 39 "taskReserve" 6} "atlas" {"capacity" 12 "taskReserve" 3}})


(deftest test-the-room-file-names-the-row-of-the-worker
  (<- zeus RoomReading (room-of-text (json.dumps ROOMS) "zeus"))
  (assert (= zeus (RoomReading :state RoomState.OK :room (WorkerRoom :capacity 39 :task-reserve 6) :reason "")) zeus)
  ;; 崩れた形はどれも malformed で枠を持たない(既定の値や 0 で埋めない)。
  (val broken [["{" "JSON でない"]
               ["[1]" "JSON の object でない"]
               [(json.dumps ROOMS) "worker eos の行が無い"]
               [(json.dumps {"eos" {"capacity" "3" "taskReserve" 0}}) "worker eos の行の capacity・taskReserve が整数でない"]
               [(json.dumps {"eos" {"capacity" 3 "taskReserve" True}}) "worker eos の行の capacity・taskReserve が整数でない"]
               [(json.dumps {"eos" {"capacity" 3}}) "worker eos の行の capacity・taskReserve が整数でない"]
               [(json.dumps {"eos" {"capacity" 3 "taskReserve" 4}}) "worker eos の taskReserve 4 が 0 以上 capacity 3 以下でない"]])
  (for [#(text reason) broken]
    (<- read RoomReading (room-of-text text "eos"))
    (assert (= read (RoomReading :state RoomState.MALFORMED :room None :reason reason)) #(text read))))


(deftest test-the-room-file-row-names-pools-and-memory-mib
  ;; card ki-ced3c45980a5: 行の省ける欄 pools(置ける組の印 pool-…)と memoryMiB(常駐の job の memory の予算)。
  ;; 失敗ケース = 機体の印 host- や導く能力の名を組の印として名乗る・崩れた欄を黙って外して残りを名乗る・memoryMiB の真偽や負の数を受ける。
  (<- atlas RoomReading (room-of-text (json.dumps {"atlas" {"capacity" 13 "taskReserve" 3 "pools" ["pool-b" "pool-a" "pool-b"]
                                                            "memoryMiB" 16384}})
                                      "atlas"))
  (assert (= atlas (RoomReading :state RoomState.OK :reason ""
                                :room (WorkerRoom :capacity 13 :task-reserve 3 :pools #("pool-a" "pool-b") :memory-mib 16384)))
          atlas)
  ;; 欄の無い行は組の印も予算も持たない(前と同じ枠)。
  (<- plain RoomReading (room-of-text (json.dumps ROOMS) "zeus"))
  (assert (= plain.room (WorkerRoom :capacity 39 :task-reserve 6 :pools #() :memory-mib None)) plain)
  (val row (fn [extra] (json.dumps {"eos" (| {"capacity" 3 "taskReserve" 0} extra)})))
  (val broken [[{"pools" "pool-a"} "worker eos の行の pools が列でない: 'pool-a'"]
               [{"pools" {"pool-a" True}} "worker eos の行の pools が列でない: {'pool-a': True}"]
               [{"pools" ["pool-a" "host-eos"]} "worker eos の pools の 'host-eos' は機体の印 — 組の印は pool- で始める"]
               [{"pools" ["company-machine"]}
                "worker eos の pools の 'company-machine' が pool- で始まらない(機体の印 host- と coordinator が node の label から導く能力は組の印に書かない)"]
               [{"pools" ["gpu"]}
                "worker eos の pools の 'gpu' が pool- で始まらない(機体の印 host- と coordinator が node の label から導く能力は組の印に書かない)"]
               [{"pools" ["pool-A"]} "worker eos の pools の 'pool-A' は pool- の後を小文字・数字・`.`・`-` で書く"]
               [{"pools" ["pool-"]} "worker eos の pools の 'pool-' は pool- の後を小文字・数字・`.`・`-` で書く"]
               [{"pools" [1]} "worker eos の pools の 1 が文字列でない"]
               [{"memoryMiB" "2048"} "worker eos の memoryMiB '2048' が 0 以上の整数でない"]
               [{"memoryMiB" True} "worker eos の memoryMiB True が 0 以上の整数でない"]
               [{"memoryMiB" -1} "worker eos の memoryMiB -1 が 0 以上の整数でない"]
               [{"memoryMiB" None} "worker eos の memoryMiB None が 0 以上の整数でない"]])
  (for [#(extra reason) broken]
    (<- read RoomReading (room-of-text (row extra) "eos"))
    (assert (= read (RoomReading :state RoomState.MALFORMED :room None :reason reason)) #(extra read))))


(deftest test-the-heartbeat-adds-the-pools-to-provides-and-keeps-them-when-the-file-breaks [tmp-path]
  ;; card ki-ced3c45980a5: 組の印は起動の引数の能力との和で heartbeat の provides に載り、memoryMiB は欄で載る。外すと次の読み直しの後の
  ;; heartbeat から消え、崩れた file の間は前に名乗った組の印のまま。
  ;; 失敗ケース = 組の印を起動の時にしか読まない(Pod を作り直さないと効かない)・崩れた file で組の印を落とす・起動の引数の能力を捨てる。
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val room (/ tmp-path "room.json"))
  (val pooled {"eos" {"capacity" 3 "taskReserve" 0 "pools" ["pool-a"] "memoryMiB" 3072}})
  (.write-text room (json.dumps pooled))
  (val link (LinkRig "http://coord" "eos" #("host-eos" "host-systemd-readable") 3 0 60000 :task-dir (str (/ tmp-path "tasks"))
                     :transport (httpx.MockTransport handle)))
  (setv link.state.room-file (str room))
  (val named (fn [body] #((get body "provides") (.get body "memoryMiB") (.get body "roomFile"))))
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-eos" "host-systemd-readable" "pool-a"] 3072 "ok")) sent)
  ;; 崩れた file の間は前の組の印と予算のまま。
  (.write-text room (json.dumps {"eos" {"capacity" 3 "taskReserve" 0 "pools" ["host-k3s-2"]}}))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-eos" "host-systemd-readable" "pool-a"] 3072 "malformed")) sent)
  ;; 欄を外すと組の印も予算も名乗らない(起動の引数の能力は残る)。
  (.write-text room (json.dumps {"eos" {"capacity" 3 "taskReserve" 0}}))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-eos" "host-systemd-readable"] None "ok")) sent))


(deftest test-the-coordinator-shows-memory-mib-and-takes-the-pools-as-provides
  ;; card ki-ced3c45980a5: coordinator は heartbeat の memoryMiB を Worker の行と Worker の spec に写し(置き方には使わない)、組の印は
  ;; provides として受ける。名乗らない worker の行は予算を持たず、spec の形も前のまま。
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "eos" "provides" ["host-eos" "pool-a"] "capacity" 3
                                                                     "taskReserve" 0 "memoryMiB" 3072})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "w2" "provides" ["net"] "capacity" 4 "taskReserve" 1})) 10)))
  (val eos (.row both.workers "eos"))
  (val silent (.row both.workers "w2"))
  (assert (= #(eos.provides eos.memory-mib silent.memory-mib) #(#("host-eos" "pool-a") 3072 None)) #(eos silent))
  (assert (= (get (worker-row both eos 10 T) "spec" "memoryMiB") 3072))
  (assert (not-in "memoryMiB" (get (worker-row both silent 10 T) "spec")))
  ;; 負の予算は本文の形の誤り(400)。
  (with [(pytest.raises ValueError)]
    (! (heartbeat-of {"name" "eos" "provides" [] "capacity" 3 "taskReserve" 0 "memoryMiB" -1}))))


(deftest test-the-naming-refuses-a-derived-capability-in-the-pool-names
  ;; card ki-ced3c45980a5: pool- は枠の file の組の印に取ってある。node の label から導く能力に pool- の名を置く naming は起動で断る
  ;; (導く能力と組の印が重なると、枠の file が node の label の境界を名乗れる)。
  (with [e (pytest.raises ValueError)]
    (naming-from-json (json.dumps {"nodeCapabilities" [{"label" "doeff.dev/pool" "value" "a" "capability" "pool-a"}]})))
  (assert (in "pool-a" (str e.value)) e.value)
  (assert (= (. (naming-from-json (json.dumps {"nodeCapabilities" [{"label" "x" "value" "y" "capability" "gpu"}]})) node-capabilities)
             #(#("x" "y" "gpu")))))


(deftest test-the-room-file-is-read-again-every-ten-seconds
  (<- first bool (room-read-due None 0))
  (<- soon bool (room-read-due 0 9999))
  (<- later bool (room-read-due 0 10000))
  (<- back bool (room-read-due 10000 0))
  (assert (= #(first soon later back) #(True False True True))))


(deftest test-the-log-line-names-the-move-and-the-kept-value
  (val before (WorkerRoom :capacity 39 :task-reserve 6))
  (val after (WorkerRoom :capacity 40 :task-reserve 6))
  (<- moved str (room-line before after (RoomReading :state RoomState.OK :room after :reason "")))
  (<- kept str (room-line before before (RoomReading :state RoomState.MALFORMED :room None :reason "JSON でない")))
  (assert (= moved "worker: 枠の file から capacity 39・task-reserve 6 → capacity 40・task-reserve 6 を名乗る") moved)
  (assert (= kept "worker: 枠の file を読めない(malformed: JSON でない)— 前の値 capacity 39・task-reserve 6 のまま名乗る") kept))


(deftest test-the-heartbeat-follows-the-room-file-and-keeps-the-last-value-when-it-breaks [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps ROOMS))
  ;; 起動の引数は capacity 1・task-reserve 0。file を渡した worker は最初の heartbeat から file の値を名乗る。
  (val link (LinkRig "http://coord" "zeus" #() 1 0 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.room-file (str room))
  (val named (fn [body] #((get body "capacity") (get body "taskReserve") (.get body "roomFile"))))
  (.poll link)
  (assert (= (named (get sent -1)) #(39 6 "ok")) sent)
  ;; file の値を変えると、読み直しの後の heartbeat が新しい値を名乗る(起こし直さない)。
  (.write-text room (json.dumps (| ROOMS {"zeus" {"capacity" 40 "taskReserve" 6}})))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(40 6 "ok")) sent)
  ;; 崩れた file・無い file は前に名乗った値のまま、状態だけを名乗る。
  (.write-text room "{")
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(40 6 "malformed")) sent)
  (.unlink room)
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(40 6 "unreadable")) sent)
  ;; 戻ると file の値を名乗り、状態は ok。
  (.write-text room (json.dumps (| ROOMS {"zeus" {"capacity" 38 "taskReserve" 5}})))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(38 5 "ok")) sent)
  ;; file を読まない worker は起動の引数を名乗り、roomFile を名乗らない。
  (val plain (LinkRig "http://coord" "zeus" #() 3 1 60000 :task-dir (str (/ tmp-path "plain")) :transport (httpx.MockTransport handle)))
  (.poll plain)
  (assert (= (named (get sent -1)) #(3 1 None)) sent))


(deftest test-the-boot-takes-the-file-first-then-the-arguments [tmp-path]
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps ROOMS))
  (val booted (fn [path capacity reserve] (run (with-handlers [os-file-handler] (boot-room path "zeus" capacity reserve)))))
  (assert (= (booted (str room) None None) (WorkerRoom :capacity 39 :task-reserve 6)))
  (assert (= (booted (str room) 2 1) (WorkerRoom :capacity 39 :task-reserve 6)))
  ;; 読めない file は引数へ戻る(--capacity は既定 10)。引数も無ければ断りの文。
  (val missing (str (/ tmp-path "no-such.json")))
  (assert (= (booted missing 2 1) (WorkerRoom :capacity 2 :task-reserve 1)))
  (assert (= (booted missing None 1) (WorkerRoom :capacity 10 :task-reserve 1)))
  (val refused (booted missing None None))
  (assert (and (isinstance refused str) (.startswith refused (+ "枠の file " missing " を読めない(unreadable"))) refused)
  ;; file を渡さない起動は今までどおり --task-reserve が要る。
  (assert (= (booted "" None 0) (WorkerRoom :capacity 10 :task-reserve 0)))
  (assert (= (booted "" 4 None) "--task-reserve を渡す(--room-file を渡さない時は必ず要る)")))


(deftest test-the-boot-reads-warm-services-from-the-env-then-the-room-file [tmp-path]
  ;; card ki-67abc0cf71e9: service を待ちの子から分ける旗を、Pod の template を変えずに機体ごとに入れる(枠の file の行の warmServices)。
  ;; 失敗ケース = 枠の file の真を読まない・環境変数の off が file の真に負ける・真偽でない綴りで黙って偽に倒れる・枠だけの行で断る。
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps {"zeus" {"capacity" 39 "taskReserve" 6 "warmServices" True}
                                 "atlas" {"capacity" 12 "taskReserve" 3}
                                 "eos" {"capacity" 3 "taskReserve" 0 "warmServices" "on"}}))
  (val booted (fn [name value] (run (with-handlers [os-file-handler] (boot-warm-services (str room) name value)))))
  (assert (is (booted "zeus" None) True))
  (assert (is (booted "atlas" None) False))
  (assert (is (booted "zeus" "off") False))
  (assert (is (booted "atlas" "on") True))
  (assert (= (booted "eos" None) "枠の file の worker eos の warmServices は true か false で書く: 'on'"))
  (assert (= (booted "eos" "maybe") "DOEFF_WORKER_WARM_SERVICES は on か off で書く: 'maybe'"))
  ;; 読めない file・file を渡さない起動は旗の無い時と同じ(偽)。枠の読みは今までどおり warmServices を読まない。
  (assert (is (run (with-handlers [os-file-handler] (boot-warm-services (str (/ tmp-path "no-such.json")) "zeus" None))) False))
  (assert (is (run (with-handlers [os-file-handler] (boot-warm-services "" "zeus" None))) False))
  (<- zeus RoomReading (room-of-text (.read-text room) "zeus"))
  (assert (= zeus.room (WorkerRoom :capacity 39 :task-reserve 6)) zeus))


(defk sized [name capacity reserve]
  {:pre [(: name str) (: capacity int) (: reserve int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "capacity のうち reserve を task のために空けておく、最後の連絡が 0 で task と同じ版 python 3 の worker の記録を作るため。"
  (WorkerInfo name #() capacity 0 :task-reserve reserve :versions #((ComponentVersion "python" "3"))))


(defk task [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力を要らない待っている task の記録(python 3・lease の期限 20000)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" #((ComponentVersion "python" "3")) #() 15000 20000 0))


(defk job [name]
  {:pre [(: name str)] :post [(: % ClusterJob)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "どの worker にも置ける常駐の job の行を作るため。"
  (ClusterJob (JobSpec name "m" #() "rev")))


(deftest test-lowering-the-room-below-what-is-used-keeps-placed-work-and-places-nothing-new
  ;; zeus は capacity 6・task-reserve 2 で常駐の job a・b・c と task t1・t2 を担う。枠を capacity 3・task-reserve 1 へ下げる heartbeat の後:
  ;; Worker の行は新しい値になり、job の側の空き(3 − 1 − 3 = −1)と全体の空き(3 − 5 = −2)は負。置いた job と task はそのまま担い、
  ;; 新しい job d は「空きが無い」で置かれず、新しい task t3 は空きの無い理由で待つ。
  (val placed (! (table-from-rows {"a" (Placement "a" "zeus" 1 0) "b" (Placement "b" "zeus" 1 0) "c" (Placement "c" "zeus" 1 0)})))
  (val running (! (table-from-rows {"t1" (replace (! (task "t1")) :phase "assigned" :worker "zeus")
                                    "t2" (replace (! (task "t2")) :phase "assigned" :worker "zeus")
                                    "t3" (replace (! (task "t3")) :detached True)})))
  (val state (ClusterState #((! (job "a")) (! (job "b")) (! (job "c")) (! (job "d"))) (! (table-from-rows {"zeus" (! (sized "zeus" 6 2))}))
                           placed running))
  (val lowered (! (register-heartbeat state (! (heartbeat-of {"name" "zeus" "provides" [] "capacity" 3 "taskReserve" 1
                                                             "versions" {"python" "3"}})) 1000)))
  (val zeus (.row lowered.workers "zeus"))
  (assert (= #(zeus.capacity zeus.task-reserve) #(3 1)) zeus)
  (val after (! (tick lowered 1000 T)))
  (assert (= (dfor n ["a" "b" "c"] n (. (.row after.placements n) worker)) {"a" "zeus" "b" "zeus" "c" "zeus"}) after.placements)
  (assert (is (.row after.placements "d") None) after.placements)
  (assert (= (get (unplaced-jobs 1000 after T) "d") "置ける worker に空きが無い") (unplaced-jobs 1000 after T))
  (assert (= (lfor id ["t1" "t2"] #((. (.row after.tasks id) phase) (. (.row after.tasks id) worker)))
             [#("assigned" "zeus") #("assigned" "zeus")])
          after.tasks)
  (val waiting (.row after.tasks "t3"))
  (assert (= #(waiting.phase waiting.worker waiting.detail) #("queued" None (! (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM)))) waiting)
  (val load (load-of after after.placements))
  (assert (= #((! (job-room-of (.row after.workers "zeus") load)) (! (task-room-of (.row after.workers "zeus") load))) #(-1 -2)) load))


(deftest test-the-worker-status-carries-the-room-file-state-only-when-not-ok
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "w1" "provides" [] "capacity" 4 "taskReserve" 1
                                                                    "roomFile" "malformed"})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "w2" "provides" [] "capacity" 4 "taskReserve" 1})) 10)))
  (val broken (.row both.workers "w1"))
  (val silent (.row both.workers "w2"))
  (assert (= #(broken.room-file silent.room-file) #("malformed" "ok")))
  (assert (= (.get (get (worker-row both broken 10 T) "status") "roomFile") "malformed"))
  (assert (not-in "roomFile" (get (worker-row both silent 10 T) "status"))))
