;;; AwaitReadiness の「落ちた」(ServiceFailed — #3668 の (a)・cisco-c8 の可 2026-10-05 23:2x)の失敗ケース。
;;;
;;; 回の Program は段ごとに準備完了を待つ。担い手が落ちても期限まで待つと、回が段の上限(job ごとの分の単位)まで
;;; 止まって見えない。ここでは本物の coordinator と worker の模擬(sim-cluster)で、
;;;   - 担い手の実行環境の準備が恒久の失敗で終わった Service を待つと、期限の前に ServiceFailed(phase・failure-kind つき)が返る
;;;     (直す前は期限まで待って ReadinessWaitExpired)。
;;;   - Crash の後に起こし直す途中(backoff)を通る Service を待つと、Ready が返る(早まって「落ちた」と答えない)。
;;; 判断は本番の境界の handler と同じ 1 つ(shared/protocol/coordinator_reads.hy の readiness-wait-answer)を通る。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import doeff_events [MemoryBroker])
(import dataclasses [dataclass])
(import doeff_cluster.sim.local [sim-cluster SimWorker])
(import doeff_cluster.shared.intent.cluster_control [AwaitReadiness ServiceReadiness ServiceFailed ReadinessWaitExpired
                                                     AwaitJobProcess JobProcessSeen JobProcessWaitExpired Crash])
(import doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure EnvFailureKind])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])
(import tests.env_fixtures [LOCK env-of])


;; beacons の service が求める能力(fixtures/sim_programs.hy の :needs)。
(val NEEDS (frozenset #("cluster-net")))
;; 恒久の準備の失敗(worker は撃ち直さない — coordinator は版の判定を Blocked にする)。
(val MISSING (EnvFailure :kind EnvFailureKind.COMMIT-MISSING :detail "commit が remote に無い" :retryable False))
;; 待つ上限(回の Program の段の上限より長い — 期限で答えたなら、ここまで眠った事になる)。
(val PATIENCE 600.0)


(defrecord Waited
  "準備完了の待ち 1 回: answer = AwaitReadiness の答え・seconds = 頼んでから答えまでの仮想の秒。"
  (#^ (| ServiceReadiness ServiceFailed ReadinessWaitExpired) answer)
  (#^ float seconds))


(defk wait-ready [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % Waited)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: Service name が Ready になるのを seconds まで待ち、答えと待った秒を返すため(読み直しのループは書かない)。"
  (<- asked int (now-epoch-ms))
  (<- answer (| ServiceReadiness ServiceFailed ReadinessWaitExpired) (AwaitReadiness name "Ready" seconds))
  (<- answered int (now-epoch-ms))
  (Waited :answer answer :seconds (/ (- answered asked) 1000.0)))


(deftest test-a-service-whose-env-cannot-be-prepared-answers-failed-before-the-deadline
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- seen Waited (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (wait-ready "beacon" PATIENCE) :runtime-env env
                               :workers #((SimWorker :name "w1" :provides NEEDS :env-failure MISSING :task-reserve 0))))
  ;; 直す前は ReadinessWaitExpired(waited 600 秒)— 落ちた事が答えに無く、回は段の上限まで止まる。
  (assert (isinstance seen.answer ServiceFailed) seen.answer)
  (assert (= seen.answer.phase JobPhase.ENV-FAILED) seen.answer)
  (assert (= seen.answer.failure-kind EnvFailureKind.COMMIT-MISSING.value) seen.answer)
  (assert (= seen.answer.state "Ready") seen.answer)
  (assert (!= seen.answer.last.state "Ready") seen.answer)
  ;; 分かった時点で返る(coordinator が担い手の行を受けて版の判定を Blocked にした書きで起きる — 期限まで眠らない)。
  (assert (< seen.seconds 60.0) seen.seconds))


(defrecord Restarted
  "Crash の後の待ち: before = 落とす前の process・crashed = Crash の答え・again = その後の準備完了の待ち。"
  (#^ (| JobProcessSeen JobProcessWaitExpired) before)
  (#^ int crashed)
  (#^ Waited again))


(defk crash-then-wait [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % Restarted)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: Service name が Ready になってから process を落とし、起こし直す途中(backoff)を通る間に Ready の戻りを待つため。"
  (<- (AwaitReadiness name "Ready" seconds))
  (<- before (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess name #() seconds))
  (<- n int (Crash name))
  ;; 落とした直後の Service はまだ Ready と数えられている — Ready の外へ出たのを見てから、戻りを待つ(待ちが backoff を通る)。
  (<- (AwaitReadiness name "NotReady" seconds))
  (<- again Waited (wait-ready name seconds))
  (Restarted :before before :crashed n :again again))


;; 起こし直しの間を長くして、待ちが backoff の行(coordinator の版の判定は Blocked)を必ず読むようにする — 既定の 2 秒では次の
;; coordinator の書きより先に起き直り、backoff を「落ちた」と数える壊れ方でもこの検が緑のままだった(2026-10-05 に実測)。
(val SLOW-RESTART (WorkerPolicy :restart-backoff-ms 30000 :restart-backoff-max-ms 30000))


(deftest test-a-crashed-service-restarting-through-backoff-is-not-answered-as-failed
  (<- seen Restarted (sim-cluster :notice-broker (MemoryBroker) (beacons sim-foundation) (crash-then-wait "beacon" PATIENCE) :policy SLOW-RESTART))
  (assert (isinstance seen.before JobProcessSeen) seen.before)
  (assert (= seen.crashed 1) seen)
  ;; backoff は FAILED-PHASES に入らない — 起こし直しが済めば Ready が返る。
  (assert (isinstance seen.again.answer ServiceReadiness) seen.again.answer)
  (assert (= seen.again.answer.state "Ready") seen.again.answer)
  ;; 待ちは backoff の間(30 秒)を通った。
  (assert (>= seen.again.seconds 30.0) seen.again.seconds))
