;;; ReportMetrics の fake の handler。metrics-memory = list に記録する(テスト)。本物は metrics_handlers.hy の metrics-http。
;;; 本物と分けた訳: fake は coordinator と同じ解きと検めを通すので coordinator の型の閉包(MetricsPayload・checked-metrics・doeff_hy._wire)
;;; を読む。本物の module はどの job の foundation も読むので、fake を同じ module に置くと全部の job がその閉包を
;;; 起動で読んでいた。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(require doeff-hy.macros [defhandler <-])
(import doeff_cluster._shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster._coordinator.core.metrics_policy [checked-metrics :as _checked-metrics])
(import doeff_cluster._coordinator.intent.request_bodies [MetricsPayload :as _MetricsPayload])
(import doeff_hy._wire [parse Malformed])
(import doeff_cluster._coordinator.core.resource_policy [Refused :as _Refused])


;; fake。本物(metrics-http → coordinator)と同じ契約を tests/test_metrics_contract.hy が両方で回す: coordinator と同じ解きと検め
;; (本文の型 MetricsPayload に解いて checked-metrics を通す — #2445)を通った形(欠けた族は空の dict)で記録し、coordinator が断る形は
;; 記録しない — 本物も断りを log に出すだけで答えは None(報告は観測なので業務を止めない)。
(defhandler metrics-memory [#^ list reports]
  (ReportMetrics [metrics]
    (<- payload (parse _MetricsPayload metrics))
    (when (not (isinstance payload Malformed))
      (try
        (.append reports (_checked-metrics payload))
        (except [_Refused]
          None)))
    (resume None)))
