;; 本物の claude CLI(2.1.292 で確かめた)+ 偽の Messages API(tests/fake_subagent_api.hy)で、背景の task を止めた本番と同じ引数の
;; CLI が走らせた subagent の API の呼びを、handler が subagent の transcript から読む事を確かめる検 — card ki-e798888dce4e。本物の
;; API・本物の資格には触らない(ANTHROPIC_BASE_URL は 127.0.0.1 の偽の API・token は偽の文字列・CLI の設定 dir は検の tmp の下)。
;; 起こす引数は本番の handler が組む物そのまま(argv.hy の launch-argv — --settings の env CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 を含む)。
;;
;; 印 e2e・slow(日次と着地の門の -m "not e2e" では走らない)。CLI が無い環境では skip する: CLI = env DOEFF_CLAUDE_CODE_CLI の path、
;; 無ければ PATH の claude。走らせ方(doeff の repo の根で):
;;   uv run pytest packages/doeff-claude-code/tests/test_subagent_transcript_real_cli.hy -m e2e
(require doeff-hy.macros [deftest defk <- val])
(import datetime [timezone])
(import os)
(import pathlib [Path])
(import shutil)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code [ClaudeHome ClaudeSessionSpec FreshSession TurnInput LentCredential])
(import doeff_claude_code [AssistantMessage Completed SubagentTranscript TaskEvent])
(import doeff_claude_code [ClaudeStartTurn ClaudeCloseSession])
(import doeff_claude_code._argv [PER-TURN-SETTINGS-ENV])
(import doeff_claude_code._clock [clock-of])
(import doeff_claude_code [ClaudeCodeHost claude-code-handler])
(import tests.fake_subagent_api [FakeSubagentApi MAIN-MARK AGENT-TOOL-USE-ID])
(import tests.scenario_steps [TurnRecord read-to-end])

(val REAL-CLI (or (.get os.environ "DOEFF_CLAUDE_CODE_CLI") (shutil.which "claude")))
(val TURN-SECONDS 120.0)


(defk subagent-spec [#^ Path tmp-path #^ FakeSubagentApi api]
  {:pre [(: tmp-path Path) (: api FakeSubagentApi)] :post [(: % ClaudeSessionSpec)] :tags {:context "claude-code" :role "program"}}
  "本物の CLI を偽の API へ向ける会話の宣言を作るため: env は PATH と検の HOME と偽の API の向け先だけ(この process の env の資格を
   継がせない)・設定 dir は検の tmp の下・資格は偽の token。"
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (.mkdir (/ tmp-path "user-home") :parents True :exist-ok True)
  (.mkdir (/ tmp-path "shared") :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "shared"))
                                       {"PATH" (.get os.environ "PATH" "/usr/bin:/bin")
                                        "HOME" (str (/ tmp-path "user-home"))
                                        "ANTHROPIC_BASE_URL" api.url
                                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" "1"})
                     :cwd (str work) :model "claude-sonnet-4-5" :settings {"disableAllHooks" True}
                     :credential (LentCredential :access-token "fake-subagent-token" :expires-at 4102444800.0
                                                 :subscription-type "max")))

(defk run-a-subagent [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "本体に道具 Agent を 1 回呼ばせる手番を最後まで読み、会話を閉じるため。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (.format "{} delegate to a subagent" MAIN-MARK) "subagent-1")))
  (<- done (read-to-end started.turn TURN-SECONDS))
  (<- (ClaudeCloseSession sid "test"))
  done)


(deftest test-the-real-cli-without-background-tasks-gives-the-subagent-calls-through-the-transcript [tmp-path]
  {:marks ["e2e" "slow"]
   :skip-if (is REAL-CLI None)
   :skip-reason "本物の claude CLI が無い(env DOEFF_CLAUDE_CODE_CLI か PATH の claude)"}
  ;; 背景の task を止めた CLI(本番の起こし方)は subagent の呼びの行を stdout に出さない(失敗ケース = 変更前は subagent の呼びが
  ;; 手番のどの行にも無かった)。handler は subagent の task の報せの直後に、その subagent の transcript から読んだ呼びを
  ;; SubagentTranscript の行で運ぶ: 偽の API が subagent の呼びに答えた message の id・model・4 種の token と同じ値・親は本体の道具
  ;; Agent の呼び・呼びの刻は時差つき。
  (assert (= (get PER-TURN-SETTINGS-ENV "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") "1"))
  (val api (.start (FakeSubagentApi)))
  (try
    (<- spec (subagent-spec tmp-path api))
    (val host (ClaudeCodeHost #(REAL-CLI) (clock-of (sync-time-handler)) 8 0.0 :launch-timeout 120.0))
    (<- done (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)]
                            (run-a-subagent spec (str (uuid.uuid4)))))
    (assert (isinstance done.end Completed) (repr done.end))
    (val kinds (lfor line done.lines line.kind))
    ;; stdout には subagent の呼びの行が無い(本番の CLI の形)。
    (assert (not (any (gfor kind kinds (and (isinstance kind AssistantMessage) (is-not kind.parent-tool-use-id None))))) kinds)
    (val subagent-calls (lfor call (.seen api) :if (= call.role "subagent") call))
    (assert subagent-calls (.seen api))
    (val found (lfor kind kinds :if (isinstance kind SubagentTranscript) kind))
    (assert (= (len found) 1) kinds)
    (val transcript (get found 0))
    (assert (= #(transcript.parent-tool-use-id transcript.problem) #(AGENT-TOOL-USE-ID None)) (repr transcript))
    ;; 報せの行は答え終えを名乗る(status completed — handler が書き切ったかを transcript の閉じた呼びで判じる印)。total_tokens の値は
    ;; 比べない(本番では最後の呼びの和より多い報せが在る — card ki-3d45f5857239)。
    (assert (any (gfor kind kinds (and (isinstance kind TaskEvent) (= #(kind.task-id kind.status kind.tool-use-id)
                                                                       #(transcript.agent-id "completed" AGENT-TOOL-USE-ID)))))
            kinds)
    (assert (= (lfor call transcript.calls #(call.call-id call.model call.usage.input-tokens call.usage.output-tokens
                                            call.usage.cache-creation-input-tokens call.usage.cache-read-input-tokens))
               (lfor call subagent-calls #(call.message-id call.model call.input call.output call.cache-write call.cache-read)))
            (repr #(transcript (.seen api))))
    (assert (all (gfor call transcript.calls (is-not call.at.tzinfo None))) (repr transcript))
    ;; 呼びの要求の id(card ki-b99ace070bf0): 偽の API が答えの頭 request-id に置いた値を、本体の呼びは stream-json の assistant の
    ;; 行の request_id で、subagent の呼びは transcript の requestId で名乗る — CLI の telemetry の api_request が同じ呼びを名指す id。
    (assert (= (lfor call transcript.calls call.request-id) (lfor call subagent-calls call.request-id)) (repr transcript))
    (val main-calls (lfor call (.seen api) :if (.startswith call.role "main") call))
    (val named (sfor kind kinds :if (and (isinstance kind AssistantMessage) (is kind.parent-tool-use-id None) (is-not kind.usage None))
                     #(kind.message-id kind.request-id)))
    (assert (= named (sfor call main-calls #(call.message-id call.request-id))) (repr #(named main-calls)))
    (finally
      (.close api))))
