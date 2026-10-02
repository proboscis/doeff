;; coordinator: 作り直し・盤の compare-and-set・task の一生(置く・結果・期限・版・担い手の沈黙)・調停ループの Program・shim。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [replace])
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import datetime [timedelta])
(import doeff_time [SimClock sim-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request Reply CoordinatorStopRequested])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState CoordinatorFault] doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.cluster_policy [reconcile state-view job-from-json identity-hash] doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json])
(import tests.program_rows [SAMPLE-RUN SAMPLE-PROGRAM SAMPLE-TASK-PROGRAM program-placed])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff [run])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.store [Persist durable-states durable-load durable-persist])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies state-view-json])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.durable_kv [LEGACY-PLACEMENT PLACEMENT])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0" "doeff" "1"})

(defk req [method path [body None] [query None] [actor "test"]]
  {:pre [(: method str) (: path str) (: body (| dict list str int float bool None)) (: query (| dict None)) (: actor (| str None))] :post [(: % Request)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "検の HTTP の要求の値 Request を作るため(送り手 actor つき)。"
  (! (http-request method path (or query {}) body :actor actor)))

(defk beat [state name now [statuses None] [versions V] [provides None]]
  {:pre [(: state ClusterState) (: name str) (: now int) (: statuses (| list None)) (: versions dict) (: provides (| list None))] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name の heartbeat を 1 つ判断 responded に渡し、(状態 状態の番号 本文) の組を返すため。"
  (responded state (! (req "POST" "/heartbeat" {"name" name "provides" (or provides ["net"]) "capacity" 10
                                           "versions" versions "statuses" (or statuses [])})) now T))


(deftest test-restart-keeps-placements-of-workers-that-have-not-reported-yet
  ;; 作り直した coordinator へ最初に名乗った worker に全 job が寄らないこと(実測 2026-09-23 の欠陥)。
  (<- reply-1 (beat (ClusterState) "a" 0))
  (var s (get reply-1 0))
  (<- reply-2 (beat s "b" 0))
  (:= s (get reply-2 0))
  (val reply-3 (responded s (! (req "PUT" "/jobs" {"jobs" (lfor i (range 4) {"name" f"s{i}" "run" SAMPLE-RUN "revision" "r" "needs" ["net"]})})) 0 T))
  (:= s (get reply-3 0))
  (setv before (dfor #(k v) (.items s.placements) k v.worker))
  (assert (= (set (.values before)) #{"a" "b"}))
  (var second (! (state-from-json (! (state-to-json s)) 5000)))
  (<- reply-4 (beat second "a" 5000))
  (:= second (get reply-4 0)) ; b はまだ名乗っていない
  (assert (= (dfor #(k v) (.items second.placements) k v.worker) before)))


(deftest test-service-declaration-becomes-the-job-entry-command
  ;; Program の job の行 → job_entry の service 入口。引数は identity の指紋だけ(詰めた Program は置き場のキーで運ぶ・比べない欄)。
  (val job (job-from-json {"name" "runner" "revision" "abc" "needs" ["net"] "run" SAMPLE-RUN "environ" {"B" "1" "A" "2"}}))
  (assert (= job.spec.entry "doeff_cluster.job_entry"))
  (assert (= job.spec.args #("service" "--identity" (identity-hash SAMPLE-RUN))))
  (assert (= job.spec.program SAMPLE-PROGRAM))
  (assert (= job.spec.environ #(#("A" "2") #("B" "1"))) "environ は名の順の組"))


;; 盤の compare-and-set(無い時だけ・値が等しい時だけ・prefix の読み)は tests/test_shared_contract.hy が本物の client と fake の
;; 両方で見る。


(defk submit [state now [versions V] [lease 15.0]]
  {:pre [(: state ClusterState) (: now int) (: versions dict) (: lease (| float int))] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task を 1 つ積み、(状態 task の id) の組を返すため。"
  ;; 詰めた Program を置き場に(送り手の版 versions と一緒に)置いてから、task の本文は置き場のキーだけを運ぶ。
  (<- placed tuple (program-placed state versions :now now))
  (val reply (responded (get placed 0) (! (req "POST" "/tasks" {"program" (get placed 1) "revision" "r"
                                                               "needs" ["net"] "name" "n" "leaseSeconds" lease})) now T))
  #((get reply 0) (get (get reply 2) "task")))


(deftest test-task-goes-to-a-worker-and-its-result-comes-back
  (<- reply-5 (beat (ClusterState) "w" 0))
  (var s (get reply-5 0))
  (<- reply-6 (submit s 100))
  (:= s (get reply-6 0))
  (val id (get reply-6 1))
  (<- reply-7 (beat s "w" 200))
  (:= s (get reply-7 0))
  (var body (get reply-7 2))
  (assert (= (lfor t (get body "tasks") (get t "id")) [id]))
  ;; 返事は詰めた Program を運ばず、置き場のキーだけ(worker が /programs/<sha> から取る)。
  (assert (= (get body "tasks" 0 "program") SAMPLE-TASK-PROGRAM))
  (assert (not-in "blob" (get body "tasks" 0)))
  ;; worker が終わったと報告する(結果の blob を添えて)
  (<- reply-8 (beat s "w" 300 [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (:= s (get reply-8 0))
  (:= body (get reply-8 2))
  (assert (= (get body "tasks") [])) ; 終わった task はもう送らない = worker は file を片付ける
  (val reply-9 (responded s (! (req "GET" (+ "/tasks/" id))) 400 T))
  (:= s (get reply-9 0))
  (val view (get reply-9 2))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")))
  ;; 結果は状態の報告(/state)には載せない
  (assert (is (. (get (. (.row s.observations.statuses "w") jobs) 0) result) None))
  (assert (not-in "result" (get (! (state-view-json (! (state-view s 400 T)))) "statuses" "w" "jobs" 0))))


(deftest test-task-is-dropped-when-the-caller-stops-asking
  (<- reply-10 (beat (ClusterState) "w" 0))
  (var s (get reply-10 0))
  (<- reply-11 (submit s 0 :lease 5.0))
  (:= s (get reply-11 0))
  (val id (get reply-11 1))
  (val reply-12 (responded s (! (req "GET" (+ "/tasks/" id))) 4000 T))
  (:= s (get reply-12 0)) ; 問い合わせが lease を 9000 まで延ばす
  (<- reply-13 (beat s "w" 8000))
  (:= s (get reply-13 0))
  (var body (get reply-13 2))
  (assert (= (len (get body "tasks")) 1))
  (<- reply-14 (beat s "w" 9001))
  (:= s (get reply-14 0))
  (:= body (get reply-14 2))
  (assert (= (get body "tasks") [])) ; 担い手は次の拍でその子 process を止める
  (val reply-15 (responded s (! (req "GET" (+ "/tasks/" id))) 9002 T))
  (:= s (get reply-15 0))
  (val view (get reply-15 2))
  (assert (= (get view "phase") "missing")))


(deftest test-task-from-a-different-version-is-refused-before-sending
  (<- reply-16 (beat (ClusterState) "w" 0))
  (var s (get reply-16 0))
  (<- reply-17 (submit s 10 :versions (| V {"python" "3.9.6"})))
  (:= s (get reply-17 0))
  (val id (get reply-17 1))
  (setv task (get s.tasks id))
  (assert (= task.phase "failed"))
  (assert (in "python=3.14.0" task.detail))
  (<- reply-18 (beat s "w" 20))
  (:= s (get reply-18 0))
  (val body (get reply-18 2))
  (assert (= (get body "tasks") [])))


(deftest test-task-of-a-silent-worker-fails-and-is-not-rerun
  (<- reply-19 (beat (ClusterState) "w" 0))
  (var s (get reply-19 0))
  (<- reply-20 (submit s 0 :lease 100.0))
  (:= s (get reply-20 0))
  (val id (get reply-20 1))
  (<- reply-21 (beat s "x" (+ T.reassign-after-ms 1000)))
  (:= s (get reply-21 0)) ; w は 0 から移し替えの期限を越えて沈黙、x だけが生きている
  (setv task (get s.tasks id))
  (assert (= task.phase "failed"))
  (assert (in "沈黙" task.detail)))


;; --- 調停ループの Program を台本の要求で動かす -----------------------------------------------

(defclass Script []
  "台本の要求。requests の要素 = 要求 1 件か、要求の list(1 まとまり)。"
  (defn #^ None __init__ [self #^ list requests #^ (| int None) [fail-at None]]
    ;; 時刻は doeff-time の仮想の時計(epoch 0 から)。要求を待つ 1 回ごとに 500 ms 進む(台本の NextRequests が時間を使った形)。
    (setv self.requests (list requests) self.replies [] self.saved [] self.clock (SimClock) self.fail-at fail-at))
  (defn [property] #^ int now [self] (run (clock-ms self.clock)))
  (defn #^ None wait-a-little [self]
    (.set-time self.clock (+ self.clock.current-time (timedelta :milliseconds 500)))
    None))

(defclass Crashed [Exception])

(defhandler scripted-requests [#^ Script script]
  (NextRequests [timeout-seconds limit]
    (.wait-a-little script)
    (setv item (if script.requests (.pop script.requests 0) []))
    (resume (if (isinstance item list) item [item])))
  (Reply [request status body] (.append script.replies #(request.path status body)) (resume None))
  (Persist [writes]
    ;; fail-at 回目の永続化で落ちる(fsync の途中で coordinator が落ちた形)。積むのは置き場の口と同じ差分(キー → 新しい値)。
    (when (= (+ (len script.saved) 1) script.fail-at) (raise (Crashed "fsync の途中で落ちた")))
    (.append script.saved (dfor w writes w.key w.value)) (resume None))
  (CoordinatorStopRequested [] (resume (and (not script.requests) (> script.now 3000)))))

(defk scripted [script]
  {:pre [(: script Script)] :post [(: % Callable)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の外側に仮想の時計(script の SimClock)を被せる。"
  (fn [program] ((sim-time-handler :clock script.clock) ((scripted-requests script) (request-bodies (durable-states (reply-bodies program)))))))

(deftest test-coordinator-loop-answers-after-persisting
  (setv script (Script [(! (req "POST" "/heartbeat" {"name" "w" "provides" ["net"] "capacity" 10 "versions" V}))
                        (! (req "PUT" "/jobs" {"jobs" [{"name" "a" "run" SAMPLE-RUN "revision" "r" "needs" ["net"]}]}))
                        (! (req "PUT" "/board/k" {"value" 1}))
                        (! (req "GET" "/nothing"))]))
  (<- final ClusterState ((! (scripted script)) (run-coordinator (ClusterState) T (ClusterNaming))))
  (assert (= (lfor r script.replies (get r 1)) [200 200 200 404]))
  (assert (= (. (get final.placements "a") worker) "w"))
  ;; 永続化は変化のあったまとまりだけ。盤の書きは盤のキー 1 つだけ(資源の状態を書き直さない)
  (setv board-batch (next (gfor d script.saved :if (in "board/k" d) d)))
  (assert (= (get board-batch "board/k") {"value" 1 "resourceVersion" 1}))
  (assert (= (sorted board-batch) ["board/k"])))

(defhandler recording [#^ list order]
  (Persist [writes] (.append order "persist") (<- (Persist writes)) (resume None))
  (Reply [request status body] (.append order (+ "reply " request.path)) (<- (Reply request status body)) (resume None)))

(deftest test-group-commit-answers-a-batch-only-after-one-persist
  ;; 3 件が 1 まとまり: 永続化は 1 回、返事は 3 件とも永続化の後。
  (setv order [])
  (setv script (Script [[(! (req "PUT" "/board/a" {"value" 1})) (! (req "PUT" "/board/b" {"value" 2})) (! (req "GET" "/board"))]]))
  (<- final ClusterState ((! (scripted script)) ((recording order) (durable-states (run-coordinator (ClusterState) T (ClusterNaming))))))
  (assert (= order ["persist" "reply /board/a" "reply /board/b" "reply /board"]) order)
  (assert (= (len script.saved) 1)))

(deftest test-a-crash-during-persist-leaves-the-batch-unanswered
  ;; 2 まとまり目の fsync の途中で落ちる: 1 まとまり目の書きは返事済み・2 まとまり目の送り手には返事が来ない(失敗として扱われる)。
  (import pytest)
  (setv script (Script [[(! (req "PUT" "/board/a" {"value" 1}))] [(! (req "PUT" "/board/b" {"value" 2}))]] :fail-at 2))
  (with [(pytest.raises Crashed)]
    (<- _ ClusterState ((! (scripted script)) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= (lfor r script.replies (get r 0)) ["/board/a"]))
  (assert (= (lfor d script.saved (sorted d)) [["board/a"]])))

(defhandler old-field-name-reader
  ;; 失敗ケース(#2722): Persist の欄を旧い名(delta — キー → 値の dict だった頃)のまま読む答え手。節の欄の束ね `(Persist [delta] …)` は
  ;; 欄を (. effect delta) で読み、型検査も名を捕まえる(この file に error を残さないよう、ここは実行の時だけ名を引く getattr で同じ読みをする)。
  (Persist [] (getattr effect "delta") (resume None)))

(deftest test-a-persist-reader-still-using-the-old-field-name-fails-by-name
  ;; #2722: Persist の欄は writes(TableWrite の組)。旧い名 delta で読む答え手は、書きの組を差分として黙って読まずに、欄の名を名指して
  ;; 落ちる(答え手の節は (. effect delta) で欄を読む)。
  (import pytest)
  (val script (Script [(! (req "PUT" "/board/k" {"value" 1}))]))
  (with [(pytest.raises AttributeError :match "delta")]
    (<- _ ClusterState ((! (scripted script)) (old-field-name-reader (durable-states (run-coordinator (ClusterState) T (ClusterNaming))))))))


(defhandler fault-log [#^ list faults]
  ;; 本番の受付が stderr へ出す中の欠陥の 1 行の代わりに、出た Fault を list へ残す。
  (CoordinatorFault [fault] (.append faults fault) (resume None)))

(deftest test-a-fault-inside-the-coordinator-is-500-with-one-log-line [monkeypatch]
  ;; 反例(#1024 — #1005 の形): 状態の書きの印(stamp)の中で TypeError が上がる。送り手の誤りの 400 に畳まず 500 で返し、
  ;; log の 1 行(CoordinatorFault)に要求の path・例外の型・上がった所が出る。同じまとまりの送り手の誤り(object でない本文)は 400 のまま。
  ;; 偽の stamp は worker w の名乗りの書きでだけ上げる(毎拍の調停 tick も stamp を通るので、他は本物に渡す)。
  (import doeff_cluster.coordinator.core.api_policy)
  (val real-stamp doeff_cluster.coordinator.core.api_policy.stamp)
  (monkeypatch.setattr doeff_cluster.coordinator.core.api_policy "stamp"
                       (fn [before after actor #* rest]
                         (if (= actor "w")
                             (raise (TypeError "stamp の引数が合わない(偽の欠陥)"))
                             (real-stamp before after actor #* rest))))
  (val faults [])
  (val script (Script [[(! (req "POST" "/heartbeat" {"name" "w" "provides" ["net"] "capacity" 10 "versions" V}))
                        (! (req "PUT" "/board/k" [1 2]))]]))
  (<- final ClusterState ((! (scripted script)) ((fault-log faults) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= (lfor r script.replies #((get r 0) (get r 1))) [#("/heartbeat" 500) #("/board/k" 400)]) script.replies)
  (val body (get (get script.replies 0) 2))
  (assert (get body "fault") body)
  (assert (in "TypeError" (get body "error")) body)
  (assert (= (len faults) 1) faults)
  (val fault (get faults 0))
  (assert (= #(fault.method fault.path fault.error-type) #("POST" "/heartbeat" "TypeError")) fault)
  (assert (in "test_coordinator.hy" fault.where) fault)
  ;; 状態は受ける前のまま(途中まで進めた変化を残さない)
  (assert (not-in "w" final.workers)))


(deftest test-a-declaration-with-an-empty-name-or-revision-is-400-and-the-loop-goes-on
  ;; 失敗ケース(#2796 — #2788 の下見で見つけた後退): 名前の無い POST /resources/Service と revision が "" の PUT /resources/Service/<名> は
  ;; 送り手の誤りで 400。直す前は宣言の JSON → JobSpec の不変条件の素の ValueError が、本文の読み(ReadBody — 判断の欠陥の囲みの外)から
  ;; 調停ループの外まで抜け、coordinator の process ごと落ちた。後ろの要求(GET /state)にも答え、宣言は書かない。
  (val faults [])
  (val script (Script [(! (req "POST" "/resources/Service" {"spec" {"run" SAMPLE-RUN "revision" "r" "needs" ["net"]}}))
                       (! (req "PUT" "/resources/Service/a" {"spec" {"run" SAMPLE-RUN "revision" "" "needs" ["net"]}}))
                       (! (req "GET" "/state"))]))
  (<- final ClusterState ((! (scripted script)) ((fault-log faults) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= (lfor r script.replies #((get r 0) (get r 1)))
             [#("/resources/Service" 400) #("/resources/Service/a" 400) #("/state" 200)])
          script.replies)
  (assert (in "名前" (get (get (get script.replies 0) 2) "error")) script.replies)
  (assert (in "revision" (get (get (get script.replies 1) 2) "error")) script.replies)
  (assert (= faults []) faults)
  (assert (= final.jobs #()) final.jobs))


(deftest test-a-fault-while-reading-a-body-is-500-and-the-loop-goes-on [monkeypatch]
  ;; 失敗ケース(#2796): 本文の読みの中で予期しない例外が上がっても(Service の宣言の読みを壊す)、その要求 1 件を 500 の Fault で答え、
  ;; log に 1 行(CoordinatorFault — 上がった所が出る)出して、調停ループは止まらない。直す前は例外が調停ループの外まで抜けた。
  (import doeff_cluster.coordinator.protocol.request_bodies)
  (monkeypatch.setattr doeff_cluster.coordinator.protocol.request_bodies "service_body_of"
                       (fn [body parts] (raise (TypeError "宣言の本文の読みの欠陥(偽)"))))
  (val faults [])
  (val script (Script [(! (req "PUT" "/resources/Service/a" {"spec" {"run" SAMPLE-RUN "revision" "r" "needs" ["net"]}}))
                       (! (req "GET" "/state"))]))
  (<- final ClusterState ((! (scripted script)) ((fault-log faults) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= (lfor r script.replies #((get r 0) (get r 1))) [#("/resources/Service/a" 500) #("/state" 200)]) script.replies)
  (assert (get (get (get script.replies 0) 2) "fault") script.replies)
  (assert (= (lfor f faults #(f.method f.path f.error-type)) [#("PUT" "/resources/Service/a" "TypeError")]) faults)
  (assert (in "test_coordinator.hy" (. (get faults 0) where)) faults)
  (assert (= final.jobs #()) final.jobs))


;; --- shim(Python のまま残す見張り)-----------------------------------------------------------

(defk shim [#* command]
  {:pre [(: command tuple)] :post [(: % subprocess.Popen)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "shim の process を 1 つ起こすため(command = shim が子として起こす job の命令)。"
  ;; worker と同じく、shim を新しい group の先頭として起動し、stdin のパイプを握る。
  (subprocess.Popen [sys.executable "-m" "doeff_cluster.shim" "1" "--" #* command]
                    :stdin subprocess.PIPE :start-new-session True))

(deftest test-shim-passes-the-job-exit-code
  (assert (= (.wait (! (shim sys.executable "-c" "raise SystemExit(3)")) :timeout 30) 3)))

(deftest test-shim-stops-the-job-when-the-worker-goes-away
  (<- p subprocess.Popen (shim sys.executable "-c" "import time; time.sleep(60)"))
  (time.sleep 1.0)
  (assert (is-not p.stdin None) "shim は stdin をパイプで開く")
  (.close p.stdin) ; worker が消えた時と同じ(パイプの EOF)
  (assert (!= (.wait p :timeout 30) 0)))


(deftest test-wal-store-keeps-answered-batches-and-drops-a-torn-tail [tmp-path]
  ;; 耐久の置き場: 返事を済ませた(fsync まで終えた)まとまりは読み直しで必ず戻る。fsync の途中で落ちたまとまり(最後の切れた行)は
  ;; 捨てる(その送り手には返事をしていない)。まとめ直しの後も同じ。
  (import doeff_cluster.foundation.wal_store [WalStore] doeff_cluster.coordinator.protocol.store [durable-load durable-persist durable-checkpoint])
  (val store (WalStore (str tmp-path) :max-log-bytes 10000000))
  (<- (durable-load store))
  (<- (durable-persist store {"board/a" {"value" 1 "resourceVersion" 1}}))
  (<- (durable-persist store {"service/s" {"name" "s"} "counter" {"revision" 3}}))
  ;; 3 まとまり目を書いている途中で落ちた(改行の前で切れた)
  (with [f (open (/ tmp-path "wal.jsonl") "ab")] (.write f b"{\"seq\": 3, \"delta\": {\"board/b\": "))
  (val again (WalStore (str tmp-path)))
  (<- kv dict (durable-load again))
  (assert (= kv {"board/a" {"value" 1 "resourceVersion" 1} "service/s" {"name" "s"} "counter" {"revision" 3}}))
  ;; 切れた行は捨てられ、次の書きは seq 3 から続く
  (<- (durable-persist again {"board/a" None}))
  (<- (durable-checkpoint again))
  (<- (durable-persist again {"board/c" {"value" 5 "resourceVersion" 1}}))
  (val third (WalStore (str tmp-path)))
  (assert (= (! (durable-load third)) {"service/s" {"name" "s"} "counter" {"revision" 3} "board/c" {"value" 5 "resourceVersion" 1}}))
  (assert (= third.seq 4)))


(defhandler no-persist-script [#^ Script script]
  (NextRequests [timeout-seconds limit]
    (.wait-a-little script)
    (setv item (if script.requests (.pop script.requests 0) []))
    (resume (if (isinstance item list) item [item])))
  (Reply [request status body] (.append script.replies #(request.path status body)) (resume None))
  (CoordinatorStopRequested [] (resume (and (not script.requests) (> script.now 3000)))))

(deftest test-state-survives-a-restart-through-the-log-with-the-same-versions
  ;; 資源の書き(版つき)を追記の log へ永続化し、読み直した状態の資源の版と宣言が同じ。
  (import tempfile)
  (import doeff_cluster.foundation.wal_store [WalStore] doeff_cluster.coordinator.protocol.store [wal-store durable-load])
  (import doeff_cluster.coordinator.protocol.durable_kv [durable-kv state-from-kv])
  (setv d (tempfile.mkdtemp) store (WalStore d))
  (<- (durable-load store))
  (setv script (Script [(! (req "POST" "/resources/Service" {"name" "a" "spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN}}))
                        (! (req "PUT" "/board/k" {"value" 1}))]))
  (<- final ClusterState ((sim-time-handler :clock script.clock) ((no-persist-script script) ((wal-store store) (request-bodies (durable-states (run-coordinator (ClusterState) T (ClusterNaming))))))))
  (setv back (! (state-from-kv (! (durable-load (WalStore d))) 99999)))
  (assert (= (! (durable-kv back)) (! (durable-kv final))))
  (assert (= (. back revision) (. final revision)))
  (assert (= (dfor #(k row) (.items back.board) k row.value) {"k" 1})))


;; --- 置き先の鍵の改名(2026-09-25): 改名の前に書いた置き場から起動する --------------------------------------------

(defk legacy-store [d]
  {:pre [(: d str)] :post [(: % WalStore)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "改名の前の coordinator が書いた形の置き場を dir d に作る — 置き先が旧い接頭辞(durable_kv.LEGACY-PLACEMENT)の鍵に在る置き場から
   起動する検のため。"
  (val store (WalStore d))
  (<- (durable-load store))
  (<- (durable-persist store {"counter" {"nextTask" 1 "revision" 2 "auditSeq" 0}
                   "service/a" {"name" "a" "revision" "r" "needs" ["net"] "pin" None "replicas" 1 "readiness" None
                                "owner" None "run" SAMPLE-RUN}
                   (+ LEGACY-PLACEMENT "a") {"job" "a" "worker" "zeus" "generation" 3 "since_ms" 100}}))
  (assert (is-not store.handle None) "開いた置き場は log の handle を持つ")
  (.close store.handle)
  store)


(deftest test-a-store-written-before-the-rename-starts-with-its-placements-and-moves-the-keys
  (import json)
  (import tempfile)
  (import pathlib [Path])
  (import doeff [with-handlers])
  (import doeff_core_effects.handlers [slog-handler])
  (import doeff_cluster.coordinator.entry.main [load-state])
  (setv d (tempfile.mkdtemp))
  (<- (legacy-store d))
  ;; 置き場が在るので以前の形の file は読まない — 読み直しの 1 行の報告(slog)だけに答える。
  (<- state (with-handlers [slog-handler] (load-state (str (/ (Path d) "absent.json")) (WalStore d) 5000)))
  ;; 読んだ置き先は旧い鍵の中身そのもの(担い手も世代も変わらない = worker は process を起こし直さない)
  (assert (= (. (get state.placements "a") worker) "zeus"))
  (assert (= (. (get state.placements "a") generation) 3))
  ;; 置き場は新しい鍵だけを持つ(読み直しても同じ)
  (setv kv (! (durable-load (WalStore d))))
  (assert (in (+ PLACEMENT "a") kv))
  (assert (not-in (+ LEGACY-PLACEMENT "a") kv))
  ;; 旧い鍵を消した書きは、新しい鍵を書いた書きより後(log の行の順)
  (setv deltas (lfor line (.splitlines (.read-text (/ (Path d) "wal.jsonl") :encoding "utf-8")) (get (json.loads line) "delta")))
  (setv wrote (next (gfor #(i x) (enumerate deltas) :if (in (+ PLACEMENT "a") x) i)))
  (setv dropped (next (gfor #(i x) (enumerate deltas) :if (and (in (+ LEGACY-PLACEMENT "a") x) (is (get x (+ LEGACY-PLACEMENT "a")) None)) i)))
  (assert (< wrote dropped) deltas)
  ;; 2 回目の起動は置き先の鍵を書かない(起動ごとに書くのは、ずらした時計と生きていた時刻の 1 行だけ — durable_kv.resume-writes)
  (setv before (len deltas))
  (<- again (with-handlers [slog-handler] (load-state (str (/ (Path d) "absent.json")) (WalStore d) 6000)))
  (assert (= (. (get again.placements "a") worker) "zeus"))
  (setv later (cut (lfor line (.splitlines (.read-text (/ (Path d) "wal.jsonl") :encoding "utf-8")) (get (json.loads line) "delta"))
                   before None))
  (assert (<= (len later) 1) later)
  (assert (not (any (gfor x later k x (or (.startswith k PLACEMENT) (.startswith k LEGACY-PLACEMENT))))) later))


(deftest test-the-new-placement-key-wins-over-the-legacy-one-and-is-not-overwritten
  (import doeff_cluster.coordinator.protocol.durable_kv [state-from-kv legacy-key-moves])
  (setv kv {(+ LEGACY-PLACEMENT "a") {"job" "a" "worker" "old" "generation" 1 "since_ms" 0}
            (+ PLACEMENT "a") {"job" "a" "worker" "new" "generation" 2 "since_ms" 10}
            (+ LEGACY-PLACEMENT "b") {"job" "b" "worker" "zeus" "generation" 5 "since_ms" 0}})
  (assert (= (. (get (. (! (state-from-kv kv 0)) placements) "a") worker) "new"))
  (assert (= (. (get (. (! (state-from-kv kv 0)) placements) "b") worker) "zeus"))
  ;; 新しい鍵が在る a は書かず、無い b だけを書く。消すのは両方の旧い鍵
  (assert (= (! (legacy-key-moves kv))
             [{(+ PLACEMENT "b") {"job" "b" "worker" "zeus" "generation" 5 "since_ms" 0}}
              {(+ LEGACY-PLACEMENT "a") None (+ LEGACY-PLACEMENT "b") None}]))
  (assert (= (! (legacy-key-moves {(+ PLACEMENT "a") {}})) [])))


(deftest test-a-state-file-written-before-the-rename-keeps-its-placements
  ;; 追記の log の置き場より前の形(state.json)も、改名の前の欄の名で置き先を持っている。
  (setv data {"formatVersion" 2 "jobs" [] "workers" [] "tasks" [] "nextTask" 1
              "assignments" {"a" {"job" "a" "worker" "atlas" "generation" 4 "since_ms" 0}}})
  (assert (= (. (get (. (! (state-from-json data 0)) placements) "a") generation) 4))
  (assert (in "placements" (! (state-to-json (! (state-from-json data 0))))))
  (assert (not-in "assignments" (! (state-to-json (! (state-from-json data 0)))))))


(deftest test-the-http-intake-splits-the-path-and-undoes-the-percent-code-per-part
  ;; percent の符号を戻すのは HTTP の境の 1 か所(#1636)。区切りの中の %2F は区切りを増やさずに / へ戻り、path は受けたまま残る。
  (val request (! (http-request "PUT" "/board/team%2Fa/b%20c" {} {"value" 1} :actor "c-test")))
  (assert (= request.parts #("board" "team/a" "b c")) request.parts)
  (assert (= request.path "/board/team%2Fa/b%20c"))
  (assert (= request.actor "c-test")))
