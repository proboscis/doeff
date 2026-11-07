;;; 追い出しの線の読みと jobs の層の上限の掛け直し(判断は worker/core/eviction_rules)。機体の memory の見張り
;;; (worker/protocol/memory_watch)が回のたびに頭で呼ぶ:
;;;   eviction-noted  1 回: Node の宣言の memory(capacity・allocatable)を要る時だけ API server から読み直し、host の cgroupfs から kubepods と
;;;                   root の cgroup を読んで追い出しまでの空きを数え、jobs の層の memory.max を「jobs の今の使い + 空き − 余裕」(上限は
;;;                   宣言の静的な値)に掛け直す。答え = 今の線(None = 線が無い・読めない — 見張りは機体の MemAvailable で判じる)。
;;;                   準備の層(prepare — keeper の版の準備)が在れば、その使いと process の有無を読み、走っている準備の山までの残りを
;;;                   jobs の上限から引き(取り置き)、「空き − 余裕 − 取り置き」と準備の層の上限を keeper の読む file(room-file)に
;;;                   書く。余裕は機体の宣言の床と、回ごとに測る jobs と準備の層の外の working set の増えから導く(worker/core/
;;;                   eviction_rules の margin-of)。
;;;                   会話の CLI の層(clis)に天井の宣言が在る機体は、jobs の層より先に clis の層の memory.max を床(宣言の静的な値)と天井の
;;;                   間で掛け直し(worker/core/eviction_rules の clis-limit — clis の今の使いより下げず、広げた上限は jobs の層を縮めて
;;;                   残す)、床を越えて貸した量を jobs の上限から引き、jobs の上限を container の中の空きの内にも収める。
;;; 線を読めない間(Node の宣言をまだ読めない・cgroup の file を読めない・Node と cgroup が食い違う)は、jobs の層を宣言の静的な値に戻す
;;; (古い空きで決めた上限を残さない)。clis の層は床と clis の今の使いの大きい方に戻す(使いを読めない回は書かない)。guard が偽の見張り(起動の引数 --memory-guard off)は数えて log に出すだけで書かない。
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
;;; room-file の中身(keeper が sh で読む 1 行): 「<準備に使える空き MiB> <準備の層の上限 MiB>」(空きは負もありうる)。線を読めない回は
;;; 書かない(keeper は古い file を読まない — deploy/boot.sh の keeper_swap)。
;;; Node は自区画の API server から ServiceAccount の token で読む(読みの答え手は入口が並べる in-cluster-api-routed と HTTP の答え手)。
;;; cgroup の file の読みと書きは file の効果(本物の答え手は入口が並べる offloaded-file-handler — jobs の層の上限を使いより下げる書きは
;;; kernel が回収を終えるまで返らないので、scheduler の thread で待たない)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord・defwire の展開が名指す
(import urllib.parse [quote :as url-quote])
(import doeff_hy._wire [Malformed parse-json])
(import doeff_core_effects [slog])
(import doeff_core_effects [FileFailed ReadText WriteText])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [EVICTION-MARGIN-BYTES MARGIN-WINDOW EvictionLine KubeletMemory PodsMemory quantity-bytes
                                                  pods-memory-of root-working-set-of eviction-line jobs-limit stat-bytes working-set
                                                  prepare-reserve prepare-room margin-of kernel-bytes-of clis-limit clis-lent box-free
                                                  jobs-box-limit])

(val EVICTION-LINE-LOG "worker: 追い出しの線を読めた")
(val EVICTION-UNREAD-LOG "worker: 追い出しの線を読めない")
(val JOBS-SQUEEZE-LOG "worker: 追い出しまでの空きが余裕を割ったので jobs の層の上限を jobs の使いより下げた")
(val JOBS-LIMIT-FAILED-LOG "worker: jobs の層の memory の上限を掛け直せない")
(val MARGIN-LOG "worker: 追い出しの線の手前に残す余裕を、測った jobs と準備の層の外の伸びから替えた")
(val CLIS-LENT-LOG "worker: 会話の CLI の層の memory の上限を、空きから床の上へ広げた・床へ戻した")
;; API server の断りの本文を訳に載せる長さと、要求 1 本の時間切れ(秒)。
(val REFUSAL-BODY-LIMIT 300)
(val REQUEST-TIMEOUT-SECONDS 5.0)


