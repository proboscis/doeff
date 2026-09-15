;;; 自己圧縮の焦点の検(段 10f 便 2・agora-redesign #82・operator 2026-09-14「that routing agent should compact itself
;;; with some threshold」)。
;;;
;;; 会話の宣言 status.agent.compactAt(文脈の使用率 % の閾値・任意・書き手 agora-conversation)を、直前の手番の文脈の
;;; 使用率が超えていたら、agentd は次の手番を履歴からの再開(rehydrate — 記録の service の本文を最初の本文に畳む・
;;; 9f-4 / 9o-3 の腕そのまま)で起こす。turn-record の usage に同等の欄が無い(和は文脈の大きさではない)ので実測は
;;; agentd: 手番の終わりに材料の末尾(claude = 最後の assistant の usage と result の modelUsage[model].contextWindow /
;;; codex = token_count の last_token_usage と model_context_window)から測り、session ごとに memory に持つ。
;;; 判断は next-arm-for-job の 1 点に条件 compact を足す形(compaction-due)。計器 agentd_compactions_total{conversation}。
;;; ここで撃つのは
;;;   * 実測(純関数): claude の events(streamed)→ tokens と window → %・result が無ければ window 無し = 測れない /
;;;     codex の rollout と app-server の通知 / 会話の行の compactAt の読み(形の外は None)/ compaction-due の 4 象限
;;;   * 腕(純関数): compact の手番は温かい session でも送らず片付けて rehydrate(compacts)・終端の候補は片付けない・
;;;     手番の途中は defer が先(test_sessionhost_acp.py の the-one-decision にも同じ反例)
;;;   * fake で一周(headless): 1 手番目の終わりに実測が state に載る → 会話の行が compactAt を宣言し実測が超えていれば
;;;     2 手番目は温かい session を片付けて rehydrate(prompt に「これまでの会話」)・計器 agentd_compactions_total・
;;;     宣言が無い会話は send のまま・閾値の下も send のまま
;;;   * 追補 3(会話の身元の env): 起こす手番(launch / rehydrate / resume)の params の session_env に AGORA_CONVERSATION_ID と
;;;     AGORA_SEAT_OPENER(会話の行の opener の逐語)が載る・行が読めなければ opener は置かない・呼び手の session_env は残る
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  ArmChoice
  CONVERSATION-ID-ENV
  CONVERSATION-KIND
  DeltaBatch
  MESSAGE-KIND
  METRIC-COMPACTIONS-TOTAL
  NODE-KIND
  PHASE-BOUND
  SEAT-OPENER-ENV
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  compact-at-of
  compaction-due
  context-percent-for
  context-percent-of
  conversation-key-of
  conversation-opener-of
  charter-with-conversation-env
  deltas-of
  mail-turn-text-of
  with-context-percent])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "mac-1")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv AT 1789000000000)


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record :ensure-ascii False) "\n"))


(defn #^ str claude-turn [#^ str text #^ int input-tokens #^ int cache-read #^ (| int None) window]
  "claude の print mode(stream-json)の 1 手番: assistant 2 通(2 通目が末尾 — 実測はこちら)と result(modelUsage の
   contextWindow・None なら result に modelUsage を載せない)。"
  (setv first-usage {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 10})
  (setv last-usage {"input_tokens" input-tokens "output_tokens" 20 "cache_creation_input_tokens" 5
                    "cache_read_input_tokens" cache-read})
  (setv result {"type" "result" "subtype" "success" "is_error" False "usage" last-usage})
  (when (is-not window None)
    (setv (get result "modelUsage") {"claude-haiku-4-5-20251001" {"inputTokens" 9 "contextWindow" 200000}
                                     "claude-opus-5" {"inputTokens" input-tokens "contextWindow" window}}))
  (.join "" [(stream-line {"type" "system" "subtype" "init" "session_id" "s" "model" "claude-opus-5"})
             (stream-line {"type" "assistant"
                           "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "text" "text" "考え中"}] "usage" first-usage}})
             (stream-line {"type" "assistant"
                           "message" {"id" "msg_2" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "text" "text" text}] "usage" last-usage}})
             (stream-line result)]))


