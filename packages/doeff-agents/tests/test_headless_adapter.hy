;; headless の handler(handlers/headless.hy)の検 — doeff-agents の公開 effect だけを撃つ Program を、層 2 の handler の組だけ替えて走らせる
;; (agora-redesign #604)。
;;
;;   fake  doeff-claude-code の fake の handler + 仮想の時計。API も process も使わない。
;;   stub  doeff-claude-code の本番の handler + 替え玉の CLI(packages/doeff-claude-code/tests/stub_cli/claude.hy)+ 壁の時計。
;;   real  doeff-claude-code の本番の handler + 本物の claude(個人の profile・model haiku)。env DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR が
;;         無ければ skip・印 e2e(日次と着地の門は -m "not e2e" で除く)。会社の profile は使わない。
;;
;; 筋書きの Program は session host の socket を開かず、doeff_claude_code も doeff_agents.sessionhost も import しない(公開 effect だけ)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import os)
(import pathlib [Path])
(import re)
(import sys)
(import pytest)
(import doeff [run with_handlers EffectBase])
(import doeff_core_effects.handlers [state :as session-store slog-discard-handler])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
;; 故障の注入だけは層 2 のテスト用の effect を使う(公開 effect ではない — process を消す手段が公開面に無いため)。事前起動した runtime
;; を最初のターンが使い回したか(層 2 の起動回数)を読む effect も同じ(公開面は process を見せない)。
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess LiveProcess NoLiveProcess])
;; fake の組に渡した env と settings が層 2 へ届く起動の宣言に載るかを見る口も、層 2 の effect を写す(#3327 — 公開面に宣言が出ないため)。
(import doeff_claude_code.effects [ClaudeStartTurn])
(import doeff_time [Delay GetMonotonic SimClock sim-time-handler sync-time-handler])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.effects [
  Launch FollowUp Interrupt Events AwaitResult Monitor Capture Stop ReleaseSession ExportContextEffect WarmSession
  SessionHandle AgentEventPage AwaitStatus TurnInputMode InputFateState
  AgentTextEvent AgentTextDeltaEvent AgentThinkingDeltaEvent AgentToolUseEvent AgentToolResultEvent AgentInputFateEvent AgentTurnEndEvent
  AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage
  AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError SessionNotFoundError
  AgentError AgentLaunchError TurnInFlightError LaunchEffect RedeemTurnCredentialEffect
  NamedContextId HandlerMadeContextId])
(import doeff_agents.monitor [SessionStatus])
;; 層 2 の handler との対は doeff-agents の組み立ての部品で作る(この検も doeff_claude_code を import しない)。
(import doeff_agents.handlers.headless_compose [FakeReply FakeClaudeWorld headless-claude-handlers fake-headless-claude-handlers])
;; fake の返事の usage の型(FakeReply の last-call-usage)と model ごとの窓の型は、組み立ての部品と公開面の module から読む。
(import doeff_agents.handlers.headless_compose :as compose)
(import doeff_agents.effects :as public-effects)

(setv FAKE "fake" STUB "stub" REAL "real")
(setv REAL-CONFIG-ENV "DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR")
(setv REPO-PACKAGES (. (Path __file__) (resolve) parent parent parent))
(setv STUB-PATH (str (/ REPO-PACKAGES "doeff-claude-code" "tests" "stub_cli" "claude.hy")))
(setv ADAPTER-PATH (/ REPO-PACKAGES "doeff-agents" "src" "doeff_agents" "handlers" "headless.hy"))
;; 子の claude に親の会話の印・hook の socket を継がせない(doeff-claude-code の検と同じ)。
(setv INHERITED-PREFIXES #("CLAUDECODE" "CLAUDE_CODE_" "CLAUDE_CONFIG_DIR" "AI_AGENT" "CLAUDE_PID"
                           "CLAUDE_EFFORT" "DOEFF_CLAUDE_CODE_"))
(setv CODEWORD "OKAPI-77" EXTRA-WORD "EXTRA-9" PAGE-WAIT 5.0 MAX-PAGES 2000)


;; --- 筋書きの言葉(替え玉の CLI の規則 scenario_rules.hy と同じ言葉 — 本物もこの言葉どおりに振る舞う) ---------------

;; 本物の model が「覚えて・思い出して」を注入の試みと読んで断った実測(2026-09-25)があるので、検の文脈を先に名乗る。
(defn #^ str remember-prompt [#^ str word]
  (.format "This is a test of conversation resume. Remember the codeword {}. Reply with exactly: {}" CODEWORD word))
