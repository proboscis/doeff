;;; 本番の土台が並べる coordinator に話す handler の組(cluster_foundation.cluster-handlers)の検。
(require doeff-hy.macros [deftest <- val])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.host_contract [HOST-CONTRACT])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.cluster_foundation [cluster-handlers lease-holder-of])
(import doeff_cluster.foundation_check [foundation-closure closed?])
(import tests.fixtures.cluster_foundation_programs [beacon-job production-foundation])


(deftest test-the-cluster-handlers-are-made-from-the-run-context
  (val ctx (RunContext "http://coordinator:8080" "w1" "abc" "beacon" :instance "w1-p3"))
  (<- handlers list (with-handlers [(reader {HOST-CONTRACT.run-context-key ctx})] (cluster-handlers)))
  (assert (= (len handlers) 7) handlers)
  (<- holder str (lease-holder-of ctx))
  (assert (= holder "beacon/w1-p3")))


(deftest test-a-service-under-the-production-foundation-with-the-cluster-handlers-is-closed
  (val closure (foundation-closure beacon-job :foundation production-foundation))
  (assert (closed? closure) closure))
