;;; Executable ADR: doeff-cluster の coordinator は、要求の無い間、次に判断の答えが変わる刻か、要求か、停止か、外の出来事まで
;;; 1 本で待つ — 1 秒ごとに起きて見に行かない。判断は期限ちょうどの刻に出る(1 秒の格子に丸めない)。
;;;
;;; 出自 = 利用者の決定 2026-10-06(原文は :problem の fact)を、Mac の調整役が 2026-10-07 に「本番も期限まで眠る」の答えと読んだ
;;; (解釈である事を :problem に書く)。追跡 = agora-redesign #3865。
;;;
;;; 段: 単位 1(期限の答えの型・now + 1 の書き直し・次に起きる刻の純粋な判断と落ち着くまでの約束)は判断だけで、待ち方は変えない。
;;; 単位 2 で調停ループ(core/program.hy の run-coordinator)を繋ぎ、1 秒の周期の設定(TICK-MS)とそれで起きる code(模擬の格子の上の
;;; 静かな区間 — idle_policy)を同じ変更で消した(単位 2b)。worker の代役が眠る間の heartbeat は、模擬の受付の列がその刻に要求として渡す。
;;;
;;; 戻し方: この ADR と単位 2 の commit を revert する(本番は 1 秒ごとの拍へ、模擬は格子の上の静かな区間へ戻る)。単位 1 の
;;; 判断(期限の答えの型)は、待ち方を変えないので残してよい。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import doeff [run])
(import pathlib [Path])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming BoardRow])
(import doeff_cluster.shared.intent.due_model [DueNow DueNever])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-due task-due sweep-due])
(import doeff_cluster.coordinator.core.api_policy [tick-due])
(import doeff_cluster.coordinator.core.wake_policy [next-wake])


;; ratchet 台帳 — 期限の関数の file に、数の (+ now 1) を期限として書く所の数(2026-10-07・単位 1a の後の実測)。
(val CORE "packages/doeff-cluster/src/doeff_cluster/coordinator/core")
(val NOW-PLUS-ONE-ROSTER
  {"cluster_policy.hy" 0
   "api_policy.hy" 0
   "wake_policy.hy" 0})   ; rollout-due の Rollout の歩の刻の下限は #3868 で消した(R5)


