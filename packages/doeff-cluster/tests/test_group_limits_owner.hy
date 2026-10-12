;;; 組の同時数の上限の値の持ち主を 1 つに保つ検(設計検証 lt-9E0W15ZW21H6R3SA26CNW1T4FP の盲検 A・B を受けた失敗ケース・card ki-b0e8028c8c8d)。
;;;
;;; - 盲検 A: 別の系(defsystem)が同じ組を違う値で宣言すると、資源 GroupLimit は後に宣言した系の値で黙って書き直され、置ける本数が
;;;   宣言の順で変わった(値の割れが「送り手の順」から「宣言の順」へ移った)。組を宣言する系は 1 つで、別の系の書き直しは断る。
;;;   job の無い宣言(組だけ)は本番の apply-declaration で IndexError になった(sim は通る)。
;;; - 盲検 B: 送り手が組の名乗りの兄弟の欄(例 slots — 1 本が枠を何本分使うか)を送り、置き方がそれで数えると、提示の検を全部通りながら
;;;   効く値が送り手と送った順で割れた。送り手の名乗る欄の集合と、数えの関数が受ける物を契約の検で固定する。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [fields replace])
(import inspect)
(import doeff [with-handlers])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.intent.detached_model [TaskLimit GroupLimit SubmitDetached TASK-LIMIT-SCOPES])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task group-limit-spec GROUP-LIMIT-KEYS])
(import doeff_cluster.shared.entry.service_build [system-of system-declaration])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.coordinator.intent.request_bodies [TaskBody ResourceBody])
(import doeff_cluster.coordinator.core.resource_policy [create-resource update-resource stamp Refused])
(import doeff_cluster.coordinator.core.cluster_policy [resource-version-of])
(import doeff_cluster.coordinator.core [cluster_policy])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json group-limit-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.sim.local [sim-cluster SimWorker ReadCoordinator Redeclare])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_hy [system_form])
(import tests.detached_rig [slow-add RIG-PROVIDES])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val LOCAL (frozenset RIG-PROVIDES))
(val ALPHA (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 1))))
(val BETA (system-of "beta" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 3))))


(deftest test-system-of-names-the-declaring-system-on-its-limits
  ;; 系の構成子は組の上限に宣言する系の名を付ける(資源 GroupLimit の spec の system に載る)。別の系の名の付いた上限は断る。
  (assert (= (. (get ALPHA.task-limits 0) system) "alpha") ALPHA.task-limits)
  (assert (= (group-limit-spec (get ALPHA.task-limits 0)) {"scope" "cluster" "limit" 1 "system" "alpha"}) ALPHA.task-limits)
  (try
    (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 1 :system "beta")))
    (raise (AssertionError "別の系の名の付いた上限を断らなかった"))
    (except [error ValueError]
      (assert (in "beta" (str error)) (str error)))))


