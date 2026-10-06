;;; 本物の handler の待ちの遅れを、替え玉の CLI(tests/stub_cli/claude.hy)で測る script(API は撃たない・上に載る系の物は import しない)。
;;;
;;; 使い方(doeff の repo の根で):
;;;   uv run --project . hy packages/doeff-claude-code/scripts/measure_handler_wait.hy <回数 N> [<名札>]
;;; 1 回 = 新しい会話を起こし(ClaudeStartTurn)、1 手番を最後まで読み(ClaudeReadTurnEvents)、閉じる。N 回を 1 つの host で続けて回す。
;;; 前の版で回す時は、前の版の作業木にこの file だけを重ねて同じ命令で回す(重ねた事は呼び手が表に書く)。
;;;
;;; 測る区間(どれも替え玉の行を handler の読み手の thread が読んだ壁の時刻を起点にする — 替え玉が書いてから読み手が読むまでは pipe の
;;; 1 回の受け渡しだけ):
;;;   init-wait-ms  替え玉が init の行を書いた刻 → 起動の待ち(ClaudeStartTurn)が答えた刻。上の層の手元の測り(turn_latency_bench)の「CLI の起動 → init」の中。
;;;   text-wait-ms  頁の読み(ClaudeReadTurnEvents)が待っている間に替え玉が本文の差分の行を書いた刻 → その頁が返った刻。上の層の
;;;                 手元の測り(turn_latency_bench)の「hook の終わり → 最初の本文」の中(替え玉は hook の秒と考える秒を置いてから本文を出す)。読みが待っていな
;;;                 かった回(行が読みの前に既に在った)は数えず、数えなかった回数を出す。
;;;   cpu-s         1 回(起動から閉じるまで)のこの process の CPU 秒(resource.getrusage の user + system — 替え玉の子の分は入らない)。
;;; 出力: 頭に表(測った版の sha・handler の file・待ちの形・開始と終わりの JST・N)、続けて区間ごとの中央と最小〜最大、最後に 1 行の JSON
;;; (呼び手が回をまたいで束ねるため — 生の値の列を持つ)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [asdict dataclass])
(import datetime [datetime timedelta timezone])
(import json)
(import os)
(import pathlib [Path])
(import resource)
(import statistics)
(import subprocess)
(import sys)
(import tempfile)
(import uuid)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [GetTime sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession TurnInput])
(import doeff_claude_code.lines [Init PartialMessage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeReadTurnEvents ClaudeCloseSession TurnStarted TurnEventPage])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code [handler :as handler-module])

