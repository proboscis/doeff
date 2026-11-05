;;; Fixture for doeff-agents-no-sleep-effect-hy — fires on the shape where doeff-agents defines its own wait effect and its constructor in Hy.
;;; The trailing "; fires" marks the lines that must fire (tests/semgrep/test_doeff_agents_no_sleep_effect_hy_rule.py matches the marked lines against the lines the rule hits).
;;; The firing shapes are copies of what existed before waiting was replaced by doeff-time's Delay: Sleep and sleep in io_effects.hy, ClockSleep and clock-sleep in sessionhost/effects.hy.
;;; This file is never executed (read-only fixture).
(require doeff-hy.macros [defk defhandler <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_time [Delay DelayEffect])


;; --- fires: own wait effects and their constructors
(defclass [(dataclass :frozen True :kw-only True)] Sleep [EffectBase]  ; fires
  "Wait on the wall clock."
  #^ float seconds)

(defclass [(dataclass :frozen True :kw-only True)] ClockSleep [EffectBase]  ; fires
  #^ float seconds)

(defclass PaneDelay [EffectBase]  ; fires
  #^ float seconds)

(defk sleep [seconds]  ; fires
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "intent"}}
  (<- _ (Sleep :seconds seconds))
  None)

(defk clock-sleep [seconds]  ; fires
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "intent"}}
  (<- _ (ClockSleep :seconds seconds))
  None)


;; --- does not fire: effects that are not waits, types that are not effects, programs that wait via doeff-time effects, clauses where a fake answers Delay
(defclass [(dataclass :frozen True :kw-only True)] MonotonicTime [EffectBase]
  "Current value of the monotonic clock (seconds).")

(defclass SleepSchedule []
  "A value type that is not an effect (does not inherit EffectBase)."
  #^ float seconds)

(defk settle-then-send [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  (<- (Delay seconds))
  None)

(defhandler fake-world-time [world]
  (DelayEffect [seconds]
    (setv world.clock (+ world.clock seconds))
    (resume None)))
