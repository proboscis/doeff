;; 続けて落ちた回数の計器 doeff_worker_service_failures{service}(2026-11-26): 置き先の担い手が heartbeat で報告した続けて落ちた回数
;; (Service の status.failures と同じ値)を GET /metrics に出す。worker の子 process の job は落ちても Pod の再起動にならず、
;; kube_pod_container_status_restarts_total では見えない — 上に載る系の見張りの警報がこの値を読む(式と閾は見張りの設定が持つ)。
(require doeff-hy.macros [deftest defk val <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import tests.room_rows [ROOM-JSONS])
(import doeff_cluster._coordinator.core.metrics_policy [metrics-text])
(import tests.test_drain [Coord service K3S T])


(defn #^ dict failure-lines [#^ Coord c]  ; cannot be a defk: 模擬の世界の状態を読む
  "GET /metrics の本文のうち doeff_worker_service_failures の行(sample の綴り → 値)。"
  (dfor line (.splitlines (metrics-text c.state c.now T))
        :if (.startswith line "doeff_worker_service_failures{")
        :setv #(sample value) (.rsplit line " " 1)
        sample (float value)))


(defn #^ dict beat-with [#^ Coord c #^ str worker #^ str name #^ dict extra]  ; cannot be a defk: 模擬の世界の method を呼ぶ(状態を進める)
  "worker が name を動かしていると、行の欄 extra を足して報告する heartbeat。"
  (c.call "POST" "/heartbeat" {"name" worker "provides" K3S "measured" (get ROOM-JSONS 10) "versions" {}
                               "boot" (get c.boots worker) "statuses" [(| (c.row worker name) extra)] "stopping" False}
          :actor None))


(defk placed-writer []
  {:pre [] :post [(: % Coord)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Service w が node-a に置かれ、担い手が回数を報告しない行で動いている。"
  (val c (Coord #("node-a")))
  (c.call "POST" "/resources/Service" {"name" "w" "spec" (! (service))} :expect 201)
  (c.beat "node-a" ["w"])
  c)


(deftest test-the-failures-the-carrier-reports-are-a-gauge-per-service
  ;; 落ち続ける job(backoff・6 回)は 6、安定して動き直すと担い手が 0 を報告して 0 に戻る。回数を報告しない行は出さない(0 と読ませない)。
  (<- c (placed-writer))
  (assert (= (failure-lines c) {}))
  (beat-with c "node-a" "w" {"phase" "backoff" "failures" 6 "lastExitCode" 1})
  (assert (= (failure-lines c) {"doeff_worker_service_failures{service=\"w\"}" 6.0}))
  (beat-with c "node-a" "w" {"failures" 0})
  (assert (= (failure-lines c) {"doeff_worker_service_failures{service=\"w\"}" 0.0})))