(defn #^ AcpRow conversation-row [#^ (| dict None) agent]
  "契約 conversation の 1 行(status.agent = 宣言・None なら欄なし)。"
  (setv status {"state" "open" "turnRoute" "agent-job"})
  (when (is-not agent None)
    (setv (get status "agent") agent))
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{CONVERSATION-KIND}:{CONVERSATION}"
          :kind CONVERSATION-KIND :resource-id CONVERSATION :version "v1" :generation 1 :created-at-ms 0
          :labels {} :payload {}
          :spec {"id" CONVERSATION "opener" "system" "createdAt" 0}
          :status status))


;; ---------------------------------------------------------------------------
;; 実測(純関数)
;; ---------------------------------------------------------------------------

(deftest test-claude-events-measure-the-last-message-against-the-result-context-window
  ;; 末尾の assistant の usage(入力側 + 出力)を、result の modelUsage の**その model** の contextWindow で割る(haiku の
  ;; 副 model の窓は読まない)。60,000 + 5 + 20 / 100,000 = 60%。
  (setv batch (run (deltas-of "claude" "events" (claude-turn "答え" 100 60000 100000) "j-1" 0 AT)))
  (assert (isinstance batch DeltaBatch))
  (assert (= batch.context {"tokens" (+ 100 60000 5 20) "window" 100000}) batch.context)
  (assert (= (run (context-percent-of batch.context)) 60))
  ;; 窓を超えた実測は 100 で止める(比較は「以上」なので意味は変わらない)。
  (assert (= (run (context-percent-of {"tokens" 250000 "window" 200000})) 100))
  ;; result に modelUsage が無い(古い CLI・途中で落ちた手番)= 窓が無い = 測れない(None)。
  (setv blind (run (deltas-of "claude" "events" (claude-turn "答え" 100 60000 None) "j-1" 0 AT)))
  (assert (= blind.context {"tokens" (+ 100 60000 5 20) "window" None}) blind.context)
  (assert (is (run (context-percent-of blind.context)) None))
  (assert (is (run (context-percent-of None)) None))
  ;; tui の transcript(streamed でない)には result の行が無いので窓は無い。
  (setv transcript (run (deltas-of "claude" "transcript" (claude-turn "答え" 100 60000 100000) "j-1" 0 AT)))
  (assert (= (get transcript.context "window") None) transcript.context))


(deftest test-codex-rollout-and-app-server-measure-the-last-response
  ;; rollout の token_count: last_token_usage(入力 + 出力)と model_context_window。累計(total)は文脈の大きさではない。
  (setv rollout (.join "" [(stream-line {"type" "event_msg"
                                         "payload" {"type" "token_count"
                                                    "info" {"total_token_usage" {"input_tokens" 900000 "output_tokens" 1000 "cached_input_tokens" 800000}
                                                            "last_token_usage" {"input_tokens" 120000 "output_tokens" 500 "cached_input_tokens" 100000}
                                                            "model_context_window" 258400}}})]))
  (setv batch (run (deltas-of "codex" "transcript" rollout "j-1" 0 AT)))
  (assert (= batch.context {"tokens" 120500 "window" 258400}) batch.context)
  (assert (= (run (context-percent-of batch.context)) 46))
  ;; app-server の thread/tokenUsage/updated: tokenUsage.last と modelContextWindow。
  (setv notice (stream-line {"jsonrpc" "2.0" "method" "thread/tokenUsage/updated"
                             "params" {"threadId" "t" "turnId" "u"
                                       "tokenUsage" {"total" {"inputTokens" 5 "outputTokens" 5}
                                                     "last" {"inputTokens" 200000 "cachedInputTokens" 1000 "outputTokens" 400}
                                                     "modelContextWindow" 258400}}}))
  (setv events (run (deltas-of "codex" "events" notice "j-1" 0 AT)))
  (assert (= events.context {"tokens" 200400 "window" 258400}) events.context)
  (assert (= (run (context-percent-of events.context)) 77))
  ;; 窓を名乗らない通知は測れない(None)。
  (setv bare (stream-line {"jsonrpc" "2.0" "method" "thread/tokenUsage/updated"
                           "params" {"tokenUsage" {"total" {} "last" {"inputTokens" 10}}}}))
  (assert (is (run (context-percent-of (. (run (deltas-of "codex" "events" bare "j-1" 0 AT)) context))) None)))


