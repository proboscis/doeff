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
;;; 余裕は、掛け直しの間(見張りの回の 5 秒)に jobs と準備の層の外(ほかの Pod・常駐の job・worker の本体)が伸びる分を覆う。jobs の層の
;;; 中の跳ね(atlas の 11-01 08:43 は 90 秒で約 5 GiB)は kernel が止めるので余裕に入れない。
;;;
;;; 準備の層(prepare — keeper の版の準備・deploy/boot.sh の keeper_swap): 版上げに要る 1 回きりの短い仕事なので、task と同じ縮む層に
;;; 置かない(縮めて殺しても memory は守れず、版上げだけが止まる — k3s-1 の 11-02 は jobs の上限 61〜139 MiB で準備の bytecode の焼きが
;;; OOM で落ち続けた)。準備の層の memory.max は宣言の静的な値(準備の山)で、worker は掛け直さない。準備が走っている間(層に process が
;;; 居る)は、その上限までの残り(上限 − 今の使い)を線の空きから先に取り置き、jobs の層の上限から引く(prepare-reserve)— 準備が山まで
;;; 伸びても線を越えない。準備を始めてよいかは keeper が決める: worker は回ごとに「追い出しまでの空き − 余裕 − 取り置き」(prepare-room)
;;; を keeper の読む file へ書き、keeper はそれが準備の層の上限より小さい間は準備を起こさずに待つ(OOM で殺さない)。
;;;
;;; 会話の CLI の層(clis — 上に載る系の仕事を余りの空きで受ける機体 helios・atlas の限る層): 宣言の静的な memory.max は床(常駐の job の予算から
;;; 引いた量・今までの上限)で、機体の宣言に天井(WORKER_CLI_LAYER_CEILING)が在る機体だけ、worker は回ごとに床と天井の間で掛け直す
;;; (clis-limit)。空きは 2 つ: 線の空き(free = 追い出しまでの空き − 余裕 − 準備の取り置き)と container の中の空き(box = container の上限 −
;;; container の working set − 余裕 − jobs と clis の外の限る層の宣言の上限の和)。clis の今の使いの上に残す量は、
;;;   広げる量  2 つの空きの小さい方の CLIS-ROOM-SHARE(残りは jobs の層へ)と、
;;;   残す量    前の回の上限までの残りのうち、jobs の層を 0 まで縮めれば 2 つの空きの内に収まる量
;;; の大きい方。広げた上限を次の回に縮めないのは、受けたばかりの会話がまだ伸びている間に上限を使いまで下げると、kernel が層の中で会話の
;;; CLI を回収・OOM で止めるため(jobs の層は task 1 本の OOM で済むが、会話の CLI はターンを失う)。機体の外の Pod が跳ねて空きが縮むと、
;;; 先に jobs の層が縮み(task が止まる)、jobs の層を 0 まで縮めても足りない分だけ clis の層が縮む。どの回も clis の今の使い
;;; (memory.current)と床より下げないので、走っていた会話は今までの静的な上限より狭くならない。
;;; jobs の層の上限は、clis に床を越えて貸した量(clis-lent)を準備の取り置きと同じく線の空きから引き(同じ空きを 2 つの層に重ねて
;;; 貸さない)、container の中の空きから clis の上限までの残りを引いた値(jobs-box-limit)も越えない。
;;; 会話を受ける turn-host は clis の memory.max を回ごとに読み、余白(上限 − working set)で新しいターンを受けるかを決める。生かす CLI の
;;; 本数の上限は起動の時に天井から導く(worker が job の子へ天井を渡す — worker/entry/main の CLI-CEILING-CHILD-VAR)。
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
(import math [ceil])
(import posixpath)
(import re)
(import doeff_cluster._worker.core.memory_rules [MIB NodeMemory])

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


;; 線の種類(閉じた型 — 頭の註): ALLOCATABLE = allocatable の線・NODE = 機体の線。
(defenum LineKind ALLOCATABLE NODE)


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


