;; coordinator: 作り直し・盤の compare-and-set・task の一生(置く・結果・期限・版・担い手の沈黙)・調停ループの Program・shim。
(require doeff-hy.macros [deftest defhandler <-])
(import collections.abc [Callable])
(import dataclasses [replace])
(import subprocess)
(import sys)
(import time)
(import datetime [timedelta])
(import doeff_time [SimClock sim-time-handler])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.cluster_model [ClusterTiming ClusterNaming ClusterState Request NextRequests Reply Persist CoordinatorStopRequested])
(import doeff_cluster.cluster_policy [reconcile state-to-json state-from-json job-from-json])
(import doeff_cluster.api_policy [respond])
(import doeff_cluster.coordinator [run-coordinator])
(import doeff_cluster.wal_store [WalStore])
(import doeff_cluster.durable_kv [LEGACY-PLACEMENT PLACEMENT])

(setv T (ClusterTiming))
(setv V {"python" "3.14.0" "doeff" "1"})

(defn req [method path [body None] [query None] [actor "test"]] (Request method path (or query {}) body :actor actor))

(defn beat [state name now [statuses None] [versions V] [labels None]]
  (respond state (req "POST" "/heartbeat" {"name" name "labels" (or labels {}) "capacity" 10
                                           "versions" versions "statuses" (or statuses [])}) now T))


