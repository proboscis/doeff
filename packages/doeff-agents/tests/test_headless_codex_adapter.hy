;; headless の codex の adapter(handlers/headless_codex.hy)の検 — doeff-agents の公開 effect だけを撃つ Program を、層 2(doeff-codex)の
;; handler の組だけ替えて走らせる(card acp:kanban-issue:ki-534a081e32eb)。
;;
;;   fake  doeff-codex の fake の handler + 仮想の時計。process も API も使わない。
;;   stub  doeff-codex の本番の handler + 替え玉の app-server(packages/doeff-codex/tests/stub_cli/codex_app_server.py — 録った codex
;;         0.162.1 の行を、id を差し替えて返す)+ 壁の時計。
;;
;; 守る事: codex のターンの答えの文字の途中が AgentTextDeltaEvent として順に届く(崩れると agora の画面は codex の答えを途中から流せない —
;; 答えは文字が届くごとに画面へ流す — 利用者の指摘 2026-09-10・2026-10-10 の答え A)。出来事の種類と欄は claude の
;; adapter と同じ形(agora の absorb-event を変えずに受けられる)。入力が読まれた事は AgentInputFateEvent(started)と完了の input_refs で
;; 届く(agora は入力の勘定をこの 2 つで数える)。
;; 筋書きの Program は doeff_codex を import しない(公開 effect と、組み立ての部品 headless_compose だけ)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "headless-codex-adapter-test" :role "program"})
(import dataclasses [dataclass])
(import pathlib [Path])
(import sys)
(import collections.abc [Callable])
(import doeff [run with_handlers])
(import doeff_agents)
(import doeff_core_effects [scheduled])
(import doeff_time [GetMonotonic SimClock sim-time-handler sync-time-handler])
(import doeff_agents [AgentType AgentSessionLifecycle])
(import doeff_agents [LaunchEffect FollowUp Interrupt Events Monitor Stop ExportContextEffect WarmSession SessionHandle AgentEventPage TurnInputMode InputFateState InputImage NamedContextId ModelWindow AgentTextEvent AgentTextDeltaEvent AgentInputFateEvent AgentTurnEndEvent AgentCallUsageEvent AgentToolCallStartedEvent AgentToolUseEvent AgentToolResultEvent AgentTurnCompleted AgentTurnFailed AgentTurnInterrupted AgentTurnLost AgentTurnUsage AgentAccountLimit AgentProviderCapacity AgentCapabilityUnsupportedError NoTurnInFlightError RedeemTurnCredentialEffect TurnCredential CodexTurnCredential TurnCredentialUnavailable TurnCredentialUnavailableError])
(import doeff_agents._effects.agent [AutocompactTokens])
(import doeff_hy.frozen [freeze-json])
(import doeff_agents [SessionStatus])
;; 層 2 との組は doeff-agents の組み立ての部品で作る(この検も doeff_codex を import しない)。
(import doeff_agents._handlers.headless_compose [FakeCodexWorld FakeCodexReply CodexTurnInput CodexApprovalPolicy CodexSandboxMode
                                                CodexUsageLimitHit CodexServerOverloadedHit codex-process-layer fake-codex-process-layer
                                                codex-adapter])

