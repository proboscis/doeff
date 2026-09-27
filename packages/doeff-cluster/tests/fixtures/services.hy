;;; job の Program と系の見本(ADR-DOE-CLUSTER-001 — job は Program の値 1 つ・実行先は handler を足さない)。
;;;
;;; Program は自分の handler(土台が返す reader と scheduler)を本体の with-handlers で並べる defk。系は defsystem で書き、土台の関数
;;; (tests.fixtures.envs の module の最上位の関数)を引数に受ける。
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [scheduled])


(defk tally-body [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Ask \"base\" に step を足す(答えるのは Program が並べた土台の reader)。"
  (<- base int (Ask "base"))
  (+ base step))


(defk tally-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の handler と scheduler を自分で並べて tally-body を走らせる(実行先は何も足さない)。"
  (<- handlers list (foundation))
  (<- total int (scheduled (with-handlers handlers (tally-body step))))
  total)


(defk greeter-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の reader が答える挨拶に step を付ける。"
  (<- handlers list (foundation))
  (<- greeting str (scheduled (with-handlers handlers (Ask "greeting"))))
  (+ greeting (str step)))


(defk self-contained-program [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler を本体の中で関数を呼んで作る Program(詰められる — handler の値を捕まえていない)。"
  (<- total int (scheduled (with-handlers [(reader {"base" 10})] (tally-body step))))
  total)


(defk bare-program [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler を 1 つも並べない Program(Ask \"base\" に答える物が無い — 実行先が handler を足さないことの反例に使う)。"
  (<- total int (tally-body step))
  total)


(defk holding-program [handler step]
  {:pre [(: handler Callable) (: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler の値を引数に受ける Program(詰めると UnsendableProgram — handler は値として詰めない)。"
  (<- total int (scheduled (with-handlers [handler] (tally-body step))))
  total)


(defsystem lab [foundation]
  "見本の系: tally 1 つ"
  (tally (tally-program foundation 2) :needs #{"cluster-net"} :environ {"TALLY_BASE" "1"}))


(defsystem lab-pair [foundation]
  "見本の系: tally と greeter"
  (tally (tally-program foundation 2) :needs #{"cluster-net"})
  (greeter (greeter-program foundation 3) :needs #{"cluster-net"} :readiness {"windowSeconds" 30} :update "handoff"))
