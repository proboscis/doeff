;;; coordinator の kube_model の effect に答える handler。kube-api = Pod の中から k8s の API へ(foundation/kube_client の
;;; KubeClient — 層 protocol は foundation を読めないので、client は下の KubeCalls の形で受け、組み立ては entry が持つ)・
;;; kube-memory = テストの dict・kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。
;;; 読みの答え(Deployment の観測・node の label)を k8s の JSON から型の値へ解くのはこの module の 1 点(deployment-reading・
;;; node-labels-table — #2728)。本物と検の答え手が同じ解きを通り、core は型の値だけを受ける。
;;;
;;; Deployment は時間で読みに行かず見張る(#3868): FollowDeployments で見張る Deployment を揃え、本番の kube-api は Deployment ごとに
;;; list の後の watch を調停ループの外の daemon の thread で受ける(KubeClient.follow)。見張りの thread は変化を受け渡しの箱
;;; (DeploymentWatches)に置いて受付の箱を起こし、調停ループは待たずに取る。模擬の kube-memory は、見張り(MemoryFollows — coordinator
;;; の process ごと)が見張っている Deployment の今が最後に伝えた物と違えば伝え、伝えていない変化が在る間は受付を待たずに返す(本番の
;;; 見張りが受付を起こすのと同じ刻)。
;;; node の label の読みは調停ループの外へ(#2807): StartKubeReads で読み(KubeReadBatch)を 1 つ置き、本番の kube-api は調停ループの外の
;;; daemon の thread で k8s の API へ順に要求する(同期の client が scheduler の thread を塞がない — 本番の要求待ちは scheduler に番を回さない
;;; ので、task では外せない)。読み終えたら受付の箱を起こす(#3868)。CollectKubeReads は読みを待たずに見て、終わっていれば観測の表への
;;; 書きへ解く(collected-reads — 3 つの答え手が同じ 1 点を通る)。模擬の kube-memory は読みを始めた時に読み、stalled-until-ms までは
;;; 「まだ」と答える(k8s の読みが答えない時間の模擬)。
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
(import doeff_cluster.coordinator.intent.kube_model [ScaleDeployment AnnotateDeployment KubeUnavailable FollowDeployments
                                                     StartKubeReads CollectKubeReads KubeReadsIdle KubeReadsRunning KubeReadsDone])


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


(defk node-labels-table [labels]
  {:pre [(: labels (get Mapping #(str object)))] :post [(: % (get Table str))] :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "k8s の Node の metadata.labels(キー → 値の JSON)を node の label の観測の表へ写すため(core は写像を受けない)。k8s の label の値は
   文字列で、文字列でない値は表に書かない。"
  (table-of (tuple (gfor #(key value) (.items labels) :if (isinstance value str) (TableWrite key value)))))


(defclass KubeCalls [Protocol]
  "kube-api が呼ぶ k8s の client の形(foundation/kube_client の KubeClient がこの形を持つ)。届かない時は KubeUnavailable を投げる。"
  ;; 読みの答えは API の JSON の本文を中を読まずに運ぶ値(解くのは受け取りの defk の 1 点 — #2807)。
  (defn #^ OpaqueJson node-labels [self #^ str node] (raise NotImplementedError))  ; 答え = Node の metadata.labels
  ;; Deployment 1 つを list の後の watch で見張る daemon の thread を始める(#3868)。on-body = Deployment の object・on-error = 届かない・
  ;; 断られた・消された理由(どちらも見張りの thread から呼ぶ)。答え = 見張りを止める関数。
  (defn #^ (get Callable #([] None)) follow [self #^ str namespace #^ str name #^ (get Callable #([OpaqueJson] None)) on-body
                                             #^ (get Callable #([str] None)) on-error]
    (raise NotImplementedError))
  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run] (raise NotImplementedError))
  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations] (raise NotImplementedError))
  ;; 読みを調停ループの外の thread で読ませる(同期の client が scheduler の thread を塞がない)。then = 終わった印を立てた後に呼ぶ関数
  ;; (受付の箱を起こす — #3868)。答え = 終わったかを答える関数。
  (defn #^ (get Callable #([] bool)) in-background [self #^ (get Callable #([] None)) work #^ (get Callable #([] None)) then]
    (raise NotImplementedError)))


;; --- Deployment の見張り(時間で読みに行かない — 頭の註・#3868)---------------------------------------------------

(defclass DeploymentWatches []
  "見張っている Deployment と、見張りの thread から調停ループへの受け渡し。stops = 見張っている「ns/名」→ 見張りを止める関数・
   noted = 「ns/名」→ 最後に受け渡した物の印(mark — 同じ物を続けて受け渡さない: 断られた見張りが試し直すたびに調停ループを起こさない)・
   passed = 見張りの thread が置き、調停ループが取る #(「ns/名」 本文か理由) の列(queue.SimpleQueue — thread の間の受け渡し)。"
  (defn #^ None __init__ [self]
    (setv self.stops {} self.noted {} self.passed (queue.SimpleQueue))
    None)

  (defn [staticmethod] #^ tuple mark [#^ (| OpaqueJson str) seen]
    "見張りが伝える物の比べの印: Deployment の object は JSON の文・読めない理由はその文。"
    (if (isinstance seen str) #("error" seen) #("body" seen.text)))

  (defn #^ bool note [self #^ str key #^ (| OpaqueJson str) seen]
    "見張りの thread が呼ぶ: key の今(Deployment の object か、読めない理由)が前に受け渡した物と違えば受け渡す。答え = 受け渡したか
     (真なら呼び手が受付の箱を起こす)。"
    (setv mark (DeploymentWatches.mark seen))
    (when (= (.get self.noted key) mark)
      (return False))
    (setv (get self.noted key) mark)
    (.put self.passed #(key seen))
    True)

  (defn #^ None follow [self #^ (get tuple #(str ...)) keys #^ (get Callable #([str] (get Callable #([] None)))) start]
    "見張る Deployment を keys に揃える: keys に無い見張りを止めて最後に受け渡した印を忘れ、新しい「ns/名」の見張りを start(「ns/名」→
     止める関数)で始める。"
    (for [key (tuple self.stops)]
      (when (not-in key keys)
        ((.pop self.stops key))
        (.pop self.noted key None)))
    (for [key keys]
      (when (not-in key self.stops)
        (setv (get self.stops key) (start key))))
    None)

  (defn #^ tuple take [self]
    "受け渡された物を待たずに全部取り、見張っている「ns/名」ごとに最後の 1 つを #(「ns/名」 本文か理由) の tuple で返す。"
    (setv latest {} draining True)
    (while draining
      (try
        (setv item (.get-nowait self.passed)
              latest (| latest {(get item 0) (get item 1)}))
        (except [queue.Empty]
          (setv draining False))))
    (tuple (gfor #(key seen) (.items latest) :if (in key self.stops) #(key seen)))))


;; --- node の label の読み(調停ループの外の読み — 頭の註・#2807)------------------------------------------------------

(defrecord KubeBodyRead
  "読みの 1 件の答え: name = 「ns/名」か node の名・body = k8s の API の JSON の本文(Deployment の object か node の metadata.labels)を
   中を読まずに運ぶ値(読みの thread は解かない — 解くのは受け取りの defk deployment-observation-of・node-labels-observation-of)。"
  (#^ str name)
  (#^ OpaqueJson body))


(defrecord KubeReadFailed
  "読みの 1 件の答え: 届かない・断られた・答えの中で上がった例外の理由。"
  (#^ str name)
  (#^ str error))


(defclass KubeReadBatch []
  "走っている node の label の読み 1 つ: 読む node の名・始めた時刻・名指したか・終わったら答えの列(調停ループの外の thread が書き、
   答え手が受け取る — finished が真を返すまで答えの列は読まない)。finished = 終わったかを答える関数(本番は k8s の client の
   in-background が返す物・同じ thread で読む答え手は読み終えた時に真へ替える)。"
  (defn #^ None __init__ [self #^ (get tuple #(str ...)) nodes #^ int started-ms]
    (setv self.nodes nodes self.started-ms started-ms self.named False)
    (setv #^ (get Callable #([] bool)) self.finished (fn [] False))
    (setv #^ (get tuple #((| KubeBodyRead KubeReadFailed) ...)) self.node-results #())
    None)

  (defn #^ (| KubeBodyRead KubeReadFailed) read-one [self #^ (get Callable #([] OpaqueJson)) read #^ str name]
    "1 件を読み、届かない・断られた・中で上がった例外を理由の値にする(1 件の失敗で読みが終わらないままにならないように)。"
    (try
      (KubeBodyRead :name name :body (read))
      (except [error Exception]
        (KubeReadFailed :name name :error (str error)))))

  (defn #^ None read-with [self #^ (get Callable #([str] OpaqueJson)) read-node]
    "node を順に読み(read-node は node の名を受ける)、答えの列を置く(終わった印は読ませた側が立てる)。"
    (setv self.node-results (tuple (gfor node self.nodes (.read-one self (fn [] (read-node node)) node))))
    None)

  (defn #^ None read-now [self #^ (get Callable #([str] OpaqueJson)) read-node]
    "同じ thread で読み終え、終わった印を立てる(資格の無い所と模擬の k8s の答え手が使う)。"
    (.read-with self read-node)
    (setv self.finished (fn [] True))
    None))


(defclass KubeReadBatches []
  "node の label の読みの受け渡し(答え手が歩をまたいで同じ 1 つを読み書きする)。current = 走っているか、終わって受け取られていない
   読み(1 度に 1 つ)。"
  (defn #^ None __init__ [self]
    (setv #^ (| KubeReadBatch None) self.current None)
    None)

  (defn #^ (| KubeReadBatch None) begin [self #^ (get tuple #(str ...)) nodes #^ int started-ms]
    "読みが無ければ新しい読みを置いて返す。在れば None(読みは 1 度に 1 つ)。"
    (when (is-not self.current None)
      (return None))
    (setv batch (KubeReadBatch nodes started-ms))
    (setv self.current batch)
    batch))


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
    (val result (if (isinstance seen str) (KubeReadFailed :name key :error seen) (KubeBodyRead :name key :body seen)))
    (<- observed (| DeploymentSeen DeploymentUnreadable) (deployment-observation-of result at))
    (:= writes (+ writes #((TableWrite key observed)))))
  writes)


(defk node-labels-observation-of [result at]
  {:pre [(: result (| KubeBodyRead KubeReadFailed)) (: at int)] :post [(: % (| NodeLabelsSeen NodeLabelsUnreadable))]
   :tags {:context "coordinator" :role "protocol" :reads "json"}}
  "読みの node 1 件の答え(metadata.labels)を、観測の表に置く観測にするため。"
  (if (isinstance result KubeReadFailed)
      (NodeLabelsUnreadable :error result.error :at at)
      (do (val opened (thaw-json (freeze-json-text result.body.text)))
          (if (isinstance opened dict)
              (do (<- labels (get Table str) (node-labels-table opened))
                  (NodeLabelsSeen :labels labels :at at))
              (NodeLabelsUnreadable :error "k8s の Node の label が object でない" :at at)))))


(defk kube-reads-done [batch]
  {:pre [(: batch KubeReadBatch)] :post [(: % KubeReadsDone)] :tags {:context "coordinator" :role "protocol"}}
  "終わった読みの答えの列を、観測の表への書き(時刻は読みを始めた時刻)にするため。"
  (var nodes #())
  (for [result batch.node-results]
    (<- node-seen (| NodeLabelsSeen NodeLabelsUnreadable) (node-labels-observation-of result batch.started-ms))
    (:= nodes (+ nodes #((TableWrite result.name node-seen)))))
  (KubeReadsDone :nodes nodes))


(defk collected-reads [batches now-ms name-after-ms held]
  {:pre [(: batches KubeReadBatches) (: now-ms int) (: name-after-ms int) (: held bool)]
   :post [(: % (| KubeReadsIdle KubeReadsRunning KubeReadsDone))] :tags {:context "coordinator" :role "protocol"}}
  "CollectKubeReads の答えを、読みの受け渡しから待たずに出すため(3 つの答え手が同じ 1 点を通る)。held = 終わっていても「まだ」と
   答える(模擬の k8s が答えない時間)。名指す秒を超えた初回だけ overdue を立てる。"
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


(defhandler kube-api [#^ KubeCalls client #^ KubeReadBatches batches #^ DeploymentWatches watches #^ (get Callable #([] None)) wake]
  ;; 引数に残す理由: k8s の client(HTTP の接続と token)・node の label の読みの受け渡し・Deployment の見張りの受け渡しと、受付の箱を
  ;; 起こす関数は composition root が 1 つずつ作って渡す
  (FollowDeployments [keys now-ms]
    ;; 見張りは調停ループの外の thread(k8s の client が持つ)。変化を受け渡したら受付の箱を起こす(頭の註)。
    (.follow watches keys
             (fn [key]
               (.follow client #* (.split key "/" 1)
                        (fn [body] (when (.note watches key body) (wake)))
                        (fn [error] (when (.note watches key error) (wake))))))
    (<- writes tuple (deployment-writes (.take watches) now-ms))
    (resume writes))
  (StartKubeReads [nodes started-ms]
    (val batch (.begin batches nodes started-ms))
    (when (is-not batch None)
      ;; 調停ループの外の thread で読み、読み終えたら受付の箱を起こす(頭の註)。thread は k8s の client(foundation)が持つ。
      (setv batch.finished (.in-background client (fn [] (.read-with batch (fn [node] (.node-labels client node)))) wake)))
    (resume (is-not batch None)))
  (CollectKubeReads [now-ms name-after-ms]
    (<- collected (| KubeReadsIdle KubeReadsRunning KubeReadsDone) (collected-reads batches now-ms name-after-ms False))
    (resume collected))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason #^ KubeReadBatches batches #^ DeploymentWatches watches]
  ;; 引数に残す理由: 資格が無い理由の文と読みと見張りの受け渡しは composition root が起動の時に 1 度だけ決める
  (FollowDeployments [keys now-ms]
    ;; 見張りは始めた時にその理由を 1 度だけ伝える(観測は読めなかった観測になり、Rollout はその理由を名指して Unknown と扱う)。
    (.follow watches keys (fn [key] (.note watches key reason) (fn [] None)))
    (<- writes tuple (deployment-writes (.take watches) now-ms))
    (resume writes))
  (StartKubeReads [nodes started-ms]
    ;; 読みは始めた時に全部その理由で終わる。
    (val batch (.begin batches nodes started-ms))
    (when (is-not batch None)
      (.read-now batch (fn [node] (raise (KubeUnavailable reason)))))
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
   reads = Deployment の見張り(MemoryFollows)に伝えた「ns/名」の列(伝えた順 — 見張りの始めの list と変化の出来事。時間で読みに行く
   数の検・#3868)。
   node の label の読みは本番と同じ受け渡しで受け(batches)、始めた時に読む。stalled-until-ms = この時刻(調停ループの now)までは
   k8s の API が答えない(node の label の読みは「まだ」・Deployment の見張りは答えない理由を伝える — #2807)。"
  (defn #^ None __init__ [self #^ dict deployments #^ (| dict None) [nodes None]]
    (setv self.deployments deployments self.calls [] self.reads #() self.down False
          self.nodes (or nodes {}) self.batches (KubeReadBatches) self.stalled-until-ms None))

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

  (defn #^ (| OpaqueJson str) seen-at [self #^ str key #^ int now-ms]
    "見張りが now-ms に「ns/名」について伝える物: Deployment の object か、読めない理由(止まっている・答えない・無い)。"
    (cond
      self.down "テストの k8s が止まっている"
      (and (is-not self.stalled-until-ms None) (< now-ms self.stalled-until-ms)) "テストの k8s が答えない"
      (not-in key self.deployments) (+ "無い Deployment: " key)
      True (.deployment-object self (get self.deployments key))))

  (defn #^ OpaqueJson node-labels-of [self #^ str node]
    "node の label(本番の k8s の API の Node の metadata.labels と同じ形)を返す。"
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (when (not-in node self.nodes) (raise (KubeUnavailable (+ "無い Node: " node))))
    (OpaqueJson.of (get self.nodes node)))

  (defn #^ None settle [self #^ str key #^ (| int None) [ready None]]
    "Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とする。"
    (setv row (get self.deployments key) n (get row "specReplicas"))
    (.update row {"replicas" n "readyReplicas" (if (is ready None) n ready) "availableReplicas" n "updatedReplicas" n})))


(defclass MemoryFollows []
  "模擬の coordinator の process 1 つの Deployment の見張り(本番の DeploymentWatches に当たる — process が起き直せば作り直し、始めの
   list からやり直す)。テストの k8s(KubeMemory)の今を読み、最後に伝えた物から変わった物を伝える。following = 見張っている「ns/名」・
   delivered = 「ns/名」→ 最後に伝えた物の印(DeploymentWatches.mark)。"
  (defn #^ None __init__ [self]
    (setv self.following #() self.delivered {})
    None)

  (defn #^ tuple changes [self #^ KubeMemory kube #^ int now-ms]
    "見張っている「ns/名」のうち、テストの k8s の今が最後に伝えた物と違う物の #(「ns/名」 本文か理由) の tuple。"
    (tuple (gfor key self.following
                 :setv seen (.seen-at kube key now-ms)
                 :if (!= (DeploymentWatches.mark seen) (.get self.delivered key))
                 #(key seen))))

  (defn #^ tuple follow [self #^ KubeMemory kube #^ (get tuple #(str ...)) keys #^ int now-ms]
    "見張る Deployment を keys に揃え(外した「ns/名」の伝えた印は忘れる)、最後に伝えた物から変わった物を伝える(テストの k8s の reads に
     積む)。答え = 伝えた #(「ns/名」 本文か理由) の tuple。"
    (setv self.following keys
          self.delivered (dfor key keys :if (in key self.delivered) key (get self.delivered key)))
    (setv changed (.changes self kube now-ms))
    (setv self.delivered (| self.delivered (dfor #(key seen) changed key (DeploymentWatches.mark seen))))
    (setv kube.reads (+ kube.reads (tuple (gfor #(key _) changed key))))
    changed)

  (defn #^ (| int None) change-at [self #^ KubeMemory kube #^ int now-ms]
    "見張っている Deployment の伝える物が、now-ms の後に時刻で変わる刻(テストの k8s が答えない区間の終わり)。無ければ None。"
    (if (and self.following (is-not kube.stalled-until-ms None) (< now-ms kube.stalled-until-ms))
        kube.stalled-until-ms
        None)))


(defk follow-wait [kube follows timeout-seconds now]
  {:pre [(: kube KubeMemory) (: follows MemoryFollows) (: timeout-seconds (| float None)) (: now int)] :post [(: % (| float None))]
   :tags {:context "coordinator" :role "protocol"}}
  "模擬の k8s が受付の待ちを縮めた秒を知るため: 見張りが伝えていない変化が在れば 0(本番の見張りは変化の刻に受付を起こす)、答えない
   区間の終わりに見張りの伝える物が変わるか、区間の中で始めた node の label の読みが答えるなら(本番は読み終えた刻に受付の箱を起こす)、
   その刻までの秒と timeout-seconds(None = 期限なし)の短い方、それ以外は timeout-seconds のまま。"
  (val held (and (is-not kube.batches.current None) (is-not kube.stalled-until-ms None) (< now kube.stalled-until-ms)))
  (val ats (tuple (gfor at #((.change-at follows kube now) (if held kube.stalled-until-ms None)) :if (is-not at None) at)))
  (cond
    (.changes follows kube now) 0.0
    (not ats) timeout-seconds
    (is timeout-seconds None) (/ (- (min ats) now) 1000.0)
    True (min timeout-seconds (/ (- (min ats) now) 1000.0))))


(defhandler kube-memory [#^ KubeMemory kube #^ MemoryFollows follows]
  ;; 引数に残す理由: テストの k8s の状態は検が持ち、歩をまたいで同じ 1 つを読み書きする。見張りの状態は coordinator の process の物で、
  ;; 組を作る側(emulated-handlers — coordinator の起き直しごと)が作って渡す
  (FollowDeployments [keys now-ms]
    (<- writes tuple (deployment-writes (.follow follows kube keys now-ms) now-ms))
    (resume writes))
  (NextRequests [timeout-seconds limit]
    ;; 見張りが伝えていない変化が在る間は受付を待たずに取る(本番の見張りが受付の箱を起こすのと同じ刻 — 頭の註)。
    (<- now int (now-epoch-ms))
    (<- wait (| float None) (follow-wait kube follows timeout-seconds now))
    (<- batch list (NextRequests wait limit))
    (resume batch))
  (StartKubeReads [nodes started-ms]
    (val batch (.begin kube.batches nodes started-ms))
    (when (is-not batch None)
      (.read-now batch kube.node-labels-of))
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
