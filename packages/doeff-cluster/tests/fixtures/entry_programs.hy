;;; 実行先(job_entry・RemoteJob・encode-program)の検の Program の見本(ADR-DOE-CLUSTER-001 R1・R2・R3b)。
;;;
;;; どの Program も自分の handler を本体の with-handlers で並べる(実行先は handler を足さない)。handler の値を捕まえる見本
;;; (ANSWER-BASE を引数に渡す)は詰める時に断られる側。
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler])
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.host_contract [HOST-CONTRACT host-reader])
;; 子の中で job_entry は __main__ として読まれる。業務の module が doeff_cluster.job_entry から文脈の読みを import しても、文脈の型が
;; 1 つのままであることの反例(test_job_context)に使うので、job_entry から import する。
(import doeff_cluster.job_entry [runtime-env-of-context])


(defhandler answer-base
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; module の最上位の handler の値(defhandler の値そのもの — Program の引数にすると詰められない見本)。
  (Ask [key] :when (= key "base") (resume 7)))


(defk based-add [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "自分で reader(base = 100)と scheduler を並べ、base に n を足す。"
  (<- base int (scheduled (with-handlers [(reader {"base" 100})] (Ask "base"))))
  (+ base n))


(defk counter-program [prefix]
  {:pre [(: prefix str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "引数の値(prefix)を Program が運び、base は自分で並べた reader が答える。"
  (<- base int (scheduled (with-handlers [(reader {"base" 1})] (Ask "base"))))
  (.format "{}-{}" prefix base))


(defk boom-program []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "自分で並べた reader の答えを入れた例外を投げる。"
  (<- base int (scheduled (with-handlers [(reader {"base" 100})] (Ask "base"))))
  (raise (ValueError (.format "業務の失敗 base={}" base))))


(defk host-foundation []
  {:pre [] :post [(: % list)] :needs #{"cluster-net"} :tags {:context "doeff-cluster-test" :role "foundation"}}
  "土台: 宿の契約に答える host-reader(handler の値)を並べる。module の最上位の関数なので Program には参照で詰まる。
   host-reader は session の値(Get / Put)を使うので、外側に state を置く。"
  [(state) host-reader])


(defk context-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宿の契約(HOST-CONTRACT)の run-context と Program の path を土台の host-reader で読み、宣言の repo の名と path を返す。
   handler の値 host-reader を本体で直に参照すると、本体(値で詰まる)が handler の値を捕まえて詰められない — 土台の関数を通す。"
  (<- handlers list (foundation))
  (<- pair tuple (scheduled (with-handlers handlers (host-answers))))
  (<- declared (runtime-env-of-context (get pair 0)))
  (.format "{}|{}" (.join "," (lfor r declared.repos r.name)) (get pair 1)))


(defk host-answers []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宿の契約の 2 つの Ask(run-context と Program の path)の答えの組。"
  (<- ctx (Ask HOST-CONTRACT.run-context-key))
  (<- path str (Ask HOST-CONTRACT.program-key))
  #(ctx path))
