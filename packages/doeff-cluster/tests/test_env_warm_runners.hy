;; 模擬の担い手(detached-local)の温める表の行が、本物の coordinator の warm-view と同じく「行の requires に合い、生きていて drain 中で
;; ない担い手」だけを準備済みに数えること(2026-09-26)。前は担い手の名簿を見ずに常に ready = ("local") と答え、送り手の Ready の方針
;; (requires の組ごとに合う worker の 1 台以上で準備済み)を模擬で確かめられなかった — label の合わない組でも Ready になった。
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects.handlers [state])
(import doeff_time [SimClock sim-time-handler Delay])
(import doeff_cluster.runtime_env_model [RuntimeEnv])
(import doeff_cluster.env_fake [fake-env FakeEnvWorld])
(import doeff_cluster.detached [detached-local DetachedLocalStore])
(import doeff_cluster.detached_model [RunnerFact SimulateRunnerDrain])
(import doeff_cluster.warm_model [WarmRuntimeEnv ReadWarmState WarmState])
(import doeff_cluster.cluster_model [Requirement])
(import tests.env_fixtures [LOCK env-of base-world])

(val RUNNERS #((RunnerFact :name "gpu-1" :labels #(#("role" "gpu")) :live True :draining False)
               (RunnerFact :name "cpu-1" :labels #(#("role" "cpu")) :live True :draining False)))


(defk warm-until-settled [store env requires]
  {:pre [(: store DetachedLocalStore) (: env RuntimeEnv) (: requires tuple)] :post [(: % WarmState)]}
  "温めるよう頼み、準備が終わる(準備中が空になる)か 300 仮想秒まで読み直す。答え = 最後の行の姿。"
  (<- first WarmState ((detached-local store) (WarmRuntimeEnv env requires 600.0 "tests")))
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
  (<- gpu WarmState (warm-until-settled store env #((Requirement "role" "gpu"))))
  (assert (= gpu.ready #("gpu-1")) gpu)
  ;; 合う担い手が居ない組: 準備中にも準備済みにもならない(Ready の方針は満たされない)。
  (<- none WarmState ((detached-local store) (WarmRuntimeEnv env #((Requirement "role" "tpu")) 600.0 "tests")))
  (assert (and (= none.ready #()) (= none.preparing #())) none)
  ;; drain した担い手は数えない。
  (<- ((detached-local store) (SimulateRunnerDrain "gpu-1")))
  (<- drained WarmState ((detached-local store) (ReadWarmState gpu.key)))
  (assert (= drained.ready #()) drained)
  True)


(deftest test-the-local-warm-table-counts-only-matching-live-undrained-runners
  (<- world FakeEnvWorld (base-world))
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) ((fake-env world) (scenario)))))
  (assert ok))
