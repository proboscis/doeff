;; 温める表の行が「行の needs(能力)に合い、生きていて drain 中でない担い手」だけを準備済みに数えること(2026-09-26)を、手元の runner
;; sim-cluster の上で確かめる: 筋書きの WarmRuntimeEnv / ReadWarmState は sim の送り手の口が本番の warm-cluster と同じ要求
;; (POST /warm・GET /warm/<キー>)で本物の coordinator へ送り、本物の coordinator が能力の合う worker の heartbeat の返事に行を配り、
;; 本物の run-worker が先読みの準備(PrepareEnv :warm)を撃ち、sim の宿が揃った root を heartbeat で名乗る(sim の準備は即座に揃う)。
;; 送り手の Ready の方針(needs の組ごとに合う worker の 1 台以上で準備済み)を、能力の合わない組・drain した担い手の反例で確かめる。
;; (2026-09-28 まで同じ VM の模擬 detached-local が自前の表で答えていた — 呼び手の外側の handler を継ぐので消した。)
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.sim.local [sim-cluster SimWorker DrainWorker PreparationsOf])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.intent.warm_model [ReadWarmState WarmState])
(import doeff_cluster.shared.core.warm_rules [warm-runtime-env])
(import tests.env_fixtures [LOCK env-of])

;; gpu-1 は gpu を専用の能力に持つ(gpu を要らない行を受けない)・cpu-1 は一般の担い手。
(val WORKERS #((SimWorker :name "gpu-1" :provides (frozenset ["gpu" "net"]) :exclusive (frozenset ["gpu"]) :task-reserve 0)
               (SimWorker :name "cpu-1" :provides (frozenset ["net"]) :task-reserve 0)))
(val NO-JOBS (system-of "warm-scenarios" #()))
;; 行を頼んでから準備済みと名乗られるまで待つ上限(仮想の秒 — 配る heartbeat・準備・名乗る heartbeat の数拍)。
(val SETTLE-SECONDS 20)


(defrecord Warmed
  "筋書きが読んだ温める表の行の姿: gpu の行・net の行・合う担い手の居ない tpu の行・drain の後の gpu の行。"
  (#^ WarmState gpu)
  (#^ WarmState plain)
  (#^ WarmState none)
  (#^ WarmState drained))


(defk warm-until-ready [env needs]
  {:pre [(: env RuntimeEnv) (: needs frozenset)] :post [(: % WarmState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "温めるよう頼み、1 台以上で準備済みになるか SETTLE-SECONDS 仮想秒まで 1 秒ごとに読み直すため。答え = 最後の行の姿。"
  (<- first WarmState (warm-runtime-env env needs 600.0 "tests"))
  (var current first)
  (var waited 0)
  (while (and (not current.ready) (< waited SETTLE-SECONDS))
    (<- (Delay 1.0))
    (<- again WarmState (ReadWarmState first.key))
    (:= current again)
    (:= waited (+ waited 1)))
  current)


(defk scenario []
  {:pre [] :post [(: % Warmed)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: gpu・net・tpu の行を温め、gpu-1 を drain してから gpu の行を読み直す。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- gpu WarmState (warm-until-ready env (frozenset ["gpu"])))
  (<- plain WarmState (warm-until-ready env (frozenset ["net"])))
  (<- none WarmState (warm-until-ready env (frozenset ["tpu"])))
  (<- asked dict (DrainWorker "gpu-1"))
  (assert (= (get asked "status") 200) asked)
  (<- drained WarmState (ReadWarmState gpu.key))
  (Warmed :gpu gpu :plain plain :none none :drained drained))


(deftest test-the-warm-table-counts-only-matching-live-undrained-runners
  (<- seen Warmed (sim-cluster NO-JOBS (scenario) :workers WORKERS))
  (assert (= seen.gpu.ready #("gpu-1")) seen.gpu)
  ;; 一般の行(net)は、net を提供していても専用の能力を持つ gpu-1 には数えない。
  (assert (= seen.plain.ready #("cpu-1")) seen.plain)
  ;; 合う担い手が居ない組: 準備中にも準備済みにもならない(Ready の方針は満たされない)。
  (assert (and (= seen.none.ready #()) (= seen.none.preparing #())) seen.none)
  ;; drain した担い手は数えない。
  (assert (= seen.drained.ready #()) seen.drained))


(defrecord DrainedWarm
  "drain の後に温めた新しい版の行の筋書きの読み(#3669): before / after = drain の前・drain の後に新しい版の行を温めて待った後に、gpu-1 が
   起こした先読みの準備の数・row = 新しい版の行の姿。"
  (#^ int before)
  (#^ int after)
  (#^ WarmState row))


(defk warm-a-newer-version-after-the-drain []
  {:pre [] :post [(: % DrainedWarm)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(#3669 — 2026-10-05 に drain 中の旧い worker が新しい版の準備を始めて捨てた形): gpu の行を温めて gpu-1 で準備済みにし、gpu-1 を
   drain してから、新しい版(app-2)の gpu の行を温めて待つ。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- (warm-until-ready env (frozenset ["gpu"])))
  (<- seen tuple (PreparationsOf "gpu-1"))
  (<- asked dict (DrainWorker "gpu-1"))
  (assert (= (get asked "status") 200) asked)
  (<- newer RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- row WarmState (warm-until-ready newer (frozenset ["gpu"])))
  (<- after tuple (PreparationsOf "gpu-1"))
  (DrainedWarm :before (len (lfor p seen :if p.warm p)) :after (len (lfor p after :if p.warm p)) :row row))


(deftest test-a-draining-runner-is-not-handed-a-newer-warm-row
  ;; 失敗ケース(#3669): drain 中の担い手には、温める表の行を heartbeat の返事で配らない(送り手の warm-view が drain 中の担い手を数えない
  ;; のと同じ判断)— 新しい版の行を温めても、gpu-1 は先読みの準備を起こさない。直す前は行が配られ、gpu-1 が準備を起こした(after が 1 増えた)。
  (<- seen DrainedWarm (sim-cluster NO-JOBS (warm-a-newer-version-after-the-drain) :workers WORKERS))
  (assert (= seen.before 1) seen)
  (assert (= seen.after seen.before) seen)
  (assert (= #(seen.row.ready seen.row.preparing) #(#() #())) seen.row))
