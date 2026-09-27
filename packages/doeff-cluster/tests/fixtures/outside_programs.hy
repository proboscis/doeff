;;; sim の外の世界(SimOutside)の検の見本 — 2 つの service が、外の系(業務の store の模擬)を effect を通してだけ共有する。
;;;
;;; 本番では、store の effect(StorePut・StoreGet)は各 service の土台の handler が外の系へ話して答える。sim では土台がそれを含まず、
;;; sim の外側に置いた store の模擬(memory-store)が答え、柵は SimOutside の effects に載った型だけを外へ通す。
(require doeff-hy.macros [defk defhandler defsystem <- val var])
(import collections.abc [Callable])
(import doeff [EffectBase])
(import doeff_time [Delay])
(import doeff_core_effects.effects [Ask])


(defclass StorePut [EffectBase]
  "外の系の store に書く(本番は土台の handler が外の系へ送る)。"
  (defn __init__ [self key value]
    (.__init__ (super))
    (setv self.key key self.value value)))

(defclass StoreGet [EffectBase]
  "外の系の store から読む(無ければ None)。"
  (defn __init__ [self key]
    (.__init__ (super))
    (setv self.key key)))


(defhandler memory-store [#^ dict rows]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検が store の中身を読む(sim の外の世界の模擬そのもの)。
  (StorePut [key value]
    (setv (get rows key) value)
    (resume None))
  (StoreGet [key]
    (val found (.get rows key))
    (resume found)))


(defk writer-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "store に 1 秒ごとに数を書く service。"
  (<- (foundation (writer-loop)))
  None)

(defk writer-loop []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "書き手の本体。"
  (var n 0)
  (while True
    (:= n (+ n 1))
    (<- (StorePut "count" n))
    (<- (Delay 1.0))))


(defk reader-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "store の数を読んで、読めた最大を store に写す service。"
  (<- (foundation (reader-loop)))
  None)

(defk reader-loop []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "読み手の本体。"
  (while True
    (<- seen (StoreGet "count"))
    (when (is-not seen None)
      (<- (StorePut "seen" seen)))
    (<- (Delay 1.0))))


(defsystem shared-store [foundation]
  "外の系の store を effect で共有する 2 つの service"
  (writer (writer-program foundation) :needs #{"cluster-net"})
  (reader (reader-program foundation) :needs #{"cluster-net"}))


(defhandler signed-puts [#^ dict rows #^ str job]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; process ごとの外の口の見本(本番の job ごとの身元の token に当たる): 書きを「job の名/鍵」の行にして答える。読みは答えず、
  ;; 共有の外の世界(memory-store)へ渡す。
  (StorePut [key value]
    (setv (get rows (+ job "/" key)) value)
    (resume None)))
