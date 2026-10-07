;;; coordinator の kube_model の effect に答える handler。kube-api = Pod の中から k8s の API へ(foundation/kube_client の
;;; KubeClient — 層 protocol は foundation を読めないので、client は下の KubeCalls の形で受け、組み立ては entry が持つ)・
;;; kube-memory = テストの dict・kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。
;;; 見張りが伝えた物(Deployment の観測・Node の label)を k8s の JSON から型の値へ解くのはこの module の 1 点(deployment-reading・
;;; node-labels-table — #2728)。本物と検の答え手が同じ解きを通り、core は型の値だけを受ける。
;;;
;;; Deployment も Node も時間で読みに行かず見張る(Deployment — #3868・Node — #4070): FollowDeployments・FollowNodes で見張る相手を
;;; 揃え、本番の kube-api は相手ごとに list の後の watch を調停ループの外の daemon の thread で受ける(KubeClient.follow・follow-node)。
;;; 見張りの thread は変化を受け渡しの箱(ObjectWatches — 種類ごとに 1 つ)に置いて受付の箱を起こし、調停ループは待たずに取る。
;;; 模擬の kube-memory は、見張り(MemoryFollows — coordinator の process ごと)が見張っている相手の今が最後に伝えた物と違えば伝え、
;;; 伝えていない変化が在る間は受付を待たずに返す(本番の見張りが受付を起こすのと同じ刻)。
(require doeff-hy.macros [val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler defk <-])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Mapping])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import queue)
(import typing [Protocol])
(import doeff_hy.wire [parse Malformed])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.frozen [freeze-json-text thaw-json])
(import doeff_hy.table [Table TableWrite table-of])
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
  "kube-api が呼ぶ k8s の client の形(foundation/kube_client の KubeClient がこの形を持つ)。届かない時は KubeUnavailable を投げる。"
  ;; Deployment 1 つを list の後の watch で見張る daemon の thread を始める(#3868)。on-body = Deployment の object・on-error = 届かない・
  ;; 断られた・消された理由(どちらも見張りの thread から呼ぶ)。答え = 見張りを止める関数。
  (defn #^ (get Callable #([] None)) follow [self #^ str namespace #^ str name #^ (get Callable #([OpaqueJson] None)) on-body
                                             #^ (get Callable #([str] None)) on-error]
    (raise NotImplementedError))
  ;; Node 1 つを同じ形で見張る(#4070)。on-body = Node の object(中を読まずに運ぶ — label の読みは node-labels-view の 1 点)。
  (defn #^ (get Callable #([] None)) follow-node [self #^ str name #^ (get Callable #([OpaqueJson] None)) on-body
                                                  #^ (get Callable #([str] None)) on-error]
    (raise NotImplementedError))
  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run] (raise NotImplementedError))
  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations] (raise NotImplementedError)))


