;;; 検が coordinator へ書く Program の job の宣言の行の見本(ADR-DOE-CLUSTER-001 改訂 1 の A・F・G)。
;;;
;;; 行の run = {"kind" "service" "program" <sha256> "identity" {...} "versions" {...} "describe" "..."}。coordinator は Program を解かない
;;; ので、制御面の検(置き場所・入れ替え・drain・資源の口)は Program の中身を要らない — 形の揃った行だけを使う。
(require doeff-hy.macros [defk val])

;; 置き場のキーの見本(64 桁の sha256 の形 — coordinator は /programs に在るかを行の受け付けでは確かめない)。
(val SAMPLE-PROGRAM (* "a" 64))

(val SAMPLE-RUN {"kind" "service"
                 "program" SAMPLE-PROGRAM
                 "identity" {"function" "m:f" "args" [] "kwargs" {}}
                 "versions" {}
                 "describe" "m:f()"})


(defk program-run [function #* args]
  {:pre [(: function str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "関数の参照 function(module:qualname)と位置の引数 args(JSON の値)の Program の job の run。identity を変えると spec-hash が変わる。"
  {"kind" "service"
   "program" SAMPLE-PROGRAM
   "identity" {"function" function "args" (list args) "kwargs" {}}
   "versions" {}
   "describe" (.format "{}({})" function (.join ", " (gfor a args (repr a))))})