(deftest test-restart-keeps-placements-of-workers-that-have-not-reported-yet
  ;; 作り直した coordinator へ最初に名乗った worker に全 job が寄らないこと(実測 2026-09-23 の欠陥)。
  (setv #(s _ _) (beat (ClusterState) "a" 0))
  (setv #(s _ _) (beat s "b" 0))
  (setv #(s _ _) (respond s (req "PUT" "/jobs" {"jobs" (lfor i (range 4) {"name" f"s{i}" "entry" "m" "args" [] "revision" "r"})}) 0 T))
  (setv before (dfor #(k v) (.items s.placements) k v.worker))
  (assert (= (set (.values before)) #{"a" "b"}))
  (setv second (state-from-json (state-to-json s) 5000))
  (setv #(second _ _) (beat second "a" 5000)) ; b はまだ名乗っていない
  (assert (= (dfor #(k v) (.items second.placements) k v.worker) before)))


(deftest test-service-declaration-becomes-the-job-entry-command
  (setv job (job-from-json {"name" "turn-runner" "revision" "abc"
                            "run" {"kind" "service" "factory" "m:f" "env" "m:e" "config" {"b" 1 "a" None}}}))
  (assert (= job.spec.entry "doeff_cluster.job_entry"))
  (assert (= job.spec.args #("service" "--factory" "m:f" "--env" "m:e" "--config" "{\"a\": null, \"b\": 1}"))))


(deftest test-board-compare-and-set
  (setv s (ClusterState))
  (setv #(s status _) (respond s (req "PUT" "/board/turn/c1/0" {"value" {"state" "queued"} "expect" None}) 0 T))
  (assert (= status 200))
  ;; 行が在るので「無い時だけ」は断られる
  (setv #(s status body) (respond s (req "PUT" "/board/turn/c1/0" {"value" {"state" "x"} "expect" None}) 0 T))
  (assert (= #(status (get body "current")) #(409 {"state" "queued"})))
  (setv #(s status _) (respond s (req "PUT" "/board/turn/c1/0" {"value" {"state" "running"} "expect" {"state" "queued"}}) 0 T))
  (assert (= status 200))
  (setv #(s status body) (respond s (req "GET" "/board" None {"prefix" "turn/"}) 0 T))
  (assert (= body {"turn/c1/0" {"state" "running"}})))


(defn submit [state now [versions V] [lease 15.0]]
  (setv #(state _ body) (respond state (req "POST" "/tasks" {"env" "m:e" "blob" "B" "versions" versions "revision" "r"
                                                              "requires" {} "name" "n" "leaseSeconds" lease}) now T))
  #(state (get body "task")))


(deftest test-task-goes-to-a-worker-and-its-result-comes-back
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s id) (submit s 100))
  (setv #(s _ body) (beat s "w" 200))
  (assert (= (lfor t (get body "tasks") (get t "id")) [id]))
  (assert (= (get body "tasks" 0 "blob") "B"))
  ;; worker が終わったと報告する(結果の blob を添えて)
  (setv #(s _ body) (beat s "w" 300 [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (assert (= (get body "tasks") [])) ; 終わった task はもう送らない = worker は file を片付ける
  (setv #(s _ view) (respond s (req "GET" (+ "/tasks/" id)) 400 T))
  (assert (= #((get view "phase") (get view "result")) #("finished" "R")))
  ;; 結果は状態の報告(/state)には載せない
  (assert (not-in "result" (get (. s statuses) "w" "jobs" 0))))


(deftest test-task-is-dropped-when-the-caller-stops-asking
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s id) (submit s 0 :lease 5.0))
  (setv #(s _ _) (respond s (req "GET" (+ "/tasks/" id)) 4000 T)) ; 問い合わせが lease を 9000 まで延ばす
  (setv #(s _ body) (beat s "w" 8000))
  (assert (= (len (get body "tasks")) 1))
  (setv #(s _ body) (beat s "w" 9001))
  (assert (= (get body "tasks") [])) ; 担い手は次の拍でその子 process を止める
  (setv #(s _ view) (respond s (req "GET" (+ "/tasks/" id)) 9002 T))
  (assert (= (get view "phase") "missing")))


(deftest test-task-from-a-different-version-is-refused-before-sending
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s id) (submit s 10 :versions (| V {"python" "3.9.6"})))
  (setv task (get s.tasks id))
  (assert (= task.phase "failed"))
  (assert (in "python=3.14.0" task.detail))
  (setv #(s _ body) (beat s "w" 20))
  (assert (= (get body "tasks") [])))


(deftest test-task-of-a-silent-worker-fails-and-is-not-rerun
  (setv #(s _ _) (beat (ClusterState) "w" 0))
  (setv #(s id) (submit s 0 :lease 100.0))
  (setv #(s _ _) (beat s "x" (+ T.reassign-after-ms 1000))) ; w は 0 から移し替えの期限を越えて沈黙、x だけが生きている
  (setv task (get s.tasks id))
  (assert (= task.phase "failed"))
  (assert (in "沈黙" task.detail)))


;; --- 調停ループの Program を台本の要求で動かす -----------------------------------------------

(defclass Script []
  "台本の要求。requests の要素 = 要求 1 件か、要求の list(1 まとまり)。"
  (defn __init__ [self requests [fail-at None]]
    ;; 時刻は doeff-time の仮想の時計(epoch 0 から)。要求を待つ 1 回ごとに 500 ms 進む(台本の NextRequests が時間を使った形)。
    (setv self.requests (list requests) self.replies [] self.saved [] self.clock (SimClock) self.fail-at fail-at))
  (defn [property] #^ int now [self] (clock-ms self.clock))
  (defn #^ None wait-a-little [self]
    (.set-time self.clock (+ self.clock.current-time (timedelta :milliseconds 500)))))

(defclass Crashed [Exception])

(defhandler scripted-requests [#^ Script script]
  (NextRequests [timeout-seconds limit]
    (.wait-a-little script)
    (setv item (if script.requests (.pop script.requests 0) []))
    (resume (if (isinstance item list) item [item])))
  (Reply [request status body] (.append script.replies #(request.path status body)) (resume None))
  (Persist [delta]
    ;; fail-at 回目の永続化で落ちる(fsync の途中で coordinator が落ちた形)
    (when (= (+ (len script.saved) 1) script.fail-at) (raise (Crashed "fsync の途中で落ちた")))
    (.append script.saved delta) (resume None))
  (CoordinatorStopRequested [] (resume (and (not script.requests) (> script.now 3000)))))

(defn #^ Callable scripted [#^ Script script]
  "台本の外側に仮想の時計(script の SimClock)を被せる。"
  (fn [program] ((sim-time-handler :clock script.clock) ((scripted-requests script) program))))

(deftest test-coordinator-loop-answers-after-persisting
  (setv script (Script [(req "POST" "/heartbeat" {"name" "w" "labels" {} "capacity" 10 "versions" V})
                        (req "PUT" "/jobs" {"jobs" [{"name" "a" "entry" "m" "args" [] "revision" "r"}]})
                        (req "PUT" "/board/k" {"value" 1})
                        (req "GET" "/nothing")]))
  (<- final ClusterState ((scripted script) (run-coordinator (ClusterState) T (ClusterNaming))))
  (assert (= (lfor r script.replies (get r 1)) [200 200 200 404]))
  (assert (= (. (get final.placements "a") worker) "w"))
  ;; 永続化は変化のあったまとまりだけ。盤の書きは盤のキー 1 つだけ(資源の状態を書き直さない)
  (setv board-batch (next (gfor d script.saved :if (in "board/k" d) d)))
  (assert (= (get board-batch "board/k") {"value" 1 "resourceVersion" 1}))
  (assert (= (sorted board-batch) ["board/k"])))

(defhandler recording [#^ list order]
  (Persist [delta] (.append order "persist") (<- (Persist delta)) (resume None))
  (Reply [request status body] (.append order (+ "reply " request.path)) (<- (Reply request status body)) (resume None)))

(deftest test-group-commit-answers-a-batch-only-after-one-persist
  ;; 3 件が 1 まとまり: 永続化は 1 回、返事は 3 件とも永続化の後。
  (setv order [])
  (setv script (Script [[(req "PUT" "/board/a" {"value" 1}) (req "PUT" "/board/b" {"value" 2}) (req "GET" "/board")]]))
  (<- final ClusterState ((scripted script) ((recording order) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= order ["persist" "reply /board/a" "reply /board/b" "reply /board"]) order)
  (assert (= (len script.saved) 1)))

(deftest test-a-crash-during-persist-leaves-the-batch-unanswered
  ;; 2 まとまり目の fsync の途中で落ちる: 1 まとまり目の書きは返事済み・2 まとまり目の送り手には返事が来ない(失敗として扱われる)。
  (import pytest)
  (setv script (Script [[(req "PUT" "/board/a" {"value" 1})] [(req "PUT" "/board/b" {"value" 2})]] :fail-at 2))
  (with [(pytest.raises Crashed)]
    (<- _ ClusterState ((scripted script) (run-coordinator (ClusterState) T (ClusterNaming)))))
  (assert (= (lfor r script.replies (get r 0)) ["/board/a"]))
  (assert (= (lfor d script.saved (sorted d)) [["board/a"]])))


;; --- shim(Python のまま残す見張り)-----------------------------------------------------------

(defn shim [#* command]
  ;; worker と同じく、shim を新しい group の先頭として起動し、stdin のパイプを握る。
  (subprocess.Popen [sys.executable "-m" "doeff_cluster.shim" "1" "--" #* command]
                    :stdin subprocess.PIPE :start-new-session True))

(defn test-shim-passes-the-job-exit-code []
  (assert (= (.wait (shim sys.executable "-c" "raise SystemExit(3)") :timeout 30) 3)))

(defn test-shim-stops-the-job-when-the-worker-goes-away []
  (setv p (shim sys.executable "-c" "import time; time.sleep(60)"))
  (time.sleep 1.0)
  (.close p.stdin) ; worker が消えた時と同じ(パイプの EOF)
  (assert (!= (.wait p :timeout 30) 0)))


(defn test-wal-store-keeps-answered-batches-and-drops-a-torn-tail [tmp-path]
  ;; 耐久の置き場: 返事を済ませた(fsync まで終えた)まとまりは読み直しで必ず戻る。fsync の途中で落ちたまとまり(最後の切れた行)は
  ;; 捨てる(その送り手には返事をしていない)。まとめ直しの後も同じ。
  (import doeff_cluster.wal_store [WalStore])
  (setv store (WalStore (str tmp-path) :max-log-bytes 10000000))
  (.load store)
  (.persist store {"board/a" {"value" 1 "resourceVersion" 1}})
  (.persist store {"service/s" {"name" "s"} "counter" {"revision" 3}})
  ;; 3 まとまり目を書いている途中で落ちた(改行の前で切れた)
  (with [f (open (/ tmp-path "wal.jsonl") "ab")] (.write f b"{\"seq\": 3, \"delta\": {\"board/b\": "))
  (setv again (WalStore (str tmp-path)))
  (setv kv (.load again))
  (assert (= kv {"board/a" {"value" 1 "resourceVersion" 1} "service/s" {"name" "s"} "counter" {"revision" 3}}))
  ;; 切れた行は捨てられ、次の書きは seq 3 から続く
  (.persist again {"board/a" None})
  (.checkpoint again)
  (.persist again {"board/c" {"value" 5 "resourceVersion" 1}})
  (setv third (WalStore (str tmp-path)))
  (assert (= (.load third) {"service/s" {"name" "s"} "counter" {"revision" 3} "board/c" {"value" 5 "resourceVersion" 1}}))
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
  (import doeff_cluster.wal_store [WalStore wal-store])
  (import doeff_cluster.durable_kv [durable-kv state-from-kv])
  (setv d (tempfile.mkdtemp) store (WalStore d))
  (.load store)
  (setv script (Script [(req "POST" "/resources/Service" {"name" "a" "spec" {"revision" "r" "entry" "m" "args" []}})
                        (req "PUT" "/board/k" {"value" 1})]))
  (<- final ClusterState ((sim-time-handler :clock script.clock) ((no-persist-script script) ((wal-store store) (run-coordinator (ClusterState) T (ClusterNaming))))))
  (setv back (state-from-kv (.load (WalStore d)) 99999))
  (assert (= (durable-kv back) (durable-kv final)))
  (assert (= (. back revision) (. final revision)))
  (assert (= back.board {"k" 1})))


;; --- 置き先の鍵の改名(2026-09-25): 改名の前に書いた置き場から起動する --------------------------------------------

(defn #^ WalStore legacy-store [#^ str d]
  ;; 改名の前の coordinator が書いた形: 置き先は旧い接頭辞(durable_kv.LEGACY-PLACEMENT)の鍵に在る。
  (setv store (WalStore d))
  (.load store)
  (.persist store {"counter" {"nextTask" 1 "revision" 2 "auditSeq" 0}
                   "service/a" {"name" "a" "revision" "r" "requires" {} "pin" None "replicas" 1 "readiness" None
                                "owner" None "entry" "m" "args" []}
                   (+ LEGACY-PLACEMENT "a") {"job" "a" "worker" "zeus" "generation" 3 "since_ms" 100}})
  (.close store.handle)
  store)


(deftest test-a-store-written-before-the-rename-starts-with-its-placements-and-moves-the-keys
  (import json)
  (import tempfile)
  (import pathlib [Path])
  (import doeff_cluster.coordinator [load-state])
  (setv d (tempfile.mkdtemp))
  (legacy-store d)
  (setv state (load-state (str (/ (Path d) "absent.json")) (WalStore d) 5000))
  ;; 読んだ置き先は旧い鍵の中身そのもの(担い手も世代も変わらない = worker は process を起こし直さない)
  (assert (= (. (get state.placements "a") worker) "zeus"))
  (assert (= (. (get state.placements "a") generation) 3))
  ;; 置き場は新しい鍵だけを持つ(読み直しても同じ)
  (setv kv (.load (WalStore d)))
  (assert (in (+ PLACEMENT "a") kv))
  (assert (not-in (+ LEGACY-PLACEMENT "a") kv))
  ;; 旧い鍵を消した書きは、新しい鍵を書いた書きより後(log の行の順)
  (setv deltas (lfor line (.splitlines (.read-text (/ (Path d) "wal.jsonl") :encoding "utf-8")) (get (json.loads line) "delta")))
  (setv wrote (next (gfor #(i x) (enumerate deltas) :if (in (+ PLACEMENT "a") x) i)))
  (setv dropped (next (gfor #(i x) (enumerate deltas) :if (and (in (+ LEGACY-PLACEMENT "a") x) (is (get x (+ LEGACY-PLACEMENT "a")) None)) i)))
  (assert (< wrote dropped) deltas)
  ;; 2 回目の起動は置き先の鍵を書かない(起動ごとに書くのは、ずらした時計と生きていた時刻の 1 行だけ — durable_kv.resume-writes)
  (setv before (len deltas))
  (setv again (load-state (str (/ (Path d) "absent.json")) (WalStore d) 6000))
  (assert (= (. (get again.placements "a") worker) "zeus"))
  (setv later (cut (lfor line (.splitlines (.read-text (/ (Path d) "wal.jsonl") :encoding "utf-8")) (get (json.loads line) "delta"))
                   before None))
  (assert (<= (len later) 1) later)
  (assert (not (any (gfor x later k x (or (.startswith k PLACEMENT) (.startswith k LEGACY-PLACEMENT))))) later))


(deftest test-the-new-placement-key-wins-over-the-legacy-one-and-is-not-overwritten
  (import doeff_cluster.durable_kv [state-from-kv legacy-key-moves])
  (setv kv {(+ LEGACY-PLACEMENT "a") {"job" "a" "worker" "old" "generation" 1 "since_ms" 0}
            (+ PLACEMENT "a") {"job" "a" "worker" "new" "generation" 2 "since_ms" 10}
            (+ LEGACY-PLACEMENT "b") {"job" "b" "worker" "zeus" "generation" 5 "since_ms" 0}})
  (assert (= (. (get (. (state-from-kv kv 0) placements) "a") worker) "new"))
  (assert (= (. (get (. (state-from-kv kv 0) placements) "b") worker) "zeus"))
  ;; 新しい鍵が在る a は書かず、無い b だけを書く。消すのは両方の旧い鍵
  (assert (= (legacy-key-moves kv)
             [{(+ PLACEMENT "b") {"job" "b" "worker" "zeus" "generation" 5 "since_ms" 0}}
              {(+ LEGACY-PLACEMENT "a") None (+ LEGACY-PLACEMENT "b") None}]))
  (assert (= (legacy-key-moves {(+ PLACEMENT "a") {}}) [])))


(deftest test-a-state-file-written-before-the-rename-keeps-its-placements
  ;; 追記の log の置き場より前の形(state.json)も、改名の前の欄の名で置き先を持っている。
  (setv data {"formatVersion" 2 "jobs" [] "workers" [] "tasks" [] "nextTask" 1
              "assignments" {"a" {"job" "a" "worker" "atlas" "generation" 4 "since_ms" 0}}})
  (assert (= (. (get (. (state-from-json data 0) placements) "a") generation) 4))
  (assert (in "placements" (state-to-json (state-from-json data 0))))
  (assert (not-in "assignments" (state-to-json (state-from-json data 0)))))
