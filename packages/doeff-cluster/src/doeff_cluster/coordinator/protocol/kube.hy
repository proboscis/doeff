;;; coordinator の kube_model の effect に答える handler。kube-api = Pod の中から k8s の API へ(foundation/kube_client の
;;; KubeClient — 層 protocol は foundation を読めないので、client は下の KubeCalls の形で受け、組み立ては entry が持つ)・
;;; kube-memory = テストの dict・kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。
;;; 読みの答え(Deployment の観測・node の label)を k8s の JSON から型の値へ解くのはこの module の 1 点(deployment-reading・
;;; node-labels-table — #2728)。本物と検の答え手が同じ解きを通り、core は型の値だけを受ける。
;;;
;;; 読みを調停ループの外へ(#2807): StartKubeReads で読みの束(KubeReadBatch)を 1 つ置き、本番の kube-api は調停ループの外の daemon の thread で
;;; k8s の API へ順に撃つ(同期の client が scheduler の thread を塞がない — 本番の要求待ちは scheduler に番を回さないので、task では外せない)。
;;; CollectKubeReads は束を待たずに見て、終わっていれば観測の表への書きへ解く(collected-reads — 3 つの答え手が同じ 1 点を通る)。
;;; 模擬の kube-memory は束を始めた時に読み、stalled-until-ms までは「まだ」と答える(k8s の読みが答えない時間の模擬)。
(require doeff-hy.macros [val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler defk <-])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Mapping])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import typing [Protocol])
(import doeff_hy.wire [parse Malformed])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.frozen [freeze-json-text thaw-json])
(import doeff_hy.table [Table TableWrite table-of])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentReading DeploymentSeen DeploymentUnreadable NodeLabelsSeen NodeLabelsUnreadable])
(import doeff_cluster.coordinator.intent.kube_model [ScaleDeployment AnnotateDeployment KubeUnavailable
                                                     StartKubeReads CollectKubeReads KubeReadsIdle KubeReadsRunning KubeReadsDone])


