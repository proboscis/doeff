;; headless の handler(handlers/headless.hy)の検 — doeff-agents の公開 effect だけを撃つ Program を、層 2 の handler の組だけ替えて走らせる
;; (agora-redesign #604)。
;;
;;   fake  doeff-claude-code の fake の handler + 仮想の時計。API も process も使わない。
;;   stub  doeff-claude-code の本番の handler + 替え玉の CLI(packages/doeff-claude-code/tests/stub_cli/claude.hy)+ 壁の時計。
;;   real  doeff-claude-code の本番の handler + 本物の claude(個人の profile・model haiku)。env DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR が
;;         無ければ skip・印 e2e(日次と着地の門は -m "not e2e" で除く)。会社の profile は使わない。
;;
;; 筋書きの Program は session host の socket を開かず、doeff_claude_code も doeff_agents.sessionhost も import しない(公開 effect だけ)。
(require doeff-hy.macros [deftest defk <- val])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import os)
(import pathlib [Path])
(import re)
(import sys)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled Spawn Wait])
;; 故障の注入の口だけは層 2 の検の effect を使う(公開 effect ではない — process の死を起こす手が公開面に無いため)。
(import doeff_claude_code.faults [ClaudeDropProcess])
(import doeff_time [Delay GetMonotonic SimClock sim-time-handler sync-time-handler])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.effects [
  Launch FollowUp Interrupt Events AwaitResult Monitor Capture Stop ReleaseSession ExportContextEffect
  SessionHandle AgentEventPage AwaitStatus TurnInputMode InputFateState
  AgentTextEvent AgentToolUseEvent AgentInputFateEvent AgentTurnEndEvent
  AgentTurnCompleted AgentTurnInterrupted AgentTurnLost
  AgentCapabilityUnsupportedError NoTurnInFlightError ResumeTargetNotFoundError SessionNotFoundError])
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
  "fake の返事(scenario_rules.hy の reply-for と同じ規則の写し — 型の違う 2 つ目の規則を作らない範囲で最小)。"
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
  (check-one-turn-then-resume (run-on FAKE tmp-path one-turn-then-resume)))

(deftest test-headless-one-turn-and-resume-stub [tmp-path]
  (check-one-turn-then-resume (run-on STUB tmp-path one-turn-then-resume)))

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
  ;; 借りた token(turn_credential)を置けない端末の handler は、家の資格で黙って走らせずに断る(agora-redesign #665)。
  (import doeff_agents.effects [TurnCredential])
  (with [(pytest.raises AgentCapabilityUnsupportedError)]
    (refuse-turn-capabilities (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                            :turn-credential (TurnCredential "tok-never-printed"))
                              :handler "t")))


(deftest test-headless-places-the-borrowed-access-token [tmp-path]
  ;; agora-redesign #665: 借りた access token は型の欄(LaunchEffect.turn_credential)1 つから入り、子の claude の env の手番の資格の名
  ;; (本番の agentd が貸与の札を運ぶ名 = TURN-AUTH-ENV-KEYS の 1 つ)にだけ置かれる。session_env から資格を入れる路は断られ、
  ;; token は effect・家・宣言の repr に写らない。欄が無ければ家の env のまま(local の家の資格)。
  (import doeff_agents.effects [LaunchEffect TurnCredential])
  (import doeff_agents.handlers.headless [HeadlessClaudeConfig TURN-CREDENTIAL-ENV spec-of])
  (import doeff_agents.sessionhost.policy [TURN-AUTH-ENV-KEYS])
  (import doeff_claude_code.values [ClaudeHome])
  (setv token "sk-ant-oat01-never-printed" config (HeadlessClaudeConfig (ClaudeHome (str (/ tmp-path "home")) {"PATH" "/usr/bin"})))
  (assert (in TURN-CREDENTIAL-ENV TURN-AUTH-ENV-KEYS))
  (setv launch (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                             :turn-credential (TurnCredential token))
        spec (spec-of config launch))
  (assert (= (get spec.home.env TURN-CREDENTIAL-ENV) token))
  (assert (= (get spec.home.env "PATH") "/usr/bin"))
  (for [shown [(repr launch) (repr spec) (repr spec.home) (repr launch.turn-credential)]]
    (assert (not-in token shown)))
  (assert (not-in TURN-CREDENTIAL-ENV (. (spec-of config (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE
                                                                         :work-dir tmp-path)) home env)))
  (with [(pytest.raises ValueError)]
    (spec-of config (LaunchEffect :session-name "x" :agent-type AgentType.CLAUDE :work-dir tmp-path
                                  :session-env {TURN-CREDENTIAL-ENV token})))
  (for [bad ["" "a\nb"]]
    (with [(pytest.raises ValueError)]
      (TurnCredential bad))))


(defk launch-with-credential [#^ Path work #^ str token]
  {:pre [(: work Path) (: token str)] :post [(: % SessionHandle)]}
  ;; 借りた token を持って 1 手番を起こす(起きない CLI の筋書きで、失敗の文に token が写らないかを見るため)。
  (import doeff_agents.effects [LaunchEffect TurnCredential])
  (<- handle SessionHandle (LaunchEffect :session-name "cred-fail" :agent-type AgentType.CLAUDE :work-dir work :prompt "x"
                                         :lifecycle AgentSessionLifecycle.MULTI-TURN :turn-credential (TurnCredential token)))
  handle)

(deftest test-a-launch-failure-does-not-carry-the-borrowed-token [tmp-path]
  ;; agora-redesign #665(cry-w8 の独立レビューの指摘): CLI が起きない時の失敗の文・repr・traceback は worker の log に入る。借りた
  ;; token(子の env の CLAUDE_CODE_OAUTH_TOKEN)の値がそこへ写らない。
  (import traceback)
  (setv token "sk-ant-oat01-must-not-leak-665" work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (setv handlers (+ [(sync-time-handler)]
                    (headless-claude-handlers (str (/ tmp-path "home")) (child-env)
                                              :command #((str (/ tmp-path "no-such-claude"))))))
  (with [info (pytest.raises Exception)]
    (run (scheduled (with_handlers handlers (launch-with-credential work token)))))
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
