;;; 配備の cluster(k3s の上で既に動いている coordinator と worker)に、契約の effect(shared/intent/cluster_control.hy)で話す handler と
;;; 入口(#3294・ADR-DOE-CLUSTER-001 R8 と追補 (3))。
;;;
;;;   (deployed-cluster scenario :target (DeployedCluster :url "http://<配備の coordinator>:8080" :actor "<送り手の名>" :revision <sha> :runtime-env env))
;;;
;;; sim-cluster(sim/local.hy)・手元の 1 台の cluster(sim/machine.hy の local-machine-cluster)と同じ入口の形で、同じ筋書きの Program を
;;; 動かす — 違いは土台の handler の組だけ(どの cluster に話すかを筋書きも命令の引数も知らない)。ここでは coordinator も worker も起こさず
;;; 止めない(配備してある物に話すだけ)。
;;;
;;; 筋書きが出せる effect(deployed-cluster-answers が答える):
;;;   Redeclare 系               宣言の部品 system-declaration と apply-declaration で、配備の coordinator へ宣言を書く(版 = target の
;;;                              revision・実行環境 = target の runtime-env・送り手 = target の actor — 手元の 1 台と同じ組み立て)。
;;;                              答え = 宣言した Service の名。
;;;   ReadinessOf 名              GET /resources/Service/<名> の status の ready(無ければ Missing)。
;;;   AwaitReadiness 名 状態 秒    同じ読みを WAIT-PROBE-SECONDS ごとにして、状態になるか秒を過ぎるまで待つ(過ぎたら ReadinessWaitExpired)。
;;;   AwaitJobProcess job 除く 秒  GET /state に**どれかの** worker が名乗った job の pid のうち、除く pid の外の物が出るまで同じ間隔で待つ
;;;                              (手元の 1 台は自分で起こした worker に絞る — 配備では worker を起こさない)。
;;;   壊す effect(Crash・KillWorker・StopWorker・StopCoordinator・CrashCoordinator)には答えない — DeployedCannotAnswer で、その effect の名と
;;;   訳を出して止める(配備してある物を壊す操作をテストの都合で足さない — 追補 (3)。壊すテストは sim と手元の 1 台で動かす)。網を切る・
;;;   固める・5xx を返させる effect は sim だけが持つ(sim/local.hy — 本番の code は sim の dir を import しない)ので、この組には届かない
;;;   (届けば答え手の無い effect として名指しで落ちる)。
;;; 準備と process の読みは手元の 1 台と共有する部品(shared/protocol/coordinator_reads.hy)。
(require doeff-hy.macros [defk defhandler <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "process"})
(import dataclasses [dataclass])
(import doeff [with-handlers Program EffectBase])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_time [async-time-handler])
(import doeff_cluster.foundation.process_versions [this-process-versions])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import doeff_cluster.shared.entry.service_build [system-declaration])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf ReadinessWaitExpired AwaitReadiness
                                                     AwaitJobProcess JobProcessSeen JobProcessWaitExpired Redeclare
                                                     Crash KillWorker StopWorker StopCoordinator CrashCoordinator])
(import doeff_cluster.shared.protocol.coordinator_reads [readiness-read readiness-awaited job-process-awaited])


(defclass DeployedCannotAnswer [Exception]
  "配備の cluster が答えない effect(job を落とす・worker や coordinator を止める — 壊す操作)を筋書きが出した。")


(defrecord DeployedCluster
  "配備の cluster に話す時の値(命令の引数にしない — 呼び手が 1 か所で持つ環境の値)。url = 配備の coordinator の口・actor = 宣言の書きに
   載せる送り手の名(出来事の記録に残る名)・revision = Redeclare が宣言に書く版・runtime-env = Redeclare が宣言に載せる実行環境(repo と
   commit と uv の lock — 手元の 1 台の LocalMachine の同じ欄と同じ役。None = 版の木の道)。"
  (#^ str url)
  (#^ str actor)
  (#^ str revision)
  (#^ (| RuntimeEnv None) runtime-env))


;; 引数に残す理由: 宛先の URL・送り手・宣言の版は、この handler を積む組み立て(deployed-cluster)が呼び手から受ける値で、読む Ask の鍵が
;; 無い(手元の 1 台の machine-answers・本番の宛先の部品 detached-cluster と同じ)。
(defhandler deployed-cluster-answers [#^ DeployedCluster target]
  (ReadinessOf [name]
    (<- readiness ServiceReadiness (readiness-read target.url name))
    (resume readiness))
  (AwaitReadiness [name state timeout-seconds]
    (<- awaited (| ServiceReadiness ReadinessWaitExpired) (readiness-awaited target.url name state (float timeout-seconds)))
    (resume awaited))
  (AwaitJobProcess [job excluding timeout-seconds]
    (<- seen (| JobProcessSeen JobProcessWaitExpired) (job-process-awaited target.url job excluding (float timeout-seconds)))
    (resume seen))
  (Redeclare [system environ]
    (<- versions dict (this-process-versions))
    ;; その宣言し直しの上書き(渡されなければ上書き無し — 本番の宣言と同じく宣言ごとの上書き・#3131)。
    (val declaration (system-declaration system target.revision :runtime-env target.runtime-env :versions versions
                                         :environ (if (is environ None) {} environ)))
    (<- placed bool (apply-declaration target.url declaration target.actor))
    (when (not placed)
      (raise (RuntimeError (+ "宣言を書けない(上の slog の行に返事)— " (.join "・" (lfor row declaration.rows (get row "name")))))))
    (resume (tuple (lfor row declaration.rows (get row "name")))))
  (Crash [name]
    (raise (DeployedCannotAnswer (+ "Crash(" name ")— 配備の cluster では job を落とさない(sim と手元の 1 台だけが答える)"))))
  (KillWorker [name]
    (raise (DeployedCannotAnswer (+ "KillWorker(" name ")— 配備の cluster では worker を殺さない(sim と手元の 1 台だけが答える)"))))
  (StopWorker [name]
    (raise (DeployedCannotAnswer (+ "StopWorker(" name ")— 配備の cluster では worker を止めない(sim と手元の 1 台だけが答える)"))))
  (StopCoordinator [seconds]
    (raise (DeployedCannotAnswer "StopCoordinator — 配備の cluster では coordinator を止めない(sim と手元の 1 台だけが答える)")))
  (CrashCoordinator [seconds]
    (raise (DeployedCannotAnswer "CrashCoordinator — 配備の cluster では coordinator を落とさない(sim と手元の 1 台だけが答える)"))))


(defk deployed-cluster [scenario * target]
  {:pre [(: scenario (| Program EffectBase)) (: target DeployedCluster)] :post [(: % "scenario の答え(型は筋書きごと)")]
   :tags {:context "doeff-cluster" :role "process"}}
  "配備の cluster に話して筋書きを走らせる入口(sim-cluster・local-machine-cluster と同じ形 — 違いは土台の handler の組だけ)。本物の HTTP と
   本物の時計で話し、配備してある物は起こさず止めない。答え = 筋書きの答え。"
  (<- answer (scheduled (with-handlers [(await-handler) slog-handler (async-time-handler) (http-production-handler)
                                        (deployed-cluster-answers target)]
                          scenario)))
  answer)
