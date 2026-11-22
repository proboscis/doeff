;; task の終わりの行(worker/core/task_record): coordinator の task の行は終わった後に消え、worker の log の memory の山は task の名と CPU を
;; 持たないので、終わった task の CPU と memory を後から数える元が無かった。worker は回収の時に task を置いた job の名の子の cpu.stat と
;; memory.peak を読み、OTLP/HTTP の logs の 1 行にして、job の log と同じ送り先へ拍の外の task で 1 回 POST する(機体に file は書かない)。
;; 送れない時は worker の log に 1 行を残して捨て、回収は送りを待たない。
;; cgroupfs の代わりは tmp の dir(cpu.stat・memory.peak は普通の file)。送り先の代わりは、模擬の秒だけ待ってから受ける検の答え手。
(require doeff-hy.macros [defk defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses)
(import datetime [datetime timezone])
(import json)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse ReadMachineName SlogEffect os-file-handler scheduled])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster [JobSpec now-epoch-ms])
(import doeff_cluster._worker.core.task_record [TaskEnd TaskRecordTarget TaskUsage cpu-seconds-of peak-mib-of task-record-body
                                                task-record-refusal task-record-target])
(import doeff_cluster._worker.entry.main [task-record-target-of])
(import doeff_cluster._worker.intent.worker_model [Outcome ProcessView SpecChanged])
(import doeff_cluster._worker.protocol.declared [task-spec])
(import doeff_cluster._worker.protocol.process_host [HostSettings Started StopAsked TASK-RECORD-FAILED-LOG recorded-task-end task-stop-word])
(import tests.host_rig [host-settings])

