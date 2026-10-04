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
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import os)
(import pathlib [Path])
(import re)
(import sys)
(import pytest)
(import doeff [run with_handlers EffectBase])
(import doeff_core_effects.handlers [state :as session-store])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
;; 故障の注入の口だけは層 2 の検の effect を使う(公開 effect ではない — process の死を起こす手が公開面に無いため)。
(import doeff_claude_code.faults [ClaudeDropProcess])
;; fake の組に渡した env と settings が層 2 へ届く起動の宣言に載るかを見る口も、層 2 の effect を写す(#3327 — 公開面に宣言が出ないため)。
(import doeff_claude_code.effects [ClaudeStartTurn])
(import doeff_time [Delay GetMonotonic SimClock sim-time-handler sync-time-handler])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.effects [
  Launch FollowUp Interrupt Events AwaitResult Monitor Capture Stop ReleaseSession ExportContextEffect
  SessionHandle AgentEventPage AwaitStatus TurnInputMode InputFateState
  AgentTextEvent AgentToolUseEvent AgentInputFateEvent AgentTurnEndEvent
  AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage
  AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError SessionNotFoundError
  AgentError AgentLaunchError TurnInFlightError LaunchEffect RedeemTurnCredentialEffect])
(import doeff_agents.monitor [SessionStatus])
;; 層 2 の handler との対は doeff-agents の組み立ての部品で作る(この検も doeff_claude_code を import しない)。
(import doeff_agents.handlers.headless_compose [FakeReply headless-claude-handlers fake-headless-claude-handlers])

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

(defn fake-responder [#^ str text #^ tuple memory]
  "fake の返事(scenario_rules.hy の reply-for と同じ規則の写し — 型の違う 2 つ目の規則を作らない範囲で最小)。
   fake にだけ在る規則: 「Fail after spending <額>」= 額を使った後に誤りで終える手番(失敗の手番の額の写しを見る検のため)。"
  (setv spent (re.search r"Fail after spending (\S+)" text))
  (when spent
    (return (FakeReply "" :tool-seconds 2.0 :fail "spent then failed" :cost-usd (float (.group spent 1)))))
  (setv sleep (re.search r"sleep (\d+)" text)
        exact (re.search r"[Rr]eply with exactly: (\S+)" text)
        extra (re.search r"include the word (\S+)" text))
  (setv word (cond
               (in "What was the codeword" text)
                 (next (gfor earlier memory :setv found (re.search r"codeword (\S+?)\." earlier) :if found (.group found 1))
                       "UNKNOWN")
               exact (.group exact 1)
               extra (.group extra 1)
               True "OK"))
  (FakeReply word :tool-seconds (if sleep (float (.group sleep 1)) 0.0)))


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
  "解釈器の handler の組(先頭が外側): 時間の handler → 層 2 の handler → headless の adapter。"
  (setv settings {"disableAllHooks" True})
  (cond
    (= backend FAKE) (+ [(sim-time-handler :clock (SimClock))] (fake-headless-claude-handlers fake-responder home-dir))
    (= backend STUB) (+ [(sync-time-handler)]
                        (headless-claude-handlers home-dir (child-env) :settings settings
                                                  :command #(sys.executable "-m" "hy" STUB-PATH)))
    (= backend REAL) (+ [(sync-time-handler)] (headless-claude-handlers home-dir (child-env) :settings settings))
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
  (or (any (gfor event events (and (isinstance event AgentToolUseEvent) (in "Bash" event.tool-names))))
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
  (assert (is (get seen "idle-ask") False))
  (setv after (. (get seen "after") end))
  (assert (isinstance after AgentTurnCompleted) (repr after))
  (assert (in "AFTER" after.result-text) after.result-text))

(defn check-stop [#^ dict seen]
  (setv events (. (get seen "page") events))
  (assert (any (gfor end (ends-of events) (isinstance end AgentTurnInterrupted))) (repr events))
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
                                    (fake-headless-claude-handlers None (str (/ tmp-path "home")) :world world))
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
  (setv answers {"lease-borrowed" (TurnCredential token) "lease-home" (HomeTurnCredential)})
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
        spec (spec-of config launch (TurnCredential token)))
  (assert (= (get spec.home.env TURN-CREDENTIAL-ENV) token))
  (assert (= (get spec.home.env "PATH") "/usr/bin"))
  (for [shown [(repr launch) (repr spec) (repr spec.home) (repr (TurnCredential token))
               (repr (RedeemTurnCredentialEffect :credential-ref "lease-borrowed"))]]
    (assert (not-in token shown)))
  (with [(pytest.raises ValueError)]
    (spec-of config (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                  :session-env {TURN-CREDENTIAL-ENV token})))
  (for [bad ["" "a\nb"]]
    (with [(pytest.raises ValueError)]
      (TurnCredential bad)))
  (with [(pytest.raises ValueError)]
    (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path :turn-credential-ref "")))

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
  (setv handlers (+ [(sync-time-handler) (redeem-answers {"lease-1" (TurnCredential token)} [])]
                    (headless-claude-handlers (str (/ tmp-path "home")) (child-env)
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
  (assert (= (lfor h (claude-agent-runtime-handlers :config-dir home :env {}) (. (type h) __name__))
             (lfor h (headless-claude-handlers home {}) (. (type h) __name__))))
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv setting (Setting work None 60.0 8))
  (check-one-turn-then-resume
    (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))]
                                      (fake-claude-agent-runtime-handlers :responder fake-responder :config-dir home))
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
  ;; 渡さなければ今までどおり env も settings も空(今の使い手の振る舞いは変わらない)。
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
  (assert (= (thaw-json (. (get given 0) settings)) settings))
  (val omitted (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock))]
                                              (launched-specs work "omitted"
                                                              (fake-claude-agent-runtime-handlers :responder fake-responder))))))
  (assert (= (len omitted) 1) omitted)
  (assert (= (dict (. (get omitted 0) home env)) {}))
  (assert (= (thaw-json (. (get omitted 0) settings)) {})))
