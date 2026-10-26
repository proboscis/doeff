;;; coordinator の HTTP の要求 1 件への返事と、Rollout の調停の段取り(純粋な判断・I/O はしない)。
;;;
;;; 要求は respond が振り分ける。状態を変える要求は settle で「要求そのものの変化(送り手 = 要求の X-Actor)」と
;;; 「それを受けた調停の変化(送り手 = coordinator)」を分けて版と記録を付ける(resource_policy.stamp)。
;;;
;;; HTTP(資源の口・2026-09-24):
;;;   GET    /resources/<Kind>             一覧(Kind = Service | Worker | Task | Rollout | GroupLimit | NeedsOverride)
;;;   GET    /resources/<Kind>/<名>        1 つ(resourceVersion・generation・所有者・作った / 書いた送り手・spec・status)
;;;   POST   /resources/<Kind>             作る {"name", "spec"}(Service・Rollout・GroupLimit — 組の同時数の上限・NeedsOverride — Service の置き先の
;;;                                         一時の上書き {"needs" "reason" "ttlSeconds"} — 宣言は書き換えない)。在れば 409
;;;   PUT    /resources/<Kind>/<名>        書き換える {"spec", "resourceVersion"}。版が古ければ 409・版が無ければ 400
;;;   DELETE /resources/<Kind>/<名>?resourceVersion=&force=   消す(送り手が誰でも消せる。進行中の Rollout が扱う Service・進行中の Rollout は force でだけ消す。
;;;                                         NeedsOverride を消すと Service は宣言の needs で置き直される)
;;;   Service の PUT で、版(spec.revision)を変えずに置き先(needs・pin)を変える書きは 409 で断り、NeedsOverride を案内する(2026-10-27・card ki-1523bc0aba4f)
;;;   POST   /resources/Service/<名>/readiness   ReportReady の報告 {worker pid revision instance attempt specHash placement ready reason}
;;;   POST   /resources/Service/<名>/metrics     ReportMetrics の報告 {worker … placement metrics}(資源の状態は変えない)
;;;   GET    /metrics                            Prometheus の text: 今動いている process の計器(label service・worker)
;;;   GET    /events?kind=&name=&since=&limit=   出来事の記録(誰が・いつ・何を・前後の版)
;;;   POST   /leases/<名>  {"op" claim|renew|release|drop, "token", "permits", "ttlMs"}  名前付きの lease の操作(期限は coordinator の
;;;                        時計で書き・判じる — lease_rules.lease-op・2026-09-25)。答え {"ok" "reason" "ttlMs"}
;;;   GET    /workers/<名>                      worker の生存・世代・drain の進み(ready = 生きていて drain 中でない)
;;;   POST   /workers/<名>/drain {"ttlSeconds"? "boot"?}  drain を頼む(何度でも同じ意味・期限だけ延びる)。DELETE で取り消す(drain_policy)。
;;;                                      boot = 頼み手の process の世代。退いた世代の頼みは今の世代に drain を付けない(2026-09-27)
;;;   POST   /warm {"runtimeEnv" "needs" "ttlSeconds" "holder"} · GET /warm/<キー>
;;;                        実行環境の温める表(2026-09-26 — warm_policy。答えは WarmState)
;;;   POST   /tasks/<id>/result {"worker" "instance" "result" "format"}  task の子 process が終わる前に直に届ける結果(#1387 —
;;;                        cluster_policy.absorb-task-result。終わった task には何もしない・届かなければ heartbeat が運ぶ)
;;;   PUT /programs/<sha> {"blob" "versions"} · GET /programs/<sha>
;;;                        詰めた Program の置き場(2026-09-27 — program_policy。宣言の行と heartbeat の返事は sha だけを運ぶ)
;;;   PUT /detached/<key> · GET /detached/<key> · POST /detached/<key>/cancel · DELETE /detached/<key>
;;;                        切り離した task(呼び手と寿命を切り離した task — 送る・読む・取り消す・保持を解く・終わりの出口を読む・受領する。detached_policy)
;;; 書きには header X-Actor(依頼の主体の id・作業係の名・worker の名)が要る。盤と task は無ければ送り元の番地で記録する。
;;; 旧い口(PUT /jobs・/heartbeat・/board・/tasks)は残す。PUT /jobs は資源ごとの compare-and-set に写す(resource_policy)。
(require doeff-hy.macros [defk val var])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import dataclasses [replace])
(import traceback [extract-tb])
(import doeff [run])
(import doeff_cluster.coordinator.intent.request_bodies [BodyMalformed BodyUnreadable RequestBody])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request PlainText BodyInvalid])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ErrorReply TargetView TaskDropped BoardRead BoardEntryView ClusterNaming Fault RolloutStatus RolloutTarget StateReply
                                                       DeploymentSeen DeploymentUnreadable ServiceBody LegacyJobs])
(import doeff_cluster.coordinator.core.cluster_rules [format-version-refusal])
(import doeff_cluster.shared.core.lease_rules [holders-shifted])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.coordinator.core.metrics_policy [record-metrics metrics-text])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.intent.due_model [StepPace Stall])
(import doeff_cluster.shared.core.due_policy [earliest-due])
(import doeff_cluster.coordinator.core.cluster_policy [reconcile register-heartbeat heartbeat-reply state-view submit-task poll-task absorb-task-result board-write board-written note-liveness note-unplaced
                         lease-write other-generation-boot alive remember-keep-marks liveness-due task-due sweep-due])
(import doeff_cluster.coordinator.core.resource_policy [Refused refuse stamp require-actor valid-actor service-readiness service-stopped record-readiness
                          running-process list-resources get-resource events-view create-resource update-resource delete-resource
                          legacy-put-jobs COORDINATOR ready-instances create-needs-override lift-needs-overrides needs-override-due])
(import doeff_cluster.coordinator.core.drain_policy [advance-drains request-drain absorb-stopping cancel-drain worker-view superseded-worker-view drains-view])
(import doeff_cluster.coordinator.core.handoff_policy [watch-handoffs handoff-due])
(import doeff_cluster.coordinator.intent.cluster_model [HandoffPhase])
(import doeff_hy.table [TableWrite draft-of])
(import doeff_cluster.coordinator.core.detached_policy [Reply submit-detached detached-read cancel-detached release-detached outbox-read outbox-ack])
(import doeff_cluster.coordinator.core.rollout_policy [rollout-step rollout-targets target-key deployment-owners drift-status action-due shift-clocks TERMINAL-PHASES])
(import doeff_cluster.coordinator.core.warm_policy [warm-write warm-read])
(import doeff_cluster.coordinator.core.program_policy [program-write program-read sweep-programs])

