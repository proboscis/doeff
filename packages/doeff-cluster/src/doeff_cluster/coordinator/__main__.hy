;;; 旧い入口 `hy -m doeff_cluster.coordinator` を新しい入口へ渡すだけ(配備中の image の boot.sh が旧い名で撃つ間だけ残す —
;;; DOEFF114 に当たる。消すのは agora-redesign #2113・本番の版が揃った後)。
(import doeff_cluster.coordinator.entry.main [main])

(main)
