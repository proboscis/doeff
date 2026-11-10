;;; worker に task をあと幾つ置けるか・どの大きさの task まで置けるかを、worker が heartbeat で名乗る機体の測った空き(measured — CPU の
;;; 使い・memory の空き・PSI・task ごとの memory の今の使い)と、task が名乗る memory の要り(SubmitDetached の memory-mib — 1 本の山の MiB)
;;; から数える純粋な判断。task の置き先(cluster_policy.place-tasks)と GET /state の taskRoom・taskMemoryRoomMiB が cluster_policy の
;;; task-room-of・task-memory-room-of を通して読む。
;;;
;;; 以前は worker ごとに手で書いた数(枠の file の taskReserve・backgroundRoom)から置いた task を引いた数を空きとした。手の数は機体の
;;; 実際の混み(他の Pod の CPU・memory の残り)を見ず、機体を足すたびに書き足し、混みの事故のたびに分け方を直した。今は:
;;;   数の空き       = min(max(CPU の空き − まだ測りに出ていない task, 層の床), memory の枠, 上限 − 置いた task)
;;;   CPU の空き     = ⌊(cpus × (1 − 普通の使い) − CPU-FLOOR-CORES) / TASK-CORES⌋
;;;   層の床         = task の層の PSI を名乗る worker は TASK-LAYER-FLOOR − 置いた task・名乗らない worker は 0(layer-floor-of)
;;;   普通の使い     = cpu-busy − cpu-nice(nice > 0 の process の分を除いた CPU の使い — normal-busy-of)
;;;   memory の枠    = ⌊memory の空き / MEMORY-SLOT-MIB⌋(予約の半分ずつの守りが数で読むための単位 — task の大きさの見込みではない)
;;;   上限           = ⌊cpus / TASK-CORES⌋(task-cap-of — 測りが遅れても CPU の数より多くは置かない)
;;;   memory の空き  = min(memory-available − MEMORY-FLOOR-MIB, jobs の層の空き − JOBS-ROOM-FLOOR-MIB)
;;;                    − Σ 置いた task の max(0, 要り − 今の使い)(MiB — memory-room-mib。jobs の層の空きを名乗らない worker は前の項だけ)
;;;   まだ測りに出ていない task = 置いた task のうち、測った空きを載せた同じ heartbeat の状態の報告で動いていない物と、測った刻 −
;;;                               TASK-RAMP-MS より後に置いた物(置いた直後・走り出した直後の task は CPU をまだ使いきっていない —
;;;                               呼び手 cluster_policy.load-of が WorkerLoad.unseen に数える)
;;; task は、数の空きが在り、要りが memory の空き以下の worker にだけ置く(要りの大きさは数でなく MiB で比べる)。
;;;
;;; memory の要りの予約(2026-11-02): 以前は memory の空きを ⌊(空き − 床) / 1 GiB⌋ 本と数え、置いてから 10 秒
;;; (TASK-RAMP-MS)だけ測りに出ていない task を引いた。日次の doeff の回は置いてから 15〜20 分かけて 0.9 → 5.2 GiB 超まで伸びるので、
;;; 置いた時の空きは後の伸びを含まず、伸びる前の空きを見て軽い task を同じ worker に詰め、重い回が伸びた時に見張りに止められた
;;; (11-02 12:05 の t27866)。今は置いた task ごとに「要り − 今の使い」(まだ伸びる分)を測った空きから引いて予約する。今の使いは worker が
;;; 測りの task-memory-MiB(見張りの最後の読みの task ごとの memory — task の木の rss の和)で名乗り、名乗られない task は 0(要りの全部を
;;; 予約する)。要りを名乗らない task(切り離さない task の RemoteJob・欄の前の保存の行)は UNDECLARED-TASK-MIB を要りと見込む。
;;;
;;; 測った山で数える要り(2026-11-10): 名乗った要りは送り手の見当で、実の山より小さい事がある(zeus の 11-09 16:52 UTC — 名乗り 3584 MiB の
;;; task 3 本を名乗る空き約 9.7 GiB に置き、8 分後に各 14 GiB を使った。予約は「要り − 今の使い」なので、名乗りを越えた伸びは置く判断にも
;;; 後の予約にも入らなかった)。coordinator は worker の測りが名乗る task ごとの今の使いの最大を行の山(peak-mib)に持ち、task の要りを
;;;   要り = max(名乗った要り, min(max(この task の山, 同じ形の行の山の最大), その worker の線 − 床))
;;; で数える(learned-need)。形(shape)は送り手が同じ程の memory を使う仕事に付ける印で、同じ形の行は保持の間の終わった行も数える —
;;; 名乗りを越えて伸びた task は自分の山で予約を続け、同じ形の次の task はその山の空きが在る worker にだけ置かれる。山は worker の線 − 床
;;; (memory-line-mib)で切る — 線より大きく測れた山(線の外の使いを数えた読み)で、その形の task がどの worker にも置けなくならない。
;;; 初めての形と形を名乗らない task は名乗った要りのまま。置いた後に名乗りと山を越えて伸びる分は、worker が jobs の層の上限で止める
;;; (worker/core/eviction_rules の頭の註 — 上に載る系が残す線の上・追い出しの線の手前)。
;;;
;;; CPU の PSI の some avg10 が CPU-PRESSURE-STOP 以上か、memory の some avg10 が MEMORY-PRESSURE-STOP 以上の間は 0(実行を待つ process が
;;; もう出ている機体へ足さない)。CPU の PSI は、task の層の値(task-cpu-some-avg10)を名乗る worker はその値、名乗らない worker は機体の値。
;;;
;;; 優先度を下げた process を数えない訳(2026-11-05): 上に載る系は自分の命令を SCHED_IDLE と nice 10 で走らせ、普通の優先度の task に CPU を
;;; 譲らせる。機体の CPU の使い(cpu-busy)は nice の分を使いに数え、機体の PSI の some は譲る側の process の実行の待ちでも立つので、譲る
;;; 命令が CPU を埋めると、普通の優先度の task が動ける機体を空き 0 と判じた(zeus: nice 19 core・user と system の和 12〜15 core・機体の
;;; some 76% の 3 時間、置かれない task が 47 本に積もり、版の配りが止まった。同じ時の task の層の some は 5.6%)。nice の分は、普通の優先度の
;;; task が起きれば奪える CPU として空きに数える。譲りは機体の中の cgroup の重みにも掛かる(兄弟の層の重みが同じなら、譲る命令の層も層ごとの
;;; 取り分を持つ)ので、置いた task が本当に動けるかは task の層の PSI で測り、待ちが閾値を越えれば 0 にする。memory の判定(空き・予約・
;;; memory の PSI)は優先度で分けない。測った空きを名乗らない worker(/proc の無い機体・測りの前の版の worker)と、CPU の使いをまだ数えられない
;;; 読み(cpu-busy None — 初めの読み)は 0。
;;;
;;; 層の床(2026-11-05): 他の service と同居する機体(16 core に常駐の service が載る機体)は、普通の優先度の service が CPU を 0.97〜1.00
;;; まで使い、機体の CPU の PSI some が平常で 40〜60% と止めの線を跨ぐ。機体の全体の値だけで数えると、その機体を要る task は混みの切れ目
;;; しか入れず、9 時間 1 本も置かれなかった(同じ日の 4 時間ごとの回が後ろに積もった)。task の層(job の cgroup)は配備が cgroup の重み
;;; (cpu.weight と、Pod の下の worker は Pod の CPU の requests)で取り分を持つので、機体が埋まっていても置いた task は取り分の CPU で
;;; 動ける。task の層の PSI を名乗る worker には、CPU の空きが 0 でも TASK-LAYER-FLOOR 本までは置く。取り分で足りるかは task の層の PSI が
;;; 測る — 置いた task 自身が実行を待てば(some ≥ CPU-PRESSURE-STOP)床も 0(pressured?)。床は CPU の判定だけを緩め、memory の判定(空き・
;;; 予約・memory の PSI)と上限はそのまま掛ける。task の層を名乗らない worker(層の無い機体・前の版)には床を掛けない(取り分を測れない)。
;;;
;;; memory の空き(memory-available)と総量(memory-total): worker が名乗る値。kubelet の下の worker は、kubelet が Pod を追い出す線までの
;;; 空き(Pod の全部の working set と allocatable の差・機体の線の近い方 — worker/core/eviction_rules)と線の大きさを名乗り、それ以外の
;;; worker は MemAvailable と MemTotal を名乗る。機体の MemAvailable が残っていても、allocatable の線を越えれば kubelet は worker の Pod を
;;; 丸ごと追い出す(atlas の 10-30〜11-01 の 5 回)ので、置き先は追い出しまでの空きで数える。線の大きさ − 床(memory-line-mib)は、その
;;; worker が空いた時に受けうる要りの最大 — needs の合うどの worker の線よりも大きい要りの task は、積む時に断る(待っても入らない)。
;;;
;;; jobs の層の空き(2026-11-09): task は jobs の層に入り、kernel はその層の memory.max で止める。worker はこの上限を追い出しまでの
;;; 空きから余裕(機体ごと・測った伸びで広がる)・準備の取り置き・会話の CLI の層への貸しを引き、container の中の空きでも縛って掛け直す
;;; (worker/core/eviction_rules の jobs-limit)ので、層の空きは追い出しまでの空き − 床よりずっと小さい時が在る。追い出しまでの空きだけで
;;; 数えると、atlas の 11-08 14:03 は空き約 5.6 GiB と見て要り 3584 MiB の task を置き、層の空きは負(上限 3173 MiB・使い 3361 MiB — 会話の
;;; CLI の層への貸し 4608 MiB と余裕 2766 MiB を引いた値)で、kernel が層の中の最大の process を OOM で止めた(同じ形で 3 回続いた)。
;;; jobs の層の空きを名乗る worker には、要りがその空き − JOBS-ROOM-FLOOR-MIB − 置いた task の予約にも収まる task だけを置く(名乗らない
;;; worker は追い出しまでの空きだけで数える)。層の空きには置いた task の今の使いが入っているので、予約は追い出しまでの空きと同じく引く。
;;;
;;; 長く待った task の取っておき(2026-11-10): 置き先は優先と id の順に task を見て、置けない task を飛ばして後ろの task を置く。小さい
;;; task が絶えず積まれる worker では、置いた task が終わって戻る空きを後ろの小さい task が先に取り、要りの大きい task の分の空きが溜まる
;;; 時が来ない(helios の 11-10 — 要り 5632 MiB の task が jobs の層の空き 約 4 GiB を待って 4 時間を越え、その間に要り 1〜3.5 GiB の task が
;;; 置かれ続けた。同じ形の要り 4096 MiB の task は 6 時間待った)。積んでから STARVE-WAIT-MS を越えて memory の空きだけで置けない task は、
;;; 置ける worker のうち、今の task が全部終われば入る見込み(drained-room-mib)の在る 1 台にその要りを取っておく(cluster_policy の
;;; starved-keeper)。同じ拍の後ろの順の task は、その worker の memory の空きから要りを引いて数える — 置いた task が終わった分は後ろの
;;; task に渡らず、待った task の分として溜まる。見込みの無い worker には取っておかない: 層の中の常駐の使いで入らない task のために worker を
;;; 塞ぎ続けない(見込みは置いた task の今の使いを足した値なので、task が終わるにつれて実の空きに近づき、入らないと分かれば取っておきは解ける)。
;;; 取っておきは拍ごとに数え直し、保存しない。
;;;
;;; 急ぎの task の取っておき(2026-11-10): 待った長さで取っておく形は、要りの同じ task どうしでは先に積んだ方を先に置く(置く順は優先と id)
;;; ので、名乗りで先へ出たい task も 30 分を待ち、その間に後ろの task が戻る空きを取り、待ちを越えても先に積んだ同じ大きさの task が先に
;;; 置かれた(11-10 — 要り 3584 MiB・優先 0 の task が、同じ要り・同じ優先の先に積まれた作り直せる task の後ろで 12 時間を越えて queued の
;;; まま待ち、その task の後ろに送り手の後の仕事が全部止まった)。優先 KEEP-NOW-PRIORITY 以上の task は、置く順で優先 0 以下の task より
;;; 先に見るうえ、memory の空きだけで置けなければ待った長さを問わずその拍から取っておく(keeps-room?)— 戻る空きは後ろの順の task に渡らず、
;;; 置いた task が終わるたびにその task の分として溜まる。急ぎを名乗るのは送り手の宣言で、系は値の意味を読まない。
;;;
;;; 急ぎの task の空き(2026-11-10): 上に載る系が残す線を名指す worker(worker/core/load_rules の頭の註の会話のターンに残す線)は、名乗る
;;; memory の空きと jobs の層の空きをその線の上に絞る。線は作り直せる task を上に載る系の仕事の後ろへ回す線で、急ぎの task まで後ろへ
;;; 回すと、その機体でしか走れない急ぎの task は線を越えて空きが戻るまで置かれない(zeus の 11-10 — 名乗る空き約 4 GiB・要り 6144 MiB の
;;; 急ぎでない task が queued のまま待った。同じ形の急ぎの task も同じに待つ)。線が空きを縮めている間、worker は線を外して追い出しの線
;;; だけで数えた空き(urgent-memory-available-MiB・urgent-jobs-room-MiB)も名乗る。優先 KEEP-NOW-PRIORITY 以上の task の memory の空きは
;;; その値で数え(memory-room-mib の urgent)、ほかの task は今までどおり線の上の空きで数える。名乗らない worker(線が縮めていない・前の版)は
;;; 急ぎの task も平常の空きで数える。追い出しの線の手前の余裕(MEMORY-FLOOR-MIB・JOBS-ROOM-FLOOR-MIB)はどちらにも掛ける。
;;;
;;; 常駐の job の分: 測った CPU の使いと memory の空きには、常駐の job が今使っている CPU と memory が入っている。空きはその残りから数えるので、
;;; 常駐の job の測った使用量の分を残して task を置く(手書きの割合や数で残さない)。置いた後に常駐の job が膨らむ分は、機体の中の cgroup の層
;;; (常駐の job の memory.min と CPU の重み・追い出しまでの空きから worker が掛け直す jobs の層の memory.max)が kernel で守る。数の値は
;;; 機体ごとに書かず、どの機体にも同じ値を使う。
;;;
;;; 優先 0 以上の task と背景の仕事(負の優先の task)の取り分の守り(cluster_policy.task-misfit)は、予約(reserve-of = 上限 /
;;; RESERVE-SHARE)を半分ずつに分けて読む。RESERVE-SHARE は、36 core の機体の予約が手で書いていた 6(半分ずつ 3)と同じになる値。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "coordinator" :role "judgment"})
(import math [floor])
(import doeff_cluster._coordinator.intent.request_bodies [MeasuredLoad])

