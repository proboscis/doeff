;;; worker の預かりの process の宿(shared/intent/hosted_model の LaunchHosted・StopHosted・ListHosted の worker の中の答え手)。job の
;;; process の木の外に、worker の子として process を起こし、id と持ち主の job の名で覚え、終わりを拾い、持ち主が居なくなったら止める。
;;; 汎用の子 process の効果(StartProcess・PollProcess)・machine の process の表の効果(ReadProcTable・SignalPid)・file system の効果
;;; (MakeDirectory・WriteText・ReadText・ListDirectory・RemoveTree)へ言い換える。I/O を持たない — 本物は外側の subprocess-handler・
;;; os-proc-table-handler・os-file-handler。判断(置き場・id・層・持ち主)は worker/core/hosted_rules。
;;;
;;; 起こし方: 専用の session(process-group — job の group への合図も、launchd が worker の group に送る止めも届かない)で、worker の
;;; 終わりの後も動き続ける子(ChildLifetime.OUTLIVES-STARTER — worker の消失で止める lifeline に載せない)。層を頼まれたら、その層の
;;; cgroup.procs へ移ってから exec する sh で包む(worker/core/job_cgroup の placed-argv — 外の job-cgroup-placer は包み直さない)。
;;; worker の環境は継がない(頼みの env と、置き場の 2 つの環境変数だけ)。cwd と出力の file は置き場の dir。
;;;
;;; 覚え方: 起こした直後に置き場の中へ記録(HostedRecord — pid と、machine の process の表の start-ticks)を書く。worker の世代が替わったら、
;;; 新しい世代は置き場の根を読んで覚え直す(走査は頼みの度と拍の度 — 同じ機体に並ぶ前の世代が起こした物も拾う)。終わりの観測は、
;;; 自分の世代が起こした子は PollProcess(終わりの code も拾う)、覚え直した物は process の表に同じ pid と start-ticks の行が在るか
;;; (code は分からない — None)。合図は SignalPid(start-ticks が違えば送らない — pid の使い回しで別の process を止めない)。
;;;
;;; 持ち主不在の止め: 拍の宣言の読み ReadDesired と世界の観測 ObserveWorld を外側へ渡し、その答えを読む(pod_labels の pod-job-labels と
;;; 同じ挟み方)。宣言が読めて、退いた世代でも途絶で絞った宣言でもなく、worker が止まり始めていない拍だけ、持ち主の居ない預かりの
;;; process に TERM を送り、猶予(stop-grace-ms)の後の拍でも残れば KILL を送り、終わった物の置き場を消して忘れる(拍を猶予の間
;;; 止めない)。退いた世代と止まり始めた worker は止めない — 次の世代が覚え直して判じる。
;;;
;;; 表は入れ物(HostedCell)が持つ: 口の頼みは拍の外の task が答え(worker/entry/hosted_socket)、持ち主不在の止めは拍が答えるので、2 つの
;;; task が同じ表を読み書きする。session の値は節の頭で読んだ写しなので、効果を待つ間に他の task が書いた物を失う — 入れ物を効果の後に
;;; 読み直して合わせる。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])  ; defrecord・defwire の展開が名指す
(import doeff_hy._wire [Malformed parse-json dump-json])
(import doeff_core_effects [slog])
(import doeff_core_effects [FileFailed ListDirectory MakeDirectory PathKind PathStat ReadText RemoveTree StatPath WriteText])
(import doeff_core_effects [ChildLifetime EnvEntry EnvMode PollProcess ProcessExited ProcessNotChild ProcessNotStarted ProcessRunning
                            ProcessSignal ProcessStarted StartProcess])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid])
