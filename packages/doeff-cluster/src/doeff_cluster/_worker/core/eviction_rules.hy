;;; 機体の kubelet が Pod を追い出す線までの空き(追い出しまでの空き)を kubelet と同じ式で数え、その空きから task の層(jobs)の
;;; memory.max を決める判断。I/O を持たない(cgroup と Node の読みと jobs の層への書きは worker/protocol/eviction_line が効果で行う)。
;;;
;;; 何のためか: kubelet は機体の memory が線を割ると、request を最も越えた Pod を丸ごと追い出す。worker の Pod は request が小さく
;;; (atlas は 1Gi)、使いは task と常駐の job で数 GiB になるので、追い出されるのはいつも worker で、常駐の job も Pod が起き直すまで
;;; 全部止まる(atlas で 10-30〜11-01 に 5 回 — どれも下の allocatable の線)。kernel は cgroup の memory.max を越えた所で、その cgroup の
;;; 中だけを回収し、足りなければその中の process を OOM kill で止める。jobs の層の memory.max を kubelet の線の手前に置けば、task の
;;; 跳ねは kubelet より先に kernel が jobs の層の中で止め、Pod と常駐の job は残る。
;;;
;;; kubelet の 2 つの線(E = kubelet の設定の evictionHard の memory.available。allocatable の線にも同じ hard の値が掛かる):
;;;   allocatable の線  Pod の全部の cgroup(kubepods)の working set が allocatable(= kubepods の memory.max − E)を越えると追い出す。
;;;                     空き = allocatable − kubepods の working set。
;;;   機体の線          機体の root の cgroup の working set が capacity − E を越えると追い出す。空き = capacity − E − root の working set。
;;;   working set = 使い − inactive_file(cAdvisor の数え方 — 使いより inactive_file が大きければ 0)。kubepods の使いは memory.current、
;;;   root の使いは memory.stat の anon + file(root の cgroup には memory.current が無い — runc が root の使いを数える式)。
;;;   E は手で書かず、Node の宣言の allocatable と kubepods の memory.max から導く: kubelet は kubepods の memory.max を capacity − 予約
;;;   (systemReserved + kubeReserved)に掛け、allocatable を capacity − 予約 − E と名乗るので、E = kubepods の memory.max − allocatable。
;;;   kubepods の memory.max が max の機体(kubelet が Pod の allocatable を cgroup に掛けない)は allocatable の線が無く E も導けないので、
;;;   線は無い(呼び手は機体の MemAvailable で見張る)。
;;; 追い出しまでの空き = 2 つの線の空きの小さい方(root の memory.stat の無い kernel は allocatable の線だけ)。
;;;
;;; jobs の層の memory.max(jobs-limit)= jobs の今の使い(memory.current)+ 追い出しまでの空き − 余裕 − 準備の取り置き。上限は宣言の
;;; 静的な値(WORKER_CGROUP_LAYOUT の jobs の memory.max)、下限は 0。kernel は jobs の層の使いをこの値で止めるので、全部の Pod の working set
;;; は 線 − 余裕 + 掛け直しの間のほかの伸びまでしか増えない。ほかの Pod や常駐の job が伸びて空きが縮んだ時は、jobs の層が先に縮む。
;;; ただし測った余裕が宣言の床を越えた分と clis への貸しでは、jobs の今の使い + 置いた task の取り置きより下げない(下の会話の CLI の層の
;;; 帳面 — 使いより下げるのは、宣言の余裕で数えた空きが負の時だけ)。
;;; 余裕は、掛け直しの間(見張りの回の 5 秒)に jobs と準備の層の外(ほかの Pod・常駐の job・worker の本体)が伸びる分を覆う。jobs の層の
;;; 中の跳ね(atlas の 11-01 08:43 は 90 秒で約 5 GiB)は kernel が止めるので余裕に入れない。
;;;
;;; 上に載る系が残す線(2026-11-10): 枠の file の行が keepLineFile を名指す worker(worker/core/room_rules — 上に載る系が機体の MemAvailable を
;;; その線より上に残したい機体)は、jobs の層の上限を、追い出しまでの空きと「機体の MemAvailable − 残す線」(keep-room — heartbeat の測りが
;;; 読む)の小さい方で数える(kept-line — 種類 KEEP の線)。置き先は名乗る空きを同じ線の上に絞って task を置くが、task は置いた後に名乗りより
;;; 大きく伸びうる(zeus の 11-09 16:52 UTC — 名乗り 3584 MiB の task 3 本が 8 分で各 14 GiB へ伸び、MemAvailable は線を約 54 GiB 割った)。
;;; ただしこの線では、jobs の層の上限を「jobs の今の使い + 置いた task の取り置き(要り − 今の使い)」より下げない(kept-jobs-limit — 追い出しの
;;; 線で数えた上限がそれより小さい時は追い出しの線のまま)。線を割る訳の大半が jobs の層の外(上に載る系の命令・会話の CLI の層)に在る機体では、
;;; 層の上限を使いより下げると、kernel は伸びた task でなく層の中の全部の task を区別なく OOM kill で止めた(zeus の 11-09 23:57〜11-10 01:33 UTC —
;;; 要り 1024 MiB・使い 128 MiB の task を含む 13 本が、上限を 0 まで下げた回の 1〜10 秒後に SIGKILL)。線の上の空きの縮みは新しい受けの上限
;;; (jobs-intake)を絞って新しい task を置かない事で吸い、走っている task が要りを越えて伸びた分は、線を割った回に見張りが要りを越えた task
;;; だけを越えの大きい順に名指して止める(over-need-tasks — worker/protocol/memory_watch)。要りの内で走る task は、線を割っても止めない。
;;; 会話の CLI の層(clis)と keeper の読む準備の空きはこの線で縮めない(線は上に載る系がそれらのために残す空き)。
;;; 線を読めない・古い(LINE-FRESH-MS を越えた)回と、追い出しの線の無い機体では今までどおり追い出しまでの空きだけで数える。
;;;
;;; 準備の層(prepare — keeper の版の準備・deploy/boot.sh の keeper_swap): 版上げに要る 1 回きりの短い仕事なので、task と同じ縮む層に
;;; 置かない(縮めて殺しても memory は守れず、版上げだけが止まる — k3s-1 の 11-02 は jobs の上限 61〜139 MiB で準備の bytecode の焼きが
;;; OOM で落ち続けた)。準備の層の memory.max は宣言の静的な値(準備の山)で、worker は掛け直さない。準備が走っている間(層に process が
;;; 居る)は、その上限までの残り(上限 − 今の使い)を線の空きから先に取り置き、jobs の層の上限から引く(prepare-reserve)— 準備が山まで
;;; 伸びても線を越えない。準備を始めてよいかは keeper が決める: worker は回ごとに「追い出しまでの空き − 余裕 − 取り置き」(prepare-room)と
;;; 準備が線へ足しうる量(prepare-addable)を keeper の読む file へ書き、keeper は空きがその量より小さい間は準備を起こさずに待つ(OOM で
;;; 殺さない)。足しうる量は上限 − 層の今の working set: 層は memory.max で止まり、前の準備の名残(process の居ない層の page cache と slab)は
;;; 線の使いに数え済みなので、上限を丸ごと求めると名残の分を 2 度数える(k3s-1 の 11-09 — 名残 288 MiB)。
;;; 準備を待つ間の取り置き(2026-11-10): keeper が版の file の替わりを読み、空きが足しうる量に足りずに準備を待つ間(版の file の版がこの世代の
;;; 版と違い、準備の層に process が居ない — prepare-waiting)も、足しうる量を新しい受けの外に取り置く(prepare-wait-reserve)。取り置いた分は
;;; heartbeat の名乗る jobs の層の空き(jobs-intake)と会話の CLI の層への貸しに入れず、heartbeat で量を名乗る(置き先は名乗る worker に新しく
;;; 常駐の job を置かない)。keeper の読む空き(prepare-room)と jobs の層の memory.max には入れない — 入れると keeper の判じが自分の
;;; 取り置きで満たなくなり、走っている task を準備のために止める。取り置かなかった前は、空いた分を新しい task と常駐の service が先に取り、
;;; 小さい機体の keeper の準備が起きなかった(k3s-1 の 11-09 — 版の file を替える前後に常駐の service 2 つ〔jev-proxy と minio〕を出しても、空いた分へ別の
;;; 常駐の service 2 つ〔約 370 MiB〕が置かれ、空きは 403 から 335 MiB に減った)。
;;;
;;; 会話の CLI の層(clis — 上に載る系の仕事を余りの空きで受ける機体 helios・atlas の限る層): 宣言の静的な memory.max は床(常駐の job の予算から
;;; 引いた量・今までの上限)で、機体の宣言に天井(WORKER_CLI_LAYER_CEILING)が在る機体だけ、worker は回ごとに床と天井の間で掛け直す
;;; (clis-limit)。空きは 2 つ: 線の空き(free = 追い出しまでの空き − 余裕 − 準備の取り置き)と container の中の空き(box = container の上限 −
;;; container の working set − 余裕 − jobs と clis の外の限る層の宣言の上限の和)。
;;; 線の空きは 1 つの帳面で、次の順に約束する(先の約束ほど守る):
;;;   1. clis の今の使いと床(走っている会話の CLI — どの回も下げない)と、jobs の今の使い(走っている task)。どちらも空きの外(使いに数え済み)。
;;;   2. 宣言の余裕(機体の宣言の床 — 線の手前に必ず残す)。
;;;   3. 置いた task の取り置き(pending — worker が task を受けた時の要り − 今の使いの和・placed-pending): 置き先が task を置いた時に
;;;      約束した、まだ使っていない分。
;;;   4. 測った余裕が宣言の床を越えた分(margin-of)。
;;;   5. clis に床を越えて貸してまだ使われていない分(前の回の上限までの残り)。
;;;   6. 残り(avail = 2 つの空きの小さい方 − 取り置き)を新しい受けに: CLIS-ROOM-SHARE を clis の層へ広げ(grow)、残りを jobs の層の受け
;;;      (置き先が新しい task を置く空き)に残す。
;;; 空きが縮むと後ろの約束から返す。clis の上限は「前の回の上限までの残り」を avail の内でだけ残し(keep)、広げる量は avail の半分なので、
;;; clis に貸して使われていない分は、jobs の層の使いと置いた task の取り置きより先に返る。jobs の層の上限(jobs-limit)は、jobs の今の使いの
;;; 上に「測った余裕で数えた空き − clis への貸し」と「置いた task の取り置き(宣言の余裕で数えた空きの内)」の大きい方を残すので、測った余裕
;;; が上がっても・clis に貸しても、jobs の層の上限は jobs の使いより下がらない。下がるのは、clis の貸しを返しきっても宣言の余裕で数えた
;;; 線の空きが負の時だけ(kubelet が Pod を追い出す前に、kernel が jobs の層の中で止める)。測った余裕の上がりは新しい受けを減らす側で
;;; 吸う: 置き先に名乗る jobs の層の空き(jobs-intake — 受けの上限 − jobs の使い)は測った余裕で数え、取り置きを入れない(置き先は置いた
;;; task の予約を自分で引く)。会話を受ける turn-host は clis の memory.max を回ごとに読み、余白(上限 − working set)で新しいターンを受ける
;;; かを決めるので、2 つの受けは同じ帳面の別の行(5・6 の clis の分と、6 の jobs の分)から出て、同じ空きを重ねて約束しない。
;;; jobs の層の上限は container の中の空きから clis の上限までの残りを引いた値(jobs-box-limit)も同じ形で越えない。生かす CLI の本数の上限は
;;; 起動の時に天井から導く(worker が job の子へ天井を渡す — worker/entry/main の CLI-CEILING-CHILD-VAR)。
;;; 以前は広げた clis の上限を「jobs の層を 0 まで縮めれば収まる間」は残したので、余裕が測りで上がった atlas の 11-09 23:00〜23:21 は、
;;; clis の上限 6 GiB・使い約 1 GiB の使われていない貸しを残すために jobs の層の上限を 0 まで下げ、走っていた task を 50 本を越えて kernel が
;;; OOM で止めた。
;;;
;;; 命令の層(commands — 上に載る系が worker の外から起こす作り直せる命令の層): 宣言の静的な memory.max だけでは、機体の
;;; 空きを見ずに build の命令が線の手前まで memory を取り、新しいターンを受ける係の余白を何時間も 0 にした(11-10 の zeus — 命令の層 35.6 GiB・
;;; そのうちターンの走っている会話の命令は 1 本ぶん)。枠の file の行の commandLineFile(上に載る系が書く MemAvailable の線・worker/core/room_rules)と
;;; commandFloorMiB が在る機体は、worker が見張りの回ごとに命令の層の memory.max を掛け直す(command-layer-limit):
;;;   上限 = tmpfs の量 + max(床, 止めて返る量 + (MemAvailable − 線))  を宣言の静的な値で切る。
;;;   回収で返らない量 = memory.current −(file − shmem)− slab_reclaimable。page cache と回収できる slab は MemAvailable に数え済みなので、
;;;     memory.current を土台にすると、回収した page cache の分だけ次の回の上限が下がり続ける(上限 = 使い + 空き では空きは変わらず使いだけ減る)。
;;;   tmpfs の量(shmem)は process を止めても返らない(返すのは中間物の掃除の係)ので、上限から引かない — 下げると、消せない page のために層の
;;;     process を全部 OOM で止めても目標に届かない。
;;;   床 = 短い命令の余地: 線を割っても命令の層がこの量までは持てる(走っているターンの短い命令を止めない)。
;;; 上限が今の使いより下がると kernel は page cache を回収し、足りなければ層の中の process を OOM で止める。先に止まるのは上に載る系が印を
;;; 付けた長い命令(oom_score_adj)で、短い命令より先に譲る。
;;;
;;; 余裕(margin-of)は機体ごと: 機体の宣言の値(WORKER_EVICTION_MARGIN — 機体の 7 日の測りから決めた値・無ければ EVICTION-MARGIN-BYTES)を
;;; 床にし、worker 自身が測った「jobs と準備の層の外の working set の回あたりの増え」の高い分位(MARGIN-QUANTILE)の MARGIN-TICKS 回分が
;;; 床を越えれば、それを使う(機体に載る Pod が替わって伸びが大きくなった時に、宣言を書き直す前から覆う)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import decimal [Decimal InvalidOperation])
(import itertools [accumulate])
(import math [ceil])
(import posixpath)
(import re)
(import doeff_cluster._worker.core.memory_rules [MIB TASK-PREFIX NodeMemory])

