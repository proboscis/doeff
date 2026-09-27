;;; job の Program と系の見本(ADR-DOE-CLUSTER-001 — job は Program の値 1 つ・実行先は handler を足さない)。
;;;
;;; Program は土台(tests.fixtures.envs の module の最上位の関数 — 本体を受けて自分の handler と scheduler の下で走らせる)で本体を包む
;;; defk(計画 10.1 — job は自分で scheduled を包まない)。系は defsystem で書き、土台を引数に受ける。
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import tests.fixtures.envs [plain-foundation])


(defk tally-body [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Ask \"base\" に step を足す(答えるのは土台の reader)。"
  (<- base int (Ask "base"))
  (+ base step))


(defk tally-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台で tally-body を包んで走らせる(実行先は何も足さない)。"
  (<- total int (foundation (tally-body step)))
  total)


(defk greeter-program [foundation step]
  {:pre [(: foundation Callable) (: step int)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の reader が答える挨拶に step を付ける。"
  (<- greeting str (foundation (Ask "greeting")))
  (+ greeting (str step)))


(defk self-contained-program [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler を本体の中で関数を呼んで作る Program(詰められる — handler の値を捕まえていない)。土台の base より内側の reader が答える。"
  (<- total int (plain-foundation (with-handlers [(reader {"base" 10})] (tally-body step))))
  total)


(defk bare-program [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler を 1 つも並べない Program(Ask \"base\" に答える物が無い — 実行先が handler を足さないことの反例に使う)。"
  (<- total int (tally-body step))
  total)


(defk holding-program [handler step]
  {:pre [(: handler Callable) (: step int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "handler の値を引数に受ける Program(詰めると UnsendableProgram — handler は値として詰めない)。"
  (<- total int (plain-foundation (with-handlers [handler] (tally-body step))))
  total)


(defsystem lab [foundation]
  "見本の系: tally 1 つ"
  (tally (tally-program foundation 2) :needs #{"cluster-net"} :environ {"TALLY_BASE" "1"}))


(defsystem lab-pair [foundation]
  "見本の系: tally と greeter"
  (tally (tally-program foundation 2) :needs #{"cluster-net"})
  (greeter (greeter-program foundation 3) :needs #{"cluster-net"} :readiness {"windowSeconds" 30} :update "handoff"))
