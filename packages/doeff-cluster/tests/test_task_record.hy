;; task の終わりの行(worker/core/task_record): coordinator の task の行は終わった後に消え、worker の log の memory の山は task の名と CPU を
;; 持たないので、終わった task の CPU と memory を後から数える元が無かった。worker は回収の時に task を置いた job の名の子の cpu.stat と
;; memory.peak を読み、設定の dir の UTC の日の file に JSON の 1 行を足す。日の file を初めて作る時に残す日数より古い日の file を消す。
;; cgroupfs の代わりは tmp の dir(cpu.stat・memory.peak は普通の file)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses)
(import datetime [datetime timezone])
(import json)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [os-file-handler slog-handler])
(import doeff_time [sim-time-handler])
(import doeff_cluster [JobSpec])
(import doeff_cluster._worker.core.task_record [KEEP-DAYS TaskUsage cpu-seconds-of day-file-name expired-day-files peak-mib-of
                                                task-record-line])
(import doeff_cluster._worker.intent.worker_model [Outcome ProcessView])
(import doeff_cluster._worker.protocol.declared [task-spec])
(import doeff_cluster._worker.protocol.process_host [HostSettings Started recorded-task-end])
(import tests.host_rig [host-settings])

;; 2026-11-16 12:00 UTC(epoch ms)。
(val NOON (datetime 2026 11 16 12 0 0 :tzinfo timezone.utc))
(val NOON-MS 1794830400000)
(val CPU-STAT "usage_usec 125500000\nuser_usec 120000000\nsystem_usec 5500000\nnr_periods 0\n")
(val TASK (JobSpec "task/t7" "doeff_cluster._worker.entry.job_entry" #() "r" :once True :task-name "daily-verify doeff"))


(deftest test-the-day-file-is-named-by-the-utc-day-and-old-day-files-expire
  ;; 日の file の名は UTC の日。残す日数より前の日の file だけを消す候補にし、日の file の形でない名は消さない。
  (assert (= (! (day-file-name NOON-MS)) "20261116.log"))
  (assert (= (! (day-file-name (- NOON-MS (* 12 3600 1000) 1))) "20261115.log") "UTC の日の境の 1 ms 前は前の日")
  (val oldest (! (day-file-name (- NOON-MS (* KEEP-DAYS 24 3600 1000)))))
  (val names #("20200101.log" oldest "20261116.log" "notes.txt" "2026111.log" "20200101.log.tmp"))
  (assert (= (! (expired-day-files names NOON-MS)) #("20200101.log")) "残す日数ちょうどの日は残す"))


(deftest test-cpu-seconds-and-the-memory-peak-are-read-from-the-cgroup-files
  ;; usage_usec は子孫を含む CPU の和。memory.peak は byte で、MiB に切り捨てる(1024 以上 = 1 GiB 以上)。読めない形は None(0 で埋めない)。
  (assert (= (! (cpu-seconds-of CPU-STAT)) 125.5))
  (assert (is (! (cpu-seconds-of "user_usec 1\n")) None))
  (assert (is (! (cpu-seconds-of "usage_usec x\n")) None))
  (assert (= (! (peak-mib-of "1073741824\n")) 1024))
  (assert (= (! (peak-mib-of "1073741823\n")) 1023))
  (assert (is (! (peak-mib-of "max\n")) None)))


(deftest test-the-line-holds-only-names-numbers-and-the-task-name-as-given
  ;; 行は JSON の 1 行で、task の名は出し手の名をそのまま(空白・非 ASCII も)。読めない使いは null。
  (val line (! (task-record-line "task/t7" "日次の検証 doeff" 2 1000 5000 "exited" 0 (TaskUsage :cpu-seconds 1.5 :peak-mib None) None)))
  (assert (.endswith line "\n"))
  (assert (= (.count line "\n") 1))
  (assert (= (json.loads line) {"v" 1 "job" "task/t7" "task" "日次の検証 doeff" "attempt" 2 "startedMs" 1000 "endedMs" 5000
                                "outcome" "exited" "exitCode" 0 "stopReason" None "cpuSeconds" 1.5 "memoryPeakMib" None})))


(deftest test-the-task-name-of-the-reply-row-reaches-the-job-spec [#^ Path tmp-path]
  ;; heartbeat の返事の task の行の name を JobSpec.task-name へ写す(比べない欄 — 名だけ違っても同じ job)。名の無い行は None。
  (val row {"id" "t7" "name" "daily-verify doeff" "revision" "r" "program" None "versions" {}})
  (<- spec JobSpec (task-spec row tmp-path))
  (assert (= spec.task-name "daily-verify doeff"))
  (assert (= spec (dataclasses.replace spec :task-name "other")))
  (<- bare JobSpec (task-spec (dfor #(k v) (.items row) :if (!= k "name") k v) tmp-path))
  (assert (is bare.task-name None)))


(defk run-recorder [settings started]
  {:pre [(: settings HostSettings) (: started Started)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "回収の時の行の書きを、本物の file の答え手と 2026-11-16 12:00 UTC に止めた時計で 1 回回すため。"
  (<- (with-handlers [(sim-time-handler :start-time NOON) slog-handler os-file-handler]
        (recorded-task-end settings started Outcome.EXITED 3 "memory-watch")))
  None)


(deftest test-the-reaped-task-leaves-one-line-and-the-first-line-of-the-day-expires-old-files [#^ Path tmp-path]
  ;; 本物の file の答え手: 子の cpu.stat と memory.peak を読んで日の file に 1 行足す。その日の最初の行の時だけ古い日の file を消す
  ;; (同じ日の 2 行目は dir を読まない — 後から置いた古い file が残る)。日の file の形でない file は消さない。
  (<- base HostSettings (host-settings tmp-path))
  (val records (/ tmp-path "records"))
  (.mkdir records)
  (.write-text (/ records "20200101.log") "{}\n")
  (.write-text (/ records "keep.txt") "x\n")
  (val group (/ tmp-path "cg" "jobs" "task_t7"))
  (.mkdir group :parents True)
  (.write-text (/ group "cpu.stat") CPU-STAT)
  (.write-text (/ group "memory.peak") "2147483648\n")
  (val settings (dataclasses.replace base :task-record-dir (str records)))
  (val started (Started :view (ProcessView "task/t7" TASK 1 4242 (- NOON-MS 60000)) :fork None :group (str group)))
  (<- (run-recorder settings started))
  (assert (not (.exists (/ records "20200101.log"))) "古い日の file を消す")
  (assert (.exists (/ records "keep.txt")) "日の file の形でない file は消さない")
  (.write-text (/ records "20200102.log") "{}\n")
  (<- (run-recorder settings started))
  (assert (.exists (/ records "20200102.log")) "同じ日の 2 行目は消さない")
  (val lines (.splitlines (.read-text (/ records "20261116.log"))))
  (assert (= (len lines) 2) lines)
  (assert (= (json.loads (get lines 0))
             {"v" 1 "job" "task/t7" "task" "daily-verify doeff" "attempt" 1 "startedMs" (- NOON-MS 60000) "endedMs" NOON-MS
              "outcome" "exited" "exitCode" 3 "stopReason" "memory-watch" "cpuSeconds" 125.5 "memoryPeakMib" 2048})
         (get lines 0)))


(deftest test-a-task-outside-a-child-gets-nulls-and-a-service-or-an-unset-dir-writes-nothing [#^ Path tmp-path]
  ;; 子に置かなかった task(置き分けない worker・cgroup を受けない版の待ちの子から分けた task)は使いを null で書く。常駐の job と、dir を
  ;; 渡されていない worker は書かない。
  (<- base HostSettings (host-settings tmp-path))
  (val records (/ tmp-path "records"))
  (val settings (dataclasses.replace base :task-record-dir (str records)))
  (<- (run-recorder settings (Started :view (ProcessView "task/t8" TASK 1 1 0) :fork None)))
  (val row (json.loads (.read-text (/ records "20261116.log"))))
  (assert (= #((get row "cpuSeconds") (get row "memoryPeakMib")) #(None None)) row)
  (val service (JobSpec "records" "doeff_cluster._worker.entry.job_entry" #() "r"))
  (<- (run-recorder settings (Started :view (ProcessView "records" service 1 2 0) :fork None :group (str tmp-path))))
  (assert (= (len (.splitlines (.read-text (/ records "20261116.log")))) 1) "常駐の job は書かない")
  (<- (run-recorder base (Started :view (ProcessView "task/t9" TASK 1 3 0) :fork None)))
  (assert (= (len (.splitlines (.read-text (/ records "20261116.log")))) 1) "dir の無い worker は書かない"))
