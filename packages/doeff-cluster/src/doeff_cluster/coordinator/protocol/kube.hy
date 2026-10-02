;;; coordinator の kube_model の effect に答える handler。kube-api = Pod の中から k8s の API へ(foundation/kube_client の
;;; KubeClient — 層 protocol は foundation を読めないので、client は下の KubeCalls の形で受け、組み立ては entry が持つ)・
;;; kube-memory = テストの dict・kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。
;;; 読みの答え(Deployment の観測・node の label)を k8s の JSON から型の値へ解くのはこの module の 1 点(deployment-reading・
;;; node-labels-table — #2728)。本物と検の答え手が同じ解きを通り、core は型の値だけを受ける。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler defk <-])
(import collections.abc [Mapping])
(import typing [Protocol])
(import doeff_hy.wire [parse Malformed])
(import doeff_hy.table [Table TableWrite table-of])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentReading])
(import doeff_cluster.coordinator.intent.kube_model [ReadDeployment ScaleDeployment AnnotateDeployment ReadNodeLabels KubeUnavailable])


(defk deployment-reading [view]
  {:pre [(: view (get Mapping #(str object)))] :post [(: % DeploymentReading)] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Deployment の観測の JSON(foundation/kube_client の deployment-view の形)を ReadDeployment の答えの型へ解くため。形が違えば
   KubeUnavailable — Rollout はこの相手を Unknown と扱い、形の読めない答えで台数を変えない。"
  (<- parsed (parse DeploymentReading view))
  (match parsed
    (Malformed :fields fields)
      (raise (KubeUnavailable (+ "k8s の Deployment の答えの形が違う: "
                                 (.join "・" (gfor f fields (.format "{}: {}" (or f.field "本文") f.reason))))))
    _ parsed))


(defk node-labels-table [labels]
  {:pre [(: labels (get dict #(str str)))] :post [(: % (get Table str))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Node の metadata.labels(鍵 → 値の JSON)を ReadNodeLabels の答えの表へ写すため(core は写像を受けない)。"
  (table-of (tuple (gfor #(key value) (.items labels) (TableWrite key value)))))


(defclass KubeCalls [Protocol]
  "kube-api が呼ぶ k8s の client の形(foundation/kube_client の KubeClient がこの形を持つ)。届かない時は KubeUnavailable を投げる。"
  (defn #^ dict node-labels [self #^ str node] (raise NotImplementedError))
  (defn #^ dict read [self #^ str namespace #^ str name] (raise NotImplementedError))
  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run] (raise NotImplementedError))
  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations] (raise NotImplementedError)))


(defhandler kube-api [#^ KubeCalls client]
  ;; 引数に残す理由: k8s の client(HTTP の接続と token の置き場)は composition root が 1 つ作って渡す
  (ReadNodeLabels [node]
    (<- labels (node-labels-table (.node-labels client node)))
    (resume labels))
  (ReadDeployment [namespace name]
    (<- reading (deployment-reading (.read client namespace name)))
    (resume reading))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason]
  ;; 引数に残す理由: 資格が無い理由の文は composition root が起動の時に 1 度だけ決める
  (ReadNodeLabels [node] (raise (KubeUnavailable reason)))
  (ReadDeployment [namespace name] (raise (KubeUnavailable reason)))
  (ScaleDeployment [namespace name replicas dry-run] (raise (KubeUnavailable reason)))
  (AnnotateDeployment [namespace name annotations] (raise (KubeUnavailable reason))))


(defclass KubeMemory []
  "テストの k8s。deployments = 「ns/名」→ 観測の dict(specReplicas・readyReplicas・annotations …)。
   scale は宣言の台数だけを変える(Pod が立つ・消えるのはテストが .settle で進める)。calls = 受けた書きの記録。
   down = 真の間は全部 KubeUnavailable(API の途絶)。nodes = node の名 → label の dict(能力の導出の検)。"
  (defn #^ None __init__ [self #^ dict deployments #^ (| dict None) [nodes None]]
    (setv self.deployments deployments self.calls [] self.down False self.nodes (or nodes {})))

  (defn #^ dict row [self #^ str namespace #^ str name]
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (setv key (+ namespace "/" name))
    (when (not-in key self.deployments) (raise (KubeUnavailable (+ "無い Deployment: " key))))
    (get self.deployments key))

  (defn #^ None settle [self #^ str key #^ (| int None) [ready None]]
    "Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とする。"
    (setv row (get self.deployments key) n (get row "specReplicas"))
    (.update row {"replicas" n "readyReplicas" (if (is ready None) n ready) "availableReplicas" n "updatedReplicas" n})))


(defhandler kube-memory [#^ KubeMemory kube]
  ;; 引数に残す理由: テストの k8s の状態は検が持ち、拍をまたいで同じ 1 つを読み書きする
  (ReadNodeLabels [node]
    (when kube.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (when (not-in node kube.nodes) (raise (KubeUnavailable (+ "無い Node: " node))))
    (<- labels (node-labels-table (get kube.nodes node)))
    (resume labels))
  (ReadDeployment [namespace name]
    (<- reading (deployment-reading (| {"replicas" 0 "readyReplicas" 0 "availableReplicas" 0 "updatedReplicas" 0
                                        "generation" 1 "observedGeneration" 1 "annotations" {}}
                                       (.row kube namespace name))))
    (resume reading))
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
