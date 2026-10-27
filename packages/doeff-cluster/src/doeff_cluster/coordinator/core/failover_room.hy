;;; 枠の file(deploy/k8s/workers/worker-room.json)の行を、N+1 の判断 failover_policy.failover-refusals が受ける worker にする読み
;;; (card ki-5996b0cce581)。I/O はしない — file の中身は呼び手が渡す。
;;;
;;; 上に載る系の照らし(常駐の job の契約が枠に入るか)は、枠の file の形(欄の名・範囲)も worker の名乗り方(起動の引数の能力に行の組の印を
;;; 足す)も持たず、ここを呼ぶ。読みを照らしの側に写すと、枠の file の形や名乗り方が変わるたびに写しだけが古い置き場を読み続けて外れる
;;; (2026-10-25 に枠の定義元が worker の env から枠の file へ移った時・2026-10-27 に組の印が足された時)。行は worker 自身の読み
;;; (worker/core/room_rules の room-of-text・launch-provides・announced-provides)で読み、worker が名乗らない行は通さない。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import json)
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
(import doeff_cluster.coordinator.intent.failover_model [ExtraProvides FailoverWorker])
(import doeff_cluster.worker.core.room_rules [RoomReading RoomState announced-provides launch-provides room-of-text])


(defk room-row-names [text]
  {:pre [(: text str)] :post [(: % (get tuple #(str ...)))] :tags {:context "coordinator" :role "judgment" :reads "json"}}
  "枠の file の中身 text の行の worker の名(名の順)を知るため — 照らす worker の母集団。JSON の object でない・行が 1 つも無い file は
   ValueError で名指す(母集団の空振りを通さない)。"
  (val decoded (try #((json.loads text)) (except [json.JSONDecodeError] #())))
  (val rows (if (and decoded (isinstance (get decoded 0) dict)) (get decoded 0) None))
  (when (is rows None)
    (raise (ValueError "枠の file が JSON の object でない")))
  (when (not rows)
    (raise (ValueError "枠の file に worker の行が 1 つも無い")))
  (tuple (sorted rows)))


(defk room-file-workers [text extras]
  {:pre [(: text str) (: extras (get tuple #(ExtraProvides ...))) (all (gfor e extras (isinstance e ExtraProvides)))
         (= (len (set (gfor e extras e.name))) (len extras))]
   :post [(: % (get tuple #(FailoverWorker ...)))] :tags {:context "coordinator" :role "judgment"}}
  "枠の file の中身 text の行の全部を、N+1 の判断が受ける worker(名の順)にするため。枠・予約・組の印・memory の予算 = room-of-text で
   読んだ行の値、能力 = 雛形が起動の引数で渡す能力(launch-provides — extras の同じ名の行の能力を足す)に行の組の印を足した名乗り
   (announced-provides)。worker が名乗らない行(room-of-text の malformed)と、行の無い worker の extras は ValueError で名指す(前の値も
   既定の値も無いので、照らせない物を通さない)。"
  (<- names (get tuple #(str ...)) (room-row-names text))
  (val stray (sorted (gfor e extras :if (not-in e.name names) e.name)))
  (when stray
    (raise (ValueError (.format "能力を足す worker {} の行が枠の file に無い" (.join "・" stray)))))
  (var workers #())
  (for [name names]
    (<- reading RoomReading (room-of-text text name))
    (when (or (!= reading.state RoomState.OK) (is reading.room None))
      (raise (ValueError (+ "枠の file の " reading.reason))))
    (<- launched (get tuple #(str ...)) (launch-provides name (sum (gfor e extras :if (= e.name name) e.provides) #())))
    (<- provides (get tuple #(str ...)) (announced-provides launched reading.room.pools))
    (:= workers (+ workers #((FailoverWorker :info (WorkerInfo :name name :provides provides :capacity reading.room.capacity :last-seen-ms 0
                                                              :exclusive #() :task-reserve reading.room.task-reserve)
                                             :memory-mib reading.room.memory-mib)))))
  workers)
