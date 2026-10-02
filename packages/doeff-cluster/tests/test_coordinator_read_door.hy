;; coordinator の読みだけの口(--read-port・#2742): 許す経路の表(read_door_policy.READ-ROUTES)に載せた経路だけが答え、表に無い経路は
;; 振り分けの前に 403 で断る(状態にも待ちにも触らない)。調停ループの Program を台本の要求で動かす(tests/test_coordinator の台本)。
(require doeff-hy.macros [defk deftest <- val var])
(import functools [partial])
(import doeff_cluster.shared.intent.protocol [ClusterTiming RequestDoor])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState])
(import doeff_cluster.coordinator.core.read_door_policy [ReadRoute READ-ROUTES])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import tests.test_coordinator [Script scripted beat submit SAMPLE-RUN])

(setv T (ClusterTiming))

;; 読みだけの口に届いた要求(名乗り X-Actor も付ける — 名乗りがあっても表に無ければ断ることを見る)と、全部の経路の口に届いた要求。
;; 引数 = method・path・query・本文。
(setv read-req (partial http-request :actor "c-test" :door RequestDoor.READ)
      main-req (partial http-request :actor "c-test"))


(defk declared []
  {:pre [] :post [(: % ClusterState)] :tags {:context "coordinator" :role "program"}}
  "台本の前の状態を作るため: worker w が名乗り、Service a(版 r1)を宣言した状態。"
  (val heard (get (beat (ClusterState) "w" 0) 0))
  (val script (Script [(main-req "PUT" "/jobs" {} {"jobs" [{"name" "a" "run" SAMPLE-RUN "revision" "r1" "needs" ["net"]}]})]))
  (<- final ClusterState ((scripted script) (run-coordinator heard T (ClusterNaming))))
  final)


(deftest test-the-read-door-answers-the-listed-route-and-refuses-everything-else
  (<- start ClusterState (declared))
  (val refused [(read-req "GET" "/resources/Service" {} None)            ; 一覧は表に無い
                (read-req "GET" "/resources/Service/" {} None)           ; 名の区切りが空
                (read-req "GET" "/resources/Worker/w" {} None)           ; 別の Kind
                (read-req "GET" "/state" {} None)
                (read-req "GET" "/board" {} None)                        ; lease の token を見せる
                (read-req "GET" "/programs/abc" {} None)                 ; 詰めた Program の本体
                (read-req "GET" "/events" {} None)
                (read-req "GET" "/metrics" {} None)
                (read-req "GET" "/readyz" {} None)                       ; probe も読みの口では受けない
                (read-req "GET" "/watch" {"after" "0" "timeoutSeconds" "5"} None)
                (read-req "POST" "/resources/Service" {} {"name" "b" "spec" {"run" SAMPLE-RUN "revision" "r" "needs" ["net"]}})
                (read-req "PUT" "/jobs" {} {"jobs" []})
                (read-req "DELETE" "/resources/Service/a" {} None)])
  (val script (Script [(read-req "GET" "/resources/Service/a" {} None) refused (main-req "GET" "/resources/Service" {} None)]))
  (<- final ClusterState ((scripted script) (run-coordinator start T (ClusterNaming))))
  (val first (get script.replies 0))
  (assert (= (cut first 0 2) #("/resources/Service/a" 200)) first)
  (assert (= (get first 2 "spec" "revision") "r1") first)
  ;; 表に無い経路は全部 403(/watch も待ちにならず同じまとまりで断られる)。
  (val refusals (cut script.replies 1 (+ 1 (len refused))))
  (assert (= (lfor r refusals (get r 1)) (* [403] (len refused))) refusals)
  (assert (all (gfor r refusals (in "読みだけの口" (get r 2 "error")))) refusals)
  ;; 全部の経路の口は今までどおり。
  (assert (= (get script.replies -1 1) 200))
  ;; 断った書き(POST・PUT・DELETE)は状態を変えない: Service は a だけで版も同じ。
  (assert (= (lfor j final.jobs #(j.spec.name j.spec.revision)) [#("a" "r1")])))


(deftest test-a-get-that-extends-a-task-lease-is-refused-on-the-read-door
  ;; GET /tasks/<id> は GET でも task の lease を延ばす(状態を変える)— 読みの口では断り、lease は延びない。全部の経路の口では延びる。
  (val submitted (submit (get (beat (ClusterState) "w" 0) 0) 100))
  (val state (get submitted 0))
  (val id (get submitted 1))
  (val before (. (get state.tasks id) lease-until-ms))
  (val read-script (Script [(read-req "GET" (+ "/tasks/" id) {} None)]))
  (<- after-read ClusterState ((scripted read-script) (run-coordinator state T (ClusterNaming))))
  (assert (= (get read-script.replies 0 1) 403))
  (assert (= (. (get after-read.tasks id) lease-until-ms) before) "読みの口の問い合わせは lease を延ばさない")
  (val main-script (Script [(main-req "GET" (+ "/tasks/" id) {} None)]))
  (<- after-main ClusterState ((scripted main-script) (run-coordinator state T (ClusterNaming))))
  (assert (= (get main-script.replies 0 1) 200))
  (assert (> (. (get after-main.tasks id) lease-until-ms) before) "全部の経路の口の問い合わせは lease を延ばす"))


(deftest test-a-route-added-to-the-coordinator-is-refused-on-the-read-door-until-listed [monkeypatch]
  ;; 振り分け(respond)に新しい書きの経路が足されても、読みの口では表に載せるまで届かない(既定で断る)。新しい経路の代わりに、
  ;; POST /new-write に届いた口の名を 200 で答える偽の respond を被せる — 読みの口の要求は respond まで届かず 403 のまま。
  (import doeff_cluster.coordinator.core.program)
  (import doeff_cluster.coordinator.core.read_door_policy)
  (val real doeff_cluster.coordinator.core.program.respond)
  (monkeypatch.setattr doeff_cluster.coordinator.core.program "respond"
                       (fn [state request now timing body [settled False]]
                         (if (= (tuple request.parts) #("new-write"))
                             #(state 200 {"door" (str request.door)})
                             (real state request now timing body :settled settled))))
  (<- start ClusterState (declared))
  (val both (Script [[(read-req "POST" "/new-write" {} {"x" 1}) (main-req "POST" "/new-write" {} {"x" 1})]]))
  (<- _ ClusterState ((scripted both) (run-coordinator start T (ClusterNaming))))
  (assert (= (lfor r both.replies (get r 1)) [403 200]) both.replies)
  (assert (= (get both.replies 1 2) {"door" "main"}) both.replies)
  ;; 表に載せれば読みの口でも答える(許すかを決めるのは表の 1 か所)。
  (monkeypatch.setattr doeff_cluster.coordinator.core.read_door_policy "READ_ROUTES"
                       (+ READ-ROUTES #((ReadRoute :method "POST" :segments #("new-write")))))
  (val listed (Script [(read-req "POST" "/new-write" {} {"x" 1})]))
  (<- _ ClusterState ((scripted listed) (run-coordinator start T (ClusterNaming))))
  (assert (= (cut (get listed.replies 0) 1 3) #(200 {"door" "read"})) listed.replies))