;; task 1 本に見込む CPU の数。会話の test や build の 1 本は、混んでいない機体で CPU 1 つ前後を使う。
(val TASK-CORES 1)
;; memory の要りを名乗らない task に見込む要り(MiB)。要りを名乗る口の無い切り離さない task(RemoteJob — POST /tasks)と、要りの欄の
;; 前に保存した行だけが使う(切り離した task は要りを名乗る — detached_rules.task-memory-refusal)。
(val UNDECLARED-TASK-MIB 1024)
;; 数の空きの memory の単位(MiB)。予約の半分ずつの守り(cluster_policy.task-misfit)は空きを数で読むので、memory の空き(要りの予約を
;; 引いた MiB)もこの単位で数に写す。task を置けるかは要りと memory の空きの MiB で比べる(この単位で task の大きさを見込まない)。
(val MEMORY-SLOT-MIB 1024)
;; 機体に残す空いた CPU と memory。CPU は常駐の job の揺れと、測りの間の増えの分。memory はその 512 MiB に、worker が jobs の層の上限で
;; 追い出しの線の手前に残す余裕 1024 MiB(worker/core/eviction_rules の EVICTION-MARGIN-BYTES — 宣言の無い機体の余裕で、task を受ける
;; 機体の宣言の値の最大〔atlas〕。task は追い出しまでの空きからこの余裕を引いた分までしか使えない)を足した値。
(val CPU-FLOOR-CORES 1)
(val MEMORY-FLOOR-MIB 1536)
;; jobs の層の空きに残す量(MiB — 頭の註の jobs の層の空き)。worker は層の上限を 5 秒ごとに掛け直し、ほかの層や Pod の伸びで上限は回ごとに
;; 数百 MiB 動く(atlas の 11-08 14:09〜14:15 は 3298 → 2852 → 1962 MiB)。置いた直後の task の伸びが次の回の縮みと重なっても層に収まる分。
(val JOBS-ROOM-FLOOR-MIB 512)
;; PSI の some avg10(%)がこの値以上の機体には置かない。CPU は実行を待つ process が出ている時間の割合、memory は回収で止まった時間の割合。
(val CPU-PRESSURE-STOP 40.0)
(val MEMORY-PRESSURE-STOP 10.0)
;; task の層の PSI を名乗る worker に、CPU の空きが 0 でも置く task の数(頭の註の層の床)。取り分で動ける本数を測らずに増やさない —
;; 2 本目からは CPU の空きで数える。
(val TASK-LAYER-FLOOR 1)
;; 走り出した task の CPU の使いが測りに出きるまでの見込みの長さ(worker の測りの間 5 秒の 2 回分 — CPU の使いは前の読みとの間の増えで
;; 数える)。memory はこの見込みで数えない(task は分の単位で伸びる — 要りの予約で数える)。
(val TASK-RAMP-MS 10000)
;; 予約 = 上限のこの分の 1。
(val RESERVE-SHARE 6)
;; 積んでからこの長さを越えて memory の空きだけで置けない task に、worker の空きを取っておく(頭の註の長く待った task の取っておき)。
;; 短い待ちでは取っておかない — 空きは置いた task が終わるたびに戻るので、普通の待ちの間は小さい task を先に置いて機体を遊ばせない。
(val STARVE-WAIT-MS (* 30 60 1000))
;; この値以上の優先の task は、待った長さを問わず取っておく(頭の註の急ぎの task の取っておき)。
(val KEEP-NOW-PRIORITY 1)


