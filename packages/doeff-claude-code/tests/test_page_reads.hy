;; 手番の出来事の読み(ClaudeReadTurnEvents)1 回の費用が、手番の長さ(手番の記録の行の数)で増えない事の検(agora card
;; acp:kanban-issue:ki-07d7059e1eea)。
;;
;; 上の層の源の task は、CLI の行が 1 つ届くたびに 1 回読む。読みが手番の記録の行を全部なめると、長い手番(本番の 30 分を越える手番の
;; 差分の行は p50 7,756〜12,160)の 1 回の読みが行の数に比例し、手番 1 つの読みの CPU の和は長さの 2 乗で増える(agora の模擬で、手番
;; 8 本・手番ごとに約 5,000 行の時、scheduler の thread の CPU の 60.9% が page-of の行のなめだった)。この検は、読み 1 回が触った行の
;; 数を数え、50 倍の長さの手番でも 2 倍を越えない(長さに比例しない)事を確かめる。
(require doeff-hy.macros [deftest <- val])
(import datetime [datetime timezone])
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeTurn])
(import doeff_claude_code.lines [ClaudeStreamLine Other])
(import doeff_claude_code.effects [ClaudeReadTurnEvents])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost SessionRuntime claude-code-handler])

(val SID "8d7f3c1e-5b2a-4c6d-9e8f-0a1b2c3d4e5f")
(val AT (datetime 2026 10 12 4 0 0 :tzinfo timezone.utc))
;; 読みの後ろに残す、まだ渡していない行の数(源の task が 1 回の読みで受ける頁の大きさ)。
(val FRESH 3)


(defclass CountingLines [list]
  "手番の記録の行の列のうち、読みが触った行の数を数える列(list と同じに振る舞い、要素を渡すたびに数える)— 読みの費用を時計でなく
   触った行の数で比べるため(機体の混みで揺れない)。"
  (defn __init__ [self items]
    (.__init__ (super) items)
    (setv self.touched 0))
  (defn __iter__ [self]
    (for [item (.__iter__ (super))]
      (+= self.touched 1)
      (yield item)))
  (defn __getitem__ [self index]
    (setv found (.__getitem__ (super) index))
    (+= self.touched (if (isinstance index slice) (len found) 1))
    found))


(defn touched-by-one-read [#^ int length]
  "行が length 本在る走っている手番の、最後の FRESH 本より前まで読んだ所から 1 回読んだ時に、読みが触った行の数と頁の行の seq を
   答えるため(読みは新しい行が在るので待たない — process も時計の待ちも要らない)。"
  (setv host (ClaudeCodeHost #("claude") (clock-of (sync-time-handler)) 8 7200.0))
  (setv runtime (SessionRuntime SID (ClaudeHome "/h/.claude" {}) "/w"))
  (.register host runtime)
  (setv turn-seq (.open-turn runtime))
  (setv log (.open-log runtime))
  (setv log.lines (CountingLines (lfor seq (range length) (ClaudeStreamLine :seq seq :at AT :kind (Other "stream_event") :raw "{}"))))
  (setv after (- length FRESH 1))
  (setv page (run-read host (ClaudeReadTurnEvents (ClaudeTurn SID turn-seq) after 0.0)))
  #(log.lines.touched (tuple (gfor line page.lines line.seq))))


(defn run-read [host request]
  "読み 1 回を本番の handler で走らせるため(時計と計時の行の答え手だけを外側に置く)。"
  (run (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)] request)))


(deftest test-one-read-does-not-grow-with-the-turn-length
  ;; 失敗ケース = 直す前の page-of は手番の記録の行を全部なめる(読み 1 回の中で 5 度 — 呼び鈴の待ちの判じを含む)ので、50 倍の
  ;; 長さの手番の読みは約 50 倍の行を触った。
  (val short (touched-by-one-read 1000))
  (val long (touched-by-one-read 50000))
  ;; 頁はどちらも、読んだ所より後の FRESH 本だけ。
  (assert (= (get short 1) (tuple (range (- 1000 FRESH) 1000))) (repr short))
  (assert (= (get long 1) (tuple (range (- 50000 FRESH) 50000))) (repr long))
  (assert (<= (get long 0) (* 2 (get short 0))) (repr #("触った行" (get short 0) (get long 0)))))