(defadr ADR-DOE-CLUSTER-002
  :title "doeff-cluster の coordinator は、要求の無い間、次に判断の答えが変わる刻(期限ちょうど)・要求・停止の合図・外の出来事のどれかまで 1 本で待つ。1 秒ごとに起きて見に行く拍を持たない。期限の関数は答えを「刻・今すぐ・無し」の閉じた型で返し、落ち着いた状態では今すぐを返さない"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/shared/intent/due_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/shared/core/due_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/intent/due_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/core/wake_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/core/api_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/core/program.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/core/rollout_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/protocol/request_queue.hy"
          "packages/doeff-cluster/src/doeff_cluster/coordinator/protocol/kube.hy"
          "packages/doeff-cluster/src/doeff_cluster/foundation/kube_client.hy"
          "docs/adr/defadr_doeff_cluster_002_coordinator_waits_until_the_next_due.hy"]
  :problem
    [(fact
       "利用者の決定 2026-10-06(原文): \"so anything that require polling, are to be fixed. polling is a last resort\""
       :evidence "agora-redesign #3865 の本文(cc3-w56 が見つけ、cisco-c8 が引いた)")
     (fact
       "2026-09-30 の決定(agora-redesign #1383 comment 5896513586・議論の会話 w3J:pE・戻せる決定)は、次の期限まで眠る形を模擬の時計の下だけに入れ、本番の拍の間隔 1 秒と判断の刻を変えず、本番も期限まで眠る形(案 1)を operator の判断として残した。本番の 1 秒は tests/test_idle_skip.hy の test-the-production-inbox-keeps-the-one-second-tick(commit 86c8b2f94)が縛っていた。"
       :evidence "agora-redesign #1383・#1522")
     (fact
       "Mac の調整役は 2026-10-07 に、2026-10-06 の利用者の原文を、残された判断(案 1)への答えと読み、本番も次の期限まで眠る形で進めると決めた — これは Mac の調整役の解釈で、利用者が案 1 を名指して選んだ記録ではない。"
       :evidence "agora-redesign #3865 と #1383 の 2026-10-07 の comment(cisco-c8 が書いた)")
     (fact
       "coordinator は要求が無くても 1 秒ごとに起きて 1 歩を回す(core/program.hy の coordinator-step が IdleNextRequests に TICK-MS 1000 を渡し、本番の受付 shared/protocol/inbox.hy の http-requests が箱を 1 秒で打ち切る)。期限の関数は「まだ落ち着いていない」と「行が在れば次の拍」を数の now + 1 で返していた(cluster_policy.hy の liveness-due・task-due・sweep-due・idle_policy.hy の rollout-due)。"
       :evidence "doeff origin/main e604d71de3 の行(agora-redesign #3865 の見直しの comment)")]
  :context
    [(interpretation
       "期限の関数が返す刻は、判断が比べに使う期限の値と同じ関数から求める(#1383 の条件 (1))。期限ちょうどで起きる形では、刻が 1 ms 早いと起きた時に判断が変わらず、関数が now より後の刻だけを見るので本当の変化の刻を飛ばす。遅いと判断が遅れる。1 秒の格子はこのずれを隠していた。")
     (interpretation
       "数の now + 1 は「1 ms 先の期限」と「すぐもう 1 歩」を見分けられない。答えを閉じた型に分ければ、待ち方を決める側(調停ループ)が今すぐの歩と期限までの待ちを名で分けられる。")]
  :decision
    [(rule R1 "coordinator は、何も変えない歩の後、次に起きる刻(core/wake_policy.hy の next-wake — 要求の無い歩の期限・Rollout の期限・返事を待たせている待ちの期限のいちばん早い答え)まで受付を 1 本で待つ。要求・停止の合図・外の出来事は待ちを起こす。1 秒の周期の設定(TICK-MS)と、それで起きる code は持たない(単位 2b で消した)。")
     (rule R2 "判断は期限ちょうどの刻に出る。1 秒の格子に丸めない(格子を残して次の格子まで待つ形は、周期の定数が残るので採らない — #3865 の見直しの追記)。")
     (rule R3 "期限の関数の答えは閉じた型 DueAt(刻)・DueNow(今すぐ — 今の刻で判断すれば状態が変わる)・DueNever(状態がこのままなら時刻では変わらない)。数の now + 1 を返さない。落ち着いた状態(要求の無い歩をもう 1 歩進めても変わらない)では DueNow を返さない。")
     (rule R4 "要求を受けずに状態を変えた歩の後は、待たずにもう 1 歩進める(after-step)。要求を受けた歩の後は、次に起きる刻まで待つ — 要求で変わった状態が落ち着いていなければ期限の関数が DueNow を返す(R3)ので、要求ごとに空の歩を回さない(2026-10-07 の直し A・cisco-c8 の可。前は要求を受けた歩の後も必ずもう 1 歩回していた)。DueNow が UNSETTLED-STEP-LIMIT 歩を越えて続いたら、前後の状態で違う欄を名指して CoordinatorUnsettled で落ちる(黙って回り続けない)。")
     (rule R5 "Rollout の相手の Kubernetes の Deployment は時間で読みに行かない(agora-redesign #3868 — 以前の Rollout の歩の間隔の下限 ROLLOUT-TICK-MS の 1 秒ごとの読みと、台数を持つ相手の 10 秒ごとの読み直しは消した)。coordinator は Rollout が観測を要る Deployment を list の後の watch で見張り(FollowDeployments・foundation/kube_client の follow)、見張りが変化を受け渡した時に受付の箱を起こす(外の出来事 — R1)。worker の置かれた node の label も時間で読みに行かない(agora-redesign #4070 — 以前の 60 秒ごとの読み直し NODE-LABELS-TTL-MS と、その期限 node-reread-due は消した)。coordinator は heartbeat で申告された node を同じ形で見張り(FollowNodes・foundation/kube_client の follow-node)、label の変化を受け渡した時に受付の箱を起こす。Rollout の歩は毎歩回るので、rollout-due は now より後の期限だけを返す。成功した Deployment への書きは、その書きの後の観測が届くまで出し直さない(rollout_policy.action-due — 見張りが伝える前の古い観測で同じ書きを重ねない。1 秒の間隔がこれを隠していた)。失敗した action(印の annotation を含む)は最後の失敗から倍々の間を空けて出し直し、その刻は終わった Rollout でも期限にする。見張りの stream は TCP の keepalive を付けた接続で受け、繋がったまま黙った相手を約 30 秒で読めなかった観測にする。")
     (rule R6 "置いた切り離していない task の期限は、担い手が reassign-after-ms の窓の外に出る刻(place-tasks と同じ比べ)。置ける生きた worker の在る待っている task の期限は、その worker が lease-ms の窓の外に出る最初の刻(空き・drain の終わりは要求と掃除の期限が受ける)。")
     (rule R7 "模擬だけの物(worker の代役が静かな拍を眠る事と、眠る前に預ける heartbeat)は受け手の層(模擬の受付の列 coordinator/protocol/request_queue.hy)に置く。列は預けた heartbeat をその刻に普通の heartbeat の要求として調停ループへ渡し、調停ループは本番と同じ要求だけを受ける(調停ループに模擬の材料を渡す effect を持たない — 前の IdleNextRequests・IdleTaken は消した)。")]
  :laws
    [(law due-answers-are-closed
       :statement "for_all 期限の関数 f ∈ {liveness-due・task-due・sweep-due・tick-due・rollout-due・next-wake}・状態 s・刻 now: f(s, now) ∈ DueAt ∪ DueNow ∪ DueNever"
       :counterexamples
         [(counterexample "liveness-due が生きていないと数える名の求め直しの違いを (+ now 1) で返す — 期限ちょうどで起きる形では 1 ms 先の期限と見分けられない(2026-10-06 の本線の形)")]
       :enforced-by ["packages/doeff-cluster/tests/test_due_answer.hy"
                     "packages/doeff-cluster/tests/test_next_wake.hy"]
       :wiring "配線済み(2026-10-07・単位 1a f459781cc)— 期限の関数の :post が閉じた型を縛り、test_due_answer が今すぐ・無しの答えを断言する")
     (law a-settled-state-is-never-due-now
       :statement "for_all 状態 s・刻 now: tick(tick(s, now), now) = tick(s, now) ⇒ tick-due(tick(s, now), now) ≠ DueNow"
       :counterexamples
         [(counterexample "置ける生きた worker の無い待っている task を DueNow にする — 全部の worker が埋まっている・黙っている間に、期限まで待つ形が待たずの歩を回り続ける")]
       :enforced-by ["packages/doeff-cluster/tests/test_due_answer.hy::test-a-settled-state-is-never-due-now"]
       :wiring "配線済み(2026-10-07・単位 1a)— 落ち着いた 4 つの筋書き。誤った形では赤を手元で確かめた")
     (law due-is-the-instant-the-judgment-changes
       :statement "for_all 期限の関数 f と、それが答える判断 j・状態 s・刻 now: f(s, now) = DueAt(D) ⇒ j(s, D − 1) = j(s, now) ∧ j(s, D) ≠ j(s, now)(答えを変え得る刻の下限を返すと宣言した関数は、前半だけ)"
       :counterexamples
         [(counterexample "期限の関数が判断の比べ(>)と 1 ms ずれた刻(>=)を返す — 期限ちょうどで起きた時に判断が変わらず、関数は now より後だけを見るので本当の変化を飛ばす")]
       :enforced-by ["packages/doeff-cluster/tests/test_idle_skip.hy(生死・掃除・task)"
                     "packages/doeff-cluster/tests/test_quiet_due_placement_rollout.hy(入れ替え・Rollout の段)"
                     "packages/doeff-cluster/tests/test_due_answer.hy(置いた task・待っている task)"
                     "packages/doeff-cluster/tests/test_next_wake.hy(待ちの期限)"
                     "packages/doeff-cluster/tests/test_due_boundaries.hy(readiness・止まり・node の読み直し)"]
       :wiring "配線済み(2026-10-07・単位 1b)")
     (law an-unsettled-loop-fails-loudly
       :statement "for_all 調停ループの歩の列: 続けて DueNow の歩の数 > UNSETTLED-STEP-LIMIT ⇒ CoordinatorUnsettled(文は前後の状態で違う欄の名を含む)"
       :counterexamples
         [(counterexample "今すぐが続く間、待たずに歩を回し続ける — CPU を使い続け、どの判断が落ち着かないかが分からない")]
       :enforced-by ["packages/doeff-cluster/tests/test_next_wake.hy::test-a-coordinator-that-never-settles-fails-naming-the-moving-field"
                     "packages/doeff-cluster/tests/test_coordinator_idle_wait.hy::test-a-loop-that-never-settles-fails-loudly"]
       :wiring "配線済み(2026-10-07・単位 2b)— 調停ループ run-coordinator が歩ごとに count-unsettled を通る(要求を受けた歩の変化は数えない)")
     (law the-coordinator-wakes-only-for-a-due-a-request-a-stop-or-an-event
       :statement "for_all 本番と模擬の coordinator・要求の無い区間 I: I の中で coordinator が起きる刻 ⊆ {next-wake の刻} ∪ {要求が届いた刻} ∪ {停止の合図の刻} ∪ {外の出来事の刻}"
       :counterexamples
         [(counterexample "受付の箱を 1 秒で打ち切り、要求の無い 3 秒に 3 歩回る(2026-10-07 の本線 — 壁の時計の模擬で実測)")]
       :enforced-by ["packages/doeff-cluster/tests/test_coordinator_idle_wait.hy"
                     "packages/doeff-cluster/tests/test_idle_skip.hy::test-a-quiet-system-steps-only-for-requests-and-deadlines"]
       :wiring "配線済み(2026-10-07・単位 2b)")
     (law a-rollout-follows-its-deployments-by-events
       :statement "for_all Rollout が Deployment の変化を待つ区間 I: I の中で coordinator が Kubernetes の Deployment を読みに行く数 = 0(見張りの始めの list と変化の出来事だけ)∧ Deployment が変わった刻 t に Rollout の処理ステージが進むなら、その刻は t(1 秒の格子に丸めない)"
       :counterexamples
         [(counterexample "Rollout の進行中は 1 秒ごとに Deployment を読みに行く(ROLLOUT-TICK-MS)— 静かな 20 秒に 19 回読み、Pod が揃った刻 …635370 ではなく次の格子 …636000 に StoppingOld へ進む(#3868 の失敗ケース 44bff9355)")
          (counterexample "成功した台数の書きの直後、見張りが結果を伝える前の古い観測で同じ書きを毎歩出し直す — 1 秒の間隔を消すと調停ループが落ち着かない(101 歩で CoordinatorUnsettled)")
          (counterexample "同じ action の失敗の繰り返しで lastAction の at を最初の失敗の刻のまま残す・印の annotation の失敗を間を空けずに出し直す — 間の上限(60 秒)の後と annotation の失敗は毎歩出し直しになる(#3868 のレビュー)")
          (counterexample "繋がったまま黙った watch の stream(FIN も RST も来ない)を読みの打ち切り(5 分半)まで待ち、その間の古い観測で判断する — TCP の keepalive で約 30 秒で見つける(#3868 のレビュー)")]
       :enforced-by ["packages/doeff-cluster/tests/test_rollout_kube_events.hy"
                     "packages/doeff-cluster/tests/test_kube_client_watch.hy"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-written-deployment-action-waits-for-an-observation-after-the-write"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-repeated-action-is-dated-at-its-last-attempt"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-failing-annotation-waits-for-the-retry-gap"
                     "packages/doeff-cluster/tests/test_kube_reads_off_loop.hy::test-the-emulated-k8s-wakes-the-loop-when-a-stalled-node-watch-answers"]
       :wiring "配線済み(2026-10-08・#3868 の単位 2)")
     (law a-node-label-is-followed-by-events
       :statement "for_all worker の置かれた node の label が変わらない区間 I: I の中で coordinator が Kubernetes の Node を読みに行く数 = 0(見張りの始めの list と変化の出来事だけ)∧ label が変わった刻 t に受付の箱が起き、その歩で worker の導いた能力が新しい label から導き直される(次の周期の刻まで古い能力を持ち続けない)"
       :counterexamples
         [(counterexample "node の label を 60 秒ごとに読み直す(NODE-LABELS-TTL-MS)— 静かな 150 秒に 2 回読み、会社の機体の label を外した刻の 1 秒後も worker が company-machine を持ち続ける(#4070 の失敗ケース b2f856817)")]
       :enforced-by ["packages/doeff-cluster/tests/test_node_label_events.hy"
                     "packages/doeff-cluster/tests/test_kube_client_watch.hy::test-a-node-is-listed-by-name-and-a-label-change-reaches-on-body"
                     "packages/doeff-cluster/tests/test_kube_reads_off_loop.hy::test-the-emulated-k8s-wakes-the-loop-when-a-stalled-node-watch-answers"]
       :wiring "配線済み(2026-10-08・#4070)")
     (law a-sleeping-stand-in-changes-no-decision
       :statement "for_all 筋書き: 期限だけで起きる走りと、余計に起こす走り(coordinator を 1 秒ごとに起こす)で、生存の印を外した耐久の状態の変わり目の列(判断とその刻)・置き場の最後の状態・筋書きの答えが等しい(worker の代役は本番と同じ待ちで周の間を待つ — agora-redesign #3871 の単位 5。前の形は代役が静かな拍を眠り、預けた heartbeat を列がその刻に渡した)"
       :counterexamples
         [(counterexample "預けた heartbeat を、起きた時にまとめて調停ループへ渡す — 判断の刻が heartbeat の刻からずれる(前の静かな区間の形はこれを本番の判断で試して隠していた)")]
       :enforced-by ["packages/doeff-cluster/tests/test_idle_skip.hy"]
       :wiring "配線済み(2026-10-07・単位 2b。眠る代役と預けの道は単位 5 で消した)")]
  :enforcement
    [(deftest test-adr-doe-cluster-002-due-answers-are-closed
       ;; 期限の関数は、期限の無い状態に DueNever・落ち着いていない状態に DueNow を返す(数の None・now + 1 ではない — R3)。
       (val timing (ClusterTiming))
       (val empty (ClusterState))
       (val expired (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms 100 :size 1)}))
       (val answers [(run (liveness-due empty 0 timing)) (run (task-due empty 0 timing)) (run (sweep-due empty 0 timing))
                     (run (tick-due empty 0 timing)) (run (next-wake empty 0 timing (ClusterNaming) #()))])
       (assert (= answers [(DueNever) (DueNever) (DueNever) (DueNever) (DueNever)]) answers)
       (assert (= (run (sweep-due expired 200 timing)) (DueNow)) "期限の過ぎた行の掃除は今すぐ"))
     (deftest test-adr-doe-cluster-002-no-number-now-plus-one
       ;; 針: 期限の関数の file に、数の (+ now 1) を期限として書く所は台帳を超えない(新設は赤・削り忘れも赤 — R3)。
       ;; wake_policy の Rollout の歩の刻の下限(max (+ now 1) …)は #3868 で消した(R5)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val counts (dfor name NOW-PLUS-ONE-ROSTER
                         name (.count (.read-text (/ repo-root CORE name) :encoding "utf-8") "(+ now 1)")))
       (assert (= counts NOW-PLUS-ONE-ROSTER)
               (+ "期限の関数の file の (+ now 1) の数が台帳と違う(ADR-DOE-CLUSTER-002 R3 — 今すぐは DueNow で返す。減らした便は "
                  "NOW-PLUS-ONE-ROSTER を同じ便で削る): " (str counts))))]
  :plans ["agora-redesign #3865"])
