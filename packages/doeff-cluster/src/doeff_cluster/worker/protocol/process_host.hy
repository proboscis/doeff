;;; worker の job の子 process の言い換え(handlers.hy の ProcessHost を置き換えた・#2464)— StartJob・SignalJob・ReapJob・RetireJob・NoticeJob と
;;; 観測 ObserveProcesses を、汎用の子 process の効果(StartProcess・PollProcess・SignalProcess・StopProcess・WriteProcessInput・ReadEnvironment・
;;; ReadInterpreter)と file system の効果(MakeDirectory・RemoveTree・WriteText)へ言い換える。I/O を持たない — 本物は外側の
;;; subprocess-handler と os-file-handler、模擬は台本と memory の答え手。
;;;
;;; 子は shim の下で専用の process group に起こし(process-group)、標準入力の pipe をこの worker の答え手が握る(hold-stdin — worker が
;;; kill -9 で死ぬと pipe が閉じ、shim が job の group を止める)。終わりを回収する時に group の残りを止める(reap-group)。
;;; 同じ pipe は退きの知らせ(#3672)も運ぶ: RetireJob・NoticeJob が 1 行(retirement-line)を書き、shim が job の知らせの pipe へ中継する
;;; (job の中の答え手 = worker/entry/retirement_notices の pipe-retirement-notices)。
;;; 起こし方(命令の並び・cwd・子の環境変数)の判断は worker/core/launch の job-launch。
;;;
;;; process の世代の名(instance): <試行の番号>-<12 桁>。12 桁は worker の pid・起こした時刻・job の名・試行の番号の sha256 の頭
;;; (前の uuid4 の頭 12 桁と同じ形 — 乱数の効果を使わず、再起動した worker でも pid が違うので重ならない)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])
(import enum [StrEnum])  ; defenum の展開が名指す
(import hashlib)
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [MakeDirectory RemoveTree WriteText file-done])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment ReadInterpreter StartProcess PollProcess StopProcess SignalProcess
                                            ProcessSignal ProcessStarted ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild
                                            WriteProcessInput ProcessInputWritten ExitTarget])
(import doeff_cluster.worker.intent.worker_model [NoticeJob])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProcessView StartJob SignalJob ReapJob RetireJob StopStage StopReason SpecChanged
                                                 Undeclared HandoffAbandoned Retired CutOff WorkerStopping])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ProcessesWake HostWake])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch spec-program-file CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-deadline-ms])
(import doeff_core_effects.warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WarmRefused WarmRunning WarmExited WarmLost])
(import doeff_cluster.worker.core.warm_rules [WarmPlace warm-place])