;; 余裕の宣言の無い機体の余裕(margin-of の床): atlas の 7 日(Prometheus・30 秒ごと・11-01)で、worker の外の Pod の working set の和の
;; 30 秒の増えは p99 0.78 GiB・p999 1.73 GiB・最大 2.5 GiB(clickhouse と garage)。最大の増えを 30 秒に均すと 5 秒の回あたり 0.42 GiB —
;; 1 GiB は回が 2 回遅れても覆う。機体ごとの宣言の値の決め方は deploy/k8s/cgroup-layers/cgroup-layers.yaml の頭の註。
(val #^ int EVICTION-MARGIN-BYTES (* 1024 MIB))
;; worker 自身の測りから余裕を導く数: 回あたりの増えの分位・その何回分を覆うか(回が 1 回遅れても覆う)・増えを残す回の数(5 秒の回で
;; 24 時間)・分位を使い始める回の数(1 時間 — それまでは宣言の床だけ)。
(val MARGIN-QUANTILE 0.999)
(val MARGIN-TICKS 2)
(val MARGIN-WINDOW 17280)
(val MARGIN-MIN-SAMPLES 720)
;; 会話の CLI の層に貸す、線の空き(空き − 余裕 − 準備の取り置き)の割合(頭の註の会話の CLI の層): 残りは jobs の層の上限に残す。半分ずつに
;; するのは、片方の層だけが伸びる時も回ごとに残りの半分を取れる(5 秒の回 5 つで 97 %)ので、使っていない層に空きを取り置かずに済むため。
(val CLIS-ROOM-SHARE 0.5)
;; 要りを名乗らずに置かれた task に見込む要り(置いた task の取り置き — 頭の註の会話の CLI の層): 置き先が同じ task に見込む値
;; (coordinator/core/measured_room の UNDECLARED-TASK-MIB)と同じ。
(val UNDECLARED-NEED-BYTES (* 1024 MIB))
;; 上に載る系が残す線の空き(keep-room)を jobs の層の上限に使える長さ(ms — 頭の註の上に載る系が残す線)。heartbeat の測りは 5 秒ごとに
;; 読み直すので、その 3 回分(worker/core/load_rules の LINE-FRESH-MS と同じ長さ)。
(val KEEP-ROOM-FRESH-MS (* 15 1000))
;; kubelet が Pod の全部を置く cgroup(Node の allocatable を掛ける cgroup)の名 — systemd の cgroup の driver と cgroupfs の driver。
(val PODS-CGROUP-NAMES #("kubepods.slice" "kubepods"))
;; kernel の memory の値の綴り(memparse — 数 + 1 字の単位)と、k8s の量の綴り(数 + 単位)。
(val #^ (get re.Pattern str) KERNEL-SIZE (re.compile r"\A([0-9]+)([kKmMgGtTpPeE]?)\Z"))
(val KERNEL-UNITS {"" 0 "k" 1 "m" 2 "g" 3 "t" 4 "p" 5 "e" 6})
(val #^ (get re.Pattern str) QUANTITY (re.compile r"\A([0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE]([+-]?[0-9]+))?(Ki|Mi|Gi|Ti|Pi|Ei|n|u|m|k|M|G|T|P|E)?\Z"))
(val QUANTITY-UNITS {None (Decimal 1) "Ki" (Decimal (** 2 10)) "Mi" (Decimal (** 2 20)) "Gi" (Decimal (** 2 30)) "Ti" (Decimal (** 2 40))
                     "Pi" (Decimal (** 2 50)) "Ei" (Decimal (** 2 60)) "n" (Decimal "1e-9") "u" (Decimal "1e-6") "m" (Decimal "1e-3")
                     "k" (Decimal "1e3") "M" (Decimal "1e6") "G" (Decimal "1e9") "T" (Decimal "1e12") "P" (Decimal "1e15")
                     "E" (Decimal "1e18")})


;; 線の種類(閉じた型 — 頭の註): ALLOCATABLE = allocatable の線・NODE = 機体の線・KEEP = 上に載る系が残す線(jobs の層の上限だけが使う)。
(defenum LineKind ALLOCATABLE NODE KEEP)


(defrecord KubeletMemory
  "Node の宣言の memory(kubelet が名乗る値): capacity-bytes = status.capacity.memory・allocatable-bytes = status.allocatable.memory。"
  {:check [(> capacity-bytes 0) (<= 0 allocatable-bytes capacity-bytes)]}
  (#^ int capacity-bytes)
  (#^ int allocatable-bytes))


(defrecord PodsMemory
  "kubepods の cgroup の読み: max-bytes = memory.max(max は None)・current-bytes = memory.current・inactive-file-bytes = memory.stat の
   inactive_file。"
  {:check [(or (is max-bytes None) (>= max-bytes 0)) (>= current-bytes 0) (>= inactive-file-bytes 0)]}
  (#^ (| int None) max-bytes)
  (#^ int current-bytes)
  (#^ int inactive-file-bytes))


(defrecord EvictionLine
  "追い出しまでの空きを決めた線(頭の註): kind = 線の種類・size-bytes = 線の大きさ(allocatable か capacity − E)・used-bytes = 線に数える
   working set・room-bytes = size − used(線を越えていれば負)。"
  {:check [(> size-bytes 0) (>= used-bytes 0) (= room-bytes (- size-bytes used-bytes))]}
  (#^ LineKind kind)
  (#^ int size-bytes)
  (#^ int used-bytes)
  (#^ int room-bytes))


(defrecord TaskNeed
  "置き先が worker に置いた task 1 本の memory の要り(置いた task の取り置き — 頭の註の会話の CLI の層): id = task の id・bytes = 置いた時に
   名乗った要り(byte — 名乗らない task は UNDECLARED-NEED-BYTES)。"
  {:check [(>= bytes 0)]}
  (#^ str id)
  (#^ int bytes))


(defrecord CommandLayerUse
  "命令の層の memory の内訳(頭の註の命令の層): held-bytes = 回収で返らない量(memory.current −(file − shmem)− slab_reclaimable・0 未満は 0)・
   pinned-bytes = そのうち tmpfs の量(shmem — process を止めても返らない)。"
  {:check [(>= held-bytes 0) (>= pinned-bytes 0)]}
  (#^ int held-bytes)
  (#^ int pinned-bytes))


(defk kernel-bytes-of [text]
  {:pre [(: text str)] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "cgroup の memory の値の綴り(memory.max の中身か WORKER_CGROUP_LAYOUT の値 — 数 + 単位 K・M・G・T・P・E の 1 字か max)を byte の数に
   するため。max は None(上限が無い)。読めない綴りは ValueError(黙って上限なしにしない)。"
  (val stripped (.strip text))
  (when (= stripped "max")
    (return None))
  (val found (.match KERNEL-SIZE stripped))
  (when (is found None)
    (raise (ValueError (.format "cgroup の memory の値 {!r} は数(K・M・G・T・P・E を付けてよい)か max で書く" text))))
  (* (int (.group found 1)) (** 1024 (get KERNEL-UNITS (.lower (.group found 2))))))


(defk quantity-bytes [text]
  {:pre [(: text str)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "k8s の量の綴り(Node の status.capacity.memory など — 20749400Ki・1.5Gi・64M・1e9)を byte の数(端数は切り上げ — k8s の Value と
   同じ)にするため。読めない綴りは ValueError。"
  (val found (.match QUANTITY (.strip text)))
  (when (is found None)
    (raise (ValueError (.format "k8s の量 {!r} を読めない" text))))
  (try
    (val number (* (Decimal (.group found 1)) (** (Decimal 10) (int (or (.group found 2) "0")))))
    (except [error InvalidOperation]
      (raise (ValueError (.format "k8s の量 {!r} を読めない: {}" text error)))))
  (int (ceil (* number (get QUANTITY-UNITS (.group found 3))))))


(defk stat-bytes [text key]
  {:pre [(: text str) (: key str)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "cgroup の memory.stat の中身から key の行の値(byte)を読むため。行が無い・数でないなら ValueError。"
  (val found (lfor line (.splitlines text) :setv parts (.split line) :if (and (= (len parts) 2) (= (get parts 0) key)) (get parts 1)))
  (when (or (not found) (not (.isdigit (get found 0))))
    (raise (ValueError (.format "memory.stat に {} の行が無いか数でない" key))))
  (int (get found 0)))


(defk working-set [used inactive-file]
  {:pre [(: used int) (: inactive-file int)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "cAdvisor と同じ working set(使い − inactive_file・使いより inactive_file が大きければ 0)を求めるため。"
  (if (< used inactive-file) 0 (- used inactive-file)))


(defk pods-memory-of [max-text current-text stat-text]
  {:pre [(: max-text str) (: current-text str) (: stat-text str)] :post [(: % PodsMemory)] :tags {:context "worker" :role "judgment"}}
  "kubepods の cgroup の memory.max・memory.current・memory.stat の中身を読みにするため。読めない中身は ValueError。"
  (<- limit (| int None) (kernel-bytes-of max-text))
  (val current (.strip current-text))
  (when (not (.isdigit current))
    (raise (ValueError (.format "kubepods の memory.current {!r} が数でない" current-text))))
  (<- inactive int (stat-bytes stat-text "inactive_file"))
  (PodsMemory :max-bytes limit :current-bytes (int current) :inactive-file-bytes inactive))


(defk root-working-set-of [stat-text]
  {:pre [(: stat-text str)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "機体の root の cgroup の memory.stat の中身から、kubelet の機体の線が数える working set(anon + file − inactive_file)を求めるため。
   読めない中身は ValueError。"
  (<- anon int (stat-bytes stat-text "anon"))
  (<- file int (stat-bytes stat-text "file"))
  (<- inactive int (stat-bytes stat-text "inactive_file"))
  (<- answer int (working-set (+ anon file) inactive))
  answer)


(defk eviction-line [kubelet pods root-working-set]
  {:pre [(: kubelet KubeletMemory) (: pods PodsMemory) (: root-working-set (| int None))] :post [(: % (| EvictionLine None))]
   :tags {:context "worker" :role "judgment"}}
  "追い出しまでの空きを決める線を求めるため(頭の註): kubelet = Node の宣言の memory・pods = kubepods の読み・root-working-set = 機体の
   root の working set(None = 読めない kernel — allocatable の線だけ)。kubepods の memory.max が max なら None(線が無い)。E が負に
   なる読み(memory.max が allocatable より小さい — Node の宣言と cgroup の食い違い)は ValueError。空きの小さい方の線(同じ空きなら
   allocatable の線)。"
  (when (is pods.max-bytes None)
    (return None))
  (val hard (- pods.max-bytes kubelet.allocatable-bytes))
  (when (< hard 0)
    (raise (ValueError (.format "kubepods の memory.max {} が Node の allocatable {} より小さい(kubelet の設定と cgroup が食い違う)"
                                pods.max-bytes kubelet.allocatable-bytes))))
  (<- pods-used int (working-set pods.current-bytes pods.inactive-file-bytes))
  (val allocatable (EvictionLine :kind LineKind.ALLOCATABLE :size-bytes kubelet.allocatable-bytes :used-bytes pods-used
                                 :room-bytes (- kubelet.allocatable-bytes pods-used)))
  (when (is root-working-set None)
    (return allocatable))
  (val node-size (- kubelet.capacity-bytes hard))
  (when (<= node-size 0)
    (raise (ValueError (.format "Node の capacity {} が evictionHard {} 以下" kubelet.capacity-bytes hard))))
  (val node (EvictionLine :kind LineKind.NODE :size-bytes node-size :used-bytes root-working-set
                          :room-bytes (- node-size root-working-set)))
  (if (< node.room-bytes allocatable.room-bytes) node allocatable))


(defk line-memory [node line]
  {:pre [(: node NodeMemory) (: line (| EvictionLine None))] :post [(: % NodeMemory)] :tags {:context "worker" :role "judgment"}}
  "見張りと heartbeat が判じる memory(NodeMemory)を、追い出しの線が在れば線から作るため: 総量 = 線の大きさ・空き = 追い出しまでの
   空き(線を越えていれば 0)・PSI は機体の読み。線が無い機体(kubelet の下に居ない・読めない)は機体の読み(MemTotal・MemAvailable)のまま。"
  (if (is line None)
      node
      (NodeMemory :total-bytes line.size-bytes :available-bytes (max 0 line.room-bytes) :some-avg10 node.some-avg10
                  :full-avg10 node.full-avg10)))


(defk kept-line [line keep-room]
  {:pre [(: line EvictionLine) (: keep-room (| int None))] :post [(: % EvictionLine) (<= %.room-bytes line.room-bytes)]
   :tags {:context "worker" :role "judgment"}}
  "jobs の層の上限を数える線を、上に載る系が残す線の上にも収めるため(頭の註の上に載る系が残す線): keep-room = 機体の MemAvailable −
   残す線(byte — None = 線が無い・古い)。keep-room が追い出しまでの空きより小さければ、同じ大きさで使いを差の分だけ多く数えた種類 KEEP の
   線(空き = keep-room — 負もありうる)、ほかは line のまま。"
  (if (or (is keep-room None) (>= keep-room line.room-bytes))
      line
      (EvictionLine :kind LineKind.KEEP :size-bytes line.size-bytes :used-bytes (+ line.used-bytes (- line.room-bytes keep-room))
                    :room-bytes keep-room)))


(defk fresh-keep-room [keep-room at-ms now-ms]
  {:pre [(: keep-room (| int None)) (: at-ms (| int None)) (: now-ms int)] :post [(: % (| int None))]
   :tags {:context "worker" :role "judgment"}}
  "heartbeat の測りが最後に読んだ「機体の MemAvailable − 上に載る系が残す線」keep-room(at-ms = 読んだ刻)を、jobs の層の上限に使える
   新しさの時だけ返すため(KEEP-ROOM-FRESH-MS の内 — 古い・刻の無い・壁の時計が戻った読みは None で、上限は追い出しまでの空きだけで数える)。"
  (if (and (is-not keep-room None) (is-not at-ms None) (<= 0 (- now-ms at-ms) KEEP-ROOM-FRESH-MS)) keep-room None))


(defk line-name [line]
  {:pre [(: line (| EvictionLine None))] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "log の欄に、見張りと heartbeat が判じた memory の出どころ(線の種類か、線の無い機体の machine)を名指すため。"
  (if (is line None) "machine" line.kind.value))


(defk kept-room [soft hard pending]
  {:pre [(: soft int) (: hard int) (>= hard soft) (: pending int) (>= pending 0)] :post [(: % int) (>= % soft)]
   :tags {:context "worker" :role "judgment"}}
  "jobs の層の今の使いの上に残す空きを求めるため(頭の註の会話の CLI の層の帳面): soft = 測った余裕で数えた空き(新しい受けの空き)・
   hard = 宣言の余裕で数えた空き(線の手前に必ず残す分だけを引いた空き — soft 以上)・pending = 置いた task の取り置き。soft と、
   取り置きを hard の内で守った量の大きい方 — 測った余裕が上がっても取り置きは残り、hard が負の時だけ使いより下になる。"
  (max soft (min pending hard)))


(defk jobs-limit [jobs-current line margin floor-margin cap reserve pending]
  {:pre [(: jobs-current int) (>= jobs-current 0) (: line EvictionLine) (: margin int) (>= margin 0) (: floor-margin int)
         (<= 0 floor-margin margin) (: cap (| int None)) (: reserve int) (>= reserve 0) (: pending int) (>= pending 0)]
   :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "jobs の層に掛ける memory.max を求めるため(頭の註): jobs の今の使い + kept-room(追い出しまでの空き − 余裕 margin − reserve・
   追い出しまでの空き − 宣言の余裕 floor-margin − reserve・置いた task の取り置き pending)を、0 と宣言の静的な上限 cap(None = 上限なし)の
   間に収める。reserve = 準備の取り置きと clis の層に床を越えて貸した量の和。jobs の今の使いより下になるのは、宣言の余裕と reserve の
   和が空きを越えた時だけ — kernel が jobs の層の中で回収し、足りなければ jobs の中の process を止める(準備の層は縮めない)。
   margin = floor-margin・pending = 0 で呼ぶと、新しい受けに名乗る上限(jobs-intake — 測った余裕で数え、取り置きを入れない)。"
  (val soft (- line.room-bytes margin reserve))
  (<- room int (kept-room soft (- line.room-bytes floor-margin reserve) pending))
  (val wanted (max 0 (+ jobs-current room)))
  (if (is cap None) wanted (min cap wanted)))


(defk clis-limit [clis-current free box floor ceiling before pending]
  {:pre [(: clis-current int) (>= clis-current 0) (: free int) (: box (| int None)) (: floor int) (>= floor 0) (: ceiling int)
         (>= ceiling floor) (: before (| int None)) (: pending int) (>= pending 0)]
   :post [(: % int) (>= % clis-current) (>= % floor)] :tags {:context "worker" :role "judgment"}}
  "会話の CLI の層に掛ける memory.max を求めるため(頭の註の会話の CLI の層): free = 線の空き − 余裕 − 準備の取り置き・box = container の
   中の空き(None = container に上限が無い)・before = 前の回に書いた上限(None = まだ)・pending = 置いた task の取り置き。貸せる空き
   avail = free と box の小さい方 − pending(負は 0)。clis の今の使いの上に残す量は、広げる量(avail の CLIS-ROOM-SHARE)と、前の回の
   上限までの残りのうち avail の内に収まる量の大きい方 — clis に貸して使われていない分は、jobs の層の使いと置いた task の取り置きより
   先に返る。それを床と天井の間に収め、clis の今の使いより下げない(天井を越えて使っている読みは使いのまま)。"
  (val pool (if (is box None) free (min free box)))
  (val avail (max 0 (- pool pending)))
  (val grow (int (* avail CLIS-ROOM-SHARE)))
  (val keep (if (is before None) 0 (max 0 (min (- before clis-current) avail))))
  (max clis-current (min ceiling (max floor (+ clis-current (max grow keep))))))


(defk clis-lent [limit clis-current floor]
  {:pre [(: limit int) (: clis-current int) (>= clis-current 0) (: floor int) (>= floor 0)] :post [(: % int) (>= % 0)]
   :tags {:context "worker" :role "judgment"}}
  "会話の CLI の層に床を越えて貸した量(jobs の層の上限から引く — 頭の註の会話の CLI の層)を求めるため: 上限 − 床と今の使いの大きい方
   (床の内と、今使っている分は、今までも jobs の層と重ねて数えていた)。"
  (max 0 (- limit (max floor clis-current))))


(defk box-free [container-max container-working-set margin held]
  {:pre [(: container-max (| int None)) (: container-working-set int) (: margin int) (>= margin 0) (: held int) (>= held 0)]
   :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "container の中で jobs と会話の CLI の層に貸せる空きを求めるため(頭の註の会話の CLI の層): container の上限 − container の working set −
   余裕 − ほかの限る層(jobs と clis の外)の宣言の上限の和 held。container に上限が無ければ None(線の空きだけで決める)。"
  (when (is container-max None)
    (return None))
  (- container-max container-working-set margin held))


(defk jobs-box-limit [jobs-current box slack clis-limit clis-current pending]
  {:pre [(: jobs-current int) (>= jobs-current 0) (: box int) (: slack int) (>= slack 0) (: clis-limit int) (: clis-current int)
         (>= clis-current 0) (: pending int) (>= pending 0)]
   :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "container の中の空きから決まる jobs の層の上限を求めるため(頭の註の会話の CLI の層): jobs の今の使い + kept-room(container の中の
   空き box − clis の層の上限までの残り(0 未満は 0)・それに測った余裕と宣言の余裕の差 slack を足した空き・置いた task の取り置き
   pending)。jobs の層と clis の層が残りまで伸びても container の上限の手前に余裕が残る。slack = 0・pending = 0 で呼ぶと新しい受けに
   名乗る上限。"
  (val soft (- box (max 0 (- clis-limit clis-current))))
  (<- room int (kept-room soft (+ soft slack) pending))
  (max 0 (+ jobs-current room)))


(defk command-layer-use-of [current-text stat-text]
  {:pre [(: current-text str) (: stat-text str)] :post [(: % CommandLayerUse)] :tags {:context "worker" :role "judgment"}}
  "命令の層の memory.current と memory.stat の中身を内訳にするため(頭の註の命令の層)。数でない・行が無い中身は ValueError。"
  (val current (.strip current-text))
  (when (not (.isdigit current))
    (raise (ValueError (.format "命令の層の memory.current {!r} が数でない" current-text))))
  (<- file int (stat-bytes stat-text "file"))
  (<- shmem int (stat-bytes stat-text "shmem"))
  (<- slab int (stat-bytes stat-text "slab_reclaimable"))
  (val held (max 0 (- (int current) (max 0 (- file shmem)) slab)))
  (CommandLayerUse :held-bytes held :pinned-bytes (min held shmem)))


(defk command-layer-limit [use room floor cap]
  {:pre [(: use CommandLayerUse) (: room int) (: floor int) (>= floor 0) (: cap int) (>= cap 0)] :post [(: % int) (<= 0 % cap)]
   :tags {:context "worker" :role "judgment"}}
  "命令の層に掛ける memory.max を求めるため(頭の註の命令の層): use = 層の内訳・room = MemAvailable − 線(線を割っていれば負)・floor = 短い
   命令の余地・cap = 宣言の静的な値。tmpfs の量 + max(床, 止めて返る量 + room)を cap で切る。"
  (val yieldable (- use.held-bytes use.pinned-bytes))
  (min cap (+ use.pinned-bytes (max floor (+ yieldable room)))))


(defk placed-pending [needs uses]
  {:pre [(: needs tuple) (: uses tuple)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "置いた task の取り置き(頭の註の会話の CLI の層の帳面の 3)を求めるため: needs = 置き先が置いた task の要り(TaskNeed の tuple)・
   uses = 見張りの最後の読みの job ごとの memory(JobUse の tuple — task の job の名は TASK-PREFIX + id)。task ごとの max(0, 要り −
   今の使い)の和(置き先が予約する量と同じ数え — coordinator/core/measured_room の reserved-mib-of)。"
  (sum (gfor need needs
             (max 0 (- need.bytes (sum (gfor use uses :if (= use.job (+ TASK-PREFIX need.id)) use.bytes)))))))


(defk kept-jobs-limit [evicted kept jobs-current pending]
  {:pre [(: evicted int) (>= evicted 0) (: kept int) (>= kept 0) (: jobs-current int) (>= jobs-current 0) (: pending int) (>= pending 0)]
   :post [(: % int) (>= % 0) (<= % (max evicted kept))] :tags {:context "worker" :role "judgment"}}
  "jobs の層の memory.max を、上に載る系が残す線(種類 KEEP)では走っている task を止める所まで下げないため(頭の註の上に載る系が残す線):
   evicted = 追い出しの線だけで数えた上限(jobs-limit)・kept = 上に載る系が残す線でも数えた上限(kept-line の線で数えた jobs-limit —
   evicted 以下)・pending = 置いた task の取り置き(要り − 今の使いの和 — placed-pending)。kept と「jobs の今の使い + 取り置き」(床 —
   走っている task が今の使いを保ち、要りまで伸びられる量)の大きい方を、evicted で切る(追い出しの線の縮めは今のまま)。"
  (max kept (min evicted (+ jobs-current pending))))


(defrecord TaskOver
  "要りを越えて伸びた task 1 本(頭の註の上に載る系が残す線): id = task の id・need-bytes = 置いた時に名乗った要り・use-bytes = 見張りの
   読みの今の使い(task の根の木の rss の和)。"
  {:check [(>= need-bytes 0) (> use-bytes need-bytes)]}
  (#^ str id)
  (#^ int need-bytes)
  (#^ int use-bytes))


(defk over-need-tasks [needs uses shortfall]
  {:pre [(: needs tuple) (: uses tuple) (: shortfall int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "機体の MemAvailable が上に載る系の残す線を shortfall(byte)だけ割った時に、見張りが止める task を選ぶため(頭の註の上に載る系が残す線):
   needs = 置いた task の要り(TaskNeed の tuple)・uses = 見張りの今の読みの job ごとの memory(JobUse の tuple — task の job の名は
   TASK-PREFIX + id)。要りを越えて使う task(TaskOver)だけを、越えの大きい順(同じなら id の順)に、それより前に選んだ task の使いの和が
   shortfall に届くまで並べる。shortfall が 0 以下・要りを越えた task が無ければ空 — 要りの内で走る task は止めない(線を割った訳が jobs
   の層の外の伸びなら、新しい受けを絞るだけ)。"
  (when (<= shortfall 0)
    (return #()))
  (val over (tuple (gfor need needs
                         :setv used (sum (gfor use uses :if (= use.job (+ TASK-PREFIX need.id)) use.bytes))
                         :if (> used need.bytes)
                         (TaskOver :id need.id :need-bytes need.bytes :use-bytes used))))
  (val ordered (sorted over :key (fn [t] #((- t.need-bytes t.use-bytes) t.id))))
  (tuple (gfor #(task freed) (zip ordered (accumulate (gfor t ordered t.use-bytes) :initial 0)) :if (< freed shortfall) task)))


(defk task-needs-of [rows]
  {:pre [(: rows tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "heartbeat の返事の task の行(置き先が worker に置いた task — JSON の dict の tuple)から、置いた task の要り(TaskNeed の tuple)を
   読むため: 欄 memoryMiB(0 以上の整数 — 真偽は数えない)が在ればその byte、無い・形の違う行は UNDECLARED-NEED-BYTES。id の無い行は
   読まない。"
  (tuple (gfor row rows
               :if (and (isinstance row dict) (isinstance (.get row "id") str))
               :setv mib (.get row "memoryMiB")
               (TaskNeed :id (get row "id")
                         :bytes (if (and (isinstance mib int) (not (isinstance mib bool)) (>= mib 0)) (* mib MIB) UNDECLARED-NEED-BYTES)))))


(defk prepare-reserve [cap current populated]
  {:pre [(: cap int) (>= cap 0) (: current int) (>= current 0) (: populated bool)] :post [(: % int) (>= % 0)]
   :tags {:context "worker" :role "judgment"}}
  "準備の層の上限 cap までの残りのうち、線の空きから先に取り置く量を求めるため(頭の註): 準備が走っている(populated — 層に process が
   居る)間は 上限 − 今の使い(使いが上限を越えた読みは 0)、走っていなければ 0(終わった準備の page cache の残りは取り置かない)。"
  (if populated (max 0 (- cap current)) 0))


(defk prepare-waiting [wanted own]
  {:pre [(: wanted (| str None)) (: own (| str None))] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "keeper が新しい版の準備を待っているかを判じるため(頭の註の準備を待つ間の取り置き): wanted = 版の file の中身(None = 無い・読めない)・
   own = この世代の版(None = keeper の世代でない)。版の file の版(空白を除いた 40 字の 16 進 — keeper が読むのと同じ綴り・deploy/boot.sh の
   keeper_version)がこの世代の版と違う時だけ真。読めない・綴りの違う file は偽(keeper も替えない)。"
  (when (or (is wanted None) (is own None))
    (return False))
  (val version (.join "" (.split wanted)))
  (and (= (len version) 40) (all (gfor c version (in c "0123456789abcdef"))) (!= version own)))


(defk prepare-wait-reserve [waiting populated addable]
  {:pre [(: waiting bool) (: populated bool) (: addable int) (>= addable 0)] :post [(: % int) (>= % 0)]
   :tags {:context "worker" :role "judgment"}}
  "keeper が準備を待つ間に、新しい受け(heartbeat の名乗る jobs の層の空きと会話の CLI の層への貸し)から先に取り置く量を求めるため(頭の註の
   準備を待つ間の取り置き): waiting = keeper が新しい版の準備を待っている(prepare-waiting)・populated = 準備の層に process が居る・
   addable = 準備が線へ足しうる量(prepare-addable)。待っていて準備がまだ走っていなければ addable、ほかは 0(走り出した準備は
   prepare-reserve が取り置く)。"
  (if (and waiting (not populated)) addable 0))


(defk prepare-room [line margin reserve]
  {:pre [(: line EvictionLine) (: margin int) (>= margin 0) (: reserve int) (>= reserve 0)] :post [(: % int)]
   :tags {:context "worker" :role "judgment"}}
  "keeper が新しい準備を起こしてよいかを判じる空き(頭の註)を求めるため: 追い出しまでの空き − 余裕 − 走っている準備の取り置き。
   keeper はこれが準備の足しうる量(prepare-addable)以上の時だけ準備を起こす(負もありうる — 線の手前に余裕が残っていない)。"
  (- line.room-bytes margin reserve))


(defk prepare-addable [cap working-set]
  {:pre [(: cap int) (>= cap 0) (: working-set int) (>= working-set 0)] :post [(: % int) (>= % 0)]
   :tags {:context "worker" :role "judgment"}}
  "新しい準備が追い出しの線へ足しうる量(頭の註)を求めるため: 準備の層の上限 cap − 層の今の working set(線の使いに数え済みの分 — 上限を
   越えた読みは 0)。keeper は prepare-room がこの量以上の時だけ準備を起こす。"
  (max 0 (- cap working-set)))


(defk margin-of [growths floor]
  {:pre [(: growths tuple) (: floor int) (>= floor 0)] :post [(: % int) (>= % floor)] :tags {:context "worker" :role "judgment"}}
  "jobs の層の上限に残す余裕を求めるため(頭の註): growths = worker が測った、jobs と準備の層の外の working set の回あたりの増え(byte・
   古い順・負は 0 に切った値)・floor = 機体の宣言の余裕。増えが MARGIN-MIN-SAMPLES 回に満たなければ floor、満ちれば floor と
   「増えの MARGIN-QUANTILE 分位 × MARGIN-TICKS」の大きい方。分位は小さい方から数えた順位 ceil(q × n) の値(標本の値そのもの)。"
  (when (< (len growths) MARGIN-MIN-SAMPLES)
    (return floor))
  (val ordered (sorted growths))
  (val at (- (min (len ordered) (max 1 (ceil (* MARGIN-QUANTILE (len ordered))))) 1))
  (max floor (* MARGIN-TICKS (get ordered at))))


(defk layer-memory-max [layout layer]
  {:pre [(: layout str) (: layer str)] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "cgroup の層の宣言(WORKER_CGROUP_LAYOUT — 空白で並べた「<名>:<file>=<値>,…」・deploy/boot.sh の「cgroup の層」)から、層 layer の
   memory.max の静的な値(byte)を読むため。層が無い・memory.max を書かない・max は None。読めない値は ValueError。"
  (val values (lfor entry (.split layout) :setv #(name _ settings) (.partition entry ":") :if (= name layer)
                    setting (.split settings ",") :setv #(key _ value) (.partition setting "=") :if (= key "memory.max") value))
  (when (not values)
    (return None))
  (<- answer (| int None) (kernel-bytes-of (get values -1)))
  answer)


(defk layout-held-bytes [layout skipped]
  {:pre [(: layout str) (: skipped tuple)] :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "cgroup の層の宣言(layer-memory-max と同じ綴り)から、名が skipped の外の層の memory.max の静的な値の和(byte)を求めるため(会話の
   CLI の層の container の中の空きから引く — 頭の註の会話の CLI の層)。memory.max を書かない・max の層は数えない。"
  (val names (tuple (.fromkeys dict (gfor entry (.split layout) :setv name (get (.partition entry ":") 0) :if (and name (not-in name skipped))
                                          name))))
  (var held 0)
  (for [name names]
    (<- cap (| int None) (layer-memory-max layout name))
    (when (is-not cap None)
      (:= held (+ held cap))))
  held)


(defk layout-layer-names [layout]
  {:pre [(: layout str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "cgroup の層の宣言(layer-memory-max と同じ綴り)から、層の名を宣言の順に読むため(機体の memory の見張りが層ごとの memory.current を
   log に出す — worker/protocol/memory_watch)。同じ名は 1 度だけ。"
  (tuple (.fromkeys dict (gfor entry (.split layout) :setv name (get (.partition entry ":") 0) :if name name))))


(defk pods-dir-of [cgroup-dir host-root]
  {:pre [(: cgroup-dir str) (: host-root str)] :post [(: % (| str None))] :tags {:context "worker" :role "judgment"}}
  "kubelet が Pod の全部を置く cgroup(kubepods — Node の allocatable を掛ける cgroup)の dir を、worker の container の cgroup の書ける見え
   cgroup-dir(DOEFF_WORKER_CGROUP_DIR)と、host の cgroupfs の mount の場所 host-root(WORKER_CGROUP_HOST)から求めるため: cgroup-dir の
   host-root からの path の中で、名が PODS-CGROUP-NAMES の物のいちばん上。cgroup-dir が host-root の下に無い・kubepods の下に居ない
   (kubelet の下で動かない worker)なら None。"
  (val root (.rstrip (posixpath.normpath host-root) "/"))
  (val own (posixpath.normpath cgroup-dir))
  (when (not (.startswith own (+ root "/")))
    (return None))
  (val parts (.split (cut own (+ (len root) 1) None) "/"))
  (val at (next (gfor #(i part) (enumerate parts) :if (in part PODS-CGROUP-NAMES) i) None))
  (if (is at None) None (posixpath.join root #* (cut parts 0 (+ at 1)))))
