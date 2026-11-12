;;; 追い出しの線の読みと jobs の層の上限の掛け直し(判断は worker/core/eviction_rules)。機体の memory の見張り
;;; (worker/protocol/memory_watch)が回のたびに頭で呼ぶ:
;;;   eviction-noted  1 回: Node の宣言の memory(capacity・allocatable)を要る時だけ API server から読み直し、host の cgroupfs から kubepods と
;;;                   root の cgroup を読んで追い出しまでの空きを数え、jobs の層の memory.max を「jobs の今の使い + 空き − 余裕」(上限は
;;;                   宣言の静的な値)に掛け直す。答え = 今の線(None = 線が無い・読めない — 見張りは機体の MemAvailable で判じる)。
;;;                   準備の層(prepare — keeper の版の準備)が在れば、その使いと process の有無を読み、走っている準備の山までの残りを
;;;                   jobs の上限から引き(取り置き)、「空き − 余裕 − 取り置き」と準備が線へ足しうる量(上限 − 層の今の working set)を
;;;                   keeper の読む file(room-file)に書く。keeper の版の file の版がこの世代の版と違い、準備がまだ走っていない間は、
;;;                   足しうる量を新しい受けの上限と clis への貸しから引いて入れ物に残す(heartbeat が名乗る — 準備を待つ間の取り置き)。余裕は機体の宣言の床と、回ごとに測る jobs と準備の層の外の working set の増えから導く(worker/core/
;;;                   eviction_rules の margin-of)。
;;;                   会話の CLI の層(clis)に天井の宣言が在る機体は、jobs の層より先に clis の層の memory.max を床(宣言の静的な値)と天井の
;;;                   間で掛け直し(worker/core/eviction_rules の clis-limit — clis の今の使いより下げず、使いの上には需要の余白だけを
;;;                   置いた task の取り置きの外の空きの内で残す)、床を越えて貸した量を jobs の上限から引き、jobs の上限を container の中の
;;;                   空きの内にも収める。container の中の空きは、jobs と clis の外の限る層の取り置き(今掛けている上限 − 層の working set)を
;;;                   引いて数え、層の余地の宣言が在る層はその前に memory.max を回収で返らない量 + 余地に掛け直す(box-layers-held)。置いた task の取り置きは、heartbeat の返事の task の要り(入れ物の needs)と見張りの読みの task ごとの
;;;                   使いから数える。
;;;                   命令の層(commands)の線の宣言(枠の file の行の commandLineFile と commandFloorMiB — 入れ物の command-line-file と
;;;                   command-floor-bytes)が在る機体は、最後に命令の層の memory.max を MemAvailable と線から掛け直す(worker/core/eviction_rules の
;;;                   command-layer-limit)。線の file を読めない・読み違える回は宣言の静的な値に戻す(古い空きで決めた上限を残さない)。
;;; 線を読めない間(Node の宣言をまだ読めない・cgroup の file を読めない・Node と cgroup が食い違う)は、jobs の層を宣言の静的な値に戻す
;;; (古い空きで決めた上限を残さない)。clis の層は床と clis の今の使いの大きい方に戻す(使いを読めない回は書かない)。guard が偽の見張り(起動の引数 --memory-guard off)は数えて log に出すだけで書かない。
;;; 退いた世代(keeper の世代の持ち主): keeper が版を入れ替えた後、前の世代は自分の task を終わりまで回すため同じ container に残り、同じ
;;; cgroup の層の file を見ている。層の memory.max と keeper の file を書くのは、いちばん新しい ready の世代の見張りだけ — keeper はそれが
;;; ready になった後に前の世代の dir へ印 draining を置く(deploy/boot.sh の keeper_retire)ので、印(settings の retired-file)を読んだ
;;; 見張りは guard が偽の見張りと同じく数えて log に出すだけになり、真に戻らない。前の世代は置き先が新しい世代に置いた task の要りを
;;; heartbeat の返事で受けないので、書けば取り置きを 0 と数えた上限で新しい世代の task を kernel に止めさせる(2026-11-10 — 前の世代が
;;; jobs の層を 0 へ書き、新しい世代に置いた task の準備が終了 -9 で止まり続けた)。
;;; 世代の書き手(keeper の世代の持ち主): 印だけでは、新しい世代が起きてから前の世代に印が付くまで(新しい世代が coordinator に名乗って
;;; ready になるまで — 受け渡しの待ちの間は長い)、2 つの世代が交互に書く。新しい世代も前の世代が置いた task の要りを受けないので、書けば
;;; 前の世代の task を同じ道で止めさせる。そこで keeper の世代は、同じ keeper の dir(retired-file の 2 つ上 — deploy/boot.sh の
;;; $keeper_dir/gens)に自分より前に起きた印の無い世代が在る間も書かない(worker/core/eviction_rules の writing-elder-of — 書くのは印の無い
;;; 世代のいちばん古い 1 つ)。keeper は新しい世代が ready になった後にだけ前の世代へ印を置くので、書き手は前の世代から新しい世代へ 1 つずつ
;;; 移り、2 つが同時に書く間が無い。終わった前の世代の dir は keeper が次の歩で消す。前の世代が居なくなったと読んだ後は読み直さない。
;;;
;;; log の行(名 + 欄の形):
;;;   EVICTION-LINE-LOG      線を読めるようになった時と、Node の宣言の値が替わった時に 1 行: capacity・allocatable・導いた E・kubepods の
;;;                          memory.max。
;;;   EVICTION-UNREAD-LOG    線を読めない訳が変わった時に 1 行(Node の読みの訳は前の値を使い続ける間も言う)。
;;;   JOBS-SQUEEZE-LOG       jobs の層の上限を jobs の今の使いより下げた時(追い出しまでの空きが余裕を割った — kernel が jobs の中で
;;;                          回収し、足りなければ jobs の中の process を止める)に、その続きの 1 回につき 1 行: 線・空き・jobs の使いと上限。
;;;   JOBS-LIMIT-FAILED-LOG  jobs の層の memory.max を書けない・jobs と準備の層の使いを読めない・keeper の file を書けない訳が変わった
;;;                          時に 1 行。
;;;   CLIS-LENT-LOG          clis の層の上限が床を越えた(貸し始めた)時と、床へ戻った時に 1 行: 上限・床・天井・clis の使い・線の空き。
;;;   MARGIN-LOG             測りから導いた余裕が宣言の床を越えた時と、床へ戻った時に 1 行: 余裕・床・測った増えの数。
;;;   COMMANDS-SQUEEZE-LOG   命令の層の上限を回収で返らない量より下げた時(kernel が層の中の process を止める)に、その続きの 1 回につき 1 行: 上限・
;;;                          回収で返らない量・tmpfs の量・線と MemAvailable の差。
;;;   COMMANDS-LIMIT-FAILED-LOG 命令の層の memory.max を書けない・読めない訳が変わった時に 1 行。
;;;   RETIRED-LOG            keeper が退かせ始めた印を読んだ時に 1 行(以後この世代は層の上限と keeper の file を書かない): 印の file。
;;;   ELDER-WRITES-LOG       層を書く前の世代が替わった時(居ると読んだ時・居なくなった時)に 1 行: 前の世代の名("" = 居なくなった — この世代が
;;;                          書き始める)。
;;; room-file の中身(keeper が sh で読む 1 行): 「<準備に使える空き MiB> <準備が線へ足しうる量 MiB>」(空きは負もありうる)。線を読めない回は
;;; 書かない(keeper は古い file を読まない — deploy/boot.sh の keeper_swap)。
;;; Node は自区画の API server から ServiceAccount の token で読む(読みの答え手は入口が並べる in-cluster-api-routed と HTTP の答え手)。
;;; cgroup の file の読みと書きは file の効果(本物の答え手は入口が並べる offloaded-file-handler — jobs の層の上限を使いより下げる書きは
;;; kernel が回収を終えるまで返らないので、scheduler の thread で待たない)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord・defwire の展開が名指す
(import posixpath)
(import urllib.parse [quote :as url-quote])
(import doeff_hy._wire [Malformed parse-json])
(import doeff_core_effects [slog])
(import doeff_core_effects [FileFailed ListDirectory PathKind ReadText WriteText])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_cluster._worker.core.memory_rules [MIB meminfo-kib])
(import doeff_cluster._worker.core.eviction_rules [EVICTION-MARGIN-BYTES MARGIN-WINDOW CommandLayerUse EvictionLine KubeletMemory PodsMemory quantity-bytes
                                                  pods-memory-of root-working-set-of eviction-line jobs-limit stat-bytes working-set
                                                  prepare-reserve prepare-room prepare-addable prepare-waiting prepare-wait-reserve margin-of kernel-bytes-of clis-limit clis-lent box-free
                                                  jobs-box-limit placed-pending kept-line fresh-keep-room command-layer-use-of command-layer-limit
                                                  CLI-TURN-BYTES CLI-TURN-SLOTS box-layer-limit layer-hold writing-elder-of])
