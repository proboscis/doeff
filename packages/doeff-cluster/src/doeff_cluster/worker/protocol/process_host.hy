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
;;;
;;; 実行環境の job の作業 dir(回収した job の物と、起こす前に残っていた前の job の物)は、拍の中では置き場(jobs-dir の下の
;;; SET-ASIDE-DIR)へ名を付け替えるだけにし、木の消し(RemoveTree)は拍の外の task が行う(card ki-2158e6d6e304 — 作業 dir は 14 万
;;; file・3.4 GiB に育ち、回収の拍が消しの終わりを 40〜250 秒待って、ほかの job の起動・止め・状態の報告を道連れに止めた)。消しの task は
;;; 同時に 1 つで、置き場の中身を全部消す(前の process が残した物も拾う)。走っている間に退けた物は、task の終わりの後の拍が次の task に渡す。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])
(import enum [StrEnum])  ; defenum の展開が名指す
(import hashlib)
(import pathlib [Path])
(import posixpath)
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [FileFailed ListDirectory MakeDirectory PathKind PathStat RemoveTree RenamePath StatPath WriteText
                                         file-done])
(import doeff_core_effects.scheduler [Spawn CreatePromise CompletePromise FailPromise Promise])
(import doeff_time [WaitWithin])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment ReadInterpreter StartProcess PollProcess StopProcess SignalProcess
                                            ProcessSignal ProcessStarted ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild
                                            WriteProcessInput ProcessInputWritten])
(import doeff_cluster.worker.intent.worker_model [NoticeJob])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProcessView StartJob JobStarted JobNotStarted SignalJob ReapJob RetireJob StopStage
                                                 StopReason SpecChanged Undeclared HandoffAbandoned Retired CutOff WorkerStopping])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveJobRoots])
(import doeff_cluster.worker.core.memory_rules [JobRoot])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.core.job_cgroup [placed-argv])
(import doeff_cluster.worker.intent.worker_model [WakeSet WorkerWakes])
(import doeff_cluster.worker.core.worker_due [wakes-with])
(import doeff_cluster.shared.intent.due_model [DueNever])
(import doeff_core_effects.process_effects [AwaitProcessExit])
(import doeff_core_effects.warm_effects [AwaitWarmChildExit])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch spec-program-file CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-deadline-ms])
(import doeff_core_effects.warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WriteWarmChildInput WarmRefused WarmRunning WarmExited
                                         WarmLost])
(import doeff_cluster.worker.core.warm_rules [WarmPlace warm-place])


