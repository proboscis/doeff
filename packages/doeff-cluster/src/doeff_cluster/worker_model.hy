;;; doeff worker が管理する job の宣言・観測・判断・effect。
;;;
;;; worker は「あるべき job の一覧」と「実際の子 process とコードの準備状況」を毎拍観測し、
;;; 差を埋める action を返す。action はそのまま effect として実行する。worker の記憶
;;; (JobRecord)は再起動で失われてよい一時的なもので、job の正本は宣言の側にある。
;;;
;;; job は 2 種類: service(once=False・終われば起動し直す常駐)と task(once=True・1 度だけ走らせて結果を返す)。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass field])
(import enum [Enum])
(import hashlib)
(import json)
(import doeff [EffectBase])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure])


(defclass [(dataclass :frozen True)] JobSpec []
  "job 1 本の宣言。entry は `hy -m` へ渡す module 名、revision は git の commit。once = 1 度だけ走らせる(task)。"
  (#^ str name)
  (#^ str entry)
  (#^ tuple args)
  (#^ str revision)
  (setv #^ bool once False)
  ;; 割り当ての世代(coordinator の Placement.generation)。process を起こした時の値を子 process へ渡し、readiness と計器の報告に
  ;; 載せる。比べない(compare=False): 世代だけが変わっても process を起こし直さない(起こし直すのは spec の中身が変わった時だけ)。
  (setv #^ (| int None) placement (field :default None :compare False))
  ;; 入れ替えの形(2026-09-24)。偽 = 旧を止めてから新を起こす(Recreate)。真 = 新を旧と並べて起こし、coordinator が新の process を
  ;; Ready と数えた(ready-instance がその世代の名になった)後に旧を止める(k8s の RollingUpdate の maxSurge 1・maxUnavailable 0)。
  ;; 名前付きの lease で書きを 1 つに絞る service だけが使う。どちらも比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ bool handoff (field :default False :compare False))
  (setv #^ (| str None) ready-instance (field :default None :compare False))
  ;; 入れ替えの諦め(2026-09-26)。coordinator が「新の世代が期限の間 Ready にならなかった」と記録した handoff の job(heartbeat の返事の
  ;; handoffAbandoned)。worker は新の process を止めて起こし直さず、退いた旧を動かし続ける(worker_policy.plan-job)。宣言が変われば
  ;; coordinator が記録を捨てて偽に戻る。比べない欄。
  (setv #^ bool handoff-abandoned (field :default False :compare False))
  ;; 切り離した task(2026-09-25・once と組)。coordinator との連絡が途絶えても止めない(担い手の heartbeat が lease を延ばし、途絶が
  ;; lease より長ければ coordinator が lost にして、再接続の返事から外れた時に止める — worker_policy.kept-when-cut-off)。比べない欄。
  (setv #^ bool detached (field :default False :compare False))
  ;; 実行環境の宣言(runtime_env_model の RuntimeEnv の JSON を正規化した文字列・2026-09-26)。在れば worker は木を展開せずに env の
  ;; root を準備し(PrepareEnv)、root の venv で子を起こす。比べる欄。
  (setv #^ (| str None) runtime-env None)
  ;; env のキー(この worker の platform で宣言から計算した値・"env-" を付けない)。root の置き場の鍵と子の DOEFF_RUNTIME_ENV_KEY に使う。
  ;; worker の中だけで決まる値なので比べない欄 — 版(revision)は宣言のまま運び、coordinator が同じ宣言から計算する版と指紋(spec-hash)
  ;; に合わせる(版を env のキーに置き換えると、coordinator は「版が違う」で env の service を Ready と数えない)。
  (setv #^ (| str None) env-key (field :default None :compare False))
  ;; Program の job(2026-09-27・ADR-DOE-CLUSTER-001・改訂 1 の F): program = 詰めた Program の置き場のキー(sha256 — worker は
  ;; coordinator の /programs/<sha> から取って子へ file で渡す)。比べない欄 — 同じ Program でも詰めた中身は揺れるので、入れ替えの要否は
  ;; args に載る identity の指紋で決める(改訂 1 の A)。
  (setv #^ (| str None) program (field :default None :compare False))
  ;; 子の環境変数(宣言の :environ・名の順の #(名 値) の tuple — 改訂 1 の G)。比べる欄(変われば入れ替える・spec-hash に入る)。
  (setv #^ tuple environ #())

  (defn #^ None __post-init__ [self]
    (when (or (not self.name) (not self.entry) (not self.revision))
      (raise (ValueError "job には name・entry・revision が必要です")))))


;; 業務の repo の木の形(2026-09-25 — クラスタの仕組みを業務の repo から切り出した時に、木の形を worker の引数へ出した)。
;;   import-roots = 子 process の PYTHONPATH に並べる木の中の dir(前が先)。bytecode の準備(code_prepare)も同じ根で module 名を決める。
;;   base-paths = 土台(worker の実行環境)の側の import の路 — 木の根の**後ろ**に並べる機体の絶対 path(2026-09-26)。pod は土台の
;;                package を image の venv に焼くので空。host の worker(zeus)は共有の venv に入れない土台の package(例 制御面の SDK)を
;;                ここで宣言する。業務の code は常に木の根が先に勝つ(同じ名の module は task の版の物)。子の PYTHONPATH は木の根
;;                と base-paths だけで、worker の process の PYTHONPATH は継がない(宣言の外の路が黙って混ざらない)。
;; 定義点はここ 1 つ(worker の CodeStore・ProbeStore・ProcessHost が同じ値を読む。値は worker の composition root が引数から作る)。
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

(setv ENV-KEY-PREFIX "env-")

(defn #^ str code-key [#^ JobSpec spec]
  "展開する木の鍵(cache の dir の名前・完成の印の版)。revision そのもの(1 つの commit の木)。
   実行環境の job は \"env-<キー>\"(worker が宣言から計算した env-key)が root の鍵。"
  (if spec.runtime-env
      (+ ENV-KEY-PREFIX (or spec.env-key (raise (ValueError (+ "実行環境の job に env-key が無い: " spec.name)))))
      spec.revision))


(defn #^ str spec-hash [#^ JobSpec spec]
  "process を起こす形(name・entry・引数 = 設定を含む・版・once)の指紋。worker が起こした process の世代の一部として子へ渡し、
   coordinator は今の宣言から同じ関数で計算して比べる — 設定だけが変わっても指紋が変わり、前の process の報告は数えない。
   割り当ての世代(placement)・入れ替えの形(handoff・ready-instance)は含めない(比べない欄)。environ(子の環境変数)は在る時だけ
   足す。Program の job の詰めた中身(program)は含めない —
   Program の同一性は args の identity の指紋が運ぶ。定義点はこの 1 つ。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (+ [spec.name spec.entry (list spec.args) spec.revision spec.once]
                                                           (if spec.runtime-env [spec.runtime-env] [])
                                                           (if spec.environ [(lfor #(k v) spec.environ [k v])] []))
                                                        :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


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


(defn #^ (| str None) ready-path [#^ (| CodeView None) code]  ; defk にできない: 純粋な判断の start-actions(Program の外の関数)が呼ぶ
  "木が READY ならその path、観測が無い・READY でなければ None(CodeView が READY ⇔ path の在る事を作る時に確かめる)。"
  (if (is code None) None code.path))


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

(setv RETIRED-MARK "#retired-")

(defn #^ str retired-name [#^ str name #^ str instance]
  (+ name RETIRED-MARK instance))


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


(defn #^ bool probed-job [#^ JobSpec spec]
  "入口の検めの対象: service の job(args の先頭が \"service\" — job_entry の service 入口)。task(once)と素の entry は対象外。"
  (and (not spec.once) (> (len spec.args) 0) (= (get spec.args 0) "service")))


(defn #^ tuple probe-args [#^ JobSpec spec]
  "検めの対象の job の入口を検める引数(spec.entry の probe 口へ渡す)。Program の job(2026-09-27)は入口の module を import できるか
   だけを検める — 詰めた Program の版と復元は起こした子が検め、理由つきで落ちる(job_entry.read-program)。"
  #("probe"))

;; 旧い service の spec の引数(2026-09-27 より前の job_entry service の形 — 関数の参照 + handler の組の import path + 設定)。
(setv OLD-SERVICE-FLAGS #("--factory" "--env" "--config"))

(defn #^ (| str None) probe-refusal [#^ JobSpec spec]
  "検めの対象の spec を検める前に断る理由(断らなければ None)。Program の job の service は詰めた Program の置き場のキー(spec.program)を
   持ち、旧い引数(--factory・--env・--config)を持たない。旧い coordinator の返事の spec は入口の module の import だけなら通ってしまい、
   子の job_entry が argparse で落ちて起こし直しを繰り返すので、検めの段で理由つきに止める(計画 2.8 の入口 15)。"
  (setv old (lfor flag OLD-SERVICE-FLAGS :if (in flag spec.args) flag))
  (cond
    (not (probed-job spec)) None
    old (.format "旧い service の spec の引数 {} は受け付けない — Program の job(service --identity と詰めた Program)で宣言し直す"
                 (.join "・" old))
    (not spec.program) "service の spec に詰めた Program の置き場のキー(program)が無い — Program の job で宣言し直す"
    True None))


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
  (setv #^ float tick-seconds 0.5))


(defclass JobPhase [Enum]
  (setv PREPARING "preparing"
        CODE-FAILED "code-failed"
        STARTING "starting"            ; コードは揃い、次の拍で起動する
        PROBING "probing"              ; コードは揃い、入口の検め(probe)が走っている・同じ木の検めの終わりを待っている
        BACKOFF "backoff"              ; 予期せず終了した後の再起動待ち
        RUNNING "running"
        STOPPING "stopping"
        STOP-UNCONFIRMED "stop-unconfirmed"  ; KILL の後も終了を確認できない。置き換えは起動しない
        PROBE-FAILED "probe-failed"    ; 木は揃ったが、実行環境で入口の module を読み込めない(起動しない)
        ENV-FAILED "env-failed"        ; 実行環境(runtime env)の root を準備できない(子 process を起こしていない)
        HANDOFF-ABANDONED "handoff-abandoned"  ; 入れ替えを諦めた(新は起こさない・退いた旧が動いている — 宣言が変わるまで)
        FINISHED "finished"            ; task が終わった(起動し直さない)
        STOPPED "stopped"))


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
  (setv #^ tuple warm #()))


(defclass [(dataclass :frozen True)] DesiredUnreadable []
  "宣言が読めない。空の宣言と取り違えて全 job を止めてはいけない。"
  (#^ str reason))


;; --- effect ----------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ReadDesired [EffectBase]
  "結果は DesiredJobs | DesiredUnreadable。")


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

(defclass [(dataclass :frozen True)] ForgetProbes [EffectBase]
  "入口の検めの持ち主へ今の宣言の spec の指紋(spec-hash)の集合を渡し、集合に無い spec の検めの記録(答え・回数・前の回の失敗の
   理由・時間切れの印・待ち)を落とさせる(2026-09-27)。走っている検めの process は止めない(終わった後の答えを次の拍で落とす)。
   宣言から消えた spec の記録が worker の寿命の間ずっと増え続けないため。"
  (#^ frozenset keep))

(defclass [(dataclass :frozen True)] ReleaseLeases [EffectBase]
  "終了を確かめた process(job の名 job・世代の名 instance)が持っていた名前付きの lease を返す。process はもう書けないので、期限(TTL)を
   待たずに次の担い手が取れるようにする。届かなければ何もしない(期限で切れる)。job は子が名乗った名(起こした spec の名 — 退いた
   process も元の名)で、担い手の名は子の名乗りと同じ定義 semaphore_model.lease-holder で作る。"
  (#^ str job)
  (#^ str instance))

(setv Action (| PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry ForgetProbes))


(defclass [(dataclass :frozen True)] WorkerState []
  (setv #^ tuple desired #())
  (setv #^ dict records (field :default-factory dict))
  ;; 最後に読めた温める env の列(宣言が読めない拍もこれを使い続ける — desired と同じ)。
  (setv #^ tuple warm #()))
