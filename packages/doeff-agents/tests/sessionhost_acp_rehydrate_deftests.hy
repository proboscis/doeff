;;; 会話の引き継ぎ(profile / 機体を変えた手番)の焦点の検(段 8q・agora-redesign #51・ADR-DOE-AGENTS-012 R20)。
;;;
;;; 既知の形 = virtual actor の状態の移送(会話 = actor・手番 = activation): cache(温かい session / transcript)を
;;; 保つのは同じ機体 ∧ 同じ家の時だけで、それ以外は正本(郵便 = ACP・手番の本文 = 会話の記録の service〔段 9f lane 9f-4・
;;; 設計 §2.4〕)を読み込む履歴からの再開(operator 決定 #54 逐語 "i want cache kept when both machine and a profile is not
;;; changed. in other cases, i think i need to accept the fact that cache gets invalidated")。ここで撃つのは
;;;   * 「これまでの会話」の畳み(時刻順・kind ごと・この手番の inputs を除く・frame は畳まない・上限で古い手番から
;;;     要約せず落とし、落とした数と全文の在処を名乗る)— 材料は型で 2 つ(RecordedTurns = service の本文 /
;;;     HeadlineTurns = ACP の見出しだけの薄い再開・名乗る)
;;;   * node の観測(sessions の account・transcripts = 終端で transcript が残る会話の最新)
;;;   * fake で agentd を一周: 家の違う温かい session は片付けて履歴からの再開(本文は service の before=latest から)/ 器に無い
;;;     predecessor は履歴からの再開 / 同じ家の片付いた session は --resume / resume の断りは同じ id で履歴からの再開 /
;;;     turn-record の spec に sessionId / service が届かない・配線されていない → 薄い再開を名乗る / 上限まで頁を後向きに読む
;;;   * 家の鍵の model(段 9o lane 9o-3・agora-redesign #75): 同じ機体・profile の家・model なら温かい session へ send /
;;;     model だけが違えば送らず(片付いた session も --resume せず)charter.model の新しい session を履歴から再開(本文は service から)
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
  HeadlineTurns
  HistoryFold
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  RECORD-PAGE-MAX-LIMIT
  RecordEvent
  RecordedTurns
  SessionRefused
  SessionView
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  record-history-satisfied
  record-page-advances
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


(defn #^ RecordEvent event-of [#^ int record-seq #^ str stream #^ int seq #^ int at #^ str kind #^ dict fields]
  "service の出来事 1 つ(契約 storedEvent — bytes / sha256 は検では形だけ)。"
  (RecordEvent :record-seq record-seq :stream-id stream :stream-kind "turn" :producer-seq seq :at at :kind kind
               :bytes (.get fields "bytes" 1) :sha256 "0"
               :text (.get fields "text") :summary (.get fields "summary") :input (.get fields "input")
               :output (.get fields "output") :tool-name (.get fields "toolName") :tool-use-id (.get fields "toolUseId")
               :model (.get fields "model") :is-error (= (.get fields "isError") True)
               :truncated (= (.get fields "truncated") True)))


(defn #^ AcpRow record-row [#^ str job-id #^ str conversation #^ list entries #^ int at]
  "契約 turn-record の 1 行(ended・entries = 手番の出来事の見出しの列)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}"
          :kind TURN-RECORD-KIND :resource-id job-id :version "v1" :generation 2 :created-at-ms at
          :labels {} :payload {}
          :spec {"conversationId" conversation "agentJobId" job-id "node" "elsewhere" "profile" "p" "model" "m"}
          :status {"state" "ended" "entries" entries}))


(defn #^ AcpRow bound-row [#^ str job-id #^ list inputs #^ str account #^ (| str None) predecessor
                           #^ str [model "claude-opus-5"]]
  "自分に結ばれた Bound の agent-job(binding.account = 借りる家・affinity.predecessor = 会話の前の session・
   model = charter.model — 配達の係が会話の宣言を重ねた値)。"
  (setv spec {"subject" CONVERSATION
              "inputs" inputs
              "charter" {"agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" model}})
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
  "fake の 4 handler(+ record = True なら会話の記録の service の fake)+ 値の宣言 + Node の行(agentd を一周させる最小の世界)。"

  (defn #^ None __init__ [self #^ str backend #^ bool [record False]]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes" :backend-kind backend
                                        :record-enabled record))
    (setv self.record-service (FakeRecord))
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
    (setv base [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch])
    (setv self.state
          (run-tick self.settings self.state
                    (if self.settings.record-enabled (+ [self.record-service.dispatch] base) base)))
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
  ;; 郵便(ACP)と service の出来事を時刻順(同じ時刻は郵便が先)に並べ、会話へ届いた郵便ごとに手番に割る。この手番の
  ;; inputs・別の会話・tui の画面の断面(frame)・郵便の写し(message — 郵便は ACP の行から)は畳まない。
  (setv messages #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
                   (message-row "m-x" OTHER "operator" "別の会話の本文" AT)
                   (message-row "m-r" "operator" CONVERSATION "報告の本文" (+ AT 3000))
                   (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ AT 9000))))
  (setv events #((event-of 1 "j-1#a1" 0 (+ AT 1000) "text" {"text" "覚えました" "model" "claude-opus-5"})
                 (event-of 2 "j-1#a1" 1 (+ AT 1100) "tool_use" {"toolName" "Read" "toolUseId" "t1" "input" {"file_path" "a.txt"}})
                 (event-of 3 "j-1#a1" 2 (+ AT 1200) "tool_result" {"toolUseId" "t1" "output" "中身" "isError" True})
                 (event-of 4 "j-1#a1" 3 (+ AT 1300) "system" {"text" "hook failed"})
                 (event-of 5 "j-1#a1" 4 (+ AT 1400) "error" {"text" "API error"})
                 (event-of 6 "j-1#a1" 5 (+ AT 1500) "frame" {"text" "❯ pane"})
                 (event-of 7 "m-1#mail" 0 (+ AT 1600) "message" {"text" "郵便の写し"})
                 (event-of 8 "old#a1" 0 (+ AT 1700) "user" {"text" "旧の手番の送信" "truncated" True})))
  (setv fold (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete True) #("m-2") 65536)))
  (assert (isinstance fold HistoryFold))
  (assert (not fold.thin))
  (assert (= #(fold.kept-turns fold.dropped-turns fold.dropped-items) #(1 0 0)) fold)
  (assert (.startswith fold.text f"これまでの会話(会話の記録の service と ACP の郵便から組んだ写し・会話 {CONVERSATION}・古い順):") fold.text)
  (setv lines (lfor line (cut (.splitlines fold.text) 1 None) :if line line))
  (assert (= (lfor line lines (get (.split line "] " 1) 1))
             [f"operator → {CONVERSATION}(note): 合言葉は ひまわり"
              "agent: 覚えました"
              "agent の道具 Read: {\"file_path\": \"a.txt\"}"
              "道具の結果(誤り): 中身"
              "system: hook failed"
              "誤り: API error"
              "user: 旧の手番の送信(切り詰め)"
              f"{CONVERSATION} → operator(note): 報告の本文"])
          fold.text)
  (assert (.startswith (get lines 0) "[2026-09-") (get lines 0))
  (assert (= fold.size-bytes (len (.encode fold.text "utf-8"))))
  ;; 会話の最初まで読めていない時はそれを名乗る。
  (setv partial (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete False) #("m-2") 65536)))
  (assert (in "会話の最初までは読んでいない" partial.text))
  ;; 記録の無い会話は空(最初の本文を変えない)。
  (assert (= (. (run (rehydrate-history-of THIRD messages (RecordedTurns :events #() :complete True) #() 65536)) text) "")))


(deftest test-history-fold-from-headlines-is-thin-and-says-so
  ;; service に届かない時の材料 = ACP の見出し(本文なし): 手番ごとに出来事の数と道具の名だけを 1 項にし、薄い再開と理由を
  ;; 名乗る。本文の無い行を本文として畳まない(見出しの JSON に text が無いので中身は名乗れない)。
  (setv messages #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
                   (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ AT 9000))))
  (setv records #((record-row "j-1" CONVERSATION
                              [{"seq" 0 "at" (+ AT 1000) "kind" "text" "bytes" 20 "sha256" "0"}
                               {"seq" 1 "at" (+ AT 1100) "kind" "tool_use" "toolName" "Read" "toolUseId" "t1" "bytes" 30 "sha256" "0"}
                               {"seq" 2 "at" (+ AT 1200) "kind" "tool_result" "toolUseId" "t1" "bytes" 9 "sha256" "0" "isError" True}
                               {"seq" 3 "at" (+ AT 1300) "kind" "text" "bytes" 8 "sha256" "0"}]
                              AT)
                  (record-row "j-x" OTHER [{"seq" 0 "at" (+ AT 1000) "kind" "text" "bytes" 5 "sha256" "0"}] AT)
                  (record-row "j-empty" CONVERSATION [] (+ AT 2000))))
  (setv source (HeadlineTurns :records records :reason "record service read failed (0: unreachable)"))
  (setv fold (run (rehydrate-history-of CONVERSATION messages source #("m-2") 65536)))
  (assert fold.thin)
  (assert (= #(fold.kept-turns fold.dropped-turns) #(1 0)) fold)
  (assert (.startswith fold.text f"これまでの会話(薄い再開・会話 {CONVERSATION}・古い順): 会話の記録の service に届かなかった(record service read failed (0: unreachable))ため") fold.text)
  (setv lines (lfor line (cut (.splitlines fold.text) 1 None) :if line line))
  (assert (= (lfor line lines (get (.split line "] " 1) 1))
             [f"operator → {CONVERSATION}(note): 合言葉は ひまわり"
              "手番 j-1(見出しだけ・本文は記録の service): text 2・tool_use 1・tool_result 1(道具: Read)"])
          fold.text)
  (assert (not-in "覚えました" fold.text))
  (assert (not-in "j-x" fold.text) "別の会話の手番を畳んだ")
  (assert (not-in "j-empty" fold.text) "見出しの無い行を畳んだ")
  ;; 見出しが無い会話は空。
  (assert (= (. (run (rehydrate-history-of THIRD messages source #() 65536)) text) "")))


(deftest test-history-fold-drops-the-oldest-turns-and-names-where-the-rest-is
  ;; 上限を超えたら古い手番から要約せずに落とし、落とした手番と項の数と全文の在処を名乗る。
  (setv messages (tuple (lfor n (range 6)
                              (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "あ" 200))
                                           (+ AT (* n 1000))))))
  (setv records (RecordedTurns :events (tuple (lfor n (range 6) (event-of (+ n 1) "j-a#a1" n (+ AT (* n 1000) 10) "text" {"text" f"答え {n}"})))
                               :complete True))
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
  (assert (in f"GET /v1/conversations/{CONVERSATION}/events" small.text) small.text)
  ;; 最新の手番 1 つだけで超える: その手番の先頭を落として末尾を残し、切ったことを名乗る。
  (setv huge #((message-row "m-h" CONVERSATION "operator" (+ "はじまり" (* "い" 3000) "しっぽ") AT)))
  (setv cut-fold (run (rehydrate-history-of CONVERSATION huge (RecordedTurns :events #() :complete True) #() 1500)))
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
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (assert (= (get (. (.record world "j-1") spec) "sessionId") warm) "turn-record の spec が session を名乗る")
  (assert (= (len (.events-of world.record-service CONVERSATION "j-1#a1")) 1) "1 手番目の本文が service に無い")
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "other" warm))
  (.tick world 1000)
  (assert (= world.sessions.cleanups [warm]))
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 2))
  (setv launch (get world.sessions.launches -1))
  (assert (= (get launch "binding") {"kind" "claude-code" "config_dir" "/homes/claude/other"}))
  (setv prompt (str-at launch "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話(会話の記録の service と ACP の郵便から") prompt)
  (assert (in f"operator → {CONVERSATION}(note): 合言葉は ひまわり" prompt) prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) "本文は service の before=latest から 1 頁")
  (assert (any (gfor line world.local.logs (in "from the record service" line))) world.local.logs)
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


(deftest test-same-node-profile-and-model-sends-to-the-warm-session
  ;; 段 9o lane 9o-3(#75)の検 (a): 同じ機体・同じ profile の家・同じ model の手番は温かい session へ send する(起こさない・
  ;; ACP の記録も記録の service も読まない)。
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" warm "claude-opus-5"))
  (.tick world 1000)
  (assert (= world.sessions.cleanups []) world.local.logs)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 1) "同じ家の手番で session を起こした")
  (assert (= (.sid world "j-2") warm))
  (assert (= (get world.sessions.sends -1) #(warm "合言葉は何でしたか" True)))
  (assert (= world.acp.history-reads []) "温かい send は ACP の記録を読まない")
  (assert (= world.record-service.reads []) "温かい send は記録の service を読まない")
  (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
  (assert (= (get metric "arm") "send")))


(deftest test-model-change-alone-rehydrates-a-new-session-on-the-charter-model
  ;; 段 9o lane 9o-3(#75)の検 (b)(c): 機体も profile の家も同じで、会話の宣言の model だけが変わった手番(charter.model が
  ;; 違う)は温かい session へ send しない — 片付けて、新しい session を charter.model で起こし、最初の本文の「これまでの会話」は
  ;; 会話の記録の service の before=latest から読む(9f-4 の形)。実射 2026-09-14: charter は claude-opus-5 なのに温かい session へ
  ;; 送られ「session started: model claude-sonnet-5」。
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" warm "claude-sonnet-5"))
  (.tick world 1000)
  ;; (b) 送らない・片付ける・charter.model の新しい session
  (assert (= world.sessions.cleanups [warm]) world.local.logs)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 2) world.local.logs)
  (setv launch (get world.sessions.launches -1))
  (assert (= (get launch "model") "claude-sonnet-5") launch)
  (setv fresh (.sid world "j-2"))
  (assert (!= fresh warm))
  (assert (= (get launch "session_id") fresh))
  (assert (= (get world.sessions.sends -1) #(fresh "合言葉は何でしたか" True)))
  (assert (not-in #(warm "合言葉は何でしたか" True) world.sessions.sends) "model の違う温かい session に送った")
  (setv stamp (dict-at (dict-at launch "launch_attribution") "agentd"))
  (assert (= (get stamp "arm") "rehydrate"))
  (assert (= (dict-at stamp "home") {"account" "acct" "binding" None "model" "claude-sonnet-5"}) stamp)
  (assert (= (get (. (.record world "j-2") spec) "model") "claude-sonnet-5"))
  (assert (any (gfor line world.local.logs (in "runs in another home (account, binding or model)" line))) world.local.logs)
  (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
  (assert (= (get metric "arm") "rehydrate"))
  ;; (c) 最初の本文の「これまでの会話」は記録の service から(1 手番目の本文は ACP の見出しに無く service にだけ在る)
  (setv prompt (str-at launch "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話(会話の記録の service と ACP の郵便から") prompt)
  (assert (in f"operator → {CONVERSATION}(note): 合言葉は ひまわり" prompt) prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) world.record-service.reads)
  (assert (any (gfor line world.local.logs (in "from the record service" line))) world.local.logs)
  (assert (= world.acp.history-reads [CONVERSATION]))
  ;; 片付いた(idle の寿命を超えた)session でも model が違えば --resume しない(起こした時の model の transcript の続きに
  ;; なる)— 同じく charter.model の新しい session を履歴から再開する。
  (setv cold (World "tmux" True))
  (setv gone (run-first-turn cold))
  (.tick cold 601000)
  (assert (= cold.sessions.cleanups [gone]))
  (.put-row cold.acp (message-row "m-2" CONVERSATION "operator" "second" (+ cold.local.now-ms 100)))
  (.put-row cold.acp (bound-row "j-2" ["m-2"] "acct" gone "claude-sonnet-5"))
  (.tick cold 1000)
  (assert (= cold.sessions.resumes []) cold.local.logs)
  (assert (= (len cold.sessions.launches) 2) cold.local.logs)
  (assert (= (get (get cold.sessions.launches -1) "model") "claude-sonnet-5"))
  (assert (= cold.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) cold.record-service.reads))


(deftest test-predecessor-unknown-to-this-node-rehydrates-from-the-record-service
  ;; 別の機体で走った会話(predecessor がこの器に無い)は郵便(ACP)と手番の本文(service)を最初の本文に畳んで新しい
  ;; session を起こす。headless は 1 手番 = 1 prompt なので「これまでの会話」の後に郵便の本文も畳み、send は撃たない。
  (setv world (World "headless" True))
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row world.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (setv (get world.record-service.stored #(CONVERSATION "j-0#a1" 0)) {"producerSeq" 0 "at" (- AT 8000) "kind" "text" "text" "覚えました"})
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
  (assert (any (gfor line world.local.logs (in f"rehydrates conversation {CONVERSATION} from the record service" line))) world.local.logs))


(deftest test-unreachable-record-service-rehydrates-thinly-from-acp-headlines
  ;; service が届かない(か配線されていない)時は ACP の見出しで薄く再開し、prompt と log がそれを名乗る。本文は無い。
  (setv world (World "headless" True))
  (setv world.record-service.unreachable True)
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row world.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" "sid-on-another-node"))
  (.tick world 0)
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (setv prompt (str-at (get world.sessions.launches 0) "prompt"))
  (assert (in "これまでの会話(薄い再開・" prompt) prompt)
  (assert (in "unreachable" prompt) prompt)
  (assert (in "合言葉は ひまわり" prompt))
  (assert (in "手番 j-0(見出しだけ・本文は記録の service): text 1" prompt) prompt)
  (assert (not-in "覚えました" prompt) "見出しに無い本文が prompt に在る")
  (assert (any (gfor line world.local.logs (in "rehydrates thinly from ACP headlines" line))) world.local.logs)
  (assert (= (len world.record-service.reads) 1))
  ;; 配線されていない(弁 off)世界も薄い再開で、Record* は 1 つも撃たない。
  (setv bare (World "headless"))
  (.put-row bare.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row bare.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (.put-row bare.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row bare.acp (bound-row "j-2" ["m-2"] "acct" "sid-on-another-node"))
  (.tick bare 0)
  (setv bare-prompt (str-at (get bare.sessions.launches 0) "prompt"))
  (assert (in "これまでの会話(薄い再開・" bare-prompt) bare-prompt)
  (assert (in "RECORD_SERVICE_URL is unset" bare-prompt) bare-prompt)
  (assert (= bare.record-service.reads [])))


(deftest test-rehydrate-pages-backwards-until-the-budget-is-covered
  ;; before=latest の頁が上限に届かなければ cursor.next を before に次の頁(古い側)を読む。届いたら止める(会話の最初までは
  ;; 読まない — その分は畳みが落とす)。判断は record-history-satisfied の 1 点。
  (setv small-events (tuple (lfor n (range 3) (event-of (+ n 1) "s#a1" n (+ AT n) "text" {"text" "x" "bytes" 10}))))
  (assert (not (run (record-history-satisfied small-events 100))))
  (assert (run (record-history-satisfied small-events 30)))
  (assert (= (lfor pair [#(None 10) #(10 9) #(10 10) #(10 11) #(None None) #(5 None)] (run (record-page-advances #* pair)))
             [True True False False False False]))
  (setv world (World "headless" True))
  (setv world.settings (AgentdSettings :node-name NODE :homes-root "/homes" :backend-kind "headless"
                                       :record-enabled True :rehydrate-history-byte-budget 4000))
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "はじめの郵便" (- AT 90000)))
  ;; 会話に 2500 件の本文(1 件 ≈ 30 byte)— 上限 4000 byte なら末尾の 1 頁(1000 件)で足りる。
  (for [n (range 2500)]
    (setv (get world.record-service.stored #(CONVERSATION f"j-{(// n 500)}#a1" (% n 500)))
          {"producerSeq" (% n 500) "at" (+ (- AT 80000) n) "kind" "text" "text" f"答え {n}"}))
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "つづき" (- AT 100)))
  (.put-row world.acp (bound-row "j-9" ["m-2"] "acct" "sid-on-another-node"))
  (.tick world 0)
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) world.record-service.reads)
  (setv prompt (str-at (get world.sessions.launches 0) "prompt"))
  (assert (in "答え 2499" prompt))
  (assert (in "会話の最初までは読んでいない" prompt) prompt)
  ;; 上限が大きければ次の頁を before = 頁の最初の recordSeq で読み、最初まで読めたら止める。
  (setv wide (World "headless" True))
  (setv wide.settings (AgentdSettings :node-name NODE :homes-root "/homes" :backend-kind "headless"
                                      :record-enabled True :rehydrate-history-byte-budget 200000))
  (setv wide.record-service.stored (dict world.record-service.stored))
  (.put-row wide.acp (message-row "m-2" CONVERSATION "operator" "つづき" (- AT 100)))
  (.put-row wide.acp (bound-row "j-9" ["m-2"] "acct" "sid-on-another-node"))
  (.tick wide 0)
  (assert (= (len wide.sessions.launches) 1) wide.local.logs)
  (assert (= (lfor read wide.record-service.reads (get read 1)) [None 1501 501]) wide.record-service.reads)
  (assert (not-in "会話の最初までは読んでいない" (str-at (get wide.sessions.launches 0) "prompt"))))


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
  (assert (in "これまでの会話(薄い再開・" (str-at relaunch "prompt")) "弁 off の世界は薄い再開を名乗る")
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