(setv ROLLOUT-ACTOR "rollout-controller")


(defk settle [before after actor now timing]
  {:pre [(: before ClusterState) (: after ClusterState) (: actor str) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)]
   :tags {:context "coordinator" :role "judgment"}}
  "要求の変化に送り手の版を付け、調停し、調停の変化に coordinator の版を付ける。"
  (val changed (stamp before after actor now timing))
  ;; drain(2026-09-25): 割り当ての後に、drain 中の worker の上の入れ替えの Service を並べる・付け替える(readiness を読む)。
  ;; 入れ替えの期限(2026-09-26): 最後に、入れ替えの Service の期限の見張りを進める(heartbeat の返事はこの後の状態から作る)。
  ;; 詰めた Program の置き場(改訂 1 の F): 参照の無くなった物を掃除する。
  ;; 生死の切り替わり(#1934): 最後に、生きていないと数える worker の名を今の時刻で求め直す(変われば Worker の status の live と版が進む)。
  ;; 置き先の上書き(2026-10-27・card ki-1523bc0aba4f): 置き方の前に、期限の来た上書きと、上書きの needs を満たす生きた worker が 0 になった
  ;; 上書きを外す(同じ拍で宣言の needs で置き直す — 外した拍と置き直す拍の間に Service が置き先の無いまま残らない)。
  (<- lifted ClusterState (lift-needs-overrides changed now timing))
  (<- placed ClusterState (reconcile now lifted timing))
  (val reconciled (note-liveness (sweep-programs (watch-handoffs now (advance-drains now placed timing) timing) now) now timing))
  ;; 置き先の無い間の起点(card ki-53b8677f1374): 最後に、置き先が無いと最初に見た拍の刻を、この拍の置き先で求め直す(保存しない・版を進めない)。
  (<- noted ClusterState (note-unplaced reconciled now))
  (stamp changed noted COORDINATOR now timing))


(defk tick [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % ClusterState)]
   :tags {:context "coordinator" :role "judgment"}}
  "要求の無い拍: 期限の経過だけで調停する(worker の沈黙・task の期限・readiness の window)。"
  (<- ticked ClusterState (settle state state COORDINATOR now timing))
  ticked)


(defk placement-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "置き先と入れ替えの判断が、状態がこのままで答えを変え得る最初の刻を知るため(#3064)。tick の中で worker の生死・task・盤・drain・
   温める表・keep の印・Program の掃除(liveness-due・task-due・sweep-due)の外に残る時刻の比べは、入れ替えの見張り(watch-handoffs —
   担い手の報告の古さ・readiness の判定・諦めの期限)だけ(place-jobs 自身は生死と drain の期限しか比べない)。入れ替えの Service ごとの
   handoff_policy.handoff-due の最小。None = 状態がこのままなら、入れ替えの見張りは時刻では変わらない。"
  (var first None)
  (for [job state.jobs]
    (when job.spec.handoff
      (<- due (| int None) (handoff-due state job now timing))
      (when (and (is-not due None) (or (is first None) (< due first)))
        (:= first due))))
  first)


(defk tick-due [state now timing]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming)] :post [(: % (| DueAt DueNow DueNever))] :tags {:context "coordinator" :role "judgment"}}
  "要求の無い拍(tick)が、状態がこのままで状態を変え得る最初の刻を知るため(#3060 — 静かな区間はそれより前の歩を試さずに進める)。
   tick の中の期限で動く判断ごとの答え(intent/due_model — #3865)を合わせた物。どれも DueNever なら DueNever(状態がこのままなら
   tick は何も変えない)。"
  (<- placing (| int None) (placement-due state now timing))
  (<- lifting (| int None) (needs-override-due state))
  (val dues #((! (liveness-due state now timing)) (! (task-due state now timing)) (! (sweep-due state now timing))
              (if (is placing None) (DueNever) (DueAt :at placing))
              (if (is lifting None) (DueNever) (DueAt :at lifting))))
  (<- answer (| DueAt DueNow DueNever) (earliest-due dues))
  answer)


;; --- coordinator が止まっていた時間(2026-09-25) --------------------------------------------------
;; 止まっている間は、誰も Rollout を進めず・task の問い合わせにも答えられない。起動の時にその時間を「経った」と数えると、
;; 進行中の Rollout は観測の無いまま時間切れで戻しに入り、task は呼び手が問い合わせていたのに lease 切れで落ちる。
;; 生きていた最後の時刻(alive-ms)を ALIVE-MARK-MS ごとに耐久の鍵へ書き、起動の時に止まっていた長さだけ時計をずらす。
;; 書きの間隔の分(最大 ALIVE-MARK-MS)だけ長めに見積もる = 時間切れを遅らせる側に外れる。
(setv ALIVE-MARK-MS 5000)

