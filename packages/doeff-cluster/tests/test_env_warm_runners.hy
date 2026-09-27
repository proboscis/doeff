;; 模擬の担い手(detached-local)の温める表の行が、本物の coordinator の warm-view と同じく「行の needs(能力)に合い、生きていて drain 中で
;; ない担い手」だけを準備済みに数えること(2026-09-26)。前は担い手の名簿を見ずに常に ready = ("local") と答え、送り手の Ready の方針
;; (needs の組ごとに合う worker の 1 台以上で準備済み)を模擬で確かめられなかった — 能力の合わない組でも Ready になった。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_cluster.runtime_env_model [RuntimeEnv])
(import doeff_cluster.env_world [env-world EnvWorld])
(import doeff_cluster.detached [detached-local DetachedLocalStore])
(import doeff_cluster.detached_model [RunnerFact SimulateRunnerDrain])
(import doeff_cluster.warm_model [WarmRuntimeEnv ReadWarmState WarmState])
(import tests.env_fixtures [LOCK env-of base-world])

;; gpu-1 は gpu を専用の能力に持つ(gpu を要らない行を受けない)・cpu-1 は一般の担い手。
(val RUNNERS #((RunnerFact :name "gpu-1" :provides #("gpu" "net") :exclusive #("gpu") :live True :draining False)
               (RunnerFact :name "cpu-1" :provides #("net") :exclusive #() :live True :draining False)))


(defk warm-until-settled [store env needs]
  {:pre [(: store DetachedLocalStore) (: env RuntimeEnv) (: needs frozenset)] :post [(: % WarmState)]}
  "温めるよう頼み、準備が終わる(準備中が空になる)か 300 仮想秒まで読み直す。答え = 最後の行の姿。"
  (<- first WarmState ((detached-local store) (WarmRuntimeEnv env needs 600.0 "tests")))
  (var current first)
  (var waited 0)
  (while (and current.preparing (< waited 300))
    (<- (Delay 1.0))
    (<- again WarmState ((detached-local store) (ReadWarmState first.key)))
    (:= current again)
    (:= waited (+ waited 1)))
  current)


(defk scenario []
  {:pre [] :post [(: % bool)]}
  "合う担い手だけが準備済み・合う担い手が居ない組は温まらない・drain した担い手は数えない。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env :runners RUNNERS))
  (<- gpu WarmState (warm-until-settled store env (frozenset ["gpu"])))
  (assert (= gpu.ready #("gpu-1")) gpu)
  ;; 一般の行(net)は、net を提供していても専用の能力を持つ gpu-1 には数えない。
  (<- plain WarmState (warm-until-settled store env (frozenset ["net"])))
  (assert (= plain.ready #("cpu-1")) plain)
  ;; 合う担い手が居ない組: 準備中にも準備済みにもならない(Ready の方針は満たされない)。
  (<- none WarmState ((detached-local store) (WarmRuntimeEnv env (frozenset ["tpu"]) 600.0 "tests")))
  (assert (and (= none.ready #()) (= none.preparing #())) none)
  ;; drain した担い手は数えない。
  (<- ((detached-local store) (SimulateRunnerDrain "gpu-1")))
  (<- drained WarmState ((detached-local store) (ReadWarmState gpu.key)))
  (assert (= drained.ready #()) drained)
  True)


(deftest test-the-local-warm-table-counts-only-matching-live-undrained-runners
  (<- world EnvWorld (base-world))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) (with-handlers (env-world world) (scenario)))))
  (assert ok))