(defrecord CliLayerSettings
  "会話の CLI の層(clis)の掛け直しの設定(入口が起動の時に 1 度だけ作る — worker/core/eviction_rules の頭の註の会話の CLI の層): dir = clis の
   層の dir・floor-bytes = 宣言の静的な memory.max(床)・ceiling-bytes = 機体の宣言の天井・container-dir = worker の container の cgroup の
   dir(memory.max と working set を読む)・held-bytes = jobs と clis の外の限る層の宣言の memory.max の和(container の中の空きから引く)。"
  {:check [(>= floor-bytes 0) (>= ceiling-bytes floor-bytes) (>= held-bytes 0)]}
  (#^ str dir)
  (#^ int floor-bytes)
  (#^ int ceiling-bytes)
  (#^ str container-dir)
  (setv #^ int held-bytes 0))


(defrecord EvictionSettings
  "追い出しの線の読みの設定(入口が起動の時に 1 度だけ作る — 頭の註): api = 自区画の API server の URL・token-file = ServiceAccount の
   token の file・node = この worker の Pod が置かれた Node の名・pods-dir = kubepods の cgroup の dir(host の cgroupfs の mount の中 —
   worker/core/eviction_rules の pods-dir-of)・host-root = host の cgroupfs の mount の場所(root の memory.stat を読む)・jobs-dir = jobs の
   層の dir(None = 層が無い — 線を数えて見張りに渡すだけ)・jobs-cap-bytes = jobs の層の memory.max の宣言の静的な値(None = 上限なし)・
   node-read-seconds = Node の宣言を読み直す間(kubelet の設定は kubelet の起動し直しでしか替わらない)・node-retry-seconds = 読めなかった
   後に試し直す間・margin-bytes = jobs の層の上限に残す余裕の床(機体の宣言 WORKER_EVICTION_MARGIN — 無ければ worker/core/
   eviction_rules の EVICTION-MARGIN-BYTES)・prepare-dir = 準備の層の dir(None = 層が無い — keeper は準備を jobs の層で起こす)・
   prepare-cap-bytes = 準備の層の memory.max の宣言の静的な値(prepare-dir が在る時は必ず在る)・room-file = keeper の読む準備の空きの
   file(None = keeper の無い worker か準備の層が無い — 書かない)・clis = 会話の CLI の層の掛け直しの設定(None = 掛け直さない — 天井の
   宣言が無い機体)。"
  {:check [(> node-read-seconds 0) (> node-retry-seconds 0) (>= margin-bytes 0) (or (is jobs-cap-bytes None) (>= jobs-cap-bytes 0))
           (= (is prepare-dir None) (is prepare-cap-bytes None)) (or (is prepare-cap-bytes None) (>= prepare-cap-bytes 0))
           (or (is clis None) (is-not jobs-dir None))]}
  (#^ str api)
  (#^ str token-file)
  (#^ str node)
  (#^ str pods-dir)
  (#^ str host-root)
  (#^ (| str None) jobs-dir)
  (#^ (| int None) jobs-cap-bytes)
  (setv #^ (| str None) prepare-dir None)
  (setv #^ (| int None) prepare-cap-bytes None)
  (setv #^ (| str None) room-file None)
  (setv #^ float node-read-seconds 300.0)
  (setv #^ float node-retry-seconds 30.0)
  (setv #^ int margin-bytes EVICTION-MARGIN-BYTES)
  (setv #^ (| CliLayerSettings None) clis None))


(defclass EvictionCell []
  "追い出しの線の読みが回から回へ持ち越す値(見張りの入れ物 MemoryCell が持ち、heartbeat の測りも読む)。kubelet = 最後に読めた Node の
   宣言の memory(None = まだ)・node-read-ms = 最後に Node を読みに行った刻・node-reason = 最後の Node の読みの訳(\"\" = 読めた)・
   line = 最後の回の線(None = 線が無いか読めない)・line-at-ms = その回の刻・told-kubelet = log に出した Node の宣言の値・
   told-unread = log に出した読めない訳・jobs-limit = 最後に書いた jobs の層の上限(None = まだ書いていない)・squeezed = 今の回に
   上限を jobs の使いより下げた続きの中・told-failure = log に出した jobs の層の失敗の訳・outside = 前の回の jobs と準備の層の外の
   working set(None = 前の回に数えられなかった — 増えを数えない)・growths = 回あたりの外の増え(負は 0・新しい MARGIN-WINDOW 回)・
   margin = 最後の回の余裕(None = まだ)・told-margin = log に出した余裕・reserve = 最後の回の準備の取り置き・uses = 見張りの最後の読みの
   job ごとの memory(JobUse の tuple — heartbeat の測りが task ごとの今の使いとして名乗る・2026-11-02)・uses-at-ms = その読みの刻(None =
   まだ)・jobs-current = 最後の回に読んだ jobs の層の memory.current(None = 読めなかった・層が無い — jobs-limit との差が調整ループの
   観測する jobs の層の空き WorldView.jobs-room)・clis-limit = 最後に書いた clis の層の上限(None = まだ書いていない・掛け直さない)・
   clis-current = 最後の回に読んだ clis の層の memory.current(None = 読めなかった)・lent = 最後の回に clis に床を越えて貸した量
   (jobs の上限から引いた量)・box = 最後の回の container の中の空き(None = 数えなかった)。scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.kubelet None self.node-read-ms None self.node-reason "" self.line None self.line-at-ms None self.told-kubelet None
          self.told-unread "" self.jobs-limit None self.squeezed False self.told-failure "" self.outside None
          self.growths #() self.margin None self.told-margin None self.reserve 0 self.uses #() self.uses-at-ms None
          self.jobs-current None self.clis-limit None self.clis-current None self.lent 0
          self.box None)))


(defrecord LayerMemory
  "cgroup の層 1 つの読み: current-bytes = memory.current・working-set-bytes = memory.current − memory.stat の inactive_file(cAdvisor の
   数え方)・populated = 層か子に process が居る(cgroup.events の populated)。"
  {:check [(>= current-bytes 0) (>= working-set-bytes 0)]}
  (#^ int current-bytes)
  (#^ int working-set-bytes)
  (#^ bool populated))


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
  (try
    (<- found int (stat-bytes stat-text "inactive_file"))
    (:= inactive found)
    (except [error ValueError]
      (return (.format "{} の層の memory.stat を読めない: {}" name error))))
  (<- set-bytes int (working-set current inactive))
  (var alive True)
  (when populated
    (<- events (| str FileFailed) (ReadText (+ dir "/cgroup.events")))
    (when (isinstance events FileFailed)
      (return (.format "{} の層の cgroup.events を読めない: {}" name events.detail)))
    (:= alive (in "populated 1" (.splitlines events))))
  (LayerMemory :current-bytes current :working-set-bytes set-bytes :populated alive))


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


(defk room-written [settings line margin reserve]
  {:pre [(: settings EvictionSettings) (: line EvictionLine) (: margin int) (: reserve int)] :post [(: % str)]
   :tags {:context "worker" :role "protocol"}}
  "keeper の読む file(room-file)へ、準備に使える空きと準備の層の上限を 1 行で書くため(頭の註の room-file の中身)。file か準備の層が
   無ければ書かない。答え = 書けない訳(\"\" = 書いた・書かない)。"
  (when (or (is settings.room-file None) (is settings.prepare-cap-bytes None))
    (return ""))
  (<- room int (prepare-room line margin reserve))
  (<- written (| FileFailed None) (WriteText settings.room-file (.format "{} {}\n" (// room MIB) (// settings.prepare-cap-bytes MIB))))
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


(defk clis-limited [clis cell line margin reserve jobs-current]
  {:pre [(: clis CliLayerSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: margin int) (: reserve int)
         (: jobs-current (| int None))]
   :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "会話の CLI の層の memory.max を掛け直し、床を越えて貸した量と container の中の空きを入れ物に残すため(worker/core/eviction_rules の
   clis-limit): 線と jobs の使いと container を読めた回は空きから決めた上限(床と天井の間・使いより下げない)、線が無いか読めない回は
   床と clis の今の使いの大きい方。clis の使いを読めない・書けない回は前の上限を残し、その上限 − 床を貸しのまま引く。上限が床を越えた
   時と床へ戻った時に 1 行言う。答え = 書けない・読めない訳(\"\" = 書いた)。"
  (setv cell.box None)
  (<- read (| LayerMemory str) (layer-read "clis" clis.dir False))
  (when (isinstance read str)
    (setv cell.clis-current None
          cell.lent (if (is cell.clis-limit None) 0 (max 0 (- cell.clis-limit clis.floor-bytes))))
    (return read))
  (val current read.current-bytes)
  (setv cell.clis-current current)
  (var failure "")
  (var free None)
  (var box None)
  (when (and (is-not line None) (is-not jobs-current None))
    (<- container (| LayerMemory str) (layer-read "container" clis.container-dir False))
    (<- container-max (| int None str) (container-max-read clis.container-dir))
    (cond
      (isinstance container str) (:= failure container)
      (isinstance container-max str) (:= failure container-max)
      True (do (:= free (- line.room-bytes margin reserve))
               (<- found (| int None) (box-free container-max container.working-set-bytes margin clis.held-bytes))
               (:= box found))))
  (var limit (max current clis.floor-bytes))
  (when (is-not free None)
    (<- found int (clis-limit current free box clis.floor-bytes clis.ceiling-bytes cell.clis-limit jobs-current))
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


(defk jobs-limited [settings cell line guard]
  {:pre [(: settings EvictionSettings) (: cell EvictionCell) (: line (| EvictionLine None)) (: guard bool)] :post [(: % None)]
   :tags {:context "worker" :role "protocol"}}
  "jobs の層の memory.max を今の線から掛け直すため(頭の註): 線が在れば jobs の今の使い + 空き − 余裕 − 準備の取り置き(上限は宣言の
   静的な値)、線が無ければ宣言の静的な値。線が在る回は keeper の読む準備の空きも書く。jobs の層が無い・guard が偽・上限なしで線も
   無い時は書かない。上限を jobs の使いより下げた続きの頭で 1 行言う。"
  (when (or (is settings.jobs-dir None) (not guard))
    (return None))
  (when (and (is line None) (is settings.jobs-cap-bytes None))
    (return None))
  (var failure "")
  (var current None)
  (var jobs None)
  (<- jobs-read (| LayerMemory str) (layer-read "jobs" settings.jobs-dir False))
  (if (isinstance jobs-read str)
      (:= failure jobs-read)
      (do (:= jobs jobs-read) (:= current jobs-read.current-bytes)))
  (setv cell.jobs-current current)
  (var prepare None)
  (when (is-not settings.prepare-dir None)
    (<- prepare-read (| LayerMemory str) (layer-read "準備" settings.prepare-dir True))
    (if (isinstance prepare-read str)
        (:= failure (or failure prepare-read))
        (:= prepare prepare-read)))
  (<- margin int (margin-noted settings cell line jobs prepare))
  ;; 準備の層を読めない回は、準備が走っているとみなして上限まで取り置く(準備の山を線の計算から落とさない)。
  (var reserve 0)
  (when (is-not settings.prepare-cap-bytes None)
    (if (is prepare None)
        (:= reserve settings.prepare-cap-bytes)
        (do (<- held int (prepare-reserve settings.prepare-cap-bytes prepare.current-bytes prepare.populated))
            (:= reserve held))))
  (setv cell.reserve reserve)
  ;; 会話の CLI の層を先に掛け直し、床を越えて貸した量を jobs の上限から引く(同じ空きを 2 つの層に重ねて貸さない)。
  (var lent 0)
  (when (is-not settings.clis None)
    (<- clis-failure str (clis-limited settings.clis cell line margin reserve current))
    (:= failure (or failure clis-failure))
    (:= lent cell.lent))
  (var limit settings.jobs-cap-bytes)
  (when (and (is-not line None) (is-not current None))
    (<- found int (jobs-limit current line margin settings.jobs-cap-bytes (+ reserve lent)))
    (:= limit found)
    (when (and (is-not settings.clis None) (is-not cell.box None) (is-not cell.clis-limit None) (is-not cell.clis-current None))
      (<- boxed int (jobs-box-limit current cell.box cell.clis-limit cell.clis-current))
      (:= limit (min limit boxed))))
  (when (is-not line None)
    (<- room-failure str (room-written settings line margin reserve))
    (:= failure (or failure room-failure)))
  (when (and (is-not line None) (is current None))
    ;; 使いを読めない回は、線から決めた前の上限を残す(宣言の値へ戻すと、線の手前を越えうる)。
    (:= limit None))
  (when (is-not limit None)
    (<- written (| FileFailed None) (WriteText (+ settings.jobs-dir "/memory.max") (str limit)))
    (if (isinstance written FileFailed)
        (:= failure (.format "jobs の層の memory.max に {} を書けない: {}" limit written.detail))
        (setv cell.jobs-limit limit)))
  (val squeezed (and (is-not line None) (is-not current None) (is-not limit None) (not failure) (< limit current)))
  (when (and squeezed (not cell.squeezed))
    (<- (slog JOBS-SQUEEZE-LOG :level "warning" :line line.kind.value :line-mib (// line.size-bytes MIB)
              :room-mib (// line.room-bytes MIB) :margin-mib (// margin MIB) :prepare-reserve-mib (// reserve MIB)
              :jobs-current-mib (// current MIB) :jobs-limit-mib (// limit MIB))))
  (setv cell.squeezed squeezed)
  (when (!= failure cell.told-failure)
    (setv cell.told-failure failure)
    (when failure
      (<- (slog JOBS-LIMIT-FAILED-LOG :level "warning" :reason failure))))
  None)


(defk eviction-noted [settings cell guard now-ms]
  {:pre [(: settings (| EvictionSettings None)) (: cell EvictionCell) (: guard bool) (: now-ms int)] :post [(: % (| EvictionLine None))]
   :tags {:context "worker" :role "protocol"}}
  "見張りの回の頭の 1 回(頭の註): Node の宣言を要る時だけ読み直し、線を数えて入れ物に残し、jobs の層の上限を掛け直すため。答え = 今の
   線(None = 線が無いか読めない)。設定の無い worker(kubelet の下に居ない・host の cgroupfs が無い)は何もせず None。"
  (when (is settings None)
    (return None))
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
  (<- (jobs-limited settings cell line guard))
  line)