(defrecord HostSettings
  "job の子 process の置き場と起こし方の設定(worker の組み立ての入口 main が作る): log-dir = 子の出力の file の dir(世代をまたいで分け合う)・
   generation = log-dir の中の名に入れる世代の印(worker/core/places の WorkerPlaces.generation — AL-D2j)・jobs-dir = 実行環境の
   job の作業 dir の親・program-dir = 詰めた Program の cache の dir・python = shim を起こす interpreter・hy-command = 木の job の hy・
   uv = 実行環境の job の uv・extra-env = 子へ渡す worker の文脈(名前・coordinator — 資格は渡さない)・layout = 業務の repo の木の形・
   program-env = 詰めた Program の file を子へ渡す環境変数の名(宿の契約 HOST-CONTRACT)・shim = shim の時間の内訳(worker の方針から
   shim_timing.shim-spans が導く — 子の shim の猶予と、回収の時に shim を待つ秒が同じ値を読む・#2940)。"
  (#^ str log-dir)
  (#^ str generation)
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
  (#^ str notice-env)
  ;; 常駐の job(JobSpec.once が偽)の shim を置く兄弟の cgroup の cgroup.procs(card ki-cd8cb84fdcd2 — worker/core/job_cgroup の
  ;; service の類・入口 main が DOEFF_WORKER_SERVICE_CGROUP から 1 度だけ決める)。None = 置き分けない(子は外の job-cgroup-placer の置き場か、
  ;; worker と同じ cgroup)。
  (setv #^ (| str None) service-procs None))


(defrecord ForkedFrom
  "待ちの子から分けて起こした子の印(#3646): start-ticks = 分かれた子 A の /proc の起動の刻(pid の使い回しを見分ける)・exit-path = A が
   終了コードを書く file・holds-input = A の入力の pipe を握って頼んだ(service — 退きの知らせをそこへ書く・card ki-de44efdb9aa4)。
   観測・止め・回収は pid とこの印で問う(汎用の子 process の効果ではなく、待ちの子の効果)。"
  (#^ int start-ticks)
  (#^ str exit-path)
  (setv #^ bool holds-input False))


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
;; 起こせなかった job の行の名(card ki-e1612a12d0c3 — 名 + 欄 job・via(shim か warm:<root のキー>)・detail の形)。
(val JOB-NOT-STARTED-LOG "worker: job を起こせなかった")
;; 退けた作業 dir の置き場の名(jobs-dir の下 — 名の付け替えが同じ file system の中の 1 手で済む。job の作業 dir の名は job-work-dir が
;; job の名から作り、. で始まらない)。
(val SET-ASIDE-DIR ".set-aside")
;; 置き場の消しの終わりの行の名(名 + 欄 removed = 消した物の数・failed = 消せなかった物の数・took-ms = task の起こしから終わりまでの ms)。
(val SET-ASIDE-CLEARED-LOG "worker: 退けた作業 dir の消しの終わり")
;; 作業 dir を置き場へ退けられず、拍の中で消した時の行の名(名 + 欄 path・detail)。
(val SET-ASIDE-FAILED-LOG "worker: 作業 dir を退けられず拍の中で消した")


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
   (retirement-line)を書く — shim がその行を job の知らせの pipe へ中継する。待ちの子から分けた service は、握った入力の pipe へ同じ行を
   書く — 分けた子 A が同じ部品で中継する(card ki-de44efdb9aa4)。分けた task(入れ替えの対象でない)は知らせの口を持たないので書かない。
   書けなかった(子が終わっていた)時も観測には残す — 終わりは ObserveProcesses が運ぶ。"
  (<- line str (retirement-line notice))
  (cond
    (is started.fork None) (<- (WriteProcessInput :pid started.view.pid :text line))
    started.fork.holds-input (<- (WriteWarmChildInput :pid started.view.pid :start-ticks started.fork.start-ticks :text line)))
  (replace started :view (replace started.view :notice notice)))


(defk job-work-dir [settings name]
  {:pre [(: settings HostSettings) (: name str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "実行環境の job の子の cwd(job の名ごとの空の dir)を、起こす時と回収する時が同じ綴りで作るため。"
  (+ settings.jobs-dir "/" (.replace name "/" "_")))


(defrecord SetAsideClearing
  "走っている置き場の消しの task(頭の註): done = task が消した物の数を渡す約束・started-ms = task を起こした刻(epoch ms)。"
  (#^ Promise done)
  (#^ int started-ms))


(defrecord SetAside
  "退けた作業 dir の置き場の記録(頭の註): clearing = 走っている(か最後に起こした)消しの task(None = まだ起こしていない)・
   unhanded = その task を起こした後に退けた物が置き場に在るか(在れば task の終わりの後の拍が次の task を起こす)・count = この process で
   退けた数(置き場の中の名を重ねない番号)。"
  (setv #^ (| SetAsideClearing None) clearing None)
  (setv #^ bool unhanded False)
  (setv #^ int count 0))


(defk set-aside-place [settings]
  {:pre [(: settings HostSettings)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "退けた作業 dir の置き場の path を、退ける時と消す時が同じ綴りで作るため。"
  (+ settings.jobs-dir "/" SET-ASIDE-DIR))


(defk set-aside-work-dir [settings work serial]
  {:pre [(: settings HostSettings) (: work str) (: serial int)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "作業 dir work を置き場へ退けるため(名の付け替え 1 回 — 木の消しは拍の外の task・頭の註)。serial = この process の中で退けた順の番号
   (同じ job の名を同じ ms に 2 度退けても名が重ならない)。答え = 置き場へ退けたか(作業 dir が無ければ False)。退けられなかった
   (付け替えの断り)時は、作業 dir を残さないよう拍の中で消し、その訳を 1 行出す(起こす job の cwd を空にする約束を守る)。"
  (<- seen (| PathStat FileFailed) (StatPath work :follow-symlinks False))
  (if (or (isinstance seen FileFailed) (= seen.kind PathKind.MISSING))
      False
      (do (<- place str (set-aside-place settings))
          (<- (file-done (MakeDirectory place)))
          (<- now int (now-epoch-ms))
          (<- moved (| None FileFailed) (RenamePath work (.format "{}/{}.{}.{}" place (posixpath.basename work) now serial)))
          (if (isinstance moved FileFailed)
              (do (<- (slog SET-ASIDE-FAILED-LOG :level "warning" :path work :detail moved.detail))
                  (<- (RemoveTree work))  ; 消せなかった断りは捨てる(前の形と同じ)
                  False)
              True))))


(defk clearing-set-aside [settings started-ms done]
  {:pre [(: settings HostSettings) (: started-ms int) (: done Promise)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "置き場の中身を全部消し(木の消しは本番の offloaded-tree-handler が thread で待つ)、消した物の数を done へ渡し、終わりの行を出すため
   (拍の外の task — 頭の註)。消せなかった物は数えて残す(次の task が消し直す)。失敗も done へ渡す(渡さないと消しが走り続けていると
   読まれ、次の task が起きない — 失敗は拍が答えを読んだ時に上がる)。"
  (try
    (<- place str (set-aside-place settings))
    (<- entries (| tuple FileFailed) (ListDirectory place))
    (var removed 0)
    (var failed 0)
    (when (not (isinstance entries FileFailed))
      (for [entry entries]
        (<- gone (| None FileFailed) (RemoveTree (posixpath.join place entry.name)))
        (if (isinstance gone FileFailed)
            (:= failed (+ failed 1))
            (:= removed (+ removed 1)))))
    (<- now int (now-epoch-ms))
    (<- (slog SET-ASIDE-CLEARED-LOG :level "info" :removed removed :failed failed :took-ms (- now started-ms)))
    (<- (CompletePromise done removed))
    (except [error Exception]
      (<- (FailPromise done error))))
  None)


(defk clearing-running [clearing]
  {:pre [(: clearing (| SetAsideClearing None))] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "置き場の消しの task が走っているかを、待たずに読むため(待つ秒 0 の WaitWithin — task が渡した失敗はここで上がる)。"
  (if (is clearing None)
      False
      (do (<- answer (WaitWithin clearing.done.future 0.0))
          (is answer None))))


(defk clearing-progressed [settings aside]
  {:pre [(: settings HostSettings) (: aside SetAside)] :post [(: % SetAside)] :tags {:context "worker" :role "protocol"}}
  "置き場の消しを進めるため: 走っている task に渡していない物(aside.unhanded)が在って task が走っていなければ、新しい task を起こす
   (拍は待たない — 頭の註)。答え = 次の記録。"
  (if (not aside.unhanded)
      aside
      (do (<- running bool (clearing-running aside.clearing))
          (if running
              aside
              (do (<- now int (now-epoch-ms))
                  (<- done Promise (CreatePromise))
                  (<- (Spawn (clearing-set-aside settings now done) :daemon True))
                  (replace aside :clearing (SetAsideClearing :done done :started-ms now) :unhanded False))))))


(defk work-dir-set-aside [settings work aside]
  {:pre [(: settings HostSettings) (: work str) (: aside SetAside)] :post [(: % SetAside)] :tags {:context "worker" :role "protocol"}}
  "作業 dir work を置き場へ退け、消しの task を進めるため(回収の時と、起こす前に前の job の作業 dir が残っていた時 — 頭の註)。
   答え = 次の記録(work が無ければ aside のまま)。"
  (<- moved bool (set-aside-work-dir settings work aside.count))
  (if moved
      (do (<- progressed SetAside (clearing-progressed settings (replace aside :count (+ aside.count 1) :unhanded True)))
          progressed)
      aside))


(defk job-log-stem [settings name attempt]
  {:pre [(: settings HostSettings) (: name str) (: attempt int)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "job の子の出力の file と、待ちの子から分けた子の exit の file の共通の頭(<log-dir>/<job の名>.<世代の印>.<試みの番号> — task の名前
   task/<id> の / は _)を、起こす時と読む時が同じ綴りで作るため。log-dir は世代をまたいで分け合い、試みの番号は worker の process ごとに
   1 から数えるので、世代の印が無いと 2 つの世代が同じ file に書き足す(AL-D2j)。"
  (.format "{}/{}.{}.{}" settings.log-dir (.replace name "/" "_") settings.generation attempt))


(defk process-instance [attempt worker-pid started-ms name]
  {:pre [(: attempt int) (: worker-pid int) (: started-ms int) (: name str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "process の世代の名(頭の註)を、起こすたびに新しく・worker を起こし直しても重ならない形で振るため。"
  (val digest (.hexdigest (hashlib.sha256 (.encode (.format "{}:{}:{}:{}" worker-pid started-ms name attempt) "utf-8"))))
  (.format "{}-{}" attempt (cut digest 0 12)))


(defk shim-argv [settings spec argv]
  {:pre [(: settings HostSettings) (: spec JobSpec) (: argv tuple)] :post [(: % tuple)] :tags {:context "worker" :role "protocol"}}
  "shim の argv を、常駐の job(once が偽)なら service の兄弟の cgroup へ移ってから起こす形に包むため(card ki-cd8cb84fdcd2 —
   worker/core/job_cgroup の service の類)。task と、置き分けない worker(service-procs が None)は argv のまま。外の job-cgroup-placer
   はこの後に全部の子を job の兄弟へ包むので、常駐の job の子は job の兄弟を経て service の兄弟に落ち着く(内の包みが後に走る)。"
  (when (or spec.once (is settings.service-procs None))
    (return argv))
  (<- placed tuple (placed-argv settings.service-procs argv))
  placed)


(defk start-job [settings action]
  {:pre [(: settings HostSettings) (: action StartJob)] :post [(: % (| Started JobNotStarted))] :tags {:context "worker" :role "protocol" :spells "env"}}
  "StartJob を汎用の効果で答えるため: 出力の file の dir を作り、起こし方(job-launch)を決め、実行環境の job は使った印と空の作業 dir を
   作ってから起こす。道は判断が決めた欄のとおり(黙って別の道へ倒れない): warm-key の在る task は、その root の待ちの子へ頼んで分ける
   (ForkFromWarm — socket は root のキーから warm-place で導く・cwd と入口の後ろの引数は shim の道と同じ job-launch の値・env は shim の
   道で uv run が足す root の venv まで含めた job-launch の entry-env — card ki-b884608b96e4)、
   それ以外は shim の下の子を専用の group に起こす。起こした子(観測と、分けた子の印)を返す。起こせなければ(待ちの子が分けるのを
   断った・shim を起こせない)その訳の JobNotStarted を返す(card ki-e1612a12d0c3 — 例外にすると worker の process ごと落ち、機体の上の
   job を全部道連れにした。断りの数えと起こし直しは判断の層 — policy の record-after・warm-child-actions)。"
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
  ;; 子へ渡す Program の cache の file(版ごとの file — service は宣言の行の版・task は task の行の版・launch.spec-program-file)。
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
  ;; 前の job の作業 dir は、呼び手(process-host の StartJob の節)が置き場へ退けた後なので、ここでは空の dir を作るだけ。
  (when plan.work-dir
    (<- (file-done (MakeDirectory plan.work-dir))))
  (<- stem str (job-log-stem settings spec.name action.attempt))
  (val log (+ stem ".log"))
  (var started None)
  (if (is-not action.warm-key None)
      (do (<- place WarmPlace (warm-place settings.warm-dir action.warm-key))
          (val exit-path (+ stem ".exit"))
          ;; 常駐の job(service — card ki-de44efdb9aa4)は入れ物 shim の 4 つの役を分けた子 A に揃える: 入力の pipe を握る(worker の
          ;; 消失の検知と退きの知らせ — shim の hold-stdin)・知らせの中継(shim の --notice-env)・service の兄弟の cgroup(shim-argv の
          ;; placed-argv と同じ cgroup.procs)。log の刻と孫の回収と終了コードは task と同じ A の見張りが持つ。
          (val service (not spec.once))
          (<- forked (ForkFromWarm :socket-path place.socket :entry spec.entry :args plan.entry-args :cwd plan.cwd :env plan.entry-env
                                   :log-path log :exit-path exit-path :grace-seconds (/ settings.shim.shim-grace-ms 1000)
                                   :hold-input service :notice-env (if service settings.notice-env None)
                                   :cgroup-procs (if service settings.service-procs None)))
          (:= started (if (isinstance forked WarmRefused)
                          (JobNotStarted :name spec.name :warm-key action.warm-key :detail (+ "待ちの子が分けるのを断った: " forked.detail)
                                         :at-ms started-ms)
                          (Started :view (ProcessView spec.name spec action.attempt forked.pid started-ms :instance instance)
                                   :fork (ForkedFrom :start-ticks forked.start-ticks :exit-path exit-path :holds-input service)))))
      (do (<- argv tuple (shim-argv settings spec plan.argv))
          (<- answer (StartProcess :argv argv :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path log :stderr-path log
                                   :process-group True :hold-stdin True :reap-group True))
          (:= started (if (isinstance answer ProcessNotStarted)
                          (JobNotStarted :name spec.name :warm-key None :detail (+ "子を起こせない: " answer.detail) :at-ms started-ms)
                          (Started :view (ProcessView spec.name spec action.attempt answer.pid started-ms :instance instance) :fork None)))))
  (val via (if (is action.warm-key None) "shim" (+ "warm:" action.warm-key)))
  (match started
    (JobNotStarted) (<- (slog JOB-NOT-STARTED-LOG :level "warning" :job spec.name :via via :detail started.detail))
    ;; 起こした job の記録(新しい版の job は worker を再起動せず、worker が展開した版の木の子 process で走ることを worker の記録で示すため):
    ;; job の名・版・木の path・子の pid・worker の pid・道(shim か待ちの子か)・子の出力の file を 1 行。env と引数の値は書かない(資格を運びうる)。
    (Started) (<- (slog (.format "worker: job-start name={} revision={} tree={} pid={} worker-pid={} via={} log={}"
                                 spec.name spec.revision action.code-path started.view.pid facts.pid via log))))
  started)


(defhandler process-host [#^ HostSettings settings]
  ;; 引数に残す理由: 置き場の dir と起こし方は worker の process ごとの設定(main が引数から作る)。
  ;; 起こした子の表(job の名 → Started — 観測と、待ちの子から分けた子の印。終わりを観測した子の観測は exit-code を持つ)。
  (session var table {})
  ;; 退けた作業 dir の置き場と、その消しの task の記録(頭の註)。
  (session var aside (SetAside))
  (StartJob [spec attempt code-path warm-key]
    (when (in spec.name table) (raise (RuntimeError f"{spec.name} は既に動いています")))
    ;; 実行環境の job は空の作業 dir で起こす — 前の job の作業 dir が残っていれば置き場へ退ける(消しは拍の外)。
    (when spec.runtime-env
      (<- work str (job-work-dir settings spec.name))
      (<- moved SetAside (work-dir-set-aside settings work aside))
      (:= aside moved))
    ;; 起こせなかった子は表に載せず、その答えを返す(判断がその job だけの失敗に数える — card ki-e1612a12d0c3)。
    (<- begun (| Started JobNotStarted) (start-job settings (StartJob spec attempt code-path :warm-key warm-key)))
    (if (isinstance begun JobNotStarted)
        (resume begun)
        (do (:= table (| table {spec.name begun}))
            (resume (JobStarted :name spec.name :pid begun.view.pid)))))
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
      ;; 実行環境の job の作業 dir(worker が作った物だけ)は、終わった後に置き場へ退け、拍の外の task が消す(頭の註 — 拍は消しの終わりを
      ;; 待たない)。
      (when started.view.spec.runtime-env
        (<- work str (job-work-dir settings name))
        (<- moved SetAside (work-dir-set-aside settings work aside))
        (:= aside moved))
      (:= table (dfor #(k v) (.items table) :if (!= k name) k v)))
    (resume None))
  (RetireJob [name pid new-name]
    ;; 入れ替え: 動いている process を止めずに名から外す(表の鍵と観測の名を new-name へ移す)。同じ名で新しい process を起こせる。
    ;; 外すと同時に子へ「退く」を知らせる(#3672 — 新の起動・新の Ready・旧の止めの合図のどれよりも前)。
    (val started (.get table name))
    (when (and started (= started.view.pid pid))
      ;; 退いた刻(寿命の上限 retired-ms を数える起点 — #4072 の D-2)。
      (<- retired-at int (now-epoch-ms))
      (<- told Started (tell-retirement (replace started :view (replace started.view :name new-name :retired-from name
                                                                         :retired-at-ms retired-at))
                                        (Retired)))
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
    ;; 消しの task が走っていた間に退けた物は、その task の終わりの後の観測で次の task に渡す(頭の註)。
    (<- progressed SetAside (clearing-progressed settings aside))
    (:= aside progressed)
    (resume (tuple (gfor started (.values seen) started.view))))
  (ObserveJobRoots []
    ;; 機体の memory の見張りの問い(card ki-7b934e2538f9): 終わりをまだ観測していない子の根を、表のまま答える(子を問い直さない)。
    (resume (tuple (gfor started (.values table) :if (is started.view.exit-code None)
                         (JobRoot :name started.view.name :pid started.view.pid)))))
  (WorkerWakes []
    ;; 周の間の待ちを起こす物(#3871 の単位 4): 終わりをまだ観測していない子の終わりを待つ効果を足す。待ちの子から分けた子は起動の刻を
    ;; 照らして待つ(pid の使い回しの終わりでは起きない)。
    (<- outer WakeSet effect)
    (val exits (tuple (gfor started (.values table)
                            :if (is started.view.exit-code None)
                            (if (is-not started.fork None)
                                (AwaitWarmChildExit :pid started.view.pid :start-ticks started.fork.start-ticks)
                                (AwaitProcessExit started.view.pid)))))
    ;; 走っている消しの task に渡していない物が置き場に在れば、その task の終わりでも起きる(次の観測が次の task を起こす)。
    (val bells (if (and aside.unhanded (is-not aside.clearing None)) #(aside.clearing.done.future) #()))
    (<- merged WakeSet (wakes-with outer (DueNever) bells exits))
    (resume merged)))
