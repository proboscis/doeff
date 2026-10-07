;; 共有の disk の空きが最低を割った時の掃除の判断(純粋 — worker/core/env_upkeep.hy・#4051)。
;;
;;   選び     空きが最低を割る間は、候補(固定でない・project の新しい 2 つの外・worker が作った root)を最後に使った時刻の古い順に、
;;            空きが最低に戻るまで選ぶ(roots の合計が上限を越えた時の選びと同じ形で、量を空きで数える)。project の新しい 2 つは選ばない。
;;   起こし   数えの結び(RootsTally)に「数えた時に空きが最低を割っていたか」を持ち、今の空きの状態と違えば数え直す。空きが割ったままの
;;            間は、固定の集合が変わった時(job が止まって root が候補になる)と完成した root の集合が変わった時だけ数え直す — 時刻では
;;            繰り返さない(候補が増えない限り数え直しても選べる物は同じ)。
;;
;; 数は本番の実例(2026-10-08 00:33・node k3s-0 の worker — free_bytes=26796892160・cap_bytes=21474836480・pinned=1・
;; candidates=2・chosen=0。04:01 の空き 26755080192 byte と最低 26843545600 byte〔WORKER_ENV_MIN_FREE_GIB 25〕)。
;; 使われていない root 3 つは合わせて約 623M(1 つ約 208M)。
(require doeff-hy.macros [deftest val])
(import doeff_cluster.worker.core.env_upkeep [RootInfo RootsTally sweep-choice sweep-due sweep-wanted SWEEP-EVERY-MS])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "judgment"})

(val GIB (** 2 30))
(val CAP (* 20 GIB))
(val MIN-FREE (* 25 GIB))
(val FREE 26755080192)
(val ROOT-BYTES 207618048)
;; 同じ project の root 4 つ: now = 走っている job の root(固定)・prev = 戻し先(project の新しい 2 つの 2 番目)・c2 と c1 = 候補(c1 が古い)。
(val ROOTS #((RootInfo :key "env-c1" :project "p" :made-ms 10 :last-used-ms 100 :bytes ROOT-BYTES :owned True)
             (RootInfo :key "env-c2" :project "p" :made-ms 20 :last-used-ms 200 :bytes ROOT-BYTES :owned True)
             (RootInfo :key "env-prev" :project "p" :made-ms 30 :last-used-ms 300 :bytes ROOT-BYTES :owned True)
             (RootInfo :key "env-now" :project "p" :made-ms 40 :last-used-ms 400 :bytes ROOT-BYTES :owned True)))
(val PINNED (frozenset #("env-now")))


(deftest test-a-low-free-disk-chooses-the-oldest-candidate-until-the-minimum-is-back
  ;; 空き 26755080192 < 最低 26843545600(足りないのは 88465408 byte ≈ 84 MiB)・roots の合計 0.83 GB ≤ 上限 20 GiB。候補 2 のうち古い c1 を
  ;; 1 つ消せば最低に戻るので、c1 だけを選ぶ。戻し先 prev と固定の now は選ばない。直す前の選びは上限だけを見て何も選ばなかった。
  (assert (= (! (sweep-choice ROOTS PINNED CAP FREE MIN-FREE)) #("env-c1")))
  ;; 足りない量が 1 つでは戻らない時は、候補を古い順に全部選ぶ(新しい 2 つは残す)。
  (assert (= (! (sweep-choice ROOTS PINNED CAP (- MIN-FREE (* 3 ROOT-BYTES)) MIN-FREE)) #("env-c1" "env-c2")))
  ;; 空きが最低の上で、合計が上限の内なら何も選ばない(#3732 の上限の選びはそのまま)。
  (assert (= (! (sweep-choice ROOTS PINNED CAP MIN-FREE MIN-FREE)) #()))
  ;; 空きが十分でも、合計が上限を越えれば今までどおり上限の内へ戻るまで選ぶ。
  (assert (= (! (sweep-choice ROOTS PINNED (* 3 ROOT-BYTES) (* 30 GIB) MIN-FREE)) #("env-c1"))))


(deftest test-a-drop-below-the-minimum-wakes-the-sweep-without-a-timer
  (val ready (frozenset #("env-c1" "env-c2" "env-prev" "env-now")))
  ;; 空きが最低の上だった時の数え(合計は上限の内)。
  (val roomy (RootsTally :ready ready :bytes (* 4 ROOT-BYTES) :below-min-free False))
  ;; 空きが最低を割っていた時の数え。
  (val short (RootsTally :ready ready :bytes (* 4 ROOT-BYTES) :below-min-free True))
  ;; 起こしの判断(sweep-due — 引数 tally ready cap low changed now-ms swept-ms)。
  (assert (! (sweep-due roomy ready CAP True False 1000 0)) "空きが最低を割った(数えた時は割っていなかった)ので数え直す")
  (assert (not (! (sweep-due short ready CAP True False SWEEP-EVERY-MS 0))) "割ったまま・固定も集合も同じなら、間隔が経っても数えない")
  (assert (! (sweep-due short ready CAP True True 1000 0)) "割ったまま固定が変われば数え直す(止まった job の root が候補になる)")
  (assert (! (sweep-due short ready CAP False False 1000 0)) "空きが戻れば数え直して結びを持ち替える")
  (assert (not (! (sweep-due roomy ready CAP False True 1000 0))) "空きが最低の上・合計が上限の内なら、固定が変わっても数えない")
  ;; heartbeat の観測(sweep-wanted — 引数 running tally ready cap low)。
  (assert (! (sweep-wanted False roomy ready CAP True)) "空きが最低を割れば掃除の係が SweepEnvs を求める")
  (assert (not (! (sweep-wanted False short ready CAP True))) "割ったままで数え済みなら求めない(固定が変われば判断の側が SweepEnvs を出す)")
  (assert (not (! (sweep-wanted False roomy ready CAP False))) "空きが最低の上・上限の内で集合も同じなら求めない"))
