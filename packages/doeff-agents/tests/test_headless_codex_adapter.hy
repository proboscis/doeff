;; headless の codex の adapter(handlers/headless_codex.hy)の検 — doeff-agents の公開 effect だけを撃つ Program を、層 2(doeff-codex)の
;; handler の組だけ替えて走らせる(card acp:kanban-issue:ki-534a081e32eb)。
;;
;;   fake  doeff-codex の fake の handler + 仮想の時計。process も API も使わない。
;;   stub  doeff-codex の本番の handler + 替え玉の app-server(packages/doeff-codex/tests/stub_cli/codex_app_server.py — 録った codex
;;         0.162.1 の行を、id を差し替えて返す)+ 壁の時計。
;;
;; 守る事: codex のターンの答えの文字の途中が AgentTextDeltaEvent として順に届く(崩れると agora の画面は codex の答えを途中から流せない —
;; 利用者 2026-09-10「どの会話も、文字が届くたびに 1 文字ずつ更新されない」・2026-10-10 21:27 の答え A)。出来事の種類と欄は claude の
;; adapter と同じ形(agora の absorb-event を変えずに受けられる)。入力が読まれた事は AgentInputFateEvent(started)と完了の input_refs で
;; 届く(agora は入力の勘定をこの 2 つで数える)。
;; 筋書きの Program は doeff_codex を import しない(公開 effect と、組み立ての部品 headless_compose だけ)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "headless-codex-adapter-test" :role "program"})
(import dataclasses [dataclass])
(import pathlib [Path])
(import sys)
(import collections.abc [Callable])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [GetMonotonic SimClock sim-time-handler sync-time-handler])
(import doeff_agents.adapters.base [AgentType AgentSessionLifecycle])
(import doeff_agents.effects [
  LaunchEffect FollowUp Interrupt Events Monitor Stop ExportContextEffect WarmSession
  SessionHandle AgentEventPage TurnInputMode InputFateState InputImage NamedContextId ModelWindow
  AgentTextEvent AgentTextDeltaEvent AgentInputFateEvent AgentTurnEndEvent AgentCallUsageEvent
  AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage
  AgentCapabilityUnsupportedError NoTurnInFlightError])
(import doeff_agents.effects.agent [AutocompactTokens])
(import doeff_agents.monitor [SessionStatus])
;; 層 2 との組は doeff-agents の組み立ての部品で作る(この検も doeff_codex を import しない)。
(import doeff_agents.handlers.headless_compose [FakeCodexWorld FakeCodexReply CodexTurnInput CodexApprovalPolicy CodexSandboxMode
                                                codex-process-layer fake-codex-process-layer codex-adapter])

