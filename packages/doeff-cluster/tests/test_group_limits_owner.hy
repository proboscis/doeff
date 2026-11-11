;;; 組の同時数の上限の値の持ち主を 1 つに保つ検(B を受けた失敗ケース)。
;;;
;;; - 盲検 A: 別の系(defsystem)が同じ組を違う値で宣言すると、資源 GroupLimit は後に宣言した系の値で黙って書き直され、置ける本数が
;;;   宣言の順で変わった(値の割れが「送り手の順」から「宣言の順」へ移った)。組を宣言する系は 1 つで、別の系の書き直しは断る。
;;;   job の無い宣言(組だけ)は本番の apply-declaration で IndexError になった(sim は通る)。
;;; - 盲検 B: 送り手が組の名乗りの兄弟の欄(例 slots — 1 本が枠を何本分使うか)を送り、置き方がそれで数えると、提示の検を全部通りながら
;;;   効く値が送り手と送った順で割れた。送り手の名乗る欄の集合と、数えの関数が受ける物を契約の検で固定する。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [fields replace])
(import inspect)
(import doeff [with-handlers])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects [slog-discard-handler])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [TaskLimit GroupLimit SubmitDetached]
        doeff_cluster._shared.intent.detached_model [TASK-LIMIT-SCOPES])
(import doeff_cluster [submit-detached-task]
        doeff_cluster._shared.core.detached_rules [group-limit-spec GROUP-LIMIT-KEYS])
(import doeff_cluster [system-of system-declaration])
(import doeff_cluster._shared.entry.declare [apply-declaration])
(import doeff_cluster [ClusterState]
        doeff_cluster._coordinator.intent.cluster_model [TaskRecord TaskUnplacedKind PLACED-PHASES])
(import doeff_cluster._coordinator.intent.request_bodies [TaskBody ResourceBody])
(import doeff_cluster [Refused]
        doeff_cluster._coordinator.core.resource_policy [create-resource update-resource stamp])
(import doeff_cluster._coordinator.core.cluster_policy [resource-version-of])
(import doeff_cluster._coordinator.core [cluster_policy])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json group-limit-from-json])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster [sim-cluster SimWorker ReadCoordinator Redeclare])
(import doeff_cluster [System])
(import doeff_cluster._shared.entry._system_form :as system_form)
(import tests.detached_rig [slow-add RIG-PROVIDES])
(import doeff_hy._table [Table])
(import tests.state_rows [table-from-rows])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))
(val LOCAL (frozenset RIG-PROVIDES))
(val ALPHA (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 1))))
(val BETA (system-of "beta" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 3))))


