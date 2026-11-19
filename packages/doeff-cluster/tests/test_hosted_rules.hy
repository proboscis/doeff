;; 預かりの process(shared/intent/hosted_model)の判断(worker/core/hosted_rules)と口の綴り(worker/protocol/hosted_host)を、I/O なしで
;; 確かめる。本物の process の筋書きは tests/test_hosted_process.hy。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_core_effects [EnvEntry])
(import doeff_cluster [JobSpec])
(import doeff_cluster._shared.intent.hosted_model [HostedLaunch HostedProcess HostedRefused LaunchHosted ListHosted StopHosted])
(import doeff_cluster._worker.intent.worker_model [ProcessView])
(import doeff_cluster._worker.core.hosted_rules [HostedRecord hosted-id-of hosted-id? hosted-layer-procs hosted-orphans hosted-owners
                                                 hosted-place-of hosted-root-of hosted-socket-of])
(import doeff_cluster._worker.protocol.hosted_host [hosted-answer-of hosted-answer-text hosted-request-of hosted-request-text
                                                   record-of record-text])

(val ROOT "/work/shared/hosted")


(defk spec-of [name]
  {:pre [(: name str)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "名だけの job の宣言を作るため。"
  (JobSpec :name name :entry "e" :args #() :revision "r"))


(defk record-of-owner [id owner]
  {:pre [(: id str) (: owner str)] :post [(: % HostedRecord)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "持ち主だけが違う記録を作るため。"
  (HostedRecord :id id :pid 10 :start-ticks 5 :owner owner :layer None :place (+ ROOT "/" id) :started-ms 1 :generation "0123456789ab"))


(deftest test-the-socket-path-is-short-and-differs-per-generation
  (<- a str (hosted-socket-of (+ "/a/very/long/shared/dir/" (* "x" 200) "/hosted") "0123456789ab"))
  (<- b str (hosted-socket-of (+ "/a/very/long/shared/dir/" (* "x" 200) "/hosted") "ba9876543210"))
  (assert (<= (len (.encode a "utf-8")) 103) a)
  (assert (!= a b) #(a b))
  (<- root str (hosted-root-of "/work/shared/"))
  (assert (= root ROOT) root))


(deftest test-an-id-names-only-a-place-under-the-root
  (<- id str (hosted-id-of (bytes (range 8))))
  (assert (= id "h-0001020304050607") id)
  (<- ok bool (hosted-id? id))
  (assert ok)
  (for [bad #("../etc" "h-0001" "h-000102030405060g" "h-0001020304050607/x")]
    (<- refused bool (hosted-id? bad))
    (assert (not refused) bad))
  (<- place str (hosted-place-of ROOT id))
  (assert (= place (+ ROOT "/" id)) place)
  (try
    (<- (hosted-place-of ROOT "../x"))
    (assert False "形の外の id を断らなかった")
    (except [ValueError] None)))


(deftest test-a-layer-is-chosen-only-when-the-worker-declares-layers
  (val layers {"clis" "/cg/clis/cgroup.procs"})
  (<- named (| str None HostedRefused) (hosted-layer-procs layers "clis"))
  (assert (= named "/cg/clis/cgroup.procs") named)
  (<- unasked (| str None HostedRefused) (hosted-layer-procs layers None))
  (assert (is unasked None) unasked)
  (<- unknown (| str None HostedRefused) (hosted-layer-procs layers "jobs"))
  (assert (isinstance unknown HostedRefused) unknown)
  ;; 層の宣言の無い worker(native の機体・macOS)は今の置き方のまま。
  (<- bare (| str None HostedRefused) (hosted-layer-procs None "clis"))
  (assert (is bare None) bare))


(deftest test-an-owner-is-present-while-declared-or-while-its-process-runs
  (<- declared JobSpec (spec-of "turn-host"))
  (<- other JobSpec (spec-of "other"))
  (val running (ProcessView :name "old#retired-ab" :spec other :attempt 1 :pid 3 :started-ms 0 :retired-from "old"))
  (val ended (ProcessView :name "done" :spec other :attempt 1 :pid 4 :started-ms 0 :exit-code 0))
  (<- owners frozenset (hosted-owners #(declared) #(running ended)))
  (assert (= owners (frozenset #("turn-host" "old#retired-ab" "old"))) owners)
  (<- a HostedRecord (record-of-owner "h-0000000000000001" "turn-host"))
  (<- b HostedRecord (record-of-owner "h-0000000000000002" "old"))
  (<- c HostedRecord (record-of-owner "h-0000000000000003" "done"))
  (<- orphans tuple (hosted-orphans #(a b c) owners))
  (assert (= orphans #("h-0000000000000003")) orphans)
  ;; 宣言からも消え、退いた process も終わった: 全部が持ち主不在。
  (<- none frozenset (hosted-owners #() #()))
  (<- all-gone tuple (hosted-orphans #(a b c) none))
  (assert (= all-gone #("h-0000000000000001" "h-0000000000000002" "h-0000000000000003")) all-gone))


(deftest test-the-socket-line-round-trips-each-request-and-answer
  (val launch (LaunchHosted (HostedLaunch :argv #("cli-shim" "--x") :owner "turn-host" :layer "clis"
                                          :env #((EnvEntry :name "A" :value "1") (EnvEntry :name "B" :value "2")))))
  (for [request #(launch (StopHosted "h-0001020304050607") (ListHosted))]
    (<- text str (hosted-request-text request))
    (assert (not-in "\n" text) text)
    (<- back (| LaunchHosted StopHosted ListHosted HostedRefused) (hosted-request-of text))
    (assert (= back request) #(back request)))
  (<- broken (| LaunchHosted StopHosted ListHosted HostedRefused) (hosted-request-of "{\"v\": 2, \"op\": \"list\"}"))
  (assert (isinstance broken HostedRefused) broken)
  (<- shapeless (| LaunchHosted StopHosted ListHosted HostedRefused) (hosted-request-of "{\"v\": 1, \"op\": \"launch\"}"))
  (assert (isinstance shapeless HostedRefused) shapeless)
  (val process (HostedProcess :id "h-0001020304050607" :pid 42 :owner "turn-host" :layer "clis" :place "/p" :started-ms 7))
  (<- one str (hosted-answer-text process))
  (<- read-one (| HostedProcess HostedRefused tuple) (hosted-answer-of one False))
  (assert (= read-one process) read-one)
  (val ended (HostedProcess :id "h-0001020304050608" :pid 43 :owner "x" :layer None :place "/q" :started-ms 8 :ended True :exit-code -9))
  (<- many str (hosted-answer-text #(process ended)))
  (<- read-many (| HostedProcess HostedRefused tuple) (hosted-answer-of many True))
  (assert (= read-many #(process ended)) read-many)
  (<- refused str (hosted-answer-text (HostedRefused :detail "知らない id")))
  (<- read-refused (| HostedProcess HostedRefused tuple) (hosted-answer-of refused True))
  (assert (= read-refused (HostedRefused :detail "知らない id")) read-refused))


(deftest test-the-place-record-round-trips
  (<- record HostedRecord (record-of-owner "h-0001020304050607" "turn-host"))
  (<- text str (record-text record))
  (<- back (record-of text))
  (assert (= back record) back))
