;; Service の needs の一時の上書き(資源 NeedsOverride — card ki-1523bc0aba4f)。
;;
;; 失敗ケース(直す前・2026-10-26 22:45〜23:21 JST): 復旧の手が Service 13 本の spec.needs を直の PUT で host-zeus から
;; 復旧の能力へ書き換え、能力の側(zeus の worker の名乗り)だけが戻った。coordinator は 13 本の置き先を黙って空にし、
;; 元の needs はどこにも残っていなかった。直した後は、上書きが元の needs・結び先の能力・期限・書き手を持ち、結び先の能力を名乗る生きた
;; worker が 0 になった拍か期限の拍に coordinator が元の needs へ戻す。持ち主でない送り手の needs の直の PUT は断る。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState NeedsOverride])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.state_json [state-to-json state-from-json needs-override-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.api_policy [tick tick-due])
(import doeff_cluster.coordinator.core.resource_policy [needs-override-due])
(import doeff_cluster.shared.intent.due_model [DueAt])
(import doeff_cluster.shared.intent.cluster_control [KillWorker])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimLink ReadCoordinator ClientLink send-request])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val V {"python" "3.14.0"})
(val SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})
(val OWNER "declare-tool")
(val HAND "recovery-hand")
(val RESCUE {"name" "a" "spec" {"needs" ["net" "rescue"] "capability" "rescue" "ttlSeconds" 3600}})


