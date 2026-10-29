;; 見張りの受け渡し(kube-api・kube-unavailable の session の箱と、今見張っている相手 — coordinator/protocol/kube.hy)。
;;
;; 見張りの thread は変化を callback(watch-reporter)で渡し、調停ループは FollowDeployments・FollowNodes のたびに待たずに取る。偽の client が
;; 渡された callback を覚え、検が thread の代わりにそれを呼ぶ:
;;   - 同じ物を続けて受け渡さない(受付の箱を起こすのは変わった時だけ)・
;;   - 見張りから外した相手は止め(stop)、外した後に届いた物は取らない・外した相手を見張り直せば、同じ物も初めから受け渡す・
;;   - 資格の無い coordinator(kube-unavailable)は、見張りを始めた時にその理由を 1 度だけ伝える。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import types [SimpleNamespace])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_hy._json_value [OpaqueJson])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentSeen DeploymentUnreadable NodeLabelsSeen NodeLabelsUnreadable])
(import doeff_cluster.coordinator.intent.kube_model [FollowDeployments FollowNodes ScaleDeployment KubeUnavailable])
(import doeff_cluster.coordinator.protocol.kube [kube-api kube-unavailable])

(val DEPLOYMENT {"metadata" {"generation" 3 "annotations" {}} "spec" {"replicas" 2} "status" {"replicas" 2 "readyReplicas" 1 "observedGeneration" 3}})
(val SCALED-DOWN {"metadata" {"generation" 4 "annotations" {}} "spec" {"replicas" 0} "status" {"replicas" 0 "observedGeneration" 4}})
(val NODE {"metadata" {"name" "n1" "labels" {"zone" "a"}}})
(val KEY "prod/app-writer")