;; 2026-11-16 12:00 UTC(epoch ms)。
(val NOON (datetime 2026 11 16 12 0 0 :tzinfo timezone.utc))
(val NOON-MS 1794830400000)
(val CPU-STAT "usage_usec 125500000\nuser_usec 120000000\nsystem_usec 5500000\nnr_periods 0\n")
(val TASK (JobSpec "task/t7" "doeff_cluster._worker.entry.job_entry" #() "r" :once True :task-name "daily-verify doeff"))
(val ENDPOINT "http://collector.test:4318/v1/logs")
(val TARGET (TaskRecordTarget :endpoint ENDPOINT :node "node-a"
                              :resource #(#("deploy.boundary" "personal") #("service.name" "records.task-ends") #("host.name" "node-a"))))
;; 送り先の代役が 1 つの要求に掛ける模擬の秒(送り先が遅い・届かない時の代役)と、回収の後に送りの task が終わるまで待つ模擬の秒。
(val SLOW-SECONDS 5.0)
(val SETTLE-SECONDS 60.0)


(defclass Collector []  ; class にする理由: 送り先の代役の答え手と検の本体が同じ入れ物を読み書きする
  "送り先の代役の入れ物: posts = 受けた #(method url 本文) の並び・answer = 返す答え(HttpResponse か HttpFailed — 例外なら投げる)。"
  (defn #^ None __init__ [self #^ object answer]
    (setv self.posts #() self.answer answer)))


(defclass ToldLines []  ; class にする理由: log の答え手が溜める入れ物(答え手の節と検の本体が同じ物を読み書きする)
  "log の行 #(名 欄の dict) の列を持つ入れ物。"
  (defn #^ None __init__ [self]
    (setv self.lines #())))


(defhandler stand-in-collector [#^ Collector collector]
  "OTLP/HTTP の logs の口の代役: 要求を模擬の SLOW-SECONDS 秒待ってから受けて溜め、入れ物の答えを返す(例外なら投げる)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 受けた物を溜める入れ物と答えは検ごとに決める値(Ask で読む設定ではない)。
  (HttpRequest [method url body]
    (<- (Delay SLOW-SECONDS))
    (setv collector.posts (+ collector.posts #(#(method url (json.loads (json.dumps body))))))
    (when (isinstance collector.answer Exception)
      (raise collector.answer))
    (resume collector.answer)))


(defhandler recording-log [#^ ToldLines told]
  "log の行を #(名 欄の dict) で溜める。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 溜める入れ物は検ごとに決める値(Ask で読む設定ではない)。
  (SlogEffect []
    (setv told.lines (+ told.lines #(#(effect.msg (dict effect.kwargs)))))
    (resume None)))


(defhandler named-machine
  "OS の host の名の代役: ReadMachineName に決まった名 box-1 を答える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (ReadMachineName []
    (resume "box-1")))


(defk reaped-then-settled [settings started outcome stop-reason [exit-code 3]]
  {:pre [(: settings HostSettings) (: started Started) (: outcome Outcome) (: stop-reason (| str None)) (: exit-code (| int None))] :post [(: % int)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "回収の時の行の送りを 1 回頼み(拍の代役)、頼みの間に進んだ模擬の ms を測ってから、送りの task が終わるまで待つため。答え = 頼みの間に
   進んだ模擬の ms(拍が送りを待たなければ 0)。"
  (<- began int (now-epoch-ms))
  (<- (recorded-task-end settings started outcome exit-code stop-reason))
  (<- asked int (now-epoch-ms))
  (<- (Delay SETTLE-SECONDS))
  (- asked began))


(defk run-reaped [settings started collector told [outcome Outcome.EXITED] [stop-reason "memory-watch"] [exit-code 3]]
  {:pre [(: settings HostSettings) (: started Started) (: collector Collector) (: told ToldLines) (: outcome Outcome) (: stop-reason (| str None))
         (: exit-code (| int None))]
   :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書き reaped-then-settled を、本物の file の答え手(子の cgroup の file の読み)・遅い送り先の代役・2026-11-16 12:00 UTC から進む模擬の
   時計の下で回すため。"
  (<- took int (scheduled (with-handlers [(sim-time-handler :start-time NOON) os-file-handler (recording-log told) (stand-in-collector collector)]
                            (reaped-then-settled settings started outcome stop-reason exit-code))))
  took)


(defk posted-fields [collector]
  {:pre [(: collector Collector)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "送り先の代役が受けた本文から、log record ごとの #(resource 属性の dict  record 属性の dict  body の JSON の dict  timeUnixNano) を取り出すため。"
  (val values (fn [attributes] (dfor a attributes (get a "key") (get (get a "value") "stringValue"))))
  (tuple (gfor #(_method _url body) collector.posts
               resource (get body "resourceLogs")
               scope (get resource "scopeLogs")
               record (get scope "logRecords")
               #((values (get (get resource "resource") "attributes")) (values (get record "attributes"))
                 (json.loads (get (get record "body") "stringValue")) (get record "timeUnixNano")))))


(defk failure-lines [told]
  {:pre [(: told ToldLines)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "溜めた log の行のうち、task の終わりの行を送れなかった行の欄を取り出すため。"
  (tuple (gfor #(msg fields) told.lines :if (= msg TASK-RECORD-FAILED-LOG) fields)))


(defk cgroup-child [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "task を置いた job の名の子の代わりの dir(cpu.stat = 125.5 CPU 秒・memory.peak = 2 GiB)を作るため。"
  (val group (/ tmp-path "cg" "jobs" "task_t7"))
  (.mkdir group :parents True)
  (.write-text (/ group "cpu.stat") CPU-STAT)
  (.write-text (/ group "memory.peak") "2147483648\n")
  (str group))


(deftest test-cpu-seconds-and-the-memory-peak-are-read-from-the-cgroup-files
  ;; usage_usec は子孫を含む CPU の和。memory.peak は byte で、MiB に切り捨てる(1024 以上 = 1 GiB 以上)。読めない形は None(0 で埋めない)。
  (assert (= (! (cpu-seconds-of CPU-STAT)) 125.5))
  (assert (is (! (cpu-seconds-of "user_usec 1\n")) None))
  (assert (is (! (cpu-seconds-of "usage_usec x\n")) None))
  (assert (= (! (peak-mib-of "1073741824\n")) 1024))
  (assert (= (! (peak-mib-of "1073741823\n")) 1023))
  (assert (is (! (peak-mib-of "max\n")) None)))


(deftest test-the-target-puts-the-service-and-the-machine-after-the-deploy-attributes
  ;; 配備の属性はそのまま前に置き、service.name と host.name を後ろに置く。同じ名の配備の属性は捨てる(2 つが勝つ)。
  (<- target TaskRecordTarget (task-record-target ENDPOINT #(#("deploy.boundary" "personal") #("service.name" "other")) "records.task-ends" "node-a"))
  (assert (= target TARGET) target))


(deftest test-the-row-holds-the-shared-keys-and-the-task-name-as-given
  ;; 行は resource 1 つ・record 1 つ。body の鍵は送り先の表の行の形の綴り、task の名は出し手の名をそのまま(空白・非 ASCII も)。読めない使いと
  ;; 無い task の名は null。record 属性は行の id(32 字の 16 進)と、在る時だけ kind。
  (val end (TaskEnd :job "task/t7" :kind "日次の検証 doeff" :attempt 2 :started-ms 1000 :ended-ms 5500 :exit-code 0
                    :usage (TaskUsage :cpu-seconds 1.5 :peak-mib None) :stopped None))
  (<- body dict (task-record-body TARGET end))
  (val record (get (get (get (get (get body "resourceLogs") 0) "scopeLogs") 0) "logRecords"))
  (assert (= (len record) 1) record)
  (val one (get record 0))
  (assert (= (json.loads (get (get one "body") "stringValue"))
             {"v" 1 "end" 5 "dur" 4.5 "node" "node-a" "layer" "jobs" "ticket" "task/t7" "kind" "日次の検証 doeff" "cpu" 1.5 "peak_mib" None
              "rc" 0 "stopped" None}))
  (assert (= (get one "timeUnixNano") "5500000000"))
  (assert (= (lfor a (get (get (get (get body "resourceLogs") 0) "resource") "attributes") #((get a "key") (get (get a "value") "stringValue")))
             (list TARGET.resource)))
  (val attributes (dfor a (get one "attributes") (get a "key") (get (get a "value") "stringValue")))
  (assert (= (sorted attributes) ["kind" "record_id"]) attributes)
  (assert (= (len (get attributes "record_id")) 32) attributes)
  (<- bare dict (task-record-body TARGET (dataclasses.replace end :kind None)))
  (val bare-one (get (get (get (get (get (get bare "resourceLogs") 0) "scopeLogs") 0) "logRecords") 0))
  (assert (is (get (json.loads (get (get bare-one "body") "stringValue")) "kind") None))
  (assert (= (lfor a (get bare-one "attributes") (get a "key")) ["record_id"]) bare-one))


(deftest test-the-refusal-reads-the-status-and-a-partial-success
  ;; 2xx でない答えは status の訳。2xx でも partialSuccess の rejectedLogRecords が 1 以上なら断りの訳(数は文字の綴りもある)。2xx の
  ;; 空の本文・JSON でない本文・真偽の値は受けたと読む。
  (assert (is (! (task-record-refusal 200 "")) None))
  (assert (is (! (task-record-refusal 200 "{}")) None))
  (assert (is (! (task-record-refusal 200 "not json")) None))
  (assert (is (! (task-record-refusal 200 "{\"partialSuccess\":{\"rejectedLogRecords\":true}}")) None))
  (assert (= (! (task-record-refusal 200 "{\"partialSuccess\":{\"rejectedLogRecords\":\"1\",\"errorMessage\":\"bad\"}}"))
             "送り先が行 1 本を断った: bad"))
  (assert (= (! (task-record-refusal 503 "busy")) "status 503: busy")))


(deftest test-the-task-name-of-the-reply-row-reaches-the-job-spec [#^ Path tmp-path]
  ;; heartbeat の返事の task の行の name を JobSpec.task-name へ写す(比べない欄 — 名だけ違っても同じ job)。名の無い行は None。
  (val row {"id" "t7" "name" "daily-verify doeff" "revision" "r" "program" None "versions" {}})
  (<- spec JobSpec (task-spec row tmp-path))
  (assert (= spec.task-name "daily-verify doeff"))
  (assert (= spec (dataclasses.replace spec :task-name "other")))
  (<- bare JobSpec (task-spec (dfor #(k v) (.items row) :if (!= k "name") k v) tmp-path))
  (assert (is bare.task-name None)))


(deftest test-the-reaped-task-sends-one-row-and-the-reap-does-not-wait-for-it [#^ Path tmp-path]
  ;; 回収は子の cpu.stat と memory.peak を読んで 1 行を送り先へ 1 回 POST する。送り先が遅くても(模擬の 5 秒)、回収の頼みの間に模擬の
  ;; 時計は進まない(送りは拍の外の task)。届いた行は失敗の行を出さない。
  (<- base HostSettings (host-settings tmp-path))
  (val settings (dataclasses.replace base :task-record TARGET))
  (<- group str (cgroup-child tmp-path))
  (val started (Started :view (ProcessView "task/t7" TASK 1 4242 (- NOON-MS 60000)) :fork None :group group))
  (val collector (Collector (HttpResponse 200 {} b"{}" "{}" ENDPOINT 0.0)))
  (val told (ToldLines))
  (<- took int (run-reaped settings started collector told))
  (assert (= took 0) took)
  (assert (= (lfor #(method url _) collector.posts #(method url)) [#("POST" ENDPOINT)]) collector.posts)
  (<- rows tuple (posted-fields collector))
  (setv #(#(resource attributes fields at)) rows)
  (assert (= resource {"deploy.boundary" "personal" "service.name" "records.task-ends" "host.name" "node-a"}) resource)
  (assert (= (get attributes "kind") "daily-verify doeff") attributes)
  (assert (= fields {"v" 1 "end" (// NOON-MS 1000) "dur" 60.0 "node" "node-a" "layer" "jobs" "ticket" "task/t7" "kind" "daily-verify doeff"
                     "cpu" 125.5 "peak_mib" 2048 "rc" 3 "stopped" "memory-watch"})
          fields)
  (assert (= at (str (* NOON-MS 1000000))) at)
  (<- failed tuple (failure-lines told))
  (assert (= failed #()) failed))


(deftest test-an-unsent-row-leaves-one-worker-line-and-the-reap-goes-on [#^ Path tmp-path]
  ;; 送り先に届かない(HttpFailed)・2xx でない・答え手が投げた時: 回収は止まらず(頼みの間に模擬の時計が進まず、筋書きが終わりまで回る)、
  ;; worker の log に job の名を名指した行が 1 つ残る。撃ち直さない(POST は 1 回)。
  (<- base HostSettings (host-settings tmp-path))
  (val settings (dataclasses.replace base :task-record TARGET))
  (<- group str (cgroup-child tmp-path))
  (val started (Started :view (ProcessView "task/t7" TASK 1 4242 (- NOON-MS 60000)) :fork None :group group))
  (for [#(answer expected) #(#((HttpFailed :url ENDPOINT :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED) "ConnectError: refused")
                            #((HttpResponse 500 {} b"down" "down" ENDPOINT 0.0) "status 500: down")
                            #((RuntimeError "答え手の思わぬ失敗") "RuntimeError: 答え手の思わぬ失敗"))]
    (val collector (Collector answer))
    (val told (ToldLines))
    (<- took int (run-reaped settings started collector told))
    (assert (= took 0) #(answer took))
    (assert (= (len collector.posts) 1) #(answer collector.posts))
    (<- failed tuple (failure-lines told))
    (assert (= failed #({"job" "task/t7" "level" "warning" "detail" expected})) #(answer failed))))


(deftest test-a-service-or-a-worker-without-a-target-sends-nothing [#^ Path tmp-path]
  ;; 常駐の job と、送り先を渡されていない worker は送らない(送り先の代役は 1 つも受けない)。
  (<- base HostSettings (host-settings tmp-path))
  (val service (JobSpec "records" "doeff_cluster._worker.entry.job_entry" #() "r"))
  (val collector (Collector (HttpResponse 200 {} b"" "" ENDPOINT 0.0)))
  (val told (ToldLines))
  (<- (run-reaped (dataclasses.replace base :task-record TARGET) (Started :view (ProcessView "records" service 1 2 0) :fork None) collector told))
  (<- (run-reaped base (Started :view (ProcessView "task/t9" TASK 1 3 0) :fork None) collector told))
  (assert (= collector.posts #()) collector.posts))


(deftest test-a-task-outside-a-child-gets-nulls-and-a-stopped-task-names-the-stop [#^ Path tmp-path]
  ;; 子に置かなかった task(置き分けない worker・cgroup を受けない版の待ちの子から分けた task)は使いを null で送る。機体の memory の訳が
  ;; 無く worker が止めを求めた task は、止めの合図の訳の語を stopped に置く。
  (<- base HostSettings (host-settings tmp-path))
  (val settings (dataclasses.replace base :task-record TARGET))
  (val asked (StopAsked :requested-ms (- NOON-MS 1000) :killed False :reason (SpecChanged)))
  (val collector (Collector (HttpResponse 200 {} b"" "" ENDPOINT 0.0)))
  (<- (run-reaped settings (Started :view (ProcessView "task/t8" TASK 1 1 0) :fork None :stop asked) collector (ToldLines)
                  :outcome Outcome.STOPPED :stop-reason None))
  (<- rows tuple (posted-fields collector))
  (setv #(#(_ _ fields _)) rows)
  (assert (= #((get fields "cpu") (get fields "peak_mib") (get fields "stopped")) #(None None "spec-changed")) fields))


(deftest test-a-task-reaped-without-an-observed-exit-writes-null-rc [#^ Path tmp-path]
  ;; 終わりを観測していない回収(ReapJob の exit-code が None)でも recorded-task-end は断らず、行の rc に null を書く。
  (<- base HostSettings (host-settings tmp-path))
  (val settings (dataclasses.replace base :task-record TARGET))
  (val collector (Collector (HttpResponse 200 {} b"" "" ENDPOINT 0.0)))
  (<- (run-reaped settings (Started :view (ProcessView "task/t8" TASK 1 1 0) :fork None) collector (ToldLines)
                  :outcome Outcome.STOPPED :stop-reason None :exit-code None))
  (<- rows tuple (posted-fields collector))
  (val fields (get (get rows 0) 2))
  (assert (is (get fields "rc") None) fields)
  (assert (= (get fields "stopped") "stopped") fields))


(deftest test-the-stop-word-is-the-asked-reason-or-the-outcome
  ;; 止めを求めずに終わった task は None・止めの合図の記録が在ればその訳の語・記録の無い止めは outcome の語 stopped。
  (val asked (StopAsked :requested-ms 0 :killed True :reason (SpecChanged)))
  (assert (is (! (task-stop-word Outcome.EXITED asked)) None))
  (assert (= (! (task-stop-word Outcome.STOPPED asked)) "spec-changed"))
  (assert (= (! (task-stop-word Outcome.STOPPED None)) "stopped")))


(deftest test-the-worker-builds-the-target-only-from-a-usable-service-and-collector
  ;; 入口は service.name の env が無ければ送らない(None)。在るのに送り先の基の URL が無い・URL の形でない・配備の属性の並べ方が崩れて
  ;; いれば、送らない訳の文(値は出さない)。使える時は基の URL + /v1/logs へ、配備の属性・service.name・host.name(--node・空なら OS の
  ;; host の名)で送る。
  (val good {"DOEFF_WORKER_TASK_RECORD_SERVICE" "records.task-ends" "DOEFF_JOB_LOG_OTLP" "http://collector.test:4318/"
             "DOEFF_JOB_LOG_ATTRIBUTES" "deploy.boundary=personal"})
  (<- off (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of (dfor #(k v) (.items good) :if (!= k "DOEFF_WORKER_TASK_RECORD_SERVICE") k v) "node-a")))
  (assert (is off None) off)
  (<- blank (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of (| good {"DOEFF_WORKER_TASK_RECORD_SERVICE" " "}) "node-a")))
  (assert (is blank None) blank)
  (<- no-base (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of (dfor #(k v) (.items good) :if (!= k "DOEFF_JOB_LOG_OTLP") k v) "node-a")))
  (assert (= no-base "DOEFF_WORKER_TASK_RECORD_SERVICE が在るのに DOEFF_JOB_LOG_OTLP が無い") no-base)
  (<- bad-url (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of (| good {"DOEFF_JOB_LOG_OTLP" "collector.test"}) "node-a")))
  (assert (= bad-url "DOEFF_JOB_LOG_OTLP が http か https の絶対の URL でない") bad-url)
  (<- bad-attributes (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of (| good {"DOEFF_JOB_LOG_ATTRIBUTES" "personal"}) "node-a")))
  (assert (isinstance bad-attributes str) bad-attributes)
  (assert (not-in "personal" bad-attributes) bad-attributes)
  (<- target (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of good "node-a")))
  (assert (= target TARGET) target)
  (<- unnamed (| TaskRecordTarget str None) (with-handlers [named-machine] (task-record-target-of good "")))
  (assert (= #(unnamed.node (get unnamed.resource -1)) #("box-1" #("host.name" "box-1"))) unnamed))
