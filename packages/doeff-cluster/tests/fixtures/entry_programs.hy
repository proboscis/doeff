;;; 実行先(job_entry・RemoteJob・encode-program)の検の Program の見本(ADR-DOE-CLUSTER-001 R1・R2・R3b)。
;;;
;;; どの Program も自分の handler を並べる(実行先は handler を足さない)。scheduler は土台(tests.fixtures.envs の scheduler-foundation・
;;; 本体を包む module の最上位の関数 — 計画 10.1)が並べ、Program は自分で scheduled を包まない。handler の値を捕まえる見本
;;; (ANSWER-BASE を引数に渡す)は詰める時に断られる側。
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [defhandler])
(import collections.abc [Callable])
(import doeff [with-handlers DoExpr EffectBase Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader state env-var-ask])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.host_contract [HOST-CONTRACT host-reader environ-reader])
(import tests.fixtures.envs [scheduler-foundation])
;; 子の中で job_entry は __main__ として読まれる。業務の module が doeff_cluster.job_entry から文脈の読みを import しても、文脈の型が
;; 1 つのままであることの反例(test_job_context)に使うので、job_entry から import する。
(import doeff_cluster.job_entry [runtime-env-of-context])


(defhandler answer-base
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; module の最上位の handler の値(defhandler の値そのもの — Program の引数にすると詰められない見本)。
  (Ask [key] :when (= key "base") (resume 7)))


(defk based-add [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "自分で reader(base = 100)を並べ、土台(scheduler)の下で base に n を足す。"
  (<- base int (scheduler-foundation (with-handlers [(reader {"base" 100})] (Ask "base"))))
  (+ base n))


(defk environ-read [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "task の :environ を本番の土台と同じ形で読む見本: 名の Ask に、子の環境変数を字面どおり読む (environ-reader)(環境に無い名は外へ通す)を
   並べて答える。本番の worker の子では環境変数が答え、sim の子では (environ-reader) が外へ通した Ask に、sim の宿が同じ読みの定義
   (host_contract.environ-reader)で spec.environ から答える。"
  (<- value str (scheduler-foundation (with-handlers [(environ-reader)] (Ask name))))
  value)


(defk environ-resolved-read [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "反例の見本: 同じ名を env-var-ask(接頭辞なし — { で始まり } で終わる値を {module.path} の import として解く)で読む。宣言の :environ に
   JSON の object を置くと、本番の子ではこの読みが import に失敗する(sim の子では環境に無い名を外へ通すので sim の宿が字面どおり返し、
   食い違いが見えない)。"
  (<- value str (scheduler-foundation (with-handlers [(env-var-ask :prefix "")] (Ask name))))
  value)


(defk counter-program [prefix]
  {:pre [(: prefix str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "引数の値(prefix)を Program が運び、base は自分で並べた reader が答える。"
  (<- base int (scheduler-foundation (with-handlers [(reader {"base" 1})] (Ask "base"))))
  (.format "{}-{}" prefix base))


(defk boom-program []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "自分で並べた reader の答えを入れた例外を投げる。"
  (<- base int (scheduler-foundation (with-handlers [(reader {"base" 100})] (Ask "base"))))
  (raise (ValueError (.format "業務の失敗 base={}" base))))


(defk host-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"} :tags {:context "doeff-cluster-test" :role "foundation"}}
  "土台: scheduler と、宿の契約に答える host-reader(handler の値)の下で本体を走らせる。module の最上位の関数なので Program には
   参照で詰まる。host-reader は session の値(Get / Put)を使うので、外側に state を置く。"
  (<- answer (scheduled (with-handlers [(state) host-reader] body)))
  answer)


(defk context-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宿の契約(HOST-CONTRACT)の run-context と Program の path を土台の host-reader で読み、宣言の repo の名と path を返す。
   handler の値 host-reader を本体で直に参照すると、本体(値で詰まる)が handler の値を捕まえて詰められない — 土台の関数を通す。"
  (<- pair tuple (foundation (host-answers)))
  (<- declared (runtime-env-of-context (get pair 0)))
  (.format "{}|{}" (.join "," (lfor r declared.repos r.name)) (get pair 1)))


(defk host-answers []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宿の契約の 2 つの Ask(run-context と Program の path)の答えの組。"
  (<- ctx (Ask HOST-CONTRACT.run-context-key))
  (<- path str (Ask HOST-CONTRACT.program-key))
  #(ctx path))