;; --- 見張り(時間で読みに行かない — 頭の註・#3868・#4070)---------------------------------------------------------

(defclass ObjectWatches []
  "見張っている k8s の object(1 つの種類 — Deployment か Node)と、見張りの thread から調停ループへの受け渡し。stops = 見張っているキー
   (Deployment は「ns/名」・Node は名)→ 見張りを止める関数・noted = キー → 最後に受け渡した物の印(mark — 同じ物を続けて受け渡さない:
   断られた見張りが試し直すたびに調停ループを起こさない)・passed = 見張りの thread が置き、調停ループが取る #(キー 本文か理由) の列
   (queue.SimpleQueue — thread の間の受け渡し)。"
  (defn #^ None __init__ [self]
    (setv self.stops {} self.noted {} self.passed (queue.SimpleQueue))
    None)

  (defn [staticmethod] #^ tuple mark [#^ (| OpaqueJson str) seen]
    "見張りが伝える物の比べの印: object は JSON の文・読めない理由はその文。"
    (if (isinstance seen str) #("error" seen) #("body" seen.text)))

  (defn #^ bool note [self #^ str key #^ (| OpaqueJson str) seen]
    "見張りの thread が呼ぶ: key の今(object か、読めない理由)が前に受け渡した物と違えば受け渡す。答え = 受け渡したか
     (真なら呼び手が受付の箱を起こす)。"
    (setv mark (ObjectWatches.mark seen))
    (when (= (.get self.noted key) mark)
      (return False))
    (setv (get self.noted key) mark)
    (.put self.passed #(key seen))
    True)

  (defn #^ None follow [self #^ (get tuple #(str ...)) keys #^ (get Callable #([str] (get Callable #([] None)))) start]
    "見張る相手を keys に揃える: keys に無い見張りを止めて最後に受け渡した印を忘れ、新しいキーの見張りを start(キー → 止める関数)で
     始める。"
    (for [key (tuple self.stops)]
      (when (not-in key keys)
        ((.pop self.stops key))
        (.pop self.noted key None)))
    (for [key keys]
      (when (not-in key self.stops)
        (setv (get self.stops key) (start key))))
    None)

  (defn #^ tuple take [self]
    "受け渡された物を待たずに全部取り、見張っているキーごとに最後の 1 つを #(キー 本文か理由) の tuple で返す。"
    (setv latest {} draining True)
    (while draining
      (try
        (setv item (.get-nowait self.passed)
              latest (| latest {(get item 0) (get item 1)}))
        (except [queue.Empty]
          (setv draining False))))
    (tuple (gfor #(key seen) (.items latest) :if (in key self.stops) #(key seen)))))


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
          (<- labels (| (get Mapping #(str object)) None) (if (isinstance opened dict) (node-labels-view opened) None))
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


(defhandler kube-api [#^ KubeCalls client #^ ObjectWatches deployments #^ ObjectWatches nodes #^ (get Callable #([] None)) wake]
  ;; 引数に残す理由: k8s の client(HTTP の接続と token)・Deployment と Node の見張りの受け渡しと、受付の箱を起こす関数は
  ;; composition root が 1 つずつ作って渡す
  (FollowDeployments [keys now-ms]
    ;; 見張りは調停ループの外の thread(k8s の client が持つ)。変化を受け渡したら受付の箱を起こす(頭の註)。
    (.follow deployments keys
             (fn [key]
               (.follow client #* (.split key "/" 1)
                        (fn [body] (when (.note deployments key body) (wake)))
                        (fn [error] (when (.note deployments key error) (wake))))))
    (<- writes tuple (deployment-writes (.take deployments) now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    ;; Deployment と同じ形(頭の註)。
    (.follow nodes names
             (fn [name]
               (.follow-node client name
                             (fn [body] (when (.note nodes name body) (wake)))
                             (fn [error] (when (.note nodes name error) (wake))))))
    (<- writes tuple (node-writes (.take nodes) now-ms))
    (resume writes))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason #^ ObjectWatches deployments #^ ObjectWatches nodes]
  ;; 引数に残す理由: 資格が無い理由の文と見張りの受け渡しは composition root が起動の時に 1 度だけ決める
  (FollowDeployments [keys now-ms]
    ;; 見張りは始めた時にその理由を 1 度だけ伝える(観測は読めなかった観測になり、Rollout はその理由を名指して Unknown と扱う)。
    (.follow deployments keys (fn [key] (.note deployments key reason) (fn [] None)))
    (<- writes tuple (deployment-writes (.take deployments) now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    ;; Deployment と同じ(node の worker は前に導いた能力を保つ)。
    (.follow nodes names (fn [name] (.note nodes name reason) (fn [] None)))
    (<- writes tuple (node-writes (.take nodes) now-ms))
    (resume writes))
  (ScaleDeployment [namespace name replicas dry-run] (raise (KubeUnavailable reason)))
  (AnnotateDeployment [namespace name annotations] (raise (KubeUnavailable reason))))


(defclass KubeMemory []
  "テストの k8s。deployments = 「ns/名」→ 観測の dict(specReplicas・readyReplicas・annotations …)。
   scale は宣言の台数だけを変える(Pod が立つ・消えるのはテストが .settle で進める)。calls = 受けた書きの記録。
   down = 真の間は全部 KubeUnavailable(API の途絶)。nodes = node の名 → label の dict(能力の導出の検 — label の変化は .relabel)。
   reads = Deployment の見張り(MemoryFollows)に伝えた「ns/名」の列(伝えた順 — 見張りの始めの list と変化の出来事。時間で読みに行く
   数の検・#3868)。node-reads = Node の見張りが coordinator へ伝えた node の名の列(伝えた順 — 時間で node の label を読みに行く数の検・
   #4070)。stalled-until-ms = この時刻(調停ループの now)までは k8s の API が答えない(見張りは答えない理由を伝える — #2807)。"
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
      True (OpaqueJson.of {"metadata" {"name" node "labels" (get self.nodes node)}})))

  (defn #^ None relabel [self #^ str node #^ dict labels]
    "node の label を labels(k8s の Node の metadata.labels と同じ形)に替える(本番の Node の label の変化に当たる・#4070)。"
    (setv (get self.nodes node) (dict labels))
    None)

  (defn #^ None settle [self #^ str key #^ (| int None) [ready None]]
    "Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とする。"
    (setv row (get self.deployments key) n (get row "specReplicas"))
    (.update row {"replicas" n "readyReplicas" (if (is ready None) n ready) "availableReplicas" n "updatedReplicas" n})))


(defclass MemoryWatch []
  "模擬の coordinator の process 1 つの、1 つの種類(Deployment か Node)の見張り(本番の ObjectWatches に当たる — process が起き直せば
   作り直し、始めの list からやり直す)。テストの k8s の今を読み、最後に伝えた物から変わった物を伝える。following = 見張っているキー・
   delivered = キー → 最後に伝えた物の印(ObjectWatches.mark)。"
  (defn #^ None __init__ [self]
    (setv self.following #() self.delivered {})
    None)

  (defn #^ tuple changes [self #^ (get Callable #([str] (| OpaqueJson str))) seen]
    "見張っているキーのうち、テストの k8s の今(seen = キー → 伝える物)が最後に伝えた物と違う物の #(キー 本文か理由) の tuple。"
    (tuple (gfor key self.following
                 :setv now (seen key)
                 :if (!= (ObjectWatches.mark now) (.get self.delivered key))
                 #(key now))))

  (defn #^ tuple follow [self #^ (get Callable #([str] (| OpaqueJson str))) seen #^ (get tuple #(str ...)) keys]
    "見張る相手を keys に揃え(外したキーの伝えた印は忘れる)、最後に伝えた物から変わった物を伝える。答え = 伝えた #(キー 本文か理由) の
     tuple(呼び手がテストの k8s の記録に積む)。"
    (setv self.following keys
          self.delivered (dfor key keys :if (in key self.delivered) key (get self.delivered key)))
    (setv changed (.changes self seen))
    (setv self.delivered (| self.delivered (dfor #(key now) changed key (ObjectWatches.mark now))))
    changed))


(defclass MemoryFollows []
  "模擬の coordinator の process 1 つの見張り(Deployment と Node の 2 つ — MemoryWatch)。テストの k8s(KubeMemory)を読み、伝えたキーを
   テストの k8s の記録(reads・node-reads)に積む。"
  (defn #^ None __init__ [self]
    (setv self.deployments (MemoryWatch) self.nodes (MemoryWatch))
    None)

  (defn #^ tuple follow-deployments [self #^ KubeMemory kube #^ (get tuple #(str ...)) keys #^ int now-ms]
    "見張る Deployment を keys に揃え、変わった物を伝える(テストの k8s の reads に積む)。答え = 伝えた #(「ns/名」 本文か理由) の tuple。"
    (setv changed (.follow self.deployments (fn [key] (.seen-at kube key now-ms)) keys))
    (setv kube.reads (+ kube.reads (tuple (gfor #(key _) changed key))))
    changed)

  (defn #^ tuple follow-nodes [self #^ KubeMemory kube #^ (get tuple #(str ...)) names #^ int now-ms]
    "見張る Node を names に揃え、変わった物を伝える(テストの k8s の node-reads に積む)。答え = 伝えた #(node の名 本文か理由) の tuple。"
    (setv changed (.follow self.nodes (fn [name] (.node-seen-at kube name now-ms)) names))
    (setv kube.node-reads (+ kube.node-reads (tuple (gfor #(name _) changed name))))
    changed)

  (defn #^ bool changed [self #^ KubeMemory kube #^ int now-ms]
    "見張っている相手のどれかの、テストの k8s の今が最後に伝えた物と違うか(伝えていない変化が在るか)。"
    (bool (or (.changes self.deployments (fn [key] (.seen-at kube key now-ms)))
              (.changes self.nodes (fn [name] (.node-seen-at kube name now-ms))))))

  (defn #^ (| int None) change-at [self #^ KubeMemory kube #^ int now-ms]
    "見張っている相手の伝える物が、now-ms の後に時刻で変わる刻(テストの k8s が答えない区間の終わり)。無ければ None。"
    (if (and (or self.deployments.following self.nodes.following)
             (is-not kube.stalled-until-ms None) (< now-ms kube.stalled-until-ms))
        kube.stalled-until-ms
        None)))


(defk follow-wait [kube follows timeout-seconds now]
  {:pre [(: kube KubeMemory) (: follows MemoryFollows) (: timeout-seconds (| float None)) (: now int)] :post [(: % (| float None))]
   :tags {:context "coordinator" :role "protocol"}}
  "模擬の k8s が受付の待ちを縮めた秒を知るため: 見張りが伝えていない変化が在れば 0(本番の見張りは変化の刻に受付を起こす)、答えない
   区間の終わりに見張りの伝える物が変わるなら、その刻までの秒と timeout-seconds(None = 期限なし)の短い方、それ以外は timeout-seconds
   のまま。"
  (val at (.change-at follows kube now))
  (cond
    (.changed follows kube now) 0.0
    (is at None) timeout-seconds
    (is timeout-seconds None) (/ (- at now) 1000.0)
    True (min timeout-seconds (/ (- at now) 1000.0))))


(defhandler kube-memory [#^ KubeMemory kube #^ MemoryFollows follows]
  ;; 引数に残す理由: テストの k8s の状態は検が持ち、歩をまたいで同じ 1 つを読み書きする。見張りの状態は coordinator の process の物で、
  ;; 組を作る側(emulated-handlers — coordinator の起き直しごと)が作って渡す
  (FollowDeployments [keys now-ms]
    (<- writes tuple (deployment-writes (.follow-deployments follows kube keys now-ms) now-ms))
    (resume writes))
  (FollowNodes [names now-ms]
    (<- writes tuple (node-writes (.follow-nodes follows kube names now-ms) now-ms))
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
