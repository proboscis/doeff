;;; 検体(違反をわざと書いた file): Service の報告の表(観測の readiness・metrics)を、持ち主の module の外で直に読む。
;;; doeff-cluster-report-tables-are-read-by-their-owner が 9 行目と 13 行目で発火し、17〜19 行目では発火しない。
(import doeff_cluster [service-readiness])


(defn new-generation-reported-ready [state job want]
  ;; 設計検証(AL-D2o)の盲検 B の形: 入れ替えの諦めを、worker・指紋・割り当ての世代だけで選んだ Ready で止める写し。
  (setv placed (.get state.placements job.spec.name))
  (any (gfor report (or (.row state.observations.readiness job.spec.name) #())
             (and report.ready (= report.origin.worker placed.worker) (= report.origin.spec-hash want)))))

(defn freshest-metrics [state name]
  (.row state.observations.metrics name))

(defn verdict-through-the-owner [state name now timing]
  ;; 発火しない: 持ち主の判断を呼ぶ・宣言の readiness の欄・k8s の Deployment の観測の表。
  (setv job (next (gfor j state.jobs :if (= j.spec.name name) j)))
  #((service-readiness state name now timing) job.readiness
    (.row state.observations.deployments name)))
