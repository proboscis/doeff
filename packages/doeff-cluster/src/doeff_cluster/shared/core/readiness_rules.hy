;;; service の readiness の判断(宣言の readiness の形の検め・入れ替えの期限)。報告の揃え(reported-readiness)は
;;; doeff_cluster.shared.core.readiness_report。型と定数は
;;; doeff_cluster.shared.intent.readiness_model。宣言の側(service_model.job)と coordinator の側(cluster_policy・handoff_policy・
;;; resource_policy)と報告の handler(readiness_handlers)が同じ規則を使う(定義点はここ 1 つ)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_cluster.shared.intent.readiness_model [HANDOFF-TIMEOUT-SECONDS READINESS-KEYS])


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
    (and (in "retiredSeconds" readiness) (not (positive-number (get readiness "retiredSeconds"))))
      (.format "readiness の retiredSeconds は正の数: {!r}" (get readiness "retiredSeconds"))
    (and (in "retiredSeconds" readiness) (!= update "handoff"))
      (.format "retiredSeconds は update = handoff の Service だけが持つ(いまの update = {!r})" update)
    (and (in "retiredLimit" readiness) (not (positive-integer (get readiness "retiredLimit"))))
      (.format "readiness の retiredLimit は正の整数: {!r}" (get readiness "retiredLimit"))
    (and (in "retiredLimit" readiness) (!= update "handoff"))
      (.format "retiredLimit は update = handoff の Service だけが持つ(いまの update = {!r})" update)
    True None))


;; value は宣言の欄の値そのもの(整数かどうかを確かめる)。
(defn #^ bool positive-integer [#^ object value]  ; defk にできない: 宣言の検め(module の読み込みの時と coordinator の純粋な判断)が呼ぶ
  "JSON の正の整数か(bool は数に数えない)。"
  (and (isinstance value int) (not (isinstance value bool)) (> value 0)))


(defn #^ (| int None) retired-limit-of [#^ (| dict None) readiness]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "宣言の readiness(None か検めを通った dict)→ 退いた process を同時に残す数の上限 R(#4072 の D-3 の改め)。書かなければ None(worker の
   既定 WorkerPolicy.retired-limit を使う)。"
  (if (is readiness None) None (.get readiness "retiredLimit")))


(defn #^ int handoff-timeout-ms [#^ (| dict None) readiness]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "宣言の readiness(None か検めを通った dict)→ 入れ替えの新の世代が Ready になるまで待つ上限(ms)。書かなければ既定。"
  (int (* 1000 (.get (or readiness {}) "handoffTimeoutSeconds" HANDOFF-TIMEOUT-SECONDS))))


(defn #^ (| int None) retired-lifetime-ms [#^ (| dict None) readiness]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "宣言の readiness(None か検めを通った dict)→ 入れ替えで退いた process の寿命の上限(ms — #4072 の D-2)。書かなければ None(既定は
   置かない — None の job は今どおり新の世代が Ready と数えられた時に退いた旧を止める。書いた job は新の Ready で止めず、旧が自分で
   終わるか、退いてからこの長さを越えた時に止める)。"
  (setv seconds (if (is readiness None) None (.get readiness "retiredSeconds")))
  (if (is seconds None) None (int (* 1000 seconds))))
