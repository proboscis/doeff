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
(import doeff_cluster.sim.local [sim-cluster SimWorker DrainWorker])
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
