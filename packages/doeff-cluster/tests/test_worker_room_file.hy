;; worker の枠(memory の予算と置ける組の印)を枠の file から読み直す。
;; 枠を起動の引数だけで受けると、値を変えるたびに worker の Pod の template が変わって作り直され、走っている job と task が切れた(常駐の job を
;; 1 つ足すたびに枠を変える — 2026-10-25 の差し戻し「枠の変更に再起動を要する設計を治す。p0」)。
;;   (1) 枠の file の自分の行を読み、JSON でない・行が無い・memoryMiB が無いか 0 以上の整数でない は malformed と訳にする。行の手書きの
;;       task の枠(capacity・taskReserve・backgroundRoom — 消した欄)は読まずに通す(旧い版の file を読む間)
;;   (2) coordinator への口は file を読み直して heartbeat の memoryMiB と組の印を file の値にし、file が無い・崩れた時は前に名乗った値の
;;       まま roomFile で状態を名乗る。file を読まない worker は roomFile を名乗らない。task の数は名乗らない
;;   (3) 起動の入口は file の値を先に使い、読めなければ引数(--memory-mib — 省けば名乗らない)
;;   (4) coordinator は heartbeat の新しい枠で Worker の行を書き直し、memory の予算や測った空きを使っている分より下げても置いた job と
;;       task を退かさず、新しく置かないだけ。roomFile が ok でない間だけ Worker の status に出す
;; 失敗ケース = 起動の時の値を名乗り続ける(file の変更が効かない)・崩れた file で既定の値や 0 を名乗る・下げた枠で走っている job を外す。
(require doeff-hy.macros [deff defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.room_rows [ROOM-LOADS ROOM-JSONS])
(import json)
(import dataclasses [replace])
(import httpx)
(import pytest)
(import doeff_cluster [naming-from-json])
(import doeff [run with-handlers])
(import doeff_core_effects [os-file-handler])
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster [JobSpec])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [RoomReading RoomState WorkerRoom room-of-text]
        doeff_cluster._worker.core.room_rules [room-read-due room-line])
(import doeff_cluster._worker.entry.main [boot-room boot-warm-services])
(import doeff_cluster [ClusterJob ClusterState WorkerInfo]
        doeff_cluster._coordinator.intent.cluster_model [Placement ComponentVersion TaskRecord TaskUnplacedKind])
(import doeff_cluster [memory-room-of]
        doeff_cluster._coordinator.core.cluster_policy [register-heartbeat load-of task-room-of task-unplaced-text jobs-placed unplaced-jobs])
(import doeff_cluster [tick])
(import doeff_cluster._coordinator.core.resource_policy [worker-row])
(import tests.state_rows [table-from-rows])
(import tests.program_rows [SAMPLE-TASK-PROGRAM])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val ROOMS {"node-h" {"memoryMiB" 63488}
            "node-a" {"memoryMiB" 8960}})


