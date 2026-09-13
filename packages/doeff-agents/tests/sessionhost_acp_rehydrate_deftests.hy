;;; 会話の引き継ぎ(profile / 機体を変えた手番)の焦点の検(段 8q・agora-redesign #51・ADR-DOE-AGENTS-012 R20)。
;;;
;;; 既知の形 = virtual actor の状態の移送(会話 = actor・手番 = activation): cache(温かい session / transcript)を
;;; 保つのは同じ機体 ∧ 同じ家の時だけで、それ以外は正本(ACP の会話の記録)を読み込む履歴からの再開(operator 決定 #54 逐語
;;; "i want cache kept when both machine and a profile is not changed. in other cases, i think i need to accept
;;; the fact that cache gets invalidated")。ここで撃つのは
;;;   * 「これまでの会話」の畳み(時刻順・kind ごと・この手番の inputs を除く・frame は畳まない・上限で古い手番から
;;;     要約せず落とし、落とした数と全文の在処を名乗る)
;;;   * node の観測(sessions の account・transcripts = 終端で transcript が残る会話の最新)
;;;   * fake で agentd を一周: 家の違う温かい session は片付けて履歴からの再開 / 器に無い predecessor は履歴からの再開 / 同じ家の
;;;     片付いた session は --resume / resume の断りは同じ id で履歴からの再開 / turn-record の spec に sessionId
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
  HistoryFold
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  SessionRefused
  SessionView
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  rehydrate-history-of
  session-observations-of
  transcript-candidates-of])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "mac-1")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv OTHER "c-01ARZ3NDEKTSV4RRFFQ69G5FAW")
(setv THIRD "c-01ARZ3NDEKTSV4RRFFQ69G5FAX")
(setv AT 1789000000000)


(defn #^ AcpRow message-row [#^ str message-id #^ str to #^ str sender #^ str body #^ int at]
  "契約 message の 1 通(配達済み)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}"
          :kind MESSAGE-KIND :resource-id message-id :version "v1" :generation 1 :created-at-ms at
          :labels {} :payload {}
          :spec {"id" message-id "to" to "from" sender "kind" "note" "items" [] "body" body "refs" []
                 "sha256" (* "0" 64) "at" at}
          :status {"state" "delivered"}))


(defn #^ AcpRow record-row [#^ str job-id #^ str conversation #^ list entries #^ int at]
  "契約 turn-record の 1 行(ended・entries = 手番の出来事の列)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}"
          :kind TURN-RECORD-KIND :resource-id job-id :version "v1" :generation 2 :created-at-ms at
          :labels {} :payload {}
          :spec {"conversationId" conversation "agentJobId" job-id "node" "elsewhere" "profile" "p" "model" "m"}
          :status {"state" "ended" "entries" entries}))