(defk req [method path [body None] [actor OWNER]]
  {:pre [(: method str) (: path str) (: body (| dict None)) (: actor (| str None))] :post [(: % Request)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "検の HTTP の要求の値 Request を作るため(送り元の番地 10.0.0.9・送り手 actor)。"
  (! (http-request method path {} body :actor actor :peer "10.0.0.9")))


(defk call [state method path [body None] [actor OWNER] [now 1000]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: actor (| str None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "要求を 1 つ coordinator の判断(responded — 本番の受け口と同じ振り分け・settle・調停)に渡し、#(状態 status 本文) を返すため。"
  (responded state (! (req method path body actor)) now T))


(defk beat [state name provides now [exclusive None] [boot "b1"]]
  {:pre [(: state ClusterState) (: name str) (: provides list) (: now int) (: exclusive (| list None)) (: boot str)] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name(世代 boot)が能力 provides(専用の能力 exclusive)を名乗る heartbeat を 1 つ受けた後の状態を返すため。"
  (<- answer tuple (call state "POST" "/heartbeat"
                         {"name" name "provides" provides "exclusive" (or exclusive []) "capacity" 10 "taskReserve" 0 "versions" V
                          "boot" boot "bootAt" (if (= boot "b1") 1 now) "statuses" []}
                         :actor None :now now))
  (assert (= (get answer 1) 200) answer)
  (get answer 0))


(defk declared-world []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "持ち主 declare-tool が Service a(needs net)を宣言し、worker zeus(net)と rescue(net・専用の rescue)が名乗った状態を作るため
   (a は zeus に置かれる — rescue は rescue を要らない仕事を受けない)。"
  (<- created tuple (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" SPEC}))
  (assert (= (get created 1) 201) created)
  (<- one ClusterState (beat (get created 0) "zeus" ["net"] 1000))
  (<- two ClusterState (beat one "rescue" ["net" "rescue"] 1000 :exclusive ["rescue"]))
  two)


(defk placed-on [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /state の置き先から Service a の担い手の worker の名を読むため(置かれていなければ None)。"
  (<- seen tuple (call state "GET" "/state" :now now))
  (val placement (.get (get seen 2 "placements") "a"))
  (if placement (get placement "worker") None))


(defk service-needs [state]
  {:pre [(: state ClusterState)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /resources/Service/a の spec の needs を読むため。"
  (<- seen tuple (call state "GET" "/resources/Service/a"))
  (get seen 2 "spec" "needs"))


(defk overridden [state actor now]
  {:pre [(: state ClusterState) (: actor str) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "送り手 actor が Service a を能力 rescue へ 1 時間結ぶ上書き(POST /resources/NeedsOverride)を作った後の状態を返すため。"
  (<- answer tuple (call state "POST" "/resources/NeedsOverride" RESCUE :actor actor :now now))
  (assert (= (get answer 1) 201) answer)
  (assert (= (get answer 2 "spec")
             {"needs" ["net" "rescue"] "capability" "rescue" "original" ["net"] "expiresAtMs" (+ now 3600000) "writer" actor "sinceMs" now})
          answer)
  (get answer 0))


(deftest test-an-override-moves-a-service-and-is-lifted-when-no-live-worker-names-its-capability
  ;; 受入の条 1 の判断の側: 上書きで rescue に結んだ a は rescue へ移り、rescue が沈黙して(生きた worker が rescue を名乗らなくなって)
  ;; lease を越えた拍に coordinator が上書きを外して needs を net へ戻し、移し替えの窓の後に元の候補の zeus へ置き直す。外したのは
  ;; coordinator で、出来事の記録に残る。
  (<- world ClusterState (declared-world))
  (<- before (| str None) (placed-on world 1000))
  (assert (= before "zeus") before)
  (<- bound ClusterState (overridden world HAND 1000))
  (<- needs list (service-needs bound))
  (assert (= needs ["net" "rescue"]) needs)
  (<- moved (| str None) (placed-on bound 1000))
  (assert (= moved "rescue") moved)
  ;; zeus だけが名乗り続け、rescue は黙る。lease を越えた拍で上書きが外れる。
  (val gone (+ 1001 T.lease-ms))
  (<- quiet ClusterState (beat bound "zeus" ["net"] gone))
  (<- lifted ClusterState (tick quiet gone T))
  (<- back list (service-needs lifted))
  (assert (= back ["net"]) back)
  (assert (is (.row lifted.needs-overrides "a") None) lifted.needs-overrides)
  (<- missing tuple (call lifted "GET" "/resources/NeedsOverride/a" :now gone))
  (assert (= (get missing 1) 404) missing)
  (<- events tuple (call lifted "GET" "/events" :now gone))
  (val deleted (lfor e (get events 2 "events") :if (and (= (get e "kind") "NeedsOverride") (= (get e "verb") "delete")) e))
  (assert (= (lfor e deleted (get e "actor")) ["coordinator"]) deleted)
  ;; 移し替えの窓(reassign-after-ms)を越えると、元の候補の zeus へ置き直す。
  (val later (+ 1001 T.reassign-after-ms))
  (<- awake ClusterState (beat lifted "zeus" ["net"] later))
  (<- again ClusterState (tick awake later T))
  (<- final (| str None) (placed-on again later))
  (assert (= final "zeus") final))


(deftest test-an-override-is-lifted-when-the-new-generation-drops-the-capability
  ;; 2026-10-26 22:45:42 の形: 結び先の worker の新しい世代が能力を名乗らずに起きた拍で、上書きが外れて元の needs に戻り、a は置き先を
  ;; 失わない(直す前は needs が復旧の能力のまま 13 本の置き先が null になった)。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (<- reborn ClusterState (beat bound "rescue" ["net"] 2000 :boot "b2"))
  (<- back list (service-needs reborn))
  (assert (= back ["net"]) back)
  (assert (is (.row reborn.needs-overrides "a") None) reborn.needs-overrides)
  (<- holder (| str None) (placed-on reborn 2000))
  (assert (in holder #("zeus" "rescue")) holder))


(deftest test-an-override-is-lifted-at-its-deadline-and-the-quiet-tick-wakes-for-it
  ;; 期限の拍で外れる。静かな区間(要求の無い間)でも、tick の起きる刻の答えに期限が入る(期限を過ぎても上書きが残らない)。
  (<- world ClusterState (declared-world))
  (<- answer tuple (call world "POST" "/resources/NeedsOverride" (| RESCUE {"spec" (| (get RESCUE "spec") {"ttlSeconds" 5})}) :actor HAND))
  (assert (= (get answer 1) 201) answer)
  (val deadline 6000)
  (<- due (| int None) (needs-override-due (get answer 0)))
  (assert (= due deadline) due)
  ;; 期限の 1 ms 前: 両方の worker が名乗っていて、上書きは残る。tick の起きる刻は期限ちょうど(worker の沈黙の刻より先)。
  (<- fresh ClusterState (beat (! (beat (get answer 0) "zeus" ["net"] (- deadline 1))) "rescue" ["net" "rescue"] (- deadline 1) :exclusive ["rescue"]))
  (<- kept ClusterState (tick fresh (- deadline 1) T))
  (assert (is-not (.row kept.needs-overrides "a") None) kept.needs-overrides)
  (<- woken object (tick-due kept (- deadline 1) T))
  (assert (= woken (DueAt :at deadline)) woken)
  (<- lifted ClusterState (tick kept deadline T))
  (assert (is (.row lifted.needs-overrides "a") None) lifted.needs-overrides)
  (<- back list (service-needs lifted))
  (assert (= back ["net"]) back))


(deftest test-a-direct-needs-put-by-a-non-owner-is-refused-and-the-owners-put-passes
  ;; 受入の条 2: 持ち主(declare-tool)でない送り手の Service の needs の直の PUT は 403 で断り、NeedsOverride を案内する(状態は変えない)。
  ;; 持ち主の needs の PUT は通る。持ち主でない送り手でも needs を変えない PUT(版の書き換え)は通る(2026-10-05 の決めのまま)。
  (<- world ClusterState (declared-world))
  (val version (. (.row world.meta "Service/a") resource-version))
  (<- refused tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["rescue"]}) "resourceVersion" version} :actor HAND))
  (assert (= (get refused 1) 403) refused)
  (assert (in "NeedsOverride" (get refused 2 "error")) refused)
  (assert (in OWNER (get refused 2 "error")) refused)
  (<- unchanged list (service-needs (get refused 0)))
  (assert (= unchanged ["net"]) unchanged)
  ;; 本文の owner で持ち主を名乗り直しても、送り手が持ち主でなければ断る(照らすのは今の持ち主)。
  (<- renamed tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["rescue"] "owner" HAND}) "resourceVersion" version} :actor HAND))
  (assert (= (get renamed 1) 403) renamed)
  (<- revised tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"revision" "r2"}) "resourceVersion" version} :actor HAND))
  (assert (= (get revised 1) 200) revised)
  (<- owned tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["net" "gpu"]}) "resourceVersion" version}))
  (assert (= (get owned 1) 200) owned)
  (<- needs list (service-needs (get owned 0)))
  (assert (= needs ["gpu" "net"]) needs))