(import doeff_cluster._worker.core.env_rules [oom-kills-of])
(import doeff_cluster._worker.core.load_rules [keep-line-of])

(val EVICTION-LINE-LOG "worker: 追い出しの線を読めた")
(val EVICTION-UNREAD-LOG "worker: 追い出しの線を読めない")
(val JOBS-SQUEEZE-LOG "worker: 追い出しまでの空きが余裕を割ったので jobs の層の上限を jobs の使いより下げた")
(val JOBS-LIMIT-FAILED-LOG "worker: jobs の層の memory の上限を掛け直せない")
(val MARGIN-LOG "worker: 追い出しの線の手前に残す余裕を、測った jobs と準備の層の外の伸びから替えた")
(val CLIS-LENT-LOG "worker: 会話の CLI の層の memory の上限を、空きから床の上へ広げた・床へ戻した")
(val COMMANDS-SQUEEZE-LOG "worker: MemAvailable が線を割ったので命令の層の上限を回収で返らない量より下げた")
(val COMMANDS-LIMIT-FAILED-LOG "worker: 命令の層の memory の上限を掛け直せない")
(val RETIRED-LOG "worker: keeper がこの世代を退かせ始めた — cgroup の層の memory の上限と keeper の file を書かず、数えて log に出すだけにする")
(val ELDER-WRITES-LOG "worker: cgroup の層の memory の上限を書く前の世代が替わった — 前の世代が居る間はこの世代は書かない")
;; API server の断りの本文を訳に載せる長さと、要求 1 本の時間切れ(秒)。
;; jobs の層の oom_kill の増えの刻を残す長さ(ms — EvictionCell の oom-at-ms。見張りの止めた印を残す間 MemoryWatchSettings.kill-mark-seconds
;; の既定と同じ — 調整ループが終わった子を回収するまでの間を十分に覆う)。
(val OOM-MARK-MS (* 600 1000))
(val REFUSAL-BODY-LIMIT 300)
(val REQUEST-TIMEOUT-SECONDS 5.0)