(deftest test-the-room-file-names-the-row-of-the-worker
  (<- node-h RoomReading (room-of-text (json.dumps ROOMS) "node-h"))
  (assert (= node-h (RoomReading :state RoomState.OK :room (WorkerRoom :memory-mib 63488) :reason "")) node-h)
  ;; 崩れた形はどれも malformed で枠を持たない(既定の値や 0 で埋めない)。
  (val broken [["{" "JSON でない"]
               ["[1]" "JSON の object でない"]
               [(json.dumps ROOMS) "worker node-b の行が無い"]
               ;; 失敗ケース: memoryMiB の無い行(手書きの task の枠だけの旧い形)は malformed。
               [(json.dumps {"node-b" {"capacity" 3 "taskReserve" 0 "backgroundRoom" 0}}) "worker node-b の行の memoryMiB が 0 以上の整数でない: None"]
               [(json.dumps {"node-b" {}}) "worker node-b の行の memoryMiB が 0 以上の整数でない: None"]
               [(json.dumps {"node-b" {"memoryMiB" "2048"}}) "worker node-b の行の memoryMiB が 0 以上の整数でない: '2048'"]
               [(json.dumps {"node-b" {"memoryMiB" True}}) "worker node-b の行の memoryMiB が 0 以上の整数でない: True"]])
  (for [#(text reason) broken]
    (<- read RoomReading (room-of-text text "node-b"))
    (assert (= read (RoomReading :state RoomState.MALFORMED :room None :reason reason)) #(text read))))


(deftest test-the-room-file-row-with-the-removed-task-fields-is-read-without-them
  ;; 行に手書きの task の枠(capacity・taskReserve・backgroundRoom — 消した欄)が在っても読み、値は名乗らない(旧い版の file を読む間)。
  ;; 消した欄の値が何でも(崩れた値でも)同じ枠を読む。失敗ケース = 消した欄の崩れで行を malformed にして、memory の予算を名乗れなくなる。
  (<- old RoomReading (room-of-text (json.dumps {"node-b" {"capacity" 7 "taskReserve" 3 "backgroundRoom" 4 "memoryMiB" 3840}}) "node-b"))
  (<- odd RoomReading (room-of-text (json.dumps {"node-b" {"capacity" "x" "taskReserve" True "backgroundRoom" -1 "memoryMiB" 3840}}) "node-b"))
  (<- without RoomReading (room-of-text (json.dumps {"node-b" {"memoryMiB" 3840}}) "node-b"))
  (val room (WorkerRoom :memory-mib 3840))
  (assert (= #(old.room odd.room without.room) #(room room room)) #(old odd without))
  (assert (= #(old.state odd.state) #(RoomState.OK RoomState.OK)) #(old odd)))


(deftest test-the-room-file-row-names-pools-and-memory-mib
  ;; 行の省ける欄 pools(置ける組の印 pool-…)と memoryMiB(常駐の job の memory の予算)。
  ;; 失敗ケース = 機体の印 host- や導く能力の名を組の印として名乗る・崩れた欄を黙って外して残りを名乗る・memoryMiB の真偽や負の数を受ける。
  (<- node-a RoomReading (room-of-text (json.dumps {"node-a" {"pools" ["pool-b" "pool-a" "pool-b"] "memoryMiB" 16384}})
                                      "node-a"))
  (assert (= node-a (RoomReading :state RoomState.OK :reason ""
                                :room (WorkerRoom :pools #("pool-a" "pool-b") :memory-mib 16384)))
          node-a)
  ;; pools の無い行は組の印を持たない。
  (<- plain RoomReading (room-of-text (json.dumps ROOMS) "node-h"))
  (assert (= plain.room (WorkerRoom :pools #() :memory-mib 63488)) plain)
  (val row (fn [extra] (json.dumps {"node-b" (| {"memoryMiB" 3840} extra)})))
  (val broken [[{"pools" "pool-a"} "worker node-b の行の pools が列でない: 'pool-a'"]
               [{"pools" {"pool-a" True}} "worker node-b の行の pools が列でない: {'pool-a': True}"]
               [{"pools" ["pool-a" "host-node-b"]} "worker node-b の pools の 'host-node-b' は機体の印 — 組の印は pool- で始める"]
               [{"pools" ["company-machine"]}
                "worker node-b の pools の 'company-machine' が pool- で始まらない(機体の印 host- と coordinator が node の label から導く能力は組の印に書かない)"]
               [{"pools" ["gpu"]}
                "worker node-b の pools の 'gpu' が pool- で始まらない(機体の印 host- と coordinator が node の label から導く能力は組の印に書かない)"]
               [{"pools" ["pool-A"]} "worker node-b の pools の 'pool-A' は pool- の後を小文字・数字・`.`・`-` で書く"]
               [{"pools" ["pool-"]} "worker node-b の pools の 'pool-' は pool- の後を小文字・数字・`.`・`-` で書く"]
               [{"pools" [1]} "worker node-b の pools の 1 が文字列でない"]
               [{"memoryMiB" -1} "worker node-b の行の memoryMiB が 0 以上の整数でない: -1"]
               [{"memoryMiB" None} "worker node-b の行の memoryMiB が 0 以上の整数でない: None"]])
  (for [#(extra reason) broken]
    (<- read RoomReading (room-of-text (row extra) "node-b"))
    (assert (= read (RoomReading :state RoomState.MALFORMED :room None :reason reason)) #(extra read))))


(deftest test-the-heartbeat-adds-the-pools-to-provides-and-keeps-them-when-the-file-breaks [tmp-path]
  ;; 組の印は起動の引数の能力との和で heartbeat の provides に載り、memoryMiB は欄で載る。外すと次の読み直しの後の
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
  (val pooled {"node-b" {"pools" ["pool-a"] "memoryMiB" 3072}})
  (.write-text room (json.dumps pooled))
  (val link (LinkRig "http://coord" "node-b" #("host-node-b" "host-systemd-readable") 60000 :task-dir (str (/ tmp-path "tasks"))
                     :transport (httpx.MockTransport handle)))
  (setv link.state.room-file (str room))
  (val named (fn [body] #((get body "provides") (.get body "memoryMiB") (.get body "roomFile"))))
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-node-b" "host-systemd-readable" "pool-a"] 3072 "ok")) sent)
  ;; 崩れた file の間は前の組の印と予算のまま。
  (.write-text room (json.dumps {"node-b" {"pools" ["host-node-f"] "memoryMiB" 1}}))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-node-b" "host-systemd-readable" "pool-a"] 3072 "malformed")) sent)
  ;; pools を外すと組の印を名乗らない(起動の引数の能力は残る)。予算は行の値。
  (.write-text room (json.dumps {"node-b" {"memoryMiB" 2048}}))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-node-b" "host-systemd-readable"] 2048 "ok")) sent))


(deftest test-the-coordinator-shows-memory-mib-and-takes-the-pools-as-provides
  ;; coordinator は heartbeat の memoryMiB を Worker の行と Worker の spec に写し(job の側の枠)、組の印は
  ;; provides として受ける。名乗らない worker の行は予算を持たず、spec の形も前のまま。
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "node-b" "provides" ["host-node-b" "pool-a"] "memoryMiB" 3072})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "w2" "provides" ["net"]})) 10)))
  (val node-b (.row both.workers "node-b"))
  (val silent (.row both.workers "w2"))
  (assert (= #(node-b.provides node-b.memory-mib silent.memory-mib) #(#("host-node-b" "pool-a") 3072 None)) #(node-b silent))
  (assert (= (get (worker-row both node-b 10 T) "spec" "memoryMiB") 3072))
  ;; spec は手書きの task の枠(capacity・taskReserve・backgroundRoom)を持たない。
  (assert (not (& #{"capacity" "taskReserve" "backgroundRoom"} (set (get (worker-row both node-b 10 T) "spec")))) (worker-row both node-b 10 T))
  (assert (not-in "memoryMiB" (get (worker-row both silent 10 T) "spec")))
  ;; 負の予算は本文の形の誤り(400)。
  (with [(pytest.raises ValueError)]
    (! (heartbeat-of {"name" "node-b" "provides" [] "memoryMiB" -1}))))


(deftest test-the-naming-refuses-a-derived-capability-in-the-pool-names
  ;; pool- は枠の file の組の印に取ってある。node の label から導く能力に pool- の名を置く naming は起動で断る
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
  (val before (WorkerRoom :memory-mib None))
  (val after (WorkerRoom :memory-mib 63488))
  (<- moved str (room-line before after (RoomReading :state RoomState.OK :room after :reason "")))
  (<- kept str (room-line before before (RoomReading :state RoomState.MALFORMED :room None :reason "JSON でない")))
  (assert (= moved "worker: 枠の file から memory の予算なし → memory 63488 MiB を名乗る") moved)
  (assert (= kept "worker: 枠の file を読めない(malformed: JSON でない)— 前の値 memory の予算なし のまま名乗る") kept))


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
  ;; 起動の引数は memory の予算なし。file を渡した worker は最初の heartbeat から file の値を名乗る。手書きの task の枠は送らない。
  (val link (LinkRig "http://coord" "node-h" #() 60000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))
  (setv link.state.room-file (str room))
  (val named (fn [body] #((.get body "memoryMiB") (.get body "roomFile"))))
  (.poll link)
  (assert (= (named (get sent -1)) #(63488 "ok")) sent)
  (assert (not (& #{"capacity" "taskReserve" "backgroundRoom"} (set (get sent -1)))) (get sent -1))
  ;; file の値を変えると、読み直しの後の heartbeat が新しい値を名乗る(起こし直さない)。
  (.write-text room (json.dumps (| ROOMS {"node-h" {"memoryMiB" 60000}})))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(60000 "ok")) sent)
  ;; 崩れた file・無い file は前に名乗った値のまま、状態だけを名乗る。
  (.write-text room "{")
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(60000 "malformed")) sent)
  (.unlink room)
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(60000 "unreadable")) sent)
  ;; 戻ると file の値を名乗り、状態は ok。
  (.write-text room (json.dumps (| ROOMS {"node-h" {"memoryMiB" 50000}})))
  (setv link.state.room-read-ms None)
  (.poll link)
  (assert (= (named (get sent -1)) #(50000 "ok")) sent)
  ;; file を読まない worker は起動の引数を名乗り、roomFile を名乗らない。
  (val plain (LinkRig "http://coord" "node-h" #() 60000 :task-dir (str (/ tmp-path "plain")) :transport (httpx.MockTransport handle)))
  (.poll plain)
  (assert (= (named (get sent -1)) #(None None)) sent))


(deftest test-the-boot-takes-the-file-first-then-the-arguments [tmp-path]
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps ROOMS))
  (val booted (fn [path [memory None]]
                 (run (with-handlers [os-file-handler] (boot-room path "node-h" memory)))))
  (assert (= (booted (str room)) (WorkerRoom :memory-mib 63488)))
  (assert (= (booted (str room) 100) (WorkerRoom :memory-mib 63488)))
  ;; 読めない file は引数へ戻る(--memory-mib は省けば名乗らない)。task の数の引数は無い(task の置き先は測った空きから数える)。
  (val missing (str (/ tmp-path "no-such.json")))
  (assert (= (booted missing 4096) (WorkerRoom :memory-mib 4096)))
  (assert (= (booted missing) (WorkerRoom :memory-mib None)))
  ;; file を渡さない起動も引数の値。負の予算は断る。
  (assert (= (booted "") (WorkerRoom :memory-mib None)))
  (assert (= (booted "" -1) "--memory-mib -1 は 0 以上で名乗る")))


(deftest test-the-boot-reads-warm-services-from-the-env-then-the-room-file [tmp-path]
  ;; service を待ちの子から分ける旗を、Pod の template を変えずに機体ごとに入れる(枠の file の行の warmServices)。
  ;; 失敗ケース = 枠の file の真を読まない・環境変数の off が file の真に負ける・真偽でない綴りで黙って偽に倒れる・枠だけの行で断る。
  (val room (/ tmp-path "room.json"))
  (.write-text room (json.dumps {"node-h" {"memoryMiB" 63488 "warmServices" True}
                                 "node-a" {"memoryMiB" 8960}
                                 "node-b" {"memoryMiB" 3840 "warmServices" "on"}}))
  (val booted (fn [name value] (run (with-handlers [os-file-handler] (boot-warm-services (str room) name value)))))
  (assert (is (booted "node-h" None) True))
  (assert (is (booted "node-a" None) False))
  (assert (is (booted "node-h" "off") False))
  (assert (is (booted "node-a" "on") True))
  (assert (= (booted "node-b" None) "枠の file の worker node-b の warmServices は true か false で書く: 'on'"))
  (assert (= (booted "node-b" "maybe") "DOEFF_WORKER_WARM_SERVICES は on か off で書く: 'maybe'"))
  ;; 読めない file・file を渡さない起動は旗の無い時と同じ(偽)。枠の読みは今までどおり warmServices を読まない。
  (assert (is (run (with-handlers [os-file-handler] (boot-warm-services (str (/ tmp-path "no-such.json")) "node-h" None))) False))
  (assert (is (run (with-handlers [os-file-handler] (boot-warm-services "" "node-h" None))) False))
  (<- node-h RoomReading (room-of-text (.read-text room) "node-h"))
  (assert (= node-h.room (WorkerRoom :memory-mib 63488)) node-h))


(defk sized [name room memory]
  {:pre [(: name str) (: room int) (: memory int)] :post [(: % WorkerInfo)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "測った空きで task を room 本置ける(ROOM-LOADS)・memory の予算 memory の、最後の連絡が 0 で task と同じ版 python 3 の worker の
   記録を作るため。"
  (WorkerInfo name #() 0 :measured (get ROOM-LOADS room) :versions #((ComponentVersion "python" "3")) :memory-mib memory))


(defk task [id]
  {:pre [(: id str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "能力を要らない待っている task の記録(python 3・lease の期限 20000)を作るため。"
  (TaskRecord id "digest" SAMPLE-TASK-PROGRAM "rev" #((ComponentVersion "python" "3")) #() 15000 20000 0))


(defk job [name]
  {:pre [(: name str)] :post [(: % ClusterJob)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "どの worker にも置ける常駐の job の行を作るため。"
  (ClusterJob (JobSpec name "m" #() "rev") :peak-mib 200 :settle-mib 100))


(deftest test-lowering-the-room-below-what-is-used-keeps-placed-work-and-places-nothing-new
  ;; node-h は測った空き 6 本・memory 400 MiB で常駐の job a・b・c(settle 100 ずつ)と task t1・t2 を担う。測った空きが 1 本・memory
  ;; 250 MiB へ下がる heartbeat の後: Worker の行は新しい値になり、memory の残り(250 − 300 = −50)は負、task の空き(1 − 2 本はまだ測りに
  ;; 出ていない)は 0。置いた job と task はそのまま担い、新しい job d は memory の訳で置かれず、新しい task t3 は空きの無い理由で待つ。
  (val placed (! (table-from-rows {"a" (Placement "a" "node-h" 1 0) "b" (Placement "b" "node-h" 1 0) "c" (Placement "c" "node-h" 1 0)})))
  (val running (! (table-from-rows {"t1" (replace (! (task "t1")) :phase "assigned" :worker "node-h")
                                    "t2" (replace (! (task "t2")) :phase "assigned" :worker "node-h")
                                    "t3" (replace (! (task "t3")) :detached True)})))
  (val state (ClusterState #((! (job "a")) (! (job "b")) (! (job "c")) (! (job "d"))) (! (table-from-rows {"node-h" (! (sized "node-h" 6 400))}))
                           placed running))
  (val lowered (! (register-heartbeat state (! (heartbeat-of {"name" "node-h" "provides" [] "measured" (get ROOM-JSONS 1)
                                                             "memoryMiB" 250 "versions" {"python" "3"}})) 1000)))
  (val node-h (.row lowered.workers "node-h"))
  (assert (= #(node-h.measured node-h.memory-mib) #((get ROOM-LOADS 1) 250)) node-h)
  (val after (! (tick lowered 1000 T)))
  (assert (= (dfor n ["a" "b" "c"] n (. (.row after.placements n) worker)) {"a" "node-h" "b" "node-h" "c" "node-h"}) after.placements)
  (assert (is (.row after.placements "d") None) after.placements)
  (assert (= (get (unplaced-jobs 1000 after T) "d")
             "置ける worker に空きが無い(job d を worker node-h に置くと、置いた job の settle の和 300 MiB + job の settle 100 MiB = 400 MiB が memory の予算 250 MiB を越える)")
          (unplaced-jobs 1000 after T))
  (assert (= (lfor id ["t1" "t2"] #((. (.row after.tasks id) phase) (. (.row after.tasks id) worker)))
             [#("assigned" "node-h") #("assigned" "node-h")])
          after.tasks)
  (val waiting (.row after.tasks "t3"))
  (assert (= #(waiting.phase waiting.worker waiting.detail) #("queued" None (! (task-unplaced-text TaskUnplacedKind.TASK-NO-ROOM)))) waiting)
  (val load (load-of after after.placements))
  (assert (= #((! (memory-room-of (.row after.workers "node-h") load)) (! (task-room-of (.row after.workers "node-h") load))) #(-50 0)) load))


(deftest test-the-worker-status-carries-the-room-file-state-only-when-not-ok
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "w1" "provides" [] "roomFile" "malformed"})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "w2" "provides" []})) 10)))
  (val broken (.row both.workers "w1"))
  (val silent (.row both.workers "w2"))
  (assert (= #(broken.room-file silent.room-file) #("malformed" "ok")))
  (assert (= (.get (get (worker-row both broken 10 T) "status") "roomFile") "malformed"))
  (assert (not-in "roomFile" (get (worker-row both silent 10 T) "status"))))
