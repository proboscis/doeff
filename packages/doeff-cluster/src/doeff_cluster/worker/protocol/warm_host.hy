;;; worker の待ちの子の言い換え(#3646 の A4)— StartWarmChild・StopWarmChild・ForgetWarmChild と観測 ObserveWarmChildren を、汎用の子
;;; process の効果(StartProcess・PollProcess・SignalProcess・ReadEnvironment)と file system の効果(MakeDirectory・RemoveTree・ReadText)へ
;;; 言い換える。I/O を持たない — 本物は外側の subprocess-handler と os-file-handler、模擬の worker は sim/local の偽の宿が答える。
;;;
;;; 待ちの子(入口 worker/entry/warm_child — A2)は root ごとに 1 つ。root の venv の uv run で、許可表の環境変数だけを持って起こす(値と資格
;;; は持たない — task の env は分ける時に頼みに載る)。専用の process group に起こし(process-group)、標準入力の pipe をこの worker が握る
;;; (hold-stdin — worker が消えると待ちの子は終わる)。置き場(socket と準備完了の印)は root の外の <state-dir>/warm/<root のキー>(warm_rules の
;;; warm-place)で、起こす前に空にして作り直し、忘れる時に消す。準備完了の印を読むのは宿、準備済みに数えるかを判じるのは判断の層
;;; (warm_rules の warm-mark-clean — 条 WC3)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])
(import json)
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [FileFailed MakeDirectory RemoveTree ReadText file-done])
(import doeff_core_effects.process_effects [EnvMode ReadEnvironment StartProcess PollProcess SignalProcess ProcessSignal ProcessStarted
                                            ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [WarmChildMark WarmMarkUnreadable WarmChildView WarmLaunch StartWarmChild StopWarmChild
                                                  ForgetWarmChild StopProgress StopStage WakeSet WorkerWakes])
(import doeff_cluster.worker.core.worker_due [wakes-with])
(import doeff_cluster.shared.intent.due_model [DueNever])
(import doeff_core_effects.process_effects [AwaitProcessExit])
(import doeff_cluster.worker.protocol.observations [ObserveWarmChildren])
(import doeff_cluster.worker.core.launch [CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.warm_rules [WarmPlace WARM-CHILD-FLAGS warm-place warm-child-argv])


(defrecord WarmSettings
  "待ちの子の置き場と起こし方の設定(worker の組み立ての入口 main が作る): warm-dir = 待ちの子の置き場の根(warm_rules.warm-dir-of)・
   log-dir = 待ちの子の出力の file の dir(job の子と同じ dir — warm-<root のキー>.log)・uv = root の venv の uv。"
  (#^ str warm-dir)
  (#^ str log-dir)
  (#^ str uv))


(defk warm-log-path [settings key]
  {:pre [(: settings WarmSettings) (: key str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "待ちの子の出力の file を、起こす時と終わりの訳を読む時が同じ綴りで作るため。"
  (.format "{}/warm-{}.log" settings.log-dir key))


(defk ready-mark-of [text]
  {:pre [(: text str)] :post [(: % (| WarmChildMark WarmMarkUnreadable))] :tags {:context "worker" :role "protocol"}}
  "準備完了の印の file の中身(入口が書く JSON — threads と vmLive の欄)を、判断が判じる印の形に読むため。形が違えば読めない印にする
   (準備済みに数えず、判断が待ちの子を止める — 黙って起こし中のまま待たない)。"
  (try
    (val value (json.loads text))
    (val threads (.get value "threads"))
    (val vm-live (.get value "vmLive"))
    (if (and (isinstance threads int) (isinstance vm-live list) (all (gfor n vm-live (isinstance n int))))
        (WarmChildMark :threads threads :vm-live (tuple vm-live))
        (WarmMarkUnreadable :detail "threads と vmLive の欄が整数でない"))
    (except [error [ValueError AttributeError]]
      (WarmMarkUnreadable :detail (.format "JSON でない: {}" (. (type error) __name__))))))


(defk log-tail [settings key]
  {:pre [(: settings WarmSettings) (: key str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "終わった待ちの子の訳として、出力の file の最後の空でない 1 行(起動の断りは「warm_child: 起動を断る: …」の 1 行 — A2)を読むため。
   読めなければ空。"
  (<- text (ReadText (! (warm-log-path settings key))))
  (if (isinstance text FileFailed)
      ""
      (do (val lines (tuple (gfor line (.splitlines text) :if (.strip line) (.strip line))))
          (if lines (cut (get lines -1) 0 300) ""))))


(defk start-warm-child [settings key launch]
  {:pre [(: settings WarmSettings) (: key str) (: launch WarmLaunch)] :post [(: % WarmChildView)] :tags {:context "worker" :role "protocol" :spells "env"}}
  "StartWarmChild を汎用の効果で答えるため: 置き場を空にして作り直し(前の socket と印を残さない・持ち主だけ — 0700)、許可表の環境変数
   だけで root の venv の待ちの子を専用の group に起こす。起こせなければ、その刻に終わった観測にする(判断が間を置いて起こし直す)。"
  (<- place WarmPlace (warm-place settings.warm-dir key))
  (<- (RemoveTree place.dir))  ; 無い dir の断りは捨てる(前の待ちの子の置き場が在れば消す)
  (<- (file-done (MakeDirectory place.dir :mode 0o700)))
  (<- (file-done (MakeDirectory settings.log-dir)))
  (<- allowed tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
  (<- argv tuple (warm-child-argv settings.uv launch place))
  (<- log str (warm-log-path settings key))
  (<- now int (now-epoch-ms))
  (<- answer (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv argv :cwd launch.root :env allowed :env-mode EnvMode.REPLACE :stdout-path log :stderr-path log
                    :process-group WARM-CHILD-FLAGS.process-group :hold-stdin WARM-CHILD-FLAGS.hold-stdin
                    :reap-group WARM-CHILD-FLAGS.reap-group))
  (match answer
    (ProcessNotStarted) (WarmChildView :key key :pid 0 :started-ms now :exit-code -1 :ended-ms now
                                       :detail (+ "待ちの子を起こせない: " answer.detail))
    (ProcessStarted)
      (do (<- (slog (.format "worker: warm-child-start key={} root={} pid={} preload={}" key launch.root answer.pid (len launch.preload))))
          (WarmChildView :key key :pid answer.pid :started-ms now))))


(defk observed-warm-child [settings view]
  {:pre [(: settings WarmSettings) (: view WarmChildView)] :post [(: % WarmChildView)] :tags {:context "worker" :role "protocol"}}
  "待ちの子 1 つの今の観測を作るため: 終わりを観測した子はそのまま・走っている子は終わりを問い、終わっていれば訳(止めた訳か出力の
   最後の 1 行)を残し、走っていて印をまだ読んでいなければ準備完了の印の file を読む。"
  (cond
    (is-not view.exit-code None) view
    True
      (do (<- polled (| ProcessRunning ProcessExited ProcessNotChild) (PollProcess view.pid))
          (<- now int (now-epoch-ms))
          (match polled
            (ProcessExited)
              (do (<- tail str (log-tail settings view.key))
                  (replace view :exit-code polled.exit-code :ended-ms now :detail (or view.detail tail)))
            (ProcessNotChild) (replace view :exit-code -1 :ended-ms now :detail (or view.detail "待ちの子がこの worker の子でない"))
            (ProcessRunning)
              (if (is-not view.mark None)
                  view
                  (do (<- place WarmPlace (warm-place settings.warm-dir view.key))
                      (<- text (ReadText place.ready))
                      (if (isinstance text FileFailed)
                          view
                          (do (<- mark (| WarmChildMark WarmMarkUnreadable) (ready-mark-of text))
                              (replace view :mark mark)))))))))


(defhandler warm-host [#^ WarmSettings settings]
  ;; 引数に残す理由: 置き場の dir と起こし方は worker の process ごとの設定(main が引数から作る)。
  ;; 起こした待ちの子の表(root のキー → WarmChildView — 終わりを観測した子は exit-code を持つ)。
  (session var table {})
  (StartWarmChild [key launch]
    (<- started WarmChildView (start-warm-child settings key launch))
    (:= table (| table {key started}))
    (resume None))
  (StopWarmChild [key stage reason]
    ;; 待ちの子の group へだけ送る(分けた子 A は別の session — 走っている task に届かない・条 WC2)。止めた訳は観測の detail に残す。
    (val view (.get table key))
    (when (and view (is view.exit-code None))
      (<- (SignalProcess :pid view.pid :signal (if (= stage StopStage.TERM) ProcessSignal.TERM ProcessSignal.KILL)))
      (<- now int (now-epoch-ms))
      (:= table (| table {key (replace view :detail reason
                                       :stop (StopProgress (if (is view.stop None) now view.stop.requested-ms) stage now))})))
    (resume None))
  (ForgetWarmChild [key]
    ;; 終わった待ちの子を表から外し、置き場(socket と印)を消す。
    (when (in key table)
      (<- place WarmPlace (warm-place settings.warm-dir key))
      (<- (RemoveTree place.dir))
      (:= table (dfor #(k v) (.items table) :if (!= k key) k v)))
    (resume None))
  (ObserveWarmChildren []
    (var seen {})
    (for [#(key view) (.items table)]
      (<- observed WarmChildView (observed-warm-child settings view))
      (:= seen (| seen {key observed})))
    (:= table seen)
    (resume (tuple (.values seen))))
  (WorkerWakes []
    ;; 周の間の待ちを起こす物(#3871 の単位 4): 終わりをまだ観測していない待ちの子(exit-code が None — 起こせずに pid 0 で終わった物は
    ;; 除く)の終わりを待つ効果を足す。待ちの子は StartProcess で立てた子なので AwaitProcessExit。
    (<- outer WakeSet effect)
    (val exits (tuple (gfor view (.values table) :if (is view.exit-code None) (AwaitProcessExit view.pid))))
    (<- merged WakeSet (wakes-with outer (DueNever) #() exits))
    (resume merged)))