(defrecord BoxLayerSettings
  "container の中の限る層 1 つ(jobs と clis の外で memory.max を持つ層 — worker/core/eviction_rules の頭の註の container の中の限る層)の
   設定: name = 層の名・dir = 層の dir・cap-bytes = 宣言の静的な memory.max(天井)・floor-bytes = 機体の宣言の層の余地(None = 宣言が
   無い — 掛け直さず静的な値のまま)。"
  {:check [(>= cap-bytes 0) (or (is floor-bytes None) (<= 0 floor-bytes cap-bytes))]}
  (#^ str name)
  (#^ str dir)
  (#^ int cap-bytes)
  (setv #^ (| int None) floor-bytes None))


(defrecord CliLayerSettings
  "会話の CLI の層(clis)の掛け直しの設定(入口が起動の時に 1 度だけ作る — worker/core/eviction_rules の頭の註の会話の CLI の層): dir = clis の
   層の dir・floor-bytes = 宣言の静的な memory.max(床)・ceiling-bytes = 機体の宣言の天井・container-dir = worker の container の cgroup の
   dir(memory.max と working set を読む)・turn-room-bytes = 需要の余白(会話の CLI の新しい受け k 本分 — 機体の宣言の 1 本の要り × k)・
   layers = jobs と clis の外の限る層(BoxLayerSettings の tuple — container の中の空きから取り置く)・kept-file = 上に載る系が clis に
   残させる余白(byte の数)を書く file(worker/core/eviction_rules の頭の註の帳面の 2' — \"\" = 読まない)。"
  {:check [(>= floor-bytes 0) (>= ceiling-bytes floor-bytes) (>= turn-room-bytes 0)]}
  (#^ str dir)
  (#^ int floor-bytes)
  (#^ int ceiling-bytes)
  (#^ str container-dir)
  (setv #^ int turn-room-bytes (* CLI-TURN-SLOTS CLI-TURN-BYTES))
  (setv #^ tuple layers #())
  (setv #^ str kept-file ""))


(defrecord BoxLayerLimit
  "container の中の限る層 1 つに最後に掛けた memory.max(入れ物 EvictionCell の box-limits — 命令の層の線の掛け直しがこれを越えて書かない):
   name = 層の名・limit-bytes = 書いた上限。"
  {:check [(>= limit-bytes 0)]}
  (#^ str name)
  (#^ int limit-bytes))


(defrecord BoxLayerUse
  "container の中の限る層 1 つの読み: use = 回収で返らない量の内訳(worker/core/eviction_rules の command-layer-use-of)・working-set-bytes =
   memory.current − inactive_file(container の working set に数え済みの分)。"
  {:check [(>= working-set-bytes 0)]}
  (#^ CommandLayerUse use)
  (#^ int working-set-bytes))


(defrecord CommandLayerSettings
  "命令の層(commands)の掛け直しの設定(入口が起動の時に 1 度だけ作る — worker/core/eviction_rules の頭の註の命令の層): dir = 命令の層の dir・
   cap-bytes = 宣言の静的な memory.max・meminfo-path = MemAvailable を読む file・name = 層の名(container の中の限る層として掛けた上限
   EvictionCell.box-limits を引く名)。線の file と床は枠の file の行から入れ物へ来る(Pod を作り直さずに替えられる)。"
  {:check [(>= cap-bytes 0)]}
  (#^ str dir)
  (#^ int cap-bytes)
  (setv #^ str meminfo-path "/proc/meminfo")
  (setv #^ str name "commands"))


(defrecord EvictionSettings
  "追い出しの線の読みの設定(入口が起動の時に 1 度だけ作る — 頭の註): api = 自区画の API server の URL・token-file = ServiceAccount の
   token の file・node = この worker の Pod が置かれた Node の名・pods-dir = kubepods の cgroup の dir(host の cgroupfs の mount の中 —
   worker/core/eviction_rules の pods-dir-of)・host-root = host の cgroupfs の mount の場所(root の memory.stat を読む)・jobs-dir = jobs の
   層の dir(None = 層が無い — 線を数えて見張りに渡すだけ)・jobs-cap-bytes = jobs の層の memory.max の宣言の静的な値(None = 上限なし)・
   jobs-ceiling-bytes = jobs の層の天井の機体の宣言(None = 宣言が無い — 線と container の中の空きを読めた回も宣言の静的な値で切る・
   worker/core/eviction_rules の頭の註の jobs の層の天井。在る時は jobs-cap-bytes 以上で clis も在る)・node-read-seconds = Node の宣言を読み直す間(kubelet の設定は kubelet の起動し直しでしか替わらない)・node-retry-seconds = 読めなかった
   後に試し直す間・margin-bytes = jobs の層の上限に残す余裕の床(機体の宣言 WORKER_EVICTION_MARGIN — 無ければ worker/core/
   eviction_rules の EVICTION-MARGIN-BYTES)・prepare-dir = 準備の層の dir(None = 層が無い — keeper は準備を jobs の層で起こす)・
   prepare-cap-bytes = 準備の層の memory.max の宣言の静的な値(prepare-dir が在る時は必ず在る)・room-file = keeper の読む準備の空きの
   file(None = keeper の無い worker か準備の層が無い — 書かない)・clis = 会話の CLI の層の掛け直しの設定(None = 掛け直さない — 天井の
   宣言が無い機体)・commands = 命令の層の掛け直しの設定(None = 層の宣言に commands の memory.max が無い機体)・version-file = keeper の
   読む版の file(None = keeper の世代でない — 準備を待つ間の取り置きをしない)・own-version = この世代の版(version-file と組で在る)・
   retired-file = keeper がこの世代を退かせる時に置く印の file(None = keeper の世代でない — 印を読まない・頭の註の退いた世代)。"
  {:check [(> node-read-seconds 0) (> node-retry-seconds 0) (>= margin-bytes 0) (or (is jobs-cap-bytes None) (>= jobs-cap-bytes 0))
           (= (is prepare-dir None) (is prepare-cap-bytes None)) (or (is prepare-cap-bytes None) (>= prepare-cap-bytes 0))
           (or (is clis None) (is-not jobs-dir None)) (= (is version-file None) (is own-version None))
           (or (is jobs-ceiling-bytes None) (and (is-not clis None) (is-not jobs-cap-bytes None) (>= jobs-ceiling-bytes jobs-cap-bytes)))]}
  (#^ str api)
  (#^ str token-file)
  (#^ str node)
  (#^ str pods-dir)
  (#^ str host-root)
  (#^ (| str None) jobs-dir)
  (#^ (| int None) jobs-cap-bytes)
  (setv #^ (| int None) jobs-ceiling-bytes None)
  (setv #^ (| str None) prepare-dir None)
  (setv #^ (| int None) prepare-cap-bytes None)
  (setv #^ (| str None) room-file None)
  (setv #^ float node-read-seconds 300.0)
  (setv #^ float node-retry-seconds 30.0)
  (setv #^ int margin-bytes EVICTION-MARGIN-BYTES)
  (setv #^ (| CliLayerSettings None) clis None)
  (setv #^ (| CommandLayerSettings None) commands None)
  (setv #^ (| str None) version-file None)
  (setv #^ (| str None) own-version None)
  (setv #^ (| str None) retired-file None))


(defclass EvictionCell []
  "追い出しの線の読みが回から回へ持ち越す値(見張りの入れ物 MemoryCell が持ち、heartbeat の測りも読む)。kubelet = 最後に読めた Node の
   宣言の memory(None = まだ)・node-read-ms = 最後に Node を読みに行った刻・node-reason = 最後の Node の読みの訳(\"\" = 読めた)・
   line = 最後の回の線(None = 線が無いか読めない)・line-at-ms = その回の刻・told-kubelet = log に出した Node の宣言の値・
   told-unread = log に出した読めない訳・jobs-limit = 最後に書いた jobs の層の上限(None = まだ書いていない)・squeezed = 今の回に
   上限を jobs の使いより下げた続きの中・told-failure = log に出した jobs の層の失敗の訳・outside = 前の回の jobs と準備の層の外の
   working set(None = 前の回に数えられなかった — 増えを数えない)・growths = 回あたりの外の増え(負は 0・新しい MARGIN-WINDOW 回)・
   margin = 最後の回の余裕(None = まだ)・told-margin = log に出した余裕・reserve = 最後の回の準備の取り置き・prepare-wait = 最後の回の
   準備を待つ間の取り置き(byte — 0 = 待っていない。heartbeat が名乗る・worker/core/eviction_rules の prepare-wait-reserve)・uses = 見張りの最後の読みの
   job ごとの memory(JobUse の tuple — heartbeat の測りが task ごとの今の使いとして名乗る・2026-11-02)・uses-at-ms = その読みの刻(None =
   まだ)・jobs-current = 最後の回に読んだ jobs の層の memory.current(None = 読めなかった・層が無い)・jobs-held = 同じ回に読んだ jobs の層の
   使い(working set − slab_reclaimable — LayerMemory.held-bytes・None = 読めなかった・層が無い — jobs-intake との差が調整ループの観測する jobs の層の空き
   WorldView.jobs-room)・clis-limit = 最後に書いた clis の層の上限(None = まだ書いていない・掛け直さない)・
   clis-current = 最後の回に読んだ clis の層の memory.current(None = 読めなかった)・lent = 最後の回に clis に床を越えて貸した量
   (jobs の上限から引いた量)・box = 最後の回の container の中の空き(None = 数えなかった)・needs = 置き先が置いた task の要り(TaskNeed
   の tuple — heartbeat の返事を読んだ worker/protocol/coordinator_link が書く)・pending = 最後の回の置いた task の取り置き・jobs-intake =
   最後に jobs の層の上限と同じ回に決めた、新しい受けに名乗る上限(測った余裕で数え、取り置きを入れない — None = まだ。これと
   jobs-held の差が heartbeat の名乗る jobs の層の空きと、調整ループの観測する WorldView.jobs-room)・jobs-intake-urgent = 同じ回に
   上に載る系が残す線を外して追い出しの線だけで数えた新しい受けの上限(急ぎの task の受け — None = 線が jobs の層を縮めていない・まだ。
   worker/core/load_rules の頭の註の急ぎの task の空き)・keep-room = heartbeat の測りが最後に
   読んだ「機体の MemAvailable − 上に載る系が残す線」(byte — None = 線が無い・読めない。worker/core/eviction_rules の頭の註の上に載る系が
   残す線)・keep-room-at-ms = その読みの刻(None = まだ)・command-line-file = 命令の層の線の
   file(枠の file の行の commandLineFile を worker/protocol/coordinator_link が書く — \"\" = 掛け直さない)・command-floor-bytes = 短い命令の
   余地(同じく commandFloorMiB)・command-limit = 最後に書いた命令の層の上限(None = まだ書いていない)・command-use = 最後の回に読んだ
   命令の層の内訳(None = 読めなかった)・command-squeezed = 上限を回収で返らない量より下げた続きの中・told-command-failure = log に
   出した命令の層の失敗の訳・jobs-dir = jobs の層の dir(見張りの最初の掛け直しで書く — None = 層が無い・まだ)・oom-count = 最後に読んだ
   jobs の層の memory.events の oom_kill(None = まだ読めていない)・oom-at-ms = oom_kill が増えたのを読んだ刻(epoch ms の tuple — 古い順・
   OOM-MARK-MS の内の物 — 状態の報告が、その間に SIGKILL で終わった task の訳に載せる)・meters = 見張りの最後の読みの常駐の job ごとの
   CPU と memory(worker/core/memory_rules の JobMeter の tuple — heartbeat の測りが欄 jobUses として名乗る・2026-11-10)・meters-at-ms = その
   読みの刻(None = まだ)・retired = keeper がこの世代を退かせ始めた印を読んだ(頭の註の退いた世代 — 真になったら戻らず、印を読み直さない)・
   elder = 層を書いている前の世代の名(頭の註の世代の書き手 — None = 居ない・まだ読んでいない)・elder-gone = 前の世代が居ないと読んだ
   (真になったら戻らず、読み直さない)・
   declared = 最後に読めた heartbeat の返事の job の名 → 宣言の memory の山(byte — JobSpec.peak-mib を持つ job だけ。worker/protocol/coordinator_link が
   書き、機体の memory の見張りが job の根に添える — worker/core/memory_rules の頭の註の宣言の内・2026-11-11)・box-limits = container の中の
   限る層に最後に掛けた上限(BoxLayerLimit の tuple — 書いた層だけ。命令の層の線の掛け直しはその層の値を越えて書かない)・box-held =
   最後の回に container の中の空きから限る層の分として引いた取り置きの和(None = 数えなかった)。
   scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.kubelet None self.node-read-ms None self.node-reason "" self.line None self.line-at-ms None self.told-kubelet None
          self.told-unread "" self.jobs-limit None self.squeezed False self.told-failure "" self.outside None
          self.growths #() self.margin None self.told-margin None self.reserve 0 self.prepare-wait 0 self.uses #() self.uses-at-ms None
          self.jobs-current None self.jobs-held None self.clis-limit None self.clis-current None self.lent 0
          self.box None self.needs #() self.pending 0 self.jobs-intake None self.jobs-intake-urgent None self.keep-room None self.keep-room-at-ms None
          ;; 線の内の貸し(worker/core/load_rules の頭の註): lend-room = heartbeat の測りが最後に読んだ「MemAvailable − 貸す下の線」(byte —
          ;; None = 貸さない回)・lend-room-at-ms = その読みの刻・jobs-intake-lend = 同じ回に下の線で数えた借りの受けの上限(None = 貸さない)。
          self.lend-room None self.lend-room-at-ms None self.jobs-intake-lend None
          self.command-line-file "" self.command-floor-bytes 0 self.command-limit None self.command-use None
          self.command-squeezed False self.told-command-failure "" self.jobs-dir None self.oom-count None self.oom-at-ms #()
          self.meters #() self.meters-at-ms None self.retired False self.elder None self.elder-gone False self.declared {} self.box-limits #() self.box-held None)))


(defrecord LayerMemory
  "cgroup の層 1 つの読み: current-bytes = memory.current・working-set-bytes = memory.current − memory.stat の inactive_file(cAdvisor の
   数え方)・populated = 層か子に process が居る(cgroup.events の populated)・held-bytes = working set − memory.stat の slab_reclaimable
   (層の memory.max に当たると kernel が process を止める前に返す page cache と slab を除いた使い — 行の無い kernel は slab 0・0 未満は 0)。"
  {:check [(>= current-bytes 0) (>= working-set-bytes 0) (<= 0 held-bytes working-set-bytes)]}
  (#^ int current-bytes)
  (#^ int working-set-bytes)
  (#^ bool populated)
  (#^ int held-bytes))


(defwire NodeResourcesWire
  "Node の status の資源の欄のうち読む物: memory(k8s の量の綴り)。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str memory))


(defwire NodeStatusWire
  "Node の status のうち読む欄: capacity・allocatable。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ NodeResourcesWire capacity)
  (#^ NodeResourcesWire allocatable))


(defwire NodeWire
  "Node の読み(GET /api/v1/nodes/<名>)の答えの本文のうち読む欄: status。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ NodeStatusWire status))


(defk kubelet-memory-of-text [text]
  {:pre [(: text str)] :post [(: % (| KubeletMemory str))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "Node の読みの答えの本文を、Node の宣言の memory にするため。本文の形が違う・量を読めない・値が食い違う時は訳の文。"
  (<- parsed (| NodeWire Malformed) (parse-json NodeWire text))
  (when (isinstance parsed Malformed)
    (return "Node の答えの本文の形が違う(status.capacity.memory・status.allocatable.memory が無い)"))
  (try
    (<- capacity int (quantity-bytes parsed.status.capacity.memory))
    (<- allocatable int (quantity-bytes parsed.status.allocatable.memory))
    (KubeletMemory :capacity-bytes capacity :allocatable-bytes allocatable)
    (except [error ValueError]
      (+ "Node の memory の値を読めない: " (str error)))))


(defk kubelet-memory-read [settings]
  {:pre [(: settings EvictionSettings)] :post [(: % (| KubeletMemory str))] :tags {:context "worker" :role "protocol" :spells "http"}}
  "この worker の Node の宣言の memory を API server から読むため。token を読めない・届かない・断られた・形の違う答えは訳の文。"
  (<- token (| str FileFailed) (ReadText settings.token-file))
  (when (isinstance token FileFailed)
    (return (.format "ServiceAccount の token の file を読めない: {}" token.detail)))
  (when (not (.strip token))
    (return "ServiceAccount の token の file が空"))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest "GET" (+ settings.api "/api/v1/nodes/" (url-quote settings.node :safe ""))
                   :headers {"authorization" (+ "Bearer " (.strip token)) "accept" "application/json"}
                   :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
  (when (isinstance answer HttpFailed)
    (return (+ "API server に届かない: " answer.detail)))
  (when (not (<= 200 answer.status 299))
    (return (.format "API server が Node {} の読みを {} で断った: {}" settings.node answer.status (cut answer.text 0 REFUSAL-BODY-LIMIT))))
  (<- memory (| KubeletMemory str) (kubelet-memory-of-text answer.text))
  memory)


(defk text-read [path]
  {:pre [(: path str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "cgroup の file 1 つを読むため。読めなければ ValueError(訳に path を挙げる — 呼び手が線を読めない訳にする)。"
  (<- text (| str FileFailed) (ReadText path))
  (when (isinstance text FileFailed)
    (raise (ValueError (.format "{} を読めない: {}" path text.detail))))
  text)


(defk cgroup-line-read [settings kubelet]
  {:pre [(: settings EvictionSettings) (: kubelet KubeletMemory)] :post [(: % (| EvictionLine None str))]
   :tags {:context "worker" :role "protocol"}}
  "kubepods と root の cgroup を読んで追い出しの線を数えるため。kubepods の file を読めない・形が違う・Node と食い違う時は訳の文。
   root の memory.stat を読めない kernel は allocatable の線だけで数える。線が無い(kubepods の memory.max が max)なら None。"
  (try
    (<- max-text str (text-read (+ settings.pods-dir "/memory.max")))
    (<- current-text str (text-read (+ settings.pods-dir "/memory.current")))
    (<- stat-text str (text-read (+ settings.pods-dir "/memory.stat")))
    (<- pods PodsMemory (pods-memory-of max-text current-text stat-text))
    (<- root-text (| str FileFailed) (ReadText (+ settings.host-root "/memory.stat")))
    (var root None)
    (when (isinstance root-text str)
      (<- root-set int (root-working-set-of root-text))
      (:= root root-set))
    (<- line (| EvictionLine None) (eviction-line kubelet pods root))
    line
    (except [error ValueError]
      (str error))))


(defk kubelet-noted [settings cell now-ms]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: now-ms int)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "Node の宣言の memory を、まだ読めていない間は node-retry-seconds ごとに、読めた後は node-read-seconds ごとに読み直すため。読めない
   読みは前に読めた値を残す(kubelet の設定は kubelet の起動し直しでしか替わらない)。"
  (val wait (if (or (is cell.kubelet None) cell.node-reason) settings.node-retry-seconds settings.node-read-seconds))
  (when (and (is-not cell.node-read-ms None) (<= cell.node-read-ms now-ms) (< (- now-ms cell.node-read-ms) (* wait 1000)))
    (return None))
  (setv cell.node-read-ms now-ms)
  (<- read (| KubeletMemory str) (kubelet-memory-read settings))
  (if (isinstance read str)
      (setv cell.node-reason read)
      (setv cell.node-reason "" cell.kubelet read))
  None)


(defk line-told [settings cell line reason]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: reason str)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "線を読めない訳が変わった時と、線を読めるようになった・Node の宣言が替わった時に log へ 1 行出すため(頭の註)。"
  (when (!= reason cell.told-unread)
    (setv cell.told-unread reason)
    (when reason
      (<- (slog EVICTION-UNREAD-LOG :level "warning" :reason reason :node settings.node))))
  (val kubelet cell.kubelet)
  (when (and (not reason) (is-not kubelet None) (!= kubelet cell.told-kubelet))
    (setv cell.told-kubelet kubelet)
    (<- (slog EVICTION-LINE-LOG :level "info" :node settings.node :capacity-mib (// kubelet.capacity-bytes MIB)
              :allocatable-mib (// kubelet.allocatable-bytes MIB)
              :line (if (is line None) None line.kind.value) :line-mib (if (is line None) None (// line.size-bytes MIB))
              :room-mib (if (is line None) None (// line.room-bytes MIB)))))
  None)


(defk layer-read [name dir populated]
  {:pre [(: name str) (: dir str) (: populated bool)] :post [(: % (| LayerMemory str))] :tags {:context "worker" :role "protocol"}}
  "cgroup の層 1 つの使い(memory.current と working set)を読むため。populated = cgroup.events の populated も読むか(偽なら populated は
   真と置く — jobs の層は process の有無で数えを替えない)。読めない・数でない時は訳の文(name = 訳に挙げる層の名)。"
  (<- current-text (| str FileFailed) (ReadText (+ dir "/memory.current")))
  (when (isinstance current-text FileFailed)
    (return (.format "{} の層の memory.current を読めない: {}" name current-text.detail)))
  (when (not (.isdigit (.strip current-text)))
    (return (.format "{} の層の memory.current {!r} が数でない" name current-text)))
  (val current (int (.strip current-text)))
  (<- stat-text (| str FileFailed) (ReadText (+ dir "/memory.stat")))
  (when (isinstance stat-text FileFailed)
    (return (.format "{} の層の memory.stat を読めない: {}" name stat-text.detail)))
  (var inactive 0)
  (var slab 0)
  (try
    (<- found int (stat-bytes stat-text "inactive_file"))
    (:= inactive found)
    ;; slab_reclaimable の行の無い kernel は回収できる slab を 0 と読む(held-bytes は working set のまま)。
    (when (any (gfor line (.splitlines stat-text) (.startswith line "slab_reclaimable ")))
      (<- reclaimable int (stat-bytes stat-text "slab_reclaimable"))
      (:= slab reclaimable))
    (except [error ValueError]
      (return (.format "{} の層の memory.stat を読めない: {}" name error))))
  (<- set-bytes int (working-set current inactive))
  (val held (max 0 (- set-bytes slab)))
  (var alive True)
  (when populated
    (<- events (| str FileFailed) (ReadText (+ dir "/cgroup.events")))
    (when (isinstance events FileFailed)
      (return (.format "{} の層の cgroup.events を読めない: {}" name events.detail)))
    (:= alive (in "populated 1" (.splitlines events))))
  (LayerMemory :current-bytes current :working-set-bytes set-bytes :populated alive :held-bytes held))


(defk margin-noted [settings cell line jobs prepare]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: jobs (| LayerMemory None))
         (: prepare (| LayerMemory None))]
   :post [(: % int)] :tags {:context "worker" :role "protocol"}}
  "この回の余裕を求めるため(worker/core/eviction_rules の margin-of): 線と jobs の層(と在れば準備の層)を読めた回は、その外の working set
   (線の使い − jobs − 準備)の前の回からの増えを残し、残した増えと宣言の床から余裕を導く。読めない回は前の回の外を忘れる(間の空いた
   増えを 1 回の増えに数えない)。余裕が床を越えた時と床へ戻った時に 1 行言う。答え = この回の余裕。"
  (val readable (and (is-not line None) (is-not jobs None) (or (is settings.prepare-dir None) (is-not prepare None))))
  (if readable
      (do (val outside (- line.used-bytes jobs.working-set-bytes (if (is prepare None) 0 prepare.working-set-bytes)))
          (when (is-not cell.outside None)
            (setv cell.growths (cut (+ cell.growths #((max 0 (- outside cell.outside)))) (- MARGIN-WINDOW) None)))
          (setv cell.outside outside))
      (setv cell.outside None))
  (<- margin int (margin-of cell.growths settings.margin-bytes))
  (setv cell.margin margin)
  (val raised (> margin settings.margin-bytes))
  (when (!= raised (and (is-not cell.told-margin None) (> cell.told-margin settings.margin-bytes)))
    (setv cell.told-margin margin)
    (<- (slog MARGIN-LOG :level "info" :margin-mib (// margin MIB) :floor-mib (// settings.margin-bytes MIB)
              :samples (len cell.growths))))
  margin)


(defk room-written [settings line margin reserve addable]
  {:pre [(: settings EvictionSettings) (: line EvictionLine) (: margin int) (: reserve int) (: addable (| int None))] :post [(: % str)]
   :tags {:context "worker" :role "protocol"}}
  "keeper の読む file(room-file)へ、準備に使える空きと準備が線へ足しうる量 addable を 1 行で書くため(頭の註の room-file の中身)。file か
   準備の層が無ければ書かない。答え = 書けない訳(\"\" = 書いた・書かない)。"
  (when (or (is settings.room-file None) (is settings.prepare-cap-bytes None))
    (return ""))
  (<- room int (prepare-room line margin reserve))
  (<- written (| FileFailed None) (WriteText settings.room-file (.format "{} {}\n" (// room MIB) (// addable MIB))))
  (if (isinstance written FileFailed)
      (.format "keeper の準備の空きの file {} を書けない: {}" settings.room-file written.detail)
      ""))


(defk container-max-read [dir]
  {:pre [(: dir str)] :post [(: % (| int None str))] :tags {:context "worker" :role "protocol"}}
  "worker の container の cgroup の memory.max を読むため(None = max — 上限が無い)。読めない・綴りが違う時は訳の文。"
  (<- text (| str FileFailed) (ReadText (+ dir "/memory.max")))
  (when (isinstance text FileFailed)
    (return (.format "container の memory.max を読めない: {}" text.detail)))
  (try
    (<- found (| int None) (kernel-bytes-of text))
    found
    (except [error ValueError]
      (.format "container の memory.max を読めない: {}" error))))


(defk box-layer-read [layer]
  {:pre [(: layer BoxLayerSettings)] :post [(: % (| BoxLayerUse str))] :tags {:context "worker" :role "protocol"}}
  "container の中の限る層 1 つの memory.current と memory.stat を、回収で返らない量の内訳と working set にするため。読めない・形の違う時は
   訳の文。"
  (<- current (| str FileFailed) (ReadText (+ layer.dir "/memory.current")))
  (when (isinstance current FileFailed)
    (return (.format "{} の層の memory.current を読めない: {}" layer.name current.detail)))
  (<- stat (| str FileFailed) (ReadText (+ layer.dir "/memory.stat")))
  (when (isinstance stat FileFailed)
    (return (.format "{} の層の memory.stat を読めない: {}" layer.name stat.detail)))
  (try
    (<- use CommandLayerUse (command-layer-use-of current stat))
    (<- inactive int (stat-bytes stat "inactive_file"))
    (<- set-bytes int (working-set (int (.strip current)) inactive))
    (BoxLayerUse :use use :working-set-bytes set-bytes)
    (except [error ValueError]
      (.format "{} の層を読めない: {}" layer.name error))))


(defk box-layers-held [clis cell boxed]
  {:pre [(: clis CliLayerSettings) (: cell EvictionCell) (: boxed bool)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "container の中の限る層(jobs と clis の外)の memory.max を掛け直し、container の中の空きから引く取り置きの和を入れ物(box-held)に残すため
   (worker/core/eviction_rules の頭の註の container の中の限る層): 余地の宣言の在る層は、boxed(この回に container の中の空きを数える)なら
   回収で返らない量 + 余地(box-layer-limit)、数えない回は宣言の静的な値に戻す(古い使いで決めた上限を残さない)。取り置きは層ごとに
   今掛けている上限 − 層の working set(layer-hold)。層を読めない・書けない回は、その層の今の上限(書けなければ前に書いた値、まだなら
   静的な値)の全部を取り置く。答え = 読めない・書けない訳(\"\" = 揃った)。"
  (var held 0)
  (var failure "")
  (var limits #())
  (for [layer clis.layers]
    (<- read (| BoxLayerUse str) (box-layer-read layer))
    (val before (next (gfor l cell.box-limits :if (= l.name layer.name) l.limit-bytes) None))
    (var limit (if (is before None) layer.cap-bytes before))
    (when (is-not layer.floor-bytes None)
      (var wanted layer.cap-bytes)
      (when (and boxed (isinstance read BoxLayerUse))
        (<- found int (box-layer-limit read.use layer.floor-bytes layer.cap-bytes))
        (:= wanted found))
      (<- written (| FileFailed None) (WriteText (+ layer.dir "/memory.max") (str wanted)))
      (if (isinstance written FileFailed)
          (do (:= failure (or failure (.format "{} の層の memory.max に {} を書けない: {}" layer.name wanted written.detail)))
              (when (is-not before None)
                (:= limits (+ limits #((BoxLayerLimit :name layer.name :limit-bytes before))))))
          (do (:= limit wanted)
              (:= limits (+ limits #((BoxLayerLimit :name layer.name :limit-bytes wanted)))))))
    (if (isinstance read str)
        (do (:= failure (or failure read))
            (:= held (+ held limit)))
        (do (<- hold int (layer-hold limit read.working-set-bytes))
            (:= held (+ held hold)))))
  (setv cell.box-limits limits cell.box-held held)
  failure)


(defk clis-kept-read [path]
  {:pre [(: path str)] :post [(: % (| int str)) (or (isinstance % str) (>= % 0))] :tags {:context "worker" :role "protocol"}}
  "上に載る系が clis に残させる余白を読むため(CliLayerSettings の kept-file — worker/core/eviction_rules の頭の註の帳面の 2'): \"\" の path は
   0(読まない)。file の中身は byte の数 1 つ。file が無い・読めない時も 0(上に載る系が書く前の起動の間 — 残させる物がまだ無い)。中身が
   数でない時は読めない訳(呼び手は 0 で数え、訳を見張りの log へ渡す — clis の掛け直しは止めない)。"
  (when (= path "")
    (return 0))
  (<- text (| str FileFailed) (ReadText path))
  (when (isinstance text FileFailed)
    (return 0))
  (val value (.strip text))
  (if (.isdigit value)
      (int value)
      (.format "clis に残させる余白の file {} の中身が byte の数でない: {!r}" path value)))


(defk clis-limited [clis cell line margin reserve jobs-current pending]
  {:pre [(: clis CliLayerSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: margin int) (: reserve int)
         (: jobs-current (| int None)) (: pending int) (>= pending 0)]
   :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "会話の CLI の層の memory.max を掛け直し、床を越えて貸した量と container の中の空きを入れ物に残すため(worker/core/eviction_rules の
   clis-limit): 線と jobs の使いと container を読めた回は空きから置いた task の取り置き pending を除いて決めた上限(clis の今の使い +
   需要の余白・床と天井の間・使いより下げない)、線が無いか読めない回は床と clis の今の使いの大きい方。その前に jobs と clis の外の
   限る層を掛け直して取り置きを数える(box-layers-held)。clis の使いを読めない・書けない回は前の上限を残し、その上限 − 床を貸しのまま引く。上限が床を越えた
   時と床へ戻った時に 1 行言う。答え = 書けない・読めない訳(\"\" = 書いた)。"
  (setv cell.box None)
  (<- read (| LayerMemory str) (layer-read "clis" clis.dir False))
  (when (isinstance read str)
    (setv cell.clis-current None
          cell.lent (if (is cell.clis-limit None) 0 (max 0 (- cell.clis-limit clis.floor-bytes))))
    (return read))
  (val current read.current-bytes)
  (setv cell.clis-current current)
  (val readable (and (is-not line None) (is-not jobs-current None)))
  (<- layers-failure str (box-layers-held clis cell readable))
  (var failure layers-failure)
  (var free None)
  (var box None)
  (when readable
    (<- container (| LayerMemory str) (layer-read "container" clis.container-dir False))
    (<- container-max (| int None str) (container-max-read clis.container-dir))
    (cond
      (isinstance container str) (:= failure (or failure container))
      (isinstance container-max str) (:= failure (or failure container-max))
      True (do (:= free (- line.room-bytes margin reserve))
               (<- found (| int None) (box-free container-max container.working-set-bytes margin cell.box-held))
               (:= box found))))
  (var limit (max current clis.floor-bytes))
  (when (is-not free None)
    (<- kept (| int str) (clis-kept-read clis.kept-file))
    (when (isinstance kept str)
      (:= failure (or failure kept)))
    (<- found int (clis-limit current free box clis.floor-bytes clis.ceiling-bytes clis.turn-room-bytes pending
                              (if (isinstance kept str) 0 kept)))
    (:= limit found))
  (<- written (| FileFailed None) (WriteText (+ clis.dir "/memory.max") (str limit)))
  (when (isinstance written FileFailed)
    (setv cell.lent (if (is cell.clis-limit None) 0 (max 0 (- cell.clis-limit clis.floor-bytes))))
    (return (or failure (.format "clis の層の memory.max に {} を書けない: {}" limit written.detail))))
  (val before cell.clis-limit)
  (setv cell.clis-limit limit cell.box box)
  (<- lent int (clis-lent limit current clis.floor-bytes))
  (setv cell.lent lent)
  (when (!= (> limit clis.floor-bytes) (and (is-not before None) (> before clis.floor-bytes)))
    (<- (slog CLIS-LENT-LOG :level "info" :clis-limit-mib (// limit MIB) :floor-mib (// clis.floor-bytes MIB)
              :ceiling-mib (// clis.ceiling-bytes MIB) :clis-current-mib (// current MIB)
              :free-mib (if (is free None) None (// free MIB)) :box-free-mib (if (is box None) None (// box MIB)))))
  failure)


(defk jobs-limited [settings cell line guard now-ms]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: guard bool) (: now-ms int)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "jobs の層の memory.max を今の線から掛け直すため(頭の註): 線が在れば jobs の今の使い + 空き − 余裕 − 準備の取り置き − clis への貸し
   (上限は宣言の静的な値・天井を宣言した機体の container の中の空きを読めた回は天井 — 測った余裕の宣言の床を越えた分と clis への貸しでは、
   使い + 置いた task の取り置きより下げない・worker/core/eviction_rules の jobs-limit)、線が無ければ宣言の静的な値。同じ回に新しい受けに名乗る上限(cell.jobs-intake)も決める。線が在る回は
   keeper の読む準備の空きも書く。新しい受けの上限だけは、heartbeat の測りが新しい「MemAvailable − 上に載る系が残す線」を
   読めていれば、その線の上にも収める(worker/core/eviction_rules の kept-line — jobs の層の memory.max と clis の層と keeper の空きは追い出しの
   線のまま: 線を割っても、取り置きを持たない process〔実行環境の準備・名の無い子〕を層の中で OOM kill させない)。jobs の層が
   無い・guard が偽・上限なしで線も無い時は書かない。上限を jobs の使いより下げた続きの頭で 1 行言う。"
  (setv cell.jobs-dir settings.jobs-dir)
  (when (or (is settings.jobs-dir None) (not guard))
    (return None))
  (when (and (is line None) (is settings.jobs-cap-bytes None))
    (return None))
  (var failure "")
  (var current None)
  (var working None)
  (var jobs None)
  (<- jobs-read (| LayerMemory str) (layer-read "jobs" settings.jobs-dir False))
  (if (isinstance jobs-read str)
      (:= failure jobs-read)
      (do (:= jobs jobs-read) (:= current jobs-read.current-bytes) (:= working jobs-read.held-bytes)))
  (setv cell.jobs-current current cell.jobs-held working)
  (var prepare None)
  (when (is-not settings.prepare-dir None)
    (<- prepare-read (| LayerMemory str) (layer-read "準備" settings.prepare-dir True))
    (if (isinstance prepare-read str)
        (:= failure (or failure prepare-read))
        (:= prepare prepare-read)))
  (<- margin int (margin-noted settings cell line jobs prepare))
  ;; 準備の層を読めない回は、準備が走っているとみなして上限まで取り置き、足しうる量も上限のままにする(準備の山を線の計算から落とさない)。
  (var reserve 0)
  (var addable settings.prepare-cap-bytes)
  (when (is-not settings.prepare-cap-bytes None)
    (if (is prepare None)
        (:= reserve settings.prepare-cap-bytes)
        (do (<- held int (prepare-reserve settings.prepare-cap-bytes prepare.current-bytes prepare.populated))
            (:= reserve held)
            (<- can-add int (prepare-addable settings.prepare-cap-bytes prepare.working-set-bytes))
            (:= addable can-add))))
  (setv cell.reserve reserve)
  ;; keeper が準備を待つ間は、足しうる量を新しい受けの外に取り置く(worker/core/eviction_rules の頭の註の準備を待つ間の取り置き)。準備の層を
  ;; 読めない回は上の取り置きが上限を持つので、重ねて取り置かない。
  (var waiting 0)
  (when (and (is-not prepare None) (is-not settings.version-file None))
    (<- wanted (| str FileFailed) (ReadText settings.version-file))
    (<- waits bool (prepare-waiting (if (isinstance wanted str) wanted None) settings.own-version))
    (<- held int (prepare-wait-reserve waits prepare.populated addable))
    (:= waiting held))
  (setv cell.prepare-wait waiting)
  ;; 置いた task の取り置き(要り − 今の使い — 見張りの前の回の読みの使い)。clis の層へ新しく貸さず、jobs の層の上限に残す。
  (<- pending int (placed-pending cell.needs cell.uses))
  (setv cell.pending pending)
  ;; 会話の CLI の層を先に掛け直し、床を越えて貸した量を jobs の上限から引く(同じ空きを 2 つの層に重ねて貸さない)。
  (var lent 0)
  (when (is-not settings.clis None)
    (<- clis-failure str (clis-limited settings.clis cell line margin (+ reserve waiting) current pending))
    (:= failure (or failure clis-failure))
    (:= lent cell.lent))
  ;; memory.max は測った余裕の宣言の床を越えた分では使い + 取り置きより下げず(limit)、新しい受けに名乗る上限は測った余裕で数えて取り置きを
  ;; 入れない(intake — 置き先が置いた task の予約を自分で引く)。準備を待つ間の取り置きは intake からだけ引く(memory.max から引くと、
  ;; 走っている task を準備のために止める)。intake は層の held-bytes(working set − slab_reclaimable)を土台に数える: 層の memory.max に
  ;; 当たると kernel は task を止める前に回収できる page cache と slab を返すので、上限で決まる空きは上限 − held-bytes(線で決まる空きは
  ;; 土台によらない)。memory.current を土台にすると、process の居ない page cache が層の空きを塞いだ(node-c の 11-10 13:4x JST — 上限 8 GiB・
  ;; inactive_file 2112 MiB を使いに数え、層が空でも要り 5632 MiB の task を置けなかった)。working set だけでも、git と uv の file 操作が残す
  ;; slab_reclaimable が空きを塞いだ(同じ node-c の 15:1x JST — task 0 本の層の working set 4212 MiB のうち slab_reclaimable 1070 MiB)。
  (var limit settings.jobs-cap-bytes)
  (var intake settings.jobs-cap-bytes)
  (var urgent None)
  (var lend None)
  ;; jobs の層の天井(worker/core/eviction_rules の頭の註): 宣言した機体は、container の中の空きを読めた回だけ宣言の静的な値の代わりに天井で
  ;; 切る(下の box の上限が container の上限の手前で止める)。container を読めない回は静的な値のまま。
  (val boxed? (and (is-not settings.clis None) (is-not cell.box None) (is-not cell.clis-limit None) (is-not cell.clis-current None)))
  (val cap (if (and boxed? (is-not settings.jobs-ceiling-bytes None)) settings.jobs-ceiling-bytes settings.jobs-cap-bytes))
  (when (and (is-not line None) (is-not current None))
    (val floor-margin (min margin settings.margin-bytes))
    ;; 上に載る系が残す線(worker/core/eviction_rules の頭の註): 新しい受けに名乗る上限だけを線の上に収める。memory.max は追い出しの線だけで
    ;; 数える — 線で絞ると、線を割っている間は層の余地が置いた task の取り置きだけになり、取り置きを持たない process(実行環境の準備・
    ;; 名の無い子)を kernel が層の中で OOM kill した。名乗った要りを越えて伸びた task は、見張りが名指して止める(memory_watch)。
    (<- keep-room (| int None) (fresh-keep-room cell.keep-room cell.keep-room-at-ms now-ms))
    (<- kept EvictionLine (kept-line line keep-room))
    (<- evicted int (jobs-limit current line margin floor-margin cap (+ reserve lent) pending))
    (<- told int (jobs-limit working kept margin margin cap (+ reserve lent waiting) 0))
    ;; 急ぎの task の受け(worker/core/load_rules の頭の註の急ぎの task の空き): 同じ式を追い出しの線だけで数える。線が新しい受けを
    ;; 縮めていない回は名乗らない(None — 置き先は平常の受けで数える)。置いた後の memory.max は追い出しの線の上限のまま。
    (<- told-urgent int (jobs-limit working line margin margin cap (+ reserve lent waiting) 0))
    (:= urgent (if (< told told-urgent) (min evicted told-urgent) None))
    ;; 借りの受け(worker/core/load_rules の頭の註の線の内の貸し): 同じ式を貸す下の線で数える(下の線の方が低いので受けは広がる)。
    ;; 貸さない回は名乗らない。置いた後の memory.max は、急ぎの task と同じく追い出しの線の上限のまま。
    (<- lend-room (| int None) (fresh-keep-room cell.lend-room cell.lend-room-at-ms now-ms))
    (when (is-not lend-room None)
      (<- lend-line EvictionLine (kept-line line lend-room))
      (<- told-lend int (jobs-limit working lend-line margin margin cap (+ reserve lent waiting) 0))
      (:= lend (if (< told told-lend) (min evicted told-lend) None)))
    (:= limit evicted)
    (:= intake told)
    (when boxed?
      (<- boxed int (jobs-box-limit current cell.box (- margin floor-margin) cell.clis-limit cell.clis-current pending))
      (<- boxed-intake int (jobs-box-limit working cell.box 0 cell.clis-limit cell.clis-current 0))
      (:= limit (min limit boxed))
      (:= intake (min intake boxed-intake))
      (when (is-not urgent None)
        (:= urgent (min urgent boxed-intake)))
      (when (is-not lend None)
        (:= lend (min lend boxed-intake)))))
  (when (is-not line None)
    (<- room-failure str (room-written settings line margin reserve addable))
    (:= failure (or failure room-failure)))
  (when (and (is-not line None) (is current None))
    ;; 使いを読めない回は、線から決めた前の上限を残す(宣言の値へ戻すと、線の手前を越えうる)。
    (:= limit None))
  (when (is-not limit None)
    (<- written (| FileFailed None) (WriteText (+ settings.jobs-dir "/memory.max") (str limit)))
    (if (isinstance written FileFailed)
        (:= failure (.format "jobs の層の memory.max に {} を書けない: {}" limit written.detail))
        (setv cell.jobs-limit limit cell.jobs-intake (if (is intake None) limit (min limit intake)) cell.jobs-intake-urgent urgent
              cell.jobs-intake-lend lend)))
  (val squeezed (and (is-not line None) (is-not current None) (is-not limit None) (not failure) (< limit current)))
  (when (and squeezed (not cell.squeezed))
    (<- (slog JOBS-SQUEEZE-LOG :level "warning" :line line.kind.value :line-mib (// line.size-bytes MIB)
              :room-mib (// line.room-bytes MIB) :margin-mib (// margin MIB) :floor-margin-mib (// settings.margin-bytes MIB)
              :prepare-reserve-mib (// reserve MIB) :clis-lent-mib (// lent MIB) :pending-mib (// pending MIB)
              :jobs-current-mib (// current MIB) :jobs-limit-mib (// limit MIB))))
  (setv cell.squeezed squeezed)
  (when (!= failure cell.told-failure)
    (setv cell.told-failure failure)
    (when failure
      (<- (slog JOBS-LIMIT-FAILED-LOG :level "warning" :reason failure))))
  None)


(defk jobs-oom-count [cell]
  {:pre [(: cell EvictionCell)] :post [(: % (| int None))] :tags {:context "worker" :role "protocol"}}
  "jobs の層の memory.events の oom_kill(kernel が層の memory.max で止めた process の数)を読むため。層が無い・読めない時は None。"
  (when (is cell.jobs-dir None)
    (return None))
  (<- events (| str FileFailed) (ReadText (+ cell.jobs-dir "/memory.events")))
  (when (isinstance events FileFailed)
    (return None))
  (<- count int (oom-kills-of events))
  count)


(defk jobs-oom-noted [cell now-ms]
  {:pre [(: cell EvictionCell) (: now-ms int)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "見張りの回ごとに jobs の層の oom_kill を読み、前に読んだ数より増えていれば今の刻を oom-at-ms に足すため(EvictionCell の註 — kernel が
   jobs の層の memory.max で process を止めた事を、状態の報告がその task の終わりの訳に載せる)。書き手は見張りだけ(終わりを回収する拍の
   観測 worker/protocol/memory_observer は読み直して、まだ刻を付けていない増えを答えに添えるだけ)。OOM-MARK-MS を過ぎた刻は除く。読めない回は
   数を残す。"
  (setv cell.oom-at-ms (tuple (gfor at cell.oom-at-ms :if (<= 0 (- now-ms at) OOM-MARK-MS) at)))
  (<- count (| int None) (jobs-oom-count cell))
  (when (is count None)
    (return None))
  (when (and (is-not cell.oom-count None) (> count cell.oom-count))
    (setv cell.oom-at-ms (+ cell.oom-at-ms #(now-ms))))
  (setv cell.oom-count count)
  None)


(defk command-room-read [commands line-file]
  {:pre [(: commands CommandLayerSettings) (: line-file str)] :post [(: % (| int None str))] :tags {:context "worker" :role "protocol"}}
  "MemAvailable − 線(byte・負もありうる)を読むため。線の file が無い・数でない(係がまだ起きていない)は None、meminfo を読めない・形の
   違う時は訳の文。"
  (<- line-text (| str FileFailed) (ReadText line-file))
  (<- line-mib (| int None) (keep-line-of (if (isinstance line-text str) line-text None)))
  (when (is line-mib None)
    (return None))
  (<- meminfo (| str FileFailed) (ReadText commands.meminfo-path))
  (when (isinstance meminfo FileFailed)
    (return (.format "meminfo を読めない: {}" meminfo.detail)))
  (try
    (<- available int (meminfo-kib meminfo "MemAvailable"))
    (- (* available 1024) (* line-mib MIB))
    (except [error ValueError]
      (.format "meminfo を読めない: {}" error))))


(defk command-layer-read [commands]
  {:pre [(: commands CommandLayerSettings)] :post [(: % (| CommandLayerUse str))] :tags {:context "worker" :role "protocol"}}
  "命令の層の memory.current と memory.stat を内訳にするため。読めない・形の違う時は訳の文。"
  (<- current (| str FileFailed) (ReadText (+ commands.dir "/memory.current")))
  (when (isinstance current FileFailed)
    (return (.format "命令の層の memory.current を読めない: {}" current.detail)))
  (<- stat (| str FileFailed) (ReadText (+ commands.dir "/memory.stat")))
  (when (isinstance stat FileFailed)
    (return (.format "命令の層の memory.stat を読めない: {}" stat.detail)))
  (try
    (<- use CommandLayerUse (command-layer-use-of current stat))
    use
    (except [error ValueError]
      (str error))))


(defk commands-limited [commands cell guard]
  {:pre [(: commands CommandLayerSettings) (: cell EvictionCell) (: guard bool)] :post [(: % None)] :tags {:context "worker" :role "protocol"}}
  "命令の層の memory.max を MemAvailable と線から掛け直すため(頭の註・worker/core/eviction_rules の command-layer-limit)。線の宣言の無い
   機体と guard が偽の見張りは書かない。線か内訳を読めない回は宣言の静的な値を書く(前の回の小さい上限で命令の層を縛らない)。同じ回に
   container の中の限る層として掛けた上限(入れ物の box-limits — container の中の空きはその値で取り置いた)が在れば、静的な値の代わりに
   それを越えて書かない。回収で返らない量より下げた続きの頭と、失敗の訳が変わった時に 1 行言う。"
  (when (or (not cell.command-line-file) (not guard))
    (return None))
  (<- room (| int None str) (command-room-read commands cell.command-line-file))
  (<- use (| CommandLayerUse str) (command-layer-read commands))
  (setv cell.command-use (if (isinstance use str) None use))
  (var failure (cond (isinstance room str) room (isinstance use str) use True ""))
  (val boxed (next (gfor l cell.box-limits :if (= l.name commands.name) l.limit-bytes) None))
  (val cap (if (is boxed None) commands.cap-bytes (min boxed commands.cap-bytes)))
  (var limit cap)
  (when (and (isinstance room int) (isinstance use CommandLayerUse))
    (<- found int (command-layer-limit use room cell.command-floor-bytes cap))
    (:= limit found))
  (<- written (| FileFailed None) (WriteText (+ commands.dir "/memory.max") (str limit)))
  (if (isinstance written FileFailed)
      (:= failure (.format "命令の層の memory.max に {} を書けない: {}" limit written.detail))
      (setv cell.command-limit limit))
  (val squeezed (and (not failure) (isinstance use CommandLayerUse) (< limit use.held-bytes)))
  (when (and squeezed (not cell.command-squeezed))
    (<- (slog COMMANDS-SQUEEZE-LOG :level "warning" :limit-mib (// limit MIB) :held-mib (// use.held-bytes MIB)
              :pinned-mib (// use.pinned-bytes MIB) :room-mib (// room MIB) :floor-mib (// cell.command-floor-bytes MIB))))
  (setv cell.command-squeezed squeezed)
  (when (!= failure cell.told-command-failure)
    (setv cell.told-command-failure failure)
    (when failure
      (<- (slog COMMANDS-LIMIT-FAILED-LOG :level "warning" :reason failure))))
  None)


(defk retired-noted [settings cell]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "keeper がこの世代を退かせ始めたかを知るため(頭の註の退いた世代): 印の file(retired-file)が読めれば(中身は問わない — keeper は空の
   file を置く)入れ物に残して 1 行言う。一度真になれば読み直さない。印の file の無い世代(keeper の世代でない)と、読めない回は偽。"
  (when cell.retired
    (return True))
  (when (is settings.retired-file None)
    (return False))
  (<- mark (| str FileFailed) (ReadText settings.retired-file))
  (when (isinstance mark FileFailed)
    (return False))
  (setv cell.retired True)
  (<- (slog RETIRED-LOG :level "info" :retired-file settings.retired-file))
  True)


(defk elder-noted [settings cell]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "同じ container に層を書いている前の世代が居るかを知るため(頭の註の世代の書き手): keeper の dir の世代の dir を並べ、自分の外の印の
   無い世代から書き手を決める(worker/core/eviction_rules の writing-elder-of)。替わった時に 1 行言う。答え = 前の世代が居る(この世代は
   書かない)。印の file の無い世代(keeper の世代でない)・一度居ないと読んだ後・dir を並べられない回は偽(前の形のとおり書く)。"
  (when (or (is settings.retired-file None) cell.elder-gone)
    (return False))
  (val own-dir (posixpath.dirname settings.retired-file))
  (val gens (posixpath.dirname own-dir))
  (val own (posixpath.basename own-dir))
  (val mark (posixpath.basename settings.retired-file))
  (<- entries (ListDirectory gens))
  (when (isinstance entries FileFailed)
    (return False))
  (var unmarked #())
  (for [entry entries]
    (when (and (= entry.kind PathKind.DIRECTORY) (!= entry.name own))
      (<- marked (| str FileFailed) (ReadText (posixpath.join gens entry.name mark)))
      (when (isinstance marked FileFailed)
        (:= unmarked (+ unmarked #(entry.name))))))
  (<- elder (| str None) (writing-elder-of own unmarked))
  (when (!= elder cell.elder)
    (<- (slog ELDER-WRITES-LOG :level "info" :elder (or elder ""))))
  (setv cell.elder elder cell.elder-gone (is elder None))
  (is-not elder None))


(defk eviction-noted [settings cell guard now-ms]
  {:pre [(: settings (| EvictionSettings None)) (: cell EvictionCell) (: guard bool) (: now-ms int)] :post [(: % (| EvictionLine None))]
   :tags {:context "worker" :role "protocol"}}
  "見張りの回の頭の 1 回(頭の註): Node の宣言を要る時だけ読み直し、線を数えて入れ物に残し、jobs の層の上限を掛け直すため。答え = 今の
   線(None = 線が無いか読めない)。設定の無い worker(kubelet の下に居ない・host の cgroupfs が無い)は何もせず None。keeper が退かせ
   始めた世代と、層を書いている前の世代が居る世代は、guard が偽の見張りと同じく層の上限と keeper の file を書かない(頭の註の退いた
   世代・世代の書き手)。"
  (when (is settings None)
    (return None))
  (<- retired bool (retired-noted settings cell))
  (<- elder bool (elder-noted settings cell))
  (val writes (and guard (not retired) (not elder)))
  (<- (kubelet-noted settings cell now-ms))
  (var line None)
  (var reason "")
  (if (is cell.kubelet None)
      (:= reason (+ "Node の宣言の memory を読めない: " (or cell.node-reason "まだ読んでいない")))
      (do (<- read (| EvictionLine None str) (cgroup-line-read settings cell.kubelet))
          (if (isinstance read str) (:= reason read) (:= line read))))
  (when (and (not reason) cell.node-reason)
    (:= reason (+ "Node の宣言を読み直せない(前に読めた値で数える): " cell.node-reason)))
  (setv cell.line line cell.line-at-ms now-ms)
  (<- (line-told settings cell line reason))
  (<- (jobs-limited settings cell line writes now-ms))
  (when (is-not settings.commands None)
    (<- (commands-limited settings.commands cell writes)))
  line)
