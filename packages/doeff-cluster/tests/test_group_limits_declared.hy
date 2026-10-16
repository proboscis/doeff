;;; 組の同時数の上限の値の持ち主は系の宣言 1 つ(2026-10-12・card ki-b0e8028c8c8d)の検。
;;; 失敗ケース(直す前): 送り手が task ごとに上限の値を名乗り、coordinator は置く task 自身の申告と升の数を比べた — 2 本目の送り手が値を
;;; 上げると、1 本目の申告した上限を越えて置かれ、同じ 2 本でも置いた順で機体の同時数が変わった(上に載る系の再現の検の P1・P2)。今は task は組の名だけを名乗り、coordinator は系が宣言した資源 GroupLimit の値で数え、
;;; 宣言に無い組を名乗る task を受け口で断る。/state の groupLimits に組ごとの宣言の値と置いている数が出る。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(import dataclasses [replace])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo ClusterState TaskRecord ComponentVersion TaskUnplacedKind])
(import doeff_cluster.coordinator.intent.request_bodies [TaskBody ResourceBody])
(import doeff_cluster.coordinator.core.cluster_policy [place-tasks task-unplaced-text submit-task])
(import doeff_cluster.coordinator.core.detached_policy [submit-detached])
(import doeff_cluster.coordinator.core.resource_policy [create-resource update-resource stamp Refused])
(import doeff_cluster.coordinator.core.cluster_policy [resource-version-of])
(import doeff_cluster.shared.intent.detached_model [TaskLimit GroupLimit AwaitDetached DetachedPending DetachedRefused ReadDetachedQueue DetachedQueueReason DetachedQueueGroup DetachedUnknown DetachedUnreachable])
(import doeff_cluster.shared.protocol.detached [detached-queue-of-state])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator])
(import tests.program_rows [program-placed task-body-of])
(import tests.detached_rig [slow-add RIG-PROVIDES])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(setv PY3 #((ComponentVersion "python" "3")))
;; 宣言: 機体ごとに 1 本の組 heavy と、呼び手(区分)ごとに 2 本の組 heavy-caller。
(val MACHINE (GroupLimit :group "heavy" :scope "worker" :limit 1))
(val PER-CALLER (GroupLimit :group "heavy-caller" :scope "cluster" :limit 2 :per-part True))


(defk declared-state [limits]
  {:pre [(: limits tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "詰めた Program を置き場に置き、組の上限 limits を資源の口(POST /resources/GroupLimit と同じ create-resource — 版は stamp が振る)で
   宣言した状態と Program の sha の組 #(状態 sha) を作るため。"
  (<- placed (get tuple #(ClusterState str)) (program-placed (ClusterState) {"python" "3"}))
  (setv #(state sha) placed)
  (var declared state)
  (for [l limits]
    (:= declared (stamp declared
                        (create-resource declared "GroupLimit" (ResourceBody :name l.group :spec {"scope" l.scope "limit" l.limit "perPart" l.per-part "system" "probe"})
                                         "declare" 0)
                        "declare" 0 T)))
  #((replace declared :workers {"zeus" (WorkerInfo "zeus" #("host-zeus") 30 0 :task-reserve 0 :versions PY3)}) sha))


(defk detached-reply [state sha key limits]
  {:pre [(: state ClusterState) (: sha str) (: key str) (: limits list)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "送り手の本文(組の名乗り limits の JSON)を受け口と同じ解きで PUT /detached/<key> として送った #(status 答え 状態) を得るため。"
  (<- body TaskBody (task-body-of {"program" sha "revision" "rev" "name" "check" "needs" ["host-zeus"] "limits" limits}))
  (val reply (submit-detached state key body 0))
  #(reply.status reply.body reply.state))


(defk phases-of [tasks]
  {:pre [(: tasks dict)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の行の phase を id の順に並べるため。"
  (lfor id (sorted tasks) (. (get tasks id) phase)))


(deftest test-two-senders-naming-one-group-are-counted-by-the-one-declared-value
  ;; 2 つの送り手(呼び手 caller-a・caller-b)が同じ組 heavy を名乗る。値は送らない(送れない)ので、置き方は宣言の値 1 で数え、2 本目は
  ;; 送った順に関わらず上限の文で待つ。宣言を 2 に書き直すと(宣言し直し — 版つきの PUT と同じ update-resource)次の置きで 2 本目が置かれる。
  (setv #(state sha) (! (declared-state #(MACHINE PER-CALLER))))
  (setv #(first-status _ after-first) (! (detached-reply state sha "caller-a/check" [{"group" "heavy"}
                                                                              {"group" "heavy-caller" "part" "caller-a"}])))
  (setv #(second-status _ after-second) (! (detached-reply after-first sha "caller-b/check" [{"group" "heavy"}
                                                                                      {"group" "heavy-caller" "part" "caller-b"}])))
  (assert (= #(first-status second-status) #(200 200)) #(first-status second-status))
  (val placed (! (place-tasks 1000 after-second {} T)))
  (assert (= (sorted (! (phases-of placed))) ["assigned" "queued"]) placed)
  (val waiting (next (gfor t (.values placed) :if (= t.phase "queued") t)))
  (assert (= waiting.detail (! (task-unplaced-text TaskUnplacedKind.TASK-LIMITED))) waiting)
  ;; 宣言し直し(値 1 → 2): 送り手は何も変えない。
  (val raised (update-resource (replace after-second :tasks placed) "GroupLimit" "heavy"
                               (ResourceBody :spec {"scope" "worker" "limit" 2 "system" "probe"}
                                             :resource-version (resource-version-of after-second "GroupLimit/heavy"))
                               "declare"))
  (val again (! (place-tasks 2000 raised {} T)))
  (assert (= (! (phases-of again)) ["assigned" "assigned"]) again))


(deftest test-the-per-part-group-counts-each-caller-by-the-declared-value
  ;; 区分ごとに数える組(perPart): 同じ呼び手 caller-a の 3 本は宣言の値 2 本まで、別の呼び手 caller-b の 1 本は別の升で置かれる(機体の組は無い宣言)。
  (setv #(state sha) (! (declared-state #(PER-CALLER))))
  (var current state)
  (for [#(key part) [#("caller-a/1" "caller-a") #("caller-a/2" "caller-a") #("caller-a/3" "caller-a") #("caller-b/1" "caller-b")]]
    (setv #(status _ after) (! (detached-reply current sha key [{"group" "heavy-caller" "part" part}])))
    (assert (= status 200) status)
    (:= current after))
  (val placed (! (place-tasks 1000 current {} T)))
  (val by-key (dfor t (.values placed) t.key t.phase))
  (assert (= by-key {"caller-a/1" "assigned" "caller-a/2" "assigned" "caller-a/3" "queued" "caller-b/1" "assigned"}) by-key))


(deftest test-the-same-tasks-sent-in-either-order-place-the-declared-number-in-one-tick
  ;; 並行(同じ拍・送る順): 組 heavy(worker の範囲・値 2)を名乗る 3 本(呼び手 2 つ)を 2 つの順で送り、1 拍の置き(place-tasks 1 回)で
  ;; 置く本数はどちらの順でも宣言の値 2(同じ拍で置いた分も数える — hold-limits。送る順は値に関わらない)。
  (setv #(state sha) (! (declared-state #((GroupLimit :group "heavy" :scope "worker" :limit 2)))))
  (for [order [["a/1" "a/2" "b/1"] ["b/1" "a/2" "a/1"]]]
    (var current state)
    (for [key order]
      (setv #(status _ after) (! (detached-reply current sha key [{"group" "heavy"}])))
      (assert (= status 200) #(key status))
      (:= current after))
    (val placed (! (place-tasks 1000 current {} T)))
    (assert (= (sorted (! (phases-of placed))) ["assigned" "assigned" "queued"]) #(order placed))))


(deftest test-a-task-naming-an-undeclared-group-or-sending-a-value-is-refused
  ;; 受け口(PUT /detached と POST /tasks)は、宣言に無い組・上限の値や範囲を送る旧い形・区分の名乗りが宣言と合わない名乗りを 400 で
  ;; 断り、どこが違うかを名乗る。断った本文は行を作らない。
  (setv #(state sha) (! (declared-state #(MACHINE PER-CALLER))))
  (for [#(limits word) [#([{"group" "nobody"}] "系の宣言に無い")
                        #([{"group" "heavy" "limit" 2 "scope" "worker"}] "系の宣言(:task-limits)が持つ")
                        #([{"group" "heavy-caller"}] "part を名乗る")
                        #([{"group" "heavy" "part" "caller-a"}] "part 'caller-a' を外す")
                        #([{"group" "heavy"} {"group" "heavy"}] "2 度")
                        #([{"group" "heavy" "part" ""}] "part")]]
    (setv #(status answer after) (! (detached-reply state sha "k" limits)))
    (assert (= status 400) #(limits status answer))
    (assert (in word answer.message) #(word answer.message))
    (assert (= after.tasks state.tasks) after.tasks)
    (<- body TaskBody (task-body-of {"program" sha "revision" "rev" "name" "check" "needs" ["host-zeus"] "limits" limits}))
    (setv #(_ task-status task-answer) (! (submit-task state body 0)))
    (assert (= task-status 400) #(limits task-status task-answer))
    (assert (in word task-answer.message) #(word task-answer.message))))


(deftest test-a-malformed-group-declaration-is-refused-by-the-resource-intake
  ;; 資源の口は系の構成子と同じ規則で組の宣言を検める(1 以上の整数・範囲の名・知らない欄)。系の構成子も同じ値を断る。
  (setv #(state _) (! (declared-state #())))
  (for [#(spec word) [#({"scope" "node" "limit" 1} "scope")
                      #({"scope" "worker" "limit" 0} "limit")
                      #({"scope" "worker" "limit" True} "limit")
                      #({"scope" "worker" "limit" 1 "perPart" "yes"} "perPart")
                      #({"scope" "worker" "limit" 1 "max" 3} "知らない欄")
                      #({"scope" "worker" "limit" 1 "system" ""} "system")]]
    (try
      (create-resource state "GroupLimit" (ResourceBody :name "g" :spec spec) "declare" 0)
      (raise (AssertionError (.format "断られなかった: {}" spec)))
      (except [refused Refused]
        (assert (= refused.status 400) refused.status)
        (assert (in word refused.body.message) #(word refused.body.message)))))
  (try
    (system-of "bad" #() :task-limits #((GroupLimit :group "g" :scope "worker" :limit 0)))
    (raise (AssertionError "系の構成子が上限 0 を断らなかった"))
    (except [error ValueError]
      (assert (in "limit" (str error)) (str error))))
  (try
    (system-of "twice" #() :task-limits #(MACHINE MACHINE))
    (raise (AssertionError "系の構成子が同じ組 2 つを断らなかった"))
    (except [error ValueError]
      (assert (in "2 つ" (str error)) (str error)))))


;; --- sim(本物の coordinator の調停ループと本物の worker)----------------------------------------------------

(val LOCAL (frozenset RIG-PROVIDES))
(val SYSTEM (system-of "group-limit-scenarios" #() :task-limits #((GroupLimit :group "probe" :scope "worker" :limit 1))))


(defk group-limit-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "系の宣言の組 probe(機体ごとに 1 本)を名乗る長い task 2 本を出し、1 本目が置かれた後の GET /state を返すため。宣言に無い組を名乗る
   3 本目は送る所で断られる(DetachedRefused)。"
  (<- (submit-detached-task (slow-add 30.0 1) :needs LOCAL :key "probe-1" :lease-seconds 5.0 :limits #((TaskLimit :group "probe"))))
  (<- (submit-detached-task (slow-add 30.0 2) :needs LOCAL :key "probe-2" :lease-seconds 5.0 :limits #((TaskLimit :group "probe"))))
  (var placed None)
  (var waited 0.0)
  (while (is placed None)
    (assert (< waited 20.0) "1 本目が 20 秒の内に置かれない")
    (<- answer (AwaitDetached "probe-1" :timeout-seconds 0.0))
    (if (and (isinstance answer DetachedPending) (= answer.phase "assigned"))
        (:= placed answer)
        (do (<- (Delay 0.5)) (:= waited (+ waited 0.5)))))
  ;; 置いた後の拍(2 本目の待ちの文が書かれる)を 1 つ待ってから読む。
  (<- (Delay 1.0))
  (<- state dict (ReadCoordinator "/state"))
  (var refused None)
  (try
    (<- (submit-detached-task (slow-add 1.0 3) :needs LOCAL :key "nobody-1" :lease-seconds 5.0 :limits #((TaskLimit :group "nobody"))))
    (except [error DetachedRefused]
      (:= refused (str error))))
  (| state {"refusedNobody" refused}))


(deftest test-the-state-shows-each-group-declared-value-and-held-count-on-sim
  ;; sim の宣言(系の :task-limits → 資源 GroupLimit)で組 probe を宣言し、同じ組を名乗る 2 本を出す: 宣言の値 1 で 1 本だけ置かれ、
  ;; /state の groupLimits に {group probe・scope worker・limit 1・held [{worker w1 count 1}]・queued 1} が出る。宣言に無い組は断られる。
  (<- state dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) SYSTEM (group-limit-scenario)
                              :workers #((SimWorker :name "w1" :provides LOCAL :task-reserve 0))))
  (val groups (get state "groupLimits"))
  (assert (= groups [{"group" "probe" "scope" "worker" "limit" 1 "system" "group-limit-scenarios" "resourceVersion" (get (get groups 0) "resourceVersion")
                      "held" [{"worker" "w1" "count" 1}] "queued" 1}])
          groups)
  (assert (isinstance (get (get groups 0) "resourceVersion") int) groups)
  (val phases (sorted (gfor t (get state "tasks") :if (in (.get t "key") #("probe-1" "probe-2")) (get t "phase"))))
  (assert (= phases ["assigned" "queued"]) phases)
  (assert (in "系の宣言に無い" (get state "refusedNobody")) (get state "refusedNobody")))


(deftest test-a-stored-task-row-naming-values-is-an-old-row
  ;; 保存に残った旧い形の行(task が上限の値と範囲を名乗る limits — 2026-10-12 より前)は、読み分けて値を捨てて数えない: まだ終わって
  ;; いない行は理由つきの failed・終わった行はそのまま(どちらも組を名乗らない)。新しい形の行は往復しても変わらない。
  (import doeff_cluster.coordinator.protocol.cluster_json [task-record-to-json task-record-from-json])
  (val row (TaskRecord "t1" "check" "sha" "rev" PY3 #("host-zeus") 15000 20000 0 :detached True :key "k"
                       :limits #((TaskLimit :group "heavy") (TaskLimit :group "heavy-caller" :part "caller-a"))))
  (val stored (task-record-to-json row))
  (<- back TaskRecord (task-record-from-json stored))
  (assert (= back row) back)
  (val old (| stored {"limits" [{"group" "heavy" "limit" 6 "scope" "worker"}]}))
  (<- queued TaskRecord (task-record-from-json old))
  (assert (= #(queued.phase queued.limits) #("failed" #())) queued)
  (assert (in "上限の値と範囲は系の宣言" queued.detail) queued.detail)
  (<- ended TaskRecord (task-record-from-json (| old {"phase" "finished" "detail" "done"})))
  (assert (= #(ended.phase ended.detail ended.limits) #("finished" "done" #())) ended))


;; --- 待っている task の理由を外から読む(ReadDetachedQueue — 依頼 lt-6203WCGWA41PGZ5KWK6FYZZBPA)-----------------------------------------

(defk queue-reason-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "group-limit-scenario と同じ 2 本を出し、2 本目(上限で待つ)と 1 本目(置かれた)の ReadDetachedQueue の答えを返すため。"
  (<- state dict (group-limit-scenario))
  (<- waiting (ReadDetachedQueue "probe-2"))
  (<- placed (ReadDetachedQueue "probe-1"))
  (| state {"waiting" waiting "placed" placed}))


(deftest test-a-queued-task-reads-which-group-holds-it-back-on-sim
  ;; 失敗ケース: 組 probe(機体ごとに 1 本)が埋まって queued の task を、「置き先が無い」と区別できない — 待たせている組の名・範囲・今の数・
  ;; 上限の値・待ち数が答えに出る事で、「列に並んでいる」と読める。置かれた task も同じ組の読みを持つ。
  (<- state dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) SYSTEM (queue-reason-scenario)
                              :workers #((SimWorker :name "w1" :provides LOCAL :task-reserve 0))))
  (val waiting (get state "waiting"))
  (assert (isinstance waiting DetachedQueueReason) waiting)
  (assert (= waiting.phase "queued") waiting)
  (assert (= waiting.groups #((DetachedQueueGroup :group "probe" :part "" :scope "worker" :limit 1 :held 1 :queued 1 :full True))) waiting)
  (assert (in "同時数" waiting.detail) waiting.detail)
  (val placed (get state "placed"))
  (assert (and (isinstance placed DetachedQueueReason) (= placed.phase "assigned")) placed)
  (assert (= (. (get placed.groups 0) group) "probe") placed))


(deftest test-the-queue-reading-of-a-state-follows-the-claimed-groups
  ;; 純粋な読み: 区分ごとの組は名乗った区分の升だけを数える・範囲 worker の組は最大の升を held に取る・宣言に無い組は読みに入れない・
  ;; 上限に達していなければ full は偽(組の外の理由 — 空きが無い など — で待っている)・届かない読みと形の違う返事は DetachedUnreachable。
  (val state {"tasks" [{"key" "k" "phase" "queued" "detail" "空きが無い"
                        "limits" [{"group" "per" "part" "c2"} {"group" "wk"} {"group" "gone"}]}]
              "groupLimits" [{"group" "per" "scope" "cluster" "limit" 2 "perPart" True "queued" 3
                              "held" [{"count" 2 "part" "c1"} {"count" 1 "part" "c2"}]}
                             {"group" "wk" "scope" "worker" "limit" 4 "queued" 0
                              "held" [{"count" 1 "worker" "a"} {"count" 3 "worker" "b"}]}]})
  (<- reason (detached-queue-of-state "k" state))
  (assert (= reason (DetachedQueueReason :key "k" :phase "queued" :detail "空きが無い"
                                         :groups #((DetachedQueueGroup :group "per" :part "c2" :scope "cluster" :limit 2 :held 1 :queued 3 :full False)
                                                   (DetachedQueueGroup :group "wk" :part "" :scope "worker" :limit 4 :held 3 :queued 0 :full False))))
          reason)
  (<- unknown (detached-queue-of-state "none" state))
  (assert (= unknown (DetachedUnknown :key "none")) unknown)
  (<- cut (detached-queue-of-state "k" "connection refused"))
  (assert (and (isinstance cut DetachedUnreachable) (in "connection refused" cut.detail)) cut)
  (<- bad (detached-queue-of-state "k" {"tasks" [{"key" "k"}]}))
  (assert (isinstance bad DetachedUnreachable) bad))
