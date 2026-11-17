;;; task の終わりの行(task 1 本ごとの CPU 秒と memory の山の記録)の判断: worker が終わった task を回収する時(worker/protocol/process_host の
;;; ReapJob)に、task を置いた job の名の子(worker/core/job_cgroup の「job ごとの子」)の cpu.stat の usage_usec と memory.peak を読み、
;;; OTLP/HTTP の logs の 1 行にして、job の log と同じ送り先(DOEFF_JOB_LOG_OTLP の基の URL + /v1/logs — worker/entry/line_stamp の
;;; 取り決め job-log/1)へ 1 回 POST する。送るのは、機体の配備が行の service.name を env DOEFF_WORKER_TASK_RECORD_SERVICE で渡した
;;; worker だけ(worker/entry/main が読む — 無ければ送らない)。行を読んで束ねるのは送り先の表の読み手(上に載る系が決める — doeff-cluster は
;;; 読み手と表を知らない)。
;;;
;;; 訳: coordinator の task の行は終わった後に消える(呼び手の lease の切れ・出し手の retain・置き直し)ので、終わった task の CPU と memory を
;;; 後から job の名で数える元が無かった。worker の log の memory の山(memory_watch)は process の木の RSS の和で、task の名と CPU を持たない。
;;; 次の task の前の掃き(SWEEP-SCRIPT)で読まない訳: 掃きは次の task を起こす時にしか走らず(task の来ない間と世代の退きの後は読まれない)、
;;; 終わりの刻と終了 code を持たない。回収の時は子がまだ在り(消すのは次の掃き)、終わりの観測と同じ拍に在る。
;;; 機体の file に書かない訳(前の形は配備が渡した dir の UTC の日の file に足し、45 日より前の日の file を書き手が消した): 増え続ける記録を
;;; 機体ごとの file に置くと、読み手が機体ごとに file を読みに行き、残す日数を書き手が持つ。送り先の表なら 1 か所で読め、残す長さは表が決める。
;;;
;;; 送りの形(1 回の POST = resource 1 つ・行 1 つ — task-record-body):
;;;   resource 属性  配備が DOEFF_JOB_LOG_ATTRIBUTES で足す属性(送り先が行を振り分ける名乗りなど — job の log と同じ値。この部品は名を
;;;                  持たない)+ service.name = DOEFF_WORKER_TASK_RECORD_SERVICE の値 + host.name = 機体の名(worker の --node・無ければ OS の
;;;                  host の名)。同じ名なら後の 2 つが勝つ
;;;   1 record       timeUnixNano = 回収の刻・body = 下の欄の JSON・record 属性 record_id(機体・job の名・試み・回収の刻の sha256 の頭 — 送り先が
;;;                  送り直しの重複を畳む鍵)と kind(task の名 — 在る時だけ)
;;;   欄             v = 形の版(TASK-RECORD-VERSION)・end = 回収の unix 秒・dur = 起こしてから回収までの秒(小数 2 桁)・node = 機体の名・
;;;                  layer = jobs(task を置く jobs の層 — worker/core/job_cgroup)・ticket = job の名(task/<id>)・kind = task の名
;;;                  (JobSpec.task-name — coordinator の task の行の name をそのまま。種類への読み替えは読み手。無ければ null)・cpu = 子の
;;;                  CPU 秒(usage_usec — 子孫と shim を含む・ms まで)・peak_mib = 子の memory の山(memory.peak の MiB の切り捨て — 1024 以上 =
;;;                  1 GiB 以上)・rc = 終了 code・stopped = 止めた訳(機体の memory の訳 — ReapJob.stop-reason。無ければ worker が止めを
;;;                  求めた訳の語 — process_host の task-stop-word。止めを求めずに終わった task は null)。鍵の綴りは送り先の表の行の形に揃える
;;;                  (形を決めるのは上に載る系 — 同じ表に別の書き手の行が並ぶ)。子に置かなかった task(置き分けない worker・頼みの cgroup を
;;;                  受けない版の待ちの子から分けた task)と読めなかった値は null(0 で埋めない — 層の子に memory を掛ける前の子は memory.peak を
;;;                  持たない)。命令の引数・cwd・環境変数は書かない(資格を運びうる)
;;;   取りこぼし     送るのは 1 回だけ(撃ち直さない)。送り先に届かない・2xx でない・行を断った時は、worker の log に job の名を名指した 1 行を
;;;                  残して捨てる(回収と task を止めない — task-record-refusal)。送りは拍の外の task なので、worker の止めの最後の拍で回収した
;;;                  task の行は、送りの task が終わる前に process が終われば届かない
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import hashlib)
(import json)


;; 行の形の版(欄を替えたら上げる — 読み手は知らない版の行を読まずに数える)。
(val TASK-RECORD-VERSION 1)
;; 欄 layer の値(task を置く jobs の層 — worker/core/job_cgroup の「job ごとの子」の親)。
(val TASK-RECORD-LAYER "jobs")
;; 行の scope の名(OTLP の InstrumentationScope — 送り手の名乗り)。
(val TASK-RECORD-SCOPE "doeff.task-record")
;; 行の id の 16 進の字数。
(val RECORD-ID-CHARS 32)
;; 送れなかった訳に写す送り先の答えの本文の字数の上限。
(val REFUSAL-CHARS 200)
(val MIB (* 1024 1024))


