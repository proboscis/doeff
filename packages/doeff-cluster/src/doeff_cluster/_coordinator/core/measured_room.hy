;;; worker に task をあと幾つ置けるかを、worker が heartbeat で名乗る機体の測った空き(measured — CPU の使い・memory の空き・PSI)から
;;; 数える純粋な判断。task の置き先(cluster_policy.place-tasks)と GET /state の taskRoom が cluster_policy.task-room-of を通して読む。
;;;
;;; 以前は worker ごとに手で書いた数(枠の file の taskReserve・backgroundRoom)から置いた task を引いた数を空きとした。手の数は機体の
;;; 実際の混み(他の Pod の CPU・memory の残り)を見ず、機体を足すたびに書き足し、混みの事故のたびに分け方を直した。今は:
;;;   空き = min(CPU の空き − まだ測りに出ていない task, memory の空き − まだ測りに出ていない task, 上限 − 置いた task)
;;;   CPU の空き     = ⌊(cpus × (1 − cpu-busy) − CPU-FLOOR-CORES) / TASK-CORES⌋
;;;   memory の空き  = ⌊(memory-available − MEMORY-FLOOR-MIB) / TASK-MEMORY-MIB⌋
;;;   上限           = ⌊cpus / TASK-CORES⌋(task-cap-of — 測りが遅れても CPU の数より多くは置かない)
;;;   まだ測りに出ていない task = 置いた task のうち、測った空きを載せた同じ heartbeat の状態の報告で動いていない物と、測った刻 −
;;;                               TASK-RAMP-MS より後に置いた物(置いた直後・走り出した直後の task は CPU と memory をまだ使いきって
;;;                               いない — 呼び手 cluster_policy.load-of が WorkerLoad.unseen に数える)
;;; CPU の PSI の some avg10 が CPU-PRESSURE-STOP 以上か、memory の some avg10 が MEMORY-PRESSURE-STOP 以上の間は 0(実行を待つ process が
;;; もう出ている機体へ足さない)。測った空きを名乗らない worker(/proc の無い機体・測りの前の版の worker)と、CPU の使いをまだ数えられない
;;; 読み(cpu-busy None — 初めの読み)は 0。
;;;
;;; memory の空き(memory-available): worker が名乗る値。kubelet の下の worker は、kubelet が Pod を追い出す線までの空き(Pod の全部の
;;; working set と allocatable の差・機体の線の近い方 — worker/core/eviction_rules)を名乗り、それ以外の worker は MemAvailable を名乗る。
;;; 機体の MemAvailable が残っていても、allocatable の線を越えれば kubelet は worker の Pod を丸ごと追い出す(atlas の 10-30〜11-01 の
;;; 5 回)ので、置き先は追い出しまでの空きで数える。
;;;
;;; 常駐の job の分: 測った CPU の使いと memory の空きには、常駐の job が今使っている CPU と memory が入っている。空きはその残りから数えるので、
;;; 常駐の job の測った使用量の分を残して task を置く(手書きの割合や数で残さない)。置いた後に task が膨らむ分は、機体の中の cgroup の層
;;; (常駐の job の memory.min と CPU の重み・追い出しまでの空きから worker が掛け直す jobs の層の memory.max)が kernel で守る。数の値は
;;; 機体ごとに書かず、どの機体にも同じ値を使う。
;;;
;;; 優先 0 以上の task と背景の仕事(負の優先の task)の取り分の守り(cluster_policy.task-misfit)は、予約(reserve-of = 上限 /
;;; RESERVE-SHARE)を半分ずつに分けて読む。RESERVE-SHARE は、36 core の機体の予約が手で書いていた 6(半分ずつ 3)と同じになる値。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import math [floor])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])

