;;; 検が coordinator へ書く Program の job の宣言の行と、task の本文の見本(ADR-DOE-CLUSTER-001 改訂 1 の A・F・G・R3b)。
;;;
;;; 行の run = {"kind" "service" "program" <sha256> "identity" {...} "versions" {...} "describe" "..."}。coordinator は Program を解かない
;;; ので、制御面の検(置き場所・入れ替え・drain・資源の口)は Program の中身を要らない — 形の揃った行だけを使う。
;;; task の本文も service の宣言と同じく詰めた Program の置き場のキー program(sha)だけを運ぶ。coordinator は置き場に sha が在る時だけ
;;; task を受けるので、task の検は先に program-placed で置いてから送る(置き場の版が task の版になる)。
(require doeff-hy.macros [defk val])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.coordinator.core.program_policy [program-write])
(import doeff_cluster.coordinator.intent.request_bodies [ProgramBody])
(import doeff_cluster.shared.intent.remote_model [program-sha])

;; 置き場のキーの見本(64 桁の sha256 の形 — coordinator は /programs に在るかを Service の行の受け付けでは確かめない)。
(val SAMPLE-PROGRAM (* "a" 64))

(val SAMPLE-RUN {"kind" "service"
                 "program" SAMPLE-PROGRAM
                 "identity" {"function" "m:f" "args" [] "kwargs" {}}
                 "versions" {}
                 "describe" "m:f()"})

;; task の見本の詰めた Program(中身は解かない — coordinator と worker の制御面の検は sha と版だけを見る)。
(val SAMPLE-BLOB "c2FtcGxlLXRhc2stcHJvZ3JhbQ==")
(val SAMPLE-TASK-PROGRAM (program-sha SAMPLE-BLOB))


(defk program-run [function #* args]
  {:pre [(: function str) (: args tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "関数の参照 function(module:qualname)と位置の引数 args(JSON の値)の Program の job の run。identity を変えると spec-hash が変わる。"
  {"kind" "service"
   "program" SAMPLE-PROGRAM
   "identity" {"function" function "args" (list args) "kwargs" {}}
   "versions" {}
   "describe" (.format "{}({})" function (.join ", " (gfor a args (repr a))))})


(defk program-placed [state versions [blob SAMPLE-BLOB] [now 0]]
  {:pre [(: state ClusterState) (: versions dict) (: blob str) (: now int)] :post [(: % tuple) (= (len %) 2)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "task を送る前に、詰めた Program を置き場へ本物の口(program_policy.program-write)で置いた状態と、その置き場のキー #(状態 sha)。
   versions = 詰めた送り手の版(task の版になる — 置く worker の版と比べられる)。版の違う task を並べる検は blob を変える。
   now = 置いた時刻(参照の無い Program は置いてから 10 分で掃除される — 検の時計に合わせる)。"
  (val sha (program-sha blob))
  (val placed (program-write state sha (ProgramBody :blob blob :versions versions) now))
  (assert (= (get placed 1) 200) placed)
  #((get placed 0) sha))
