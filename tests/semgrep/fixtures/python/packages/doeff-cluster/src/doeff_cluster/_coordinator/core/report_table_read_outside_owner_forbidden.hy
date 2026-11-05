;;; Sample (a file with deliberate violations): reads a Service's report tables (observed readiness / metrics) directly, outside the owner module.
;;; doeff-cluster-report-tables-are-read-by-their-owner fires on lines 9 and 13, and does not fire on lines 17-19.
(import doeff_cluster [service-readiness])


(defn new-generation-reported-ready [state job want]
  ;; Shape of blind B from the design review (AL-D2o): a copy that stops the swap's give-up with a Ready chosen only from the worker, fingerprint, and assignment generation.
  (setv placed (.get state.placements job.spec.name))
  (any (gfor report (or (.row state.observations.readiness job.spec.name) #())
             (and report.ready (= report.origin.worker placed.worker) (= report.origin.spec-hash want)))))

(defn freshest-metrics [state name]
  (.row state.observations.metrics name))

(defn verdict-through-the-owner [state name now timing]
  ;; Does not fire: calling the owner's judgment, the declaration's readiness field, the k8s Deployment observation table.
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j)))
  #((service-readiness state name now timing) job.readiness
    (.row state.observations.deployments name)))