(deftest test-the-legacy-jobs-put-keeps-the-same-needs-rule
  ;; 旧い PUT /jobs も同じ規則: 持ち主でない送り手の needs の書き換えは競合の行で断る(何も書かない)。上書きの間の Service は旧い口で書かない。
  (<- world ClusterState (declared-world))
  (val version (. (.row world.meta "Service/a") resource-version))
  (<- refused tuple (call world "PUT" "/jobs" {"jobs" [(| SPEC {"name" "a" "needs" ["rescue"] "resourceVersion" version})]} :actor HAND))
  (assert (= (get refused 1) 409) refused)
  (assert (in "NeedsOverride" (get (get refused 2 "conflicts") 0 "error")) refused)
  (<- bound ClusterState (overridden world HAND 1000))
  (val bound-version (. (.row bound.meta "Service/a") resource-version))
  (<- held tuple (call bound "PUT" "/jobs" {"jobs" [(| SPEC {"name" "a" "revision" "r2" "resourceVersion" bound-version})]}))
  (assert (= (get held 1) 409) held))


(deftest test-the-owners-redeclare-during-an-override-becomes-the-needs-it-returns-to
  ;; 上書きの間の持ち主の宣言し直し(配備の宣言 — main 入りのたびに走る)は上書きを壊さない: Service の needs は上書きの needs のまま、
  ;; 宣言の needs は上書きの元の needs に入り、上書きを消すとその値に戻る。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (val version (. (.row bound.meta "Service/a") resource-version))
  (<- redeclared tuple (call bound "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["agent" "net"] "revision" "r2"}) "resourceVersion" version}))
  (assert (= (get redeclared 1) 200) redeclared)
  (assert (= (get redeclared 2 "spec" "needs") ["net" "rescue"]) redeclared)
  (assert (= (get redeclared 2 "spec" "revision") "r2") redeclared)
  (assert (= (. (.row (. (get redeclared 0) needs-overrides) "a") original) #("agent" "net")) redeclared)
  (<- dropped tuple (call (get redeclared 0) "DELETE" "/resources/NeedsOverride/a" :actor HAND))
  (assert (= (get dropped 1) 200) dropped)
  (<- back list (service-needs (get dropped 0)))
  (assert (= back ["agent" "net"]) back))


(deftest test-an-override-that-cannot-take-effect-is-refused-when-written
  ;; 作る時に断る形: 結び先を名乗る生きた worker が無い(作っても次の拍で外れる — 409)・結び先が needs に無い・期限の無い・期限の長すぎる・
  ;; 知らない欄(元の needs は coordinator が持つ — 400)・無い Service(404)・2 つ目の上書き(409)・書き直し(405)。
  (<- world ClusterState (declared-world))
  (for [#(spec status) [#({"needs" ["net" "ghost"] "capability" "ghost" "ttlSeconds" 60} 409)
                        #({"needs" ["net"] "capability" "rescue" "ttlSeconds" 60} 400)
                        #({"needs" ["net" "rescue"] "capability" "rescue"} 400)
                        #({"needs" ["net" "rescue"] "capability" "rescue" "ttlSeconds" (+ 1 (* 7 24 3600))} 400)
                        #({"needs" ["net" "rescue"] "capability" "rescue" "ttlSeconds" 60 "original" ["x"]} 400)]]
    (<- answer tuple (call world "POST" "/resources/NeedsOverride" {"name" "a" "spec" spec} :actor HAND))
    (assert (= (get answer 1) status) #(spec answer)))
  (<- nobody tuple (call world "POST" "/resources/NeedsOverride" (| RESCUE {"name" "b"}) :actor HAND))
  (assert (= (get nobody 1) 404) nobody)
  (<- bound ClusterState (overridden world HAND 1000))
  (<- twice tuple (call bound "POST" "/resources/NeedsOverride" RESCUE :actor HAND))
  (assert (= (get twice 1) 409) twice)
  (<- rewrite tuple (call bound "PUT" "/resources/NeedsOverride/a" {"spec" (get RESCUE "spec") "resourceVersion" 1} :actor HAND))
  (assert (= (get rewrite 1) 405) rewrite))


(deftest test-an-override-survives-the-state-file-and-the-durable-keys
  ;; 保存先(state file・耐久の鍵)で上書き(元の needs を含む)が往復する — coordinator を作り直しても元の needs を忘れない。壊れた保存の値は
  ;; 読み直しが理由つきで断る(黙って落とすと元の needs が失われる)。Service を消すと上書きも消える。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (val expected (.row bound.needs-overrides "a"))
  (<- data dict (state-to-json bound))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= (.row from-file.needs-overrides "a") expected) from-file.needs-overrides)
  (<- kv dict (full-kv bound))
  (<- from-keys ClusterState (state-from-kv kv 0))
  (assert (= (.row from-keys.needs-overrides "a") expected) from-keys.needs-overrides)
  (for [#(spec word) [#({"needs" ["net"] "capability" "rescue" "original" ["net"] "expiresAtMs" 1 "writer" "w" "sinceMs" 0} "capability")
                      #({"needs" ["rescue"] "capability" "rescue" "expiresAtMs" 1 "writer" "w" "sinceMs" 0} "original")]]
    (try
      (needs-override-from-json "a" spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {}" spec)))
      (except [error ValueError]
        (assert (in word (str error)) #(word (str error))))))
  (<- gone tuple (call bound "DELETE" "/resources/Service/a"))
  (assert (= (get gone 1) 200) gone)
  (assert (is (.row (. (get gone 0) needs-overrides) "a") None) gone))


;; --- 模擬(sim-cluster — 本物の coordinator と本物の worker の上)--------------------------------------------------------------

(defk rescue-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon が zeus に置かれた後に、送り手 recovery-hand が beacon を能力 rescue へ上書きで結び、rescue へ移った後に
   rescue の機体が死ぬ。移った置き先・死んだ後の置き先・needs・上書きの有無を読んで返すため。"
  (<- (Delay 8.0))
  (<- link SimLink (ClientLink))
  (<- before dict (ReadCoordinator "/state"))
  (<- written tuple (send-request (SimLink :queue link.queue :actor HAND :revision link.revision :peer link.peer :versions link.versions
                                           :timing link.timing)
                                  "POST" "/resources/NeedsOverride" {}
                                  {"name" "beacon" "spec" {"needs" ["cluster-net" "rescue"] "capability" "rescue" "ttlSeconds" 3600}}))
  (<- (Delay 15.0))
  (<- bound dict (ReadCoordinator "/state"))
  (<- (KillWorker "rescue"))
  ;; lease(10 秒)を越えた拍で上書きが外れ、needs が戻る。置き先は、担い手が途絶しても動かし続けてよい印の約束の期限
  ;; (kept-reassign-after-ms — 280 秒・上書きの間は他に置ける worker が無かったので印を渡してある)の後に元の候補へ移る。
  (<- (Delay 20.0))
  (<- service dict (ReadCoordinator "/resources/Service/beacon"))
  (<- overrides dict (ReadCoordinator "/resources/NeedsOverride"))
  (<- (Delay 300.0))
  (<- after dict (ReadCoordinator "/state"))
  {"written" (get written 0)
   "before" (get before "placements" "beacon" "worker")
   "bound" (get bound "placements" "beacon" "worker")
   "after" (get after "placements" "beacon" "worker")
   "needs" (get service "spec" "needs")
   "overrides" (len (get overrides "items"))})


(deftest test-an-overridden-service-returns-to-its-own-workers-when-the-bound-worker-dies-on-sim
  ;; 受入の条 1(模擬): 上書きで能力 rescue に結んで rescue へ置いた beacon が、rescue を名乗る worker の消えた拍で元の needs(cluster-net)に
  ;; 戻り、元の候補の zeus に置かれる。worker の死を試す筋書きなので本番の timing を明示して渡す。
  (<- seen dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (beacons sim-foundation) (rescue-scenario)
                             :workers #((SimWorker :name "zeus" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                                        (SimWorker :name "rescue" :provides (frozenset ["cluster-net" "rescue"])
                                                   :exclusive (frozenset ["rescue"]) :task-reserve 0))))
  (assert (= seen {"written" 201 "before" "zeus" "bound" "rescue" "after" "zeus" "needs" ["cluster-net"] "overrides" 0}) seen))