(deftest test-system-of-names-the-declaring-system-on-its-limits
  ;; 系の構成子は組の上限に宣言する系の名を付ける(資源 GroupLimit の spec の system に載る)。既に名の付いた上限(作った系の値を繋ぐ
  ;; 時 — 上に載る系の宣言の道具は 1 度に宣言する系を名 "a+b" の 1 つの系に繋ぐ)は、作った系の名を保つ。繋いだ名で付け直すと、同じ系を
  ;; 別の組み合わせで宣言するたびに持ち主が a+b と a の間で揺れ、資源の口が 409 で断る(以前は名の違いを断り、繋ぐ宣言が止まった)。
  (assert (= (. (get ALPHA.task-limits 0) system) "alpha") ALPHA.task-limits)
  (assert (= (group-limit-spec (get ALPHA.task-limits 0)) {"scope" "cluster" "limit" 1 "system" "alpha"}) ALPHA.task-limits)
  (val joined (system-of "alpha+gamma" #() :task-limits (+ ALPHA.task-limits #((GroupLimit :group "gpu" :scope "worker" :limit 2)))))
  (assert (= (tuple (gfor l joined.task-limits #(l.group l.system))) #(#("llm" "alpha") #("gpu" "alpha+gamma"))) joined.task-limits))


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
  (for [#(spec word) [#({"scope" "cluster" "limit" 1 "system" ""} "system") #({"scope" "cluster" "limit" 0 "system" "alpha"} "limit")]]
    (try
      (group-limit-from-json "llm" spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {}" spec)))
      (except [error ValueError]
        (assert (in word (str error)) #(word (str error)))))))


(deftest test-an-ownerless-group-is-claimed-by-the-first-system-that-declares-it
  ;; 持ち主の無い組(system の無い spec — 74ebb50b6 の宣言の道具が本番の coordinator に書いた 3 組・運用者が資源の口に直に作った組)は受け、
  ;; 保存から読み直せる。最初に宣言し直した系がその組を持ち(版つきの書き直しが system を載せる)、その後は別の系の書き直しを 409 で断る。
  ;; 持ち主の在る組を、system の無い本文(古い宣言の道具)で書き直すのも断る。
  (val bare {"scope" "cluster" "limit" 1})
  (val state (stamp (ClusterState) (create-resource (ClusterState) "GroupLimit" (ResourceBody :name "llm" :spec bare) "declare" 0) "declare" 0 T))
  (assert (= (. (.row state.group-limits "llm") system) "") state.group-limits)
  (<- stored ClusterState (state-from-json (! (state-to-json state)) 0))
  (assert (= (.row stored.group-limits "llm") (.row state.group-limits "llm")) stored.group-limits)
  (val claimed (stamp state (update-resource state "GroupLimit" "llm"
                                             (ResourceBody :spec (group-limit-spec (get ALPHA.task-limits 0))
                                                           :resource-version (resource-version-of state "GroupLimit/llm"))
                                             "declare")
                      "declare" 0 T))
  (assert (= (. (.row claimed.group-limits "llm") system) "alpha") claimed.group-limits)
  (for [spec [(group-limit-spec (get BETA.task-limits 0)) bare]]
    (try
      (update-resource claimed "GroupLimit" "llm" (ResourceBody :spec spec :resource-version (resource-version-of claimed "GroupLimit/llm")) "declare")
      (raise (AssertionError (.format "持ち主の在る組の書き直しを断らなかった: {}" spec)))
      (except [refused Refused]
        (assert (= refused.status 409) refused.status)
        (assert (in "alpha" refused.body.message) refused.body.message)))))


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
                              :limits #((TaskLimit :group "llm")) :memory-mib 1024)))
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
                                 :workers #((SimWorker :name "w1" :provides LOCAL))))
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


;; 組の上限の数えの関数と、受ける引数の名(置き方の判断は task の行を受けない — 下の検の註)。
(val COUNTING-SIGNATURES
  #(#(cluster_policy.hold-limits ["held" "limits" "worker" "groups"])
    #(cluster_policy.limits-misfit ["limits" "worker" "held" "groups"])
    #(cluster_policy.limit-holdings ["claims" "groups"])
    #(cluster_policy.task-misfit ["limits" "priority" "worker" "room" "held" "groups" "bg_held" "bg_wanted" "need" "memory_room"])
    #(cluster_policy.placed-claims ["tasks"])))


(defk signature-misfits [pinned]
  {:pre [(: pinned tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "数えの関数と固定した引数の名の組の列 pinned のうち、引数が固定と違う関数を知るため: 外れの文の list(空 = 固定どおり)。"
  (lfor #(f params) pinned :if (!= (list (. (inspect.signature f) parameters)) params)
        (.format "{} の引数 {} が固定 {} と違う" f.__name__ (inspect.signature f) params)))


(deftest test-the-sender-facing-fields-and-the-counting-inputs-are-pinned
  ;; 盲検 B: 送り手の名乗る欄を足して置き方がそれで数えると、提示の検を全部通りながら上限の値が送り手へ戻った。送り手の欄の集合と、数えの
  ;; 関数が受ける物を固定する — 足す時は、組の上限の数えに送り手の値を入れない事を確かめ(値と範囲の持ち主は系の宣言 GroupLimit だけ)、
  ;; この表を直す。notify(終わりの知らせ先)は出口へ積む判断(detached_policy.outbox-noted)だけが読み、下の数えの関数は
  ;; 受けない。memory-mib(memory の要り — 2026-11-02)は memory の空きの比べ(task-misfit の need)だけが読み、組の上限の数えは受けない。
  ;; rerun(走らせ直しの名乗り — 2026-11-04・TaskBody にも同じ名で載る)は担い手が消えた試行を待ちの行へ戻す判断と、次の試行を
  ;; 置ける worker の判じ(heard-since-rerun — attempt と刻だけを読む)が読み、組の上限の数えは受けない。
  ;; shape(仕事の形 — 2026-11-10・TaskBody にも同じ名で載る)は memory の要りの数え(cluster_policy.shape-peaks-of・task-need-of・
  ;; measured_room.learned-need)だけが読み、その答えは task-misfit の need として memory の空きと比べるだけで、組の上限の数えは受けない。
  ;; ungated(測った空きの門の外 — 2026-11-11・TaskBody にも同じ名で載る)は置き先の判じを task-misfit から limits-misfit だけへ替え、置き先の順
  ;; (要りの足りなさ)を変えるが、組の升の数え(hold-limits の 1 本 = 1)と宣言の値・範囲は変えない。preemptible(場所を譲る名乗り —
  ;; 2026-11-11・TaskBody にも同じ名で載る)は急ぎの task に memory を譲らせる task を選ぶ判じ(cluster_policy.yield-candidates)だけが読み、
  ;; 譲らせた拍の組の上限の判じ(yield-plan の task-misfit)は止めた task の升を引かない held と宣言の値で問い直す — 組の升の数えは受けない。
  ;; 3 つとも下の test-the-group-holdings-ignore-the-shape-the-measured-peak-and-ungated が数えの答えで確かめる。
  (assert (= (tuple (gfor f (fields TaskLimit) f.name)) #("group" "part"))
          "TaskLimit は組の名と区分だけ — 値・範囲・重さを task に名乗らせない(値の持ち主は系の宣言)")
  (assert (= (tuple (gfor f (fields SubmitDetached) f.name))
             #("program" "key" "needs" "name" "lease_seconds" "retain_seconds" "environ" "priority" "limits" "notify" "memory_mib" "rerun" "shape"
               "ungated" "preemptible"))
          "SubmitDetached の欄を足す時は、組の上限の数えに入れない事を確かめてからこの表を直す")
  (assert (= (tuple (gfor f (fields TaskBody) f.name))
             #("program" "revision" "name" "needs" "requires" "env" "blob" "versions" "runtime_env" "environ" "lease_seconds"
               "retain_seconds" "priority" "limits" "notify" "memory_MiB" "shape" "ungated" "preemptible" "rerun"
               "format"))
          "TaskBody(受け口の本文)の欄を足す時は、組の上限の数えに入れない事を確かめてからこの表を直す")
  ;; 置き方の判断の関数は task の行(TaskRecord — 送り手の欄を全部持つ)を受けず、組の名乗り(TaskLimit の tuple)・置いた worker・
  ;; 置いている数・系の宣言の表だけを受ける。task の行を読むのは、置いている task の名乗りを射影する placed-claims と tasks-placed の
  ;; 呼びだけ(既に在る送り手の欄 — environ など — を数えに読ませない。設計のレビューの指摘 2-a)。
  (<- misfits list (signature-misfits COUNTING-SIGNATURES))
  (assert (= misfits []) misfits))


(deftest test-the-placed-claims-are-one-per-placed-task-whatever-else-the-sender-sent
  ;; 置いている task の名乗りの射影は、置いている task 1 本につき #(名乗った組 置いた worker) を 1 つだけ返し、送り手の他の欄(environ・
  ;; 名・key・優先)を変えても同じ(1 本は升ごとに 1 — 送り手の値で重さを変えない)。待っている task は数えない。
  (val claims #((TaskLimit :group "llm")))
  (val plain (TaskRecord "t1" "check" "sha" "rev" #() #("local") 15000 20000 0 :detached True :key "k1" :limits claims
                         :phase "assigned" :worker "w1"))
  (val loud (replace plain :id "t2" :name "heavy" :key "k2" :priority 7
                           :environ #(#("SLOTS" "4") #("WEIGHT" "9"))))
  (val waiting (replace plain :id "t3" :key "k3" :phase "queued" :worker None))
  (<- answer tuple (cluster_policy.placed-claims (! (table-from-rows {"t1" plain "t2" loud "t3" waiting}))))
  (assert (= (sorted answer :key repr) [#(claims "w1") #(claims "w1")]) answer))


;; shape(仕事の形)・測った山(peak-mib)・門の外の名乗り(ungated)・場所を譲る名乗り(preemptible)だけが違う、置いた task の行。どれも組 llm(cluster の範囲・上限 2)と組 gpu
;; (worker の範囲・上限 1)を名乗り、w1 に置いた。shape の違う行は shape-peaks-of で互いに違う山を引く(memory の要りは違う)。
(val OWNED (system-of "alpha" #() :task-limits #((GroupLimit :group "llm" :scope "cluster" :limit 2)
                                                 (GroupLimit :group "gpu" :scope "worker" :limit 1))))
(val PLACED (TaskRecord "t0" "check" "sha" "rev" #() #("local") 15000 20000 0 :detached True :key "k0"
                        :limits #((TaskLimit :group "llm") (TaskLimit :group "gpu")) :phase "assigned" :worker "w1" :memory-mib 1024))
(val SHAPED #(PLACED
              (replace PLACED :id "t1" :key "k1" :shape "small" :peak-mib 512)
              (replace PLACED :id "t2" :key "k2" :shape "huge" :peak-mib 60000)
              (replace PLACED :id "t3" :key "k3" :peak-mib 9000)
              (replace PLACED :id "t4" :key "k4" :shape "deploy" :ungated True :memory-mib None)
              (replace PLACED :id "t5" :key "k5" :shape "batch" :preemptible True :rerun True :priority -1)))


(defk owner-holdings [tasks groups]
  {:pre [(: tasks Table) (: groups Table)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "本物の組の升の数え(place-tasks の拍の頭と同じ placed-claims から limit-holdings)を、壊した数えと同じ形で owner-only-misfits に渡すため。"
  (<- claims tuple (cluster_policy.placed-claims tasks))
  (<- held dict (cluster_policy.limit-holdings claims groups))
  held)


(defk owner-only-misfits [count-of]
  {:pre [(: count-of Callable)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "組の升の数え count-of(置いた task の表と系の宣言の表 → 升 → 置いている数)が、送り手の shape・測った山・ungated・preemptible で組の上限の値・範囲・
   1 本の重さを変えない事を判じるため: 外れの文の list(空 = 守る)。比べる数は宣言 OWNED から手で書いた値で、count-of の答えから作らない。"
  (<- state ClusterState (declared OWNED.task-limits))
  (val groups state.group-limits)
  (var misfits [])
  ;; 1 本の重さ: どの行も 1 本だけ置けば、名乗った組の升ごとに 1(組 gpu の升は置いた worker w1 — 範囲は宣言の worker)。
  (for [t SHAPED]
    (<- one dict (count-of (! (table-from-rows {t.id t})) groups))
    (when (!= one {#("llm" "" None) 1 #("gpu" "" "w1") 1})
      (:= misfits [#* misfits (.format "{}(shape {!r}・山 {}・ungated {}・preemptible {})1 本の持分: {}"
                                       t.id t.shape t.peak-mib t.ungated t.preemptible one)])))
  ;; 形の違う 6 本は同じ升に 6 と数える(形で升を分けない)。
  (<- six dict (count-of (! (table-from-rows (dfor t SHAPED t.id t))) groups))
  (when (!= six {#("llm" "" None) 6 #("gpu" "" "w1") 6})
    (:= misfits [#* misfits (.format "形の違う 6 本の持分: {}" six)]))
  ;; 値と範囲は宣言: 形だけが違う 2 本(山 512 と 60000)で組 llm(上限 2)は cluster のどの worker でも埋まり、組 gpu(worker の範囲・上限 1)は
  ;; w1 だけが埋まる。山の大きい 1 本だけなら組 llm にはまだ 1 本置ける。
  (<- two dict (count-of (! (table-from-rows {"t1" (get SHAPED 1) "t2" (get SHAPED 2)})) groups))
  (<- one-huge dict (count-of (! (table-from-rows {"t2" (get SHAPED 2)})) groups))
  (for [#(held group worker expected) [#(two "llm" "w2" TaskUnplacedKind.TASK-LIMITED)
                                       #(two "gpu" "w1" TaskUnplacedKind.TASK-LIMITED)
                                       #(two "gpu" "w2" None)
                                       #(one-huge "llm" "w2" None)]]
    (<- misfit (| TaskUnplacedKind None) (cluster_policy.limits-misfit #((TaskLimit :group group)) worker held groups))
    (when (!= misfit expected)
      (:= misfits [#* misfits (.format "持分 {} で組 {} を {} に置く判じ: {}(宣言どおりなら {})" held group worker misfit expected)])))
  misfits)


(defk peak-weighted-holdings [tasks groups]
  {:pre [(: tasks Table) (: groups Table)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "負例の壊した数え: 置いた task 1 本を、測った山 4096 MiB ごとに 1 本と重く数える(1 本の重さが送り手の欄へ移る)— owner-only-misfits が拒む事を示すため。"
  (var held {})
  (for [t (.rows tasks) :if (in t.phase PLACED-PHASES)]
    (for [_ (range (+ 1 (// (or t.peak-mib 0) 4096)))]
      (<- more dict (cluster_policy.hold-limits held t.limits t.worker groups))
      (:= held more)))
  held)


(defk shape-split-holdings [tasks groups]
  {:pre [(: tasks Table) (: groups Table)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "負例の壊した数え: 組の升を task の shape ごとに分ける(上限の範囲が送り手の欄へ移り、形を名乗り替えれば同じ組に上限を越えて置ける)—
   owner-only-misfits が拒む事を示すため。"
  (var held {})
  (for [t (.rows tasks) :if (in t.phase PLACED-PHASES)]
    (<- own dict (cluster_policy.hold-limits {} t.limits t.worker groups))
    (for [#(slot count) (.items own)]
      (val key #(#* slot t.shape))
      (:= held (| held {key (+ (.get held key 0) count)}))))
  held)


(defk ungated-free-holdings [tasks groups]
  {:pre [(: tasks Table) (: groups Table)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "負例の壊した数え: 測った空きの門の外の task(ungated)を組の升に数えない(門の外の 1 本の重さが 0 になり、組の上限を越えて置ける)—
   owner-only-misfits が拒む事を示すため。"
  (<- claims tuple (cluster_policy.placed-claims (! (table-from-rows (dfor t (.rows tasks) :if (not t.ungated) t.id t)))))
  (<- held dict (cluster_policy.limit-holdings claims groups))
  held)


(defk preemptible-free-holdings [tasks groups]
  {:pre [(: tasks Table) (: groups Table)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "負例の壊した数え: 場所を譲る名乗りの task(preemptible)を組の升に数えない(譲れる 1 本の重さが 0 になり、組の上限を越えて置ける)—
   owner-only-misfits が拒む事を示すため。"
  (<- claims tuple (cluster_policy.placed-claims (! (table-from-rows (dfor t (.rows tasks) :if (not t.preemptible) t.id t)))))
  (<- held dict (cluster_policy.limit-holdings claims groups))
  held)


(defk limits-misfit-by-shape [limits worker held groups shape]
  {:pre [(: limits tuple) (: worker str) (: held dict) (: groups Table) (: shape str)] :post [(: % (| TaskUnplacedKind None))]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "負例の壊した判じ: 組の上限の判じが task の shape を受け、形を名乗った task には上限を 2 倍に数える(値が送り手の欄へ移る)—
   引数の固定(signature-misfits)が拒む事を示すため。"
  (var full False)
  (for [l limits :setv declared (.row groups l.group) :if (is-not declared None)]
    (<- slot tuple (cluster_policy.limit-slot l declared worker))
    (when (>= (.get held slot 0) (* declared.limit (if shape 2 1)))
      (:= full True)))
  (if full TaskUnplacedKind.TASK-LIMITED None))


(deftest test-the-group-holdings-ignore-the-shape-the-measured-peak-and-ungated
  ;; 正例: 本物の数え(placed-claims から limit-holdings)は、shape・測った山・ungated・preemptible だけが違う task の組の持分を等しく 1 本 = 1 と数え、
  ;; 上限の値と範囲は系の宣言 GroupLimit のまま(送り手の欄へ移らない)。
  (<- misfits list (owner-only-misfits owner-holdings))
  (assert (= misfits []) misfits))


(deftest test-a-counting-that-mixes-the-shape-into-the-group-limits-is-refused
  ;; 負例: shape・測った山・ungated・preemptible を組の升・1 本の重さ・上限の値へ混ぜる壊した実装を、上の判じと引数の固定がどれも拒む
  ;; (判じが空の検になっていない事を示す)。
  (for [broken [peak-weighted-holdings shape-split-holdings ungated-free-holdings preemptible-free-holdings]]
    (<- misfits list (owner-only-misfits broken))
    (assert misfits (.format "壊した数え {} を拒まなかった" broken.__name__)))
  (<- by-signature list (signature-misfits #(#(limits-misfit-by-shape ["limits" "worker" "held" "groups"]))))
  (assert by-signature "shape を受ける組の上限の判じを引数の固定が拒まなかった"))


(defk shaped-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "組 llm(alpha の宣言・上限 1)を名乗り、shape と memory の要りだけが違う長い task 3 本を出して 5 秒(仮想)後の GET /state を返すため
   (llm-summary が読む形 — 断りは無い)。"
  (for [#(i shape memory) [#(0 "small" 512) #(1 "huge" 2048) #(2 "" 1024)]]
    (<- (submit-detached-task (slow-add 30.0 i) :needs LOCAL :key (.format "llm-{}" i) :lease-seconds 5.0
                              :limits #((TaskLimit :group "llm")) :memory-mib memory :shape shape)))
  (<- (Delay 5.0))
  (<- state dict (ReadCoordinator "/state"))
  {"refused" None "state" state})


(deftest test-tasks-that-differ-only-in-shape-share-the-group-limit-on-sim
  ;; 正例(sim — 本物の coordinator): shape と要りだけが違う 3 本は、組 llm の宣言の値 1 で 1 本だけが置かれる(形ごとに上限を持たない)。
  (<- observed dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) ALPHA (shaped-scenario)
                                 :workers #((SimWorker :name "w1" :provides LOCAL))))
  (<- summary dict (llm-summary observed))
  (assert (= summary {"limit" 1 "system" "alpha" "held" 1 "phases" ["assigned" "queued" "queued"]}) summary))


(deftest test-the-defsystem-form-reads-the-same-scopes-and-keys-as-the-rule
  ;; 盲検 A の付随の所見: defsystem の形の読み(_system_form — 誤りの文に並べる順を持つ)は範囲の名と欄の集合を自前で持つ。規則
  ;; (detached_rules)と食い違えば赤にする(系の名 system は系の構成子が付ける欄で、書き手は書かない)。
  (assert (= (frozenset system_form.LIMIT-SCOPES) TASK-LIMIT-SCOPES) system_form.LIMIT-SCOPES)
  (assert (= (frozenset system_form.LIMIT-KEYS) (- GROUP-LIMIT-KEYS #{"system"})) system_form.LIMIT-KEYS))
