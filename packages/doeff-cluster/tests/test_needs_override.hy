;; Service の置き先の一時の上書き(資源 NeedsOverride — card ki-1523bc0aba4f)。
;;
;; 失敗ケース(直す前・2026-10-26 22:45〜23:21 JST): 復旧の手が Service 13 本の spec.needs を直の PUT で host-zeus から復旧の能力へ
;; 書き換え、worker の側(zeus の Deployment の能力の名乗り)だけが Flux の当て直しで戻った。coordinator は 13 本の置き先を黙って空にし、
;; 元の needs はどこにも残っていなかった。直した後は、上書きが宣言(spec)を書き換えない記録(訳・期限・書き手)として入り、置き先の判断が
;; 効く needs をその記録から決める。期限の拍か、上書きの needs を満たす生きた worker が 0 になった拍に、coordinator が記録を外して同じ拍で
;; 宣言の needs で置き直す。版(revision)を変えずに置き先(needs・pin)を書き換える PUT は 409 で断る。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_cluster [ClusterTiming Request])
(import doeff_cluster [ClusterState])
(import doeff_cluster [http-request])
(import doeff_cluster [responded])
(import doeff_cluster._coordinator.protocol.state_json [state-to-json state-from-json needs-override-from-json])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster [tick tick-due])
(import doeff_cluster._coordinator.core.resource_policy [needs-override-due])
(import doeff_cluster._shared.intent.due_model [DueAt])
(import doeff_cluster [KillWorker StopWorker])
(import doeff_cluster [sim-cluster SimWorker ReadCoordinator ClientLink StartWorker ReplaceWorker]
        doeff_cluster._sim.local [SimLink send-request])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons thirteen-beacons])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val V {"python" "3.14.0"})
(val SPEC {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100})
(val OWNER "declare-tool")
(val HAND "recovery-hand")
(val WHY "停電の復旧の間、rescue の機体に置く")
(val RESCUE {"name" "a" "spec" {"needs" ["net" "rescue"] "reason" WHY "ttlSeconds" 3600}})


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
                         {"name" name "provides" provides "exclusive" (or exclusive []) "backgroundRoom" 10 "taskReserve" 0 "versions" V
                          "boot" boot "bootAt" (if (= boot "b1") 1 now) "statuses" []}
                         :actor None :now now))
  (assert (= (get answer 1) 200) answer)
  (get answer 0))