(import doeff_core_effects [RandomBytes])
(import doeff_time [Delay])
(import doeff_cluster._shared.core.clock [now-epoch-ms])
(import doeff_cluster._shared.intent.hosted_model [HostedLaunch HostedProcess HostedRefused LaunchHosted StopHosted ListHosted])
(import doeff_cluster._worker.intent.worker_model [DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorldView])
(import doeff_cluster._worker.core.hosted_rules [HostedRecord ID-BYTES ID-VAR OUTPUT-NAME PLACE-VAR RECORD-NAME STOP-GRACE-MS
                                                 hosted-id-of hosted-id? hosted-layer-procs hosted-orphans hosted-owners hosted-place-of])
(import doeff_cluster._worker.core.job_cgroup [placed-argv])

;; 頼みと答えの綴りの版(口の 1 行の欄 v)。
(val WIRE-VERSION 1)
;; 頼みの止め(StopHosted)が終わりを読み直す間(秒)。
(val STOP-POLL-SECONDS 0.05)


(defrecord HostedSettings
  "預かりの process の宿の設定(worker の組み立ての入口 main が作る): root = 置き場の根(worker/core/hosted_rules の hosted-root-of —
   世代をまたいで分け合う dir の下)・generation = この worker の世代の印(記録に書く)・layers = cgroup の層の名 → その層の
   cgroup.procs(None = 層の宣言の無い worker — 層を頼まれても今の置き方のまま起こす)・stop-grace-ms = 止めの TERM から KILL までの猶予。"
  {:tags {:context "worker" :role "type"}}
  (#^ str root)
  (#^ str generation)
  (setv #^ (| (get dict #(str str)) None) layers None)
  (setv #^ int stop-grace-ms STOP-GRACE-MS))


(defrecord HostedEntry
  "宿が覚えている 1 本: record = 置き場の記録・own = この宿が起こした子(PollProcess で終わりと code を拾える)・ended = 終わりを観測した・
   exit-code = 終わりの code(own の子だけ)・term-ms = 持ち主不在の止めで TERM を送った刻(None = 送っていない)。"
  {:tags {:context "worker" :role "type"}}
  (#^ HostedRecord record)
  (#^ bool own)
  (setv #^ bool ended False)
  (setv #^ (| int None) exit-code None)
  (setv #^ (| int None) term-ms None))


(defclass HostedCell []
  "宿の表の入れ物(入口が作って渡す — 頭の註の 2 つの task が分け合う)。entries = id → HostedEntry・desired = 最後に読めた拍の宣言
   (DesiredJobs — None = まだ読めていない)・stopping = 最後の拍の宣言の読みで worker が止まり始めていた・told = log に出した最後の
   走査の失敗の訳。"
  (defn #^ None __init__ [self]
    "空の表で始めるため。"
    (setv self.entries {} self.desired None self.stopping False self.told "")))


;; --- 置き場の記録と口の綴り(JSON の境の型)--------------------------------------

(defwire HostedRecordWire
  "置き場の記録の file(RECORD-NAME)の綴り。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str id)
  (#^ int pid)
  (#^ int start-ticks)
  (#^ str owner)
  (setv #^ (| str None) layer None)
  (#^ str place)
  (#^ int started-ms)
  (#^ str generation))


(defwire HostedRequestWire
  "口の頼みの 1 行: op = launch・stop・list。launch は argv・env・layer・owner、stop は id を持つ。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ int v)
  (#^ str op)
  (setv #^ (| (get tuple #(str ...)) None) argv None)
  (setv #^ (| (get dict #(str str)) None) env None)
  (setv #^ (| str None) layer None)
  (setv #^ (| str None) owner None)
  (setv #^ (| str None) id None))


(defwire HostedProcessWire
  "口の答えの預かりの process 1 本(HostedProcess の綴り)。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str id)
  (#^ int pid)
  (#^ str owner)
  (setv #^ (| str None) layer None)
  (#^ str place)
  (#^ int started-ms)
  (setv #^ bool ended False)
  (setv #^ (| int None) exit-code None))


(defwire HostedAnswerWire
  "口の答えの 1 行: process(launch・stop)・processes(list)・refused(断りの訳)のどれか 1 つ。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ int v)
  (setv #^ (| HostedProcessWire None) process None)
  (setv #^ (| (get tuple #(HostedProcessWire ...)) None) processes None)
  (setv #^ (| str None) refused None))


(defk process-wire-of [process]
  {:pre [(: process HostedProcess)] :post [(: % HostedProcessWire)] :tags {:context "worker" :role "protocol"}}
  "答えの process を口の綴りにするため。"
  (HostedProcessWire :id process.id :pid process.pid :owner process.owner :layer process.layer :place process.place
                     :started-ms process.started-ms :ended process.ended :exit-code process.exit-code))


(defk process-of-wire [wire]
  {:pre [(: wire HostedProcessWire)] :post [(: % HostedProcess)] :tags {:context "worker" :role "protocol"}}
  "口の綴りの process を型に戻すため。"
  (HostedProcess :id wire.id :pid wire.pid :owner wire.owner :layer wire.layer :place wire.place :started-ms wire.started-ms
                 :ended wire.ended :exit-code wire.exit-code))


(defk process-wires-of [processes]
  {:pre [(: processes tuple)] :post [(: % tuple)] :tags {:context "worker" :role "protocol"}}
  "答えの process の列を口の綴りの列にするため。"
  (var wires #())
  (for [process processes]
    (<- wire HostedProcessWire (process-wire-of process))
    (:= wires (+ wires #(wire))))
  wires)


(defk processes-of-wires [wires]
  {:pre [(: wires tuple)] :post [(: % tuple)] :tags {:context "worker" :role "protocol"}}
  "口の綴りの process の列を型の列に戻すため。"
  (var processes #())
  (for [wire wires]
    (<- process HostedProcess (process-of-wire wire))
    (:= processes (+ processes #(process))))
  processes)


(defk hosted-request-text [request]
  {:pre [(: request (| LaunchHosted StopHosted ListHosted))] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "頼みの効果を口の 1 行にするため(頼む側 — worker/entry/hosted_socket の hosted-client)。"
  (<- text str
      (dump-json
        (match request
          (LaunchHosted) (HostedRequestWire :v WIRE-VERSION :op "launch" :argv request.launch.argv
                                            :env (dfor e request.launch.env e.name e.value)
                                            :layer request.launch.layer :owner request.launch.owner)
          (StopHosted) (HostedRequestWire :v WIRE-VERSION :op "stop" :id request.id)
          (ListHosted) (HostedRequestWire :v WIRE-VERSION :op "list"))))
  text)


(defk hosted-request-of [text]
  {:pre [(: text str)] :post [(: % (| LaunchHosted StopHosted ListHosted HostedRefused))] :tags {:context "worker" :role "protocol"}}
  "口の 1 行を頼みの効果に読むため(受ける側)。形の違う頼み・版の違う頼みは断りにする。"
  (<- wire (| HostedRequestWire Malformed) (parse-json HostedRequestWire text))
  (cond
    (isinstance wire Malformed) (HostedRefused :detail (.format "頼みの形が違う: {}" wire))
    (!= wire.v WIRE-VERSION) (HostedRefused :detail (.format "頼みの版 {} を読めない(この worker は {})" wire.v WIRE-VERSION))
    (= wire.op "list") (ListHosted)
    (and (= wire.op "stop") (is-not wire.id None)) (StopHosted wire.id)
    (and (= wire.op "launch") wire.argv wire.owner)
      (LaunchHosted (HostedLaunch :argv wire.argv :owner wire.owner :layer wire.layer
                                  :env (tuple (gfor #(k v) (.items (or wire.env {})) (EnvEntry :name k :value v)))))
    True (HostedRefused :detail (.format "頼みの op {!r} と欄が合わない" wire.op))))


(defk hosted-answer-text [answer]
  {:pre [(: answer (| HostedProcess HostedRefused tuple))] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "答えを口の 1 行にするため(受ける側)。"
  (var wire (HostedAnswerWire :v WIRE-VERSION))
  (match answer
    (HostedProcess) (do (<- one HostedProcessWire (process-wire-of answer))
                        (:= wire (HostedAnswerWire :v WIRE-VERSION :process one)))
    (HostedRefused) (:= wire (HostedAnswerWire :v WIRE-VERSION :refused answer.detail))
    _ (do (<- many tuple (process-wires-of answer))
          (:= wire (HostedAnswerWire :v WIRE-VERSION :processes many))))
  (<- text str (dump-json wire))
  text)


(defk hosted-answer-of [text listing]
  {:pre [(: text str) (: listing bool)] :post [(: % (| HostedProcess HostedRefused tuple))] :tags {:context "worker" :role "protocol"}}
  "口の答えの 1 行を型に読むため(頼む側)。listing = 一覧の頼みの答え(process の列を返す — 断りの時は断り)。"
  (<- wire (| HostedAnswerWire Malformed) (parse-json HostedAnswerWire text))
  (cond
    (isinstance wire Malformed) (HostedRefused :detail (.format "答えの形が違う: {}" wire))
    (is-not wire.refused None) (HostedRefused :detail wire.refused)
    (and listing (is-not wire.processes None)) (! (processes-of-wires wire.processes))
    (and (not listing) (is-not wire.process None)) (! (process-of-wire wire.process))
    True (HostedRefused :detail "答えに頼みの欄が無い")))


(defk record-text [record]
  {:pre [(: record HostedRecord)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "置き場の記録を file の中身にするため。"
  (<- text str (dump-json (HostedRecordWire :id record.id :pid record.pid :start-ticks record.start-ticks :owner record.owner
                                            :layer record.layer :place record.place :started-ms record.started-ms
                                            :generation record.generation)))
  text)


(defk record-of [text]
  {:pre [(: text str)] :post [(: % (| HostedRecord Malformed))] :tags {:context "worker" :role "protocol"}}
  "置き場の記録の file の中身を型に読むため。"
  (<- wire (| HostedRecordWire Malformed) (parse-json HostedRecordWire text))
  (if (isinstance wire Malformed)
      wire
      (HostedRecord :id wire.id :pid wire.pid :start-ticks wire.start-ticks :owner wire.owner :layer wire.layer :place wire.place
                    :started-ms wire.started-ms :generation wire.generation)))


;; --- 起こす・観る・止める ---------------------------------------------------------

(defk view-of [entry]
  {:pre [(: entry HostedEntry)] :post [(: % HostedProcess)] :tags {:context "worker" :role "protocol"}}
  "覚えている 1 本を答えの形にするため。"
  (val r entry.record)
  (HostedProcess :id r.id :pid r.pid :owner r.owner :layer r.layer :place r.place :started-ms r.started-ms :ended entry.ended
                 :exit-code entry.exit-code))


(defk start-ticks-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "worker" :role "protocol"}}
  "起こした process の start-ticks を machine の process の表から読むため(None = もう居ない)。"
  (<- rows tuple (ReadProcTable))
  (next (gfor row rows :if (= row.pid pid) row.start-ticks) None))


(defk launched [settings launch]
  {:pre [(: settings HostedSettings) (: launch HostedLaunch)] :post [(: % (| HostedEntry HostedRefused))]
   :tags {:context "worker" :role "protocol"}}
  "LaunchHosted を汎用の効果で答えるため(頭の註の起こし方と覚え方): 層を選び、置き場を作り、子を起こし、記録を書く。起こせなければ
   置き場を消して断る。"
  (<- procs (| str None HostedRefused) (hosted-layer-procs settings.layers launch.layer))
  (when (isinstance procs HostedRefused)
    (return procs))
  (<- noise bytes (RandomBytes ID-BYTES))
  (<- id str (hosted-id-of noise))
  (<- place str (hosted-place-of settings.root id))
  (<- made (| FileFailed None) (MakeDirectory place :mode 0o700))
  (when (isinstance made FileFailed)
    (return (HostedRefused :detail (.format "置き場 {} を作れない: {}" place made.detail))))
  (val env (+ launch.env #((EnvEntry :name PLACE-VAR :value place) (EnvEntry :name ID-VAR :value id))))
  (var argv launch.argv)
  (when (is-not procs None)
    (<- wrapped tuple (placed-argv procs launch.argv))
    (:= argv wrapped))
  (val output (+ place "/" OUTPUT-NAME))
  (<- now int (now-epoch-ms))
  (<- answer (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv argv :cwd place :env env :env-mode EnvMode.REPLACE :stdout-path output :stderr-path output
                    :process-group True :lifetime ChildLifetime.OUTLIVES-STARTER))
  (when (isinstance answer ProcessNotStarted)
    (<- (RemoveTree place))
    (return (HostedRefused :detail (+ "起こせない: " answer.detail))))
  ;; 起こした直後に終わった子は process の表に居ない — 起こした刻の印を 0 にし、次の観測が PollProcess で終わりを拾う(SignalPid は
  ;; 印の違う process へ送らない)。
  (<- ticks (| int None) (start-ticks-of answer.pid))
  (val record (HostedRecord :id id :pid answer.pid :start-ticks (if (is ticks None) 0 ticks) :owner launch.owner
                            :layer (if (is procs None) None launch.layer) :place place :started-ms now :generation settings.generation))
  (<- body str (record-text record))
  (<- written (| FileFailed None) (WriteText (+ place "/" RECORD-NAME) body :replace True))
  (when (isinstance written FileFailed)
    (<- (slog (.format "worker: hosted-record-unwritten id={} pid={} detail={}" id answer.pid written.detail) :level "warning")))
  (<- (slog (.format "worker: hosted-start id={} pid={} owner={} layer={} place={}" id answer.pid launch.owner record.layer place)))
  (HostedEntry :record record :own True))


(defk adopted [settings known]
  {:pre [(: settings HostedSettings) (: known dict)] :post [(: % (| dict str))] :tags {:context "worker" :role "protocol"}}
  "置き場の根を読み、まだ覚えていない id の記録を覚え直すため(頭の註の覚え方 — 前の世代や並ぶ世代が起こした物)。答え = 新しく覚えた
   id → HostedEntry(own 偽)・根を読めなければ訳の文(根がまだ無いのは空)。記録の無い dir と読めない記録は覚えない(起こしの途中)。"
  (<- seen (| PathStat FileFailed) (StatPath settings.root))
  (when (and (isinstance seen PathStat) (= seen.kind PathKind.MISSING))
    (return {}))
  (<- listed (| tuple FileFailed) (ListDirectory settings.root))
  (when (isinstance listed FileFailed)
    (return (.format "{}: {}" settings.root listed.detail)))
  (var found {})
  (for [entry listed]
    (when (and (= entry.kind PathKind.DIRECTORY) (! (hosted-id? entry.name)) (not-in entry.name known))
      (<- text (| str FileFailed) (ReadText (+ settings.root "/" entry.name "/" RECORD-NAME)))
      (when (isinstance text str)
        (<- record (| HostedRecord Malformed) (record-of text))
        (when (and (isinstance record HostedRecord) (= record.id entry.name))
          (:= found (| found {record.id (HostedEntry :record record :own False)}))))))
  found)


(defk observed [entries]
  {:pre [(: entries tuple)] :post [(: % dict)] :tags {:context "worker" :role "protocol"}}
  "終わりをまだ観測していない預かりの process の今を読むため(頭の註の終わりの観測)。答え = 変わった id → HostedEntry。"
  (var changed {})
  (var rows None)
  (for [entry entries]
    (when (not entry.ended)
      (var polled None)
      (when entry.own
        (<- answer (| ProcessRunning ProcessExited ProcessNotChild) (PollProcess entry.record.pid))
        (:= polled answer))
      (cond
        (isinstance polled ProcessExited)
          (:= changed (| changed {entry.record.id (replace entry :ended True :exit-code polled.exit-code)}))
        (isinstance polled ProcessRunning) None
        True
          (do (when (is rows None)
                (<- read tuple (ReadProcTable))
                (:= rows read))
              (val alive (any (gfor row rows :if (and (= row.pid entry.record.pid) (= row.start-ticks entry.record.start-ticks)) True)))
              (when (not alive)
                (:= changed (| changed {entry.record.id (replace entry :ended True)})))))))
  changed)


(defk signalled [entry signal]
  {:pre [(: entry HostedEntry) (: signal ProcessSignal)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "預かりの process 1 本へ合図を 1 度送るため(起こした刻の印の違う process — pid の使い回し — へは送らない)。答え = 届けたか。"
  (if entry.ended
      False
      (do (<- sent bool (SignalPid :pid entry.record.pid :start-ticks entry.record.start-ticks :signal signal))
          sent)))


(defk stopped-now [settings entry]
  {:pre [(: settings HostedSettings) (: entry HostedEntry)] :post [(: % HostedEntry)] :tags {:context "worker" :role "protocol"}}
  "StopHosted のため: TERM を送り、猶予の内に終わらなければ KILL を送り、終わりを読んでから置き場を消す。答え = 止めた後の 1 本。"
  (<- (signalled entry ProcessSignal.TERM))
  (var now entry)
  (<- began int (now-epoch-ms))
  (var killed False)
  (while (not now.ended)
    (<- (Delay STOP-POLL-SECONDS))
    (<- changed dict (observed #(now)))
    (:= now (.get changed now.record.id now))
    (<- at int (now-epoch-ms))
    (when (and (not now.ended) (not killed) (> (- at began) settings.stop-grace-ms))
      (<- (signalled now ProcessSignal.KILL))
      (:= killed True))
    (when (> (- at began) (* 2 settings.stop-grace-ms))
      (<- (slog (.format "worker: hosted-stop-unended id={} pid={}" now.record.id now.record.pid) :level "warning"))
      (break)))
  (<- (RemoveTree now.record.place))
  (<- (slog (.format "worker: hosted-stop id={} pid={} owner={} exit={} killed={}" now.record.id now.record.pid now.record.owner
                     now.exit-code killed)))
  now)


(defk orphan-step [settings entry now]
  {:pre [(: settings HostedSettings) (: entry HostedEntry) (: now int)] :post [(: % (| HostedEntry None))]
   :tags {:context "worker" :role "protocol"}}
  "持ち主の居ない 1 本を 1 段進めるため(頭の註の持ち主不在の止め — 拍を待たせない): 終わっていれば置き場を消して忘れる(None)、
   まだ TERM を送っていなければ送る、猶予を過ぎても残れば KILL を送る。答え = 残す 1 本か None。"
  (cond
    entry.ended
      (do (<- (RemoveTree entry.record.place))
          (<- (slog (.format "worker: hosted-orphan-forgotten id={} pid={} owner={} exit={}" entry.record.id entry.record.pid
                             entry.record.owner entry.exit-code)))
          None)
    (is entry.term-ms None)
      (do (<- (signalled entry ProcessSignal.TERM))
          (<- (slog (.format "worker: hosted-orphan-stop id={} pid={} owner={} signal=TERM" entry.record.id entry.record.pid entry.record.owner)))
          (replace entry :term-ms now))
    (> (- now entry.term-ms) settings.stop-grace-ms)
      (do (<- (signalled entry ProcessSignal.KILL))
          (<- (slog (.format "worker: hosted-orphan-stop id={} pid={} owner={} signal=KILL" entry.record.id entry.record.pid entry.record.owner)))
          entry)
    True entry))


(defk cell-refreshed [settings cell]
  {:pre [(: settings HostedSettings) (: cell HostedCell)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "入れ物の表を置き場の根と process の今に合わせるため(覚え直しと終わりの観測)。効果の後に入れ物を読み直して合わせる(頭の註)。"
  (<- found (| dict str) (adopted settings (dict cell.entries)))
  (if (isinstance found str)
      (when (!= found cell.told)
        (setv cell.told found)
        (<- (slog (.format "worker: hosted-scan-failed detail={}" found) :level "warning")))
      (do (setv cell.told "")
          (for [#(id entry) (.items found)]
            (<- (slog (.format "worker: hosted-adopt id={} pid={} owner={} generation={}" id entry.record.pid entry.record.owner
                               entry.record.generation))))
          (setv cell.entries (| found cell.entries))))
  (<- changed dict (observed (tuple (.values cell.entries))))
  (setv cell.entries (dfor #(k v) (.items cell.entries) k (.get changed k v)))
  None)


(defk orphans-swept [settings cell world]
  {:pre [(: settings HostedSettings) (: cell HostedCell) (: world WorldView)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "拍の観測の後に、持ち主の居ない預かりの process を 1 段ずつ止めるため(頭の註の持ち主不在の止め)。宣言がまだ読めていない・退いた
   世代・途絶で絞った宣言・止まり始めた worker では何もしない。"
  (val desired cell.desired)
  (when (or (is desired None) desired.superseded (is-not desired.cut-off None) cell.stopping)
    (return None))
  (<- (cell-refreshed settings cell))
  (<- owners frozenset (hosted-owners desired.jobs world.processes))
  (<- orphans tuple (hosted-orphans (tuple (gfor e (.values cell.entries) e.record)) owners))
  (<- now int (now-epoch-ms))
  (for [id orphans]
    (val entry (.get cell.entries id))
    (when (is-not entry None)
      (<- kept (| HostedEntry None) (orphan-step settings entry now))
      (setv cell.entries (if (is kept None)
                             (dfor #(k v) (.items cell.entries) :if (!= k id) k v)
                             (| cell.entries {id kept})))))
  None)


(defhandler hosted-host [#^ HostedSettings settings #^ HostedCell cell]
  "預かりの process の宿(頭の註)。"
  {:tags {:context "worker" :role "protocol"}}
  ;; 引数に残す理由: 置き場と層は worker の process ごとの設定(main が作る)・入れ物は口の task と拍が分け合う表(頭の註)。
  (LaunchHosted [launch]
    (<- started (| HostedEntry HostedRefused) (launched settings launch))
    (if (isinstance started HostedRefused)
        (resume started)
        (do (setv cell.entries (| cell.entries {started.record.id started}))
            (<- view HostedProcess (view-of started))
            (resume view))))
  (StopHosted [id]
    (if (not (! (hosted-id? id)))
        (resume (HostedRefused :detail (.format "id の形でない: {!r}" id)))
        (do (<- (cell-refreshed settings cell))
            (val entry (.get cell.entries id))
            (if (is entry None)
                (resume (HostedRefused :detail (.format "知らない id: {}" id)))
                (do (<- ended HostedEntry (stopped-now settings entry))
                    (setv cell.entries (dfor #(k v) (.items cell.entries) :if (!= k id) k v))
                    (<- view HostedProcess (view-of ended))
                    (resume view))))))
  (ListHosted []
    (<- (cell-refreshed settings cell))
    (var views #())
    (for [id (sorted cell.entries)]
      (<- view HostedProcess (view-of (get cell.entries id)))
      (:= views (+ views #(view))))
    (resume views))
  (ReadDesired [env-report stopping]
    ;; 拍の宣言の読みを外側へ渡し、答えを持ち主の判じに覚える(読めない回は前の宣言のまま — 途絶の間に止めない)。
    (<- desired (| DesiredJobs DesiredUnreadable) effect)
    (setv cell.stopping stopping)
    (when (isinstance desired DesiredJobs)
      (setv cell.desired desired))
    (resume desired))
  (ObserveWorld []
    (<- world WorldView effect)
    (<- (orphans-swept settings cell world))
    (resume world)))
