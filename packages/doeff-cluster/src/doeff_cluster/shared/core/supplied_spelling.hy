;;; job の宣言の :environ の値のうち、cluster が持つ ConfigMap・Secret の参照の綴りを見分ける純粋な判断(ADR-DOE-CLUSTER-004)。
;;;
;;;   REF-SPELLING         値 1 つの参照の綴り「<種類>:<namespace>/<名>/<キー>」
;;;   OBJECT-SPELLING      物 1 つ丸ごとの参照の綴り「<種類>:<namespace>/<名>」
;;;   supplied-spellings   宣言の :environ(名の順の #(名 値) の tuple)の値のうち、参照の綴りの物を並べる
;;;
;;; 値を読む部品(shared/protocol/supplied_values)と、worker に置かれうる job の参照を heartbeat の返事で渡す coordinator の判断
;;; (coordinator/core/cluster_policy の supplied-for)が同じこの綴りを読む(綴りの定義元はここ 1 か所)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import re)

;; 参照の綴り: 種類・namespace・名は k8s の名の字(小文字の英数字・-・.)・キーは data のキーの字(英数字・-・_・.)。
(val REF-SPELLING (re.compile r"^(configmap|secret):([a-z0-9][a-z0-9.-]*)/([a-z0-9][a-z0-9.-]*)/([A-Za-z0-9._-]+)$"))
(val OBJECT-SPELLING (re.compile r"^(configmap|secret):([a-z0-9][a-z0-9.-]*)/([a-z0-9][a-z0-9.-]*)$"))


(defk supplied-spellings [environ]
  {:pre [(: environ tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "judgment"}}
  "宣言の :environ environ(#(名 値) の tuple)の値のうち、値 1 つか物 1 つ丸ごとの参照の綴りの物を、綴りの順に重ねずに並べるため
   (参照でない値 — URL・path・数 — は並べない)。"
  (tuple (sorted (sfor #(_ value) environ
                       :if (and (isinstance value str)
                                (or (is-not (.match REF-SPELLING value) None) (is-not (.match OBJECT-SPELLING value) None)))
                       value))))