(defn #^ AcpRow bound-row [#^ str job-id #^ list inputs #^ str account #^ (| str None) predecessor]
  "自分に結ばれた Bound の agent-job(binding.account = 借りる家・affinity.predecessor = 会話の前の session)。"
  (setv spec {"subject" CONVERSATION
              "inputs" inputs
              "charter" {"agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}})
  (when (is-not predecessor None)
    (setv (get spec "affinity") {"predecessor" predecessor}))
  (AcpRow :namespace AGENT-JOB-NAMESPACE :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"
          :kind AGENT-JOB-KIND :resource-id job-id :version "v1" :generation 1 :created-at-ms 500
          :labels {} :payload {} :spec spec
          :status {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" account}
                   "conditions" []}))


(defn #^ str assistant-line [#^ str text]
  "claude の transcript の assistant の 1 行(本文 1 block)。"
  (+ (json.dumps {"type" "assistant"
                  "message" {"role" "assistant" "id" "msg_1" "model" "claude-opus-5"
                             "content" [{"type" "text" "text" text}]}}
                 :ensure-ascii False)
     "\n"))


(defn #^ SessionView view-of [#^ str sid #^ str status #^ str conversation #^ int started]
  "帰属(agentd の欄)を持つ温かい session の眺め。"
  (SessionView :session-id sid :agent-type "claude" :status status :work-dir "/work" :lifecycle "multi_turn"
               :conversation {"session_id" sid} :effective-identity {"CLAUDE_CONFIG_DIR" "/homes/claude/acct"}
               :result-payload None :terminal-cause None :turn-ended-at-ms 10
               :launch-attribution {"agentd" {"conversationId" conversation "agentJobId" "j" "account" None
                                              "home" {"account" None "binding" None} "arm" "launch"}}
               :started-at-ms started))


(defn #^ dict dict-at [#^ dict obj #^ str key]
  "JSON の object の欄を object として読む(型を絞る — 違えば赤)。"
  (setv item (.get obj key))
  (assert (isinstance item dict) f"{key} は object でない: {item !r}")
  item)


(defn #^ str str-at [#^ dict obj #^ str key]
  "JSON の object の欄を文字列として読む(型を絞る — 違えば赤)。"
  (setv item (.get obj key))
  (assert (isinstance item str) f"{key} は文字列でない: {item !r}")
  item)


(defclass World []
  "fake の 4 handler + 値の宣言 + Node の行(agentd を一周させる最小の世界)。"

  (defn #^ None __init__ [self #^ str backend]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes" :backend-kind backend))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"
                               :kind NODE-KIND :resource-id NODE :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" NODE "labels" {} "capacity" 2 "streamCapability" "frames"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-a" "other" "sk-ant-oat01-b"}))
    (setv self.sessions (FakeSessions :backend-kind backend))
    (setv self.local (FakeLocal :now-ms AT))
    (setv self.state (initial-state))
    None)

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch
                     self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ dict job-status [self #^ str job-id]
    (setv row (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))
    (if (isinstance row.status dict) row.status {}))

  (defn #^ str sid [self #^ str job-id]
    (str-at (dict-at (.job-status self job-id) "sessionHandle") "sessionId"))

  (defn #^ AcpRow record [self #^ str job-id]
    (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}"))

  (defn #^ dict observations [self]
    (setv row (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"))
    (dict-at (if (isinstance row.status dict) row.status {}) "observations")))


(defn #^ str run-first-turn [#^ World world]
  "1 手番目(家 acct)を温かい session で走らせて手番の終わりまで進める。戻り = その session の id。"
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 500)))
  (.put-row world.acp (bound-row "j-1" ["m-1"] "acct" None))
  (.tick world 0)
  (setv sid (.sid world "j-1"))
  (setv path f"/homes/claude/acct/projects/-work/{sid}.jsonl")
  (setv (get world.local.transcripts path) (+ (.get world.local.transcripts path "") (assistant-line "覚えました")))
  (.tick world 1000)
  (.finish-turn world.sessions sid (+ world.local.now-ms 200))
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.local.logs)
  sid)


;; ---------------------------------------------------------------------------
;; 「これまでの会話」の畳み(純関数)
;; ---------------------------------------------------------------------------