(val FAKE "fake")
(val STUB "stub")
;; 同じ層 2 と adapter を、doeff_agents の公開の口(codex_process_layer_handler・fake_codex_process_layer_handler・codex_agent_adapter_handler)
;; で組む backend。agora は module の名に下の層の語が出る組み立ての部品を import できない(agora-controllers の境界の検 — 名の語の禁止)ので、
;; claude と同じく公開の口から組む(card acp:kanban-issue:ki-f63f65c153b0)。
(val PUBLIC-FAKE "public-fake")
(val PUBLIC-STUB "public-stub")
(val REPO-PACKAGES (. (Path __file__) (resolve) parent parent parent))
(val STUB-PATH (str (/ REPO-PACKAGES "doeff-codex" "tests" "stub_cli" "codex_app_server.py")))
(val PAGE-WAIT 1.0)
(val MAX-PAGES 2000)
;; 筋書きの言葉(替え玉の app-server の規則と同じ): SLOW = 止めまで途中の文字を出し続ける / 画像を数える言い方 / 宣言を読み返す言い方。
(val SLOW-PROMPT "SLOW please")
(val IMAGES-PHRASE "Count the attached images.")
(val SETTINGS-PHRASE "Tell the settings.")
;; 公開の口の backend の adapter が切って起こす codex の機能(組み立ての部品の backend は何も切らない — 宣言が argv に載るかを両方で見る)。
(val PUBLIC-DISABLED #("hooks"))
;; 子の process から見た CODEX_HOME の形を答えさせる言い方(替え玉の規則 — 借りた資格の家の形を見る)。
(val HOME-PHRASE "Tell the home.")
;; 録った断りのターンを流させる言い方(替え玉の規則 — 録った codex 0.162.1 の利用枠切れ・429 のターンをそのまま流す)と、利用枠切れの
;; 録りで codex が名乗った値(戻りの刻 = 録りの偽の上流が header で名乗った 5 時間の窓の刻・文 = codex の誤りの文)。
(val USAGE-LIMIT-PROMPT "USAGE-LIMIT please")
(val RATE-LIMIT-PROMPT "RATE-LIMIT please")
(val USAGE-RESETS-AT 1893456000)
(val USAGE-LIMIT-TEXT (+ "You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
                         "https://chatgpt.com/settings/usage to purchase more credits or try again at Jan 1st, 2030 12:00 AM."))
;; 提供側の混雑で断られたターンを流させる言い方(替え玉の規則 — 録った codex 0.162.1 の server-overloaded の 1 つ目のターンを流す)と、
;; codex が名乗る文・会話の宣言の model(codex の誤りは model を名乗らない — adapter は宣言の model を運ぶ)。
(val OVERLOADED-PROMPT "SERVER-OVERLOADED please")
(val OVERLOADED-TEXT "Selected model is at capacity. Please try a different model.")
(val OVERLOADED-MODEL "gpt-6-astra")
;; 道具を呼ぶターンの言い方(替え玉の規則 — 録った codex 0.162.1 の道具の呼びのターン tool-calls を流す)。
(val TOOLS-PROMPT "TOOL-CALLS please")
;; code mode の host を使えない codex のターンを流させる言い方(替え玉の規則 — 録った codex 0.162.1 の code-mode-unavailable の、ターンの
;; 前の警告とターンを流す)と、その警告の文(録りの host の無い置き場の path を含む — fake は同じ文を筋書きで受ける)。
(val CODE-MODE-PROMPT "CODE-MODE please")
(val CODE-MODE-WARNING (+ "Code Mode is unavailable because failed to spawn code-mode host "
                          "/home/ubuntu/.codex-rec-cm15/codex-host-absent/codex-code-mode-host: host executable was not found. "
                          "Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`."))
(val ANSWER-PIECES #("Hel" "lo, " "wor" "ld."))
(val ANSWER "Hello, world.")
;; 元の CODEX_HOME の dir の名(検ごとの tmp の dir の下 — config.toml を 1 つ置く)と、借りた口座の auth.json の中身(検の作り物 —
;; 預かり所が封じる形。refresh token は貸し手の印の作り物)。
(val CODEX-HOME-DIR "codex-home")
(val LENT-AUTH-JSON "{\"auth_mode\":\"chatgpt\",\"OPENAI_API_KEY\":null,\"tokens\":{\"id_token\":null,\"access_token\":\"test-access\",\"refresh_token\":\"NO.T.test-marker\",\"account_id\":\"acct-test\"},\"last_refresh\":null}")
;; 参照 → 引き換えの答え(検の環境が参照を出した側の代わりに答える)。
(val LENT {"lease-codex" (CodexTurnCredential LENT-AUTH-JSON None)
           "lease-claude" (TurnCredential "sk-ant-oat01-never-placed" None)
           "lease-gone" (TurnCredentialUnavailable "returned")})


;; --- 解釈器(composition root) --------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Setting []
  "筋書きの宣言: work-dir = 作業 dir / timeout = 1 つの読みの上限(秒)/ codex-home = 元の CODEX_HOME / world = fake の層 2 の世界
   (stub は None — 層 2 の宣言に載った物を見る口)。"
  (#^ Path work-dir)
  (#^ float timeout)
  (#^ Path codex-home)
  (#^ object world))

;; 引数に残す理由: 答えの表は検の環境の値で、検ごとに同じ表を参照を出した側の代わりに使う(Ask で読む設定の handler は無い)。
(defhandler lent-credentials [answers]
  (RedeemTurnCredentialEffect [credential-ref]
    (resume (get answers credential-ref))))

(defk respond [#^ CodexTurnInput input]
  {:pre [(: input CodexTurnInput)] :post [(: % FakeCodexReply)] :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "fake の筋書きの答えを入力から決めるため(替え玉の app-server と同じ規則)。"
  (cond
    (in "SLOW" input.text) (FakeCodexReply :pieces #("slow-0 " "slow-1 ") :hold True)
    (in IMAGES-PHRASE input.text) (FakeCodexReply :pieces #((.format "IMAGES {}" (len input.images))))
    ;; 利用枠切れ: 層 2 の事実(5 時間の窓・戻りの刻・codex の文)を筋書きで渡す — 替え玉は録った行から本番の handler が同じ事実を作る。
    (in USAGE-LIMIT-PROMPT input.text) (FakeCodexReply :pieces #()
                                                       :usage-limit (CodexUsageLimitHit :minutes 300 :resets-at USAGE-RESETS-AT
                                                                                        :text USAGE-LIMIT-TEXT))
    ;; 提供側の混雑: 層 2 の事実(codex の文)を筋書きで渡す — 替え玉は録った行から本番の行の分類が同じ事実を作る。
    (in OVERLOADED-PROMPT input.text) (FakeCodexReply :pieces #() :server-overloaded (CodexServerOverloadedHit :text OVERLOADED-TEXT))
    ;; 道具を呼べない process: 層 2 の終わり(codex の警告の文)を筋書きで渡す — 替え玉は録った警告から本番の handler が同じ終わりを作る。
    (in CODE-MODE-PROMPT input.text) (FakeCodexReply :pieces #() :tools-lost CODE-MODE-WARNING)
    True (FakeCodexReply :pieces ANSWER-PIECES)))

(defk handlers-for [#^ str backend #^ Path tmp-path world]
  {:pre [(: backend str) (: tmp-path Path) (: world (| FakeCodexWorld None))] :post [(: % list)]
   :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "解釈器の handler の組(先頭が外側): 時間の handler → 資格の引き換えの答え手 → 層 2 の handler → headless の codex の adapter。"
  ;; 子の process は自前の env で起きるので、根の conftest の固定(PYTHONDONTWRITEBYTECODE=1)を継がない — 明示で渡し、替え玉の app-server
  ;; の python が checkout(.venv の site-packages)へ .pyc を書かない(書くと根の conftest の session の終わりの見張りが赤)。
  (val env {"PATH" "/usr/bin:/bin" "HOME" (str tmp-path) "CODEX_HOME" (str (/ tmp-path CODEX-HOME-DIR)) "PYTHONDONTWRITEBYTECODE" "1"})
  (<- adapter (codex-adapter env CodexApprovalPolicy.NEVER CodexSandboxMode.READ-ONLY #()))
  (match backend
    "fake" (do (<- layer (fake-codex-process-layer world))
               [(sim-time-handler :clock (SimClock)) (lent-credentials LENT) layer adapter])
    "stub" (do (<- layer (codex-process-layer #(sys.executable STUB-PATH) 30.0))
               [(sync-time-handler) (lent-credentials LENT) layer adapter])
    "public-fake" [(sim-time-handler :clock (SimClock)) (lent-credentials LENT) (doeff_agents.fake-codex-process-layer-handler :world world)
                   (! (public-adapter env))]
    "public-stub" [(sync-time-handler) (lent-credentials LENT)
                   (doeff_agents.codex-process-layer-handler :command #(sys.executable STUB-PATH) :launch-timeout 30.0) (! (public-adapter env))]
    _ (raise (ValueError backend))))

(defk public-adapter [#^ dict env]
  {:pre [(: env dict)] :post [(: % Callable)] :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "公開の口の adapter(組み立ての部品の codex-adapter と同じ許可 NEVER・sandbox READ-ONLY に、切る機能 PUBLIC-DISABLED を足した宣言 —
   agora は公開の口から hook を切った codex を起こす)を作るため。"
  (doeff_agents.codex-agent-adapter-handler :env env :approval-policy CodexApprovalPolicy.NEVER :sandbox CodexSandboxMode.READ-ONLY
                                            :disabled-features PUBLIC-DISABLED))

(defk run-on [#^ str backend #^ Path tmp-path #^ Callable scenario]
  {:pre [(: backend str) (: tmp-path Path) (: scenario Callable)] :post [(: % (| Read dict))]
   :tags {:context "headless-codex-adapter-test" :role "entry"}}
  "scenario(Setting) → Program を、層 2 の handler の組 + headless の codex の adapter の下で、検ごとの scheduler で走らせるため。"
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (val codex-home (/ tmp-path CODEX-HOME-DIR))
  (.mkdir codex-home :exist-ok True)
  (.write-text (/ codex-home "config.toml") "# 検の元の CODEX_HOME\n" :encoding "utf-8")
  (val world (if (in backend #(FAKE PUBLIC-FAKE)) (FakeCodexWorld (fn [input] (run (respond input)))) None))
  (<- stack (handlers-for backend tmp-path world))
  (run (scheduled (with_handlers stack (scenario (Setting work 30.0 codex-home world))))))


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

(deftest test-the-public-codex-handlers-carry-the-text-deltas-fake [tmp-path]
  ;; 失敗ケース(card acp:kanban-issue:ki-f63f65c153b0): doeff_agents の公開の口だけで組んだ層 2 と adapter でも、答えの文字の途中が届く。
  (<- seen (run-on PUBLIC-FAKE tmp-path one-turn))
  (<- (check-one-turn seen))
  None)

(deftest test-the-public-codex-handlers-carry-the-text-deltas-stub [tmp-path]
  (<- seen (run-on PUBLIC-STUB tmp-path one-turn))
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


(defk named-stop-after-a-steer [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "走っているターンに入力を足した直後に、その入力を名指して止めるため(agora の割り込み送信の形 — InterruptEffect の for_input)。codex の
   層 2 は入力を読んだかを名乗らないので、名指しても名指さない止めと同じに止まる(I3 — adapter が steer で合成した行方を「読んだ」と読み
   替えて止めを省くと、ターンが終わらない・設計検証 lt-2F61HHZ2YZKD0HY0GV1YG0ZDN0 の盲検 B)。"
  (<- handle (launch s "codex-named-stop" SLOW-PROMPT {}))
  (<- before (read-until handle first-delta s.timeout -1))
  (<- (FollowUp handle "more" :input-ref "ref-more" :mode TurnInputMode.INJECT))
  (<- joined (read-until handle steered-delta s.timeout before.after))
  (<- asked (Interrupt handle :for-input "ref-more"))
  (<- done (read-until handle ended s.timeout joined.after))
  (<- (Stop handle))
  {"asked" asked "end" done.end})

(defk check-named-stop-stops [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "codex の名指した止めが受けられ、ターンが AgentTurnInterrupted で終わる事を確かめるため。"
  (assert (is (get seen "asked") True) seen)
  (assert (isinstance (get seen "end") AgentTurnInterrupted) seen)
  None)

(deftest test-headless-codex-a-named-stop-still-stops-the-turn-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path named-stop-after-a-steer))
  (<- (check-named-stop-stops seen)))

(deftest test-headless-codex-a-named-stop-still-stops-the-turn-stub [tmp-path]
  (<- seen (run-on STUB tmp-path named-stop-after-a-steer))
  (<- (check-named-stop-stops seen)))


;; turn/steer を「走っているターンが無い」と断らせる言い方(替え玉の規則 — 本物の codex 0.162.1 の断りが、ターンの終わりの通知より先に
;; 届く順と後に届く順を写す。doeff-codex の tests/stub_cli/codex_app_server.py の STEER_REFUSALS)。
(val STEER-REFUSED-FIRST "STEER-REFUSED-FIRST")
(val STEER-ENDED-FIRST "STEER-ENDED-FIRST")

(defk two-ends [events end]
  {:pre [(: events tuple) (: end PageEnd)] :post [(: % bool)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "ターンの終わりが 2 つ届いたか(断られた入力を次のターンとして出し直す筋書きの、読みの止めの条件)。"
  (<- ends (of-kind events AgentTurnEndEvent))
  (>= (len ends) 2))

(defk inject-while-codex-ends-the-turn [#^ Setting s #^ str word #^ bool stop-first]
  {:pre [(: s Setting) (: word str) (: stop-first bool)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "目印 word の替え玉のターン(turn/steer を「走っているターンが無い」と断る)が走る所へ入力を足し(INJECT)、断りが型で届くのを見てから、
   断られた入力を次のターンの入力として出し直して(上の層がする回し方)、次のターンの終わりまで読むため。stop-first = 断りの時点で
   ターンが終わっていない言い方なので、出し直した後にそのターンを止める。"
  (<- handle (launch s "codex-steer-race" (+ word " please") {}))
  (<- before (read-until handle first-delta s.timeout -1))
  (var refused None)
  (try
    (<- (FollowUp handle "more" :input-ref "ref-more" :mode TurnInputMode.INJECT))
    (except [error NoTurnInFlightError]
      (:= refused error)))
  (<- (FollowUp handle "say hello" :input-ref "ref-next" :mode TurnInputMode.NEXT-TURN))
  (when stop-first
    (<- (Interrupt handle)))
  (<- done (read-until handle two-ends s.timeout before.after))
  (<- (Stop handle))
  {"events" (+ before.events done.events) "end" done.end "refused" refused})

(defk check-inject-while-codex-ends-the-turn [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "ターンの終わりと行き違った INJECT が、手番を失敗させる AgentError でなく NoTurnInFlightError で断られ、断られた入力は読まれたと名乗られず、
   出し直した入力が次のターンとして完了することを確かめるため。"
  (assert (isinstance (get seen "refused") NoTurnInFlightError) seen)
  (<- fates (of-kind (get seen "events") AgentInputFateEvent))
  (val started (tuple (gfor fate fates :if (= fate.state InputFateState.STARTED) fate.input-ref)))
  (assert (not-in "ref-more" started) fates)
  (assert (in "ref-next" started) fates)
  (val end (get seen "end"))
  (assert (and (isinstance end AgentTurnCompleted) (= end.input-refs #("ref-next"))) end)
  None)

(deftest test-headless-codex-inject-refused-before-the-end-notice-is-no-turn-in-flight-stub [tmp-path]
  (<- seen (run-on STUB tmp-path (fn [s] (inject-while-codex-ends-the-turn s STEER-REFUSED-FIRST True))))
  (<- (check-inject-while-codex-ends-the-turn seen)))

(deftest test-headless-codex-inject-refused-after-the-end-notice-is-no-turn-in-flight-stub [tmp-path]
  (<- seen (run-on STUB tmp-path (fn [s] (inject-while-codex-ends-the-turn s STEER-ENDED-FIRST False))))
  (<- (check-inject-while-codex-ends-the-turn seen)))


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
               (= done.end.result-text "SETTINGS effort=high compact=600000 model=gpt-test disabled="))
          done.end))

(deftest test-the-public-codex-handlers-carry-the-disabled-features-to-the-app-server-stub [tmp-path]
  ;; 公開の口の adapter が宣言した切る機能が、層 2 が起こす app-server の argv の --disable に載る(替え玉が argv を読み返す)。
  (<- done (run-on PUBLIC-STUB tmp-path declared-settings))
  (assert (and (isinstance done.end AgentTurnCompleted)
               (= done.end.result-text "SETTINGS effort=high compact=600000 model=gpt-test disabled=hooks"))
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
  "codex の adapter が持たない能力を、黙って捨てずに型で断るのを集めるため(名指しの文脈の id・写しの持ち込みと書き出し)。
   前もっての起動は持たないので偽を答える。"
  (var refused {})
  (for [#(name fields) [#("named" {"new_context_id" (NamedContextId "ctx-1")})
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
                                   "snapshot" "LaunchEffect.resume_snapshot"
                                   "export" "ExportContextEffect(codex)"})
          seen)
  (assert (is (get seen "warmed") False) seen))


(defk credential-turns [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "ターンの資格の参照を持つ起動を、引き換えの答えごとに走らせて集めるため: codex の口座 = そのターンの process の家に置かれる・
   claude の形の資格と引き換えられない参照 = 起こさずに型で断る。"
  (<- handle (launch s "codex-lent" HOME-PHRASE {"turn_credential_ref" "lease-codex"}))
  (<- done (read-until handle ended s.timeout -1))
  ;; fake の層 2 は家を作らないので、層 2 へ届いた宣言の家に資格が載ったかを見る(stub は子の process から家の形を見る)。
  (val placed (if (is s.world None) None (tuple (gfor thread (.values s.world.threads) thread.spec.home.auth-json))))
  (val homes (/ s.codex-home ".credential-homes"))
  (val during (if (.exists homes) (len (list (.iterdir homes))) 0))
  (<- (Stop handle))
  (val after (if (.exists homes) (len (list (.iterdir homes))) 0))
  (var refused {})
  (for [ref ["lease-claude" "lease-gone"]]
    (try
      (<- (launch s (+ "codex-" ref) "say hello" {"turn_credential_ref" ref}))
      (except [error TurnCredentialUnavailableError]
        (:= refused (| refused {ref error.reason})))))
  {"end" done.end "placed" placed "during" during "after" after "refused" refused})

(defk check-credential-refusals [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "引き換えられない参照と codex の形でない資格は、起こさずに TurnCredentialUnavailableError で断られる(文に資格の値は出ない)ことを
   確かめるため。"
  (val refused (get seen "refused"))
  (assert (= (set refused) #{"lease-claude" "lease-gone"}) refused)
  (assert (= (get refused "lease-gone") "returned") refused)
  (assert (in "TurnCredential" (get refused "lease-claude")) refused)
  (assert (not-in "sk-ant" (get refused "lease-claude")) refused)
  None)

(deftest test-headless-codex-places-the-lent-account-in-the-process-home-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path credential-turns))
  (assert (isinstance (get seen "end") AgentTurnCompleted) seen)
  ;; 引き換えた auth.json の中身が、層 2 の宣言の家にそのまま載った。
  (assert (= (get seen "placed") #(LENT-AUTH-JSON)) "the lent auth.json did not reach the declaration")
  (<- (check-credential-refusals seen)))

(deftest test-headless-codex-places-the-lent-account-in-the-process-home-stub [tmp-path]
  (<- seen (run-on STUB tmp-path credential-turns))
  ;; 子の process の CODEX_HOME は、元の CODEX_HOME の下の資格の家: dir 0700・auth.json 0600(借りた口座)・config.toml と会話の記録は
  ;; 元への link。ターンの間は家が 1 つ在り、session を止めて process が降りたら消える。
  (val end (get seen "end"))
  (assert (and (isinstance end AgentTurnCompleted)
               (= end.result-text "HOME dir=0o700 auth=0o600 account=acct-test config=link sessions=link"))
          end)
  (assert (= (get seen "during") 1) seen)
  (assert (= (get seen "after") 0) seen)
  (<- (check-credential-refusals seen)))


(defk refused-turn [#^ Setting s #^ str prompt]
  {:pre [(: s Setting) (: prompt str)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "上流が断ったターン(替え玉が録った断りのターンを流す言い方 prompt)を 1 つ最後まで読むため。"
  (<- handle (launch s "codex-refused" prompt {}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(defk usage-limited-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "口座の利用枠切れで断られたターンを読むため。"
  (<- done (refused-turn s USAGE-LIMIT-PROMPT))
  done)

(defk rate-limited-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "上流の 429(口座の利用枠切れでない断り)で断られたターンを読むため。"
  (<- done (refused-turn s RATE-LIMIT-PROMPT))
  done)

(defk check-usage-limit [#^ Read done]
  {:pre [(: done Read)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "利用枠切れで失敗したターンの終わりが、尽きた窓(5 時間の窓)と戻りの刻と codex の文を AgentAccountLimit で運ぶことを確かめるため —
   agora は claude と同じ道(account_limit の在る失敗 = 口座の利用枠切れ・戻りの刻まで口座を外す)で口座を替える・待つ。"
  (<- ends (of-kind done.events AgentTurnEndEvent))
  (assert (= (len ends) 1) done.events)
  (val end done.end)
  (assert (isinstance end AgentTurnFailed) end)
  (assert (= end.account-limit (AgentAccountLimit :window "five_hour" :resets-at USAGE-RESETS-AT :text USAGE-LIMIT-TEXT)) end)
  (assert (= end.detail USAGE-LIMIT-TEXT) end)
  None)

(deftest test-headless-codex-usage-limit-carries-the-account-limit-stub [tmp-path]
  ;; 失敗ケース(card acp:kanban-issue:ki-a59d80e0afa9): 録った codex 0.162.1 の利用枠切れのターン(枠の知らせ・誤り usageLimitExceeded・
  ;; 失敗の終わり)が、AgentTurnFailed.account_limit に届く。
  (<- done (run-on STUB tmp-path usage-limited-turn))
  (<- (check-usage-limit done)))

(deftest test-the-public-codex-handlers-carry-the-account-limit-stub [tmp-path]
  ;; agora が組む公開の口でも同じ(card acp:kanban-issue:ki-a59d80e0afa9)。
  (<- done (run-on PUBLIC-STUB tmp-path usage-limited-turn))
  (<- (check-usage-limit done)))

(deftest test-headless-codex-usage-limit-carries-the-account-limit-fake [tmp-path]
  ;; 層 2 の fake が筋書きの利用枠切れの事実で失敗のターンを出せば、adapter は本番と同じ AgentAccountLimit を出す(fake は adapter の
  ;; 窓の名の表を知らない — 長さで受ける・設計検証 lt-4FZFGSA5AJ7DGD7BZ4656WY6HS の盲検 A・B)。
  (<- done (run-on FAKE tmp-path usage-limited-turn))
  (<- (check-usage-limit done)))

(deftest test-the-public-codex-handlers-carry-the-account-limit-fake [tmp-path]
  (<- done (run-on PUBLIC-FAKE tmp-path usage-limited-turn))
  (<- (check-usage-limit done)))

(deftest test-headless-codex-rate-limit-is-not-an-account-limit-stub [tmp-path]
  ;; 上流の 429(誤り responseTooManyFailedAttempts — 口座の利用枠切れでない断り)のターンは、口座の利用枠切れを名乗らずに失敗で終わる
  ;; (口座を外す訳にしない)。
  (<- done (run-on STUB tmp-path rate-limited-turn))
  (val end done.end)
  (assert (isinstance end AgentTurnFailed) end)
  (assert (is end.account-limit None) end)
  (assert (in "429" end.detail) end))


(defk completed-end [events end]
  {:pre [(: events tuple) (: end PageEnd)] :post [(: % bool)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "頁の終わりが完了の終わりか(失敗の終わりの後の、次の入力のターンを読み終える止めの条件)。"
  (isinstance end AgentTurnCompleted))


(defk overloaded-turn-then-retry [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "提供側の混雑で断られたターンを最後まで読み、同じ session の次の入力(上の層の試し直し)が次のターンとして完了するのを読むため。"
  (<- handle (launch s "codex-overloaded" OVERLOADED-PROMPT {"model" OVERLOADED-MODEL}))
  (<- failed (read-until handle ended s.timeout -1))
  (<- (FollowUp handle "say hello" :input-ref "ref-retry" :mode TurnInputMode.NEXT-TURN))
  (<- after (read-until handle completed-end s.timeout failed.after))
  (<- (Stop handle))
  {"failed" failed "after" after})

(defk check-overloaded-turn [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "提供側の混雑で断られたターンの終わりが、会話の宣言の model と codex の文を AgentProviderCapacity で運び(口座の利用枠切れでない)、
   同じ session の次の入力のターンが完了する事を確かめるため — agora は混雑の事実の在る失敗を、少し後に同じ session で試し直す
   (card ki-843475dd8f8b・本番 2026-10-22 21:59 JST の会話 c-QM2DD91FG7T13716VSC9C6GKV2)。"
  (val end (. (get seen "failed") end))
  (assert (isinstance end AgentTurnFailed) end)
  (assert (= end.provider-capacity (AgentProviderCapacity :model OVERLOADED-MODEL :text OVERLOADED-TEXT)) end)
  (assert (is end.account-limit None) end)
  (assert (= end.detail OVERLOADED-TEXT) end)
  (val after (. (get seen "after") end))
  (assert (isinstance after AgentTurnCompleted) after)
  (assert (= after.result-text ANSWER) after)
  (assert (= after.resume-from end.resume-from) #(after end))
  None)

(deftest test-headless-codex-provider-capacity-is-carried-and-the-session-retries-stub [tmp-path]
  ;; 失敗ケース(card ki-843475dd8f8b): 録った codex 0.162.1 の混雑のターン(誤り serverOverloaded・失敗の終わり)が
  ;; AgentTurnFailed.provider_capacity に届き、同じ session の試し直しが完了する。
  (<- seen (run-on STUB tmp-path overloaded-turn-then-retry))
  (<- (check-overloaded-turn seen)))

(deftest test-the-public-codex-handlers-carry-the-provider-capacity-stub [tmp-path]
  ;; agora が組む公開の口でも同じ。
  (<- seen (run-on PUBLIC-STUB tmp-path overloaded-turn-then-retry))
  (<- (check-overloaded-turn seen)))

(deftest test-headless-codex-provider-capacity-is-carried-and-the-session-retries-fake [tmp-path]
  ;; 層 2 の fake が筋書きの混雑の事実で失敗のターンを出せば、adapter は本番と同じ AgentProviderCapacity を出す。
  (<- seen (run-on FAKE tmp-path overloaded-turn-then-retry))
  (<- (check-overloaded-turn seen)))

(deftest test-headless-codex-rate-limit-is-not-provider-capacity-stub [tmp-path]
  ;; 上流の 429・口座の利用枠切れのターンは、提供側の混雑を名乗らない(試し直しの訳にしない)。
  (for [refused #(rate-limited-turn usage-limited-turn)]
    (<- done (run-on STUB tmp-path refused))
    (assert (isinstance done.end AgentTurnFailed) done.end)
    (assert (is done.end.provider-capacity None) done.end)))

(defk toolless-turn-then-next [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "道具を呼べない process のターン(code mode が使えない警告)を最後まで読み、同じ session の次の入力が次のターンとして完了するのを
   読むため。"
  (<- handle (launch s "codex-toolless" CODE-MODE-PROMPT {}))
  (<- failed (read-until handle ended s.timeout -1))
  (<- (FollowUp handle "say hello" :input-ref "ref-next" :mode TurnInputMode.NEXT-TURN))
  (<- after (read-until handle completed-end s.timeout failed.after))
  (<- (Stop handle))
  {"failed" failed "after" after})

(defk check-toolless-turn [#^ dict seen]
  {:pre [(: seen dict)] :post [(: % None)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "道具を呼べない process のターンが、completed でなく codex の警告の文を detail に持つ AgentTurnFailed(口座の利用枠切れでない)で
   終わり、道具を呼べなかった model の文だけの答えを届けない事と、同じ session の次の入力のターンが同じ thread の続きとして完了する
   事を確かめるため — agora は失敗のターン(cli-failed)として記録し、責任 turns-keep-succeeding が数える(card ki-86b1443b22b6)。"
  (val failed (get seen "failed"))
  (val end failed.end)
  (assert (isinstance end AgentTurnFailed) end)
  (assert (= end.detail CODE-MODE-WARNING) end)
  (assert (is end.account-limit None) end)
  (<- ends (of-kind failed.events AgentTurnEndEvent))
  (assert (= (tuple (gfor event ends event.end)) #(end)) failed.events)
  (<- texts (of-kind failed.events AgentTextEvent))
  (assert (= texts #()) failed.events)
  (val after (get seen "after"))
  (assert (isinstance after.end AgentTurnCompleted) after.end)
  (assert (= after.end.result-text ANSWER) after.end)
  (assert (= after.end.input-refs #("ref-next")) after.end)
  (assert (= after.end.resume-from end.resume-from) #(end after.end))
  None)

(deftest test-headless-codex-a-turn-without-tools-ends-failed-stub [tmp-path]
  ;; 失敗ケース(card ki-86b1443b22b6・本番の実例 = 10-15 の会話 c-HFAMV0E6RHQJ773QTWP1HJ58T2): 録った codex 0.162.1 の、code mode の
  ;; host を使えない process のターン(ターンの前の警告の後に、文だけの答えと completed の終わり)が、completed でなく失敗で届く。
  (<- seen (run-on STUB tmp-path toolless-turn-then-next))
  (<- (check-toolless-turn seen)))

(deftest test-the-public-codex-handlers-end-a-turn-without-tools-failed-stub [tmp-path]
  ;; agora が組む公開の口でも同じ。
  (<- seen (run-on PUBLIC-STUB tmp-path toolless-turn-then-next))
  (<- (check-toolless-turn seen)))

(deftest test-headless-codex-a-turn-without-tools-ends-failed-fake [tmp-path]
  (<- seen (run-on FAKE tmp-path toolless-turn-then-next))
  (<- (check-toolless-turn seen)))


(defk tool-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "道具を呼ぶ 1 つのターンを公開 effect だけで最後まで読むため。"
  (<- handle (launch s "codex-tools" TOOLS-PROMPT {}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(deftest test-headless-codex-tool-items-reach-tool-use-and-tool-result-events-stub [tmp-path]
  ;; 失敗ケース(card acp:kanban-issue:ki-77e76c7c07ce): 録った codex 0.162.1 の道具の item(命令の成功・失敗・apply_patch・web 検索)が、
  ;; claude の adapter と同じ出来事(AgentToolUseEvent の ToolCall・AgentToolResultEvent の ToolAnswer)で届く — agora は absorb-event を
  ;; 変えずに画面の道具カードへ出す。道具の名は codex の item の種類の名、命令は画面の shell の card が読む command の欄。
  (<- done (run-on STUB tmp-path tool-turn))
  (assert (isinstance done Read) done)
  (assert (and (isinstance done.end AgentTurnCompleted) (= done.end.result-text "Tools done.")) done.end)
  (<- uses (of-kind done.events AgentToolUseEvent))
  (val calls (tuple (gfor use uses call use.tool-calls call)))
  (assert (= (tuple (gfor call calls #(call.id call.name)))
             #(#("call_0" "commandExecution") #("call_1" "commandExecution") #("call_2" "fileChange") #("ws_1" "webSearch")))
          calls)
  (val echo (get calls 0))
  (val fail (get calls 1))
  (val patch (get calls 2))
  (val search (get calls 3))
  (assert (= (get echo.input "command") "/bin/bash -lc 'echo tool-out'") echo.input)
  (assert (.endswith (get echo.input "cwd") "/cwd") echo.input)
  (assert (= (get fail.input "command") "/bin/bash -lc 'ls no-such-file'") fail.input)
  (val edits (get patch.input "changes"))
  (assert (= (len edits) 1) patch.input)
  (val edit (get edits 0))
  (assert (= #((get edit "kind") (get edit "diff")) #("add" "hello\n")) patch.input)
  (assert (.endswith (get edit "path") "/cwd/note.txt") patch.input)
  (assert (= (dict search.input) {"query" "doeff effects" "action" "search"}) search.input)
  ;; 結果: 出力の文と誤りの印 — 失敗した命令(終了 code 2)は誤り。ファイルの変更と検索は出力の文を持たない。
  (<- results (of-kind done.events AgentToolResultEvent))
  (val answers (tuple (gfor result results answer result.answers answer)))
  (assert (= (tuple (gfor answer answers #(answer.id answer.text answer.is-error)))
             #(#("call_0" "tool-out\n" False)
               #("call_1" "ls: cannot access 'no-such-file': No such file or directory\n" True)
               #("call_2" "" False)
               #("ws_1" "" False)))
          answers)
  ;; 順: どの呼びもその結果より前に届き、道具は答えの文字の途中より前(codex が走らせた順)。web 検索は語の無い始まりで「呼んでいる」
  ;; (AgentToolCallStartedEvent)を先に出し、語は呼び(AgentToolUseEvent)で届く。
  (val use-seqs (dfor use uses call use.tool-calls call.id use.seq))
  (val result-seqs (dfor result results answer result.answers answer.id result.seq))
  (assert (all (gfor #(id seq) (.items use-seqs) (< seq (get result-seqs id)))) #(use-seqs result-seqs))
  (<- deltas (of-kind done.events AgentTextDeltaEvent))
  (assert (< (max (.values result-seqs)) (. (get deltas 0) seq)) #(result-seqs deltas))
  (<- starts (of-kind done.events AgentToolCallStartedEvent))
  (assert (= (tuple (gfor start starts #(start.id start.name))) #(#("ws_1" "webSearch"))) starts)
  (assert (< (. (get starts 0) seq) (get use-seqs "ws_1")) #(starts use-seqs)))


;; 後ろで走る命令の筋書きの言い方(替え玉の規則 — 録った codex 0.162.1 の 2 つのターン background-command を流す)。1 つ目のターンで
;; 命令 call_bg を始めて答え、2 つ目のターンの間にその命令が終わる(命令の終わりの行は 1 つ目のターンを名乗る)。
(val BACKGROUND-PROMPT "BG-COMMAND please")
(val BACKGROUND-WAIT-PROMPT "BG-WAIT please")

(defk background-turns [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % dict)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "命令を後ろで走らせて終わるターンと、その命令が終わる次のターンを、公開 effect だけで読むため。"
  (<- handle (launch s "codex-background" BACKGROUND-PROMPT {}))
  (<- first (read-until handle ended s.timeout -1))
  (<- (FollowUp handle BACKGROUND-WAIT-PROMPT :input-ref "ref-wait" :mode TurnInputMode.NEXT-TURN))
  (<- second (read-until handle ended s.timeout first.after))
  (<- (Stop handle))
  {"first" first "second" second})

(deftest test-headless-codex-background-command-result-reaches-a-tool-result-event-stub [tmp-path]
  ;; 失敗ケース(card acp:kanban-issue:ki-77e76c7c07ce の設計検証・盲検 A): 前のターンで始めて後ろで走った命令の終わりは、前のターンを
  ;; 名乗って次のターンの間に届く。その結果も AgentToolResultEvent で届く(届かないと画面の道具カードは結果の無いまま残る)。
  (<- read (run-on STUB tmp-path background-turns))
  (assert (isinstance read dict) read)
  (val first (get read "first"))
  (val second (get read "second"))
  (assert (and (isinstance first.end AgentTurnCompleted) (isinstance second.end AgentTurnCompleted)) #(first.end second.end))
  (<- uses (of-kind first.events AgentToolUseEvent))
  (assert (= (tuple (gfor use uses call use.tool-calls #(call.id (get call.input "command"))))
             #(#("call_bg" "/bin/bash -lc 'sleep 1; echo late-1; sleep 1; echo late-2; sleep 1; echo late-3'")))
          uses)
  (<- results (of-kind (+ first.events second.events) AgentToolResultEvent))
  (val answers (tuple (gfor result results answer result.answers #(answer.id answer.text answer.is-error))))
  (assert (= answers #(#("call_bg" "late-1\nlate-2\nlate-3\n" False))) answers)
  ;; 結果は次のターンの出来事として届く(前のターンの終わりの後)。
  (<- later (of-kind second.events AgentToolResultEvent))
  (assert (= (len later) 1) second.events))


;; ほかの道具を呼ぶターンの言い方(替え玉の規則 — 録った codex 0.162.1 のほかの道具の呼びのターン other-tools を流す。子の agent の
;; thread の行も録った順のまま混ざる)。
(val OTHER-TOOLS-PROMPT "OTHER-TOOLS please")

(defk other-tools-turn [#^ Setting s]
  {:pre [(: s Setting)] :post [(: % Read)] :tags {:context "headless-codex-adapter-test" :role "program"}}
  "ほかの道具(MCP・動的な道具・画像を見る道具・子の agent)を呼ぶ 1 つのターンを公開 effect だけで最後まで読むため。"
  (<- handle (launch s "codex-other-tools" OTHER-TOOLS-PROMPT {}))
  (<- done (read-until handle ended s.timeout -1))
  (<- (Stop handle))
  done)

(deftest test-headless-codex-other-tool-items-reach-tool-use-and-tool-result-events-stub [tmp-path]
  ;; 失敗ケース(card ki-c09b93b72bff): 録った codex 0.162.1 の mcpToolCall・dynamicToolCall・imageView・collabAgentToolCall の item も、
  ;; commandExecution ほかと同じ出来事(AgentToolUseEvent の ToolCall・AgentToolResultEvent の ToolAnswer)で届く — 道具の名は codex の
  ;; item の種類の名、命令は item の中身の欄(MCP と動的な道具の引数は道具が決めた形のまま)。子の agent の thread の答えは親の答えに
  ;; 混ざらない。
  (<- done (run-on STUB tmp-path other-tools-turn))
  (assert (isinstance done Read) done)
  (assert (and (isinstance done.end AgentTurnCompleted) (= done.end.result-text "Others done.")) done.end)
  (<- uses (of-kind done.events AgentToolUseEvent))
  (val calls (tuple (gfor use uses call use.tool-calls call)))
  (assert (= (tuple (gfor call calls #(call.id call.name)))
             #(#("call_other_0" "mcpToolCall") #("call_other_1" "dynamicToolCall") #("call_other_2" "imageView")
               #("call_other_3" "collabAgentToolCall") #("call_other_4" "collabAgentToolCall")))
          calls)
  (val mcp (get calls 0))
  (val dynamic (get calls 1))
  (val image (get calls 2))
  (val spawn (get calls 3))
  (val wait (get calls 4))
  (assert (= mcp.input (freeze-json {"server" "recorder" "tool" "echo" "arguments" {"text" "hi"}})) mcp.input)
  (assert (= dynamic.input (freeze-json {"tool" "lookup_ticket" "arguments" {"id" "T-1"}})) dynamic.input)
  (assert (= (list image.input) ["path"]) image.input)
  (assert (.endswith (get image.input "path") "/cwd/pixel.png") image.input)
  (assert (= spawn.input (freeze-json {"tool" "spawnAgent" "prompt" "SUB-AGENT say hi"})) spawn.input)
  (val child (get wait.input "receivers" 0))
  (assert (= wait.input (freeze-json {"tool" "wait" "receivers" [child]})) wait.input)
  ;; 結果: MCP の道具と動的な道具は答えの文・画像を見る道具は空の文・子の agent の道具は子ごとの状態(と最後の答え)の行。
  (<- results (of-kind done.events AgentToolResultEvent))
  (val answers (tuple (gfor result results answer result.answers #(answer.id answer.text answer.is-error))))
  (assert (= answers #(#("call_other_0" "echo: hi" False)
                       #("call_other_1" "ticket T-1: open" False)
                       #("call_other_2" "" False)
                       #("call_other_3" (+ child ": pendingInit") False)
                       #("call_other_4" (+ child ": completed — Sub done.") False)))
          answers)
  (val use-seqs (dfor use uses call use.tool-calls call.id use.seq))
  (val result-seqs (dfor result results answer result.answers answer.id result.seq))
  (assert (all (gfor #(id seq) (.items use-seqs) (< seq (get result-seqs id)))) #(use-seqs result-seqs))
  ;; 子の agent の答え(Sub done.)は親の答えの文に届かない。
  (<- texts (of-kind done.events AgentTextEvent))
  (assert (= (tuple (gfor text texts text.text)) #("Others done.")) texts))
