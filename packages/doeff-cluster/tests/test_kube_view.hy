;;; k8s の Deployment の object → Rollout が見る観測の dict(kube_client.deployment-view)。
;;; 4418414 で入れ子の読みを誤り、本番の Deployment を読むたびに coordinator が落ちた
;;; (AttributeError: 'list' object has no attribute 'get')— 本物の形の object で撃つ。
;;; pod template の container の image は読まない(読んでいたのは image の版を追う係だけで、2026-09-28 に消した)。
(require doeff-hy.macros [deftest val])
(import doeff_cluster.foundation.kube_client [deployment-view])


(val REAL-SHAPED
  {"metadata" {"generation" 7 "annotations" {"a" "b"}}
   "spec" {"replicas" 0
           "template" {"spec" {"containers" [{"name" "app-writer" "image" "zeus:5000/app:20260924-305aac4"}
                                              {"name" "sidecar" "image" "busybox:1"}]}}}
   "status" {"replicas" 0 "readyReplicas" 0 "observedGeneration" 7}})


(deftest test-reads-the-rollout-fields-from-a-real-shaped-deployment
  (val view (deployment-view REAL-SHAPED))
  (assert (= view {"specReplicas" 0 "replicas" 0 "readyReplicas" 0 "availableReplicas" 0 "updatedReplicas" 0
                   "generation" 7 "observedGeneration" 7 "annotations" {"a" "b"}})
          view))


(deftest test-a-deployment-without-status-reads-as-zero-replicas
  (val view (deployment-view {"spec" {"replicas" 1}}))
  (assert (= (get view "specReplicas") 1) view)
  (assert (= (get view "readyReplicas") 0) view)
  (assert (= (get view "annotations") {}) view))