;; task 1 本に見込む CPU の数と memory(MiB)。会話の test や build の 1 本は、混んでいない機体で CPU 1 つ・memory 1 GiB 前後を使う。
(val TASK-CORES 1)
(val TASK-MEMORY-MIB 1024)
;; 機体に残す空いた CPU と memory。CPU は常駐の job の揺れと、測りの間の増えの分。memory はその 512 MiB に、worker が jobs の層の上限で
;; 追い出しの線の手前に残す余裕 1024 MiB(worker/core/eviction_rules の EVICTION-MARGIN-BYTES — task は追い出しまでの空きからこの余裕を
;; 引いた分までしか使えない)を足した値。
(val CPU-FLOOR-CORES 1)
(val MEMORY-FLOOR-MIB 1536)
;; PSI の some avg10(%)がこの値以上の機体には置かない。CPU は実行を待つ process が出ている時間の割合、memory は回収で止まった時間の割合。
(val CPU-PRESSURE-STOP 40.0)
(val MEMORY-PRESSURE-STOP 10.0)
;; 走り出した task の使いが測りに出きるまでの見込みの長さ(worker の測りの間 5 秒の 2 回分 — CPU の使いは前の読みとの間の増えで数える)。
(val TASK-RAMP-MS 10000)
;; 予約 = 上限のこの分の 1。
(val RESERVE-SHARE 6)


(defk task-cap-of [cpus]
  {:pre [(: cpus int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "CPU の数 cpus の機体に同時に置ける task の上限を知るため: ⌊cpus / TASK-CORES⌋。測りが遅れて CPU の空きが多く見える間も、これより
   多くは置かない。"
  (// cpus TASK-CORES))


(defk unseen-since [measured]
  {:pre [(: measured (| MeasuredLoad None))] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "置いた task が worker の測りにまだ出ていないと数える刻の起点を知るため(この刻より後に置いた task は、動いていると報告されていても
   数える — 刻は task の started-ms と比べる):
   測った刻 − TASK-RAMP-MS。測りの無い worker は None(数えない — 空きは 0)。"
  (if (is measured None) None (- measured.at-ms TASK-RAMP-MS)))


(defk pressured? [measured]
  {:pre [(: measured MeasuredLoad)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "機体に実行や回収を待つ process がもう出ているかを知るため: CPU の some avg10 ≥ CPU-PRESSURE-STOP か memory の some avg10 ≥
   MEMORY-PRESSURE-STOP。PSI の無い機体(None)は待ちを見ない。"
  (or (and (is-not measured.cpu-some-avg10 None) (>= measured.cpu-some-avg10 CPU-PRESSURE-STOP))
      (and (is-not measured.memory-some-avg10 None) (>= measured.memory-some-avg10 MEMORY-PRESSURE-STOP))))


(defk measured-room [measured placed unseen]
  {:pre [(: measured (| MeasuredLoad None)) (: placed int) (: unseen int)] :post [(: % int) (>= % 0)]
   :tags {:context "coordinator" :role "judgment"}}
  "worker に task をあと幾つ置けるかを機体の測った空きから知るため(頭の註の式 — 0 以上): measured = heartbeat の測った空き(None =
   名乗らない)・placed = その worker に置いた task の数・unseen = そのうち測りにまだ出ていない数。測りの無い・CPU の使いをまだ数えられない・
   PSI の待ちが出ている機体は 0。"
  (when (or (is measured None) (is measured.cpu-busy None))
    (return 0))
  (when (! (pressured? measured))
    (return 0))
  (val idle-cores (- (* measured.cpus (- 1 measured.cpu-busy)) CPU-FLOOR-CORES))
  (val cpu-room (- (floor (/ idle-cores TASK-CORES)) unseen))
  (val memory-room (- (// (- measured.memory-available-MiB MEMORY-FLOOR-MIB) TASK-MEMORY-MIB) unseen))
  (val cap-room (- (! (task-cap-of measured.cpus)) placed))
  (max 0 (min cpu-room memory-room cap-room)))


(defk reserve-of [measured]
  {:pre [(: measured (| MeasuredLoad None))] :post [(: % int) (>= % 0)] :tags {:context "coordinator" :role "judgment"}}
  "worker の予約(優先 0 以上の task と背景の仕事に半分ずつ残す数 — cluster_policy.foreground-reserve-of・background-reserve-of)を
   知るため: 上限(task-cap-of)の ⌊1 / RESERVE-SHARE⌋。測りの無い worker は 0。"
  (if (is measured None) 0 (// (! (task-cap-of measured.cpus)) RESERVE-SHARE)))