(defk keeps-room? [priority waited-ms]
  {:pre [(: priority int) (: waited-ms int)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "memory の空きだけで置けない task に worker の空きを取っておくかを知るため(頭の註の長く待った task と急ぎの task の取っておき):
   優先 priority が KEEP-NOW-PRIORITY 以上か、積んでから待った長さ waited-ms が STARVE-WAIT-MS 以上。"
  (or (>= priority KEEP-NOW-PRIORITY) (>= waited-ms STARVE-WAIT-MS)))


(defk urgent-priority? [priority]
  {:pre [(: priority int)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "task の memory の空きを、上に載る系の残す線を外した空きで数えるかを知るため(頭の註の急ぎの task の空き): 優先 priority が
   KEEP-NOW-PRIORITY 以上。待った長さでは外さない(長く待った作り直せる task は線の後ろで待つ)。"
  (>= priority KEEP-NOW-PRIORITY))


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


(defk normal-busy-of [measured]
  {:pre [(: measured MeasuredLoad)] :post [(: % (| float None))] :tags {:context "coordinator" :role "judgment"}}
  "普通の優先度の task が使えない CPU の割合を知るため(頭の註の普通の使い): cpu-busy − cpu-nice(0 未満は 0)。nice の分を名乗らない
   worker(前の版)は cpu-busy のまま。CPU の使いをまだ数えられない読み(cpu-busy None)は None。"
  (cond
    (is measured.cpu-busy None) None
    (is measured.cpu-nice None) measured.cpu-busy
    True (max 0.0 (- measured.cpu-busy measured.cpu-nice))))


(defk pressured? [measured]
  {:pre [(: measured MeasuredLoad)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "機体に実行や回収を待つ process がもう出ているかを知るため: CPU の some avg10 ≥ CPU-PRESSURE-STOP か memory の some avg10 ≥
   MEMORY-PRESSURE-STOP。CPU の some は task の層の値を名乗る worker はその値(優先度を下げた process の待ちを数えない — 頭の註)、
   名乗らない worker は機体の値。PSI の無い機体(None)は待ちを見ない。"
  (val cpu-some (if (is measured.task-cpu-some-avg10 None) measured.cpu-some-avg10 measured.task-cpu-some-avg10))
  (or (and (is-not cpu-some None) (>= cpu-some CPU-PRESSURE-STOP))
      (and (is-not measured.memory-some-avg10 None) (>= measured.memory-some-avg10 MEMORY-PRESSURE-STOP))))


(defk layer-floor-of [measured placed]
  {:pre [(: measured MeasuredLoad) (: placed int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "機体の CPU が埋まっていても task の層の取り分で置ける task の数を知るため(頭の註の層の床): task の層の PSI を名乗る worker は
   TASK-LAYER-FLOOR − placed(置いた task の数)、名乗らない worker は 0。負は負のまま返す(呼び手が CPU の空きと大きい方を取る)。"
  (if (is measured.task-cpu-some-avg10 None) 0 (- TASK-LAYER-FLOOR placed)))


(defk need-of [memory-mib]
  {:pre [(: memory-mib (| int None))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "task の memory の要り(MiB)を知るため: 名乗った要り memory-mib、名乗らない task(None)は UNDECLARED-TASK-MIB。"
  (if (is memory-mib None) UNDECLARED-TASK-MIB memory-mib))


(defk learned-need [declared peak shape-peak line]
  {:pre [(: declared (| int None)) (: peak (| int None)) (: shape-peak (| int None)) (: line (| int None))] :post [(: % int)]
   :tags {:context "coordinator" :role "judgment"}}
  "task の要り(MiB)を、名乗った要りと測った山から知るため(頭の註の測った山で数える要り): declared = 名乗った要り(None = 名乗らない —
   need-of)・peak = この task の測った山・shape-peak = 同じ形の行の測った山の最大(None = 測っていない・形を名乗らない)・line = 置き先の
   worker の線 − 床(memory-line-mib — None = 測りの無い worker で、山を線で切らない)。max(need-of declared, min(max(peak, shape-peak), line))。"
  (<- need int (need-of declared))
  (val measured (max (or peak 0) (or shape-peak 0)))
  (max need (if (is line None) measured (min measured line))))


(defk reserved-mib-of [need use]
  {:pre [(: need int) (: use (| int None))] :post [(: % int) (>= % 0)] :tags {:context "coordinator" :role "judgment"}}
  "置いた task 1 本が、測った memory の空きからまだ予約する分(MiB — これから伸びうる分)を知るため: max(0, 要り need − 今の使い use)。
   今の使いを名乗られない task(None — 置いた直後・測りの前の版の worker)は要りの全部。"
  (max 0 (- need (or use 0))))


(defk memory-room-mib [measured reserved * [urgent False]]
  {:pre [(: measured (| MeasuredLoad None)) (: reserved int) (: urgent bool)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker に新しく置ける task の要りの最大(MiB)を知るため: min(測った memory の空き − MEMORY-FLOOR-MIB, jobs の層の空き −
   JOBS-ROOM-FLOOR-MIB)− reserved(置いた task の reserved-mib-of の和)。jobs の層の空きを名乗らない worker は前の項だけ(頭の註の
   jobs の層の空き)。urgent = 急ぎの優先の task の空きか(urgent-priority? — 頭の註の急ぎの task の空き): 真なら、worker が上に載る系の
   残す線を外して名乗った 2 つの空き(名乗らない欄は平常の空き)で数える。負は 0 未満のまま返す(GET /state で詰めすぎを読めるように —
   置き先は 0 以下の worker に置かない)。測りの無い worker は 0。"
  (when (is measured None)
    (return 0))
  (val available (if (and urgent (is-not measured.urgent-memory-available-MiB None))
                     measured.urgent-memory-available-MiB measured.memory-available-MiB))
  (val jobs-room (if (and urgent (is-not measured.urgent-jobs-room-MiB None)) measured.urgent-jobs-room-MiB measured.jobs-room-MiB))
  (val line-room (- available MEMORY-FLOOR-MIB))
  (val room (if (is jobs-room None) line-room (min line-room (- jobs-room JOBS-ROOM-FLOOR-MIB))))
  (- room reserved))


(defk drained-room-mib [measured]
  {:pre [(: measured (| MeasuredLoad None))] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "worker の今の task が全部終われば置ける要りの最大(MiB)の見込みを知るため(頭の註の長く待った task の取っておき): 予約 0 の
   memory-room-mib + 測りが名乗る task ごとの今の使いの和(task の使いは測った空きと jobs の層の空きの両方から引かれている)。今の使いを
   名乗られない task の分は足さないので、見込みは小さく出る。測りの無い worker は 0。"
  (when (is measured None)
    (return 0))
  (<- room int (memory-room-mib measured 0))
  (+ room (sum (.values (or measured.task-memory-MiB {})))))


(defk memory-line-mib [measured]
  {:pre [(: measured (| MeasuredLoad None))] :post [(: % (| int None))] :tags {:context "coordinator" :role "judgment"}}
  "worker が空いた時に受けうる task の要りの最大(MiB)を知るため: 名乗った memory の総量(kubelet の下の worker は追い出しの線の大きさ)
   − MEMORY-FLOOR-MIB。測りの無い worker は None(判じない)。積む時の断り(detached_policy の memory-need-refusal)が読む。"
  (if (is measured None) None (- measured.memory-total-MiB MEMORY-FLOOR-MIB)))


(defk measured-room [measured placed unseen reserved * [urgent False]]
  {:pre [(: measured (| MeasuredLoad None)) (: placed int) (: unseen int) (: reserved int) (: urgent bool)] :post [(: % int) (>= % 0)]
   :tags {:context "coordinator" :role "judgment"}}
  "worker に task をあと幾つ置けるかを機体の測った空きから知るため(頭の註の数の空き — 0 以上): measured = heartbeat の測った空き(None =
   名乗らない)・placed = その worker に置いた task の数・unseen = そのうち測りにまだ出ていない数・reserved = 置いた task の memory の
   予約の和(MiB)。測りの無い・CPU の使いをまだ数えられない・PSI の待ちが出ている機体は 0。CPU の空きは普通の使い(normal-busy-of)
   から数え、task の層の PSI を名乗る worker は層の床(layer-floor-of)を下回らない。memory の空き(memory-room-mib)は
   MEMORY-SLOT-MIB の枠の数に写す(測りに出ていない task は引かない — その使いは予約が既に引いている)。置ける task の大きさは
   memory-room-mib が決める。urgent = 急ぎの優先の task の数えか(memory-room-mib の urgent — 頭の註の急ぎの task の空き)。"
  (when (or (is measured None) (is measured.cpu-busy None))
    (return 0))
  (when (! (pressured? measured))
    (return 0))
  (<- memory-room int (memory-room-mib measured reserved :urgent urgent))
  (<- busy float (normal-busy-of measured))
  (val idle-cores (- (* measured.cpus (- 1 busy)) CPU-FLOOR-CORES))
  (val cpu-room (- (floor (/ idle-cores TASK-CORES)) unseen))
  (<- layer-floor int (layer-floor-of measured placed))
  (val memory-slots (// memory-room MEMORY-SLOT-MIB))
  (val cap-room (- (! (task-cap-of measured.cpus)) placed))
  (max 0 (min (max cpu-room layer-floor) memory-slots cap-room)))


(defk reserve-of [measured]
  {:pre [(: measured (| MeasuredLoad None))] :post [(: % int) (>= % 0)] :tags {:context "coordinator" :role "judgment"}}
  "worker の予約(優先 0 以上の task と背景の仕事に半分ずつ残す数 — cluster_policy.foreground-reserve-of・background-reserve-of)を
   知るため: 上限(task-cap-of)の ⌊1 / RESERVE-SHARE⌋。測りの無い worker は 0。"
  (if (is measured None) 0 (// (! (task-cap-of measured.cpus)) RESERVE-SHARE)))
