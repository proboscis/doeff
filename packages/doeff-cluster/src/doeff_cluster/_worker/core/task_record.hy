;;; task の終わりの行(task 1 本ごとの CPU 秒と memory の山の記録)の判断: worker が終わった task を回収する時(worker/protocol/process_host の
;;; ReapJob)に、task を置いた job の名の子(worker/core/job_cgroup の「job ごとの子」)の cpu.stat の usage_usec と memory.peak を読み、
;;; 機体の配備が渡した dir(DOEFF_WORKER_TASK_RECORD_DIR — worker/entry/main が読む・無ければ書かない)の UTC の日ごとの file
;;; <YYYYMMDD>.log に JSON の 1 行を足す。行を読んで束ねるのは機体の外の測り(上に載る系が決める — doeff-cluster は読み手を知らない)。
;;;
;;; 訳: coordinator の task の行は終わった後に消える(呼び手の lease の切れ・出し手の retain・置き直し)ので、終わった task の CPU と memory を
;;; 後から job の名で数える元が無かった。worker の log の memory の山(memory_watch)は process の木の RSS の和で、task の名と CPU を持たない。
;;; 次の task の前の掃き(SWEEP-SCRIPT)で読まない訳: 掃きは次の task を起こす時にしか走らず(task の来ない間と世代の退きの後は読まれない)、
;;; 終わりの刻と終了 code を持たない。回収の時は子がまだ在り(消すのは次の掃き)、終わりの観測と同じ拍に在る。
;;;
;;; 行の欄: v = 形の版(TASK-RECORD-VERSION)・job = job の名(task/<id>)・task = task の名(JobSpec.task-name — coordinator の task の行の
;;; name をそのまま。無ければ null)・attempt = 試みの番号・startedMs・endedMs = 起こした刻と回収した刻(epoch ms)・outcome = exited
;;; (止めを求めずに終わった)か stopped・exitCode = 終了 code・stopReason = 機体の memory の訳で止めた訳(memory の見張りの止めか層の
;;; OOM kill — ReapJob.stop-reason・無ければ null)・cpuSeconds = 子の CPU 秒(usage_usec — 子孫と shim を含む)・memoryPeakMib =
;;; 子の memory の山(memory.peak の MiB — 切り捨てなので 1024 以上 = 1 GiB 以上)。子に置かなかった task(置き分けない worker・頼みの cgroup を
;;; 受けない版の待ちの子から分けた task)と読めなかった値は null(0 で埋めない — 層の子に memory を掛ける前の子は memory.peak を持たない)。
;;; 命令の引数・cwd・環境変数は書かない(資格を運びうる)。
;;;
;;; 寿命: 書き手が日の file を初めて作る時に、KEEP-DAYS 日より前の日の file を消す(読み手は日の名で読むので、消す者は書き手 1 人)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import re)
(import doeff_cluster._shared.core.clock [datetime-of-epoch-ms])


;; 行の形の版(欄を替えたら上げる — 読み手は知らない版の行を読まずに数える)。
(val TASK-RECORD-VERSION 1)
;; 日の file を残す日数(1 日 約 1,600 本 × 1 行 約 250 byte × 45 日 = 約 18 MB)。
(val KEEP-DAYS 45)
;; 日の file の名の形(UTC の日)。
(val DAY-FILE-PATTERN "[0-9]{8}\\.log")
(val ONE-DAY-MS (* 24 60 60 1000))
(val MIB (* 1024 1024))


(defrecord TaskUsage
  "task を置いた job の名の子の cgroup から読んだ使い(行の欄 cpuSeconds・memoryPeakMib): cpu-seconds = CPU 秒・peak-mib = memory の山の
   MiB。読めない値(子に置いていない・file が無い・形が違う)は None。"
  (#^ (| float None) cpu-seconds)
  (#^ (| int None) peak-mib))


(defk day-file-name [at-ms]
  {:pre [(: at-ms int)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "刻 at-ms(epoch ms)の行を足す日の file の名(UTC の日の <YYYYMMDD>.log)を決めるため。"
  (+ (.strftime (datetime-of-epoch-ms at-ms) "%Y%m%d") ".log"))


(defk expired-day-files [names at-ms]
  {:pre [(: names tuple) (: at-ms int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "dir の中の名 names のうち、刻 at-ms の日から KEEP-DAYS 日より前の日の file の名を選ぶため(名の順)。日の file の形でない名は消さない。"
  (<- oldest str (day-file-name (- at-ms (* KEEP-DAYS ONE-DAY-MS))))
  (tuple (sorted (gfor name names :if (and (re.fullmatch DAY-FILE-PATTERN name) (< name oldest)) name))))


(defk cpu-seconds-of [stat]
  {:pre [(: stat str)] :post [(: % (| float None))] :tags {:context "worker" :role "judgment"}}
  "cgroup の cpu.stat の中身から CPU 秒(usage_usec ÷ 10^6・ms まで)を読むため。行が無い・数でなければ None。"
  (for [line (.splitlines stat)]
    (val parts (.split line))
    (when (and (= (len parts) 2) (= (get parts 0) "usage_usec") (.isdigit (get parts 1)))
      (return (round (/ (int (get parts 1)) 1000000) 3))))
  None)


(defk peak-mib-of [text]
  {:pre [(: text str)] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "cgroup の memory.peak の中身(byte の数)を MiB(切り捨て)に読むため。数でなければ None。"
  (val value (.strip text))
  (if (.isdigit value) (// (int value) MIB) None))


(defk task-record-line [job task attempt started-ms ended-ms outcome exit-code usage stop-reason]
  {:pre [(: job str) (: task (| str None)) (: attempt int) (: started-ms int) (: ended-ms int) (: outcome str) (: exit-code int)
         (: usage TaskUsage) (: stop-reason (| str None))]
   :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "task の終わりの行(頭の註の欄 — 改行で終わる JSON の 1 行)を作るため。"
  (+ (json.dumps {"v" TASK-RECORD-VERSION "job" job "task" task "attempt" attempt "startedMs" started-ms "endedMs" ended-ms
                  "outcome" outcome "exitCode" exit-code "stopReason" stop-reason "cpuSeconds" usage.cpu-seconds
                  "memoryPeakMib" usage.peak-mib}
                 :ensure-ascii False :separators #("," ":"))
     "\n"))