(defrecord TaskUsage
  "task を置いた job の名の子の cgroup から読んだ使い(行の欄 cpu・peak_mib): cpu-seconds = CPU 秒・peak-mib = memory の山の
   MiB。読めない値(子に置いていない・file が無い・形が違う)は None。"
  (#^ (| float None) cpu-seconds)
  (#^ (| int None) peak-mib))


(defrecord TaskRecordTarget
  "task の終わりの行の送り先(worker/entry/main が機体の env から 1 度だけ作る — task-record-target): endpoint = POST する URL(基の URL +
   /v1/logs)・node = 機体の名(resource の host.name と欄 node)・resource = 行に付ける resource 属性の #(名 値) の並び。"
  (#^ str endpoint)
  (#^ str node)
  (#^ (get tuple #((get tuple #(str str)) ...)) resource))


(defrecord TaskEnd
  "回収した task 1 本の終わり(行の欄の元 — 頭の註の欄): job = job の名(task/<id>)・kind = task の名(無ければ None)・attempt = 試みの
   番号・started-ms・ended-ms = 起こした刻と回収した刻(epoch ms)・exit-code = 終了 code・usage = 子の使い・stopped = 止めた訳
   (無ければ None)。"
  (#^ str job)
  (#^ (| str None) kind)
  (#^ int attempt)
  (#^ int started-ms)
  (#^ int ended-ms)
  (#^ int exit-code)
  (#^ TaskUsage usage)
  (#^ (| str None) stopped))


(defk task-record-target [endpoint extra service node]
  {:pre [(: endpoint str) (: extra tuple) (: service str) (: node str)] :post [(: % TaskRecordTarget)]
   :tags {:context "worker" :role "judgment"}}
  "送り先を作るため(頭の註の resource 属性): 配備が足す属性 extra(#(名 値) の並び)の後ろに service.name = service と host.name = node を
   置く。同じ名の配備の属性は捨てる(この 2 つが勝つ)。"
  (val own #(#("service.name" service) #("host.name" node)))
  (val owned (frozenset (gfor pair own (get pair 0))))
  (TaskRecordTarget :endpoint endpoint :node node
                    :resource (+ (tuple (gfor pair extra :if (not-in (get pair 0) owned) pair)) own)))


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


(defk task-record-id [node end]
  {:pre [(: node str) (: end TaskEnd)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "行の id を作るため(頭の註の record 属性 — 機体・job の名・試み・回収の刻〔ナノ秒〕の sha256 の 16 進の頭 RECORD-ID-CHARS 字)。"
  (val key (.format "{}\n{}\n{}\n{}" node end.job end.attempt (* end.ended-ms 1000000)))
  (cut (.hexdigest (hashlib.sha256 (.encode key "utf-8"))) 0 RECORD-ID-CHARS))


(defk task-record-body [target end]
  {:pre [(: target TaskRecordTarget) (: end TaskEnd)] :post [(: % dict)] :tags {:context "worker" :role "judgment" :spells "json"}}
  "task の終わりの行を OTLP/JSON の ExportLogsServiceRequest(resource 1 つ・行 1 つ)に綴るため(頭の註の送りの形 — 本文は JSON の境界の
   dict で、HTTP の答え手が JSON に綴る)。"
  (val pair (fn [key value] {"key" key "value" {"stringValue" value}}))
  (val nanos (str (* end.ended-ms 1000000)))
  (val fields {"v" TASK-RECORD-VERSION "end" (// end.ended-ms 1000) "dur" (round (/ (- end.ended-ms end.started-ms) 1000) 2)
               "node" target.node "layer" TASK-RECORD-LAYER "ticket" end.job "kind" end.kind "cpu" end.usage.cpu-seconds
               "peak_mib" end.usage.peak-mib "rc" end.exit-code "stopped" end.stopped})
  (val keys (+ #(#("record_id" (! (task-record-id target.node end)))) (if (is end.kind None) #() #(#("kind" end.kind)))))
  {"resourceLogs" [{"resource" {"attributes" (lfor #(k v) target.resource (pair k v))}
                    "scopeLogs" [{"scope" {"name" TASK-RECORD-SCOPE "version" (str TASK-RECORD-VERSION)}
                                  "logRecords" [{"timeUnixNano" nanos "observedTimeUnixNano" nanos
                                                 "severityText" "INFO" "severityNumber" 9
                                                 "body" {"stringValue" (json.dumps fields :ensure-ascii False :separators #("," ":"))}
                                                 "attributes" (lfor #(k v) keys (pair k v))}]}]}]})


(defk task-record-refusal [status text]
  {:pre [(: status int) (: text str)] :post [(: % (| str None))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "送り先の答え(status と本文 text)から、行を受けなかった訳を読むため(頭の註の取りこぼし — None = 受けた)。2xx でなければ status の訳、
   2xx でも OTLP の部分の受け(partialSuccess の rejectedLogRecords)で断っていれば断りの訳。2xx の空の本文・JSON でない本文は受けたと読む
   (OTLP/HTTP の約束)。"
  (when (not (<= 200 status 299))
    (return (.format "status {}: {}" status (cut text 0 REFUSAL-CHARS))))
  (val document (try (json.loads text) (except [ValueError] None)))
  (val partial (if (isinstance document dict) (.get document "partialSuccess") None))
  (val rejected (if (isinstance partial dict) (.get partial "rejectedLogRecords") None))
  ;; 数は OTLP/JSON の int64 の綴りで文字のこともある(真偽は数でない)。
  (val count (match rejected
               (bool) 0
               (int) rejected
               (str) :if (.isdigit rejected) (int rejected)
               _ 0))
  (if (> count 0)
      (.format "送り先が行 {} 本を断った: {}" count (cut (str (.get partial "errorMessage" "")) 0 REFUSAL-CHARS))
      None))