(defk stamp-alive [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "生きていた時刻を now に進め、各 worker の最後の連絡の時刻を WorkerInfo.seen-mark に写した状態を作るため(耐久の鍵 counter と、連絡の
   あった worker の worker/<名> が変わる = 次の Persist に載る。沈黙している worker の鍵は変わらない)。印の間隔を問わない — 止まる時の
   印(run-coordinator の止まりの枝 — #3865)と、間隔の来た印(mark-alive)が使う。
   印が最後の連絡の時刻と揃っている worker は同じ物のまま運び、全員が揃っていれば workers の表も同じ物のまま — 沈黙している
   worker の行と、版の比べ(resource_policy.dirty-keys の workers)を印の拍で動かさない(#2903)。"
  (replace state
           :alive-ms now
           :workers (.with-writes state.workers
                                  (tuple (gfor #(n w) (.items state.workers)
                                               :if (!= w.seen-mark w.last-seen-ms)
                                               (TableWrite n (replace w :seen-mark w.last-seen-ms)))))))


(defk mark-alive [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "ALIVE-MARK-MS 経っていれば生きていた時刻の印を付けた状態(stamp-alive)、経っていなければ同じ状態を求めるため(調停ループの歩と、
   模擬の静かな区間の歩が同じこの関数で印を付ける)。"
  (if (>= (- now state.alive-ms) ALIVE-MARK-MS)
      (! (stamp-alive state now))
      state))

(defn #^ tuple resume-after-downtime [#^ ClusterState state #^ int now]
  "純粋: 読み直した状態 → #(時計をずらした状態 止まっていた長さ ms)。ずらすのは進行中の Rollout の段の起点(shift-clocks)と
   task の lease の期限と、worker の最後の連絡の時刻(と、その印 WorkerInfo.seen-mark)と、Ready を待っている入れ替えの期限の起点
   (HandoffWatch.since-ms — 止まっていた間は期限に数えない・2026-09-26)と、盤の lease の行(semaphore/<名>)の担い手の期限
   (lease_rules.holders-shifted — 止まっていた間に延ばせなかった持ち主を切らない・行の版を 1 進める・card ki-3b7498747e4c)と、
   頼み手が頼み直して延ばす期限 — 盤の TTL の行の expires-ms・drain の until-ms・温める表の行の until-ms — と、退いた世代の最後の報告の
   刻(SupersededRun.seen-ms — 沈黙を lease で数える)(2026-10-16・card ki-d033e5f969cf の設計検証)。欄ごとの分け方は STALL-SHIFTED-FIELDS・
   STALL-UNSHIFTED-FIELDS。生きていた時刻を知らない置き場(2026-09-25 より前)は 0。
   worker の最後の連絡の時刻は、止まる前の最後の印(alive-ms)の時点の沈黙を今から数え直した値になる: 止まる直前まで連絡のあった
   worker は起き直した直後も生きていて、その後に連絡が無ければ移し替えの期限の後に沈黙と判じる。止まる前から沈黙していた worker は
   沈黙のまま(再起動で「いま連絡があった」に戻さない)。止まっていた長さは沈黙に数えない(担い手が一斉に沈黙に倒れ、最初に
   heartbeat を送った worker へ全 job が移ると、元の担い手と二重に動く — 実測 2026-09-23)。"
  (setv gap (if (> state.alive-ms 0) (max 0 (- now state.alive-ms)) 0))
  ;; 受付が worker から届き得るようになるまで(最初の heartbeat)の長さも沈黙に数えない — 起点の刻を置く(unreached-shifted)。
  (if (= gap 0)
      #((replace state :alive-ms now :unreached-ms now) 0)
      #((replace (run (clocks-shifted state now gap)) :unreached-ms now) gap)))


;; --- 起き直しの後、worker から届き得るようになるまで(2026-10-21・card ki-439a73dc075c) ------------------------------------
;; 読み直しは止まっていた長さを「読み直した刻」までしか数えないが、worker の heartbeat が届き始めるのは Pod が Ready になり Service に
;; 載った刻から(readinessProbe の tcpSocket の周期 10 秒 — 受付を始めてから最大 10 秒 = lease と同じ長さ)。その差を沈黙に数えると、
;; ずらした最後の連絡が lease で切れ、配備のたびに全 worker が WorkerGone になって Service に載った直後に WorkerBack で戻った
;; (rev 47 2026-10-17 15:24:57Z〜15:25:14Z の実測・card ki-a7f26191a164 の形 A)。起き直しから最初の heartbeat を受けるまでを止まりと
;; 同じく経過・沈黙に数えない(clocks-shifted でずらす)。
;; 終える証拠(card ki-f9fd557e6ad0): 最初の heartbeat か、外からの最初の接続(ServiceReached — Pod が Ready になり Service に載った印)。
;; worker の heartbeat だけを待つと、全 worker が止まりの間に死んでいた時に、死んだ worker を上限まで生きていると答える(条 L1・L2 — 死んだ
;; worker は lease の後に生きていると答えず、置き先にも選ばない)。coordinator の側からは「届かない worker」と「死んだ worker」を見分けられない
;; (止まる直前の連絡の古さは heartbeat の間隔と重なる)ので、見分ける材料は Service に載ったかどうかにする: 載った後は、生きた worker は
;; lease の内(10 秒 ≥ endpoint の伝わり + heartbeat の間隔 2.5 秒)に届くので、届かなければ従来どおり沈黙に数える。
;; 待ちの上限(後ろ盾): 起き直しから REACH-WAIT-MS の内に証拠が 1 つも無ければ、その後は従来どおり数える(受付が接続を見られない時にも、
;; Rollout の段・盤の TTL・drain の期限を止め続けない)。上限は Ready までの最長(周期 10 秒)+ Service の endpoint の伝わり + worker の
;; heartbeat の間隔と接続の打ち切りを越える長さ。
(setv REACH-WAIT-MS 30000)

(defk unreached-shifted [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "起き直しの後まだ heartbeat を受けていない(unreached-ms が在る)状態から、その刻から now まで(待ちの上限 REACH-WAIT-MS の刻まで)の
   長さを経過・沈黙に数えないように時計をずらし(clocks-shifted)、起点を now へ進めた状態を作るため。上限の刻を越えたら起点を
   None に戻す(以後は従来どおり数える)。受けた後・起き直していない(None)なら同じ状態。最初の heartbeat を受けた歩の後に起点を
   None に戻すのは調停の歩(program.coordinator-step)— 受けた heartbeat の前の判断(tick)はこの関数でずらした時計で判じる。"
  (val since state.unreached-ms)
  (when (is since None)
    (return state))
  (val limit (+ state.started-ms REACH-WAIT-MS))
  (val gap (- (min now limit) since))
  (val shifted (if (> gap 0) (! (clocks-shifted state now gap)) state))
  (replace shifted :unreached-ms (if (< now limit) (max since now) None)))


;; ClusterState の欄ごとの、止まっていた長さのずらし方の分け方(2026-10-16・card ki-d033e5f969cf の設計検証)。止まっていた間は、頼み手が
;; 頼み直して延ばす期限も、沈黙を数える最後の連絡も進められないので、その長さを経過に数えない。新しい欄はどちらかに必ず入れる
;; (tests/test_coordinator_stall.hy が、どちらにも無い欄と、ずらすと言ってずらさない欄を断る — 期限を持つ機能を足す人に、止まりの後に
;; その期限が切れてよいかを決めさせる)。
(val STALL-SHIFTED-FIELDS
  (frozenset #("workers"           ; 最後の連絡と印 — 沈黙・移し替え・約束の期限の起点
               "tasks"             ; lease の期限 — 呼び手の問い合わせで延びる
               "rollouts"          ; 段の起点 — 止まっていた間は観測が無い(rollout_policy.shift-clocks)
               "handoffs"          ; Ready を待つ入れ替えの期限の起点
               "board_tally"       ; 盤の集計 — board の書きと一緒に足し引きする(board-written)
               "board"             ; 盤の lease の担い手の期限と、TTL の行の expires-ms — 頼み手が書き直して延ばす
               "drains"            ; drain の until-ms — 頼み直すたびに延びる
               "warms"             ; 温める表の行の until-ms — 頼み直すたびに延びる
               "superseded_runs"   ; 退いた世代の最後の報告の刻 — 沈黙を lease で数える・受け渡しの待ちの始まり — 止まりの知らせの起点
               "alive_ms")))       ; 生きていた刻 — now にする(止まりの直後の再起動で二重にずらさない)
(val STALL-UNSHIFTED-FIELDS
  (frozenset #("jobs" "refused" "group_limits"            ; 宣言 — 刻を持たない
               "placements" "surges"                     ; 置いた刻 since-ms は事実(期限は担い手の最後の連絡で数える)
               "keep_marks"                              ; 渡した刻 since-ms は事実(約束の期限は担い手の最後の連絡で数える)
               "programs"                                ; 置いた刻 put-ms は事実(掃除の猶予は参照の無い blob だけ)
               "started_ms"                              ; process の起動の刻 — 起動直後の猶予の起点
               "observations"                            ; 観測の刻 — 次の heartbeat と見張りで新しくなる(古い報告の担い手は最後の連絡で数える)
               "silent"                                  ; 沈黙の集合 — 最後の連絡から求め直す
               "events" "audit" "audit_seq" "meta" "revision"  ; 出来事と版の記録 — 事実
               "next_task" "task_prefix" "env_cold_starts"     ; 数と名
               "outbox"                                  ; 終わった刻 finished-ms は事実(出口の保持の期限は受領されない行の掃除だけ)
               "needs_overrides"                         ; 上書きの期限 expires-ms は書き手が決めた刻(頼み直して延ばす期限でない)
               "derivable" "running_commit"                    ; 導く能力の名・走っている版
               "unreached_ms")))                               ; 届かない間の起点 — ずらしの起点そのもの(unreached-shifted が進める)


(defk clocks-shifted [state now gap]
  {:pre [(: state ClusterState) (: now int) (: gap int) (> gap 0)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "judgment"}}
  "coordinator が gap ms 止まっていた状態から、その長さを経過・沈黙に数えないように時計をずらし、生きていた時刻を now にした状態を
   作るため。ずらす欄は STALL-SHIFTED-FIELDS、ずらす物と訳は resume-after-downtime の註(起き直しと、process の中の止まり — stall-of・
   program.stall-resumed — が同じこの関数でずらす)。"
  (replace (board-written state (tuple (gfor #(k r) (.items state.board)
                                              :setv shifted (if (.startswith k SEMAPHORE-PREFIX) (holders-shifted r.value gap) None)
                                              :setv held (if (is shifted None) r (replace r :value shifted :version (+ r.version 1)))
                                              :setv moved (if (is held.expires-ms None) held (replace held :expires-ms (+ held.expires-ms gap)))
                                              :if (is-not moved r)
                                              (TableWrite k moved))))
           ;; 盤: lease の行は担い手の期限をずらして版を 1 進め、TTL の行は期限だけをずらす(値は同じなので版は進めない)— 上の board-written。
           :rollouts (.with-writes state.rollouts (tuple (gfor #(k r) (.items state.rollouts) (TableWrite k (replace r :status (shift-clocks r.status gap))))))
           :tasks (.with-writes state.tasks (tuple (gfor #(k t) (.items state.tasks) (TableWrite k (replace t :lease-until-ms (+ t.lease-until-ms gap))))))
           :workers (.with-writes state.workers
                                  (tuple (gfor #(k w) (.items state.workers)
                                               (TableWrite k (replace w
                                                                      :last-seen-ms (min now (+ w.last-seen-ms gap))
                                                                      :seen-mark (if (is w.seen-mark None) None (min now (+ w.seen-mark gap))))))))
           :handoffs (.with-writes state.handoffs
                                   (tuple (gfor #(k w) (.items state.handoffs) :if (= w.phase HandoffPhase.WAITING)
                                                (TableWrite k (replace w :since-ms (min now (+ w.since-ms gap)))))))
           :drains (.with-writes state.drains (tuple (gfor #(k d) (.items state.drains) (TableWrite k (replace d :until-ms (+ d.until-ms gap))))))
           :warms (.with-writes state.warms (tuple (gfor #(k w) (.items state.warms) (TableWrite k (replace w :until-ms (+ w.until-ms gap))))))
           ;; 受け渡しの待ちの始まり(since-ms)も、Ready を待つ入れ替えの期限の起点と同じく止まっていた間を数えない(card ki-d8e594fe934d)。
           :superseded-runs (tuple (gfor r state.superseded-runs
                                         (replace r :seen-ms (min now (+ r.seen-ms gap))
                                                    :since-ms (if (is r.since-ms None) None (min now (+ r.since-ms gap))))))
           :alive-ms now))


;; --- coordinator が process の中で止まっていた時間(2026-10-16・card ki-d033e5f969cf) -----------------------------------
;; 機体ごとの止まり(2026-10-13 02:22〜02:24 JST の atlas — containerd と kubelet が 3 分答えず、host の記録にも空白が在る)では、
;; coordinator は落ちずに止まり、起きた最初の歩で全 worker の最後の連絡が lease を越えていて、全員を沈黙と数えた(配る担当へ消えと
;; 戻り 33 回・card ki-b4d94c618094)。起き直し(resume-after-downtime)と同じく、止まっていた長さは沈黙・経過に数えない。
;; 止まりは「予定の外の長さ」で見分ける: 前の歩の長さ(判断の刻から受付を待ち始めるまで — 保存の fsync・k8s の書きを含む)+ 予定の
;; 起きる刻からの寝過ごし。予定どおりの眠り(worker の期限の刻まで待つ等)は数えない — 数えると、期限の刻に起きるたびに期限をずらし、
;; 本当に黙った worker を沈黙と判じない。期限なしの待ち(次に起きる刻が無い)は寝過ごしを測らず、前の歩の長さだけで見つける。
;; 残る代償(採った上で): 予定の外の長さが lease-ms 以下の止まりは見つけない(直す前と同じく、期限の近い worker が消えて戻る)。予定の
;; 起きる刻までの眠りは止まりに数えないので、起きる予定が前の歩から lease 近く先なら、見つけない止まりは最大でおよそ lease の 2 倍に
;; なる(期限の近い worker の消えと戻りが残る)。止まりが歩ごとに続く間
;; (どの歩も予定の外の長さが lease を越える — 保存がいつも詰まる等)は経過を数えないので、その間は死んだ worker の判定も延びる。
;; 続けば止まりの行(program.STALL-LOG)が歩ごとに出る。

(defk stall-of [pace now timing]
  {:pre [(: pace (| StepPace None)) (: now int) (: timing ClusterTiming)] :post [(: % (| Stall None))]
   :tags {:context "coordinator" :role "judgment"}}
  "前の歩の刻み pace と今の歩の刻 now から、coordinator が process の中で止まっていたか(止まっていれば経過に数えない長さを持つ
   Stall・止まっていなければ None)を知るため。予定の外の長さ(前の歩の長さ + 予定の起きる刻からの寝過ごし)が lease-ms を越えた時
   だけ止まりと数え、前の歩の判断の刻から今までの全部を経過に数えない(止まりが待ちのどこで始まったかは分からない — 待ちの間に
   届いたはずの heartbeat を受けられなかった側に倒す。ずらした後の worker の沈黙は、前の歩の時点の沈黙になる)。止まりの後の歩は、
   止まり続けない限り予定の外の長さが短いので、ずらしを重ねない。pace = None(起動の最初の歩 — 止まっていた長さは読み直しの
   resume-after-downtime が数える)は None。"
  (when (is pace None)
    (return None))
  (val busy (max 0 (- pace.waited-ms pace.judged-ms)))
  (val overslept (if (is pace.planned-ms None) 0 (max 0 (- now pace.planned-ms))))
  (val gap (- now pace.judged-ms))
  (if (and (> (+ busy overslept) timing.lease-ms) (> gap 0))
      (Stall :busy-ms busy :overslept-ms overslept :gap-ms gap)
      None))

;; --- Rollout の相手の観測 ----------------------------------------------------------------------

(defn #^ TargetView target-view [#^ ClusterState state #^ RolloutTarget target #^ RolloutStatus status #^ int now #^ ClusterTiming timing]
  (if (= target.kind "Service")
      ;; 止まっているかは版の判定(resource_policy.version-state の Stopped)と同じ述語 service-stopped で読む(条件を 2 か所に書かない)。
      (do (setv name target.name
                job (next (gfor j state.jobs :if (= j.spec.name name) j) None)
                verdict (service-readiness state name now timing)
                stopped (service-stopped state name now timing))
          (TargetView :ready (get verdict "state") :stopped stopped :spec-replicas (if job job.replicas None)
                      :reason (if stopped "止まっている" (get verdict "reason"))))
      (do (setv obs (.row state.observations.deployments (+ target.namespace "/" target.name))
                ;; 読めた観測だけを判じに使う。無い・読めなかった観測は Unknown(台数を変えない)。観測は Deployment の見張りが変化の
                ;; たびに書き直し、見張りが届かない間は読めなかった観測になる(#3868)ので、時刻で古いと数えない。
                fresh (match obs
                        (DeploymentSeen :reading reading) reading
                        _ None))
          (when (is fresh None)
            (return (TargetView :ready "Unknown" :stopped None :spec-replicas None
                                :reason (match obs
                                          None "まだ観測していない"
                                          (DeploymentUnreadable :error error) error))))
          (setv dry target.dry-run
                simulated (.get (or status.simulated {}) (target-key target))
                want (if (and dry (is-not simulated None)) simulated fresh.spec-replicas)
                ready-n fresh.ready-replicas
                settled (or dry (and (>= fresh.observed-generation fresh.generation)
                                     (>= fresh.updated-replicas want)))
                ready (and (> want 0) (>= ready-n want) settled)
                stopped (and (= want 0) (or dry (= fresh.replicas 0))))
          (TargetView :ready (if ready "Ready" "NotReady") :stopped stopped :spec-replicas want
                      :reason (.format "宣言 {}{}・Pod {}・ready {}" want (if (and dry (is-not simulated None)) "(dry-run の値)" "")
                                       fresh.replicas ready-n)))))


(defk deployments-to-follow [state]
  {:pre [(: state ClusterState)] :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "Rollout の判断が観測を要る Deployment の「ns/名」を知るため(見張る相手 — FollowDeployments・#3868): 進行中の Rollout の相手と、
   台数を持つ(Observing / Complete の)Rollout の相手。時刻で選ばない — 観測は見張りが変化のたびに書き直す。"
  (val moving (tuple (gfor r (.rows state.rollouts) :if (not-in r.status.phase TERMINAL-PHASES)
                           t (rollout-targets r.spec) :if (= t.kind "Deployment")
                           (+ t.namespace "/" t.name))))
  (tuple (dict.fromkeys (+ moving (tuple (deployment-owners state.rollouts))))))


(defk plan-rollouts [state now timing [naming (ClusterNaming)]]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "全 Rollout を 1 拍進める。返り値 #(次の状態 action の list)。action = rollout(名)・op(scale / annotate)・target・replicas の dict。
   台数の食い違い(配備の流れが Deployment の replicas を当て直した等)は直さず status.drift に出す。
   naming = 台数の持ち主の annotation の鍵と値の頭(配備する側が決める — cluster_model.ClusterNaming)。"
  (setv rollouts (draft-of state.rollouts) actions [])
  (for [#(name r) (sorted (.items state.rollouts))]
    (setv spec r.spec status r.status)
    (when (not-in status.phase TERMINAL-PHASES)
      (setv #(stepped acts) (rollout-step spec status (target-view state spec.from-target status now timing)
                                          (target-view state spec.to-target status now timing) now))
      (.put rollouts name (replace r :status stepped))
      ;; 失敗が続く action は間を空けて出し(action-due — 1 秒から倍々・上限 60 秒)、成功した Deployment への書きは、その書きの後の
      ;; 観測が届くまで出し直さない(#3868)。
      (.extend actions (gfor a acts
                             :setv target (.get a "target")
                             :setv observed (if (and (isinstance target RolloutTarget) (= target.kind "Deployment"))
                                                (.row state.observations.deployments (+ target.namespace "/" target.name))
                                                None)
                             :if (action-due stepped a now observed)
                             (| a {"rollout" name})))))
  ;; 台数の持ち主と食い違い。進行中の Rollout が扱っている Deployment は、台数が動くのが意図どおりなので数えない。
  ;; 持ち主でなくなった(後の Rollout へ移った・進行中の Rollout が扱い始めた)Rollout の食い違いは消す。
  ;; Observing の Rollout は旧を止め終えて台数を持つ側なので、ここでは「進行中」に数えない(2026-09-24 の実弾: 数えていたので
  ;; 観察の間に本番の配備の流れが replicas を 1 へ戻したのを食い違いとして出せなかった)。
  (setv busy (sfor r (.rows rollouts) :if (not-in r.status.phase (| TERMINAL-PHASES #{"Observing"}))
                   t (rollout-targets r.spec) :if (= t.kind "Deployment")
                   (+ t.namespace "/" t.name))
        owners (dfor #(k v) (.items (deployment-owners (.freeze rollouts))) :if (not-in k busy) k v)
        owning (sfor v (.values owners) (get v 0)))
  (for [#(name r) (.items (.freeze rollouts))]
    (when (and r.status.drift (not-in name owning))
      (.put rollouts name (replace r :status (replace r.status :drift None :drift-resolved-ms now)))))
  (for [#(key #(name expected)) (.items owners)]
    (setv owned (.row rollouts name))
    (when (is owned None) (continue))
    (setv drifted (drift-status owned.status key expected (.row state.observations.deployments key) now))
    (.put rollouts name (replace owned :status drifted))
    (when (and owned.spec.mark-deployment (!= drifted.marked-deployment key))
      (setv #(ns dep) (.split key "/" 1)
            mark {"rollout" name "op" "annotate" "namespace" ns "name" dep
                  "annotations" {naming.owner-annotation (.format "{}/Rollout/{} replicas={}" naming.owner-scope name expected)}})
      ;; 印の annotation の失敗も scale と同じ間を空けて出し直す(action-due — 間を空けないと失敗の記録で状態が毎歩変わる・#3868)。
      (when (action-due drifted mark now None)
        (.append actions mark))))
  ;; 段が進まなければ状態そのものを返す(reconcile と同じ — stamp が写しを作らずに返す・#1356)。
  (val stepped-rollouts (.freeze rollouts))
  #((if (= stepped-rollouts state.rollouts) state (replace state :rollouts stepped-rollouts)) actions))


(defn #^ ClusterState scale-service [#^ ClusterState state #^ str name #^ int replicas]
  (replace state :jobs (tuple (gfor j state.jobs (if (= j.spec.name name) (replace j :replicas replicas) j)))))


(defn #^ ClusterState record-action [#^ ClusterState state #^ dict action #^ bool ok #^ (| str None) error #^ int now
                                     #^ (| int None) [result None]]
  "実行した action の結果を Rollout の status に残す(dry-run の台数は simulated に)。同じ action の繰り返しは数を進め、at を最後に実行した
   刻にする(失敗の出し直しの間は最後の失敗から数え、成功した書きは最後の書きの後の観測を待つ — action-due・#3868)。"
  (setv name (get action "rollout") r (.row state.rollouts name))
  (when (is r None) (return state))
  (setv status r.status
        what (dfor #(k v) (.items action) :if (not-in k #("rollout" "target")) k v)
        target (.get action "target"))
  (when target (setv (get what "target") (target-key target)))
  (setv previous status.last-action)
  (setv entry (| what {"ok" ok "error" error "at" now "count" 1}))
  (when (and previous (= (dfor #(k v) (.items previous) :if (not-in k #("at" "count")) k v)
                         (dfor #(k v) (.items entry) :if (not-in k #("at" "count")) k v)))
    (setv entry (| previous {"count" (+ (.get previous "count" 1) 1) "at" now})))
  (setv status (replace status :last-action entry))
  (when (and ok target (= target.kind "Deployment") target.dry-run)
    (setv status (replace status :simulated (| (or status.simulated {}) {(target-key target) (if (is result None) (get action "replicas") result)}))))
  (when (and ok (= (get action "op") "annotate"))
    (setv status (replace status :marked-deployment (+ (get action "namespace") "/" (get action "name")))))
  (replace state :rollouts (.with-writes state.rollouts (tuple [(TableWrite name (replace r :status status))]))))


;; --- 要求の振り分け ---------------------------------------------------------------------------

(defn #^ str loose-actor [#^ Request request]
  "盤と task の送り手(旧い client は X-Actor を付けないので、送り元の番地で記録する)。"
  (or (valid-actor request.actor) (+ "anonymous@" (or request.peer "?"))))


(defk detached-reply [state reply request now timing]
  {:pre [(: state ClusterState) (: reply Reply) (: request Request) (: now int) (: timing ClusterTiming)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "切り離した task の口の答え(detached_policy の Reply)→ respond の返り値 #(次の状態 status 本文)。状態を変えた答えだけ
   settle(版と出来事の記録)を通す。"
  #((if (is reply.state state) state (! (settle state reply.state (loose-actor request) now timing))) reply.status reply.body))


(defn #^ tuple unknown-request [#^ ClusterState state #^ Request request]
  "知らない要求に 404 で答えるため(状態は変えない)。"
  #(state 404 (ErrorReply :message (.format "知らない要求: {} {}" request.method request.path))))


(defk respond-resources [state request body parts now timing]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)]
   :post [(: % tuple)]
   :tags {:context "coordinator" :role "judgment"}}
  "資源の口(/resources の下)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 資源の口 ---
    (and (= head "resources") (= (len parts) 2) (= method "GET"))
      #(state 200 (list-resources state (get parts 1) now timing))
    (and (= head "resources") (= (len parts) 3) (= method "GET"))
      #(state 200 (get-resource state (get parts 1) (get parts 2) now timing))
    (and (= head "resources") (= (len parts) 2) (= method "POST"))
      (do (setv actor (require-actor request.actor))
          (setv created (if (= (get parts 1) "NeedsOverride")
                            (! (create-needs-override state body actor now timing))
                            (create-resource state (get parts 1) body actor now)))
          (setv after (! (settle state created actor now timing)))
          #(after 201 (get-resource after (get parts 1) body.name now timing)))
    (and (= head "resources") (= (len parts) 3) (= method "PUT"))
      (do (setv actor (require-actor request.actor))
          (setv after (! (settle state (update-resource state (get parts 1) (get parts 2) body actor) actor now timing)))
          #(after 200 (get-resource after (get parts 1) (get parts 2) now timing)))
    (and (= head "resources") (= (len parts) 3) (= method "DELETE"))
      (do (setv actor (require-actor request.actor))
          (setv after (! (settle state (delete-resource state (get parts 1) (get parts 2) request.query now timing)
                                 actor now timing)))
          #(after 200 {"deleted" (+ (get parts 1) "/" (get parts 2)) "revision" after.revision}))
    (and (= head "resources") (= (len parts) 4) (= (get parts 1) "Service") (= (get parts 3) "readiness") (= method "POST"))
      (do (setv actor (loose-actor request))
          #((! (settle state (record-readiness state (get parts 2) body now) actor now timing)) 200 {"ok" True}))
    ;; 計器の報告は資源の状態を変えない(版も記録も進めない・永続化しない)ので settle を通さない。
    (and (= head "resources") (= (len parts) 4) (= (get parts 1) "Service") (= (get parts 3) "metrics") (= method "POST"))
      #((record-metrics state (get parts 2) body now) 200 {"ok" True})
    True (unknown-request state request)))


(defn #^ tuple respond-observations [#^ ClusterState state #^ Request request #^ object body #^ list parts #^ int now #^ ClusterTiming timing]
  "計器(/metrics)と出来事(/events)の読みに答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "GET") (= parts ["metrics"]))
      #(state 200 (PlainText (metrics-text state now timing)))
    (and (= method "GET") (= parts ["events"])) #(state 200 (events-view state request.query))
    True (unknown-request state request)))


(defn #^ bool quiet-heartbeat [#^ ClusterState before #^ ClusterState heard #^ str name #^ int now #^ ClusterTiming timing]
  "heartbeat を写した状態 heard が、調停の答えを変えない「静かな heartbeat」か — 静かなら、同じ now で調停を済ませた before の調停を
   繰り返さずに済ませるため(#2655 — 模擬の 1 拍の費用の大半が、変化の無い heartbeat の歩の調停だった)。静か = 前から知っている
   worker が now に生きていて沈黙の列に無く、名乗り・job の報告・task が、時刻の欄(最後の連絡の時刻・報告の時刻・task の lease の
   期限の延長)を除いて前と同じ。調停はこの 3 つの欄を「生きているか・期限を過ぎたか」でしか読まず、before の調停の時点で生きていて
   期限の内だった物は、延びても同じ判断になる。"
  (setv prev (.row before.workers name) info (.row heard.workers name)
        old-report (.row before.observations.statuses name) new-report (.row heard.observations.statuses name))
  (and (is-not prev None) (is-not info None) (is-not old-report None) (is-not new-report None)
       (not-in name before.silent)
       (alive now prev timing.lease-ms)
       (= (replace info :last-seen-ms prev.last-seen-ms) prev)
       (= (replace new-report :at old-report.at) old-report)
       ;; task は変わった鍵(changed-keys — 基を分け合う表は書いた行だけ読む)だけ比べる(task 387 本で replace が 774 回 → 0〜6 回・Q1)。
       ;; 行の数が違えば静かでない。
       (= (.size heard.tasks) (.size before.tasks))
       (all (gfor k (.changed-keys heard.tasks before.tasks)
              (let [t (.row heard.tasks k) o (.row before.tasks k)]
                (and (is-not t None) (is-not o None) (= (replace t :lease-until-ms 0) (replace o :lease-until-ms 0))))))
       (= (replace heard :workers before.workers :tasks before.tasks
                         :observations (replace heard.observations :statuses before.observations.statuses))
          before)))


(defk respond-legacy [state request body parts now timing [settled False]]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)
         (: settled bool)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "旧い口(/jobs・/heartbeat・/state)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 旧い口 ---
    (and (= method "PUT") (= parts ["jobs"]))
      (do (setv actor (require-actor (or request.actor body.actor)))
          (setv #(after status reply) (legacy-put-jobs state body.rows actor))
          #((if (is after state) state (! (settle state after actor now timing))) status reply))
    (and (= method "POST") (= parts ["heartbeat"]) (is-not (format-version-refusal body.format) None))
      #(state 400 (ErrorReply :message (format-version-refusal body.format)))
    (and (= method "POST") (= parts ["heartbeat"]))
      ;; settled = 呼び手が state を同じ now で調停済みと保証する(coordinator の 1 歩の中 — 拍の頭の tick の後)。その時だけ、静かな
      ;; heartbeat は調停を繰り返さない(quiet-heartbeat・#2655)。
      ;; 止まり始めを名乗る heartbeat(#2819)は、写した後にその世代の drain の頼みとして通す — drain が増えた heartbeat は
      ;; quiet-heartbeat の比べ(drains も見る)で静かでなくなり、同じ歩で調停が置き先を外す。
      (do (setv name body.name
                heard (absorb-stopping (! (register-heartbeat state body now)) name body.boot body.stopping now))
          (setv after (if (and settled (quiet-heartbeat state heard name now timing)) heard (! (settle state heard name now timing)))
                reply (! (heartbeat-reply after name timing (ready-instances after name now timing) :now now
                                          :boot body.boot :statuses body.statuses)))
          ;; 返事で付けた「途絶しても動かし続けてよい」印を、印を渡した担い手との約束として残す(#2804 — 返事の前に状態と一緒に保存され、
          ;; 担い手が印を持たないと知らせるまで job を他へ移さない)。
          #((! (remember-keep-marks after name body.boot body.kept-when-cut-off reply now)) 200 reply))
    (and (= method "GET") (= parts ["state"]))
      #(state 200 (StateReply :view (! (state-view state now timing)) :audit (tuple (cut state.audit -30 None))
                              :drains (drains-view state now timing)))
    True (unknown-request state request)))


(defk respond-workers [state request body parts now timing]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "worker の読みと drain の頼み(/workers の下・drain_policy)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- worker の drain(2026-09-25 — drain_policy)---
    (and (= head "workers") (= (len parts) 2) (= method "GET"))
      #(state 200 (worker-view state (get parts 1) now timing (ready-instances state (get parts 1) now timing)))
    (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "POST"))
      (do (setv actor (require-actor request.actor))
          (setv after (! (settle state (request-drain state (get parts 1) body actor now) actor now timing)))
          ;; 今の世代でない頼み(退いた世代・見ていない世代の preStop)には、その世代の待ちの答え(drain_policy.superseded-worker-view)。
          #(after 200 (if (other-generation-boot after (get parts 1) body.boot)
                          (superseded-worker-view after (get parts 1) body.boot now timing)
                          (worker-view after (get parts 1) now timing))))
    (and (= head "workers") (= (len parts) 3) (= (get parts 2) "drain") (= method "DELETE"))
      (do (setv actor (require-actor request.actor))
          (setv after (! (settle state (cancel-drain state (get parts 1)) actor now timing)))
          #(after 200 (worker-view after (get parts 1) now timing)))
    True (unknown-request state request)))


(defk respond-board [state request body parts now timing]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "盤の読み書き(/board)と lease の書き(/leases)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "GET") (= parts ["board"]))
      (do (setv prefix (.get request.query "prefix" ""))
          #(state 200 (BoardRead :entries (tuple (gfor #(k row) (sorted (.items state.board)) :if (.startswith k prefix)
                                                       (BoardEntryView :key k :value row.value :version row.version)))
                                 :with-versions (bool (.get request.query "withVersions")))))
    (and (= method "POST") (= head "leases") (= (len parts) 2))
      (! (lease-write state (get parts 1) body now))
    (and (= method "PUT") (= head "board") (> (len parts) 1))
      (! (board-write state (.join "/" (cut parts 1 None)) body now))
    True (unknown-request state request)))


(defk respond-tasks [state request body parts now timing]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "task の頼み・問い・結果・取り下げ(/tasks の下)に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    (and (= method "POST") (= parts ["tasks"]))
      (do (setv #(after status reply) (! (submit-task state body now)))
          ;; 断った本文(400・429)は状態を変えない — 調停も通さず同じ状態を返す。
          #((if (is after state) state (! (settle state after (loose-actor request) now timing))) status reply))
    (and (= method "GET") (= head "tasks") (= (len parts) 2)) (! (poll-task state (get parts 1) now))
    ;; task の子 process が終わる前に直に届ける結果(#1387 — cluster_policy.absorb-task-result)。
    (and (= method "POST") (= head "tasks") (= (len parts) 3) (= (get parts 2) "result"))
      (do (setv #(after status reply) (! (absorb-task-result state (get parts 1) body now)))
          #((if (is after state) state (! (settle state after (loose-actor request) now timing))) status reply))
    (and (= method "DELETE") (= head "tasks") (= (len parts) 2))
      #((! (settle state (replace state :tasks (.with-writes state.tasks #((TableWrite (get parts 1) None))))
                   (loose-actor request) now timing))
        200 (TaskDropped :id (get parts 1)))
    True (unknown-request state request)))


(defk respond-stores [state request body parts now timing]
  {:pre [(: state ClusterState) (: request Request) (: body (| RequestBody ServiceBody LegacyJobs)) (: parts list) (: now int) (: timing ClusterTiming)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "温める表(/warm)・詰めた Program の置き場(/programs)・切り離した task(/detached)と、その終わりの出口(/outbox)の要求に答えるため。#(次の状態 status 本文) を返し、知らない形は 404(respond の振り分けの 1 群 — 1 つの cond では型検査が解析をあきらめた・#1690)。"
  (setv method request.method
        head (get parts 0))
  (cond
    ;; --- 実行環境の温める表(2026-09-26 — warm_policy)---
    (and (= method "POST") (= parts ["warm"]))
      (do (setv actor (loose-actor request)
                #(after status reply) (warm-write state body now actor timing))
          #((if (is after state) state (! (settle state after actor now timing))) status reply))
    (and (= method "GET") (= head "warm") (= (len parts) 2)) (warm-read state (get parts 1) now timing)
    ;; --- 詰めた Program の置き場(2026-09-27 — program_policy・改訂 1 の F)---
    (and (= method "PUT") (= head "programs") (= (len parts) 2))
      (do (setv actor (loose-actor request)
                #(after status reply) (program-write state (get parts 1) body now))
          #((if (is after state) state (! (settle state after actor now timing))) status reply))
    (and (= method "GET") (= head "programs") (= (len parts) 2)) (program-read state (get parts 1))
    ;; --- 切り離した task(2026-09-25 — detached_policy)---
    (and (= method "PUT") (= head "detached") (= (len parts) 2))
      (! (detached-reply state (submit-detached state (get parts 1) body now) request now timing))
    (and (= method "GET") (= head "detached") (= (len parts) 2))
      (! (detached-reply state (detached-read state (get parts 1) now timing) request now timing))
    (and (= method "POST") (= head "detached") (= (len parts) 3) (= (get parts 2) "cancel"))
      (! (detached-reply state (cancel-detached state (get parts 1) now) request now timing))
    (and (= method "DELETE") (= head "detached") (= (len parts) 2))
      (! (detached-reply state (release-detached state (get parts 1)) request now timing))
    ;; --- 切り離した task の終わりの出口(2026-10-21・card ki-e7945a2b2d00 — detached_policy の出口の節)---
    (and (= method "GET") (= parts ["outbox"]))
      (! (detached-reply state (outbox-read state (str (.get request.query "prefix" ""))) request now timing))
    (and (= method "DELETE") (= head "outbox") (= (len parts) 2))
      (! (detached-reply state (outbox-ack state (get parts 1)) request now timing))
    True (unknown-request state request)))


(defk respond [state request now timing body [settled False]]
  {:pre [(: state ClusterState) (: request Request) (: now int) (: timing ClusterTiming) (: body (| RequestBody ServiceBody LegacyJobs BodyUnreadable))
         (: settled bool)]
   :post [(: % tuple)] :tags {:context "coordinator" :role "judgment"}}
  "要求 1 件と、その本文を道の型に解いた値(coordinator/protocol/request_bodies の body-of — 型の値・まだ型にしていない道は JSON の
   object・形が合わなければ BodyMalformed・読みの中で例外が上がれば BodyUnreadable)→ #(次の状態 status 本文)。settled = state を同じ
   now で調停済みと呼び手が保証する(coordinator の 1 歩の中だけ — 静かな heartbeat の早道 quiet-heartbeat の前提・#2655)。"
  (setv method request.method
        parts (list request.parts)
        head (get parts 0))
  (try
    (when (isinstance body BodyMalformed)
      (raise (BodyInvalid body.reason)))
    ;; 本文の読みの中で上がった例外は、この囲みで上げ直して下の except で分ける — 1 件の要求の欠陥で調停ループを止めない(#2796)。
    (when (isinstance body BodyUnreadable)
      (raise body.error))
    (match head
      "resources" (! (respond-resources state request body parts now timing))
      (| "metrics" "events") (respond-observations state request body parts now timing)
      (| "jobs" "heartbeat" "state") (! (respond-legacy state request body parts now timing :settled settled))
      "workers" (! (respond-workers state request body parts now timing))
      (| "board" "leases") (! (respond-board state request body parts now timing))
      "tasks" (! (respond-tasks state request body parts now timing))
      (| "warm" "programs" "detached" "outbox") (! (respond-stores state request body parts now timing))
      _ (unknown-request state request))
    (except [refused Refused]
      #(state refused.status refused.body))
    (except [invalid BodyInvalid]
      #(state 400 (ErrorReply :message (str invalid))))
    ;; 送り手の誤りの型(Refused・BodyInvalid)でない例外は coordinator の中の欠陥 — 400 に畳まず 500 の Fault で返す。状態は受ける前の
    ;; まま(途中まで進めた変化を残さない)。log の 1 行は coordinator-step が CoordinatorFault で出す(#1024)。
    ;; where = 例外が上がった一番内側の所(file:行 関数)。
    (except [error Exception]
      (setv inner (get (extract-tb error.__traceback__) -1))
      #(state 500 (Fault method request.path (. (type error) __name__) (str error)
                         (.format "{}:{} {}" (get (.rsplit (.replace inner.filename "\\" "/") "/" 1) -1) inner.lineno inner.name))))))
