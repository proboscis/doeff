;;; k8s の Deployment の object → Rollout が見る観測の dict(kube_handlers.deployment-view)。
;;; 4418414 で containers の読みの入れ子を誤り、本番の Deployment を読むたびに coordinator が落ちた
;;; (AttributeError: 'list' object has no attribute 'get')— 本物の形の object で撃つ。
(import doeff_cluster.kube_handlers [deployment-view])


(setv REAL-SHAPED
  {"metadata" {"generation" 7 "annotations" {"a" "b"}}
   "spec" {"replicas" 0
           "template" {"spec" {"containers" [{"name" "app-writer" "image" "zeus:5000/app:20260924-305aac4"}
                                              {"name" "sidecar" "image" "busybox:1"}]}}}
   "status" {"replicas" 0 "readyReplicas" 0 "observedGeneration" 7}})


(defn test-reads-container-images-from-the-pod-template []
  (setv view (deployment-view REAL-SHAPED))
  (assert (= (get view "images")
             {"app-writer" "zeus:5000/app:20260924-305aac4" "sidecar" "busybox:1"}))
  (assert (= (get view "specReplicas") 0)))


(defn test-missing-template-gives-no-images []
  (assert (= (get (deployment-view {"spec" {"replicas" 1}}) "images") {})))
