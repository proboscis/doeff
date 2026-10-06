;; 計器の橋(shared/protocol/meter_report.hy — #2740)の検。
;;   綴り   計器の断面 → ReportMetrics の報告の形(秒の観測の total → sum・counter と gauge はそのまま)。
;;   sim    sim-cluster(本物の coordinator と worker)の上で、記録の client で書く service が、記録の service に届かない窓の間に撃った書き
;;          3 件を client の計器で数え、橋がその累計を coordinator へ送り、coordinator の GET /metrics に service の label つきで出る。
;;          橋を壊す(断面を送らない)と同じ筋書きで行が出ない — 1 本目の検はこの形を赤にする。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_events [MemoryBroker])
(import doeff_time [Delay])
(import doeff_core_effects.meter_effects [MeterSnapshot SecondsTotal])
(import doeff_hy.frozen [FrozenMap])
(import doeff_cluster.shared.intent.protocol [PlainText])
(import doeff_cluster.sim.local [sim-cluster ReadCoordinator])
(import doeff_cluster.shared.protocol.meter_report [report-metrics-of])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.meter_programs [records-writers silent-records-writers])

;; coordinator の GET /metrics の、service writer の「届かなかった書き」の counter の行の頭(counter は名に _total が付く)。
(val UNREACHABLE-WRITES-LINE "records_client_requests_write_unreachable_total{service=\"writer\"")


(defk unreachable-writes-seen [seconds]
  {:pre [(: seconds float)] :post [(: % (| float None))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: seconds 秒待ってから coordinator の GET /metrics を読み、service writer の「届かなかった書き」の値を返すため(行が無ければ None)。"
  (<- (Delay seconds))
  (<- metrics PlainText (ReadCoordinator "/metrics"))
  (val lines (lfor line (.splitlines metrics.text) :if (.startswith line UNREACHABLE-WRITES-LINE) line))
  (if lines (float (get (.split (get lines 0)) -1)) None))


(deftest test-report-metrics-of-spells-the-snapshot-in-the-report-form
  (<- metrics dict (report-metrics-of (MeterSnapshot :counters (FrozenMap {"writes" 3.0}) :gauges (FrozenMap {"depth" 2.0})
                                                     :durations (FrozenMap {"put" (SecondsTotal :total 1.5 :count 3)}))))
  (assert (= metrics {"counters" {"writes" 3.0} "gauges" {"depth" 2.0} "durations" {"put" {"sum" 1.5 "count" 3}}}) metrics))


(deftest test-unreachable-writes-reach-the-coordinator-metrics
  ;; 届かない窓(10 秒)の間に 1 秒ごとの書き 3 件 → client の計器の write_unreachable が 3 → 橋が 30 秒ごとに送る → 45 秒後の
  ;; GET /metrics に 3(記録の service の計器には出ない数)。
  (<- seen (sim-cluster :notice-broker (MemoryBroker) (records-writers sim-foundation) (unreachable-writes-seen 45.0)))
  (assert (= seen 3.0) seen))


(deftest test-a-bridge-that-does-not-report-leaves-no-metric
  ;; 失敗ケース: 橋を壊す(断面を送らない)と、同じ筋書きで GET /metrics に行が無い — 上の検はこの形を赤にする。
  (<- seen (sim-cluster :notice-broker (MemoryBroker) (silent-records-writers sim-foundation) (unreachable-writes-seen 45.0)))
  (assert (is seen None) seen))
