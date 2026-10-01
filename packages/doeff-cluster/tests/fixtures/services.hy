;;; job の Program と系の見本(ADR-DOE-CLUSTER-001 — job は Program の値 1 つ・実行先は handler を足さない)。
;;;
;;; Program は土台(tests.fixtures.envs の module の最上位の関数 — 本体を受けて自分の handler と scheduler の下で走らせる)で本体を包む
;;; defk(計画 10.1 — job は自分で scheduled を包まない)。系は defsystem で書き、土台を引数に受ける。
(require doeff-hy.macros [defk defsystem <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
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


(defrecord PairFoundation
  "土台を欄の名と型を持つ 1 つの値で渡す見本(系の引数の record — identity は {record, fields})。main = tally の土台・side = 予備の土台・
   step = tally の歩み。"
  (#^ Callable main)
  (#^ Callable side)
  (#^ int step))


(defclass [(dataclass)] LooseFoundation []
  "凍っていない record の見本(系の引数にすると宣言が断る)。"
  (setv #^ (| Callable None) main None))


(defk tally-on [foundation]
  {:pre [(: foundation PairFoundation)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "土台の record の main で tally-body を包んで走らせる(job の関数が record を受け、中で欄を読む形の見本)。"
  (<- total int (foundation.main (tally-body foundation.step)))
  total)


(defsystem lab-record [foundation]
  "見本の系: 土台を record 1 つで受ける"
  (tally (tally-on foundation) :needs #{"cluster-net"}))


(defsystem lab [foundation]
  "見本の系: tally 1 つ"
  (tally (tally-program foundation 2) :needs #{"cluster-net"} :environ {"TALLY_BASE" "1"}))


(defsystem lab-pair [foundation]
  "見本の系: tally と greeter"
  (tally (tally-program foundation 2) :needs #{"cluster-net"})
  (greeter (greeter-program foundation 3) :needs #{"cluster-net"} :readiness {"windowSeconds" 30} :update "handoff"))