(val FAKE "fake")
(val STUB "stub")
(val REPO-PACKAGES (. (Path __file__) (resolve) parent parent parent))
(val STUB-PATH (str (/ REPO-PACKAGES "doeff-codex" "tests" "stub_cli" "codex_app_server.py")))
(val PAGE-WAIT 1.0)
(val MAX-PAGES 2000)
;; 筋書きの言葉(替え玉の app-server の規則と同じ): SLOW = 止めまで途中の文字を出し続ける / 画像を数える言い方 / 宣言を読み返す言い方。
(val SLOW-PROMPT "SLOW please")
(val IMAGES-PHRASE "Count the attached images.")
(val SETTINGS-PHRASE "Tell the settings.")
(val ANSWER-PIECES #("Hel" "lo, " "wor" "ld."))
(val ANSWER "Hello, world.")


;; --- 解釈器(composition root) --------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Setting []
  "筋書きの宣言: work-dir = 作業 dir / timeout = 1 つの読みの上限(秒)。"
  (#^ Path work-dir)
  (#^ float timeout))

(defk respond [#^ CodexTurnInput input]
  {:pre [(: input CodexTurnInput)] :post [(: % FakeCodexReply)] :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "fake の筋書きの答えを入力から決めるため(替え玉の app-server と同じ規則)。"
  (cond
    (in "SLOW" input.text) (FakeCodexReply :pieces #("slow-0 " "slow-1 ") :hold True)
    (in IMAGES-PHRASE input.text) (FakeCodexReply :pieces #((.format "IMAGES {}" (len input.images))))
    True (FakeCodexReply :pieces ANSWER-PIECES)))

(defk handlers-for [#^ str backend #^ Path tmp-path]
  {:pre [(: backend str) (: tmp-path Path)] :post [(: % list)] :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "解釈器の handler の組(先頭が外側): 時間の handler → 層 2 の handler → headless の codex の adapter。"
  (val env {"PATH" "/usr/bin:/bin" "HOME" (str tmp-path)})
  (<- adapter (codex-adapter env CodexApprovalPolicy.NEVER CodexSandboxMode.READ-ONLY))
  (match backend
    "fake" (do (<- layer (fake-codex-process-layer (FakeCodexWorld (fn [input] (run (respond input))))))
               [(sim-time-handler :clock (SimClock)) layer adapter])
    "stub" (do (<- layer (codex-process-layer #(sys.executable STUB-PATH) 30.0))
               [(sync-time-handler) layer adapter])
    _ (raise (ValueError backend))))

(defk run-on [#^ str backend #^ Path tmp-path #^ Callable scenario]
  {:pre [(: backend str) (: tmp-path Path) (: scenario Callable)] :post [(: % (| Read dict))]
   :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "scenario(Setting) → Program を、層 2 の handler の組 + headless の codex の adapter の下で、検ごとの scheduler で走らせるため。"
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (<- stack (handlers-for backend tmp-path))
  (run (scheduled (with_handlers stack (scenario (Setting work 30.0))))))


;; --- 筋書きの部品(公開 effect だけ) ----------------------------------------------------------------

(defclass [(dataclass :frozen True)] Read []
  "読んだ出来事の全部(seq の順)と頁の終わり(まだなら None)と、次に読む位置。"
  (#^ tuple events)
  (#^ object end)
  (#^ int after))

(defk read-until [#^ SessionHandle handle stop #^ float timeout #^ int after]
  {:pre [(: handle SessionHandle) (: stop Callable) (: timeout float) (: after int)] :post [(: % Read)]
   :tags {:context "headless-codex-adapter-test" :role "program"}}
  "stop(出来事の列 終わり) が真になるまで Events で読むため。seq は after から欠落も重複もなく 1 つずつ増える。"
  (<- started (GetMonotonic))
  (var seen #())
  (var position after)
  (for [_ (range MAX-PAGES)]
    (<- page (Events handle :after-seq position :wait-seconds PAGE-WAIT))
    (assert (isinstance page AgentEventPage) (repr page))
    (for [event page.events]
      (assert (= event.seq (+ position 1)) (.format "seq {} after {}" event.seq position))
      (:= position event.seq)
      (:= seen (+ seen #(event))))
    (<- enough (stop seen page.end))
    (when enough (return (Read seen page.end position)))
    (<- now (GetMonotonic))
    (assert (< (- now started) timeout) (.format "{} 秒の内に読み終わらない: {!r}" timeout seen)))
  (raise (AssertionError (.format "{} 頁を読んでも終わらない: {!r}" MAX-PAGES seen))))

(val PageEnd (| AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost None))

(defk ended [events end]
  {:pre [(: events tuple) (: end PageEnd)] :post [(: % bool)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "頁の終わりが届いたか(読みの止めの条件)。"
  (is-not end None))

(defk first-delta [events end]
  {:pre [(: events tuple) (: end PageEnd)] :post [(: % bool)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "答えの文字の途中が 1 つ届いたか(途中で止める・足す筋書きの拍)。"
  (any (gfor event events (isinstance event AgentTextDeltaEvent))))

(defk steered-delta [events end]
  {:pre [(: events tuple) (: end PageEnd)] :post [(: % bool)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "足した入力 more が走っているターンに届いたか(替え玉と fake は足した文字を steered: の途中の文字として見せる)。"
  (any (gfor event events (and (isinstance event AgentTextDeltaEvent) (= event.text "steered:more ")))))

(defk launch [#^ Setting s #^ str name prompt #^ dict fields]
  {:pre [(: s Setting) (: name str) (: prompt (| str None)) (: fields dict)] :post [(: % SessionHandle)]
   :tags {:context "headless-codex-adapter-test" :role "program"}}
  "codex の session を起こすため(fields = LaunchEffect のほかの欄 — Python の欄の名で)。"
  (<- handle (LaunchEffect :session-name name :agent-type AgentType.CODEX :work-dir s.work-dir :prompt prompt
                           :lifecycle AgentSessionLifecycle.MULTI-TURN #** fields))
  handle)

(defk of-kind [#^ tuple events kind]
  {:pre [(: events tuple) (: kind type)] :post [(: % tuple)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "出来事の列から、ある種類の出来事だけを順に取り出すため。"
  (tuple (gfor event events :if (isinstance event kind) event)))


;; --- 筋書き ------------------------------------------------------------------------------------

(defk one-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "1 つのターンを公開 effect だけで最後まで読むため。"
  (<- handle (launch s "codex-one" "say hello" {}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(defk check-one-turn [#^ Read done]
  {:pre [(: done Read)] :post [(: % AgentTurnCompleted)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "答えの文字の途中が順に AgentTextDeltaEvent で届き、全文が AgentTextEvent で 1 つ、終わりがちょうど 1 つ届くことを確かめるため。"
  (<- deltas (of-kind done.events AgentTextDeltaEvent))
  (assert (= (tuple (gfor delta deltas delta.text)) ANSWER-PIECES) done.events)
  (<- texts (of-kind done.events AgentTextEvent))
  (assert (= (tuple (gfor text texts text.text)) #(ANSWER)) done.events)
  (<- ends (of-kind done.events AgentTurnEndEvent))
  (assert (= (len ends) 1) done.events)
  (val end done.end)
  (assert (isinstance end AgentTurnCompleted) end)
  (assert (= (. (get ends 0) end) end) end)
  (assert (= end.result-text ANSWER) end)
  (assert (and (isinstance end.resume-from str) end.resume-from) end)
  ;; 入力は始めた時に読まれた(started)と名乗られ、完了の input_refs に同じ参照が載る(agora の入力の勘定)。
  (<- fates (of-kind done.events AgentInputFateEvent))
  (assert (= (tuple (gfor fate fates fate.state)) #(InputFateState.STARTED)) fates)
  (assert (= end.input-refs #((. (get fates 0) input-ref))) end)
  ;; 順: 入力の行方 → 途中の文字 → 全文 → 終わり。
  (val seqs (tuple (gfor event done.events event.seq)))
  (assert (< (. (get fates 0) seq) (. (get deltas 0) seq) (. (get texts 0) seq) (. (get ends 0) seq)) seqs)
  (assert (all (gfor delta deltas (< delta.seq (. (get texts 0) seq)))) seqs)
  end)

(deftest test-headless-codex-text-deltas-reach-agent-text-delta-events-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path one-turn))
  (<- (check-one-turn seen))
  None)

(deftest test-headless-codex-text-deltas-reach-agent-text-delta-events-stub [tmp-path]
  (<- seen (run-on STUB tmp-path one-turn))
  (<- end (check-one-turn seen))
  ;; 替え玉は録った usage の行(この呼び = 入力 10・cache から 0・出力 4)を返す — 入力の側は cache を除いた数。
  (assert (= end.usage (AgentTurnUsage :input-tokens 10 :output-tokens 4 :cache-read-tokens 0)) end.usage)
  (assert (= end.last-call-usage (AgentTurnUsage :input-tokens 10 :output-tokens 4 :cache-read-tokens 0)) end.last-call-usage))


(defk continue-by-resume-from [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "1 つのターンの終わりの resume_from で別の session を起こし、同じ codex の thread が続くことを見るため。"
  (<- first (launch s "codex-first" "say hello" {}))
  (<- one (read-until first ended s.timeout -1))
  (<- (Stop first))
  (<- second (launch s "codex-second" "say hello again" {"resume_from" one.end.resume-from}))
  (<- two (read-until second ended s.timeout -1))
  (<- (Stop second))
  {"one" one.end "two" two.end})

(defk check-continued [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "続きのターンが完了し、同じ thread の id を続きの身元として名乗ることを確かめるため。"
  (val one (get seen "one"))
  (val two (get seen "two"))
  (assert (isinstance one AgentTurnCompleted) one)
  (assert (isinstance two AgentTurnCompleted) two)
  (assert (= two.resume-from one.resume-from) seen)
  None)

(deftest test-headless-codex-continues-the-thread-named-by-resume-from-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path continue-by-resume-from))
  (<- (check-continued seen)))

(deftest test-headless-codex-continues-the-thread-named-by-resume-from-stub [tmp-path]
  (<- seen (run-on STUB tmp-path continue-by-resume-from))
  (<- (check-continued seen)))


(defk interrupt-then-next [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "走っているターンの後に回す入力(NEXT_TURN)を待たせてからターンを止め、止めた終わりの後に待たせた入力が次のターンとして走るのを読むため。"
  (<- handle (launch s "codex-interrupt" SLOW-PROMPT {}))
  (<- before (read-until handle first-delta s.timeout -1))
  (<- (FollowUp handle "say hello" :input-ref "ref-next" :mode TurnInputMode.NEXT-TURN))
  (<- asked (Interrupt handle))
  (assert asked asked)
  (<- done (read-until handle ended s.timeout before.after))
  (<- (Stop handle))
  (Read (+ before.events done.events) done.end done.after))

(defk check-interrupt-then-next [#^ Read done]
  {:pre [(: done Read)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "止めたターンは CLI を残したまま AgentTurnInterrupted で終わり、待たせた入力が次のターンとして完了することを確かめるため。"
  (<- ends (of-kind done.events AgentTurnEndEvent))
  (val outcomes (tuple (gfor event ends event.end)))
  (assert (= (len outcomes) 2) outcomes)
  (val stopped (get outcomes 0))
  (val finished (get outcomes 1))
  (assert (and (isinstance stopped AgentTurnInterrupted) stopped.cli-kept) stopped)
  (assert (isinstance finished AgentTurnCompleted) finished)
  (assert (= finished.input-refs #("ref-next")) finished)
  (assert (= done.end finished) done.end)
  (assert (= finished.resume-from stopped.resume-from) outcomes)
  None)

(deftest test-headless-codex-interrupt-keeps-the-session-and-runs-the-waiting-input-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path interrupt-then-next))
  (<- (check-interrupt-then-next seen)))

(deftest test-headless-codex-interrupt-keeps-the-session-and-runs-the-waiting-input-stub [tmp-path]
  (<- seen (run-on STUB tmp-path interrupt-then-next))
  (<- (check-interrupt-then-next seen)))


(defk inject-into-the-running-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "走っているターンに入力を足し(INJECT)、同じターンの中で読まれるのを見てから止めるため。ターンの無い session への INJECT も見る。"
  (<- handle (launch s "codex-inject" SLOW-PROMPT {}))
  (<- before (read-until handle first-delta s.timeout -1))
  (<- (FollowUp handle "more" :input-ref "ref-more" :mode TurnInputMode.INJECT))
  (<- joined (read-until handle steered-delta s.timeout before.after))
  (<- (Interrupt handle))
  (<- done (read-until handle ended s.timeout joined.after))
  (var refused None)
  (try
    (<- (FollowUp handle "late" :input-ref "ref-late" :mode TurnInputMode.INJECT))
    (except [error NoTurnInFlightError]
      (:= refused error)))
  (<- (Stop handle))
  {"events" (+ before.events joined.events done.events) "end" done.end "refused" refused})

(defk check-injected [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "足した入力が読まれた(started)と名乗られ、止めたターンが AgentTurnInterrupted で終わり、ターンの無い INJECT が型で断られることを
   確かめるため。"
  (<- fates (of-kind (get seen "events") AgentInputFateEvent))
  (assert (in #("ref-more" InputFateState.STARTED) (tuple (gfor fate fates #(fate.input-ref fate.state)))) fates)
  (assert (isinstance (get seen "end") AgentTurnInterrupted) seen)
  (assert (isinstance (get seen "refused") NoTurnInFlightError) seen)
  None)

(deftest test-headless-codex-injected-input-joins-the-running-turn-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path inject-into-the-running-turn))
  (<- (check-injected seen)))

(deftest test-headless-codex-injected-input-joins-the-running-turn-stub [tmp-path]
  (<- seen (run-on STUB tmp-path inject-into-the-running-turn))
  (<- (check-injected seen)))


(defk stop-a-running-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "走っているターンと待たせた入力の在る session を止め、終わりと入力の行方と状態を読むため。"
  (<- handle (launch s "codex-stop" SLOW-PROMPT {}))
  (<- before (read-until handle first-delta s.timeout -1))
  (<- (FollowUp handle "say hello" :input-ref "ref-dropped" :mode TurnInputMode.NEXT-TURN))
  (<- (Stop handle))
  (<- after (Events handle :after-seq before.after :wait-seconds 0.0))
  (<- status (Monitor handle))
  {"events" (+ before.events after.events) "end" after.end "status" status.status})

(defk check-stopped [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "止めたターンは CLI を降ろした AgentTurnInterrupted で終わり、待たせた入力は discarded の行方で閉じ、session は STOPPED になることを
   確かめるため。"
  (val end (get seen "end"))
  (assert (and (isinstance end AgentTurnInterrupted) (not end.cli-kept)) end)
  (<- fates (of-kind (get seen "events") AgentInputFateEvent))
  (assert (in #("ref-dropped" InputFateState.DISCARDED) (tuple (gfor fate fates #(fate.input-ref fate.state)))) fates)
  (assert (= (get seen "status") SessionStatus.STOPPED) seen)
  None)

(deftest test-headless-codex-stop-ends-the-turn-and-discards-waiting-inputs-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path stop-a-running-turn))
  (<- (check-stopped seen)))

(deftest test-headless-codex-stop-ends-the-turn-and-discards-waiting-inputs-stub [tmp-path]
  (<- seen (run-on STUB tmp-path stop-a-running-turn))
  (<- (check-stopped seen)))


(defk images-ride-with-the-prompt [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "prompt に添えた画像が codex のターンの入力に載るのを、画像の数を答えさせて見るため。"
  (val images #((InputImage :mime "image/png" :data-base64 "iVBORw0KGgo=") (InputImage :mime "image/jpeg" :data-base64 "/9j/4AAQ")))
  (<- handle (launch s "codex-images" IMAGES-PHRASE {"attachments" images}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(defk check-images [#^ Read done]
  {:pre [(: done Read)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "codex が 2 つの画像を受けたと答えることを確かめるため。"
  (assert (and (isinstance done.end AgentTurnCompleted) (= done.end.result-text "IMAGES 2")) done.end)
  None)

(deftest test-headless-codex-images-ride-with-the-prompt-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path images-ride-with-the-prompt))
  (<- (check-images seen)))

(deftest test-headless-codex-images-ride-with-the-prompt-stub [tmp-path]
  (<- seen (run-on STUB tmp-path images-ride-with-the-prompt))
  (<- (check-images seen)))


(defk declared-settings [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "起動の model・effort・圧縮の閾値が codex の要求に載るのを、替え玉に読み返させて見るため(agora は codex の起動にも毎回 autocompact を
   載せる — 断ると codex のターンが全部断りになる)。"
  (<- handle (launch s "codex-settings" SETTINGS-PHRASE {"model" "gpt-test" "effort" "high" "autocompact" (AutocompactTokens 600000)}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(deftest test-headless-codex-model-effort-and-compaction-reach-the-app-server-stub [tmp-path]
  (<- done (run-on STUB tmp-path declared-settings))
  (assert (and (isinstance done.end AgentTurnCompleted)
               (= done.end.result-text "SETTINGS effort=high compact=600000 model=gpt-test"))
          done.end))


(defk usage-with-a-named-model [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "model を名指した 1 つのターンを読み、呼びの usage の出来事と終わりの model の窓を見るため。"
  (<- handle (launch s "codex-usage" "say hello" {"model" "gpt-test"}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(deftest test-headless-codex-carries-the-call-usage-and-the-model-window-stub [tmp-path]
  (<- done (run-on STUB tmp-path usage-with-a-named-model))
  (val call (AgentTurnUsage :input-tokens 10 :output-tokens 4 :cache-read-tokens 0))
  (<- calls (of-kind done.events AgentCallUsageEvent))
  (assert (= (tuple (gfor event calls #(event.usage event.model))) #(#(call "gpt-test"))) calls)
  (val end done.end)
  (assert (and (= end.last-call-usage call) (= end.last-call-model "gpt-test")) end)
  (assert (= end.model-windows #((ModelWindow "gpt-test" 258400 None))) end))


(defk refusals [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "codex の adapter が持たない能力を、黙って捨てずに型で断るのを集めるため(名指しの文脈の id・手番の資格・写しの持ち込みと書き出し)。
   前もっての起動は持たないので偽を答える。"
  (var refused {})
  (for [#(name fields) [#("named" {"new_context_id" (NamedContextId "ctx-1")})
                        #("credential" {"turn_credential_ref" "lease-1"})
                        #("snapshot" {"resume_from" "thread-1" "resume_snapshot" "{}"})]]
    (try
      (<- (launch s (+ "codex-refused-" name) "say hello" fields))
      (except [error AgentCapabilityUnsupportedError]
        (:= refused (| refused {name error.capability})))))
  (try
    (<- (ExportContextEffect :agent-type AgentType.CODEX :work-dir s.work-dir :context-id "thread-1"))
    (except [error AgentCapabilityUnsupportedError]
      (:= refused (| refused {"export" error.capability}))))
  (<- idle (launch s "codex-idle" None {}))
  (<- warmed (WarmSession idle))
  (<- (Stop idle))
  {"refused" refused "warmed" warmed})

(deftest test-headless-codex-refuses-what-it-cannot-honour-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path refusals))
  (assert (= (get seen "refused") {"named" "LaunchEffect.new_context_id"
                                   "credential" "LaunchEffect.turn_credential_ref"
                                   "snapshot" "LaunchEffect.resume_snapshot"
                                   "export" "ExportContextEffect(codex)"})
          seen)
  (assert (is (get seen "warmed") False) seen))