(deftest test-history-fold-orders-by-time-folds-kinds-and-skips-this-turn
  ;; 郵便と entries を時刻順(同じ時刻は郵便が先)に並べ、会話へ届いた郵便ごとに手番に割る。この手番の
  ;; inputs・別の会話・tui の画面の断面(frame)は畳まない。
  (setv messages #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
                   (message-row "m-x" OTHER "operator" "別の会話の本文" AT)
                   (message-row "m-r" "operator" CONVERSATION "報告の本文" (+ AT 3000))
                   (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ AT 9000))))
  (setv records #((record-row "j-1" CONVERSATION
                              [{"seq" 0 "at" (+ AT 1000) "kind" "text" "text" "覚えました"}
                               {"seq" 1 "at" (+ AT 1100) "kind" "tool_use" "toolName" "Read" "summary" "a.txt"}
                               {"seq" 2 "at" (+ AT 1200) "kind" "tool_result" "summary" "中身" "isError" True}
                               {"seq" 3 "at" (+ AT 1300) "kind" "system" "text" "hook failed"}
                               {"seq" 4 "at" (+ AT 1400) "kind" "error" "text" "API error"}
                               {"seq" 5 "at" (+ AT 1500) "kind" "frame" "text" "❯ pane"}]
                              AT)
                  (record-row "j-x" OTHER [{"seq" 0 "at" (+ AT 1000) "kind" "text" "text" "別の会話の答え"}] AT)))
  (setv fold (run (rehydrate-history-of CONVERSATION messages records #("m-2") 65536)))
  (assert (isinstance fold HistoryFold))
  (assert (= #(fold.kept-turns fold.dropped-turns fold.dropped-items) #(1 0 0)) fold)
  (assert (.startswith fold.text f"これまでの会話(ACP の記録から組んだ写し・会話 {CONVERSATION}・古い順):") fold.text)
  (setv lines (lfor line (cut (.splitlines fold.text) 1 None) :if line line))
  (assert (= (lfor line lines (get (.split line "] " 1) 1))
             [f"operator → {CONVERSATION}(note): 合言葉は ひまわり"
              "agent: 覚えました"
              "agent の道具 Read: a.txt"
              "道具の結果(誤り): 中身"
              "system: hook failed"
              "誤り: API error"
              f"{CONVERSATION} → operator(note): 報告の本文"])
          fold.text)
  (assert (.startswith (get lines 0) "[2026-09-") (get lines 0))
  (assert (= fold.size-bytes (len (.encode fold.text "utf-8"))))
  ;; 記録の無い会話は空(最初の本文を変えない)。
  (assert (= (. (run (rehydrate-history-of THIRD messages records #() 65536)) text) "")))


(deftest test-history-fold-drops-the-oldest-turns-and-names-where-the-rest-is
  ;; 上限を超えたら古い手番から要約せずに落とし、落とした手番と項の数と全文の在処を名乗る。
  (setv messages (tuple (lfor n (range 6)
                              (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "あ" 200))
                                           (+ AT (* n 1000))))))
  (setv records #((record-row "j-a" CONVERSATION
                              (lfor n (range 6) {"seq" n "at" (+ AT (* n 1000) 10) "kind" "text" "text" f"答え {n}"})
                              AT)))
  (setv whole (run (rehydrate-history-of CONVERSATION messages records #("m-5") 65536)))
  (assert (= #(whole.kept-turns whole.dropped-turns) #(5 0)) whole)
  (assert (not-in "問い 5" whole.text) "この手番の inputs は畳まない")
  (assert (in "答え 5" whole.text))
  (setv small (run (rehydrate-history-of CONVERSATION messages records #("m-5") 2000)))
  (assert (<= small.size-bytes 2000) small.size-bytes)
  (assert (>= small.dropped-turns 1) small)
  (assert (= (+ small.kept-turns small.dropped-turns) 5))
  (assert (= small.dropped-items (* 2 small.dropped-turns)))
  (assert (not-in "問い 0" small.text))
  (assert (in "答え 5" small.text) "新しい手番は残る")
  (assert (in f"古い手番 {small.dropped-turns} 件" small.text) small.text)
  (assert (in f"kind turn-record(spec.conversationId = {CONVERSATION})" small.text) small.text)
  ;; 最新の手番 1 つだけで超える: その手番の先頭を落として末尾を残し、切ったことを名乗る。
  (setv huge #((message-row "m-h" CONVERSATION "operator" (+ "はじまり" (* "い" 3000) "しっぽ") AT)))
  (setv cut-fold (run (rehydrate-history-of CONVERSATION huge #() #() 1500)))
  (assert (<= cut-fold.size-bytes 1500) cut-fold.size-bytes)
  (assert (in "しっぽ" cut-fold.text))
  (assert (not-in "はじまり" cut-fold.text))
  (assert (in "最新の手番の先頭を落としました" cut-fold.text)))


;; ---------------------------------------------------------------------------
;; node の観測(純関数)
;; ---------------------------------------------------------------------------

(deftest test-observations-carry-account-and-the-newest-ended-session-per-conversation
  (setv views #((view-of "s-old" "stopped" CONVERSATION 1)
                (view-of "s-new" "stopped" CONVERSATION 2)
                (view-of "s-live" "running" OTHER 3)
                (view-of "s-other-old" "exited" OTHER 1)
                (view-of "s-third" "done" THIRD 5)))
  (setv live (run (session-observations-of views)))
  (assert (= live [{"conversationId" OTHER "sessionId" "s-live" "state" "idle" "account" None}]) live)
  (assert (= (lfor view (run (transcript-candidates-of views live 10)) view.session-id) ["s-third" "s-new"]))
  (assert (= (lfor view (run (transcript-candidates-of views live 1)) view.session-id) ["s-third"])))


;; ---------------------------------------------------------------------------
;; fake で一周
;; ---------------------------------------------------------------------------

(deftest test-another-home-drops-the-warm-cache-and-rehydrates
  ;; operator 決定 #54: 同じ機体でも家(profile の家)が違う手番は温かい session に送らず、片付けて ACP の
  ;; 全史を読み込んで履歴から再開する(新しい session・最初の本文に「これまでの会話」・本文は tui なので send)。
  (setv world (World "tmux"))
  (setv warm (run-first-turn world))
  (assert (= (get (. (.record world "j-1") spec) "sessionId") warm) "turn-record の spec が session を名乗る")
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "other" warm))
  (.tick world 1000)
  (assert (= world.sessions.cleanups [warm]))
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 2))
  (setv launch (get world.sessions.launches -1))
  (assert (= (get launch "binding") {"kind" "claude-code" "config_dir" "/homes/claude/other"}))
  (setv prompt (str-at launch "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話") prompt)
  (assert (in f"operator → {CONVERSATION}(note): 合言葉は ひまわり" prompt) prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (not-in "合言葉は何でしたか" prompt) "この手番の本文は tui では send で届く")
  (setv fresh (.sid world "j-2"))
  (assert (!= fresh warm))
  (assert (= (get world.sessions.sends -1) #(fresh "合言葉は何でしたか" True)))
  (assert (not-in #(warm "合言葉は何でしたか" True) world.sessions.sends) "別の家の session に送らない")
  (setv stamp (dict-at (dict-at launch "launch_attribution") "agentd"))
  (assert (= #((get stamp "arm") (get stamp "account") (get stamp "conversationId") (get stamp "agentJobId"))
             #("rehydrate" "other" CONVERSATION "j-2")))
  (assert (= world.acp.history-reads [CONVERSATION]))
  (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
  (assert (= (get metric "arm") "rehydrate")))


(deftest test-predecessor-unknown-to-this-node-rehydrates-from-acp-records
  ;; 別の機体で走った会話(predecessor がこの器に無い)は ACP の記録を最初の本文に畳んで新しい session を起こす。
  ;; headless は 1 手番 = 1 prompt なので「これまでの会話」の後に郵便の本文も畳み、send は撃たない。
  (setv world (World "headless"))
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row world.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "text" "覚えました"}] (- AT 8500)))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" "sid-on-another-node"))
  (.tick world 0)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (setv prompt (str-at (get world.sessions.launches 0) "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話") prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (.endswith prompt "\n\n合言葉は何でしたか") "郵便の本文は最後(headless の 1 手番目)")
  (assert (= world.sessions.sends []))
  (assert (any (gfor line world.local.logs (in f"rehydrates conversation {CONVERSATION}" line))) world.local.logs))


(deftest test-same-home-resumes-and-a-refused-resume-rehydrates-with-the-same-id
  ;; 同じ機体 ∧ 同じ家の片付いた session は --resume(cache を保つ・ACP の記録は読まない)。器が resume を
  ;; 断れば(transcript が無い等)同じ鋳造 id で履歴から再開する(fallback-arm-of)。
  (setv world (World "tmux"))
  (setv warm (run-first-turn world))
  (.tick world 601000)
  (assert (= world.sessions.cleanups [warm]))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "second" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" warm))
  (.tick world 1000)
  (assert (= (lfor resumed world.sessions.resumes (get resumed "session_id")) [warm]))
  (assert (= (len world.sessions.launches) 1))
  (assert (= world.acp.history-reads []) "resume は ACP の記録を読まない")
  (setv refused (World "tmux"))
  (setv gone (run-first-turn refused))
  (.tick refused 601000)
  (setv refused.sessions.refuse-resume (SessionRefused "transcript does not exist" "transcript_not_discoverable"))
  (.put-row refused.acp (message-row "m-2" CONVERSATION "operator" "second" (+ refused.local.now-ms 100)))
  (.put-row refused.acp (bound-row "j-2" ["m-2"] "acct" gone))
  (.tick refused 1000)
  (assert (= (len refused.sessions.resumes) 1))
  (assert (= (len refused.sessions.launches) 2) refused.local.logs)
  (setv relaunch (get refused.sessions.launches -1))
  (assert (= (get relaunch "session_id") (.sid refused "j-2")) "同じ鋳造 id で起こし直す")
  (assert (in "これまでの会話" (str-at relaunch "prompt")))
  (assert (= (str-at (dict-at (dict-at relaunch "launch_attribution") "agentd") "arm") "rehydrate"))
  (assert (= (get (.job-status refused "j-2") "phase") "Running"))
  (assert (any (gfor line refused.local.logs (in "refused (transcript_not_discoverable)" line))) refused.local.logs))


(deftest test-node-reports-account-for-live-sessions-and-transcripts-for-ended-ones
  ;; sessions に account、片付いた(終端)session は transcript の file が在る時だけ transcripts に移る。
  (setv world (World "tmux"))
  (setv warm (run-first-turn world))
  (.tick world 30000)
  (assert (= (get (.observations world) "sessions")
             [{"conversationId" CONVERSATION "sessionId" warm "state" "idle" "account" "acct"}]))
  (assert (= (get (.observations world) "transcripts") []))
  (.tick world 601000)
  (.tick world 30000)
  (assert (= (get (.observations world) "sessions") []))
  (assert (= (get (.observations world) "transcripts")
             [{"conversationId" CONVERSATION "sessionId" warm "account" "acct"}]))
  (setv (get world.local.transcripts f"/homes/claude/acct/projects/-work/{warm}.jsonl") "")
  (.tick world 30000)
  (assert (= (get (.observations world) "transcripts") []) "transcript の file が無い session は載せない"))
