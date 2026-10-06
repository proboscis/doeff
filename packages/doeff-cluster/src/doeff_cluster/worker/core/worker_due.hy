;;; worker の期限の純粋な判断(#3871 の単位 2)— 状態がこのままで、時刻だけで worker の判断の答えが変わる最初の刻を、期限の答え
;;; (shared/intent/due_model の DueAt・DueNow・DueNever)で返す。周の間の待ち(周期の眠り)にはまだ繋がない(単位 4)。
;;;
;;;   plan-due            policy.hy の時刻の比べ: 準備の作り直し(code の failed-ms + code-retry-ms)・検めの撃ち直し(probe の failed-ms +
;;;                       code-retry-ms)・起こし直しの間(last-exit-ms + backoff-ms)・TERM から KILL(signalled-ms + stop-grace-ms)・
;;;                       止まりを確かめられない(KILL の signalled-ms + kill-grace-ms)・待ちの子の KILL(stop の signalled-ms + stop-grace-ms)・
;;;                       待ちの子の起こし直し(ended-ms + code-retry-ms)・長く動いた後の報告(last-start-ms + stable-run-ms)
;;;   beat-due            heartbeat を送る間隔(最後に届いた返事 + 間隔 — beat_policy.heartbeat-due の時刻の条件)
;;;   fence-due           途絶の柵(最後に届いた返事 + fence-ms + 1・+ keep-fence-ms + 1 — heartbeat_rules と policy.kept-when-cut-off?)
;;;   prepare-stop-due    準備の停滞の止め(進みの印 + stall-seconds + 1 ms — env_upkeep.prepare-overdue)
;;;   sweep-interval-due  上限を越えたままの roots の数え直し(前の掃除の終わり + SWEEP-EVERY-MS — env_upkeep.sweep-due)
;;;
;;; 刻の拾い方: 判断が比べる刻を、その刻を持つ観測・記憶から全部拾い、now より後の物の最も早い刻を返す(無ければ DueNever)。now 以前の
;;; 刻は返さない — now の判断(周の頭で読んだ now で判じた判断)がもう当てた刻で、時刻だけではこの後の答えを変えない。だから落ち着いた
;;; worker には今すぐ(DueNow)を返さない(今すぐは「状態を変えた周の後はもう 1 周」の約束が持つ — 単位 4)。now は周の判断の now を渡す
;;; (周の後の刻を渡すと、周の間に過ぎた刻を落とす)。拾った刻で判断が変わらない事は在る(別の門が閉じている)— 早く起きて何も変えない
;;; だけで、判断が遅れる事は無い。
;;;
;;; 拾わない物: 状態の報告の検めの経過の秒(policy.probe-status — 表示だけで、他の訳で heartbeat を送る時に新しい値が載る)・
;;; heartbeat の時刻でない条件(待ちの口を使えない・前の heartbeat が届いていない・待ちが「変わった」と答えた・報告が変わった — 出来事)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import math)
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.shared.core.due_policy [due-of-instants])
(import doeff_cluster.worker.intent.worker_model [CodeState ProbeState StopStage Outcome WorldView WorkerPolicy])
(import doeff_cluster.worker.core.policy [backoff-ms])
(import doeff_cluster.worker.core.env_upkeep [RootsTally PrepareLimits SWEEP-EVERY-MS])


(defk due-after [now instants]
  {:pre [(: now int) (: instants tuple)] :post [(: % (| DueAt DueNever))] :tags {:context "worker" :role "judgment"}}
  "拾った刻の列 instants のうち now より後の物から、期限の答えを作るため(now 以前の刻は now の判断が当て済み — 頭の註)。"
  (<- due (| DueAt DueNow DueNever) (due-of-instants now (tuple (gfor at instants :if (> at now) at))))
  due)