(deftest test-compact-at-reads-the-declared-percent-and-nothing-else
  ;; 契約の形(0〜100 の整数)だけを読む。行なし・欄なし・bool・範囲の外・文字列は None(圧縮しない)。
  (assert (= (run (compact-at-of (conversation-row {"model" "gpt-6-astra" "effort" "low" "compactAt" 60}))) 60))
  (assert (= (run (compact-at-of (conversation-row {"compactAt" 0}))) 0))
  (assert (is (run (compact-at-of (conversation-row {"model" "claude-opus-5"}))) None))
  (assert (is (run (compact-at-of (conversation-row None))) None))
  (assert (is (run (compact-at-of None)) None))
  (for [bad [True 101 -1 "60" 60.5]]
    (assert (is (run (compact-at-of (conversation-row {"compactAt" bad}))) None) bad))
  (assert (= (run (conversation-key-of CONVERSATION)) f"default:conversation:{CONVERSATION}")))


(deftest test-compaction-is-due-only-with-both-a-threshold-and-a-measurement-at-or-above-it
  (assert (run (compaction-due 60 60)))
  (assert (run (compaction-due 60 99)))
  (assert (not (run (compaction-due 60 59))))
  (assert (not (run (compaction-due None 99))) "宣言の無い会話は圧縮しない")
  (assert (not (run (compaction-due 60 None))) "測れていない session は圧縮しない")
  (assert (run (compaction-due 0 0)) "閾値 0 = 毎手番を縮めて始める")
  ;; state の cache: 同じ session は置き換え・None は消す・別の session は残る。
  (setv state (run (with-context-percent (initial-state) "s-1" 70)))
  (setv state (run (with-context-percent state "s-2" 10)))
  (assert (= (run (context-percent-for state "s-1")) 70))
  (setv state (run (with-context-percent state "s-1" 30)))
  (assert (= (run (context-percent-for state "s-1")) 30))
  (assert (= (run (context-percent-for state "s-2")) 10))
  (setv state (run (with-context-percent state "s-1" None)))
  (assert (is (run (context-percent-for state "s-1")) None))
  (assert (is (run (context-percent-for state None)) None))
  (assert (= state.context-by-session #(#("s-2" 10)))))


;; ---------------------------------------------------------------------------
;; fake で一周(headless — 実測は events の末尾から)
;; ---------------------------------------------------------------------------

(defn #^ dict env-of [#^ dict params]
  "launch / resume の params の session_env を object として読む(型を絞る — 違えば赤)。"
  (setv env (.get params "session_env"))
  (assert (isinstance env dict) f"session_env が object でない: {env !r}")
  env)


(defn #^ AcpRow row-of [#^ str namespace #^ str kind #^ str resource-id #^ dict spec #^ (| dict None) status]
  (AcpRow :namespace namespace :key f"{namespace}:{kind}:{resource-id}" :kind kind :resource-id resource-id
          :version "v1" :generation 1 :created-at-ms 500 :labels {} :payload {} :spec spec :status status))


(defn #^ AcpRow message-row [#^ str message-id #^ str body #^ int at]
  (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND message-id
          {"id" message-id "to" CONVERSATION "from" "operator" "kind" "note" "items" [] "body" body "refs" []
           "sha256" (* "0" 64) "at" at}
          {"state" "delivered"}))


(defn #^ AcpRow bound-row [#^ str job-id #^ list inputs #^ (| str None) predecessor]
  (setv spec {"subject" CONVERSATION "inputs" inputs
              "charter" {"agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}})
  (when (is-not predecessor None)
    (setv (get spec "affinity") {"predecessor" predecessor}))
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id spec
          {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"} "conditions" []}))


(defclass World []
  "backend = headless の器(events file が実況の正本)+ 記録の service の fake で agentd を一周させる最小の世界。"
  (defn #^ None __init__ [self #^ (| dict None) agent]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes" :backend-kind "headless"
                                        :stream-capability "events" :record-enabled True))
    (setv self.record-service (FakeRecord))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND NODE
                               {"name" NODE "labels" {} "capacity" 2 "streamCapability" "events"}
                               {"state" "joined"}))
    (.put-row self.acp (conversation-row agent))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-a"}))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))
    (setv self.local (FakeLocal :now-ms AT))
    (setv self.state (initial-state))
    None)

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.record-service.dispatch self.acp.dispatch self.custody.dispatch
                     self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ str sid [self #^ str job-id]
    (setv row (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))
    (setv status (if (isinstance row.status dict) row.status {}))
    (setv handle (.get status "sessionHandle"))
    (assert (isinstance handle dict) status)
    (setv sid (.get handle "sessionId"))
    (assert (isinstance sid str) handle)
    sid)

  (defn #^ str run-first-turn [self #^ int cache-read]
    "1 手番目を温かい headless の session で走らせ、末尾の実測(cache-read + 125 / 100,000)を載せて終える。戻り = session の id。"
    (.put-row self.acp (message-row "m-1" "合言葉は ひまわり" (- AT 500)))
    (.put-row self.acp (bound-row "j-1" ["m-1"] None))
    (.tick self 0)
    (setv sid (.sid self "j-1"))
    (setv (get self.local.transcripts f"/events/{sid}.events.jsonl") (claude-turn "覚えました" 100 cache-read 100000))
    (.tick self 1000)
    (.finish-turn self.sessions sid (+ self.local.now-ms 200))
    (.tick self 1000)
    (assert (= self.state.jobs #()) self.local.logs)
    sid))


(deftest test-a-turn-end-measures-the-context-and-the-next-turn-over-compact-at-rehydrates
  ;; 会話が compactAt 60 を宣言し、1 手番目の末尾が 60,125 / 100,000 = 60% → 2 手番目は温かい session を片付けて
  ;; 履歴から再開(prompt に「これまでの会話」)し、計器 agentd_compactions_total{conversation} を 1 行出す。
  (setv world (World {"model" "claude-opus-5" "compactAt" 60}))
  (setv warm (.run-first-turn world 60000))
  (assert (= (run (context-percent-for world.state warm)) 60) world.state.context-by-session)
  (setv asked (message-row "m-2" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] warm))
  (.tick world 1000)
  (assert (= world.sessions.cleanups [warm]) world.local.logs)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 2) world.local.logs)
  (setv launch (get world.sessions.launches -1))
  (setv prompt (get launch "prompt"))
  (assert (isinstance prompt str))
  (assert (.startswith prompt "start\n\nこれまでの会話(会話の記録の service と ACP の郵便から") prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  ;; 段 10 lane 10r 追補: 郵便は見出し 1 行 + 本文(judgment.mail-turn-text-of の 1 点)。
  (assert (.endswith prompt (+ "\n\n" (run (mail-turn-text-of "m-2" asked.spec "合言葉は何でしたか")))) "郵便の見出しと本文は最後(headless の 1 手番目)")
  (setv fresh (.sid world "j-2"))
  (assert (!= fresh warm))
  (assert (any (gfor line world.local.logs (in "starts compacted" line))) world.local.logs)
  (setv compactions (lfor m world.local.metrics :if (= (get m "metric") METRIC-COMPACTIONS-TOTAL) m))
  (assert (= (len compactions) 1) world.local.metrics)
  (assert (= (get (get compactions 0) "conversation") CONVERSATION))
  (assert (= (get (get compactions 0) "sessionId") warm))
  (setv arm-metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
  (assert (= (get arm-metric "arm") "rehydrate"))
  ;; 圧縮した手番の終わりの実測は新しい session に載り、片付けた session の値は残っても読まれない(候補が変わる)。
  (assert (= world.acp.history-reads [CONVERSATION])))


(deftest test-below-the-threshold-or-without-a-declaration-the-warm-session-is-kept
  ;; 閾値の下(30% < 60)は温かい session へ send(片付けない・記録の service も読まない)。
  (setv world (World {"model" "claude-opus-5" "compactAt" 60}))
  (setv warm (.run-first-turn world 30000))
  (assert (= (run (context-percent-for world.state warm)) 30))
  (.put-row world.acp (message-row "m-2" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] warm))
  (.tick world 1000)
  (assert (= world.sessions.cleanups []) world.local.logs)
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (assert (= (.sid world "j-2") warm))
  (assert (= (lfor m world.local.metrics :if (= (get m "metric") METRIC-COMPACTIONS-TOTAL) m) []))
  (assert (= world.record-service.reads []))
  ;; 宣言の無い会話は 99% でも send のまま(圧縮は会話の宣言があってはじめて効く)。
  (setv plain (World {"model" "claude-opus-5"}))
  (setv kept (.run-first-turn plain 99000))
  (assert (= (run (context-percent-for plain.state kept)) 99))
  (.put-row plain.acp (message-row "m-2" "合言葉は何でしたか" (+ plain.local.now-ms 100)))
  (.put-row plain.acp (bound-row "j-2" ["m-2"] kept))
  (.tick plain 1000)
  (assert (= plain.sessions.cleanups []) plain.local.logs)
  (assert (= (.sid plain "j-2") kept))
  (assert (= (lfor m plain.local.metrics :if (= (get m "metric") METRIC-COMPACTIONS-TOTAL) m) [])))


;; ---------------------------------------------------------------------------
;; 追補 3(依頼者 2026-09-14 17:1x): 手番の CLI の env に会話の身元
;; ---------------------------------------------------------------------------

(deftest test-charter-env-carries-the-conversation-id-and-the-opener
  ;; 純関数: 呼び手の session_env は残し、AGORA_CONVERSATION_ID と AGORA_SEAT_OPENER を置く。opener が読めなければ置かない。
  (setv charter {"agent_type" "claude" "session_env" {"FOO" "1"}})
  (setv env (get (run (charter-with-conversation-env charter CONVERSATION "system")) "session_env"))
  (assert (= env {"FOO" "1" CONVERSATION-ID-ENV CONVERSATION SEAT-OPENER-ENV "system"}) env)
  (setv bare (get (run (charter-with-conversation-env {"agent_type" "claude"} CONVERSATION None)) "session_env"))
  (assert (= bare {CONVERSATION-ID-ENV CONVERSATION}) bare)
  (assert (= (get charter "session_env") {"FOO" "1"}) "元の charter の session_env を変えた")
  (assert (= (run (conversation-opener-of (conversation-row None))) "system"))
  (assert (is (run (conversation-opener-of None)) None)))


(deftest test-every-incarnation-arm-puts-the-conversation-identity-in-the-process-env
  ;; fake で一周: launch(1 手番目)・rehydrate(圧縮)の params の session_env に会話の id と opener(会話の行 = system)が載る。
  (setv world (World {"model" "claude-opus-5" "compactAt" 60}))
  (setv warm (.run-first-turn world 60000))
  (setv launched (env-of (get world.sessions.launches 0)))
  (assert (= (get launched CONVERSATION-ID-ENV) CONVERSATION) launched)
  (assert (= (get launched SEAT-OPENER-ENV) "system") launched)
  (.put-row world.acp (message-row "m-2" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] warm))
  (.tick world 1000)
  (setv rehydrated (env-of (get world.sessions.launches -1)))
  (assert (= (get rehydrated CONVERSATION-ID-ENV) CONVERSATION) rehydrated)
  (assert (= (get rehydrated SEAT-OPENER-ENV) "system") rehydrated)
  ;; 会話の行が無い世界(旧の会話・読めない拍)でも id は載り、opener は置かない。
  (setv orphan (World None))
  (.delete-row orphan.acp f"{AGORA-KINDS-NAMESPACE}:{CONVERSATION-KIND}:{CONVERSATION}")
  (.run-first-turn orphan 10)
  (setv env (env-of (get orphan.sessions.launches 0)))
  (assert (= (get env CONVERSATION-ID-ENV) CONVERSATION) env)
  (assert (not-in SEAT-OPENER-ENV env) env))
