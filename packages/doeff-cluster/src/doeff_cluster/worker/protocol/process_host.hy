;;; worker の job の子 process の言い換え(handlers.hy の ProcessHost を置き換えた・#2464)— StartJob・SignalJob・ReapJob・RetireJob と
;;; 観測 ObserveProcesses を、汎用の子 process の効果(StartProcess・PollProcess・SignalProcess・StopProcess・ReadEnvironment・
;;; ReadInterpreter)と file system の効果(MakeDirectory・RemoveTree・WriteText)へ言い換える。I/O を持たない — 本物は外側の
;;; subprocess-handler と os-file-handler、模擬は台本と memory の答え手。
;;;
;;; 子は shim の下で専用の process group に起こし(process-group)、標準入力の pipe をこの worker の答え手が握る(hold-stdin — worker が
;;; kill -9 で死ぬと pipe が閉じ、shim が job の group を止める)。終わりを回収する時に group の残りを止める(reap-group)。
;;; 起こし方(命令の並び・cwd・子の環境変数)の判断は worker/core/launch の job-launch。
;;;
;;; process の世代の名(instance): <試行の番号>-<12 桁>。12 桁は worker の pid・起こした時刻・job の名・試行の番号の sha256 の頭
;;; (前の uuid4 の頭 12 桁と同じ形 — 乱数の効果を使わず、再起動した worker でも pid が違うので重ならない)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import dataclasses [dataclass replace])
(import hashlib)
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [MakeDirectory RemoveTree WriteText file-done])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment ReadInterpreter StartProcess PollProcess StopProcess SignalProcess
                                            ProcessSignal ProcessStarted ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProcessView StartJob SignalJob ReapJob RetireJob StopStage])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch program-file CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])


