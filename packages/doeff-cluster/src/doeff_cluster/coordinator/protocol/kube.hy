;;; coordinator の kube_model の effect に答える handler。kube-api = Pod の中から k8s の API へ(foundation/kube_client の
;;; KubeClient の 4 つの口 — 層 protocol は foundation を読めないので、client は下の KubeCalls の形で受け、組み立ては entry が持つ)・
;;; kube-memory = テストの dict・kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。
;;; 見張りが伝えた物(Deployment の観測・Node の label)を k8s の JSON から型の値へ解くのはこの module の 1 点(deployment-reading・
;;; node-labels-table — #2728)。本物と検の答え手が同じ解きを通り、core は型の値だけを受ける。
;;;
;;; Deployment も Node も時間で読みに行かず見張る(Deployment — #3868・Node — #4070): FollowDeployments・FollowNodes で見張る相手を
;;; 揃え、本番の kube-api は相手ごとに list の後の watch を調停ループの外の daemon の thread で受ける(KubeClient.follow・follow-node)。
;;; 見張りの thread は変化を受け渡しの箱(kube-api と kube-unavailable の session の queue — 種類ごとに 1 つ)に置いて受付の箱を起こし、
;;; 調停ループは待たずに取る。今見張っている相手(Watching の列)も同じ session に持つ。
;;; 模擬の kube-memory は、見張り(MemoryFollows — coordinator の process ごと)が見張っている相手の今が最後に伝えた物と違えば伝え、
;;; 伝えていない変化が在る間は受付を待たずに返す(本番の見張りが受付を起こすのと同じ刻)。
(require doeff-hy.macros [val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler defk deff <-])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Mapping])
(import dataclasses [dataclass field])  ; defrecord の展開が名指す
(import queue)
(import typing [Protocol])
(import doeff [Pure])
(import doeff_hy.wire [parse Malformed])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.frozen [freeze-json-text thaw-json])
(import doeff_hy._table [Table TableWrite table-of])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentReading DeploymentSeen DeploymentUnreadable NodeLabelsSeen NodeLabelsUnreadable])
(import doeff_cluster.coordinator.intent.kube_model [ScaleDeployment AnnotateDeployment KubeUnavailable FollowDeployments FollowNodes])