(defk line-name [line]
  {:pre [(: line (| EvictionLine None))] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "log の欄に、見張りと heartbeat が判じた memory の出どころ(線の種類か、線の無い機体の machine)を名指すため。"
  (if (is line None) "machine" line.kind.value))


(defk jobs-limit [jobs-current line margin cap reserve]
  {:pre [(: jobs-current int) (>= jobs-current 0) (: line EvictionLine) (: margin int) (>= margin 0) (: cap (| int None)) (: reserve int)
         (>= reserve 0)]
   :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "jobs の層に掛ける memory.max を求めるため(頭の註): jobs の今の使い + 追い出しまでの空き − 余裕 − 準備の取り置き reserve を、0 と
   宣言の静的な上限 cap(None = 上限なし)の間に収める。空きが余裕と取り置きの和より小さければ jobs の今の使いより下になる — kernel が
   jobs の層の中で回収し、足りなければ jobs の中の process を止める(準備の層は縮めない)。"
  (val wanted (max 0 (- (+ jobs-current line.room-bytes) margin reserve)))
  (if (is cap None) wanted (min cap wanted)))


(defk clis-limit [clis-current free box floor ceiling before jobs-current]
  {:pre [(: clis-current int) (>= clis-current 0) (: free int) (: box (| int None)) (: floor int) (>= floor 0) (: ceiling int)
         (>= ceiling floor) (: before (| int None)) (: jobs-current int) (>= jobs-current 0)]
   :post [(: % int) (>= % clis-current) (>= % floor)] :tags {:context "worker" :role "judgment"}}
  "会話の CLI の層に掛ける memory.max を求めるため(頭の註の会話の CLI の層): free = 線の空き − 余裕 − 準備の取り置き・box = container の
   中の空き(None = container に上限が無い)・before = 前の回に書いた上限(None = まだ)。clis の今の使いの上に残す量は、広げる量(free と
   box の小さい方の CLIS-ROOM-SHARE・負は 0)と、前の回の上限までの残りのうち jobs の層を 0 まで縮めれば線と container の内に残る量の
   大きい方。それを床と天井の間に収め、clis の今の使いより下げない(天井を越えて使っている読みは使いのまま)。"
  (val pool (if (is box None) free (min free box)))
  (val grow (max 0 (int (* pool CLIS-ROOM-SHARE))))
  (val keep (if (is before None) 0 (max 0 (min (- before clis-current) (+ pool jobs-current)))))
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


(defk jobs-box-limit [jobs-current box clis-limit clis-current]
  {:pre [(: jobs-current int) (>= jobs-current 0) (: box int) (: clis-limit int) (: clis-current int) (>= clis-current 0)]
   :post [(: % int) (>= % 0)] :tags {:context "worker" :role "judgment"}}
  "container の中の空きから決まる jobs の層の上限を求めるため(頭の註の会話の CLI の層): jobs の今の使い + container の中の空き − clis の
   層の上限までの残り(0 未満は 0)。jobs の層と clis の層が残りまで伸びても container の上限の手前に余裕が残る。"
  (max 0 (- (+ jobs-current box) (max 0 (- clis-limit clis-current)))))


(defk prepare-reserve [cap current populated]
  {:pre [(: cap int) (>= cap 0) (: current int) (>= current 0) (: populated bool)] :post [(: % int) (>= % 0)]
   :tags {:context "worker" :role "judgment"}}
  "準備の層の上限 cap までの残りのうち、線の空きから先に取り置く量を求めるため(頭の註): 準備が走っている(populated — 層に process が
   居る)間は 上限 − 今の使い(使いが上限を越えた読みは 0)、走っていなければ 0(終わった準備の page cache の残りは取り置かない)。"
  (if populated (max 0 (- cap current)) 0))


(defk prepare-room [line margin reserve]
  {:pre [(: line EvictionLine) (: margin int) (>= margin 0) (: reserve int) (>= reserve 0)] :post [(: % int)]
   :tags {:context "worker" :role "judgment"}}
  "keeper が新しい準備を起こしてよいかを判じる空き(頭の註)を求めるため: 追い出しまでの空き − 余裕 − 走っている準備の取り置き。
   keeper はこれが準備の層の上限以上の時だけ準備を起こす(負もありうる — 線の手前に余裕が残っていない)。"
  (- line.room-bytes margin reserve))


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