(defn #^ str recall-prompt [] "Same resume test. What was the codeword I asked you to remember? Reply with only the codeword.")
(defn #^ str reply-prompt [#^ str word] (.format "Reply with exactly: {}" word))
(defn #^ str sleep-prompt [#^ int seconds #^ str word]
  (.format "Use the Bash tool to run exactly this command: sleep {} . When it finishes, reply with exactly: {}" seconds word))
(defn #^ str extra-prompt [] (.format "Also include the word {} in your final reply." EXTRA-WORD))
;; 出力のある道具の手番(agora-redesign #3744 — 道具の命令と出力が出来事まで届くかを見る)。替え玉の CLI の規則と同じ言葉。
(defk echo-prompt [#^ str output #^ str word]
  {:pre [(: output str) (: word str)] :post [(: % str)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "道具の出力が output になる命令(echo)を走らせてから word で答えさせる prompt。"
  (.format "Use the Bash tool to run exactly this command: echo {} . Then reply with exactly: {}" output word))
;; 答えの前に考えている間の差分を片の数だけ出す手番(agora-redesign #3789 — 考えの差分が出来事まで届くかを見る)。替え玉の CLI の規則
;; (scenario_rules.hy の THINKING-PIECES-PHRASE)と同じ言葉。片の中身は替え玉の CLI も fake も "..."。
(defk thinking-prompt [#^ int pieces #^ str word]
  {:pre [(: pieces int) (: word str)] :post [(: % str)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "答えの前に考えの差分を pieces 片出してから word で答えさせる prompt。"
  (.format "Stream {} thinking pieces. Reply with exactly: {}" pieces word))
(val THINKING-PIECES 3)
(val THINKING-PIECE "...")
(val ECHO-OUTPUT "TOOL-OUT-5")
(val ECHO-SECONDS 0.2)
;; 替え玉の CLI の本体と subagent の model の名(stub_cli/claude.hy と同じ — #3744)。
(val CALL-MODEL "claude-stub")
(val SUBAGENT-MODEL "claude-stub-sub")

(defn fake-responder [#^ str text #^ tuple memory]
  "fake の返事(scenario_rules.hy の reply-for と同じ規則の写し — 型の違う 2 つ目の規則を作らない範囲で最小)。
   fake にだけ在る規則: 「Fail after spending <額>」= 額を使った後に誤りで終える手番(失敗の手番の額の写しを見る検のため)。"
  (setv spent (re.search r"Fail after spending (\S+)" text))
  (when spent
    (return (FakeReply "" :tool-seconds 2.0 :fail "spent then failed" :cost-usd (float (.group spent 1)))))
  (setv sleep (re.search r"sleep (\d+)" text)
        thinking (re.search r"Stream (\d+) thinking pieces\." text)
        command (re.search r"run exactly this command: (.+?) \." text)
        exact (re.search r"[Rr]eply with exactly: (\S+)" text)
        extra (re.search r"include the word (\S+)" text))
  (setv echoed (if command (re.fullmatch r"echo (\S+)" (.group command 1)) None))
  (setv word (cond
               (in "What was the codeword" text)
                 (next (gfor earlier memory :setv found (re.search r"codeword (\S+?)\." earlier) :if found (.group found 1))
                       "UNKNOWN")
               exact (.group exact 1)
               extra (.group extra 1)
               True "OK"))
  ;; 道具の命令(input)は prompt の命令の文、出力は echo の語(ほかの命令は出力なし)— 替え玉の CLI と同じ。echo の道具の手番は、
  ;; 本体の最後の呼びの usage と model・model ごとの窓も替え玉の CLI と同じ値を名乗る(#3744)。
  (FakeReply word :tool-seconds (cond sleep (float (.group sleep 1)) echoed ECHO-SECONDS True 0.0)
             :thinking-deltas (if thinking (int (.group thinking 1)) 0)
             :tool-input (if command {"command" (.group command 1)} {})
             :tool-output (if echoed (.group echoed 1) "")
             :last-call-usage (if echoed
                                  (compose.Usage :input-tokens 4 :output-tokens 1 :cache-creation-input-tokens 20
                                                 :cache-read-input-tokens 1010)
                                  None)
             :last-call-model (if echoed CALL-MODEL None)
             :model-windows (if echoed
                                #((public-effects.ModelWindow CALL-MODEL 200000 32000)
                                  (public-effects.ModelWindow SUBAGENT-MODEL 100000 None))
                                #())))


;; --- 解釈器(composition root) --------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Setting []
  "筋書きの宣言: work-dir = 作業 dir / model = 手番の model / timeout = 1 手番の終わりを待つ上限(秒)/ sleep = 道具の秒数。"
  (#^ Path work-dir)
  (#^ (| str None) model)
  (#^ float timeout)
  (#^ int sleep))

(defn child-env []
  (dfor #(key value) (.items os.environ) :if (not (.startswith key INHERITED-PREFIXES)) key value))

(defn handlers-for [#^ str backend #^ str home-dir]
  "解釈器の handler の組(先頭が外側): 時間の handler → 層 2 の handler → headless の adapter。本番の層 2 の外側には、計時の行(slog)の
   答え手も置く(agora-redesign #3605)。"
  (setv settings {"disableAllHooks" True})
  (cond
    ;; fake の層 2 は process を起こさないので子の env は空・CLI の settings は本番の 2 つと同じ宣言。
    (= backend FAKE) (+ [(sim-time-handler :clock (SimClock))] (fake-headless-claude-handlers fake-responder home-dir :env {} :settings settings))
    (= backend STUB) (+ [(sync-time-handler) slog-discard-handler]
                        (headless-claude-handlers home-dir (child-env) :settings settings :live-limit 8 :credential-floor-seconds 7200.0
                                                  :command #(sys.executable "-m" "hy" STUB-PATH)))
    (= backend REAL) (+ [(sync-time-handler) slog-discard-handler] (headless-claude-handlers home-dir (child-env) :settings settings :live-limit 8 :credential-floor-seconds 7200.0))
    True (raise (ValueError backend))))

(defn run-on [#^ str backend #^ Path tmp-path #^ Callable scenario [home-name "home"]]
  "scenario(Setting) → Program を、層 2 の handler の組 + headless の handler の下で走らせる。
   home-name = claude の家の dir の名(別の名 = 空の別の家 — fake は run ごとに新しい世界)。"
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv home-dir (if (= backend REAL) (get os.environ REAL-CONFIG-ENV) (str (/ tmp-path home-name))))
  (setv setting (Setting work (if (= backend REAL) "haiku" None) (if (= backend REAL) 180.0 60.0) (if (= backend FAKE) 8 3)))
  (run (scheduled (with_handlers (handlers-for backend home-dir) (scenario setting)))))


;; --- 筋書きの部品(公開 effect だけ) ----------------------------------------------------------------

(defclass [(dataclass :frozen True)] Read []
  (#^ tuple events)
  (#^ object end)
  (#^ int after))

(defk read-until [#^ SessionHandle handle #^ Callable stop #^ float timeout #^ int after]
  {:pre [(: handle SessionHandle) (: stop Callable) (: timeout float) (: after int)] :post [(: % Read)]}
  "stop(出来事の列 終わり) が真になるまで Events で読む。seq は after から欠落も重複もなく 1 つずつ増える。"
  (<- started (GetMonotonic))
  (setv seen [] end None pages 0)
  (while True
    ;; 仮想の時計は待ちが無いと進まない — 待たずに空の頁を返し続ける壊れ方を、時刻ではなく頁の数で止める。
    (+= pages 1)
    (assert (< pages MAX-PAGES) (.format "{} 頁を読んでも終わらない: {!r}" MAX-PAGES seen))
    (<- page (Events handle :after-seq after :wait-seconds PAGE-WAIT))
    (assert (isinstance page AgentEventPage) (repr page))
    (for [event page.events]
      (assert (= event.seq (+ after 1)) (.format "seq {} after {}" event.seq after))
      (setv after event.seq)
      (.append seen event))
    (setv end page.end)
    (when (stop (tuple seen) end) (return (Read (tuple seen) end after)))
    (<- now (GetMonotonic))
    (assert (< (- now started) timeout) (.format "{} 秒の内に読み終わらない: {!r}" timeout seen))))

(defn ends-of [#^ tuple events] (lfor event events :if (isinstance event AgentTurnEndEvent) event.end))
(defn tool-started [#^ tuple events end]
  (or (any (gfor event events (and (isinstance event AgentToolUseEvent) (in "Bash" (gfor call event.tool-calls call.name)))))
      (is-not end None)))

(defk launch [#^ Setting s #^ str name #^ (| str None) prompt #^ (| str None) resume-from #^ (| str None) [snapshot None]]
  {:pre [(: s Setting) (: name str) (: prompt (| str None)) (: resume-from (| str None)) (: snapshot (| str None))]
   :post [(: % SessionHandle)]}
  (<- handle (Launch name :agent-type AgentType.CLAUDE :work-dir s.work-dir :prompt prompt :model s.model
                     :lifecycle AgentSessionLifecycle.MULTI-TURN :resume-from resume-from :resume-snapshot snapshot))
  handle)


;; --- 筋書き ------------------------------------------------------------------------------------

(defk one-turn-then-resume [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "1 手番を公開 effect だけで最後まで読み、その終わりの resume_from で別の session を起こして文脈が続くことを見る。"
  (<- first (launch s "adapter-one" (remember-prompt "ALPHA-1") None))
  (<- one (read-until first (fn [events end] (is-not end None)) s.timeout -1))
  (<- outcome (AwaitResult first :timeout-seconds 1.0))
  (<- status (Monitor first))
  (<- (Stop first))
  (<- after-stop (Monitor first))
  (<- second (launch s "adapter-two" (recall-prompt) one.end.resume-from))
  (<- two (read-until second (fn [events end] (is-not end None)) s.timeout -1))
  (<- (ReleaseSession second))
  {"one" one "outcome" outcome "status" status.status "after-stop" after-stop.status "two" two})

(defk one-turn-then-stop [#^ Setting s #^ (| str None) resume-from]
  {:pre [(: s Setting) (: resume-from (| str None))] :post [(: % AgentTurnCompleted)]}
  "1 手番を最後まで読んで session を止める(process が降りて CLI が額を transcript に記すまで待つ)— 手番ごとに handler の組を
   作り直す使い手の 1 手番の形。resume-from = 続ける文脈(None = 新しい文脈)。答え = 手番の終わり。"
  (<- handle (launch s (if (is resume-from None) "adapter-cold-one" "adapter-cold-next") (reply-prompt "COLD") resume-from))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  done.end)

(defk failed-turn-after-spending [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)]}
  "額を使った後に誤りで終える手番を 1 つ最後まで読む(失敗の手番も額を運ぶかを見るため — fake の規則)。"
  (<- handle (launch s "adapter-failed" "Fail after spending 0.5" None))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  done)

(defk next-turn-input-waits-for-the-running-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)]}
  "走っている手番に NEXT_TURN の入力を届けると、その手番の終わりの後に次の手番として走る(終わりは 2 つ)。"
  (<- handle (launch s "adapter-next" (sleep-prompt 1 "FIRST") None))
  (<- _ (FollowUp handle (reply-prompt "SECOND") :input-ref "ref-second"))
  (<- record (read-until handle (fn [events end] (and (is-not end None) (= (len (ends-of events)) 2))) s.timeout -1))
  (<- (Stop handle))
  record)

(defk tool-turn-to-the-end [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)]}
  "道具を 1 度呼ぶ手番を最後まで読む(呼びの出来事と結果の出来事の id の突き合わせを見るため・agora-redesign #3518)。"
  (<- handle (launch s "adapter-tool-ids" (sleep-prompt 1 "TOOLED") None))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  done)

(defk tool-turn-with-output [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-adapter-test" :role "entry"}}
  "出力のある道具を 1 度呼ぶ手番を最後まで読む(道具の命令と出力が出来事に載るかを見るため・agora-redesign #3744)。"
  (<- prompt (echo-prompt ECHO-OUTPUT "ECHOED"))
  (<- handle (launch s "adapter-tool-content" prompt None))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  done)

(defk thinking-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-adapter-test" :role "entry"}}
  "答えの前に考えの差分を THINKING-PIECES 片出す手番を最後まで読む(考えの差分が出来事に載るかを見るため・agora-redesign #3789)。"
  (<- prompt str (thinking-prompt THINKING-PIECES "THOUGHT"))
  (<- handle (launch s "adapter-thinking" prompt None))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  done)

(defk injected-input-joins-the-running-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)]}
  (<- handle (launch s "adapter-inject" (sleep-prompt s.sleep "SLEPT") None))
  (<- started (read-until handle tool-started s.timeout -1))
  (assert (is started.end None) (repr started.end))
  (<- _ (FollowUp handle (extra-prompt) :mode TurnInputMode.INJECT :input-ref "ref-inject"))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout started.after))
  (<- (Stop handle))
  done)

(defk interrupt-keeps-the-session [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  (<- handle (launch s "adapter-interrupt" (sleep-prompt 40 "NEVER") None))
  (<- started (read-until handle tool-started s.timeout -1))
  (<- asked (Interrupt handle))
  (<- stopped (read-until handle (fn [events end] (is-not end None)) s.timeout started.after))
  (<- idle-ask (Interrupt handle))
  (<- _ (FollowUp handle (reply-prompt "AFTER")))
  (<- after (read-until handle (fn [events end] (and (is-not end None) (= (len (ends-of events)) 1))) s.timeout stopped.after))
  (<- (Stop handle))
  {"asked" asked "stopped" stopped "idle-ask" idle-ask "after" after})

(defk interrupt-after-inject-keeps-the-cli [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "読まれていない注入の在る割り込み(control の止め — #3672 の決め 6): 手番だけを止め、同じ CLI が生き残った注入を次の手番で走らせる。"
  (<- handle (launch s "adapter-interrupt-inject" (sleep-prompt 40 "NEVER") None))
  (<- started (read-until handle tool-started s.timeout -1))
  (<- _ (FollowUp handle (reply-prompt "KEPT") :mode TurnInputMode.INJECT :input-ref "ref-kept"))
  (<- asked (Interrupt handle))
  (<- after (read-until handle (fn [events end] (= (len (ends-of events)) 2)) s.timeout started.after))
  (<- (Stop handle))
  {"asked" asked "after" after})

(defk stop-discards-waiting-inputs [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  (<- handle (launch s "adapter-stop" (sleep-prompt 40 "NEVER") None))
  (<- started (read-until handle tool-started s.timeout -1))
  (<- _ (FollowUp handle (reply-prompt "LATER") :input-ref "ref-waiting"))
  (<- (Stop handle))
  (<- page (Events handle :after-seq started.after))
  (setv refused None)
  (try
    (<- (FollowUp handle (reply-prompt "TOO-LATE")))
    (except [error SessionNotFoundError] (setv refused error)))
  {"page" page "refused" refused})

(defk refusals [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "できないことは黙って別の事をせず、型で断る。"
  (<- handle (launch s "adapter-refusals" None None))
  (setv found {})
  (try (<- (Capture handle)) (except [error AgentCapabilityUnsupportedError] (setv (get found "capture") error)))
  (try (<- (FollowUp handle (extra-prompt) :mode TurnInputMode.INJECT))
       (except [error NoTurnInFlightError] (setv (get found "inject") error)))
  (try (<- (launch s "adapter-missing" None "0b7a3e8e-2d0c-4a55-9d3f-6c1a3b7a0f11"))
       (except [error ResumeTargetNotFoundError] (setv (get found "resume") error)))
  (try (<- (launch s "adapter-malformed" (reply-prompt "X") "not-a-uuid"))
       (except [error ResumeTargetNotFoundError] (setv (get found "malformed") error)))
  (<- page (Events handle))
  (setv (get found "idle-page") page)
  (<- (Stop handle))
  found)


(defk busy-context-refused [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "文脈で手番が走っている間に、同じ文脈を続ける別の session を起こすと TurnInFlightError で断る(起動の失敗 AgentLaunchError ではない —
   agora-redesign #789)。走っている手番は断りの後もそのまま走る。"
  (<- handle (launch s "adapter-busy-a" (reply-prompt "FIRST") None))
  (<- first (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- _ (FollowUp handle (sleep-prompt 40 "NEVER")))
  (<- started (read-until handle tool-started s.timeout first.after))
  (var refused None)
  (try
    (<- (launch s "adapter-busy-b" (reply-prompt "SECOND") first.end.resume-from))
    (except [error AgentError] (:= refused error)))
  (<- page (Events handle :after-seq started.after))
  (<- (Stop handle))
  {"context" first.end.resume-from "refused" refused "page" page})


(deftest test-headless-busy-context-is-refused-as-turn-in-flight-fake [tmp-path]
  (val seen (run-on FAKE tmp-path busy-context-refused))
  (val refused (get seen "refused"))
  (assert (isinstance refused TurnInFlightError) (repr refused))
  ;; 反例: 起動の失敗(AgentLaunchError の族)としては名乗らない — 呼び手が「起こせなかった」と数え違えない。
  (assert (not (isinstance refused AgentLaunchError)) (repr refused))
  (assert (= #(refused.session-id refused.context-id) #("adapter-busy-b" (get seen "context"))) (repr refused))
  ;; 走っている手番は断りで終わらない(終わりはまだ無い)。
  (assert (is (. (get seen "page") end) None) (repr (get seen "page"))))


(defk lost-turn-continues [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "手番の途中に runtime が消えると AgentTurnLost で終わり、次の手番は同じ session のまま続く(起こし直しは層 2 の中)。"
  (<- handle (launch s "adapter-lost" (reply-prompt "FIRST") None))
  (<- first (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- _ (FollowUp handle (sleep-prompt 40 "NEVER")))
  (<- started (read-until handle tool-started s.timeout first.after))
  (<- dropped (ClaudeDropProcess first.end.resume-from))
  (<- lost (read-until handle (fn [events end] (is-not end None)) s.timeout started.after))
  (<- _ (FollowUp handle (reply-prompt "AGAIN")))
  (<- again (read-until handle (fn [events end] (is-not end None)) s.timeout lost.after))
  (<- (Stop handle))
  {"dropped" dropped "lost" lost "again" again "context" first.end.resume-from})

(defk export-after-one-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "手番 1 を走らせ、その文脈の写しを ExportContextEffect で取り出す(agora-redesign #731)。手元に無い文脈・綴りの外の id は None。"
  (<- handle (launch s "memory-one" (remember-prompt "ALPHA-1") None))
  (<- one (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop handle))
  (val first-end one.end)
  (assert (isinstance first-end AgentTurnCompleted) (repr first-end))
  (val context first-end.resume-from)
  (<- copied (ExportContextEffect :agent-type AgentType.CLAUDE :work-dir s.work-dir :context-id context))
  (<- missing (ExportContextEffect :agent-type AgentType.CLAUDE :work-dir s.work-dir
                                   :context-id "0b7a3e8e-2d0c-4a55-9d3f-6c1a3b7a0f11"))
  (<- malformed (ExportContextEffect :agent-type AgentType.CLAUDE :work-dir s.work-dir :context-id "../not-a-uuid"))
  {"context" context "copied" copied "missing" missing "malformed" malformed})

(defk continue-from-copy [#^ Setting s #^ str context #^ (| str None) copied #^ bool prompt-first]
  {:pre [(: s Setting) (: context str) (: copied (| str None)) (: prompt-first bool)] :post [(: % dict)]}
  "空の家で、写しを持ち込んで文脈を続ける。prompt-first = Launch に prompt を載せる(偽なら prompt なしで起こして FollowUp で頼む
   — 手元の在否の事前の確かめは写しが在る時は飛ぶ)。手番 2 つ目(持ち込みなし)も続く。"
  (<- handle (launch s "memory-two" (if prompt-first (recall-prompt) None) context copied))
  (when (not prompt-first)
    (<- (FollowUp handle (recall-prompt))))
  (<- two (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (FollowUp handle (reply-prompt "AGAIN")))
  (<- three (read-until handle (fn [events end] (and (is-not end None) (= (len (ends-of events)) 1))) s.timeout two.after))
  (<- (Stop handle))
  {"two" two "three" three})

(defn check-carried [#^ dict found #^ str context]
  (setv ends (+ (ends-of (. (get found "two") events)) (ends-of (. (get found "three") events))))
  (assert (= (len ends) 2) (repr ends))
  (setv #(recalled again) ends)
  (assert (and (isinstance recalled AgentTurnCompleted) (isinstance again AgentTurnCompleted)) (repr ends))
  (assert (in CODEWORD recalled.result-text) (repr ends))
  (assert (in "AGAIN" again.result-text) (repr ends))
  (assert (= recalled.resume-from again.resume-from context) (repr ends)))

(defn check-memory-carry [#^ str backend #^ Path tmp-path]
  (setv out (run-on backend tmp-path export-after-one-turn))
  (setv context (get out "context") copied (get out "copied"))
  (assert (and (isinstance copied str) (.strip copied)) (repr out))
  (assert (is (get out "missing") None) (repr out))
  (assert (is (get out "malformed") None) (repr out))
  ;; 写しなしでは、空の家に文脈が無い — 黙って新しく始めずに断る。
  (with [info (pytest.raises ResumeTargetNotFoundError)]
    (run-on backend tmp-path (fn [s] (continue-from-copy s context None True)) "home-without-copy"))
  (assert (= info.value.resume-from context))
  (check-carried (run-on backend tmp-path (fn [s] (continue-from-copy s context copied True)) "home-with-copy") context)
  (check-carried (run-on backend tmp-path (fn [s] (continue-from-copy s context copied False)) "home-with-copy-no-prompt")
                 context))

(deftest test-headless-memory-carried-into-an-empty-home-fake [tmp-path]
  ;; agora-redesign #731: agent の記憶(文脈の transcript)を runtime の家の外へ写して、空の家で続ける。
  (check-memory-carry FAKE tmp-path))

(deftest test-headless-memory-carried-into-an-empty-home-stub [tmp-path]
  (check-memory-carry STUB tmp-path))

(deftest test-resume-snapshot-is-a-typed-field-and-turnless-handlers-refuse-it [tmp-path]
  ;; resume_snapshot は resume_from の文脈の写し: resume_from なし・空は作れない。持ち込めない handler は黙って捨てずに断る。
  (import doeff_agents.effects [LaunchEffect refuse-turn-capabilities])
  (import doeff_agents.handlers.codex [codex-handler])
  (for [#(resume-from snapshot) [#(None "x") #("0b7a3e8e-2d0c-4a55-9d3f-6c1a3b7a0f11" "") #("0b7a3e8e-2d0c-4a55-9d3f-6c1a3b7a0f11" "  ")]]
    (with [(pytest.raises ValueError)]
      (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :resume-from resume-from
                    :resume-snapshot snapshot)))
  (with [info (pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities (LaunchEffect :session-name "x" :agent-type AgentType.CODEX :work-dir tmp-path
                                            :resume-from "abc" :resume-snapshot "copy")
                              :handler "t"))
  (assert (in "resume_from" info.value.capability) info.value.capability)
  (with [info (pytest.raises AgentCapabilityUnsupportedError)]
    (run (scheduled (with_handlers [(codex-handler)]
                                   (ExportContextEffect :agent-type AgentType.CODEX :work-dir tmp-path :context-id "abc")))))
  (assert (= info.value.capability "ExportContextEffect") info.value.capability))

(defk reader [#^ SessionHandle handle #^ float timeout]
  {:pre [(: handle SessionHandle) (: timeout float)] :post [(: % Read)]}
  (<- record (read-until handle (fn [events end] (is-not end None)) timeout -1))
  record)

(defk concurrent-reader-and-interrupt [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)]}
  "1 つの task が Events で待つ間に別の task が割り込み・入力を届けても、出来事は欠落も重複もなく終わりは手番ごとに 1 つ。"
  (<- handle (launch s "adapter-concurrent" (sleep-prompt 40 "NEVER") None))
  (<- _ (read-until handle tool-started s.timeout -1))
  (<- task (Spawn (reader handle s.timeout)))
  ;; 読み手が層 2 の待ちに入るまで譲る → その間に割り込み、Monitor で先に終わりを読む(読み手は同じ行と終わりを持って戻る)。
  (<- (Delay 0.5))
  (<- _ (FollowUp handle (reply-prompt "NEXT") :input-ref "ref-next"))
  (<- asked (Interrupt handle))
  (<- _ (Monitor handle))
  (<- record (Wait task))
  (<- all (Events handle :after-seq -1))
  (<- (Stop handle))
  {"asked" asked "record" record "all" all})


;; --- 検 -----------------------------------------------------------------------------------------

(defn check-lost [#^ dict seen]
  (assert (is (get seen "dropped") True))
  (setv lost (. (get seen "lost") end) again (. (get seen "again") end))
  (assert (isinstance lost AgentTurnLost) (repr lost))
  (assert (= lost.resume-from (get seen "context")))
  (assert (isinstance again AgentTurnCompleted) (repr again))
  (assert (in "AGAIN" again.result-text) again.result-text))

(defn check-concurrent [#^ dict seen]
  (assert (is (get seen "asked") True))
  (setv events (. (get seen "all") events))
  (assert (= (lfor event events event.seq) (list (range (len events)))) (repr events))
  (setv ends (ends-of events))
  (assert (= (len ends) 2) (repr ends))
  (assert (isinstance (get ends 0) AgentTurnInterrupted) (repr ends))
  ;; 次の手番の入力(FollowUp の既定 — 注入ではない)は読まれていない注入に数えないので、割り込みは SIGINT の形 — CLI は降り、次の手番は
  ;; 新しい CLI が続きから走らせる(残る形は check-interrupt-after-inject)。
  (assert (is (. (get ends 0) cli-kept) False) (repr ends))
  (assert (isinstance (get ends 1) AgentTurnCompleted) (repr ends))
  (assert (in "ref-next" (. (get ends 1) input-refs)) (repr ends)))


(defn check-one-turn-then-resume [#^ dict seen]
  (setv one (get seen "one") two (get seen "two"))
  (assert (isinstance one.end AgentTurnCompleted) (repr one.end))
  (assert (in "ALPHA-1" one.end.result-text) one.end.result-text)
  (assert (any (gfor event one.events (isinstance event AgentTextEvent))) (repr one.events))
  (assert (= (ends-of one.events) [one.end]))
  (assert (= (. (get seen "outcome") turn-end) one.end) (repr (get seen "outcome")))
  (assert (= (. (get seen "outcome") status) AwaitStatus.AWAITING-INPUT))
  (assert (= (get seen "status") SessionStatus.BLOCKED))
  (assert (= (get seen "after-stop") SessionStatus.STOPPED))
  (assert (isinstance two.end AgentTurnCompleted) (repr two.end))
  (assert (in CODEWORD two.end.result-text) two.end.result-text)
  (assert (= two.end.resume-from one.end.resume-from)))

(defn check-next-turn [#^ Read record]
  (setv ends (ends-of record.events))
  (assert (= (len ends) 2) (repr ends))
  (assert (all (gfor end ends (isinstance end AgentTurnCompleted))) (repr ends))
  (assert (in "SECOND" (. (get ends 1) result-text)) (repr ends))
  (assert (in "ref-second" (. (get ends 1) input-refs)) (repr ends)))

(defn check-inject [#^ Read done]
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  (assert (in EXTRA-WORD done.end.result-text) done.end.result-text)
  (assert (in #("ref-inject" InputFateState.STARTED)
              (lfor event done.events :if (isinstance event AgentInputFateEvent) #(event.input-ref event.state)))
          (repr done.events)))

(defn check-interrupt [#^ dict seen]
  (assert (is (get seen "asked") True))
  (assert (isinstance (. (get seen "stopped") end) AgentTurnInterrupted) (repr (get seen "stopped")))
  ;; 読まれていない入力の無い割り込みは SIGINT の形 — CLI は降り、会話の文脈だけが残る(次の手番は新しい CLI が続きから)。
  (assert (is (. (get seen "stopped") end cli-kept) False) (repr (get seen "stopped")))
  (assert (is (get seen "idle-ask") False))
  (setv after (. (get seen "after") end))
  (assert (isinstance after AgentTurnCompleted) (repr after))
  (assert (in "AFTER" after.result-text) after.result-text))

(defn check-interrupt-after-inject [#^ dict seen]
  (assert (is (get seen "asked") True))
  (setv ends (ends-of (. (get seen "after") events)))
  (assert (= (len ends) 2) (repr ends))
  (assert (isinstance (get ends 0) AgentTurnInterrupted) (repr ends))
  ;; 同じ CLI が会話に残る — 使い手は CLI と、その CLI の借りた資格を持ち続けてよい。
  (assert (is (. (get ends 0) cli-kept) True) (repr ends))
  (assert (= (. (get ends 0) surviving-refs) #("ref-kept")) (repr ends))
  (assert (isinstance (get ends 1) AgentTurnCompleted) (repr ends)))

(defn check-stop [#^ dict seen]
  (setv events (. (get seen "page") events))
  (assert (any (gfor end (ends-of events) (isinstance end AgentTurnInterrupted))) (repr events))
  ;; 止め(Stop)は会話の CLI を降ろす。
  (assert (all (gfor end (ends-of events) :if (isinstance end AgentTurnInterrupted) (is end.cli-kept False))) (repr events))
  (assert (in #("ref-waiting" InputFateState.DISCARDED)
              (lfor event events :if (isinstance event AgentInputFateEvent) #(event.input-ref event.state)))
          (repr events))
  (assert (is-not (get seen "refused") None)))

(defn check-refusals [#^ dict found]
  (assert (= (sorted found) ["capture" "idle-page" "inject" "malformed" "resume"]) (repr found))
  (assert (= (. (get found "resume") resume-from) "0b7a3e8e-2d0c-4a55-9d3f-6c1a3b7a0f11"))
  (setv page (get found "idle-page"))
  (assert (= page (AgentEventPage :events #() :next-seq -1 :end None)) (repr page)))


(deftest test-headless-one-turn-and-resume-fake [tmp-path]
  (val seen (run-on FAKE tmp-path one-turn-then-resume))
  (check-one-turn-then-resume seen)
  ;; 偽の CLI の完了は token の数を 1 つも名乗らない → usage = None(0 を発明しない・agora-redesign #766)。
  (assert (is (. (get seen "one") end usage) None) (repr (. (get seen "one") end))))

(deftest test-headless-one-turn-and-resume-stub [tmp-path]
  (val seen (run-on STUB tmp-path one-turn-then-resume))
  (check-one-turn-then-resume seen)
  ;; 手番の使った token の数は層 2 の result の行の usage から層 3 の終わりへ運ぶ(stub は input 1・output 1 だけを名乗る —
  ;; 名乗らない cache の欄は None のまま・agora-redesign #766)。手番の額は層 2 が CLI の累積の額から手番の分に直した値:
  ;; stub の累積は 1 手番目 0.25・続きの 2 手番目 0.5 だが、どちらの手番の額も 0.25(agora-redesign #883)。
  (assert (= (. (get seen "one") end usage) (AgentTurnUsage :input-tokens 1 :output-tokens 1 :cost-usd 0.25))
          (repr (. (get seen "one") end)))
  (assert (= (. (get seen "two") end usage) (AgentTurnUsage :input-tokens 1 :output-tokens 1 :cost-usd 0.25))
          (repr (. (get seen "two") end))))

(deftest test-headless-each-turn-in-a-new-handler-carries-its-own-cost-stub [tmp-path]
  ;; agora の worker の形: 手番ごとに handler の組(= 層 2 の host)を作り直し、2 手番目からはその host の知らない続き
  ;; (毎回 cold resume)。層 2 は transcript の最後の cost-state の額を起点に読むので、どの手番の額も累積(0.25 → 0.5 → 0.75)
  ;; ではなく手番の分 0.25(#883)。
  (val one (run-on STUB tmp-path (fn [s] (one-turn-then-stop s None))))
  (val two (run-on STUB tmp-path (fn [s] (one-turn-then-stop s one.resume-from))))
  (val three (run-on STUB tmp-path (fn [s] (one-turn-then-stop s two.resume-from))))
  (assert (= [one.usage two.usage three.usage]
             (* [(AgentTurnUsage :input-tokens 1 :output-tokens 1 :cost-usd 0.25)] 3))
          (repr [one two three])))

(deftest test-headless-failed-turn-carries-its-cost-fake [tmp-path]
  ;; 誤りで終えた手番も層 2 の額を AgentTurnUsage.cost_usd へ運ぶ。token の数を名乗らなくても額が在れば usage は None にしない
  ;; (4 欄と額がすべて無い時だけ None・agora-redesign #883)。
  (val done (run-on FAKE tmp-path failed-turn-after-spending))
  (assert (isinstance done.end AgentTurnFailed) (repr done.end))
  (assert (= done.end.detail "spent then failed") (repr done.end))
  (assert (= done.end.usage (AgentTurnUsage :cost-usd 0.5)) (repr done.end)))

(deftest test-headless-one-turn-and-resume-real [tmp-path]
  {:marks ["e2e" "slow"]
   :skip-if (not (.get os.environ "DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR"))
   :skip-reason "本物の claude の筋書きは env DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR に個人の profile の CLAUDE_CONFIG_DIR を置いた時だけ走る"}
  (check-one-turn-then-resume (run-on REAL tmp-path one-turn-then-resume)))

(defk check-tool-calls-meet-results [#^ Read done]
  {:pre [(: done Read)] :post [(: % (type None))] :tags {:context "headless-adapter-test" :role "judgment"}}
  "AgentToolUseEvent の tool_calls の id が、後に続く AgentToolResultEvent の答え(answers)の id と突き合う(どの結果も前の呼びの id を
   名指し、どの呼びにも結果が届く)。呼びの id を落とす adapter では突き合わず赤(agora-redesign #3518)。"
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  (val uses (lfor event done.events :if (isinstance event AgentToolUseEvent) event))
  (val results (lfor event done.events :if (isinstance event AgentToolResultEvent) event))
  (assert (and uses results) (repr done.events))
  (val called (lfor event uses call event.tool-calls call.id))
  (assert (all called) (repr uses))
  (val answered (lfor event results answer event.answers answer.id))
  (assert (= (sorted called) (sorted answered)) (repr #(uses results)))
  (for [result results]
    (assert (all (gfor answer result.answers
                       (any (gfor use uses :if (< use.seq result.seq) call use.tool-calls (= call.id answer.id)))))
            (repr #(uses result))))
  None)

(deftest test-headless-tool-call-ids-meet-their-results-fake [tmp-path]
  (<- (check-tool-calls-meet-results (run-on FAKE tmp-path tool-turn-to-the-end))))

(deftest test-headless-tool-call-ids-meet-their-results-stub [tmp-path]
  (<- (check-tool-calls-meet-results (run-on STUB tmp-path tool-turn-to-the-end))))

(defk check-tool-events-carry-the-command-and-the-output [#^ Read done]
  {:pre [(: done Read)] :post [(: % (type None))] :tags {:context "headless-adapter-test" :role "judgment"}}
  "AgentToolUseEvent の呼びが道具の命令(input)を、AgentToolResultEvent の答えが結果の中身(本文・誤りの印・text でない block の種類)を
   運ぶ。命令と出力を捨てる adapter では会話の画面の道具の行を開けず赤(agora-redesign #3744)。"
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  (val calls (lfor event done.events :if (isinstance event AgentToolUseEvent) call event.tool-calls call))
  (val results (lfor event done.events :if (isinstance event AgentToolResultEvent) event))
  (assert (= (lfor call calls #(call.name (dict call.input))) [#("Bash" {"command" (+ "echo " ECHO-OUTPUT)})]) (repr calls))
  (val answers (lfor event results answer event.answers answer))
  (assert (= (lfor answer answers #(answer.id answer.text answer.is-error answer.non-text-kinds))
             [#((. (get calls 0) id) ECHO-OUTPUT False #())])
          (repr answers))
  None)

(defk check-thinking-deltas-come-before-the-text [#^ Read done]
  {:pre [(: done Read)] :post [(: % (type None))] :tags {:context "headless-adapter-test" :role "judgment"}}
  "考えている間の差分(層 2 の PartialMessage の種類 THINKING)が、片ごとに考えの出来事(AgentThinkingDeltaEvent — 考えの文字列つき)として、
   確定の本文の出来事より前に出る。考えの差分を落とす adapter では 0 で赤(agora-redesign #3789 — 本文が 66 秒後まで来ない手番で、画面が
   考えている事を出せなかった)。"
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  (val thought (lfor #(index event) (enumerate done.events) :if (isinstance event AgentThinkingDeltaEvent) #(index event.text)))
  (val texts (lfor #(index event) (enumerate done.events) :if (isinstance event AgentTextEvent) index))
  (assert (= (lfor #(_ text) thought text) (* [THINKING-PIECE] THINKING-PIECES)) (repr done.events))
  (assert (and texts (< (max (lfor #(index _) thought index)) (get texts 0))) (repr done.events))
  None)

(deftest test-headless-carries-the-thinking-deltas-before-the-text-fake [tmp-path]
  (<- (check-thinking-deltas-come-before-the-text (run-on FAKE tmp-path thinking-turn))))

(deftest test-headless-carries-the-thinking-deltas-before-the-text-stub [tmp-path]
  (<- (check-thinking-deltas-come-before-the-text (run-on STUB tmp-path thinking-turn))))

(deftest test-headless-tool-events-carry-the-command-and-the-output-fake [tmp-path]
  (<- (check-tool-events-carry-the-command-and-the-output (run-on FAKE tmp-path tool-turn-with-output))))

(deftest test-headless-tool-events-carry-the-command-and-the-output-stub [tmp-path]
  (<- (check-tool-events-carry-the-command-and-the-output (run-on STUB tmp-path tool-turn-with-output))))

(defk check-turn-end-carries-the-last-call [#^ Read done]
  {:pre [(: done Read)] :post [(: % (type None))] :tags {:context "headless-adapter-test" :role "judgment"}}
  "手番の終わり(AgentTurnCompleted — 頁の end と AgentTurnEndEvent の end)が、本体の会話の最後の呼びの usage(AgentTurnUsage — 額は
   呼びごとには分からないので None)と model、model ごとの窓(context の大きさの上限・出力の上限)を運ぶ。道具の呼びの行の後の最後の
   本文の行の usage を取り、subagent の行(替え玉の CLI は道具の途中に 1 行出す)は取らない(#3744)。"
  (assert (isinstance done.end AgentTurnCompleted) (repr done.end))
  (assert (= (ends-of done.events) [done.end]) (repr done.events))
  (assert (= done.end.last-call-usage (AgentTurnUsage :input-tokens 4 :output-tokens 1 :cache-write-tokens 20
                                                      :cache-read-tokens 1010))
          (repr done.end))
  (assert (= done.end.last-call-model CALL-MODEL) (repr done.end))
  (assert (= (lfor window done.end.model-windows #(window.model window.context-window window.max-output-tokens))
             [#(CALL-MODEL 200000 32000) #(SUBAGENT-MODEL 100000 None)])
          (repr done.end))
  None)

(deftest test-headless-turn-end-carries-the-last-call-and-the-model-windows-fake [tmp-path]
  (<- (check-turn-end-carries-the-last-call (run-on FAKE tmp-path tool-turn-with-output))))

(deftest test-headless-turn-end-carries-the-last-call-and-the-model-windows-stub [tmp-path]
  (<- (check-turn-end-carries-the-last-call (run-on STUB tmp-path tool-turn-with-output))))

(deftest test-headless-next-turn-input-waits-fake [tmp-path]
  (check-next-turn (run-on FAKE tmp-path next-turn-input-waits-for-the-running-turn)))

(deftest test-headless-next-turn-input-waits-stub [tmp-path]
  (check-next-turn (run-on STUB tmp-path next-turn-input-waits-for-the-running-turn)))

(deftest test-headless-inject-fake [tmp-path]
  (check-inject (run-on FAKE tmp-path injected-input-joins-the-running-turn)))

(deftest test-headless-inject-stub [tmp-path]
  (check-inject (run-on STUB tmp-path injected-input-joins-the-running-turn)))

(deftest test-headless-interrupt-fake [tmp-path]
  (check-interrupt (run-on FAKE tmp-path interrupt-keeps-the-session)))

(deftest test-headless-interrupt-stub [tmp-path]
  (check-interrupt (run-on STUB tmp-path interrupt-keeps-the-session)))

(deftest test-headless-interrupt-after-inject-keeps-the-cli-fake [tmp-path]
  (check-interrupt-after-inject (run-on FAKE tmp-path interrupt-after-inject-keeps-the-cli)))

(deftest test-headless-interrupt-after-inject-keeps-the-cli-stub [tmp-path]
  (check-interrupt-after-inject (run-on STUB tmp-path interrupt-after-inject-keeps-the-cli)))

(deftest test-headless-stop-discards-waiting-fake [tmp-path]
  (check-stop (run-on FAKE tmp-path stop-discards-waiting-inputs)))

(deftest test-headless-refusals-fake [tmp-path]
  (check-refusals (run-on FAKE tmp-path refusals)))

(deftest test-headless-refusals-stub [tmp-path]
  (check-refusals (run-on STUB tmp-path refusals)))

(deftest test-headless-stop-discards-waiting-stub [tmp-path]
  (check-stop (run-on STUB tmp-path stop-discards-waiting-inputs)))

(deftest test-headless-lost-turn-continues-fake [tmp-path]
  (check-lost (run-on FAKE tmp-path lost-turn-continues)))

(deftest test-headless-lost-turn-continues-stub [tmp-path]
  (check-lost (run-on STUB tmp-path lost-turn-continues)))

(deftest test-headless-concurrent-reader-and-interrupt-fake [tmp-path]
  (check-concurrent (run-on FAKE tmp-path concurrent-reader-and-interrupt)))


;; --- 最後の本文の差分(agora-redesign #3628)--------------------------------------------------------------------

(val STREAMED-TEXT "streamed final reply")
(val STREAMED-PIECES 5)

(defk streamed-reply [#^ str text #^ tuple memory]
  {:pre [(: text str) (: memory tuple)] :post [(: % FakeReply)] :tags {:context "doeff-agents" :role "judgment"}}
  "fake の返事(respond の形): 道具を 1 度呼んでから、最後の本文を STREAMED-PIECES 片の差分で出す手番 — 上の層の模擬が、書きかけの本文の
   出来事を確定の本文より先に読めるかを確かめる材料を、この adapter が行の写しのまま運ぶかを見るため。本文の語 plain は差分なし(既定の返事)。"
  (if (= text "plain")
      (FakeReply STREAMED-TEXT :tool-seconds 1.0)
      (FakeReply STREAMED-TEXT :tool-seconds 1.0 :deltas STREAMED-PIECES)))

(defk streamed-turn [#^ Path work #^ str prompt]
  {:pre [(: work Path) (: prompt str)] :post [(: % Read)] :tags {:context "doeff-agents" :role "program"}}
  "本文 prompt の 1 手番を公開 effect だけで最後まで読んで止める(差分の出来事と確定の本文の出来事の並びを見るため)。"
  (<- handle (Launch (+ "adapter-streamed-" prompt) :agent-type AgentType.CLAUDE :work-dir work :prompt prompt
                     :lifecycle AgentSessionLifecycle.MULTI-TURN))
  (<- done (read-until handle (fn [events end] (is-not end None)) 60.0 -1))
  (<- (Stop handle))
  done)

(deftest test-headless-carries-the-final-text-deltas-before-the-final-text-fake [tmp-path]
  ;; fake の返事が最後の本文を deltas 片の差分(層 2 の PartialMessage の text_delta)で出すと、adapter の行の写しを通って、上の層は
  ;; 書きかけの本文の出来事(AgentTextDeltaEvent)を片の数だけ、確定の本文の出来事(AgentTextEvent — 1 つ)より前に読む。片の連結は
  ;; 確定の本文と同じ。既定の返事(deltas 0)は差分の出来事を 1 つも出さない(agora-redesign #3628 — 上の層の模擬が「文の途中を画面へ
  ;; 流す」を確かめる材料)。
  (import doeff_claude_code.fake [FakeClaudeWorld])
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (val world (FakeClaudeWorld :respond streamed-reply))
  (val run-turn (fn [prompt]
                  (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))]
                                                    (fake-headless-claude-handlers None (str (/ tmp-path "home")) :world world
                                                                                   :env {} :settings {}))
                                                 (streamed-turn work prompt))))))
  (val streamed (run-turn "streamed"))
  (val plain (run-turn "plain"))
  (assert (isinstance streamed.end AgentTurnCompleted) (repr streamed.end))
  (assert (= streamed.end.result-text STREAMED-TEXT) (repr streamed.end))
  (val events streamed.events)
  (val delta-indexes (lfor #(index event) (enumerate events) :if (isinstance event AgentTextDeltaEvent) index))
  (val text-indexes (lfor #(index event) (enumerate events) :if (isinstance event AgentTextEvent) index))
  (assert (= (len delta-indexes) STREAMED-PIECES) events)
  (assert (= (.join "" (lfor index delta-indexes (. (get events index) text))) STREAMED-TEXT) events)
  (assert (= (lfor index text-indexes (. (get events index) text)) [STREAMED-TEXT]) events)
  (assert (< (max delta-indexes) (get text-indexes 0)) events)
  ;; 既定の返事: 差分の出来事 0・確定の本文の出来事 1 つ。
  (assert (isinstance plain.end AgentTurnCompleted) (repr plain.end))
  (assert (= (lfor event plain.events :if (isinstance event AgentTextDeltaEvent) event) []) plain.events)
  (assert (= (lfor event plain.events :if (isinstance event AgentTextEvent) event.text) [STREAMED-TEXT]) plain.events))


(deftest test-headless-adapter-knows-no-process-and-no-session-host []
  ;; O7 / O5 の構造の検: adapter は process の寿命も session host も知らない。子 process・socket・信号・起動の引数の綴り・
  ;; sessionhost の import が adapter の code に無い(註は数えない)。
  (setv code (lfor line (.splitlines (.read-text ADAPTER-PATH :encoding "utf-8"))
                   :setv stripped (.strip line)
                   :if (and stripped (not (.startswith stripped ";")))
                   stripped))
  (setv text (.join "\n" code))
  (for [needle ["subprocess" "socket" "Popen" "signal" "os.kill" "doeff_agents.sessionhost" "--resume" "--session-id"
                "stream-json" "\"-p\"" ".pid" "doeff_claude_code.handler" "doeff_claude_code.fake" "ClaudeCodeHost"]]
    (assert (not-in needle text) (.format "adapter の code に {!r} が在る" needle))))


(deftest test-terminal-handlers-refuse-turn-fields [tmp-path]
  ;; resume_from と INJECT を持たない端末の handler は、黙って新しく始めたり keys にしたりせずに断る。
  (import doeff_agents.effects [LaunchEffect FollowUpEffect refuse-turn-capabilities])
  (setv launch-effect (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :resume-from "abc"))
  (with [(pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities launch-effect :handler "t"))
  (with [(pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities (FollowUpEffect :handle (SessionHandle "x") :message "m" :mode TurnInputMode.INJECT) :handler "t"))
  (refuse-turn-capabilities (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path) :handler "t")
  (refuse-turn-capabilities (FollowUpEffect :handle (SessionHandle "x") :message "m") :handler "t")
  (import doeff_agents.handlers.testing [MockAgentHandler])
  (with [(pytest.raises AgentCapabilityUnsupportedError)]
    (.handle-launch (MockAgentHandler) launch-effect))
  ;; 手番の資格の参照(turn_credential_ref)を引き換えて置けない端末の handler は、家の資格で黙って走らせずに断る(#665・#979)。
  (with [(pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                            :turn-credential-ref "lease-1")
                              :handler "t")))


;; --- 入力の前に runtime を事前起動して待たせる(WarmSessionEffect)-----------------------------------------------------
;; prompt なしで起動した session の runtime(層 2 の CLI)を、最初の入力の前に起動して待たせる — 起動してから入力を受けられるまでの
;; 秒を入力の前に済ませる。最初のターンはその runtime を使い回す(層 2 の起動回数が増えない)。

(defrecord WarmedSession
  "事前起動してから最初のターンを走らせたシナリオの観測: warmed・again = 事前起動の結果 2 回・first = 最初のターンの読み取り結果・
   view = 最初のターンの後の層 2 の process の状態・busy = ターンの動いている間の事前起動の結果。"
  (#^ bool warmed)
  (#^ bool again)
  (#^ Read first)
  (#^ (| LiveProcess NoLiveProcess) view)
  (#^ bool busy))

(defk warm-then-first-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % WarmedSession)] :tags {:context "headless-adapter-test" :role "program"}}
  "prompt なしで起動した session を WarmSession で事前起動し、最初の入力(FollowUp)のターンがその runtime を使い回すかを見るため。"
  (<- handle (launch s "adapter-warm" None None))
  (<- warmed (WarmSession handle))
  (<- again (WarmSession handle))
  (<- (FollowUp handle (remember-prompt "ALPHA-1")))
  (<- first (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- view (ClaudeLiveProcess first.end.resume-from))
  (<- (FollowUp handle (sleep-prompt 40 "NEVER")))
  (<- _ (read-until handle tool-started s.timeout first.after))
  (<- busy (WarmSession handle))
  (<- (Stop handle))
  (WarmedSession :warmed warmed :again again :first first :view view :busy busy))

(defk check-warmed [#^ WarmedSession seen]
  {:pre [(: seen WarmedSession)] :post [(: % None)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "事前起動してから最初のターンを走らせた観測が、事前起動の約束(使い回し・ターンの間は事前起動しない)を満たすかを確かめるため。"
  (assert (and seen.warmed seen.again) (repr seen))
  (val ends (ends-of seen.first.events))
  (assert (= (len ends) 1) (repr ends))
  (val end (get ends 0))
  (assert (isinstance end AgentTurnCompleted) (repr end))
  (assert (in "ALPHA-1" end.result-text) (repr end))
  ;; 最初のターンは事前起動した runtime を使った(層 2 の起動回数 1)。
  (assert (= seen.view (LiveProcess :launches 1)) (repr seen.view))
  ;; ターンの動いている間は事前起動するものが無い(偽)。
  (assert (is seen.busy False) (repr seen.busy))
  None)

(deftest test-headless-warm-session-serves-the-first-turn-fake [tmp-path]
  (<- (check-warmed (run-on FAKE tmp-path warm-then-first-turn))))

(deftest test-headless-warm-session-serves-the-first-turn-stub [tmp-path]
  (<- (check-warmed (run-on STUB tmp-path warm-then-first-turn))))


(defrecord WarmedContinuation
  "前の文脈を続ける session を事前起動したシナリオの観測: warmed = 事前起動の結果・done = 続きのターンの読み取り結果・view = 続きの
   ターンの後の層 2 の process の状態・context = 続けた文脈の id。"
  (#^ bool warmed)
  (#^ Read done)
  (#^ (| LiveProcess NoLiveProcess) view)
  (#^ str context))

(defk warm-a-continuation [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % WarmedContinuation)] :tags {:context "headless-adapter-test" :role "program"}}
  "1 ターンを走らせて止めた文脈を、prompt なしで起動した別の session(resume_from)で事前起動し、続きのターンがその runtime を使うかを
   見るため。"
  (<- first-handle (launch s "adapter-warm-first" (remember-prompt "ALPHA-1") None))
  (<- one (read-until first-handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- (Stop first-handle))
  (val context one.end.resume-from)
  (<- handle (launch s "adapter-warm-next" None context))
  (<- warmed (WarmSession handle))
  (<- launched (ClaudeLiveProcess context))
  (<- (FollowUp handle (recall-prompt)))
  (<- done (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- view (ClaudeLiveProcess context))
  (assert (= view launched) (repr #(launched view)))
  (<- (Stop handle))
  (WarmedContinuation :warmed warmed :done done :view view :context context))

(defk check-warmed-continuation [#^ WarmedContinuation seen]
  {:pre [(: seen WarmedContinuation)] :post [(: % None)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "続きを事前起動した観測が、続きのターンが事前起動した runtime を使った事を満たすかを確かめるため。"
  (assert seen.warmed (repr seen))
  (val ends (ends-of seen.done.events))
  (assert (and (= (len ends) 1) (isinstance (get ends 0) AgentTurnCompleted)) (repr ends))
  (assert (in CODEWORD (. (get ends 0) result-text)) (repr ends))
  (assert (= (. (get ends 0) resume-from) seen.context) (repr ends))
  ;; 1 ターン目の runtime(止めて終了した)・事前起動した runtime の 2 つ — 続きのターンは事前起動した runtime を使い、3 つ目を起動
  ;; しない。
  (assert (= seen.view (LiveProcess :launches 2)) (repr seen.view))
  None)

(deftest test-headless-warm-a-continuation-fake [tmp-path]
  (<- (check-warmed-continuation (run-on FAKE tmp-path warm-a-continuation))))

(deftest test-headless-warm-a-continuation-stub [tmp-path]
  (<- (check-warmed-continuation (run-on STUB tmp-path warm-a-continuation))))


(defrecord WarmedThenStopped
  "事前起動した session をターンなしで止めたシナリオの観測: warmed = 事前起動の結果・status = 止めた後の状態・refused = 止めた後の
   事前起動の拒否。"
  (#^ bool warmed)
  (#^ SessionStatus status)
  (#^ (| SessionNotFoundError None) refused))

(defk warm-then-stop [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % WarmedThenStopped)] :tags {:context "headless-adapter-test" :role "program"}}
  "事前起動した session をターンを 1 度も走らせずに止め、止めた session は事前起動できない事を見るため。"
  (<- handle (launch s "adapter-warm-stop" None None))
  (<- warmed (WarmSession handle))
  (<- (Stop handle))
  (<- observed (Monitor handle))
  (var refused None)
  (try
    (<- (WarmSession handle))
    (except [error SessionNotFoundError] (:= refused error)))
  (WarmedThenStopped :warmed warmed :status observed.status :refused refused))

(deftest test-headless-a-warm-session-stops-without-a-turn-fake [tmp-path]
  (val seen (run-on FAKE tmp-path warm-then-stop))
  (assert seen.warmed (repr seen))
  (assert (= seen.status SessionStatus.STOPPED) (repr seen))
  (assert (isinstance seen.refused SessionNotFoundError) (repr seen)))


;; --- 新しい文脈の id を呼び手が名指す(LaunchEffect.new_context_id)-------------------------------------------------------
;; 事前起動する session と、最初のターンを始める session が別の LaunchEffect でも、同じ新しい文脈の id を名指せば同じ文脈になり、
;; 最初のターンは事前起動した runtime を使い回す(agora-redesign #3810)。名指さなければ handler が id を作る(今までの形)。

(val NAMED-CONTEXT "5f0c1c5e-7a55-4b7e-9d0b-0a4d3d7f1a01")
(val OTHER-CONTEXT "5f0c1c5e-7a55-4b7e-9d0b-0a4d3d7f1a02")

(defrecord NamedWarmThenTurn
  "新しい文脈の id を名指して事前起動し、別の session の最初のターンを走らせたシナリオの観測: warmed = 事前起動の結果・first = 最初の
   ターンの読み取り結果・warm-view = 事前起動で名指した文脈の層 2 の process の状態・turn-view = 最初のターンで名指した文脈の状態。"
  (#^ bool warmed)
  (#^ Read first)
  (#^ (| LiveProcess NoLiveProcess) warm-view)
  (#^ (| LiveProcess NoLiveProcess) turn-view))

(defk launch-naming [#^ Setting s #^ str name #^ (| str None) prompt #^ str context-id]
  {:pre [(: s Setting) (: name str) (: prompt (| str None)) (: context-id str)] :post [(: % SessionHandle)]
   :tags {:context "headless-adapter-test" :role "program"}}
  "新しい文脈の id を名指して session を起動するため(prompt が在れば最初のターンも始める)。"
  (<- handle (Launch name :agent-type AgentType.CLAUDE :work-dir s.work-dir :prompt prompt :model s.model
                     :lifecycle AgentSessionLifecycle.MULTI-TURN :new-context-id (NamedContextId context-id)))
  handle)

(defk named-warm-then-turn [#^ Setting s #^ str warm-context #^ str turn-context]
  {:pre [(: s Setting) (: warm-context str) (: turn-context str)] :post [(: % NamedWarmThenTurn)]
   :tags {:context "headless-adapter-test" :role "program"}}
  "warm-context を名指した session を事前起動し、turn-context を名指した別の session で最初のターンを走らせて、層 2 の起動回数を
   見るため。"
  (<- holder (launch-naming s "adapter-named-warm" None warm-context))
  (<- warmed (WarmSession holder))
  (<- handle (launch-naming s "adapter-named-turn" (remember-prompt "ALPHA-1") turn-context))
  (<- first (read-until handle (fn [events end] (is-not end None)) s.timeout -1))
  (<- warm-view (ClaudeLiveProcess warm-context))
  (<- turn-view (ClaudeLiveProcess turn-context))
  (<- (Stop handle))
  (<- (Stop holder))
  (NamedWarmThenTurn :warmed warmed :first first :warm-view warm-view :turn-view turn-view))

(defk check-named-turn-completed [#^ NamedWarmThenTurn seen #^ str turn-context]
  {:pre [(: seen NamedWarmThenTurn) (: turn-context str)] :post [(: % None)]
   :tags {:context "headless-adapter-test" :role "judgment"}}
  "名指した文脈の最初のターンが終わり、続きの id が名指した id である事を確かめるため。"
  (assert seen.warmed (repr seen))
  (val ends (ends-of seen.first.events))
  (assert (and (= (len ends) 1) (isinstance (get ends 0) AgentTurnCompleted)) (repr ends))
  (assert (in "ALPHA-1" (. (get ends 0) result-text)) (repr ends))
  (assert (= (. (get ends 0) resume-from) turn-context) (repr ends))
  None)

(defk same-named-context [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % NamedWarmThenTurn)] :tags {:context "headless-adapter-test" :role "program"}}
  "事前起動と最初のターンが同じ id を名指すシナリオを run-on へ渡すため。"
  (<- seen (named-warm-then-turn s NAMED-CONTEXT NAMED-CONTEXT))
  seen)

(defk different-named-contexts [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % NamedWarmThenTurn)] :tags {:context "headless-adapter-test" :role "program"}}
  "事前起動と最初のターンが違う id を名指すシナリオを run-on へ渡すため。"
  (<- seen (named-warm-then-turn s NAMED-CONTEXT OTHER-CONTEXT))
  seen)

(defk check-same-named-context [#^ NamedWarmThenTurn seen]
  {:pre [(: seen NamedWarmThenTurn)] :post [(: % None)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "同じ id を名指した時、最初のターンが事前起動した runtime を使い回した(層 2 の起動回数 1)事を確かめるため。"
  (<- (check-named-turn-completed seen NAMED-CONTEXT))
  (assert (= seen.turn-view (LiveProcess :launches 1)) (repr seen.turn-view))
  None)

(defk check-different-named-contexts [#^ NamedWarmThenTurn seen]
  {:pre [(: seen NamedWarmThenTurn)] :post [(: % None)] :tags {:context "headless-adapter-test" :role "judgment"}}
  "違う id を名指した時、最初のターンは事前起動した runtime を使わず自分の runtime を起動した(層 2 の起動回数は文脈ごとに 1・
   合わせて 2)事を確かめるため。"
  (<- (check-named-turn-completed seen OTHER-CONTEXT))
  (assert (= seen.warm-view (LiveProcess :launches 1)) (repr seen.warm-view))
  (assert (= seen.turn-view (LiveProcess :launches 1)) (repr seen.turn-view))
  None)

(deftest test-headless-a-named-new-context-warmed-serves-the-first-turn-of-another-session-fake [tmp-path]
  (<- (check-same-named-context (run-on FAKE tmp-path same-named-context))))

(deftest test-headless-a-named-new-context-warmed-serves-the-first-turn-of-another-session-stub [tmp-path]
  (<- (check-same-named-context (run-on STUB tmp-path same-named-context))))

(deftest test-headless-a-different-named-new-context-does-not-use-the-warmed-runtime-fake [tmp-path]
  (<- (check-different-named-contexts (run-on FAKE tmp-path different-named-contexts))))

(deftest test-headless-a-different-named-new-context-does-not-use-the-warmed-runtime-stub [tmp-path]
  (<- (check-different-named-contexts (run-on STUB tmp-path different-named-contexts))))

(deftest test-launch-new-context-id-is-a-closed-choice-and-terminal-handlers-refuse-a-named-one [tmp-path]
  ;; 既定は handler が id を作る(HandlerMadeContextId)。名指しは NamedContextId だけで、resume_from(前の文脈の続き)とは両立しない。
  ;; 文脈の id を自分で決められない端末の handler は、名指しを黙って捨てずに型で拒否する。
  (import doeff_agents.effects [refuse-turn-capabilities])
  (val plain (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path))
  (assert (= plain.new-context-id (HandlerMadeContextId)) (repr plain))
  (with [(pytest.raises ValueError)]
    (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :resume-from NAMED-CONTEXT
                  :new-context-id (NamedContextId OTHER-CONTEXT)))
  (with [(pytest.raises TypeError)]
    (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :new-context-id NAMED-CONTEXT))
  (with [(pytest.raises ValueError)]
    (NamedContextId ""))
  (with [info (pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                            :new-context-id (NamedContextId NAMED-CONTEXT))
                              :handler "t"))
  (assert (= info.value.capability "LaunchEffect.new_context_id") info.value.capability))

(defk launch-with-a-malformed-name [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % SessionHandle)] :tags {:context "headless-adapter-test" :role "program"}}
  "runtime が受けない綴りの id を名指して起動するシナリオを run-on へ渡すため。"
  (<- handle (launch-naming s "adapter-named-malformed" None "not-a-context-id"))
  handle)

(deftest test-headless-refuses-a-named-new-context-id-the-runtime-cannot-take-fake [tmp-path]
  ;; runtime が受けない綴りの id は、層 2 の値の例外を上へ漏らさず起動の失敗として型で拒否する(session は起動しない)。
  (with [(pytest.raises AgentLaunchError)]
    (run-on FAKE tmp-path launch-with-a-malformed-name)))


(deftest test-terminal-handlers-refuse-warming-by-type [tmp-path]
  ;; LaunchEffect が既に CLI を起動して待たせる handler(AgentHandler の object を包む defhandler)は、入力の前に事前起動する
  ;; WarmSessionEffect に黙って何もせずに応答せず、AgentCapabilityUnsupportedError で拒否する(claude-handler・codex-handler の拒否は
  ;; test_claude_handler.py・test_codex_handler.py)。
  (import doeff_agents.handlers.testing [ScenarioAgentHandler])
  (with [info (pytest.raises AgentCapabilityUnsupportedError)]
    (run (scheduled (.wrap (ScenarioAgentHandler) (WarmSession (SessionHandle "x"))))))
  (assert (= info.value.capability "WarmSessionEffect") info.value.capability))


;; --- 手番の資格の参照の引き換え(issue #979)------------------------------------------------------------------------

(defhandler redeem-answers [#^ dict answers #^ list asked]
  ;; 検の環境: 手番の資格の参照を出した側の代わりに、参照 → 引き換えの答えを返す(頼まれた参照を asked に写す)。
  ;; 引数に残す理由: 答えの表と写しの置き場は検ごとに違い、検が走らせた後に asked を読む。
  (RedeemTurnCredentialEffect [credential-ref]
    (.append asked credential-ref)
    (resume (get answers credential-ref))))

(defk launch-with-ref [#^ Path work #^ str name #^ (| str None) ref]
  {:pre [(: work Path) (: name str) (: ref (| str None))] :post [(: % SessionHandle)]}
  "手番の資格の参照を持って 1 手番を起こす(引き換えの答えごとに、子の env に何が置かれるかを見るため)。"
  (<- handle SessionHandle (LaunchEffect :session-name name :agent-type AgentType.CLAUDE :work-dir work :prompt (reply-prompt "OK")
                                         :lifecycle AgentSessionLifecycle.MULTI-TURN :turn-credential-ref ref))
  handle)

(defn run-with-redeem [#^ Path tmp-path world #^ dict answers #^ list asked program]
  "fake の層 2(呼び手の world)+ headless の adapter の外側に、引き換えの答え手を置いて走らせる。"
  (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock)) (redeem-answers answers asked)]
                                    (fake-headless-claude-handlers None (str (/ tmp-path "home")) :world world :env {} :settings {}))
                                 program))))

(deftest test-headless-redeems-the-credential-ref-into-the-turn-env [tmp-path]
  ;; #665・#979: token は LaunchEffect に載らない。adapter が起こす直前に RedeemTurnCredentialEffect(参照)を外側へ出し、答えの token を
  ;; 子の claude の env の手番の資格の名(本番の agentd が貸与の札を運ぶ名 = TURN-AUTH-ENV-KEYS の 1 つ)にだけ置く。家の資格の答え・
  ;; 参照の無い起動は家の env のまま。token は effect・答え・家・宣言の repr に写らない。
  (import doeff_agents.effects [LaunchEffect TurnCredential HomeTurnCredential RedeemTurnCredentialEffect])
  (import doeff_agents.handlers.headless [HeadlessClaudeConfig TURN-CREDENTIAL-ENV spec-of])
  (import doeff_agents.sessionhost.policy [TURN-AUTH-ENV-KEYS])
  (import doeff_claude_code.fake [FakeClaudeWorld])
  (import doeff_claude_code.values [ClaudeHome])
  (setv token "sk-ant-oat01-never-printed" work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (assert (in TURN-CREDENTIAL-ENV TURN-AUTH-ENV-KEYS))
  (setv world (FakeClaudeWorld fake-responder) asked [])
  (setv answers {"lease-borrowed" (TurnCredential token None) "lease-home" (HomeTurnCredential)})
  (for [#(name ref) [#("borrowed" "lease-borrowed") #("home" "lease-home") #("plain" None)]]
    (run-with-redeem tmp-path world answers asked (launch-with-ref work name ref)))
  ;; 引き換えは参照のある起動だけ・1 回ずつ。
  (assert (= asked ["lease-borrowed" "lease-home"]) asked)
  (setv envs (lfor session (.values world.sessions) (dict session.home.env)))
  (assert (= (len envs) 3) envs)
  (assert (= (lfor env envs :if (in TURN-CREDENTIAL-ENV env) (get env TURN-CREDENTIAL-ENV)) [token]) "token を置くのは借りた起動 1 つだけ")
  ;; 資格の入口は引き換えの答え 1 つ: session_env から資格を入れる路は断られ、宣言の repr に token は写らない。
  (setv config (HeadlessClaudeConfig (ClaudeHome (str (/ tmp-path "home")) {"PATH" "/usr/bin"}))
        launch (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :turn-credential-ref "lease-borrowed")
        spec (spec-of config launch (TurnCredential token 1234.5)))
  (assert (= (get spec.home.env TURN-CREDENTIAL-ENV) token))
  (assert (= (get spec.home.env "PATH") "/usr/bin"))
  ;; 借りた資格の期限は層 2 の宣言へ写る(層 2 が床で生きた process を止める — #3672 の D2)。家の資格は期限を知らない。
  (assert (= spec.credential-expires-at 1234.5) spec.credential-expires-at)
  (assert (is (. (spec-of config launch) credential-expires-at) None))
  (for [shown [(repr launch) (repr spec) (repr spec.home) (repr (TurnCredential token None))
               (repr (RedeemTurnCredentialEffect :credential-ref "lease-borrowed"))]]
    (assert (not-in token shown)))
  (with [(pytest.raises ValueError)]
    (spec-of config (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                  :session-env {TURN-CREDENTIAL-ENV token})))
  (for [bad ["" "a\nb"]]
    (with [(pytest.raises ValueError)]
      (TurnCredential bad None)))
  (for [bad-expiry [True "1234"]]
    (with [(pytest.raises TypeError)]
      (TurnCredential token bad-expiry)))
  (with [(pytest.raises ValueError)]
    (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :turn-credential-ref "")))

(deftest test-the-redeemed-github-token-rides-on-the-child-env-as-gh-token [tmp-path]
  ;; agora-redesign #3753: 引き換えた TurnCredential が github_token を持てば、adapter は子の claude の env の GH_TOKEN(名の定義元 = agent_env.hy)
  ;; にその値を 1 つ置く。持たない答え・設定 dir の資格の答えの起動には GH_TOKEN が無い。token は TurnCredential・起動の宣言・設定 dir の repr に写らず、
  ;; 値の確かめは oauth_token と同じ(空でない文字列・CR/LF/NUL を含まない)。
  (import doeff_agents.agent_env [GITHUB-TOKEN-ENV])
  (import doeff_agents.effects [LaunchEffect TurnCredential HomeTurnCredential])
  (import doeff_agents.handlers.headless [HeadlessClaudeConfig spec-of])
  (import doeff_claude_code.fake [FakeClaudeWorld])
  (import doeff_claude_code.values [ClaudeHome])
  (val oauth "sk-ant-oat01-never-printed-3753")
  (val github "ghs-never-printed-3753")
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (assert (= GITHUB-TOKEN-ENV "GH_TOKEN"))
  (val world (FakeClaudeWorld fake-responder))
  (val asked [])
  (val credential (TurnCredential oauth None :github-token github))
  (val answers {"lease-gh" credential "lease-plain" (TurnCredential oauth None) "lease-home" (HomeTurnCredential)})
  (for [#(name ref) [#("gh" "lease-gh") #("plain" "lease-plain") #("home" "lease-home")]]
    (run-with-redeem tmp-path world answers asked (launch-with-ref work name ref)))
  (val envs (lfor session (.values world.sessions) (dict session.home.env)))
  (assert (= (len envs) 3) (len envs))
  (assert (= (lfor env envs (.get env GITHUB-TOKEN-ENV)) [github None None]) "GH_TOKEN を置くのは github_token を持つ起動 1 つだけ")
  (val config (HeadlessClaudeConfig (ClaudeHome (str (/ tmp-path "home")) {"PATH" "/usr/bin"})))
  (val launch (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :turn-credential-ref "lease-gh"))
  (val spec (spec-of config launch credential))
  (for [shown [(repr credential) (repr spec) (repr spec.home)]]
    (assert (not-in github shown) "github_token の値が repr に写った"))
  (for [bad ["" "a\nb" "a\rb" "a\x00b" 7]]
    (with [(pytest.raises ValueError)]
      (TurnCredential oauth None :github-token bad))))

(deftest test-an-unredeemable-credential-ref-does-not-start-the-session [tmp-path]
  ;; #979 の反例: 引き換えられない参照(知らない・もう返した lease)の起動は TurnCredentialUnavailableError(AgentLaunchError の 1 つ)で
  ;; 断り、CLI の会話を 1 つも始めない(家の資格で黙って走らせない)。断りの理由は答えの文のまま運ぶ。
  (import doeff_agents.effects [TurnCredentialUnavailable TurnCredentialUnavailableError])
  (import doeff_claude_code.fake [FakeClaudeWorld])
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv world (FakeClaudeWorld fake-responder) asked [])
  (with [info (pytest.raises TurnCredentialUnavailableError)]
    (run-with-redeem tmp-path world {"lease-gone" (TurnCredentialUnavailable "lease lease-gone は借りていないか、もう返した")} asked
                     (launch-with-ref work "gone" "lease-gone")))
  (assert (isinstance info.value AgentLaunchError))
  (assert (= info.value.reason "lease lease-gone は借りていないか、もう返した") info.value.reason)
  (assert (= info.value.credential-ref "lease-gone"))
  (assert (= asked ["lease-gone"]) asked)
  (assert (= world.sessions {}) world.sessions))


(defk launch-with-credential [#^ Path work]
  {:pre [(: work Path)] :post [(: % SessionHandle)]}
  ;; 手番の資格の参照を持って 1 手番を起こす(起きない CLI の筋書きで、失敗の文に引き換えた token が写らないかを見るため)。
  (import doeff_agents.effects [LaunchEffect])
  (<- handle SessionHandle (LaunchEffect :session-name "cred-fail" :agent-type AgentType.CLAUDE :work-dir work :prompt "x"
                                         :lifecycle AgentSessionLifecycle.MULTI-TURN :turn-credential-ref "lease-1"))
  handle)

(deftest test-a-launch-failure-does-not-carry-the-borrowed-token [tmp-path]
  ;; agora-redesign #665(cry-w8 の独立レビューの指摘): CLI が起きない時の失敗の文・repr・traceback は worker の log に入る。引き換えた
  ;; token(子の env の CLAUDE_CODE_OAUTH_TOKEN)の値がそこへ写らない。
  (import traceback)
  (import doeff_agents.effects [TurnCredential])
  (setv token "sk-ant-oat01-must-not-leak-665" work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv handlers (+ [(sync-time-handler) (redeem-answers {"lease-1" (TurnCredential token None)} [])]
                    (headless-claude-handlers (str (/ tmp-path "home")) (child-env) :live-limit 8 :credential-floor-seconds 7200.0
                                              :command #((str (/ tmp-path "no-such-claude"))))))
  (with [info (pytest.raises Exception)]
    (run (scheduled (with_handlers handlers (launch-with-credential work)))))
  (setv shown (.join "" (traceback.format-exception info.value)))
  (assert (in "no-such-claude" shown) shown)
  (for [text [(str info.value) (repr info.value) shown]]
    (assert (not-in token text))))


(deftest test-claude-agent-runtime-names-leave-the-substrate-to-doeff-agents [tmp-path]
  ;; 土台を名指さない名(agora-redesign #606): claude_agent_runtime_handlers / fake_claude_agent_runtime_handlers は、今日の土台
  ;; (print mode の adapter)の組と同じ種類の handler を同じ順で返し、fake の名で同じ筋書きが通る。
  (import doeff_agents [claude-agent-runtime-handlers fake-claude-agent-runtime-handlers])
  (setv home (str (/ tmp-path "home")))
  (assert (= (lfor h (claude-agent-runtime-handlers :config-dir home :env {} :live-limit 8 :credential-floor-seconds 7200.0) (. (type h) __name__))
             (lfor h (headless-claude-handlers home {} :live-limit 8 :credential-floor-seconds 7200.0) (. (type h) __name__))))
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv setting (Setting work None 60.0 8))
  (check-one-turn-then-resume
    (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))]
                                      (fake-claude-agent-runtime-handlers :responder fake-responder :config-dir home :env {} :settings {}))
                                   (one-turn-then-resume setting))))))


;; --- fake の組に渡した env と settings(agora-redesign #3327)---------------------------------------------------------

(defclass [(dataclass :frozen True)] ReadLaunchSpecs [EffectBase]
  "launch-specs が写した起動の宣言を読む(答え = ClaudeSessionSpec の tuple — 層 2 へ届いた順)。")

(defhandler launch-specs
  ;; 層 2 の fake と adapter の間に挟み、adapter が層 2 へ渡す起動の宣言(ClaudeSessionSpec)を写してから、そのまま層 2 へ渡すため。
  ;; 写しは handler の session に積み、ReadLaunchSpecs で読む。
  (session var seen #())
  (ClaudeStartTurn [origin spec input]
    (:= seen (+ seen #(spec)))
    (reperform effect))
  (ReadLaunchSpecs []
    (resume seen)))

(defk launch-then-read-specs [work name]
  {:pre [(: work Path) (: name str)] :post [(: % tuple)]}
  "1 手番を起こし、そこまでに層 2 へ届いた起動の宣言を読むため(launch-specs の内側で撃つ)。"
  (<- _handle SessionHandle (launch-with-ref work name None))
  (<- specs tuple (ReadLaunchSpecs))
  specs)

(defk launched-specs [work name runtime]
  {:pre [(: work Path) (: name str) (: runtime list) (= (len runtime) 2)] :post [(: % tuple)]}
  "fake の組 runtime(層 2 の fake → adapter)の間に写しを挟んで 1 手番を起こし、層 2 へ届いた起動の宣言の列を返すため。"
  (<- specs tuple (with_handlers [(session-store) (get runtime 0) launch-specs (get runtime 1)] (launch-then-read-specs work name)))
  specs)

(deftest test-the-fake-runtime-carries-the-given-env-and-settings-on-the-launch [tmp-path]
  ;; agora-redesign #3327: fake の組(fake_claude_agent_runtime_handlers)に渡した env と settings は、本番の組と同じく adapter が層 2 へ渡す
  ;; 起動の宣言(home.env と settings)に載る — 上の層の模擬が、本番と同じ手順で決めた子の env と CLI の settings を起動ごとに観測するため。
  (import doeff_agents [fake-claude-agent-runtime-handlers])
  (import doeff_hy.frozen [thaw-json])
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (val env {"PATH" "/opt/agent-tools/bin:/usr/bin" "AGENT_SESSION_CLASS" "unattended"})
  (val settings {"hooks" {"Stop" [{"matcher" "*" "hooks" [{"type" "command" "command" "true"}]}]}})
  (val given (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))]
                                            (launched-specs work "given"
                                                            (fake-claude-agent-runtime-handlers :responder fake-responder
                                                                                                :env env :settings settings))))))
  (assert (= (len given) 1) given)
  (assert (= (dict (. (get given 0) home env)) env))
  (assert (= (thaw-json (. (get given 0) settings)) settings)))

(deftest test-the-fake-runtime-refuses-a-call-that-drops-env-or-settings []
  ;; agora-redesign #3387: env と settings は既定の無い引数 — 落とした呼びは組を作る前に、落とした名を名指す TypeError で落ちる
  ;; (「渡さなければ空」の古い形を残さない。宣言する物の無い呼び手は空の写像を明示で渡す)。fake の 3 つの口(土台を名指さない名・
  ;; headless の名・組み立ての部品)ごとに、片方ずつ落として呼ぶ。
  (import doeff_agents [fake-claude-agent-runtime-handlers fake-headless-claude-agent-handlers])
  (val dropped [#("'env'" {"settings" {}}) #("'settings'" {"env" {}})])
  (for [make [fake-claude-agent-runtime-handlers fake-headless-claude-agent-handlers]]
    (for [#(missing given) dropped]
      (with [(pytest.raises TypeError :match missing)]
        (make :responder fake-responder #** given))))
  (for [#(missing given) dropped]
    (with [(pytest.raises TypeError :match missing)]
      (fake-headless-claude-handlers fake-responder #** given))))


;; --- 層 2 だけ・adapter だけの入口(agora-redesign #3507)------------------------------------------------------------

(deftest test-the-split-entries-build-the-same-handlers-as-the-pairs [tmp-path]
  ;; agora-redesign #3507: 層 2 だけ(本番・fake)と adapter だけの入口は、対の入口と同じ種類の handler を同じ順で作り、並べると同じ
  ;; 筋書きが通る — 呼び手が層 2 を土台の外側に、adapter を Program の近くに置き分けても、組の中身は対の入口と同じ。
  (import doeff_agents [claude-agent-runtime-handlers fake-claude-agent-runtime-handlers claude-process-layer-handler
                        fake-claude-process-layer-handler claude-agent-adapter-handler])
  (import doeff_hy.frozen [thaw-json])
  (setv home (str (/ tmp-path "home")))
  (assert (= (lfor h [(claude-process-layer-handler :live-limit 8 :credential-floor-seconds 7200.0) (claude-agent-adapter-handler :config-dir home :env {} :settings {})] (. (type h) __name__))
             (lfor h (claude-agent-runtime-handlers :config-dir home :env {} :live-limit 8 :credential-floor-seconds 7200.0) (. (type h) __name__))))
  (assert (= (lfor h [(fake-claude-process-layer-handler :responder fake-responder) (claude-agent-adapter-handler :config-dir home :env {} :settings {})]
                   (. (type h) __name__))
             (lfor h (fake-claude-agent-runtime-handlers :responder fake-responder :config-dir home :env {} :settings {}) (. (type h) __name__))))
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (check-one-turn-then-resume
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (fake-claude-process-layer-handler :responder fake-responder)
                                    (claude-agent-adapter-handler :config-dir home :env {} :settings {})]
                                   (one-turn-then-resume (Setting work None 60.0 8))))))
  ;; adapter だけの入口に渡した家・env・settings は、対の入口と同じく層 2 へ渡る起動の宣言に載る。
  (val env {"PATH" "/opt/agent-tools/bin:/usr/bin" "AGENT_SESSION_CLASS" "unattended"})
  (val settings {"hooks" {"Stop" [{"matcher" "*" "hooks" [{"type" "command" "command" "true"}]}]}})
  (val given (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))]
                                            (launched-specs work "split"
                                                            [(fake-claude-process-layer-handler :responder fake-responder)
                                                             (claude-agent-adapter-handler :config-dir home :env env :settings settings)])))))
  (assert (= (len given) 1) given)
  (assert (= (. (get given 0) home config-dir) home))
  (assert (= (dict (. (get given 0) home env)) env))
  (assert (= (thaw-json (. (get given 0) settings)) settings)))

(deftest test-the-adapter-and-the-fake-runtimes-carry-the-given-permission-on-the-launch [tmp-path]
  ;; agora-redesign #3753: adapter だけの入口と fake の handler の並びの入口(土台を指定しない名・headless の名・組み立ての部品)に permission を渡すと、
  ;; 層 2 へ渡る起動の宣言(ClaudeSessionSpec)の permission がその値になる — 許可を設定 dir の settings.json に任せる形(HomeSettings)を
  ;; 本番の adapter と模擬の fake に同じく渡すため。渡さなければ BypassAll(今のまま)。
  (import doeff_agents [claude-agent-adapter-handler fake-claude-process-layer-handler fake-claude-agent-runtime-handlers
                        fake-headless-claude-agent-handlers])
  (import doeff_claude_code.values [BypassAll HomeSettings])
  (val home (str (/ tmp-path "home")))
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (val cases [#("adapter-given" (HomeSettings)
                [(fake-claude-process-layer-handler :responder fake-responder)
                 (claude-agent-adapter-handler :config-dir home :env {} :settings {} :permission (HomeSettings))])
              #("adapter-default" (BypassAll)
                [(fake-claude-process-layer-handler :responder fake-responder)
                 (claude-agent-adapter-handler :config-dir home :env {} :settings {})])
              #("fake-runtime-given" (HomeSettings)
                (fake-claude-agent-runtime-handlers :responder fake-responder :config-dir home :env {} :settings {}
                                                    :permission (HomeSettings)))
              #("fake-runtime-default" (BypassAll)
                (fake-claude-agent-runtime-handlers :responder fake-responder :config-dir home :env {} :settings {}))
              #("fake-headless-given" (HomeSettings)
                (fake-headless-claude-agent-handlers :responder fake-responder :config-dir home :env {} :settings {}
                                                     :permission (HomeSettings)))
              #("fake-compose-given" (HomeSettings)
                (fake-headless-claude-handlers fake-responder home :env {} :settings {} :permission (HomeSettings)))
              #("fake-compose-default" (BypassAll)
                (fake-headless-claude-handlers fake-responder home :env {} :settings {}))])
  (for [#(name expected runtime) cases]
    (val given (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))] (launched-specs work name runtime)))))
    (assert (= (len given) 1) #(name given))
    (assert (= (. (get given 0) permission) expected) #(name (. (get given 0) permission)))))

(deftest test-the-fake-process-layer-takes-exactly-one-of-responder-and-world []
  ;; agora-redesign #3507: fake の層 2 だけの入口も、対の入口と同じく responder と world のちょうど 1 つを受ける(両方・どちらも無しは断る)。
  (import doeff_agents [fake-claude-process-layer-handler])
  (with [(pytest.raises ValueError :match "ちょうど 1 つ")]
    (fake-claude-process-layer-handler))
  (with [(pytest.raises ValueError :match "ちょうど 1 つ")]
    (fake-claude-process-layer-handler :responder fake-responder :world (FakeClaudeWorld fake-responder))))
