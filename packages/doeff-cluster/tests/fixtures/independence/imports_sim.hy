;; 反例の fixture: 本番の code が模擬の環境(doeff_cluster.sim)を import する(test_package_independence.hy が拾うことを確かめる)。読み込まない。
(import doeff_cluster.sim.local [sim-cluster])
