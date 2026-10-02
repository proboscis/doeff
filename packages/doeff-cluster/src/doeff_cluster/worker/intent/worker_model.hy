;;; doeff worker が管理する job の観測・記録・effect の型(判断の関数は worker/core/worker_rules — #2025 で分けた)。
;;;
;;; worker は「あるべき job の一覧」と「実際の子 process とコードの準備状況」を毎拍観測し、
;;; 差を埋める action を返す。action はそのまま effect として実行する。worker の記憶
;;; (JobRecord)は再起動で失われてよい一時的なもので、job の正本は宣言の側にある。
;;;
;;; job は 2 種類: service(once=False・終われば起動し直す常駐)と task(once=True・1 度だけ走らせて結果を返す)。
;;; job の宣言 JobSpec と段階 JobPhase は coordinator と共有の部品なので doeff_cluster.shared.intent.job_model、指紋 spec-hash は
;;; doeff_cluster.shared.core.job_rules に在る(#2025)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "intent"})
(import dataclasses [dataclass field])
(import enum [Enum])
(import doeff [EffectBase])
(import doeff_core_effects.scheduler [Future])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure])
(import doeff_cluster.shared.intent.job_model [JobSpec JobPhase])


;; 業務の repo の木の形(2026-09-25 — クラスタの仕組みを業務の repo から切り出した時に、木の形を worker の引数へ出した)。
;;   import-roots = 子 process の PYTHONPATH に並べる木の中の dir(前が先)。bytecode の準備(code_prepare)も同じ根で module 名を決める。
;;   base-paths = 土台(worker の実行環境)の側の import の路 — 木の根の**後ろ**に並べる機体の絶対 path(2026-09-26)。pod は土台の
;;                package を image の venv に焼くので空。host の worker(zeus)は共有の venv に入れない土台の package(例 制御面の SDK)を
;;                ここで宣言する。業務の code は常に木の根が先に勝つ(同じ名の module は task の版の物)。子の PYTHONPATH は木の根
;;                と base-paths だけで、worker の process の PYTHONPATH は継がない(宣言の外の路が黙って混ざらない)。
;; 定義点はここ 1 つ(worker のコードの木・入口の検め・子 process の言い換えが同じ値を読む。値は worker の composition root が引数から作る)。
(defclass [(dataclass :frozen True)] CodeLayout []
  (setv #^ tuple import-roots #("."))
  (setv #^ tuple base-paths #())

  (defn #^ None __post-init__ [self]
    (when (not self.import-roots)
      (raise (ValueError "import-roots は 1 つ以上")))
    (for [path self.base-paths]
      (when (or (not (.startswith path "/")) (in ":" path) (in "," path))
        (raise (ValueError (.format "土台の import の路は機体の絶対 path(`:` と `,` を含まない): {!r}" path)))))
    (for [root self.import-roots]
      (when (or (.startswith root "/") (in ".." (.split root "/")) (in ":" root) (in "," root))
        (raise (ValueError (.format "import の根は木の中の相対の dir: {!r}" root))))))

  (defn #^ str pythonpath [self #^ str tree]
    "木の中の import の根と土台の import の路を PYTHONPATH の形に(`.` は木そのもの・木の根が先)。"
    (.join ":" (+ (lfor root self.import-roots (if (= root ".") tree (+ tree "/" root))) (list self.base-paths))))

  (defn #^ str roots-arg [self]
    "code_prepare の --import-roots の値。"
    (.join "," self.import-roots)))


(defclass CodeState [Enum]
  (setv PREPARING "preparing" READY "ready" FAILED "failed"))


(defclass [(dataclass :frozen True)] CodeView []
  "revision ごとに展開したコードの観測。path は READY の時だけ在る。failed-ms = FAILED になった時刻(epoch ms)。"
  (#^ str revision)
  (#^ CodeState state)
  (setv #^ (| str None) path None)
  (setv #^ str detail "")
  (setv #^ (| int None) failed-ms None)
  ;; 実行環境の root の準備の失敗(env の job だけ)。kind と一時かを coordinator へ運ぶ(置き直しと答えの型)。
  (setv #^ (| EnvFailure None) failure None)

  (defn #^ None __post-init__ [self]
    ;; 木の置き場は READY の時だけ在る事を、作る時に確かめるため(ready-path がこの対応に頼る)。
    (when (!= (= self.state CodeState.READY) (is-not self.path None))
      (raise (ValueError (.format "path は READY の時だけ在る: {} {} path={!r}" self.revision self.state.value self.path))))))


(defclass [(dataclass :frozen True)] ProcessView []
  "worker が起動した子 process 1 本の観測。exit-code が None なら終了を観測していない。"
  (#^ str name)
  (#^ JobSpec spec)
  (#^ int attempt)
  (#^ int pid)
  (#^ int started-ms)
  (setv #^ (| int None) exit-code None)
  ;; process の世代の名(worker が起こすたびに新しく振る・再起動した worker でも重ならない)。子 process へ渡し、子の readiness と
  ;; 計器の報告に載る。coordinator は「担い手が running と報告している process の名」と一致する報告だけを数える。
  (setv #^ str instance "")
  ;; 入れ替え(handoff)で退いた process: 元の job の名。退いた process は名を「<元の名>#retired-<世代の名>」へ移して動かし続け、
  ;; 新しい process が Ready と数えられた後に止める。None = 退いていない。
  (setv #^ (| str None) retired-from None))


(defclass ProbeState [Enum]
  ;; QUEUED = 同じ木の検めの process が走っているので、その終わりを待っている(同じ木の検めは 1 本ずつ — 2026-09-27)。
  (setv QUEUED "queued" RUNNING "running" PASSED "passed" FAILED "failed"))


(defclass [(dataclass :frozen True)] ProbeView []
  "入口の検め(probe)1 回の観測(2026-09-25)。鍵 = 検めた job の spec-hash(版・入口・引数の指紋)。FAILED の detail は理由の 1 行、
   failed-ms = FAILED になった時刻(epoch ms — 撃ち直すまでの間を数える)。
   started-ms = 今の(RUNNING)または最後の検めの process を起こした時刻(epoch ms・まだ起こしていなければ None)・
   attempts = この spec を検めた回数(撃ち直すたびに 1 増える)・last-failure = 前の回の失敗の理由(撃ち直しの間も消さない — 2026-09-27)。"
  (#^ str spec-hash)
  (#^ ProbeState state)
  (setv #^ str detail "")
  (setv #^ (| int None) failed-ms None)
  (setv #^ (| int None) started-ms None)
  (setv #^ int attempts 1)
  (setv #^ str last-failure ""))


(defrecord ProbeStatus
  "状態の報告に載せる入口の検めの姿(2026-09-27): state = ProbeState の値・elapsed-seconds = 今の検めを起こしてからの秒
   (まだ起こしていなければ 0)・attempts = 回数・last-failure = 直前の失敗の理由(無ければ空)。"
  (#^ str state)
  (#^ int elapsed-seconds)
  (#^ int attempts)
  (#^ str last-failure))


(defclass [(dataclass :frozen True)] EnvDisk []
  "実行環境の root の置き場の disk の観測(2026-09-26): free = 空き(byte)・floor = 掃除を始める空きの下限・
   pinned = 掃除の係が今持っている固定の集合(root のキー env-<キー>)。worker の判断は固定の集合が変わった時と、空きが下限を切った
   時に SweepEnvs を撃つ。"
  (#^ int free)
  (#^ int floor)
  (#^ frozenset pinned))


(defclass [(dataclass :frozen True)] WarmEnv []
  "coordinator の温める表から受けた env 1 つ(heartbeat の返事の warm)。key = この worker の root のキー(env-<キー>)・
   runtime-env = 宣言の JSON の文字列。worker は job の準備より低い優先度で準備する(準備済みなら何もしない)。"
  (#^ str key)
  (#^ str runtime-env))


(defclass [(dataclass :frozen True)] WorldView []
  (#^ tuple codes)
  (#^ tuple processes)
  ;; 入口の検めの観測(ProbeView)。既定は空(検めを知らない呼び手の WorldView をそのまま通す)。
  (setv #^ tuple probes #())
  ;; 実行環境の root の置き場の disk(EnvDisk)。実行環境の job を扱わない worker は None(掃除をしない)。
  (setv #^ (| EnvDisk None) env-disk None))


(defclass StopStage [Enum]
  (setv TERM "term" KILL "kill"))


(defclass [(dataclass :frozen True)] StopProgress []
  (#^ int requested-ms)
  (#^ StopStage stage)
  (#^ int signalled-ms))


(defclass Outcome [Enum]
  ;; EXITED = 停止を求めていないのに終了した・STOPPED = 停止を求めて終了を確認した
  (setv EXITED "exited" STOPPED "stopped"))


(defclass [(dataclass :frozen True)] JobRecord []
  "worker の一時的な記憶。process の生死はここではなく観測(ProcessView)が持つ。"
  (#^ str name)
  (setv #^ int attempts 0)
  (setv #^ (| int None) last-exit-ms None)
  (setv #^ (| Outcome None) last-outcome None)
  (setv #^ (| int None) last-exit-code None)
  (setv #^ (| StopProgress None) stopping None)
  ;; 続けて exit code が 0 でなく終わった回数(失敗の数え方・状態の表示に使う)。exit code 0 の終わりは失敗ではないので 0 に戻す。
  (setv #^ int failures 0)
  ;; 最後に起動した時刻(十分長く動いた後の終了は数え直す)。
  (setv #^ (| int None) last-start-ms None)
  ;; 停止を求めずに続けて終わった回数(exit code を問わない — 起こし直しの間 backoff を伸ばす)。失敗の数え方とは別に持つ
  ;; (exit code 0 で終わってすぐ起こし直すサービスも、間を伸ばして起こし直しの連打を避ける)。
  (setv #^ int unexpected-exits 0))


(defclass [(dataclass :frozen True)] WorkerPolicy []
  (setv #^ int stop-grace-ms 10000)
  (setv #^ int kill-grace-ms 5000)
  ;; 予期せず終わった job を起こし直すまでの間。続けて落ちるたびに倍にし(k8s の CrashLoopBackOff と同じ形)、上限で止める。
  ;; stable-run-ms より長く動いてから終わった時は 1 回目として数え直す。
  (setv #^ int restart-backoff-ms 2000)
  (setv #^ int restart-backoff-max-ms 60000)
  (setv #^ int stable-run-ms 60000)
  ;; コードの準備に失敗した版を作り直すまでの間(失敗が続く版で git と焼きを毎拍撃たない)。
  (setv #^ int code-retry-ms 30000)
  ;; 拍と拍の間の眠りの上限。宣言の変化の呼び鈴(DesiredJobs.changed)が鳴れば、上限を待たずに次の拍へ進む(#2692)。
  (setv #^ float tick-seconds 0.5)
  ;; 呼び鈴で起きる時も、拍の終わりからこの秒は空ける(変化が途切れなく続いても拍は 1 秒に 1 / wake-gap-seconds 回まで — #2692)。
  (setv #^ float wake-gap-seconds 0.1))


(defclass [(dataclass :frozen True)] JobStatus []
  (#^ str name)
  (#^ JobPhase phase)
  (#^ (| str None) desired-revision)
  (#^ (| str None) running-revision)
  (#^ (| int None) pid)
  (#^ int attempts)
  (setv #^ str detail "")
  ;; 動いている process の世代(process が無ければ None): 起こした時に振った名・起こした spec の指紋・割り当ての世代。
  (setv #^ (| str None) instance None)
  (setv #^ (| str None) spec-hash None)
  (setv #^ (| int None) placement None)
  ;; 入れ替えで退いた process の行だけ: 元の job の名(coordinator は、その job がまだどこかで動いていると数える)。
  (setv #^ (| str None) retired-from None)
  ;; ENV-FAILED の行だけ: 準備の失敗の kind と一時か(coordinator が置き直すか・答えの型を決める)。
  (setv #^ (| EnvFailure None) failure None)
  ;; 宣言の spec の入口の検めが通っていない間(走っている・待っている・失敗した)だけ: その姿(入れ替えの途中で旧が動いている行も含む)。
  (setv #^ (| ProbeStatus None) probe None))


;; --- 宣言の読み取り -------------------------------------------------------------

(defclass [(dataclass :frozen True)] DesiredJobs []
  (#^ tuple jobs)
  ;; 温める env の列(WarmEnv — coordinator の温める表のうち、この worker の label に合う行)。宣言の file で動く worker は空。
  (setv #^ tuple warm #())
  ;; 宣言の変化の呼び鈴(#2692): この読みの後に宣言が変わった(名指しの待ちが「変わった」と答えた)時に満ちる Future。拍の間の眠りは
  ;; これと tick-seconds を競わせ、変化を次の拍の境まで待たない。None = 変化を知らせる口が無い(拍ごとに読む宿・待ちの口の無い
  ;; coordinator)— 眠りは tick-seconds。値の比べには入れない(同じ宣言は呼び鈴が違っても同じ)。
  (setv #^ (| Future None) changed (field :default None :compare False)))


(defclass [(dataclass :frozen True)] DesiredUnreadable []
  "宣言が読めない。空の宣言と取り違えて全 job を止めてはいけない。"
  (#^ str reason))


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ReadDesired [EffectBase]
  "結果は DesiredJobs | DesiredUnreadable。env-report = heartbeat に載せる root の姿(拍の Program が EnvReport で問うて渡す — None = 実行環境の
   root を名乗らない。#2427)。"
  (setv #^ (| dict None) env-report None))


(defclass [(dataclass :frozen True)] ObserveWorld [EffectBase]
  "結果は WorldView。終了した process も Reap されるまで観測に残る。")


(defclass [(dataclass :frozen True)] WorkerStopRequested [EffectBase]
  "結果は bool。worker 自身の停止要求(SIGTERM 等)を読む。")


(defclass [(dataclass :frozen True)] PublishStatus [EffectBase]
  (#^ tuple statuses)
  (setv #^ str note ""))


;; --- action(判断の結果。そのまま effect として実行する) -------------------------

(defclass [(dataclass :frozen True)] PrepareCode [EffectBase]
  "revision のコードを展開し始める。完了は ObserveWorld の CodeView で観測する。"
  (#^ str revision))


(defclass [(dataclass :frozen True)] PrepareEnv [EffectBase]
  "実行環境(runtime env)の root を準備し始める(key = \"env-<キー>\"・runtime-env = 宣言の JSON の文字列)。完了は ObserveWorld の
   CodeView(鍵 = key・READY の path = root)で観測する。worker のループは待たない。"
  (#^ str key)
  (#^ str runtime-env)
  ;; 先読み(温める表から)の準備か。先読みは job の準備より後に起こし、同時の準備の枠の 1 つを job に残し、期限は停滞だけ。
  (setv #^ bool warm False))


(defclass [(dataclass :frozen True)] SweepEnvs [EffectBase]
  "実行環境の root の掃除の係へ固定の集合(root のキー env-<キー> — 走っている job・宣言の job・準備中・温める表)を渡し、空きが
   下限を切っていれば掃除させる(消す root の選びは env_upkeep.sweep-choice)。disk を空けて次の準備を通すため。"
  (#^ frozenset pinned))


(defclass [(dataclass :frozen True)] StartJob [EffectBase]
  (#^ JobSpec spec)
  (#^ int attempt)
  (#^ str code-path))


(defclass [(dataclass :frozen True)] SignalJob [EffectBase]
  "process group 全体へ signal を送る。受理は終了の確認ではない。"
  (#^ str name)
  (#^ int pid)
  (#^ StopStage stage))


(defclass [(dataclass :frozen True)] ReapJob [EffectBase]
  "終了を観測した process を観測の表から外す。"
  (#^ str name)
  (#^ int pid)
  (#^ Outcome outcome)
  (#^ int exit-code))


(defclass [(dataclass :frozen True)] RetireJob [EffectBase]
  "動いている process を止めずに job の名から外す(名を new-name へ移す)。入れ替え(handoff)で新しい process を同じ名で並べて
   起こすため。退いた process は new-name の job として観測に残り、止めるのは方針の判断(新が Ready になった後)。"
  (#^ str name)
  (#^ int pid)
  (#^ str new-name))


(defclass [(dataclass :frozen True)] ProbeEntry [EffectBase]
  "spec の入口(factory と env)を、code-path の木と worker の実行環境で読み込めるかを試し始める(import と属性の在否だけ・呼ばない)。
   結果は ObserveWorld の ProbeView(鍵 = spec-hash)で観測する。"
  (#^ JobSpec spec)
  (#^ str code-path))


(defclass [(dataclass :frozen True)] EnvReport [EffectBase]
  "heartbeat で名乗る root の姿(準備済み・準備中・失敗のキーと disk の条件 — 形は worker/protocol/heartbeat の env-report)。
   拍の Program(worker/core/program の worker-tick)が毎拍 ReadDesired の前に問い、答えを ReadDesired の欄で coordinator への口へ渡す
   (#2467・#2427)。root を扱わない宿は None で答える。")


(defclass [(dataclass :frozen True)] ForgetProbes [EffectBase]
  "入口の検めの持ち主へ今の宣言の spec の指紋(spec-hash)の集合を渡し、集合に無い spec の検めの記録(答え・回数・前の回の失敗の
   理由・時間切れの印・待ち)を落とさせる(2026-09-27)。走っている検めの process は止めない(終わった後の答えを次の拍で落とす)。
   宣言から消えた spec の記録が worker の寿命の間ずっと増え続けないため。"
  (#^ frozenset keep))

(defclass [(dataclass :frozen True)] ReleaseLeases [EffectBase]
  "終了を確かめた process(job の名 job・世代の名 instance)が持っていた名前付きの lease を返す。process はもう書けないので、期限(TTL)を
   待たずに次の担い手が取れるようにする。届かなければ何もしない(期限で切れる)。job は子が名乗った名(起こした spec の名 — 退いた
   process も元の名)で、担い手の名は子の名乗りと同じ定義 lease_rules.lease-holder で作る。"
  (#^ str job)
  (#^ str instance))

(setv Action (| PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry ForgetProbes))


(defclass [(dataclass :frozen True)] WorkerState []
  (setv #^ tuple desired #())
  (setv #^ dict records (field :default-factory dict))
  ;; 最後に読めた温める env の列(宣言が読めない拍もこれを使い続ける — desired と同じ)。
  (setv #^ tuple warm #()))
