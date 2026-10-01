;;; service の readiness の判断(宣言の readiness の形の検め・入れ替えの期限・報告の揃え)。型と定数は
;;; doeff_cluster.shared.intent.readiness_model。宣言の側(service_model.job)と coordinator の側(cluster_policy・handoff_policy・
;;; resource_policy)と報告の handler(readiness_handlers)が同じ規則を使う(定義点はここ 1 つ)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.readiness_model [ROLE-ACTIVE ROLE-STANDBY HANDOFF-TIMEOUT-SECONDS READINESS-KEYS
                                                     REASON-KEPT-CHARS JsonField])


;; value は宣言の欄の値そのもの(数かどうかを確かめる)。
(defn #^ bool positive-number [#^ object value]  ; defk にできない: 宣言の検め(module の読み込みの時と coordinator の純粋な判断)が呼ぶ
  "JSON の正の数か(bool は数に数えない)。"
  (and (isinstance value #(int float)) (not (isinstance value bool)) (> value 0)))


;; readiness は宣言の値そのもの(None か dict のはず — 違えば理由を返す)。
(defn #^ (| str None) readiness-refusal [#^ object readiness #^ str update]  ; defk にできない: 宣言の検め(module の読み込みの時と coordinator の純粋な判断)が呼ぶ
  "宣言の readiness(None か dict)と入れ替えの形 update → 読めなければ理由の文、読めれば None。service の宣言(service_model.service)と
   coordinator の宣言の読み(cluster_policy.job-from-json)の 2 つの入口が同じ規則で検める(定義点はここ 1 つ)。"
  (cond
    (is readiness None) None
    (not (isinstance readiness dict)) (.format "readiness は dict: {!r}" readiness)
    (not (positive-number (.get readiness "windowSeconds")))
      (.format "readiness は windowSeconds(正の数)を持つ dict: {!r}" readiness)
    (any (gfor k readiness (not-in k READINESS-KEYS)))
      (.format "readiness の知らない欄: {}(書ける欄 = {})" (sorted (gfor k readiness :if (not-in k READINESS-KEYS) (str k)))
               (list READINESS-KEYS))
    (and (in "handoffTimeoutSeconds" readiness) (not (positive-number (get readiness "handoffTimeoutSeconds"))))
      (.format "readiness の handoffTimeoutSeconds は正の数: {!r}" (get readiness "handoffTimeoutSeconds"))
    (and (in "handoffTimeoutSeconds" readiness) (!= update "handoff"))
      (.format "handoffTimeoutSeconds は update = handoff の Service だけが持つ(いまの update = {!r})" update)
    True None))


(defn #^ int handoff-timeout-ms [#^ (| dict None) readiness]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "宣言の readiness(None か検めを通った dict)→ 入れ替えの新の世代が Ready になるまで待つ上限(ms)。書かなければ既定。"
  (int (* 1000 (.get (or readiness {}) "handoffTimeoutSeconds" HANDOFF-TIMEOUT-SECONDS))))


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