(defk declared [system-limits]
  {:pre [(: system-limits tuple)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "組の上限 system-limits を資源の口(POST /resources/GroupLimit と同じ create-resource — 版は stamp が振る)で宣言した状態を作るため。"
  (var state (ClusterState))
  (for [l system-limits]
    (:= state (stamp state (create-resource state "GroupLimit" (ResourceBody :name l.group :spec (group-limit-spec l)) "declare" 0)
                     "declare" 0 T)))
  state)


(deftest test-a-group-declared-by-one-system-is-not-rewritten-by-another-system
  ;; 盲検 A: alpha が宣言した組 llm を、beta の宣言(違う値)が版つきで書き直そうとしても、資源の口は 409 で断る(1 つの組を宣言する系は 1 つ)。
  ;; alpha 自身の宣言し直しは通る。
  (<- state ClusterState (declared ALPHA.task-limits))
  (val version (resource-version-of state "GroupLimit/llm"))
  (try
    (update-resource state "GroupLimit" "llm" (ResourceBody :spec (group-limit-spec (get BETA.task-limits 0)) :resource-version version) "declare")
    (raise (AssertionError "別の系の書き直しを断らなかった"))
    (except [refused Refused]
      (assert (= refused.status 409) refused.status)
      (assert (in "alpha" refused.body.message) refused.body.message)))
  (val raised (update-resource state "GroupLimit" "llm"
                               (ResourceBody :spec {"scope" "cluster" "limit" 2 "system" "alpha"} :resource-version version) "declare"))
  (assert (= (. (.row raised.group-limits "llm") limit) 2) raised.group-limits))


(deftest test-a-declared-group-and-its-system-survive-the-state-file-and-the-durable-keys
  ;; 保存先(state file・耐久の鍵)で組の上限と持ち主の系が往復する(再起動の後も別の系の書き直しを断れる)。壊れた保存の値(系の名の無い
  ;; spec・0 の上限)は保存の読み直しが理由つきで断る(黙って既定の値で読まない)。
  (<- state ClusterState (declared ALPHA.task-limits))
  (<- data dict (state-to-json state))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= (.row from-file.group-limits "llm") (get ALPHA.task-limits 0)) from-file.group-limits)
  (<- kv dict (full-kv state))
  (<- from-keys ClusterState (state-from-kv kv 0))
  (assert (= (.row from-keys.group-limits "llm") (get ALPHA.task-limits 0)) from-keys.group-limits)
  (for [#(spec word) [#({"scope" "cluster" "limit" 1} "system") #({"scope" "cluster" "limit" 0 "system" "alpha"} "limit")]]
    (try
      (group-limit-from-json "llm" spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {}" spec)))
      (except [error ValueError]
        (assert (in word (str error)) #(word (str error)))))))


(defk second-system-scenario [second]
  {:pre [(: second System)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "最初の宣言(sim-cluster の系)の後に second の系を宣言し直し(Redeclare — 本番と同じ group-limit-write)、断られた文と、組 llm を名乗る
   長い task 3 本を出して 5 秒(仮想)後の GET /state を返すため。"
  (var refused None)
  (try
    (<- (Redeclare second))
    (except [error Exception]
      (:= refused (str error))))
  (for [i (range 3)]
    (<- (submit-detached-task (slow-add 30.0 i) :needs LOCAL :key (.format "llm-{}" i) :lease-seconds 5.0
                              :limits #((TaskLimit :group "llm")))))
  (<- (Delay 5.0))
  (<- state dict (ReadCoordinator "/state"))
  {"refused" refused "state" state})


(defk llm-summary [observed]
  {:pre [(: observed dict)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "筋書きの答えから、組 llm の宣言の値・持ち主の系・置いている数と task の phase を抜き出すため。"
  (val group (next (gfor g (get (get observed "state") "groupLimits") :if (= (get g "group") "llm") g)))
  {"limit" (get group "limit") "system" (get group "system")
   "held" (sum (gfor h (get group "held") (get h "count")))
   "phases" (sorted (gfor t (get (get observed "state") "tasks") :if (.startswith (.get t "key" "") "llm-") (get t "phase")))})


(deftest test-the-second-system-declaring-a-group-is-refused-on-sim
  ;; 盲検 A の再現の形(sim — 本物の coordinator): alpha の後に beta が同じ組を宣言すると、beta の宣言は断られ(黙って書き直さない)、組 llm は
  ;; alpha の値 1 で数えられる。
  (<- observed dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) ALPHA (second-system-scenario BETA)
                                 :workers #((SimWorker :name "w1" :provides LOCAL :task-reserve 0))))
  (assert (and (get observed "refused") (in "alpha" (get observed "refused"))) (get observed "refused"))
  (<- summary dict (llm-summary observed))
  (assert (= summary {"limit" 1 "system" "alpha" "held" 1 "phases" ["assigned" "queued" "queued"]}) summary))


(defhandler group-only-coordinator [#^ dict world #^ int post-status]
  ;; coordinator の代役(本番の declare の検): GroupLimit は無い(GET 404)・作り(POST)は post-status で答え、送られた本文を world の
  ;; "posts" の組に足す。他の要求は来ない筈。引数に残す理由: 代役の外の世界の記録と答えの番号は検ごとに作る値で、業務の設定ではない。
  (HttpRequest [method url body]
    (cond
      (and (= method "GET") (in "/resources/GroupLimit/" url))
        (resume (HttpResponse 404 {} b"" "" url 0.0))
      (and (= method "POST") (.endswith url "/resources/GroupLimit"))
        (do (setv (get world "posts") (+ (get world "posts") #(body)))
            (resume (HttpResponse post-status {} b"" "" url 0.0)))
      True (raise (AssertionError (.format "思わない要求: {} {}" method url))))))


(deftest test-the-production-declare-writes-the-limits-of-a-declaration-without-jobs
  ;; 盲検 A の観測 5: job の無い宣言(組の上限だけ)を本番の apply-declaration で書く — 以前は rows[0] を読んで IndexError(sim は通った)。
  (val world {"posts" #()})
  (val declaration (system-declaration ALPHA "rev" :versions {"python" "3"}))
  (<- ok bool (with-handlers [slog-discard-handler (group-only-coordinator world 201)]
                (apply-declaration "http://coordinator.test" declaration "c-test")))
  (assert ok)
  (assert (= (get world "posts") #({"name" "llm" "spec" {"scope" "cluster" "limit" 1 "system" "alpha"}})) world))


(deftest test-the-production-declare-stops-at-the-first-refused-group-like-the-sim
  ;; 本番の宣言の書きは、組の上限の書きが 1 つ断られたら(例 別の系の組で 409)残りの組を書かずに偽 — sim の宣言の書きと同じ所で止まる
  ;; (以前は残りの組を書き続けてから偽を返し、sim は最初で止まった — 設計のレビューの指摘 1-d)。
  (val world {"posts" #()})
  (val two (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 1)
                                                 (GroupLimit :group "own" :scope "worker" :limit 2))))
  (<- ok bool (with-handlers [slog-discard-handler (group-only-coordinator world 409)]
                (apply-declaration "http://coordinator.test" (system-declaration two "rev" :versions {"python" "3"}) "c-test")))
  (assert (not ok))
  (assert (= (tuple (gfor b (get world "posts") (get b "name"))) #("llm")) world))


(deftest test-the-sender-facing-fields-and-the-counting-inputs-are-pinned
  ;; 盲検 B: 送り手の名乗る欄を足して置き方がそれで数えると、提示の検を全部通りながら上限の値が送り手へ戻った。送り手の欄の集合と、数えの
  ;; 関数が受ける物を固定する — 足す時は、組の上限の数えに送り手の値を入れない事を確かめ(値と範囲の持ち主は系の宣言 GroupLimit だけ)、
  ;; この表を直す。
  (assert (= (tuple (gfor f (fields TaskLimit) f.name)) #("group" "part"))
          "TaskLimit は組の名と区分だけ — 値・範囲・重さを task に名乗らせない(値の持ち主は系の宣言)")
  (assert (= (tuple (gfor f (fields SubmitDetached) f.name))
             #("program" "key" "needs" "name" "lease_seconds" "retain_seconds" "environ" "priority" "limits"))
          "SubmitDetached の欄を足す時は、組の上限の数えに入れない事を確かめてからこの表を直す")
  (assert (= (tuple (gfor f (fields TaskBody) f.name))
             #("program" "revision" "name" "needs" "requires" "env" "blob" "versions" "runtime_env" "environ" "lease_seconds"
               "retain_seconds" "priority" "limits" "format"))
          "TaskBody(受け口の本文)の欄を足す時は、組の上限の数えに入れない事を確かめてからこの表を直す")
  ;; 置き方の判断の関数は task の行(TaskRecord — 送り手の欄を全部持つ)を受けず、組の名乗り(TaskLimit の tuple)・置いた worker・
  ;; 置いている数・系の宣言の表だけを受ける。task の行を読むのは、置いている task の名乗りを射影する placed-claims と place-tasks の
  ;; 呼びだけ(既に在る送り手の欄 — environ など — を数えに読ませない。設計のレビューの指摘 2-a)。
  (for [#(f params) [#(cluster_policy.hold-limits ["held" "limits" "worker" "groups"])
                     #(cluster_policy.limits-misfit ["limits" "worker" "held" "groups"])
                     #(cluster_policy.limit-holdings ["claims" "groups"])
                     #(cluster_policy.task-misfit ["limits" "priority" "worker" "room" "held" "groups"])
                     #(cluster_policy.placed-claims ["tasks"])]]
    (assert (= (list (. (inspect.signature f) parameters)) params) #(f.__name__ (inspect.signature f)))))


(deftest test-the-placed-claims-are-one-per-placed-task-whatever-else-the-sender-sent
  ;; 置いている task の名乗りの射影は、置いている task 1 本につき #(名乗った組 置いた worker) を 1 つだけ返し、送り手の他の欄(environ・
  ;; 名・key・優先)を変えても同じ(1 本は升ごとに 1 — 送り手の値で重さを変えない)。待っている task は数えない。
  (val claims #((TaskLimit :group "llm")))
  (val plain (TaskRecord "t1" "check" "sha" "rev" #() #("local") 15000 20000 0 :detached True :key "k1" :limits claims
                         :phase "assigned" :worker "w1"))
  (val loud (replace plain :id "t2" :name "heavy" :key "k2" :priority 7
                           :environ #(#("SLOTS" "4") #("WEIGHT" "9"))))
  (val waiting (replace plain :id "t3" :key "k3" :phase "queued" :worker None))
  (<- answer tuple (cluster_policy.placed-claims {"t1" plain "t2" loud "t3" waiting}))
  (assert (= (sorted answer :key repr) [#(claims "w1") #(claims "w1")]) answer))


(deftest test-the-defsystem-form-reads-the-same-scopes-and-keys-as-the-rule
  ;; 盲検 A の付随の所見: defsystem の形の読み(doeff-hy — doeff-cluster を import できない)は範囲の名と欄の集合を自前で持つ。規則
  ;; (detached_rules)と食い違えば赤にする(系の名 system は系の構成子が付ける欄で、書き手は書かない)。
  (assert (= (frozenset system_form.LIMIT-SCOPES) TASK-LIMIT-SCOPES) system_form.LIMIT-SCOPES)
  (assert (= (frozenset system_form.LIMIT-KEYS) (- GROUP-LIMIT-KEYS #{"system"})) system_form.LIMIT-KEYS))