(defk plan-due [now world records policy]
  {:pre [(: now int) (: world WorldView) (: records dict) (: policy WorkerPolicy)] :post [(: % (| DueAt DueNever))]
   :tags {:context "worker" :role "judgment"}}
  "policy.hy の判断(plan・statuses)の答えが時刻だけで変わる最初の刻を知るため(頭の註の 8 種の刻)。world = 周の後の観測・
   records = 周の後の job ごとの記憶。"
  (val codes (tuple (gfor code world.codes :if (= code.state CodeState.FAILED) (+ (or code.failed-ms 0) policy.code-retry-ms))))
  (val probes (tuple (gfor probe world.probes :if (= probe.state ProbeState.FAILED) (+ (or probe.failed-ms 0) policy.code-retry-ms))))
  (val backoffs (tuple (gfor record (.values records)
                             :if (and (is-not record.last-exit-ms None) (= record.last-outcome Outcome.EXITED))
                             (+ record.last-exit-ms (backoff-ms record policy)))))
  (val stops (tuple (gfor record (.values records)
                          :if (is-not record.stopping None)
                          (+ record.stopping.signalled-ms (if (= record.stopping.stage StopStage.TERM) policy.stop-grace-ms policy.kill-grace-ms)))))
  (val stables (tuple (gfor record (.values records) :if (is-not record.last-start-ms None) (+ record.last-start-ms policy.stable-run-ms))))
  (val warm-stops (tuple (gfor view world.warm-children
                               :if (and (is-not view.stop None) (= view.stop.stage StopStage.TERM))
                               (+ view.stop.signalled-ms policy.stop-grace-ms))))
  (val warm-restarts (tuple (gfor view world.warm-children :if (is-not view.exit-code None) (+ (or view.ended-ms 0) policy.code-retry-ms))))
  (<- due (| DueAt DueNever) (due-after now (+ codes probes backoffs stops stables warm-stops warm-restarts)))
  due)


(defk beat-due [now last-ok-ms interval-ms]
  {:pre [(: now int) (: last-ok-ms int) (: interval-ms int)] :post [(: % (| DueAt DueNever))] :tags {:context "worker" :role "judgment"}}
  "heartbeat を送る判断(beat_policy.heartbeat-due)が時刻だけで送りに変わる刻を知るため: 最後に届いた返事 last-ok-ms から interval-ms
   (silent-ms >= interval-ms)。"
  (<- due (| DueAt DueNever) (due-after now #((+ last-ok-ms interval-ms))))
  due)


(defk fence-due [now last-ok-ms fence-ms keep-fence-ms]
  {:pre [(: now int) (: last-ok-ms int) (: fence-ms int) (: keep-fence-ms int)] :post [(: % (| DueAt DueNever))]
   :tags {:context "worker" :role "judgment"}}
  "途絶の柵の判断が時刻だけで変わる刻を知るため: fence を越える刻(silent-ms > fence-ms — heartbeat_rules の desired-after-silence・
   desired-when-unreachable)と、動かし続けてよい印の job を止める刻(silent-ms > keep-fence-ms — policy.kept-when-cut-off?)。"
  (<- due (| DueAt DueNever) (due-after now #((+ last-ok-ms fence-ms 1) (+ last-ok-ms keep-fence-ms 1))))
  due)


(defk prepare-stop-due [now progressed-ms limits]
  {:pre [(: now int) (: progressed-ms int) (: limits PrepareLimits)] :post [(: % (| DueAt DueNever))]
   :tags {:context "worker" :role "judgment"}}
  "準備の停滞の止め(env_upkeep.prepare-overdue — 秒の float で now − progressed > stall-seconds)が真に変わる刻を知るため: 進みの印の刻
   progressed-ms に stall-seconds の ms を足した刻の 1 ms 後(> なので)。"
  (<- due (| DueAt DueNever) (due-after now #((+ progressed-ms (int (math.floor (* limits.stall-seconds 1000))) 1))))
  due)


(defk sweep-interval-due [now tally cap swept-ms]
  {:pre [(: now int) (: tally (| RootsTally None)) (: cap int) (: swept-ms int)] :post [(: % (| DueAt DueNever))]
   :tags {:context "worker" :role "judgment"}}
  "roots の数え直しの判断(env_upkeep.sweep-due)が時刻だけで変わる刻を知るため: 数えた合計が上限 cap を越えている間だけ、前の掃除の
   終わり swept-ms から SWEEP-EVERY-MS。まだ数えていない・上限の内なら、時刻では変わらない。"
  (<- due (| DueAt DueNever) (due-after now (if (and (is-not tally None) (> tally.bytes cap)) #((+ swept-ms SWEEP-EVERY-MS)) #())))
  due)