(defrecord HostSettings
  "job の子 process の置き場と起こし方の設定(worker の組み立ての入口 main が作る): log-dir = 子の出力の file の dir・jobs-dir = 実行環境の
   job の作業 dir の親・program-dir = 詰めた Program の cache の dir・python = shim を起こす interpreter・hy-command = 木の job の hy・
   uv = 実行環境の job の uv・extra-env = 子へ渡す worker の文脈(名前・coordinator — 資格は渡さない)・layout = 業務の repo の木の形・
   program-env = 詰めた Program の file を子へ渡す環境変数の名(宿の契約 HOST-CONTRACT)。"
  (#^ str log-dir)
  (#^ str jobs-dir)
  (#^ str program-dir)
  (#^ str python)
  (#^ str hy-command)
  (#^ str uv)
  (#^ (get tuple #(EnvEntry ...)) extra-env)
  (#^ CodeLayout layout)
  (#^ str program-env))


(defk job-work-dir [settings name]
  {:pre [(: settings HostSettings) (: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "実行環境の job の子の cwd(job の名ごとの空の dir)を、起こす時と回収する時が同じ綴りで作るため。"
  (+ settings.jobs-dir "/" (.replace name "/" "_")))


(defk process-instance [attempt worker-pid started-ms name]
  {:pre [(: attempt int) (: worker-pid int) (: started-ms int) (: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "process の世代の名(頭の註)を、起こすたびに新しく・worker を起こし直しても重ならない形で振るため。"
  (val digest (.hexdigest (hashlib.sha256 (.encode (.format "{}:{}:{}:{}" worker-pid started-ms name attempt) "utf-8"))))
  (.format "{}-{}" attempt (cut digest 0 12)))


(defk start-job [settings action]
  {:pre [(: settings HostSettings) (: action StartJob)] :post [(: % ProcessView)] :tags {:context "doeff-cluster" :role "protocol"}}
  "StartJob を汎用の効果で答えるため: 出力の file の dir を作り、起こし方(job-launch)を決め、実行環境の job は使った印と空の作業 dir を
   作ってから、shim の下の子を専用の group に起こす。起こした子の観測(ProcessView)を返す。起こせなければ OSError(前の Popen と同じ)。"
  (val spec action.spec)
  (<- (file-done (MakeDirectory settings.log-dir)))
  (<- facts (ReadInterpreter))
  (<- started-ms int (now-epoch-ms))
  (<- instance str (process-instance action.attempt facts.pid started-ms spec.name))
  ;; 実行環境の job だけが worker の環境の許可表の名と LC_* を継ぐ(木の job は StartProcess の EXTEND で worker の環境を全部継ぐ)。
  (var allowed #())
  (when spec.runtime-env
    (<- read tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
    (:= allowed read))
  (<- work str (job-work-dir settings spec.name))
  (<- plan JobLaunch (job-launch spec action.code-path instance action.attempt :python settings.python :hy-command settings.hy-command
                                 :uv settings.uv :extra-env (dfor e settings.extra-env e.name e.value) :layout settings.layout
                                 :allowed-env (dfor e allowed e.name e.value) :worker-pid facts.pid
                                 :program-path (if spec.program (str (program-file (Path settings.program-dir) spec.program)) None)
                                 :program-env settings.program-env :work-dir work))
  (when plan.last-used
    ;; 使った印(掃除は最後に使った時刻の古い root から消す — env_upkeep.sweep-choice)。
    (<- (file-done (WriteText plan.last-used "" :replace True))))
  (when plan.work-dir
    (<- (RemoveTree plan.work-dir))  ; 無い dir の断りは捨てる(前の job の作業 dir が在れば消す)
    (<- (file-done (MakeDirectory plan.work-dir))))
  (val log (.format "{}/{}.{}.log" settings.log-dir (.replace spec.name "/" "_") action.attempt))  ; task の名前は task/<id>
  (<- answer (StartProcess :argv plan.argv :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path log :stderr-path log
                           :process-group True :hold-stdin True :reap-group True))
  (when (isinstance answer ProcessNotStarted)
    (raise (OSError answer.detail)))
  ;; 起こした job の記録(新しい版の job は worker を再起動せず、worker が展開した版の木の子 process で走ることを worker の記録で示すため):
  ;; job の名・版・木の path・子の pid・worker の pid を 1 行。env と引数の値は書かない(資格を運びうる)。
  (<- (slog (.format "worker: job-start name={} revision={} tree={} pid={} worker-pid={}"
                     spec.name spec.revision action.code-path answer.pid facts.pid)))
  (ProcessView spec.name spec action.attempt answer.pid started-ms :instance instance))


(defhandler process-host [#^ HostSettings settings]
  ;; 引数に残す理由: 置き場の dir と起こし方は worker の process ごとの設定(main が引数から作る)。
  ;; 起こした子の表(job の名 → ProcessView — 終わりを観測した子は exit-code を持つ)。
  (session var table {})
  (StartJob [spec attempt code-path]
    (when (in spec.name table) (raise (RuntimeError f"{spec.name} は既に動いています")))
    (<- view ProcessView (start-job settings (StartJob spec attempt code-path)))
    (:= table (| table {spec.name view}))
    (resume None))
  (SignalJob [name pid stage]
    ;; 孫 process まで届くよう process group へ送る(group で起こした子 — SignalProcess は立てた時の表で group へ送る)。
    (<- (SignalProcess :pid pid :signal (if (= stage StopStage.TERM) ProcessSignal.TERM ProcessSignal.KILL)))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (val view (.get table name))
    (when (and view (= view.pid pid))
      ;; 終わりを観測していない子(止め切れていない)は、止めて回収する(group の残りも — reap-group)。観測した子は回収の時に
      ;; group の残りを止め、標準入力の pipe を閉じている。
      (when (is view.exit-code None)
        (<- (StopProcess :pid pid :stop-grace 0.0)))
      ;; 実行環境の job の作業 dir(worker が作った物だけ)は、終わった後に消す。
      (when view.spec.runtime-env
        (<- work str (job-work-dir settings name))
        (<- (RemoveTree work)))
      (:= table (dfor #(k v) (.items table) :if (!= k name) k v)))
    (resume None))
  (RetireJob [name pid new-name]
    ;; 入れ替え: 動いている process を止めずに名から外す(表の鍵と観測の名を new-name へ移す)。同じ名で新しい process を起こせる。
    (val view (.get table name))
    (when (and view (= view.pid pid))
      (:= table (| (dfor #(k v) (.items table) :if (!= k name) k v)
                   {new-name (replace view :name new-name :retired-from name)})))
    (resume None))
  (ObserveProcesses []
    ;; 終わりを観測していない子だけを問う(終わりを答えた子は答え手が回収して忘れるので、ここに exit-code を残す)。
    (var seen {})
    (for [#(name view) (.items table)]
      (if (is-not view.exit-code None)
          (setv (get seen name) view)
          (do (<- polled (PollProcess view.pid))
              (setv (get seen name) (if (isinstance polled ProcessExited) (replace view :exit-code polled.exit-code) view)))))
    (:= table seen)
    (resume (tuple (.values seen)))))
