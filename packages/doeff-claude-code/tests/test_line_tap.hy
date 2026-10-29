;; 手番の行の写し手(ClaudeStartTurn.tap — card ki-31b41eac5071)の検。
;;
;; 上の層(CLI のホスト)は答えの記録を、ClaudeReadTurnEvents で頁を受けた時に書いていた。本番の turn-host では、読み手の thread が
;; 行を読んでから上の層が頁を受けるまで中央値 7.3 秒・最大 119 秒遅れ、記録を 2 秒しか待たない hook が、答えた後の呼びを「答えていない」と
;; 断った。写し手は、読み手の thread が行を手番の記録に足した直後に呼ばれる — 上の層が頁を読まなくても、行は写し手へ届く。
;;
;; 失敗ケース:
;;   1 本番の handler(替え玉の CLI): 頁を 1 度も読まずに、手番の result の行までが写し手へ届く。届いた行は後で読んだ頁の行と同じ行を
;;     同じ順に持つ(写し手だけが持つ行・頁だけが持つ行は無い)。直す前は写し手の口が無く、頁を読むまで上の層は何も知らなかった。
;;   2 同じ process を使い回す次の手番の行は、次の手番の写し手へだけ届く(前の手番の写し手へ混ぜない)。
;;   3 例外を投げる写し手でも手番は終わる(読み手の thread を止めない — 止めると process を降ろす)。
;;   4 fake も同じ: 写し手へ届いた行は頁の行と同じ。
(require doeff-hy.macros [deftest defk <- val])
(import collections.abc [Callable])
(import pathlib [Path])
(import sys)
(import threading)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_core_effects [scheduled])
(import doeff_time [sync-time-handler sim-time-handler SimClock])
(import doeff_claude_code [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession TurnInput])
(import doeff_claude_code [Completed TurnResult])
(import doeff_claude_code [ClaudeStartTurn TurnStarted])
(import doeff_claude_code._clock [clock-of])
(import doeff_claude_code [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [reply-prompt])
(import tests.scenario_steps [read-to-end])

;; 頁を読まずに result の行が写し手へ届くのを待つ上限の秒(替え玉の CLI の 1 手番は 1 秒ほど)。
(val TAP-WAIT-SECONDS 30.0)


(defclass Tapped []
  "写し手へ届いた行を集め、result の行が届いたら合図する検の写し手(読み手の thread から呼ばれる)。"
  (defn __init__ [self]
    (setv self.lines #()
          self.result (threading.Event)))
  (defn __call__ [self line]
    (setv self.lines (+ self.lines #(line)))
    (when (isinstance line.kind TurnResult)
      (.set self.result))))


(defn raising-tap [line]
  "例外を投げる写し手(失敗ケース 3)。"
  (raise (RuntimeError "tap failed")))


(defn spec-in [#^ Path tmp-path]
  "替え玉の CLI の会話の宣言を作るため(作業 dir は tmp-path の下)。"
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env ""))
                     :cwd (str work) :settings {"disableAllHooks" True}))


(defn on-real-handler [host program]
  "program を本番の handler で走らせるため(時計と計時の行の答え手だけを外側に置く)。"
  (run (scheduled (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)] program))))


(defn seqs-of [lines]
  "行の列の seq の組(写し手と頁の行を比べるため)。"
  (tuple (gfor line lines line.seq)))


(deftest test-the-tap-gets-the-lines-before-any-page-is-read [tmp-path]
  ;; 失敗ケース 1・2 — 本番の handler と替え玉の CLI。
  (val host (ClaudeCodeHost #(sys.executable "-m" "hy" STUB-PATH) (clock-of (sync-time-handler)) 8 7200.0))
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val first (Tapped))
  (val started (on-real-handler host (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "ONE") "r1") :tap first)))
  (assert (isinstance started TurnStarted) (repr started))
  ;; 頁を 1 度も読まずに待つ — 写し手は読み手の thread から呼ばれる。
  (assert (.wait first.result TAP-WAIT-SECONDS) (repr (lfor line first.lines line.kind)))
  (val page (on-real-handler host (read-to-end started.turn 30.0)))
  (assert (isinstance page.end Completed) (repr page.end))
  (assert (= (seqs-of first.lines) (seqs-of page.lines)) (repr #((seqs-of first.lines) (seqs-of page.lines))))
  (assert (= (lfor line first.lines line.kind) (lfor line page.lines line.kind)))
  ;; 同じ process を使い回す次の手番は、次の写し手へだけ。
  (val heard (len first.lines))
  (val second (Tapped))
  (val again (on-real-handler host (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "TWO") "r2") :tap second)))
  (assert (isinstance again TurnStarted) (repr again))
  (assert (.wait second.result TAP-WAIT-SECONDS) (repr (lfor line second.lines line.kind)))
  (val next-page (on-real-handler host (read-to-end again.turn 30.0)))
  (assert (= (seqs-of second.lines) (seqs-of next-page.lines)) (repr #((seqs-of second.lines) (seqs-of next-page.lines))))
  (assert (= (len first.lines) heard) "前の手番の写し手へ次の手番の行が届いた"))


(deftest test-a-raising-tap-does-not-stop-the-turn [tmp-path]
  ;; 失敗ケース 3。
  (val host (ClaudeCodeHost #(sys.executable "-m" "hy" STUB-PATH) (clock-of (sync-time-handler)) 8 7200.0))
  (val started (on-real-handler host (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) (spec-in tmp-path)
                                                      (TurnInput (reply-prompt "ONE") "r1") :tap raising-tap)))
  (assert (isinstance started TurnStarted) (repr started))
  (val page (on-real-handler host (read-to-end started.turn 30.0)))
  (assert (isinstance page.end Completed) (repr page.end)))


(defn fake-reply [#^ str text #^ tuple memory]  ; defk にできない: fake の世界が呼ぶ callback
  "検の返事(道具なしで本文を返す)。"
  (FakeReply text))

(defk fake-turn [tap]
  {:pre [(: tap Callable)] :post [(: % tuple)] :tags {:context "claude-code-test" :role "program"}}
  "fake の世界で写し手つきの手番を 1 つ終わりまで読むため。答え = 頁の行。"
  (<- started (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) (ClaudeSessionSpec :home (ClaudeHome "fake-home") :cwd "/work")
                               (TurnInput "hello" "r1") :tap tap))
  (<- page (read-to-end started.turn 120.0))
  page.lines)

(deftest test-the-fake-taps-the-lines-it-emits
  ;; 失敗ケース 4。
  (val tapped (Tapped))
  (<- lines (with_handlers [(sim-time-handler :clock (SimClock)) (fake-claude-code-handler (FakeClaudeWorld fake-reply))]
                           (fake-turn tapped)))
  (assert (.is-set tapped.result))
  (assert (= (seqs-of tapped.lines) (seqs-of lines)) (repr #((seqs-of tapped.lines) (seqs-of lines)))))