(defk deployment-view [body]
  {:pre [(: body (get Mapping #(str object)))] :post [(: % (get dict #(str object)))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Deployment の object(API の JSON の本文)から、Rollout が見る欄だけの観測の JSON を読むため(deployment-reading が型の値へ解く)。
   KubeClient.read は本文を返すだけで、読みはこの 1 点(#2764 — 以前は foundation/kube_client の method が素で呼んだ)。"
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


(defk node-labels-table [labels]
  {:pre [(: labels (get Mapping #(str object)))] :post [(: % (get Table str))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Node の metadata.labels(鍵 → 値の JSON)を node の label の観測の表へ写すため(core は写像を受けない)。k8s の label の値は
   文字列で、文字列でない値は表に書かない。"
  (table-of (tuple (gfor #(key value) (.items labels) :if (isinstance value str) (TableWrite key value)))))


(defclass KubeCalls [Protocol]
  "kube-api が呼ぶ k8s の client の形(foundation/kube_client の KubeClient がこの形を持つ)。届かない時は KubeUnavailable を投げる。"
  ;; 読みの答えは API の JSON の本文を中を読まずに運ぶ値(解くのは受け取りの defk の 1 点 — #2807)。
  (defn #^ OpaqueJson node-labels [self #^ str node] (raise NotImplementedError))  ; 答え = Node の metadata.labels
  (defn #^ OpaqueJson read [self #^ str namespace #^ str name] (raise NotImplementedError))  ; 答え = Deployment の object
  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run] (raise NotImplementedError))
  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations] (raise NotImplementedError))
  ;; 読みの束を調停ループの外の thread で読ませる(同期の client が scheduler の thread を塞がない)。答え = 終わったかを答える関数。
  (defn #^ (get Callable #([] bool)) in-background [self #^ (get Callable #([] None)) work] (raise NotImplementedError)))


;; --- 読みの束(調停ループの外の読み — 頭の註・#2807)------------------------------------------------------------

(defrecord KubeBodyRead
  "読みの束の 1 件の答え: name = 「ns/名」か node の名・body = k8s の API の JSON の本文(Deployment の object か node の metadata.labels)を
   中を読まずに運ぶ値(読みの thread は解かない — 解くのは受け取りの defk deployment-observation-of・node-labels-observation-of)。"
  (#^ str name)
  (#^ OpaqueJson body))


(defrecord KubeReadFailed
  "読みの束の 1 件の答え: 届かない・断られた・答えの中で上がった例外の理由。"
  (#^ str name)
  (#^ str error))


(defclass KubeReadBatch []
  "走っている読みの束 1 つ: 読む鍵(deployments = 「ns/名」・nodes = node の名)・始めた時刻・名指したか・終わったら答えの列
   (調停ループの外の thread が書き、答え手が受け取る — finished が真を返すまで答えの列は読まない)。finished = 終わったかを答える関数
   (本番は k8s の client の in-background が返す物・同じ thread で読む答え手は読み終えた時に真へ替える)。"
  (defn #^ None __init__ [self #^ (get tuple #(str ...)) deployments #^ (get tuple #(str ...)) nodes #^ int started-ms]
    (setv self.deployments deployments self.nodes nodes self.started-ms started-ms self.named False)
    (setv #^ (get Callable #([] bool)) self.finished (fn [] False))
    (setv #^ (get tuple #((| KubeBodyRead KubeReadFailed) ...)) self.deployment-results #())
    (setv #^ (get tuple #((| KubeBodyRead KubeReadFailed) ...)) self.node-results #())
    None)

  (defn #^ (| KubeBodyRead KubeReadFailed) read-one [self #^ (get Callable #([] OpaqueJson)) read #^ str name]
    "1 件を読み、届かない・断られた・中で上がった例外を理由の値にする(1 件の失敗で束が終わらないままにならないように)。"
    (try
      (KubeBodyRead :name name :body (read))
      (except [error Exception]
        (KubeReadFailed :name name :error (str error)))))

  (defn #^ None read-with [self #^ (get Callable #([str] OpaqueJson)) read-deployment #^ (get Callable #([str] OpaqueJson)) read-node]
    "束の鍵を順に読み(read-deployment は「ns/名」・read-node は node の名を受ける)、答えの列を置く(終わった印は読ませた側が立てる)。"
    (setv self.deployment-results (tuple (gfor key self.deployments (.read-one self (fn [] (read-deployment key)) key)))
          self.node-results (tuple (gfor node self.nodes (.read-one self (fn [] (read-node node)) node))))
    None)

  (defn #^ None read-now [self #^ (get Callable #([str] OpaqueJson)) read-deployment #^ (get Callable #([str] OpaqueJson)) read-node]
    "同じ thread で束を読み終え、終わった印を立てる(資格の無い所と模擬の k8s の答え手が使う)。"
    (.read-with self read-deployment read-node)
    (setv self.finished (fn [] True))
    None))


(defclass KubeReadBatches []
  "読みの束の置き場(答え手が拍をまたいで同じ 1 つを読み書きする)。current = 走っているか、終わって受け取られていない束(1 度に 1 つ)。"
  (defn #^ None __init__ [self]
    (setv #^ (| KubeReadBatch None) self.current None)
    None)

  (defn #^ (| KubeReadBatch None) begin [self #^ (get tuple #(str ...)) deployments #^ (get tuple #(str ...)) nodes #^ int started-ms]
    "束が無ければ新しい束を置いて返す。束が在れば None(読みは 1 度に 1 つ)。"
    (when (is-not self.current None)
      (return None))
    (setv batch (KubeReadBatch deployments nodes started-ms))
    (setv self.current batch)
    batch))


(defk deployment-observation-of [result at]
  {:pre [(: result (| KubeBodyRead KubeReadFailed)) (: at int)] :post [(: % (| DeploymentSeen DeploymentUnreadable))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "読みの束の Deployment 1 件の答えを、観測の表に置く観測にするため(本文は deployment-view と deployment-reading で型へ解く・形が違えば
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


(defk node-labels-observation-of [result at]
  {:pre [(: result (| KubeBodyRead KubeReadFailed)) (: at int)] :post [(: % (| NodeLabelsSeen NodeLabelsUnreadable))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "読みの束の node 1 件の答え(metadata.labels)を、観測の表に置く観測にするため。"
  (if (isinstance result KubeReadFailed)
      (NodeLabelsUnreadable :error result.error :at at)
      (do (val opened (thaw-json (freeze-json-text result.body.text)))
          (if (isinstance opened dict)
              (do (<- labels (get Table str) (node-labels-table opened))
                  (NodeLabelsSeen :labels labels :at at))
              (NodeLabelsUnreadable :error "k8s の Node の label が object でない" :at at)))))


(defk kube-reads-done [batch]
  {:pre [(: batch KubeReadBatch)] :post [(: % KubeReadsDone)] :tags {:context "coordinator" :role "protocol"}}
  "終わった束の答えの列を、観測の表への書き(時刻は読みを始めた時刻)にするため。"
  (var deployments #())
  (for [result batch.deployment-results]
    (<- deployment-seen (| DeploymentSeen DeploymentUnreadable) (deployment-observation-of result batch.started-ms))
    (:= deployments (+ deployments #((TableWrite result.name deployment-seen)))))
  (var nodes #())
  (for [result batch.node-results]
    (<- node-seen (| NodeLabelsSeen NodeLabelsUnreadable) (node-labels-observation-of result batch.started-ms))
    (:= nodes (+ nodes #((TableWrite result.name node-seen)))))
  (KubeReadsDone :deployments deployments :nodes nodes))


(defk collected-reads [batches now-ms name-after-ms held]
  {:pre [(: batches KubeReadBatches) (: now-ms int) (: name-after-ms int) (: held bool)]
   :post [(: % (| KubeReadsIdle KubeReadsRunning KubeReadsDone))] :tags {:context "coordinator" :role "protocol"}}
  "CollectKubeReads の答えを、束の置き場から待たずに出すため(3 つの答え手が同じ 1 点を通る)。held = 終わっていても「まだ」と答える
   (模擬の k8s が答えない時間)。名指す秒を超えた初回だけ overdue を立てる。"
  (val batch batches.current)
  (cond
    (is batch None)
      (KubeReadsIdle)
    (or held (not (batch.finished)))
      (do (val overdue (and (not batch.named) (>= (- now-ms batch.started-ms) name-after-ms)))
          (when overdue
            (setv batch.named True))
          (KubeReadsRunning :started-ms batch.started-ms :overdue overdue))
    True
      (do (setv batches.current None)
          (<- done KubeReadsDone (kube-reads-done batch))
          done)))


(defhandler kube-api [#^ KubeCalls client #^ KubeReadBatches batches]
  ;; 引数に残す理由: k8s の client(HTTP の接続と token の置き場)と読みの束の置き場は composition root が 1 つずつ作って渡す
  (StartKubeReads [deployments nodes started-ms]
    (val batch (.begin batches deployments nodes started-ms))
    (when (is-not batch None)
      ;; 調停ループの外の thread で読む(同期の client が scheduler の thread を塞がない — 頭の註)。thread は k8s の client(foundation)が持つ。
      (setv batch.finished (.in-background client (fn [] (.read-with batch (fn [key] (.read client #* (.split key "/" 1)))
                                                                         (fn [node] (.node-labels client node)))))))
    (resume (is-not batch None)))
  (CollectKubeReads [now-ms name-after-ms]
    (<- collected (| KubeReadsIdle KubeReadsRunning KubeReadsDone) (collected-reads batches now-ms name-after-ms False))
    (resume collected))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason #^ KubeReadBatches batches]
  ;; 引数に残す理由: 資格が無い理由の文と読みの束の置き場は composition root が起動の時に 1 度だけ決める
  (StartKubeReads [deployments nodes started-ms]
    ;; 読みは始めた時に全部その理由で終わる(観測は読めなかった観測になり、Rollout はその理由を名指して Unknown と扱う)。
    (val batch (.begin batches deployments nodes started-ms))
    (when (is-not batch None)
      (.read-now batch (fn [key] (raise (KubeUnavailable reason))) (fn [node] (raise (KubeUnavailable reason)))))
    (resume (is-not batch None)))
  (CollectKubeReads [now-ms name-after-ms]
    (<- collected (| KubeReadsIdle KubeReadsRunning KubeReadsDone) (collected-reads batches now-ms name-after-ms False))
    (resume collected))
  (ScaleDeployment [namespace name replicas dry-run] (raise (KubeUnavailable reason)))
  (AnnotateDeployment [namespace name annotations] (raise (KubeUnavailable reason))))


(defclass KubeMemory []
  "テストの k8s。deployments = 「ns/名」→ 観測の dict(specReplicas・readyReplicas・annotations …)。
   scale は宣言の台数だけを変える(Pod が立つ・消えるのはテストが .settle で進める)。calls = 受けた書きの記録。
   down = 真の間は全部 KubeUnavailable(API の途絶)。nodes = node の名 → label の dict(能力の導出の検)。
   読みは本番と同じ束で受け(batches)、始めた時に読む。stalled-until-ms = この時刻(調停ループの now)までは読みが「まだ」と答える
   (k8s の読みが答えない時間の模擬 — #2807)。"
  (defn #^ None __init__ [self #^ dict deployments #^ (| dict None) [nodes None]]
    (setv self.deployments deployments self.calls [] self.down False self.nodes (or nodes {})
          self.batches (KubeReadBatches) self.stalled-until-ms None))

  (defn #^ dict row [self #^ str namespace #^ str name]
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (setv key (+ namespace "/" name))
    (when (not-in key self.deployments) (raise (KubeUnavailable (+ "無い Deployment: " key))))
    (get self.deployments key))

  (defn #^ OpaqueJson deployment-object [self #^ str key]
    "「ns/名」の行を、本番の k8s の API の答えと同じ Deployment の object の形で返す(本番と同じ解き deployment-view を通すため)。
     行に欄が無い時の値は以前の模擬の読みの既定と同じ(台数 0・世代 1・annotations は空)。"
    (setv row (.row self #* (.split key "/" 1)))
    (OpaqueJson.of
      {"spec" {"replicas" (get row "specReplicas")}
       "status" {"replicas" (.get row "replicas" 0) "readyReplicas" (.get row "readyReplicas" 0)
                 "availableReplicas" (.get row "availableReplicas" 0) "updatedReplicas" (.get row "updatedReplicas" 0)
                 "observedGeneration" (.get row "observedGeneration" 1)}
       "metadata" {"generation" (.get row "generation" 1) "annotations" (.get row "annotations" {})}}))

  (defn #^ OpaqueJson node-labels-of [self #^ str node]
    "node の label(本番の k8s の API の Node の metadata.labels と同じ形)を返す。"
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (when (not-in node self.nodes) (raise (KubeUnavailable (+ "無い Node: " node))))
    (OpaqueJson.of (get self.nodes node)))

  (defn #^ None settle [self #^ str key #^ (| int None) [ready None]]
    "Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とする。"
    (setv row (get self.deployments key) n (get row "specReplicas"))
    (.update row {"replicas" n "readyReplicas" (if (is ready None) n ready) "availableReplicas" n "updatedReplicas" n})))


(defhandler kube-memory [#^ KubeMemory kube]
  ;; 引数に残す理由: テストの k8s の状態は検が持ち、拍をまたいで同じ 1 つを読み書きする
  (StartKubeReads [deployments nodes started-ms]
    (val batch (.begin kube.batches deployments nodes started-ms))
    (when (is-not batch None)
      (.read-now batch kube.deployment-object kube.node-labels-of))
    (resume (is-not batch None)))
  (CollectKubeReads [now-ms name-after-ms]
    (val held (and (is-not kube.stalled-until-ms None) (< now-ms kube.stalled-until-ms)))
    (<- collected (| KubeReadsIdle KubeReadsRunning KubeReadsDone) (collected-reads kube.batches now-ms name-after-ms held))
    (resume collected))
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