(defrecord HostSettings
  "job の子 process の置き場と起こし方の設定(worker の組み立ての入口 main が作る): log-dir = 子の出力の file の dir・jobs-dir = 実行環境の
   job の作業 dir の親・program-dir = 詰めた Program の cache の dir・python = shim を起こす interpreter・hy-command = 木の job の hy・
   uv = 実行環境の job の uv・extra-env = 子へ渡す worker の文脈(名前・coordinator — 資格は渡さない)・layout = 業務の repo の木の形・
   program-env = 詰めた Program の file を子へ渡す環境変数の名(宿の契約 HOST-CONTRACT)・shim = shim の時間の内訳(worker の方針から
   shim_timing.shim-spans が導く — 子の shim の猶予と、回収の時に shim を待つ秒が同じ値を読む・#2940)。"
  (#^ str log-dir)
  (#^ str jobs-dir)
  (#^ str program-dir)
  (#^ str python)
  (#^ str hy-command)
  (#^ str uv)
  (#^ (get tuple #(EnvEntry ...)) extra-env)
  (#^ CodeLayout layout)
  (#^ str program-env)
  (#^ ShimSpans shim)
  ;; 待ちの子の置き場の根(#3646 — warm_rules.warm-dir-of・待ちの子を起こす宿 warm_host と同じ値)。task を分ける頼みの socket を root の
  ;; キーから導く(warm_rules.warm-place)。
  (#^ str warm-dir)
  ;; 退きの知らせの pipe の読み口の fd の番号を子へ渡す環境変数の名(宿の契約 HOST-CONTRACT の notice-env — #3672)。shim がこの名で
  ;; job へ渡し、worker が標準入力へ書いた行をその pipe へ中継する。
  (#^ str notice-env))


(defrecord ForkedFrom
  "待ちの子から分けて起こした子の印(#3646): start-ticks = 分かれた子 A の /proc の起動の刻(pid の使い回しを見分ける)・exit-path = A が
   終了コードを書く file。観測・止め・回収は pid とこの印で問う(汎用の子 process の効果ではなく、待ちの子の効果)。"
  (#^ int start-ticks)
  (#^ str exit-path))


(defrecord StopAsked
  "起こした子へ送った止めの合図の記録(#3713): requested-ms = 最初の合図(SignalJob)を送った刻(epoch ms)・killed = KILL まで送ったか
   (= 猶予を使い切った)・reason = 止める訳(最初の合図の訳 — KILL も同じ訳)。止めの計時の行が、合図から終わりまでの秒・猶予の使い切り・
   訳を名乗るため。"
  (#^ int requested-ms)
  (#^ bool killed)
  (#^ StopReason reason))


(defrecord Started
  "StartJob で起こした子: view = 観測・fork = 待ちの子から分けた子の印(入れ物 shim で起こした子は None)・stop = 止めの合図の記録
   (None = まだ止めていない — #3713)。"
  (#^ ProcessView view)
  (#^ (| ForkedFrom None) fork)
  (setv #^ (| StopAsked None) stop None))


;; 止めの計時の行(#3713)の段: TERM / KILL = 止めの合図を送った刻・REAPED = 終わった子を回収した刻。
(defenum StopMoment TERM KILL REAPED)
;; 止めの計時の行の名(他の計時の行と同じく、名 + 欄の形 — 本文は持たない)。
(val STOP-TIMING-LOG "worker: job の止めの計時")


(defk stop-reason-word [reason]
  {:pre [(: reason StopReason)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "止めの訳を計時の行の reason の語にするため(#3713 — 語の綴りはここ 1 か所)。"
  (match reason
    (SpecChanged) "spec-changed"
    (Undeclared) "undeclared"
    (HandoffAbandoned) "handoff-abandoned"
    (Retired) "retired"
    (CutOff) "cut-off"
    (WorkerStopping) "worker-stopping"))


;; 退きの知らせになる止めの訳(#3672 — retirement_model の AwaitRetirement の答えの値)。
(val RETIREMENT-NOTICES #((Retired) (HandoffAbandoned)))


(defk retirement-line [notice]
  {:pre [(: notice (| Retired HandoffAbandoned))] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "退きの知らせを、worker が shim の標準入力へ書く 1 行にするため(#3672 — 語は止めの訳の語 stop-reason-word と同じ綴り・行の終わりは改行。
   shim は行をそのまま job の知らせの pipe へ中継し、job の中の答え手 pipe-retirement-notices が retirement-of-word で読む)。"
  (<- word str (stop-reason-word notice))
  (+ word "\n"))


(defk retirement-of-word [word]
  {:pre [(: word str)] :post [(: % (| Retired HandoffAbandoned))] :tags {:context "worker" :role "protocol"}}
  "知らせの pipe の 1 行の語(改行を除いた物)を退きの知らせに読むため(retirement-line の逆 — 綴りは stop-reason-word の 1 か所)。知らない
   語は名指しで断る(ValueError — 黙って既定の知らせに倒れない)。"
  (var found None)
  (for [notice RETIREMENT-NOTICES]
    (<- spelled str (stop-reason-word notice))
    (when (= spelled word)
      (:= found notice)))
  (when (is found None)
    (raise (ValueError (.format "退きの知らせの語ではない: {!r}" word))))
  found)


(defk silent-ms-of [reason]
  {:pre [(: reason StopReason)] :post [(: % (| int None))] :tags {:context "worker" :role "protocol"}}
  "途絶で止めた時だけ、最後に届いた coordinator の返事からの ms を計時の行に載せるため(他の訳は None)。"
  (match reason
    (CutOff) reason.silent-ms
    _ None))


(defk noted-stop [moment name pid asked now-ms]
  {:pre [(: moment StopMoment) (: name str) (: pid int) (: asked StopAsked) (: now-ms int)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "job の止めの 1 刻を計時の行 1 つにするため(#3713 — 止めの合図から子の終わりまでの秒・KILL まで行ったか・止めた訳を worker の log で読む):
   stage = 段・job = job の名・pid = 子の pid・wall-ms = この刻(epoch ms)・elapsed-ms = 最初の止めの合図からの ms・killed = KILL を送ったか・
   reason = 止めた訳の語・silent-ms = 途絶で止めた時の、最後に届いた coordinator の返事からの ms(他の訳は None)。"
  (<- word str (stop-reason-word asked.reason))
  (<- silent (| int None) (silent-ms-of asked.reason))
  (<- (slog STOP-TIMING-LOG :level "info" :stage moment.value :job name :pid pid :wall-ms now-ms :elapsed-ms (- now-ms asked.requested-ms)
            :killed asked.killed :reason word :silent-ms silent))
  None)


(defk tell-retirement [started notice]
  {:pre [(: started Started) (: notice (| Retired HandoffAbandoned))] :post [(: % Started)] :tags {:context "worker" :role "protocol"}}
  "退きの知らせ notice を子へ送り、観測の notice に残した子を返すため(#3672): shim の下の子は shim の標準入力の pipe へ 1 行
   (retirement-line)を書く — shim がその行を job の知らせの pipe へ中継する。待ちの子から分けた子(task — 入れ替えの対象でない)は知らせの
   口を持たないので書かない。書けなかった(子が終わっていた)時も観測には残す — 終わりは ObserveProcesses が運ぶ。"
  (when (is started.fork None)
    (<- line str (retirement-line notice))
    (<- (WriteProcessInput :pid started.view.pid :text line)))
  (replace started :view (replace started.view :notice notice)))


(defk job-work-dir [settings name]
  {:pre [(: settings HostSettings) (: name str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "実行環境の job の子の cwd(job の名ごとの空の dir)を、起こす時と回収する時が同じ綴りで作るため。"
  (+ settings.jobs-dir "/" (.replace name "/" "_")))


(defk process-instance [attempt worker-pid started-ms name]
  {:pre [(: attempt int) (: worker-pid int) (: started-ms int) (: name str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "process の世代の名(頭の註)を、起こすたびに新しく・worker を起こし直しても重ならない形で振るため。"
  (val digest (.hexdigest (hashlib.sha256 (.encode (.format "{}:{}:{}:{}" worker-pid started-ms name attempt) "utf-8"))))
  (.format "{}-{}" attempt (cut digest 0 12)))


(defk start-job [settings action]
  {:pre [(: settings HostSettings) (: action StartJob)] :post [(: % Started)] :tags {:context "worker" :role "protocol" :spells "env"}}
  "StartJob を汎用の効果で答えるため: 出力の file の dir を作り、起こし方(job-launch)を決め、実行環境の job は使った印と空の作業 dir を
   作ってから起こす。道は判断が決めた欄のとおり(黙って別の道へ倒れない): warm-key の在る task は、その root の待ちの子へ頼んで分ける
   (ForkFromWarm — socket は root のキーから warm-place で導く・cwd と env と入口の後ろの引数は shim の道と同じ job-launch の値)、
   それ以外は shim の下の子を専用の group に起こす。起こした子(観測と、分けた子の印)を返す。起こせなければ OSError(前の Popen と同じ)。"
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
  ;; 子へ渡す Program の cache の file(task は task の行の版ごとの file — launch.spec-program-file・#3762)。
  (var program-path None)
  (when spec.program
    (<- cached Path (spec-program-file (Path settings.program-dir) spec))
    (:= program-path (str cached)))
  (<- plan JobLaunch (job-launch spec action.code-path instance action.attempt :python settings.python :hy-command settings.hy-command
                                 :uv settings.uv :extra-env (dfor e settings.extra-env e.name e.value) :layout settings.layout
                                 :allowed-env (dfor e allowed e.name e.value) :worker-pid facts.pid
                                 :program-path program-path
                                 :program-env settings.program-env :work-dir work :shim-grace-ms settings.shim.shim-grace-ms
                                 :notice-env settings.notice-env))
  (when plan.last-used
    ;; 使った印(掃除は最後に使った時刻の古い root から消す — env_upkeep.sweep-choice)。
    (<- (file-done (WriteText plan.last-used "" :replace True))))
  (when plan.work-dir
    (<- (RemoveTree plan.work-dir))  ; 無い dir の断りは捨てる(前の job の作業 dir が在れば消す)
    (<- (file-done (MakeDirectory plan.work-dir))))
  (val stem (.format "{}/{}.{}" settings.log-dir (.replace spec.name "/" "_") action.attempt))  ; task の名前は task/<id>
  (val log (+ stem ".log"))
  (var started None)
  (if (is-not action.warm-key None)
      (do (<- place WarmPlace (warm-place settings.warm-dir action.warm-key))
          (val exit-path (+ stem ".exit"))
          (<- forked (ForkFromWarm :socket-path place.socket :entry spec.entry :args plan.entry-args :cwd plan.cwd :env plan.env
                                   :log-path log :exit-path exit-path :grace-seconds (/ settings.shim.shim-grace-ms 1000)))
          (when (isinstance forked WarmRefused)
            (raise (OSError (+ "待ちの子が分けるのを断った: " forked.detail))))
          (:= started (Started :view (ProcessView spec.name spec action.attempt forked.pid started-ms :instance instance)
                               :fork (ForkedFrom :start-ticks forked.start-ticks :exit-path exit-path))))
      (do (<- answer (StartProcess :argv plan.argv :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path log :stderr-path log
                                   :process-group True :hold-stdin True :reap-group True))
          (when (isinstance answer ProcessNotStarted)
            (raise (OSError answer.detail)))
          (:= started (Started :view (ProcessView spec.name spec action.attempt answer.pid started-ms :instance instance) :fork None))))
  ;; 起こした job の記録(新しい版の job は worker を再起動せず、worker が展開した版の木の子 process で走ることを worker の記録で示すため):
  ;; job の名・版・木の path・子の pid・worker の pid・道(shim か待ちの子か)を 1 行。env と引数の値は書かない(資格を運びうる)。
  (<- (slog (.format "worker: job-start name={} revision={} tree={} pid={} worker-pid={} via={}"
                     spec.name spec.revision action.code-path started.view.pid facts.pid
                     (if (is action.warm-key None) "shim" (+ "warm:" action.warm-key)))))
  started)


(defhandler process-host [#^ HostSettings settings]
  ;; 引数に残す理由: 置き場の dir と起こし方は worker の process ごとの設定(main が引数から作る)。
  ;; 起こした子の表(job の名 → Started — 観測と、待ちの子から分けた子の印。終わりを観測した子の観測は exit-code を持つ)。
  (session var table {})
  (StartJob [spec attempt code-path warm-key]
    (when (in spec.name table) (raise (RuntimeError f"{spec.name} は既に動いています")))
    (<- begun Started (start-job settings (StartJob spec attempt code-path :warm-key warm-key)))
    (:= table (| table {spec.name begun}))
    (resume None))
  (SignalJob [name pid stage reason]
    ;; 孫 process まで届くよう process group へ送る(group で起こした子 — SignalProcess は立てた時の表で group へ送る)。待ちの子から
    ;; 分けた子は、分けた子 A(group の先頭)へ起動の刻を照らしてから送る(使い回された pid へ送らない)。
    (val started (.get table name))
    (val signal (if (= stage StopStage.TERM) ProcessSignal.TERM ProcessSignal.KILL))
    (if (and started (is-not started.fork None) (= started.view.pid pid))
        (<- (SignalWarmChild :pid pid :start-ticks started.fork.start-ticks :signal signal))
        (<- (SignalProcess :pid pid :signal signal)))
    ;; 止めの計時(#3713): 最初の合図の刻・KILL を送ったか・止めた訳を子の表に残し、合図ごとに 1 行。表に無い子(別の世代の pid)は
    ;; この合図の刻と訳から数える。
    (<- now int (now-epoch-ms))
    (val ours (and started (= started.view.pid pid)))
    (val killed (= stage StopStage.KILL))
    (val asked (if (and ours (is-not started.stop None))
                   (replace started.stop :killed (or started.stop.killed killed))
                   (StopAsked :requested-ms now :killed killed :reason reason)))
    (when ours
      (:= table (| table {name (replace started :stop asked)})))
    (<- (noted-stop (if killed StopMoment.KILL StopMoment.TERM) name pid asked now))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (val started (.get table name))
    (when (and started (= started.view.pid pid))
      ;; 終わりを観測していない子(止め切れていない)は、止めて回収する(group の残りも — reap-group)。観測した子は回収の時に
      ;; group の残りを止め、標準入力の pipe を閉じている。止めの合図から shim の期限(shim の猶予 + 掃除の余裕)まで待ってから
      ;; group へ KILL を送る — shim が job の子孫を片づけ終える前に shim を殺さない(#2940)。拍の判断(policy の plan-job)は終わりを
      ;; 観測した子にだけ ReapJob を出すので、本番の拍はこの枝を通らない(通るのは終わりを待たずに回収する呼び手だけ)。待ちの子から
      ;; 分けた子は、分けた子 A が shim と同じ見張りで子孫を片づけるので、group へ KILL を送るだけ(終わりは exit の file が残す)。
      (when (is started.view.exit-code None)
        (if (is-not started.fork None)
            (<- (SignalWarmChild :pid pid :start-ticks started.fork.start-ticks :signal ProcessSignal.KILL))
            (do (<- deadline int (shim-deadline-ms settings.shim))
                (<- (StopProcess :pid pid :stop-grace (/ deadline 1000))))))
      ;; 止めの計時(#3713): 止めの合図を送った子の回収(= 終わりを観測した後)を 1 行 — 合図からの ms・KILL まで行ったか・止めた訳。
      (when (is-not started.stop None)
        (<- now int (now-epoch-ms))
        (<- (noted-stop StopMoment.REAPED name pid started.stop now)))
      ;; 実行環境の job の作業 dir(worker が作った物だけ)は、終わった後に消す。
      (when started.view.spec.runtime-env
        (<- work str (job-work-dir settings name))
        (<- (RemoveTree work)))
      (:= table (dfor #(k v) (.items table) :if (!= k name) k v)))
    (resume None))
  (RetireJob [name pid new-name]
    ;; 入れ替え: 動いている process を止めずに名から外す(表の鍵と観測の名を new-name へ移す)。同じ名で新しい process を起こせる。
    ;; 外すと同時に子へ「退く」を知らせる(#3672 — 新の起動・新の Ready・旧の止めの合図のどれよりも前)。
    (val started (.get table name))
    (when (and started (= started.view.pid pid))
      (<- told Started (tell-retirement (replace started :view (replace started.view :name new-name :retired-from name)) (Retired)))
      (:= table (| (dfor #(k v) (.items table) :if (!= k name) k v) {new-name told})))
    (resume None))
  (NoticeJob [name pid notice]
    ;; 退いた子への知らせの変わり目(入れ替えの諦めの取り消し・諦めが解けた後の もう一度の退き — #3672)。
    (val started (.get table name))
    (when (and started (= started.view.pid pid))
      (<- told Started (tell-retirement started notice))
      (:= table (| table {name told})))
    (resume None))
  (ObserveProcesses []
    ;; 終わりを観測していない子だけを問う(終わりを答えた子は答え手が回収して忘れるので、ここに exit-code を残す)。待ちの子から分けた子は
    ;; 起動の刻と exit の file で問う(A の終わり = exit の file の値・印の無い終わりは -1)。
    (var seen {})
    (for [#(name started) (.items table)]
      (cond
        (is-not started.view.exit-code None) (setv (get seen name) started)
        (is-not started.fork None)
          (do (<- polled (PollWarmChild :pid started.view.pid :start-ticks started.fork.start-ticks :exit-path started.fork.exit-path))
              (setv (get seen name) (match polled
                                      (WarmExited) (replace started :view (replace started.view :exit-code polled.exit-code))
                                      (WarmLost) (replace started :view (replace started.view :exit-code -1))
                                      (WarmRunning) started)))
        True
          (do (<- polled (PollProcess started.view.pid))
              (setv (get seen name) (if (isinstance polled ProcessExited)
                                        (replace started :view (replace started.view :exit-code polled.exit-code))
                                        started)))))
    (:= table seen)
    (resume (tuple (gfor started (.values seen) started.view))))
  (ProcessesWake []
    ;; 終わりを観測していない子の終わりで拍の間の眠りを起こす(#3834 — 子の終わりを時間で起きて問わずに知る。問うのは起きた拍の
    ;; ObserveProcesses だけ)。待ちの子から分けた子はこの worker の子でないので、起動の刻で照らして見張る(使い回された pid を待たない)。
    (resume (HostWake :targets (tuple (gfor started (.values table) :if (is started.view.exit-code None)
                                            (ExitTarget :pid started.view.pid
                                                        :start-ticks (if (is started.fork None) None started.fork.start-ticks))))))))
