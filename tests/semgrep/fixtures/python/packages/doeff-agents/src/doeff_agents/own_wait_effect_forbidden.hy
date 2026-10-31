;;; doeff-agents-no-sleep-effect-hy の検体 — doeff-agents が Hy で自前の待ちの効果とその構築子を定める形を鳴らす。
;;; 行末の「; 鳴る」が鳴るべき行の印(tests/semgrep/test_doeff_agents_no_sleep_effect_hy_rule.py が印の行と当たりの行を突き合わせる)。
;;; 鳴る形は待ちを doeff-time の Delay に替える前に在った物の写し: io_effects.hy の Sleep と sleep・sessionhost/effects.hy の ClockSleep と clock-sleep。
;;; この file は実行しない(読むだけの検体)。
(require doeff-hy.macros [defk defhandler <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_time [Delay DelayEffect])


;; --- 鳴る: 自前の待ちの効果と、その構築子
(defclass [(dataclass :frozen True :kw-only True)] Sleep [EffectBase]  ; 鳴る
  "壁時計で待つ。"
  #^ float seconds)

(defclass [(dataclass :frozen True :kw-only True)] ClockSleep [EffectBase]  ; 鳴る
  #^ float seconds)

(defclass PaneDelay [EffectBase]  ; 鳴る
  #^ float seconds)

(defk sleep [seconds]  ; 鳴る
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "intent"}}
  (<- _ (Sleep :seconds seconds))
  None)

(defk clock-sleep [seconds]  ; 鳴る
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "intent"}}
  (<- _ (ClockSleep :seconds seconds))
  None)


;; --- 鳴らない: 待ちでない効果・効果でない型・doeff-time の効果で待つ program・fake が Delay に答える節
(defclass [(dataclass :frozen True :kw-only True)] MonotonicTime [EffectBase]
  "単調時計の現在値(秒)。")

(defclass SleepSchedule []
  "効果ではない値の型(EffectBase を継がない)。"
  #^ float seconds)

(defk settle-then-send [seconds]
  {:pre [(: seconds float)] :post [(: % None)] :tags {:context "sample" :role "program"}}
  (<- (Delay seconds))
  None)

(defhandler fake-world-time [world]
  (DelayEffect [seconds]
    (setv world.clock (+ world.clock seconds))
    (resume None)))
