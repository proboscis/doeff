;;; service の readiness の判断(宣言の readiness の形の検め・入れ替えの期限)。報告の揃え(reported-readiness)は
;;; doeff_cluster._shared.core.readiness_report。型と定数は
;;; doeff_cluster._shared.intent.readiness_model。宣言の側(service_model.job)と coordinator の側(cluster_policy・handoff_policy・
;;; resource_policy)と報告の handler(readiness_handlers)が同じ規則を使う(定義点はここ 1 つ)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import json)
(import math)
(import re)
(import doeff_cluster._shared.intent.readiness_model [HANDOFF-TIMEOUT-SECONDS READINESS-KEYS])

;; 環境変数の値のうち数として読む綴り(JSON の数の文法 — 空白・下線・NaN・Infinity は数にしない)。
(val ENVIRON-NUMBER (re.compile r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"))


;; text は環境変数の値そのもの。
(defn #^ (| int float None) environ-number [#^ str text]  ; defk にできない: 宣言の組み立て(module の読み込みの時と system-declaration)が呼ぶ純粋な判断
  "環境変数の値 → JSON の数(整数の綴りは int・小数か指数の綴りは float)。数の綴りでない・有限でなければ None。"
  (if (not (re.fullmatch ENVIRON-NUMBER text))
      None
      (do (setv number (json.loads text))
          (if (math.isfinite number) number None))))


;; readiness は宣言の値そのもの・environ は job の environ(名 → 値 — 上書きを重ねた後)。
(defn #^ (| str None) readiness-environ-refusal [#^ object readiness #^ dict environ]  ; defk にできない: 宣言の組み立て(module の読み込みの時と system-declaration)が呼ぶ純粋な判断
  "宣言の readiness のうち :environ の名で書いた欄(値が文字列 — readiness_model の註)を environ で読めるか検め、読めなければ理由の文、
   読めれば None。dict でない readiness は readiness-refusal が断るので見ない。"
  (when (not (isinstance readiness dict))
    (return None))
  (for [#(key value) (.items readiness)]
    (when (isinstance value str)
      (when (not-in value environ)
        (return (.format "readiness の {} が名指す {} は job の :environ に無い(在るのは {})" key value (sorted environ))))
      (when (is (environ-number (get environ value)) None)
        (return (.format "readiness の {} が名指す environ {} の値は JSON の数の綴り: {!r}" key value (get environ value))))))
  None)


;; readiness は宣言の値そのもの(readiness-environ-refusal を通った物)・environ は job の environ(名 → 値 — 上書きを重ねた後)。
(defn #^ object readiness-from-environ [#^ object readiness #^ dict environ]  ; defk にできない: 宣言の組み立て(module の読み込みの時と system-declaration)が呼ぶ純粋な判断
  "宣言の readiness → :environ の名で書いた欄を environ のその名の値の数に置き換えた readiness(coordinator が受ける行の形 — 欄はいつも数)。
   dict でない readiness はそのまま返す(readiness-refusal が断る)。"
  (if (isinstance readiness dict)
      (dfor #(key value) (.items readiness) key (if (isinstance value str) (environ-number (get environ value)) value))
      readiness))


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