(defk declared-world []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "宣言の道具 declare-tool が Service a(needs net)を宣言し、worker zeus(net)と rescue(net・専用の rescue)が名乗った状態を作るため
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


(defk service-of [state]
  {:pre [(: state ClusterState)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /resources/Service/a の本文を読むため。"
  (<- seen tuple (call state "GET" "/resources/Service/a"))
  (get seen 2))


(defk overridden [state actor now]
  {:pre [(: state ClusterState) (: actor str) (: now int)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "送り手 actor が Service a を 1 時間 rescue の機体へ向ける上書き(POST /resources/NeedsOverride)を作った後の状態を返すため。"
  (<- answer tuple (call state "POST" "/resources/NeedsOverride" RESCUE :actor actor :now now))
  (assert (= (get answer 1) 201) answer)
  (assert (= (get answer 2 "spec")
             {"needs" ["net" "rescue"] "reason" WHY "expiresAtMs" (+ now 3600000) "writer" actor "sinceMs" now})
          answer)
  (get answer 0))


(deftest test-an-override-is-a-record-beside-the-declaration-and-is-lifted-when-no-live-worker-satisfies-it
  ;; 受入の条 1・c511f7e30: 上書きは宣言の spec を書き換えない記録(訳・期限・書き手 — GET /resources と GET /state に出る)で、置き先の
  ;; 判断は上書きの needs を効かせて a を rescue へ移す。rescue が黙って上書きの needs を満たす生きた worker が 0 になった拍に、
  ;; coordinator が記録を外し(出来事に送り手 coordinator の delete)、移し替えの窓の後に宣言の候補 zeus へ置き直す。
  (<- world ClusterState (declared-world))
  (<- before (| str None) (placed-on world 1000))
  (assert (= before "zeus") before)
  (<- bound ClusterState (overridden world HAND 1000))
  (<- service dict (service-of bound))
  (assert (= (get service "spec" "needs") ["net"]) service)
  (assert (= (get service "status" "needsOverride" "needs") ["net" "rescue"]) service)
  (<- seen tuple (call bound "GET" "/state"))
  (assert (= (lfor o (get seen 2 "needsOverrides") #((get o "service") (get o "reason"))) [#("a" WHY)]) seen)
  (<- moved (| str None) (placed-on bound 1000))
  (assert (= moved "rescue") moved)
  (val gone (+ 1001 T.lease-ms))
  (<- quiet ClusterState (beat bound "zeus" ["net"] gone))
  (<- lifted ClusterState (tick quiet gone T))
  (assert (is (.row lifted.needs-overrides "a") None) lifted.needs-overrides)
  (<- after dict (service-of lifted))
  (assert (= (get after "spec" "needs") ["net"]) after)
  (assert (not-in "needsOverride" (get after "status")) after)
  (<- events tuple (call lifted "GET" "/events" :now gone))
  (val deleted (lfor e (get events 2 "events") :if (and (= (get e "kind") "NeedsOverride") (= (get e "verb") "delete")) e))
  (assert (= (lfor e deleted (get e "actor")) ["coordinator"]) deleted)
  (val later (+ 1001 T.reassign-after-ms))
  (<- awake ClusterState (beat lifted "zeus" ["net"] later))
  (<- again ClusterState (tick awake later T))
  (<- final (| str None) (placed-on again later))
  (assert (= final "zeus") final))


(deftest test-an-override-is-lifted-in-the-same-step-the-new-generation-drops-the-capability
  ;; 2026-10-26 22:45:42 の形: 上書きの needs を満たしていた worker の新しい世代が能力を名乗らずに起きた拍で、上書きが外れ、同じ拍の
  ;; 置き方が宣言の needs で a を置く(置き先の無い拍が無い — 直す前は 13 本の置き先が null になった)。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (<- reborn ClusterState (beat bound "rescue" ["net"] 2000 :boot "b2"))
  (assert (is (.row reborn.needs-overrides "a") None) reborn.needs-overrides)
  (<- holder (| str None) (placed-on reborn 2000))
  (assert (in holder #("zeus" "rescue")) holder))


(deftest test-an-override-is-lifted-at-its-deadline-or-when-cancelled
  ;; 受入の条 c511f7e30: 期限の拍で外れる(静かな区間でも tick の起きる刻に期限が入る)。取り消し(DELETE)でも外れ、宣言の needs で置く。
  (<- world ClusterState (declared-world))
  (<- answer tuple (call world "POST" "/resources/NeedsOverride" (| RESCUE {"spec" (| (get RESCUE "spec") {"ttlSeconds" 5})}) :actor HAND))
  (assert (= (get answer 1) 201) answer)
  (val deadline 6000)
  (<- due (| int None) (needs-override-due (get answer 0)))
  (assert (= due deadline) due)
  (<- fresh ClusterState (beat (! (beat (get answer 0) "zeus" ["net"] (- deadline 1))) "rescue" ["net" "rescue"] (- deadline 1) :exclusive ["rescue"]))
  (<- kept ClusterState (tick fresh (- deadline 1) T))
  (assert (is-not (.row kept.needs-overrides "a") None) kept.needs-overrides)
  (<- woken object (tick-due kept (- deadline 1) T))
  (assert (= woken (DueAt :at deadline)) woken)
  (<- lifted ClusterState (tick kept deadline T))
  (assert (is (.row lifted.needs-overrides "a") None) lifted.needs-overrides)
  (<- bound ClusterState (overridden world HAND 1000))
  (<- cancelled tuple (call bound "DELETE" "/resources/NeedsOverride/a" :actor HAND))
  (assert (= (get cancelled 1) 200) cancelled)
  (assert (is (.row (. (get cancelled 0) needs-overrides) "a") None) cancelled)
  (<- back (| str None) (placed-on (get cancelled 0) 1000))
  (assert (= back "zeus") back))


(deftest test-a-placement-rewrite-without-a-new-revision-is-refused-and-a-new-revision-passes
  ;; 受入の条 2・c6e6fcbb6: 版(spec.revision)を変えずに needs か pin を変える Service の PUT は 409 で断り、NeedsOverride を案内する
  ;; (状態は変えない)。宣言の道具の版を変えた宣言は、送り手が誰でも通る(照らすのは版で、名乗りではない — 2026-10-05 の決め)。
  ;; 置き先を変えない PUT(子の環境変数)は版を変えずに通る。
  (<- world ClusterState (declared-world))
  (val version (. (.row world.meta "Service/a") resource-version))
  (for [#(spec what) [#((| SPEC {"needs" ["rescue"]}) "needs") #((| SPEC {"pin" "rescue"}) "pin")]]
    (<- refused tuple (call world "PUT" "/resources/Service/a" {"spec" spec "resourceVersion" version} :actor HAND))
    (assert (= (get refused 1) 409) #(what refused))
    (assert (in "NeedsOverride" (get refused 2 "error")) refused)
    (<- unchanged dict (service-of (get refused 0)))
    (assert (= (get unchanged "spec" "needs") ["net"]) unchanged))
  (<- environ tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"environ" {"STEP" "2"}}) "resourceVersion" version} :actor HAND))
  (assert (= (get environ 1) 200) environ)
  (<- redeclared tuple (call world "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["gpu" "net"] "revision" "r2"}) "resourceVersion" version}
                             :actor HAND))
  (assert (= (get redeclared 1) 200) redeclared)
  (assert (= (get redeclared 2 "spec" "needs") ["gpu" "net"]) redeclared))


(deftest test-the-legacy-jobs-put-keeps-the-same-revision-rule
  ;; 旧い PUT /jobs も同じ規則: 版を変えない needs の書き換えは競合の行で断る(何も書かない)。
  (<- world ClusterState (declared-world))
  (val version (. (.row world.meta "Service/a") resource-version))
  (<- refused tuple (call world "PUT" "/jobs" {"jobs" [(| SPEC {"name" "a" "needs" ["rescue"] "resourceVersion" version})]} :actor HAND))
  (assert (= (get refused 1) 409) refused)
  (assert (in "NeedsOverride" (get (get refused 2 "conflicts") 0 "error")) refused))


(deftest test-a-redeclaration-during-an-override-changes-the-declaration-and-keeps-the-override
  ;; 上書きの間の宣言し直し(main 入りのたびの宣言)は宣言だけを書き換え、上書きはそのまま効く。上書きを消すと新しい宣言の needs が効く。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (val version (. (.row bound.meta "Service/a") resource-version))
  (<- redeclared tuple (call bound "PUT" "/resources/Service/a" {"spec" (| SPEC {"needs" ["agent" "net"] "revision" "r2"}) "resourceVersion" version}))
  (assert (= (get redeclared 1) 200) redeclared)
  (assert (= (get redeclared 2 "spec" "needs") ["agent" "net"]) redeclared)
  (assert (= (get redeclared 2 "status" "needsOverride" "needs") ["net" "rescue"]) redeclared)
  (<- holder (| str None) (placed-on (get redeclared 0) 1000))
  (assert (= holder "rescue") holder))


(deftest test-an-override-that-cannot-take-effect-is-refused-when-written
  ;; 作る時に断る形: 上書きの needs を満たす生きた worker が無い(作っても次の拍で外れる — 409)・訳の無い / 空の / 長すぎる訳・期限の
  ;; 無い / 長すぎる・知らない欄(400)・無い Service(404)・2 つ目の上書き(409)・書き直し(405)。
  (<- world ClusterState (declared-world))
  (for [#(spec status) [#({"needs" ["net" "ghost"] "reason" WHY "ttlSeconds" 60} 409)
                        #({"needs" ["net" "rescue"] "ttlSeconds" 60} 400)
                        #({"needs" ["net" "rescue"] "reason" " " "ttlSeconds" 60} 400)
                        #({"needs" ["net" "rescue"] "reason" (* "x" 201) "ttlSeconds" 60} 400)
                        #({"needs" ["net" "rescue"] "reason" WHY} 400)
                        #({"needs" ["net" "rescue"] "reason" WHY "ttlSeconds" (+ 1 (* 7 24 3600))} 400)
                        #({"needs" ["net" "rescue"] "reason" WHY "ttlSeconds" 60 "capability" "rescue"} 400)]]
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
  ;; 保存先(state file・耐久の鍵)で上書きが往復する(coordinator を作り直しても訳・期限・書き手を忘れない)。壊れた保存の値は読み直しが
  ;; 理由つきで断る。Service を消すと上書きも消える。
  (<- world ClusterState (declared-world))
  (<- bound ClusterState (overridden world HAND 1000))
  (val expected (.row bound.needs-overrides "a"))
  (<- data dict (state-to-json bound))
  (<- from-file ClusterState (state-from-json data 0))
  (assert (= (.row from-file.needs-overrides "a") expected) from-file.needs-overrides)
  (<- kv dict (full-kv bound))
  (<- from-keys ClusterState (state-from-kv kv 0))
  (assert (= (.row from-keys.needs-overrides "a") expected) from-keys.needs-overrides)
  (for [#(spec word) [#({"needs" ["rescue"] "expiresAtMs" 1 "writer" "w" "sinceMs" 0} "reason")
                      #({"needs" [] "reason" "r" "expiresAtMs" 1 "writer" "w" "sinceMs" 0} "needs")]]
    (try
      (needs-override-from-json "a" spec)
      (raise (AssertionError (.format "壊れた保存の値を読んだ: {}" spec)))
      (except [error ValueError]
        (assert (in word (str error)) #(word (str error))))))
  (<- gone tuple (call bound "DELETE" "/resources/Service/a"))
  (assert (= (get gone 1) 200) gone)
  (assert (is (.row (. (get gone 0) needs-overrides) "a") None) gone))


;; --- 模擬(sim-cluster — 本物の coordinator と本物の worker の上)--------------------------------------------------------------

(defk hand-link []
  {:pre [] :post [(: % SimLink)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの送り手の口を、送り手 recovery-hand の口に替えて返すため(上書きを書く復旧の手)。"
  (<- link SimLink (ClientLink))
  (SimLink :queue link.queue :actor HAND :revision link.revision :peer link.peer :versions link.versions :timing link.timing))


(defk rescue-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: beacon が zeus に置かれた後に、復旧の手が beacon を上書きで rescue の機体へ向け、rescue へ移った後に rescue の機体が死ぬ。
   移った置き先・死んだ後の置き先・宣言の needs・上書きの有無を読んで返すため。"
  (<- (Delay 8.0))
  (<- link SimLink (hand-link))
  (<- before dict (ReadCoordinator "/state"))
  (<- written tuple (send-request link "POST" "/resources/NeedsOverride" {}
                                  {"name" "beacon" "spec" {"needs" ["cluster-net" "rescue"] "reason" WHY "ttlSeconds" 3600}}))
  (<- (Delay 15.0))
  (<- bound dict (ReadCoordinator "/state"))
  (<- (KillWorker "rescue"))
  ;; lease(10 秒)を越えた拍で上書きが外れる。置き先は、担い手が途絶しても動かし続けてよい印の約束の期限
  ;; (kept-reassign-after-ms — 280 秒・上書きの間は他に置ける worker が無かったので印を渡してある)の後に宣言の候補へ移る。
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
  ;; 受入の条 1(模擬): 上書きで rescue の機体へ向けた beacon が、rescue の死の後に上書きを失い(宣言の needs は cluster-net のまま)、
  ;; 宣言の候補の zeus に置かれる。worker の死を試す筋書きなので本番の timing を明示して渡す。
  (<- seen dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (beacons sim-foundation) (rescue-scenario)
                             :workers #((SimWorker :name "zeus" :provides (frozenset ["cluster-net"]) :task-reserve 0)
                                        (SimWorker :name "rescue" :provides (frozenset ["cluster-net" "rescue"])
                                                   :exclusive (frozenset ["rescue"]) :task-reserve 0))))
  (assert (= seen {"written" 201 "before" "zeus" "bound" "rescue" "after" "zeus" "needs" ["cluster-net"] "overrides" 0}) seen))


(val THIRTEEN (tuple (gfor i (range 1 14) (.format "b{:02d}" i))))


(defk unplaced-among [state]
  {:pre [(: state dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "GET /state の答えから、13 本のうち置き先の無い Service の名を並べるため。"
  (tuple (gfor n THIRTEEN :if (or (in n (get state "unplaced")) (not-in n (get state "placements"))) n)))


(defk zeus-rebuilt-scenario []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(2026-10-26 22:45 の形): zeus の worker が復旧の能力 rescue を専用に名乗り(手の patch)、復旧の手が 13 本の Service を上書きで
   rescue へ向けて置く。Flux の当て直しに当たる作り直しで、zeus が rescue を名乗らない値で起き直す。起き直した後 30 秒、1 秒ごとに
   置き先の無い Service を数え、上書きの残りを読んで返すため。"
  (<- (Delay 3.0))
  (<- link SimLink (hand-link))
  (var written #())
  (for [n THIRTEEN]
    (<- answer tuple (send-request link "POST" "/resources/NeedsOverride" {}
                                   {"name" n "spec" {"needs" ["cluster-net" "rescue"] "reason" WHY "ttlSeconds" 3600}}))
    (:= written (+ written #((get answer 0)))))
  (<- (Delay 10.0))
  (<- bound dict (ReadCoordinator "/state"))
  (<- (StopWorker "zeus"))
  (<- replaced bool (ReplaceWorker "zeus" (SimWorker :name "zeus" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
  (<- started bool (StartWorker "zeus"))
  (var gaps 0)
  (for [_ (range 30)]
    (<- (Delay 1.0))
    (<- state dict (ReadCoordinator "/state"))
    (<- missing tuple (unplaced-among state))
    (when missing (:= gaps (+ gaps 1))))
  (<- after dict (ReadCoordinator "/state"))
  (<- overrides dict (ReadCoordinator "/resources/NeedsOverride"))
  {"written" (sorted (set written))
   "bound" (sorted (set (gfor n THIRTEEN (get bound "placements" n "worker"))))
   "replaced" #(replaced started)
   "gaps" gaps
   "after" (sorted (set (gfor n THIRTEEN (get after "placements" n "worker"))))
   "overrides" (len (get overrides "items"))})


(deftest test-thirteen-overridden-services-keep-a-placement-when-the-worker-is-rebuilt-without-the-capability-on-sim
  ;; 受入の条 c69a30194(模擬・10-26 の形): 上書きで rescue へ向けた 13 本の Service は、zeus が rescue を名乗らない値で作り直された後、
  ;; 1 秒ごとに読んだ 30 回のどの拍でも置き先を失わない(上書きは起き直した最初の拍で外れ、同じ拍で宣言の needs で置かれる)。
  ;; 直す前の形(上書きを外さない)では、起き直した後の全部の拍で 13 本が置き先を持たない。worker の作り直しを試すので本番の timing を渡す。
  (<- seen dict (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) (thirteen-beacons sim-foundation) (zeus-rebuilt-scenario)
                             :workers #((SimWorker :name "zeus" :provides (frozenset ["cluster-net" "rescue"])
                                                   :exclusive (frozenset ["rescue"]) :task-reserve 0 :background-room 20))))
  (assert (= seen {"written" [201] "bound" ["zeus"] "replaced" #(True True) "gaps" 0 "after" ["zeus"] "overrides" 0}) seen))
