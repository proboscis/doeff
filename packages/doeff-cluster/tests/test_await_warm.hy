;; 組みの完成を待つ効果 AwaitWarm(#3668 (b)・2026-10-06)を、手元の runner sim-cluster の上で確かめる: 筋書きの AwaitWarm は sim の送り手の口が
;; 本番の warm-cluster と同じ形(GET /warm/<キー> の読みと GET /watch の版の変化の待ち・同じ判断 warm_rules.warm-wait-answer)で本物の
;; coordinator へ問い、本物の run-worker が先読みの準備(PrepareEnv :warm)を撃ち、sim の宿が準備の進み(準備中・準備済み・失敗)を heartbeat で
;; 名乗る。worker の組みの進みは Worker の資源の行の status の env に載り、coordinator の版を進める — 待ちはそれで起きる(時間で起きて確かめない)。
;;   * 組み上がれば、その時に WarmReady で返る(期限まで待たない — env を Worker の行に載せないと版が進まず、期限まで起きない)
;;   * 組みが恒久の失敗で終われば、WarmFailed で返り、失敗の種類(kind)が読める
;;   * 期限まで組み上がらなければ、最後の読み(準備中)つきの WarmWaitExpired で返る
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import doeff_events [MemoryBroker])
(import dataclasses [dataclass])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure EnvFailureKind])
(import doeff_cluster.sim.local [sim-cluster SimWorker])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.intent.warm_model [AwaitWarm WarmState WarmReady WarmFailed WarmWaitExpired])
(import doeff_cluster.shared.intent.detached_model [ReadRunners AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.core.warm_rules [warm-runtime-env])
(import tests.env_fixtures [LOCK env-of])

(val NEEDS (frozenset ["gpu"]))
(val NO-JOBS (system-of "await-warm-scenarios" #()))
;; 組みにかかる仮想の秒と、待つ上限。組み上がる筋書きは上限の 1/4 で揃う(期限まで待てば赤と分かる差)。
(val PREPARE-SECONDS 30.0)
(val LIMIT-SECONDS 120.0)
(val SLOW-PREPARE-SECONDS 600.0)
;; 組みの完成から答えまでの許し(仮想の秒)。準備済みを名乗る heartbeat の数拍(測って約 2.5 秒)は入り、GET /watch の上限(WATCH-MAX-SECONDS
;; = 10 秒)で起きて読み直すだけの待ちは入らない — Worker の行に env を載せないと、測って約 11 秒遅れ(30 秒の組みで 41 秒)て赤になる。
(val READY-SLACK-SECONDS 5.0)
(val FAILURE (EnvFailure :kind EnvFailureKind.NATIVE-BUILD-FAILED :detail "compiler の誤り(模擬)" :retryable False))
;; 組みの子が cgroup の memory の上限で殺された worker の名乗り(worker の翻訳が signal 9 と oom_kill の増えから作る形 — test_env_prepare が持つ)。
(val MEMORY-FAILURE (EnvFailure :kind EnvFailureKind.MEMORY-KILLED :retryable False
                                :detail "native の build が signal 9 で終わり、cgroup の memory.events の oom_kill が 1 増えた(memory の上限で殺された)"))


(defrecord Awaited
  "筋書きの結果: answer = AwaitWarm の答え・waited = 頼んでから答えを受けるまでの仮想の秒。"
  (#^ object answer)
  (#^ float waited))


(defk worker-with [env-prepare-seconds env-failure]
  {:pre [(: env-prepare-seconds float) (: env-failure (| EnvFailure None))] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの担い手(gpu を専用の能力に持つ 1 台)を、組みの秒と失敗で作るため。"
  #((SimWorker :name "gpu-1" :provides (frozenset ["gpu" "net"]) :exclusive (frozenset ["gpu"]) :task-reserve 0
               :env-prepare-seconds env-prepare-seconds :env-failure env-failure)))


(defk warm-and-await []
  {:pre [] :post [(: % Awaited)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: gpu の行を温めるよう頼み、AwaitWarm で LIMIT-SECONDS を上限に組みの完成を待つ。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- asked int (now-epoch-ms))
  (<- first WarmState (warm-runtime-env env NEEDS 600.0 "tests"))
  (<- answer (AwaitWarm first.key LIMIT-SECONDS))
  (<- now int (now-epoch-ms))
  (Awaited :answer answer :waited (/ (- now asked) 1000.0)))


(deftest test-a-warm-row-that-finishes-answers-ready-when-it-finishes
  ;; 失敗ケース: Worker の行の status に env を載せないと、組みの完成で coordinator の版が進まず、待ちは期限(120 秒)まで起きない
  ;; (WarmWaitExpired)。載せれば準備の 30 秒の数拍後に WarmReady で返る。
  (<- seen Awaited (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (warm-and-await) :workers (! (worker-with PREPARE-SECONDS None))))
  (assert (isinstance seen.answer WarmReady) seen)
  (assert (= seen.answer.state.ready #("gpu-1")) seen)
  (assert (< seen.waited (+ PREPARE-SECONDS READY-SLACK-SECONDS)) seen))


(deftest test-a-warm-row-whose-preparation-fails-answers-failed-with-the-kind
  ;; 失敗ケース: 恒久の失敗を待ち続けず、WarmFailed で失敗した担い手と種類を返す(判断が失敗を数えなければ期限まで待って赤)。
  (<- seen Awaited (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (warm-and-await) :workers (! (worker-with PREPARE-SECONDS FAILURE))))
  (assert (isinstance seen.answer WarmFailed) seen)
  (assert (= (lfor f seen.answer.state.failed #(f.worker f.kind f.retryable))
             [#("gpu-1" EnvFailureKind.NATIVE-BUILD-FAILED.value False)])
          seen)
  (assert (< seen.waited (+ PREPARE-SECONDS READY-SLACK-SECONDS)) seen))


(deftest test-a-warm-row-killed-by-the-memory-limit-answers-failed-as-memory-killed
  ;; 組みの子が cgroup の memory の上限で殺された worker(memory-killed・恒久)は、待ち続けず WarmFailed で返り、種類 memory-killed が読める
  ;; — 回の Program は memory を読まずに「先の組みを取り消して止まりの中で宣言する形へ」を決められる(vg-w45・cisco-c8 の可 10-06 00:3x)。
  (<- seen Awaited (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (warm-and-await) :workers (! (worker-with PREPARE-SECONDS MEMORY-FAILURE))))
  (assert (isinstance seen.answer WarmFailed) seen)
  (assert (= (lfor f seen.answer.state.failed #(f.worker f.kind f.retryable))
             [#("gpu-1" EnvFailureKind.MEMORY-KILLED.value False)])
          seen)
  (assert (< seen.waited (+ PREPARE-SECONDS READY-SLACK-SECONDS)) seen))


;; 先の組みを入口で断った worker の名乗り(#3748 — worker の env-host が組みを始めずに置く形・恒久)。3 種のどれも同じ道を通る。
(val REFUSALS #((EnvFailure :kind EnvFailureKind.NO-DISK-ROOM :retryable False :detail "先の組みを断った: 空きが足りない(模擬)")
                (EnvFailure :kind EnvFailureKind.OVER-ROOTS-CAP :retryable False :detail "先の組みを断った: roots の上限(模擬)")
                (EnvFailure :kind EnvFailureKind.NO-MEMORY-ROOM :retryable False :detail "先の組みを断った: memory が足りない(模擬)")))


(deftest test-a-warm-row-refused-at-the-door-answers-failed-at-once-with-the-kind
  ;; 先の組みを入口で断った worker(no-disk-room・over-roots-cap・no-memory-room — 恒久)は、待ち続けず WarmFailed で返り種類が読める。
  ;; 断りは組みを始めないので、組みの秒(30 秒)を待たずに数拍で返る — 種類を一時(retryable)に数えれば期限(120 秒)まで待って赤。
  (for [refusal REFUSALS]
    (<- seen Awaited (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (warm-and-await) :workers (! (worker-with 0.0 refusal))))
    (assert (isinstance seen.answer WarmFailed) seen)
    (assert (= (lfor f seen.answer.state.failed #(f.worker f.kind f.retryable)) [#("gpu-1" refusal.kind.value False)]) seen)
    (assert (< seen.waited READY-SLACK-SECONDS) seen)))


(defrecord AroundWarm
  "組み 1 回の前後の名簿の読み: before / after = ReadRunners の答え・moves = その間に coordinator の版が進んだ回数。"
  (#^ object before)
  (#^ object after)
  (#^ int moves))


(defk runners-around-a-warm []
  {:pre [] :post [(: % AroundWarm)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 組み 1 回(頼む → AwaitWarm)の前後で、名簿の読み手が読む名簿(ReadRunners)と coordinator の版を読むため。"
  (<- before (ReadRunners))
  (<- start RunnersChange (AwaitRunnersChange 0 0.0))
  (<- (warm-and-await))
  (<- end RunnersChange (AwaitRunnersChange 0 0.0))
  (<- after (ReadRunners))
  (AroundWarm :before before :after after :moves (- end.revision start.revision)))


(deftest test-a-warm-moves-the-revision-but-not-the-roster-facts
  ;; #3668 (b) の費用の見張り(cisco-c8 の 1 点): env を Worker の行に載せると、名指さない版の待ち手(名簿を写す係 — 差だけを直す reconciler)
  ;; は組みの始まりと終わりで起きる。起きても名簿の事実(RunnerFact = 名・能力・生死・drain・task の空き)は変わらないので、係が直す差は 0
  ;; (読み直し 1 回だけ)。版が進むのは組み 1 回で、当たる worker ごとに 3 回まで — 行が当たった刻(Worker の行の status の warm に行の
  ;; 鍵が載り、その worker を名指した待ちを起こす — tests/test_watch.hy)・準備中・準備済み。同じ行の頼み直し(期限の延長)は版を進めない。
  (<- seen AroundWarm (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (runners-around-a-warm) :workers (! (worker-with PREPARE-SECONDS None))))
  (assert (= seen.before seen.after) seen)
  (assert (<= 1 seen.moves 3) seen))


(deftest test-a-warm-row-that-does-not-finish-in-time-answers-expired-with-the-last-read
  ;; 期限まで組み上がらない行は、最後の読み(準備中の担い手)つきの WarmWaitExpired で返る — 期限より前には返らない。
  (<- seen Awaited (sim-cluster :notice-broker (MemoryBroker) :timing (ClusterTiming) NO-JOBS (warm-and-await) :workers (! (worker-with SLOW-PREPARE-SECONDS None))))
  (assert (isinstance seen.answer WarmWaitExpired) seen)
  (assert (isinstance seen.answer.last WarmState) seen)
  (assert (= seen.answer.last.preparing #("gpu-1")) seen)
  (assert (>= seen.answer.waited-seconds LIMIT-SECONDS) seen)
  (assert (< seen.waited (+ LIMIT-SECONDS 15.0)) seen))
