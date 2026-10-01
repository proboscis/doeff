;;; coordinator の Persist の effect に答える handler。wal-store = 耐久の置き場(foundation/wal_store の WalStore・memory の
;;; MemoryWalStore — 層 protocol は foundation を読めないので、置き場は下の DurableStore の形で受け、組み立ては entry が持つ)・
;;; memory-store = まとまりごとの delta を list に積むテストの置き場。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(require doeff-hy.macros [defhandler])
(import typing [Protocol])
(import doeff_cluster.coordinator.intent.cluster_model [Persist])


(defclass DurableStore [Protocol]
  "wal-store が書く置き場の形(foundation/wal_store の WalStore と entry の MemoryWalStore がこの形を持つ)。"
  (defn #^ None persist [self #^ dict delta] (raise NotImplementedError)))


(defhandler wal-store [#^ DurableStore store]
  ;; 引数に残す理由: 置き場(開いた log の file と seq)は composition root が起動の時に 1 つ作って渡す
  (Persist [delta]
    (.persist store delta)
    (resume None)))


(defhandler memory-store [#^ list log]
  ;; テスト: まとまりごとの delta を list に積む(耐久の置き場の代わり)。
  ;; 引数に残す理由: 積んだ delta の列は検が持って読む
  (Persist [delta]
    (.append log delta)
    (resume None)))