(defk fake-kube []
  {:pre [] :post [(: % SimpleNamespace)] :tags {:context "doeff-cluster-test" :role "program"}}
  "偽の k8s の client を作るため: 見張りを始める呼びを覚え(started = 始めた相手のキー・reports = キーと渡された callback の組)、止める関数が
   呼ばれたキーを覚える(stopped)。woke = 受付の箱を起こす関数が呼ばれた回数の列。"
  (val started [])
  (val stopped [])
  (val reports [])
  (val woke [])
  (SimpleNamespace
    :started started :stopped stopped :reports reports :woke woke
    :follow (fn [namespace name on-body on-error]
              (.append started (+ namespace "/" name))
              (.append reports #((+ namespace "/" name) on-body))
              (fn [] (.append stopped (+ namespace "/" name))))
    :follow-node (fn [name on-body on-error]
                   (.append started name)
                   (.append reports #(name on-body))
                   (fn [] (.append stopped name)))))


(defk reported [kube key seen]
  {:pre [(: kube SimpleNamespace) (: key str) (: seen (| OpaqueJson str))] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りの thread の代わりに、key の見張りが最後に受け取った callback を呼ぶため(seen = 渡す object か、読めない理由の文)。"
  (val callback (next (gfor #(k cb) (reversed kube.reports) :if (= k key) cb)))
  (callback seen)
  None)


(defk api-handlers [kube]
  {:pre [(: kube SimpleNamespace)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "kube-api を、偽の client と、起こした回数を数える wake で並べるため(session の置き場の state が外側)。"
  [(state) (kube-api kube (fn [] (.append kube.woke 1)))])


(defk change-reaches-the-loop-once [kube]
  {:pre [(: kube SimpleNamespace)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Deployment 1 つを見張り、同じ object・同じ読めない理由を続けて受けても 1 度ずつしか受け渡さないことを読むため。答え = 各歩の書きの列。"
  (<- start tuple (FollowDeployments :keys #(KEY) :now-ms 1000))
  (<- (reported kube KEY (OpaqueJson.of DEPLOYMENT)))
  (<- first tuple (FollowDeployments :keys #(KEY) :now-ms 2000))
  (<- (reported kube KEY (OpaqueJson.of DEPLOYMENT)))
  (<- again tuple (FollowDeployments :keys #(KEY) :now-ms 3000))
  (<- (reported kube KEY (OpaqueJson.of SCALED-DOWN)))
  (<- changed tuple (FollowDeployments :keys #(KEY) :now-ms 4000))
  (<- (reported kube KEY "k8s に届かない"))
  (<- (reported kube KEY "k8s に届かない"))
  (<- refused tuple (FollowDeployments :keys #(KEY) :now-ms 5000))
  #(start first again changed refused))


(deftest test-a-deployment-is-handed-over-only-when-it-changes
  (<- kube (fake-kube))
  (<- steps tuple (with-handlers (! (api-handlers kube)) (change-reaches-the-loop-once kube)))
  (val start (get steps 0))
  (val first (get steps 1))
  (val again (get steps 2))
  (val changed (get steps 3))
  (val refused (get steps 4))
  (assert (= start #()) start)
  (assert (= (len first) 1) first)
  (assert (= (. (get first 0) key) KEY) first)
  (assert (isinstance (. (get first 0) value) DeploymentSeen) first)
  (assert (= (. (get first 0) value reading spec-replicas) 2) first)
  (assert (= (. (get first 0) value at) 2000) first)
  ;; 同じ object を続けて受けても、受け渡さない(受付の箱も起こさない)。
  (assert (= again #()) again)
  (assert (= (. (get changed 0) value reading spec-replicas) 0) changed)
  (assert (isinstance (. (get refused 0) value) DeploymentUnreadable) refused)
  (assert (= (. (get refused 0) value error) "k8s に届かない") refused)
  ;; 受付の箱を起こしたのは変わった 3 回(最初の object・台数 0 の object・最初の読めない理由)だけ。
  (assert (= (len kube.woke) 3) kube.woke)
  (assert (= kube.started [KEY]) kube.started))


(defk drop-and-follow-again [kube]
  {:pre [(: kube SimpleNamespace)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "見張りから外した相手の止めと、外した後に届いた物の扱いと、見張り直した相手の初めからの受け渡しを読むため。"
  (<- (FollowDeployments :keys #(KEY) :now-ms 1000))
  (<- (reported kube KEY (OpaqueJson.of DEPLOYMENT)))
  (<- first tuple (FollowDeployments :keys #(KEY) :now-ms 2000))
  (<- dropped tuple (FollowDeployments :keys #() :now-ms 3000))
  ;; 外した後に、止める前から走っていた thread の callback が届いても、取らない。
  (<- (reported kube KEY (OpaqueJson.of SCALED-DOWN)))
  (<- late tuple (FollowDeployments :keys #() :now-ms 4000))
  (<- (FollowDeployments :keys #(KEY) :now-ms 5000))
  (<- (reported kube KEY (OpaqueJson.of DEPLOYMENT)))
  (<- restarted tuple (FollowDeployments :keys #(KEY) :now-ms 6000))
  #(first dropped late restarted))


(deftest test-a-dropped-watch-is-stopped-and-a-restarted-watch-hands-over-from-the-start
  (<- kube (fake-kube))
  (<- steps tuple (with-handlers (! (api-handlers kube)) (drop-and-follow-again kube)))
  (val first (get steps 0))
  (val dropped (get steps 1))
  (val late (get steps 2))
  (val restarted (get steps 3))
  (assert (= (len first) 1) first)
  (assert (= kube.stopped [KEY]) kube.stopped)
  (assert (= dropped #()) dropped)
  (assert (= late #()) late)
  ;; 見張り直した相手は、前と同じ object も初めから受け渡す(前に受け渡した印は外した時に捨てている)。
  (assert (= kube.started [KEY KEY]) kube.started)
  (assert (= (len restarted) 1) restarted)
  (assert (= (. (get restarted 0) value reading spec-replicas) 2) restarted))


(defk nodes-and-deployments-are-watched-apart [kube]
  {:pre [(: kube SimpleNamespace)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Node の見張りは Deployment の見張りと別の箱を持つことを読むため(一方の物がもう一方の取りに混ざらない)。"
  (<- (FollowDeployments :keys #(KEY) :now-ms 1000))
  (<- (FollowNodes :names #("n1") :now-ms 1000))
  (<- (reported kube "n1" (OpaqueJson.of NODE)))
  (<- deployments tuple (FollowDeployments :keys #(KEY) :now-ms 2000))
  (<- nodes tuple (FollowNodes :names #("n1") :now-ms 2000))
  #(deployments nodes))


(deftest test-a-node-object-is-handed-over-in-the-node-watch-only
  (<- kube (fake-kube))
  (<- steps tuple (with-handlers (! (api-handlers kube)) (nodes-and-deployments-are-watched-apart kube)))
  (val deployments (get steps 0))
  (val nodes (get steps 1))
  (assert (= deployments #()) deployments)
  (assert (= (len nodes) 1) nodes)
  (assert (= (. (get nodes 0) key) "n1") nodes)
  (assert (isinstance (. (get nodes 0) value) NodeLabelsSeen) nodes))


(defk unavailable-says-why-once [names]
  {:pre [(: names tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "資格の無い coordinator が、見張りを始めた時にその理由を 1 度だけ伝え、台数を変える書きは断ることを読むため。"
  (<- first tuple (FollowDeployments :keys #(KEY) :now-ms 1000))
  (<- again tuple (FollowDeployments :keys #(KEY) :now-ms 2000))
  (<- node-first tuple (FollowNodes :names names :now-ms 1000))
  (<- node-again tuple (FollowNodes :names names :now-ms 2000))
  #(first again node-first node-again))


(deftest test-the-coordinator-without-credentials-says-why-once
  (<- steps tuple (with-handlers [(state) (kube-unavailable "資格が無い")] (unavailable-says-why-once #("n1"))))
  (val first (get steps 0))
  (val again (get steps 1))
  (val node-first (get steps 2))
  (val node-again (get steps 3))
  (assert (= (len first) 1) first)
  (assert (isinstance (. (get first 0) value) DeploymentUnreadable) first)
  (assert (= (. (get first 0) value error) "資格が無い") first)
  (assert (= again #()) again)
  (assert (isinstance (. (get node-first 0) value) NodeLabelsUnreadable) node-first)
  (assert (= node-again #()) node-again))


(deftest test-the-coordinator-without-credentials-refuses-to-scale
  (val refusal
    (try
      (<- (with-handlers [(state) (kube-unavailable "資格が無い")] (ScaleDeployment :namespace "prod" :name "app-writer" :replicas 0 :dry-run False)))
      None
      (except [error KubeUnavailable] (str error))))
  (assert (= refusal "資格が無い") refusal))
