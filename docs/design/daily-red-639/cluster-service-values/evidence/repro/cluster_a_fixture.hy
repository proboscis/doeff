;;; 盲検 A の反例の再現に使う service 2 本(設計者が書き直した物 — 盲検の file は使わない)。
;;;   ledger = 宣言の :config に組み立て側の欄 record を書く。本体は record を知らない。
;;;   tally  = 本体が業務の引数に record という名を使う。
(require doeff-hy.macros [defk])
(import doeff_cluster.service_model [service System])

(defn plain-env [config ctx]
  "handler を 1 つも足さない env(外の I/O をしない)。"
  [])

(defk ledger-program [n]
  {:pre [(: n int)] :post [(: % int)]}
  (+ n 1))

(defk tally-program [record n]
  {:pre [(: record bool) (: n int)] :post [(: % int)]}
  (if record (+ n 100) n))

(setv RECORD {"otlp" "http://127.0.0.1:9" "chunkSeconds" 3600 "flushSeconds" 2.0})
