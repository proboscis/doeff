;;; 止めの合図の箱(stop_signal_handlers.StopBox)の待ちの登録の失敗ケース(#3871 の単位 4 で見つけた取りこぼし)。
;;;
;;; AwaitStop は「理由がまだ無い」と確かめてから待ちを作って登録する。その間に合図が来ると、合図の受け手は待ちの無いまま理由だけを
;;; 立て、後から登録した待ちは誰にも満たされない(worker の 60 秒の待ちが SIGTERM で抜けなかった)。登録は、理由が立っていれば待ちを
;;; その場で満たし、立っていなければ並べる — 合図の受け手との間に取りこぼしの合間を作らない。
(require doeff-hy.macros [deftest val])
(import doeff_core_effects.stop_signal_handlers [StopBox])


(defclass Recorder []
  "待ちの代わり: complete で受けた理由を覚える。"
  (defn __init__ [self]
    (setv self.reasons #()))
  (defn #^ None complete [self #^ str reason]
    (setv self.reasons (+ self.reasons #(reason)))
    None))


(deftest test-a-wait-parked-after-the-signal-is-completed-at-once
  (val box (StopBox))
  (.receive box 15 None)
  (val waiter (Recorder))
  (.park box waiter)
  (assert (= waiter.reasons #("signal 15")) waiter.reasons)
  (assert (= box.waiters #()) box.waiters))


(deftest test-a-wait-parked-before-the-signal-is-completed-by-the-signal-once
  (val box (StopBox))
  (val waiter (Recorder))
  (.park box waiter)
  (assert (= waiter.reasons #()) waiter.reasons)
  (.receive box 15 None)
  (assert (= waiter.reasons #("signal 15")) waiter.reasons))
