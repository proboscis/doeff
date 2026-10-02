;;; 報告の readiness(ready・reason・role)を coordinator が残す形に揃える純粋な判断 — coordinator(record-readiness)と fake
;;; (readiness-memory)が同じ形で残す。型・定数(ROLE-ACTIVE・ROLE-STANDBY・REASON-KEPT-CHARS・JsonField)は
;;; doeff_cluster.shared.intent.readiness_model。intent の層から移した(判断は core — DOEFF105)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.readiness_model [ROLE-ACTIVE ROLE-STANDBY REASON-KEPT-CHARS JsonField])


(defk reported-readiness [ready reason role]
  ;; 3 つの引数は報告の本文の素の値(旧い版の process は欄を欠き・型も揃わない)— それを読む形に揃えるのがこの関数の役目。
  {:pre [(: ready JsonField) (: reason JsonField) (: role JsonField)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "報告の ready・reason・role を coordinator が残す形 {ready reason role} に揃えるため: ready は真偽・reason は文字列の先頭
   REASON-KEPT-CHARS 字・role は standby 以外(旧い報告の欠けた欄を含む)を active と読む。coordinator(record-readiness)と
   fake(readiness-memory)が同じ形で残す(定義点はここ 1 つ — 契約テスト tests/test_readiness_contract.hy)。"
  {"ready" (bool ready)
   "reason" (cut (str reason) 0 REASON-KEPT-CHARS)
   "role" (if (= role ROLE-STANDBY) ROLE-STANDBY ROLE-ACTIVE)})