(defk deployment-view [body]
  {:pre [(: body (get Mapping #(str object)))] :post [(: % (get dict #(str object)))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Deployment の object(API の JSON の本文)から、Rollout が見る欄だけの観測の JSON を読むため(deployment-reading が型の値へ解く)。
   見張りの thread は本文を運ぶだけで、読みはこの 1 点(#2764 — 以前は foundation/kube_client の method が素で呼んだ)。"
  (val spec (.get body "spec" {}))
  (val status (.get body "status" {}))
  (val meta (.get body "metadata" {}))
  {"specReplicas" (.get spec "replicas" 1)
   "replicas" (.get status "replicas" 0)
   "readyReplicas" (.get status "readyReplicas" 0)
   "availableReplicas" (.get status "availableReplicas" 0)
   "updatedReplicas" (.get status "updatedReplicas" 0)
   "generation" (.get meta "generation" 0)
   "observedGeneration" (.get status "observedGeneration" 0)
   "annotations" (or (.get meta "annotations") {})})


(defk deployment-reading [view]
  {:pre [(: view (get Mapping #(str object)))] :post [(: % DeploymentReading)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Deployment の観測の JSON(deployment-view の形)を Deployment の観測の型 DeploymentReading へ解くため。形が違えば
   KubeUnavailable — Rollout はこの相手を Unknown と扱い、形の読めない答えで台数を変えない。"
  (<- parsed (parse DeploymentReading view))
  (match parsed
    (Malformed :fields fields)
      (raise (KubeUnavailable (+ "k8s の Deployment の答えの形が違う: "
                                 (.join "・" (gfor f fields (.format "{}: {}" (or f.field "本文") f.reason))))))
    _ parsed))


(defk node-labels-view [body]
  {:pre [(: body (get Mapping #(str object)))] :post [(: % (| (get Mapping #(str object)) None))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Node の object(API の JSON の本文)から metadata.labels を読むため(node-labels-table が表へ写す)。label を持たない Node は空の
   写像・metadata か labels が object でなければ None(読めなかった観測)。"
  (val meta (.get body "metadata" {}))
  (val labels (if (isinstance meta dict) (.get meta "labels" {}) None))
  (cond
    (is labels None) {}
    (isinstance labels dict) labels
    True None))


(defk node-labels-table [labels]
  {:pre [(: labels (get Mapping #(str object)))] :post [(: % (get Table str))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Node の metadata.labels(キー → 値の JSON)を node の label の観測の表へ写すため(core は写像を受けない)。k8s の label の値は
   文字列で、文字列でない値は表に書かない。"
  (table-of (tuple (gfor #(key value) (.items labels) :if (isinstance value str) (TableWrite key value)))))


(defclass KubeCalls [Protocol]
  "kube-api が呼ぶ k8s の client の形(foundation/kube_client の KubeClient の 4 つの欄がこの形を持つ — 欄は関数)。届かない時は KubeUnavailable を投げる。"
  ;; follow = Deployment 1 つを list の後の watch で見張る daemon の thread を始める(#3868)。on-body = Deployment の object・on-error = 届かない・
  ;; 断られた・消された理由(どちらも見張りの thread から呼ぶ)。答え = 見張りを止める関数。
  (#^ (get Callable #([str str (get Callable #([OpaqueJson] None)) (get Callable #([str] None))] (get Callable #([] None)))) follow)
  ;; follow-node = Node 1 つを同じ形で見張る(#4070)。on-body = Node の object(中を読まずに運ぶ — label の読みは node-labels-view の 1 点)。
  (#^ (get Callable #([str (get Callable #([OpaqueJson] None)) (get Callable #([str] None))] (get Callable #([] None)))) follow-node)
  (#^ (get Callable #([str str int bool] int)) scale)
  (#^ (get Callable #([str str dict] None)) annotate))


;; --- 見張り(時間で読みに行かない — 頭の註・#3868・#4070)---------------------------------------------------------
;; 見張りの状態は kube-api と kube-unavailable の session に置く(種類 — Deployment・Node — ごとに 1 組): 見張りの thread が置く受け渡しの箱
;; (queue.SimpleQueue — thread の間の受け渡し)は session val・今見張っている相手(Watching の列)は session var。

(defrecord Watching
  "見張っている相手 1 つ: key = 相手のキー(Deployment は「ns/名」・Node は名)・stop = その見張りを止める関数。"
  (#^ str key)
  (#^ Callable stop))


(deff watch-mark [seen]  ; defk にできない: 見張りの thread が呼ぶ callback(watch-reporter が作る — Program の外)と defk の両方が同じ印を作る
  {:pre [(: seen (| OpaqueJson str))] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張りが伝える物の比べの印を作るため: object は JSON の文・読めない理由はその文。"
  (if (isinstance seen str) #("error" seen) #("body" seen.text)))


(deff watch-reporter [box key wake]  ; defk にできない: k8s の client の見張りの thread(Program の外)が、変化を受けるたびに呼ぶ callback を作る
  {:pre [(: box queue.SimpleQueue) (: key str) (: wake Callable)] :post [(: % Callable)] :tags {:context "coordinator" :role "protocol"}}
  "key の見張りの thread が呼ぶ callback を作るため: 受けた物(object か、読めない理由)が前に受け渡した物と違えば箱に受け渡して wake を呼ぶ
   (同じ物は続けて受け渡さない — 断られた見張りが試し直すたびに調停ループを起こさない)。最後に受け渡した印は callback ごとに持つ — 見張りを
   外して始め直せば、新しい callback が初めから受け渡す。"
  (setv last [None])
  (fn [seen]
    (setv mark (watch-mark seen))
    (when (!= (get last 0) mark)
      (setv (get last 0) mark)
      (.put box #(key seen))
      (wake))))


(defk watch-follow [watching keys start]
  {:pre [(: watching tuple) (: keys tuple) (: start Callable)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張る相手を keys に揃えるため: keys に無い見張りを止め、新しいキーの見張りを start(キー → 止める関数)で始める。答え = 揃えた後の
   Watching の列。"
  (for [w (tuple (gfor w watching :if (not-in w.key keys) w))]
    (w.stop))
  (val kept (tuple (gfor w watching :if (in w.key keys) w)))
  (val kept-keys (tuple (gfor w kept w.key)))
  (val new-keys (tuple (gfor #(i key) (enumerate keys) :if (and (not-in key kept-keys) (= (.index keys key) i)) key)))
  (+ kept (tuple (gfor key new-keys (Watching :key key :stop (start key))))))


(defk watch-take [box watching]
  {:pre [(: box queue.SimpleQueue) (: watching tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "受け渡された物を待たずに全部取り、見張っているキーごとに最後の 1 つを #(キー 本文か理由) の tuple(キーは初めて受け渡された順)で
   返すため。"
  ;; 取るのはこの 1 つの手だけ(箱に足す見張りの thread は取らない)ので、数えた分は必ず取れる — 数えた後に足された物は次の取りへ残る。
  (val items (tuple (gfor _ (range (.qsize box)) (.get-nowait box))))
  (val watched (tuple (gfor w watching w.key)))
  (val order (tuple (gfor #(key _) items key)))
  (val keys (tuple (gfor #(i key) (enumerate order) :if (and (in key watched) (= (.index order key) i)) key)))
  (tuple (gfor key keys #(key (next (gfor #(k seen) (reversed items) :if (= k key) seen))))))


;; --- 見張りが伝えた物を観測の表への書きへ解く(3 つの答え手が同じ 1 点を通る)----------------------------------------

(defrecord KubeBodyRead
  "見張りが伝えた 1 件: name = キー(「ns/名」か node の名)・body = k8s の API の JSON の本文(Deployment か Node の object)を中を読まずに
   運ぶ値(見張りの thread は解かない — 解くのは受け取りの defk deployment-observation-of・node-labels-observation-of)。"
  (#^ str name)
  (#^ OpaqueJson body))


(defrecord KubeReadFailed
  "見張りが伝えた 1 件: 届かない・断られた・消された・答えの中で上がった例外の理由。"
  (#^ str name)
  (#^ str error))


(defk read-of [key seen]
  {:pre [(: key str) (: seen (| OpaqueJson str))] :post [(: % (| KubeBodyRead KubeReadFailed))] :tags {:context "coordinator" :role "protocol"}}
  "見張りが伝えた #(キー 本文か理由) の 1 件を、解く前の型の値にするため(理由の文は読めなかった 1 件)。"
  (if (isinstance seen str) (KubeReadFailed :name key :error seen) (KubeBodyRead :name key :body seen)))


(defk deployment-observation-of [result at]
  {:pre [(: result (| KubeBodyRead KubeReadFailed)) (: at int)] :post [(: % (| DeploymentSeen DeploymentUnreadable))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "見張りが伝えた Deployment 1 件を、観測の表に置く観測にするため(本文は deployment-view と deployment-reading で型へ解く・形が違えば
   読めなかった観測)。"
  (if (isinstance result KubeReadFailed)
      (DeploymentUnreadable :error result.error :at at)
      (do (val opened (thaw-json (freeze-json-text result.body.text)))
          (if (isinstance opened dict)
              (try
                (<- view (get dict #(str object)) (deployment-view opened))
                (<- reading DeploymentReading (deployment-reading view))
                (DeploymentSeen :reading reading :at at)
                (except [error KubeUnavailable]
                  (DeploymentUnreadable :error (str error) :at at)))
              (DeploymentUnreadable :error "k8s の Deployment の答えが object でない" :at at)))))


(defk deployment-writes [changes at]
  {:pre [(: changes tuple) (: at int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張りが伝えた #(「ns/名」 本文か理由) の tuple を、観測の表への書き(時刻は受け取った時刻 at)にするため。"
  (var writes #())
  (for [#(key seen) changes]
    (<- result (| KubeBodyRead KubeReadFailed) (read-of key seen))
    (<- observed (| DeploymentSeen DeploymentUnreadable) (deployment-observation-of result at))
    (:= writes (+ writes #((TableWrite key observed)))))
  writes)


(defk node-labels-observation-of [result at]
  {:pre [(: result (| KubeBodyRead KubeReadFailed)) (: at int)] :post [(: % (| NodeLabelsSeen NodeLabelsUnreadable))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "見張りが伝えた Node 1 件を、観測の表に置く観測にするため(本文は node-labels-view と node-labels-table で表へ写す・形が違えば
   読めなかった観測)。"
  (if (isinstance result KubeReadFailed)
      (NodeLabelsUnreadable :error result.error :at at)
      (do (val opened (thaw-json (freeze-json-text result.body.text)))
          (<- labels (| (get Mapping #(str object)) None) (if (isinstance opened dict) (node-labels-view opened) (Pure None)))
          (if (is labels None)
              (NodeLabelsUnreadable :error "k8s の Node の答えが object でないか、label が object でない" :at at)
              (do (<- table (get Table str) (node-labels-table labels))
                  (NodeLabelsSeen :labels table :at at))))))


(defk node-writes [changes at]
  {:pre [(: changes tuple) (: at int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張りが伝えた #(node の名 本文か理由) の tuple を、観測の表への書き(時刻は受け取った時刻 at)にするため。"
  (var writes #())
  (for [#(key seen) changes]
    (<- result (| KubeBodyRead KubeReadFailed) (read-of key seen))
    (<- observed (| NodeLabelsSeen NodeLabelsUnreadable) (node-labels-observation-of result at))
    (:= writes (+ writes #((TableWrite key observed)))))
  writes)


(defhandler kube-api [#^ KubeCalls client #^ (get Callable #([] None)) wake]
  ;; 引数に残す理由: k8s の client(HTTP の接続と token)と、受付の箱を起こす関数は composition root が 1 つ作って渡す
  (session val deployment-box (queue.SimpleQueue))
  (session var deployment-watching #())
  (session val node-box (queue.SimpleQueue))
  (session var node-watching #())
  (FollowDeployments [keys now-ms]
    ;; 見張りは調停ループの外の thread(k8s の client が持つ)。変化を受け渡したら受付の箱を起こす(頭の註)。
    (<- aligned tuple (watch-follow deployment-watching keys
                                    (fn [key]
                                      (let [report (watch-reporter deployment-box key wake)]
                                        (.follow client #* (.split key "/" 1) report report)))))
    (:= deployment-watching aligned)
    (<- taken tuple (watch-take deployment-box deployment-watching))
    (<- writes tuple (deployment-writes taken now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    ;; Deployment と同じ形(頭の註)。
    (<- aligned tuple (watch-follow node-watching names
                                    (fn [name]
                                      (let [report (watch-reporter node-box name wake)]
                                        (.follow-node client name report report)))))
    (:= node-watching aligned)
    (<- taken tuple (watch-take node-box node-watching))
    (<- writes tuple (node-writes taken now-ms))
    (resume writes))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason]
  ;; 引数に残す理由: 資格が無い理由の文は composition root が起動の時に 1 度だけ決める
  (session val deployment-box (queue.SimpleQueue))
  (session var deployment-watching #())
  (session val node-box (queue.SimpleQueue))
  (session var node-watching #())
  (FollowDeployments [keys now-ms]
    ;; 見張りは始めた時にその理由を 1 度だけ伝える(観測は読めなかった観測になり、Rollout はその理由を名指して Unknown と扱う)。
    (<- aligned tuple (watch-follow deployment-watching keys
                                    (fn [key] ((watch-reporter deployment-box key (fn [] None)) reason) (fn [] None))))
    (:= deployment-watching aligned)
    (<- taken tuple (watch-take deployment-box deployment-watching))
    (<- writes tuple (deployment-writes taken now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    ;; Deployment と同じ(node の worker は前に導いた能力を保つ)。
    (<- aligned tuple (watch-follow node-watching names
                                    (fn [name] ((watch-reporter node-box name (fn [] None)) reason) (fn [] None))))
    (:= node-watching aligned)
    (<- taken tuple (watch-take node-box node-watching))
    (<- writes tuple (node-writes taken now-ms))
    (resume writes))
  (ScaleDeployment [namespace name replicas dry-run] (raise (KubeUnavailable reason)))
  (AnnotateDeployment [namespace name annotations] (raise (KubeUnavailable reason))))


(defclass KubeMemory []
  "テストの k8s。deployments = 「ns/名」→ 観測の dict(specReplicas・readyReplicas・annotations …)。
   scale は宣言の台数だけを変える(Pod が立つ・消えるのはテストが memory-settle で進める)。calls = 受けた書きの記録。
   down = 真の間は全部 KubeUnavailable(API の途絶)。nodes = node の名 → label の dict(能力の導出の検 — label の変化は memory-relabel)。
   reads = Deployment の見張り(MemoryFollows)に伝えた「ns/名」の列(伝えた順 — 見張りの始めの list と変化の出来事。時間で読みに行く
   数の検・#3868)。node-reads = Node の見張りが coordinator へ伝えた node の名の列(伝えた順 — 時間で node の label を読みに行く数の検・
   #4070)。stalled-until-ms = この時刻(調停ループの now)までは k8s の API が答えない(見張りは答えない理由を伝える — #2807)。
   欄を書き換えるのは置き場の外(検・模擬の宿・memory-settle・memory-relabel・follows-deployments・follows-nodes・kube-memory)で、この class の
   method は欄から答えを計算するだけ(検の台が歩をまたいで同じ 1 つを読み書きする)。"
  (defn #^ None __init__ [self #^ dict deployments #^ (| dict None) [nodes None]]
    (setv self.deployments deployments self.calls [] self.reads #() self.node-reads #() self.down False
          self.nodes (or nodes {}) self.stalled-until-ms None))

  (defn #^ dict row [self #^ str namespace #^ str name]
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (setv key (+ namespace "/" name))
    (when (not-in key self.deployments) (raise (KubeUnavailable (+ "無い Deployment: " key))))
    (get self.deployments key))

  (defn #^ OpaqueJson deployment-object [self #^ dict row]
    "Deployment の行を、本番の k8s の API の答えと同じ Deployment の object の形で返す(本番と同じ解き deployment-view を通すため)。
     行に欄が無い時の値は以前の模擬の読みの既定と同じ(台数 0・世代 1・annotations は空)。"
    (OpaqueJson.of
      {"spec" {"replicas" (get row "specReplicas")}
       "status" {"replicas" (.get row "replicas" 0) "readyReplicas" (.get row "readyReplicas" 0)
                 "availableReplicas" (.get row "availableReplicas" 0) "updatedReplicas" (.get row "updatedReplicas" 0)
                 "observedGeneration" (.get row "observedGeneration" 1)}
       "metadata" {"generation" (.get row "generation" 1) "annotations" (.get row "annotations" {})}}))

  (defn #^ (| str None) refusal-at [self #^ int now-ms]
    "見張りが now-ms に、相手を問わず伝える読めない理由(止まっている・答えない)。無ければ None。"
    (cond
      self.down "テストの k8s が止まっている"
      (and (is-not self.stalled-until-ms None) (< now-ms self.stalled-until-ms)) "テストの k8s が答えない"
      True None))

  (defn #^ (| OpaqueJson str) seen-at [self #^ str key #^ int now-ms]
    "Deployment の見張りが now-ms に「ns/名」について伝える物: Deployment の object か、読めない理由(止まっている・答えない・無い)。"
    (setv refusal (.refusal-at self now-ms))
    (cond
      (is-not refusal None) refusal
      (not-in key self.deployments) (+ "無い Deployment: " key)
      True (.deployment-object self (get self.deployments key))))

  (defn #^ (| OpaqueJson str) node-seen-at [self #^ str node #^ int now-ms]
    "Node の見張りが now-ms に node について伝える物: 本番の k8s の API と同じ Node の object(metadata.name と labels)か、読めない理由
     (止まっている・答えない・無い)。"
    (setv refusal (.refusal-at self now-ms))
    (cond
      (is-not refusal None) refusal
      (not-in node self.nodes) (+ "無い Node: " node)
      True (OpaqueJson.of {"metadata" {"name" node "labels" (get self.nodes node)}}))))


(defk memory-relabel [kube node labels]
  {:pre [(: kube KubeMemory) (: node str) (: labels dict)] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "テストの k8s の node の label を labels(k8s の Node の metadata.labels と同じ形)に替えるため(本番の Node の label の変化に当たる・#4070)。"
  (setv (get kube.nodes node) (.copy labels))
  None)


(defk memory-settle [kube key ready]
  {:pre [(: kube KubeMemory) (: key str) (: ready (| int None))] :post [(: % None)] :tags {:context "coordinator" :role "protocol" :spells "json"}}
  "テストの k8s の Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とするため。"
  (val row (get kube.deployments key))
  (val n (get row "specReplicas"))
  (setv (get row "replicas") n)
  (setv (get row "readyReplicas") (if (is ready None) n ready))
  (setv (get row "availableReplicas") n)
  (setv (get row "updatedReplicas") n)
  None)


(defrecord MemoryWatching
  "模擬の見張りが見張っている相手 1 つ: key = 相手のキー・delivered = 最後に伝えた物の印(watch-mark — まだ伝えていなければ None)。"
  (#^ str key)
  (#^ (| tuple None) delivered))


(defrecord MemoryWatch
  "模擬の coordinator の process 1 つの、1 つの種類(Deployment か Node)の見張りの置き場(本番の見張りに当たる — process が起き直せば作り直し、
   始めの list からやり直す)。テストの k8s の今を読み、最後に伝えた物から変わった物を伝える(memory-watch-follow)。watching = 見張っている相手
   (MemoryWatching)の列 — 置き場を作る時に空の列を 1 つ作り、中を書くのは memory-watch-follow だけ。"
  (setv #^ list watching (field :default-factory list)))


(defk memory-watch-changes [watch seen]
  {:pre [(: watch MemoryWatch) (: seen Callable)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張っている相手のうち、テストの k8s の今(seen = キー → 伝える物)が最後に伝えた物と違う物の #(キー 本文か理由) の tuple を知るため。"
  (tuple (gfor w watch.watching
               :setv now (seen w.key)
               :if (!= (watch-mark now) w.delivered)
               #(w.key now))))


(defk memory-watch-follow [watch seen keys]
  {:pre [(: watch MemoryWatch) (: seen Callable) (: keys tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張る相手を keys に揃え(外した相手の伝えた印は忘れる)、最後に伝えた物から変わった物を伝えるため。答え = 伝えた #(キー 本文か理由) の
   tuple(呼び手がテストの k8s の記録に積む)。"
  (val before (tuple watch.watching))
  (val distinct (tuple (gfor #(i key) (enumerate keys) :if (= (.index keys key) i) key)))
  (val aligned (tuple (gfor key distinct
                            (MemoryWatching :key key :delivered (next (gfor w before :if (= w.key key) w.delivered) None)))))
  (.clear watch.watching)
  (.extend watch.watching aligned)
  (<- changed tuple (memory-watch-changes watch seen))
  (val marked (tuple (gfor #(key now) changed #(key (watch-mark now)))))
  (val updated (list (gfor w watch.watching
                           (MemoryWatching :key w.key :delivered (next (gfor #(k mark) marked :if (= k w.key) mark) w.delivered)))))
  (.clear watch.watching)
  (.extend watch.watching updated)
  changed)


(defrecord MemoryFollows
  "模擬の coordinator の process 1 つの見張りの置き場(Deployment と Node の 2 つ — MemoryWatch)。テストの k8s(KubeMemory)を読み、伝えた
   キーをテストの k8s の記録(reads・node-reads)に積む(follows-deployments・follows-nodes)。"
  (setv #^ MemoryWatch deployments (field :default-factory MemoryWatch))
  (setv #^ MemoryWatch nodes (field :default-factory MemoryWatch)))


(defk follows-deployments [follows kube keys now-ms]
  {:pre [(: follows MemoryFollows) (: kube KubeMemory) (: keys tuple) (: now-ms int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張る Deployment を keys に揃え、変わった物を伝える(テストの k8s の reads に積む)ため。答え = 伝えた #(「ns/名」 本文か理由) の tuple。"
  (<- changed tuple (memory-watch-follow follows.deployments (fn [key] (.seen-at kube key now-ms)) keys))
  (setv kube.reads (+ kube.reads (tuple (gfor #(key _) changed key))))
  changed)


(defk follows-nodes [follows kube names now-ms]
  {:pre [(: follows MemoryFollows) (: kube KubeMemory) (: names tuple) (: now-ms int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "見張る Node を names に揃え、変わった物を伝える(テストの k8s の node-reads に積む)ため。答え = 伝えた #(node の名 本文か理由) の tuple。"
  (<- changed tuple (memory-watch-follow follows.nodes (fn [name] (.node-seen-at kube name now-ms)) names))
  (setv kube.node-reads (+ kube.node-reads (tuple (gfor #(name _) changed name))))
  changed)


(defk follows-changed [follows kube now-ms]
  {:pre [(: follows MemoryFollows) (: kube KubeMemory) (: now-ms int)] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "見張っている相手のどれかの、テストの k8s の今が最後に伝えた物と違うか(伝えていない変化が在るか)を知るため。"
  (<- deployments tuple (memory-watch-changes follows.deployments (fn [key] (.seen-at kube key now-ms))))
  (<- nodes tuple (memory-watch-changes follows.nodes (fn [name] (.node-seen-at kube name now-ms))))
  (bool (or deployments nodes)))


(defk follows-change-at [follows kube now-ms]
  {:pre [(: follows MemoryFollows) (: kube KubeMemory) (: now-ms int)] :post [(: % (| int None))] :tags {:context "coordinator" :role "protocol"}}
  "見張っている相手の伝える物が、now-ms の後に時刻で変わる刻(テストの k8s が答えない区間の終わり)を知るため。無ければ None。"
  (if (and (or follows.deployments.watching follows.nodes.watching)
           (is-not kube.stalled-until-ms None) (< now-ms kube.stalled-until-ms))
      kube.stalled-until-ms
      None))


(defk follow-wait [kube follows timeout-seconds now]
  {:pre [(: kube KubeMemory) (: follows MemoryFollows) (: timeout-seconds (| float None)) (: now int)] :post [(: % (| float None))]
   :tags {:context "coordinator" :role "protocol"}}
  "模擬の k8s が受付の待ちを縮めた秒を知るため: 見張りが伝えていない変化が在れば 0(本番の見張りは変化の刻に受付を起こす)、答えない
   区間の終わりに見張りの伝える物が変わるなら、その刻までの秒と timeout-seconds(None = 期限なし)の短い方、それ以外は timeout-seconds
   のまま。"
  (<- at (| int None) (follows-change-at follows kube now))
  (<- changed bool (follows-changed follows kube now))
  (cond
    changed 0.0
    (is at None) timeout-seconds
    (is timeout-seconds None) (/ (- at now) 1000.0)
    True (min timeout-seconds (/ (- at now) 1000.0))))


(defhandler kube-memory [#^ KubeMemory kube #^ MemoryFollows follows]
  ;; 引数に残す理由: テストの k8s の状態は検が持ち、歩をまたいで同じ 1 つを読み書きする。見張りの状態は coordinator の process の物で、
  ;; 組を作る側(emulated-handlers — coordinator の起き直しごと)が作って渡す
  (FollowDeployments [keys now-ms]
    (<- followed tuple (follows-deployments follows kube keys now-ms))
    (<- writes tuple (deployment-writes followed now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    (<- followed tuple (follows-nodes follows kube names now-ms))
    (<- writes tuple (node-writes followed now-ms))
    (resume writes))
  (NextRequests [timeout-seconds limit]
    ;; 見張りが伝えていない変化が在る間は受付を待たずに取る(本番の見張りが受付の箱を起こすのと同じ刻 — 頭の註)。
    (<- now int (now-epoch-ms))
    (<- wait (| float None) (follow-wait kube follows timeout-seconds now))
    (<- batch list (NextRequests wait limit))
    (resume batch))
  (ScaleDeployment [namespace name replicas dry-run]
    (setv row (.row kube namespace name))
    (.append kube.calls {"op" "scale" "key" (+ namespace "/" name) "replicas" replicas "dryRun" dry-run})
    (when (not dry-run) (setv (get row "specReplicas") replicas))
    (resume replicas))
  (AnnotateDeployment [namespace name annotations]
    (setv row (.row kube namespace name))
    (.append kube.calls {"op" "annotate" "key" (+ namespace "/" name) "annotations" annotations})
    (setv merged (| (.get row "annotations" {}) annotations))
    (setv (get row "annotations") (dfor #(k v) (.items merged) :if (is-not v None) k v))
    (resume None)))