(val STUB-PATH (str (/ (. (Path __file__) (resolve) parent parent) "tests" "stub_cli" "claude.hy")))
;; 替え玉は init の後に hook の知らせを出して 0.2 秒、message_start を出して 0.2 秒待ってから本文を 3 片の差分で出す(頁の読みが待って
;; いる間に行が来る形)。
(val PROMPT "Run the hooks for 0.2 seconds first. Think for 0.2 seconds before replying. Stream the reply in 3 pieces. Reply with exactly: MEASURED")
(val INHERITED-PREFIXES #("CLAUDECODE" "CLAUDE_CODE_" "CLAUDE_CONFIG_DIR" "AI_AGENT" "CLAUDE_PID" "CLAUDE_EFFORT" "DOEFF_CLAUDE_CODE_"))
(val JST (timezone (timedelta :hours 9)))


(defk ms-between [since until]
  {:pre [(: since datetime) (: until datetime)] :post [(: % float)] :tags {:context "claude-code" :role "program"}}
  "2 つの壁の時刻の差をミリ秒で綴るため。"
  (* (- (.timestamp until) (.timestamp since)) 1000.0))

(defk cpu-seconds []
  {:pre [] :post [(: % float)] :tags {:context "claude-code" :role "program"}}
  "この process の今までの CPU 秒(user + system)を読むため。"
  (val usage (resource.getrusage resource.RUSAGE-SELF))
  (+ usage.ru-utime usage.ru-stime))

(defrecord RoundMeasure
  "1 回の区間の値: init-wait-ms・text-wait-ms(本文の読みが待っていなかった回は None)・cpu-s(頭の註の区間)。"
  (#^ float init-wait-ms)
  (#^ (| float None) text-wait-ms)
  (#^ float cpu-s))

(defk one-round [#^ ClaudeSessionSpec spec]
  {:pre [(: spec ClaudeSessionSpec)] :post [(: % RoundMeasure)] :tags {:context "claude-code" :role "program"}}
  "1 回(起こす → 1 手番を最後まで読む → 閉じる)の区間の値を測るため。"
  (<- cpu-before (cpu-seconds))
  (val sid (str (uuid.uuid4)))
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput PROMPT (str (uuid.uuid4)))))
  (<- answered (GetTime))
  (assert (isinstance started TurnStarted) (repr started))
  (var after -1)
  (var lines #())
  (var text-wait None)
  (var end None)
  (while (is end None)
    (<- called (GetTime))
    (<- page (ClaudeReadTurnEvents started.turn after 30.0))
    (<- back (GetTime))
    (assert (isinstance page TurnEventPage) (repr page))
    (val has-text (any (gfor line page.lines (and (isinstance line.kind PartialMessage) line.kind.text-delta))))
    (when (and has-text (is text-wait None) page.lines (> (.timestamp (. (get page.lines 0) at)) (.timestamp called)))
      (<- waited (ms-between (. (get page.lines 0) at) back))
      (:= text-wait waited))
    (:= lines (+ lines (tuple page.lines)))
    (:= after page.next-seq)
    (:= end page.end))
  (val init-line (next (gfor line lines :if (isinstance line.kind Init) line)))
  (<- init-wait (ms-between init-line.at answered))
  (<- (ClaudeCloseSession sid "measure"))
  (<- cpu-after (cpu-seconds))
  (RoundMeasure :init-wait-ms init-wait :text-wait-ms text-wait :cpu-s (- cpu-after cpu-before)))

(defk rounds [#^ ClaudeSessionSpec spec #^ int count]
  {:pre [(: spec ClaudeSessionSpec) (: count int)] :post [(: % tuple)] :tags {:context "claude-code" :role "program"}}
  "count 回を続けて測るため(答え = 回ごとの値の tuple)。"
  (var measured #())
  (for [_ (range count)]
    (<- one (one-round spec))
    (:= measured (+ measured #(one))))
  measured)

(defk summary [#^ str name #^ tuple values]
  {:pre [(: name str) (: values tuple)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "1 区間の値の列を「中央 (最小〜最大)」の 1 行に綴るため。"
  (val present (tuple (gfor value values :if (is-not value None) value)))
  (if present
      (.format "{:<14} 中央 {:9.3f}  最小 {:9.3f}  最大 {:9.3f}  (数えた {} 回・数えなかった {} 回)"
               name (statistics.median present) (min present) (max present) (len present) (- (len values) (len present)))
      (.format "{:<14} 値なし" name)))

(when (= __name__ "__main__")
  (val count (int (get sys.argv 1)))
  (val label (if (> (len sys.argv) 2) (get sys.argv 2) ""))
  (val handler-file (Path handler-module.__file__))
  (val repo (. handler-file parent))
  (val sha (.strip (. (subprocess.run ["git" "-C" (str repo) "rev-parse" "HEAD"] :capture-output True :text True) stdout)))
  (val dirty (.strip (. (subprocess.run ["git" "-C" (str repo) "status" "--short" "--" (str handler-file)]
                                        :capture-output True :text True) stdout)))
  (val polls (in "POLL-SECONDS" (.read-text handler-file :encoding "utf-8")))
  (val work (Path (tempfile.mkdtemp :prefix "measure-handler-wait-")))
  (val env (dfor #(key value) (.items os.environ) :if (not (.startswith key INHERITED-PREFIXES)) key value))
  (.mkdir (/ work "work") :parents True :exist-ok True)
  (val spec (ClaudeSessionSpec :home (ClaudeHome (str (/ work "home")) env) :cwd (str (/ work "work"))
                               :settings {"disableAllHooks" True}))
  (val host (ClaudeCodeHost #(sys.executable "-m" "hy" STUB-PATH) (clock-of (sync-time-handler)) 4 7200.0 :launch-timeout 60.0))
  (val began (datetime.now JST))
  (val measured (run (scheduled (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)]
                                               (rounds spec count)))))
  (val finished (datetime.now JST))
  (print (.format "名札          {}" label))
  (print (.format "版の sha      {}{}" sha (if dirty " (handler.hy に commit していない変更あり)" "")))
  (print (.format "handler       {}" handler-file))
  (print (.format "待ちの形      {}" (if polls "時間で起きて確かめる(POLL-SECONDS)" "呼び鈴で起きる")))
  (print (.format "開始 / 終わり {} / {} (JST)" (.isoformat began :timespec "seconds") (.isoformat finished :timespec "seconds")))
  (print (.format "回数 N        {}" count))
  (print (run (summary "init-wait-ms" (tuple (gfor one measured one.init-wait-ms)))))
  (print (run (summary "text-wait-ms" (tuple (gfor one measured one.text-wait-ms)))))
  (print (run (summary "cpu-s" (tuple (gfor one measured one.cpu-s)))))
  (print (json.dumps {"label" label "sha" sha "dirty" (bool dirty) "polls" polls "began" (.isoformat began) "finished" (.isoformat finished)
                      "rounds" (lfor one measured (asdict one))})))
