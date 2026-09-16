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
;;;   * 再開の材料は名指しの順(段 9q・agora-redesign #77): service が答えた拍は ACP の turn-record の全量(見出し)を読まない・
;;;     家が変わる手番の claim は温かい手番と同じ拍に着地する(器の準備は claim の後)・見出しを読むのは薄い再開の拍だけ
;;;   * 上限で落とした古い手番は黙って捨てない(段 11 lane 11v・agora-redesign #55 便 1・ADR-012 R34): 落とした区間を見出し 1 行
;;;     (期間・kind ごとの件数・道具の名・全文の在処 — 綴りは薄い再開の turn-record の見出しと同じ)に畳んで残した手番の前に置く・
;;;     落とさなければ見出しは無い・見出しも上限の中に数え最新の手番だけでも超えれば先頭を切って切った byte を名乗る・薄い再開でも同じ
;;;   * 落とす前に薄くする(段 11 lane 11v 便 3・agora-redesign #225・ADR-012 R35): 上限を超えたらまず古い手番から道具の項(tool_use の
;;;     入力・tool_result の本文)だけを先頭 budget / HISTORY_THIN_DIVISOR byte に薄くして元の byte を名乗る・郵便と agent の text・
;;;     user / system / error は 1 byte も変えない・全部を薄くしても超える時だけ落とす(見出し)・薄い再開は薄くする本文が無い・決定的
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import base64)
(import hashlib)
(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  HISTORY-MAIL-KIND
  HISTORY-SUMMARY-KIND
  HISTORY-THIN-DIVISOR
  HeadlineTurns
  HistoryFold
  HistorySummary
  SUMMARY-KIND
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  RECORD-PAGE-MAX-LIMIT
  RECORD-RAW-EVENT-KINDS
  RecordEvent
  RecordPage
  RecordUnread
  RecordedTurns
  SessionRefused
  SessionView
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.attachment [TurnAttachment attachment-of-wire])
;; 段 10 lane 10o: 腕の語と、器の種類と、添付の欄の綴り(effects の 1 点)。
(import doeff_agents.sessionhost.acp.effects [
  BACKEND-HEADLESS
  MESSAGE-ATTACHMENTS-KEY
  NEXT-ARM-LAUNCH
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions record-body-bytes record-body-sha256])
(import doeff_agents.sessionhost.acp.judgment [
  HISTORY-ERASED-MARK
  history-event-line
  record-history-satisfied
  record-page-advances
  attachment-of
  first-turn-attachments-of
  launch-charter-with-attachments
  mail-text-of
  mail-turn-text-of
  first-turn-carries-inputs
  history-message-line
  history-time-of
  message-attachments-of
  message-bodies-of
  message-body-ref-of
  resume-params-of
  rehydrate-history-of
  summary-floor-at-of
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


(defn #^ str mailed [#^ AcpRow row]
  "段 10 lane 10r 追補: 手番へ渡る郵便の文(見出し 1 行 + 本文 — judgment.mail-turn-text-of の 1 点)。"
  (run (mail-turn-text-of (str (get row.spec "id")) row.spec (str (get row.spec "body")))))


(defn #^ RecordEvent event-of [#^ int record-seq #^ str stream #^ int seq #^ int at #^ str kind #^ dict fields]
  "service の出来事 1 つ(契約 storedEvent — bytes / sha256 は検では形だけ)。"
  (RecordEvent :record-seq record-seq :stream-id stream :stream-kind "turn" :producer-seq seq :at at :kind kind
               :bytes (.get fields "bytes" 1) :sha256 "0"
               :text (.get fields "text") :summary (.get fields "summary") :input (.get fields "input")
               :output (.get fields "output") :tool-name (.get fields "toolName") :tool-use-id (.get fields "toolUseId")
               :model (.get fields "model") :is-error (= (.get fields "isError") True)
               :truncated (= (.get fields "truncated") True)
               ;; 段 12 lane 12l(agora-redesign #383 粒 2): 本文が消された刻。
               :tombstoned-at (.get fields "tombstonedAt")
               ;; 段 10 lane 10o(agora-redesign #96): 添付の出来事(kind attachment)の 3 欄。
               :mime (.get fields "mime") :name (.get fields "name") :data (.get fields "data")))


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


(defn #^ AcpRow summary-row [#^ int from-seq #^ int to-seq #^ int at]
  "契約 kind summary の 1 行(段 12 lane 12j 便 3 — 会話の履歴の段階つき要約 1 区間・本文は記録の service の claim check)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{SUMMARY-KIND}:sum-{CONVERSATION}-{to-seq}"
          :kind SUMMARY-KIND :resource-id f"sum-{CONVERSATION}-{to-seq}" :version "v1" :generation 1 :created-at-ms at
          :labels {} :payload {}
          :spec {"conversationId" CONVERSATION "from" from-seq "to" to-seq
                 "recordRef" f"record:{CONVERSATION}/summary#{from-seq}-{to-seq}" "bytes" 10 "sha256" (* "0" 64)}
          :status {"state" "current" "model" "claude-opus-5" "at" at}))


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
  (setv fold (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete True) #("m-2") 65536 {} #() None)))
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
  (setv partial (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete False) #("m-2") 65536 {} #() None)))
  (assert (in "会話の最初までは読んでいない" partial.text))
  ;; 記録の無い会話は空(最初の本文を変えない)。
  (assert (= (. (run (rehydrate-history-of THIRD messages (RecordedTurns :events #() :complete True) #() 65536 {} #() None)) text) "")))


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
  (setv fold (run (rehydrate-history-of CONVERSATION messages source #("m-2") 65536 {} #() None)))
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
  (assert (= (. (run (rehydrate-history-of THIRD messages source #() 65536 {} #() None)) text) "")))


(deftest test-history-fold-drops-the-oldest-turns-and-names-where-the-rest-is
  ;; 上限を超えたら古い手番から要約せずに落とし、落とした手番と項の数と全文の在処を名乗る。
  (setv messages (tuple (lfor n (range 6)
                              (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "あ" 200))
                                           (+ AT (* n 1000))))))
  (setv records (RecordedTurns :events (tuple (lfor n (range 6) (event-of (+ n 1) "j-a#a1" n (+ AT (* n 1000) 10) "text" {"text" f"答え {n}"})))
                               :complete True))
  (setv whole (run (rehydrate-history-of CONVERSATION messages records #("m-5") 65536 {} #() None)))
  (assert (= #(whole.kept-turns whole.dropped-turns) #(5 0)) whole)
  (assert (not-in "問い 5" whole.text) "この手番の inputs は畳まない")
  (assert (in "答え 5" whole.text))
  (setv small (run (rehydrate-history-of CONVERSATION messages records #("m-5") 2000 {} #() None)))
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
  (setv cut-fold (run (rehydrate-history-of CONVERSATION huge (RecordedTurns :events #() :complete True) #() 1500 {} #() None)))
  (assert (<= cut-fold.size-bytes 1500) cut-fold.size-bytes)
  (assert (in "しっぽ" cut-fold.text))
  (assert (not-in "はじまり" cut-fold.text))
  ;; R34(段 11 lane 11v): 切った byte を名乗る(欄 cut_bytes と断りの文が同じ数)。
  (assert (> cut-fold.cut-bytes 0) cut-fold)
  (assert (in f"最新の手番の先頭 {cut-fold.cut-bytes} byte を落としました" cut-fold.text) cut-fold.text))


(deftest test-dropped-turns-fold-into-one-headline-with-period-counts-and-tools
  ;; R34(段 11 lane 11v・agora-redesign #55 便 1): 上限で落とした古い手番は黙って捨てず、落とした区間を見出し 1 行(期間・
  ;; 件数・道具の名・在処)に畳んで残した手番の前に置く。見出しは区間に 1 行(手番ごとではない)。model は呼ばない(決定的)。
  ;; 反例 = 見出しの無い落とし(旧の footer だけ)はここで赤。
  (setv messages (tuple (lfor n (range 8)
                              (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "あ" 300)) (+ AT (* n 10000))))))
  (setv events (tuple (+ (lfor n (range 8) (event-of (+ 1 (* n 3)) f"j-{n}#a1" 0 (+ AT (* n 10000) 100) "text" {"text" f"答え {n}"}))
                         (lfor n (range 8) (event-of (+ 2 (* n 3)) f"j-{n}#a1" 1 (+ AT (* n 10000) 200) "tool_use"
                                                     {"toolName" (if (< n 4) "Bash" "Read") "toolUseId" f"t{n}" "input" {"cmd" "ls"}}))
                         (lfor n (range 8) (event-of (+ 3 (* n 3)) f"j-{n}#a1" 2 (+ AT (* n 10000) 300) "tool_result"
                                                     {"toolUseId" f"t{n}" "output" "ok"})))))
  (setv records (RecordedTurns :events events :complete True))
  (setv fold (run (rehydrate-history-of CONVERSATION messages records #("m-7") 4000 {} #() None)))
  (assert (<= fold.size-bytes 4000) fold.size-bytes)
  (assert (>= fold.dropped-turns 1) fold)
  (assert (= (+ fold.kept-turns fold.dropped-turns) 7) fold)
  (assert (= fold.cut-bytes 0) fold)
  (setv k fold.dropped-turns)
  (assert (= fold.dropped-items (* 4 k)) fold)
  (setv headline fold.dropped-headline)
  (assert (isinstance headline str) fold)
  (assert (= (.count fold.text headline) 1) "見出しは text にちょうど 1 度")
  ;; 位置: 頭の直後・残した手番の前。
  (assert (= (get (.split fold.text "\n\n") 1) headline) fold.text)
  (assert (< (.index fold.text headline) (.index fold.text f"答え {k}")) "見出しは残した手番の前")
  ;; 期間 = 落とした区間の最初の郵便 〜 最後の出来事。件数は kind ごと(初出の順・郵便も数える)。道具の名は初出の順。
  (setv first-stamp (run (history-time-of AT)))
  (setv last-stamp (run (history-time-of (+ AT (* (- k 1) 10000) 300))))
  (assert (.startswith headline f"[{first-stamp}〜{last-stamp}] 古い手番 {k} 件(出来事と郵便 {(* 4 k)} 件)は上限 4000 byte を超えるため") headline)
  (setv tools (if (<= k 4) "Bash" "Bash, Read"))
  (assert (in f": {HISTORY-MAIL-KIND} {k}・text {k}・tool_use {k}・tool_result {k}(道具: {tools})。" headline) headline)
  (assert (in f"GET /v1/conversations/{CONVERSATION}/events" headline) "全文の在処は見出しが名乗る")
  ;; 落とした本文は無く、残した手番の本文は在る。旧の黙った断りは無い。手番ごとの見出しも無い(区間に 1 行)。
  (assert (not-in "問い 0" fold.text))
  (assert (not-in "答え 0" fold.text))
  (assert (in "答え 6" fold.text))
  (assert (in "答え 7" fold.text))
  (assert (not-in "要約せずに落としました" fold.text))
  (assert (= (.count fold.text "古い手番") 1) fold.text)
  ;; 決定的: 同じ材料からは同じ見出し。
  (assert (= (. (run (rehydrate-history-of CONVERSATION messages records #("m-7") 4000 {} #() None)) dropped-headline) headline)))


(deftest test-a-fold-within-the-budget-carries-no-headline
  ;; R34: 落とさなければ見出しは無い(欄は None・text に「古い手番」の行が無い)— 見出しは落とした区間の印であって常設の飾りではない。
  (setv messages #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
                   (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ AT 9000))))
  (setv events #((event-of 1 "j-1#a1" 0 (+ AT 1000) "text" {"text" "覚えました"})))
  (setv fold (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete True) #("m-2") 65536 {} #() None)))
  (assert (= #(fold.kept-turns fold.dropped-turns fold.dropped-items fold.cut-bytes) #(1 0 0 0)) fold)
  (assert (is fold.dropped-headline None) fold)
  (assert (not-in "古い手番" fold.text))
  (assert (not-in "上限" fold.text))
  ;; 記録の無い会話も同じ(空の答え)。
  (setv empty (run (rehydrate-history-of THIRD messages (RecordedTurns :events #() :complete True) #() 65536 {} #() None)))
  (assert (= #(empty.text empty.dropped-headline empty.cut-bytes) #("" None 0)) empty))


(deftest test-the-newest-turn-is-cut-after-the-headline-and-the-cut-bytes-are-named
  ;; R34: 見出しも上限の中に数える。古い手番を全部落としても最新の手番 1 つ(と頭・見出し)が超えるなら、その先頭を切って末尾を
  ;; 残し、切った byte を名乗る。見出しは残る(切っても落とした区間の印は消えない)。
  (setv messages #((message-row "m-0" CONVERSATION "operator" "最初の問い" AT)
                   (message-row "m-1" CONVERSATION "operator" "二つ目の問い" (+ AT 1000))
                   (message-row "m-2" CONVERSATION "operator" (+ "はじまり" (* "い" 3000) "しっぽ") (+ AT 2000))))
  (setv events #((event-of 1 "j-0#a1" 0 (+ AT 100) "tool_use" {"toolName" "Edit" "toolUseId" "t0" "input" {"a" 1}})
                 (event-of 2 "j-1#a1" 0 (+ AT 1100) "text" {"text" "二つ目の答え"})))
  (setv fold (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete True) #() 1800 {} #() None)))
  (assert (<= fold.size-bytes 1800) fold.size-bytes)
  (assert (= #(fold.kept-turns fold.dropped-turns fold.dropped-items) #(1 2 4)) fold)
  (assert (> fold.cut-bytes 0) fold)
  (assert (isinstance fold.dropped-headline str) fold)
  (assert (= (.count fold.text fold.dropped-headline) 1))
  (assert (in f"{HISTORY-MAIL-KIND} 2・tool_use 1・text 1(道具: Edit)" fold.dropped-headline) fold.dropped-headline)
  (assert (in "しっぽ" fold.text))
  (assert (not-in "はじまり" fold.text))
  (assert (in f"最新の手番の先頭 {fold.cut-bytes} byte を落としました" fold.text) fold.text)
  ;; 切った byte の勘定 = 最新の手番の本文の大きさ − 残った末尾の大きさ。
  (setv newest-line (run (history-message-line (get messages 2) (+ AT 2000) {})))
  (setv tail (get (.split fold.text "\n\n") 2))
  (assert (= fold.cut-bytes (- (len (.encode newest-line "utf-8")) (len (.encode tail "utf-8")))) fold.cut-bytes)
  ;; 落とした区間が無い時の切りは、在処を断りの中で名乗る(見出しが無いので)。
  (setv alone (run (rehydrate-history-of CONVERSATION #((get messages 2)) (RecordedTurns :events #() :complete True) #() 1500 {} #() None)))
  (assert (<= alone.size-bytes 1500) alone.size-bytes)
  (assert (is alone.dropped-headline None) alone)
  (assert (> alone.cut-bytes 0) alone)
  (assert (in f"GET /v1/conversations/{CONVERSATION}/events" alone.text) alone.text))


(deftest test-a-thin-rehydrate-folds-dropped-record-headlines-into-the-range-headline
  ;; R34 × 薄い再開: 材料が ACP の見出し(本文なし)でも、上限で落とした区間は同じ形の見出し 1 行に畳む — 手番ごとの見出しの
  ;; 件数を足し合わせ、道具の名は初出の順、期間は区間の最初と最後。綴りは turn-record の見出し(history-counts-note)と同じ。
  (setv messages (tuple (lfor n (range 6) (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "う" 120)) (+ AT (* n 10000))))))
  (setv records (tuple (lfor n (range 6)
                             (record-row f"j-{n}" CONVERSATION
                                         [{"seq" 0 "at" (+ AT (* n 10000) 100) "kind" "text" "bytes" 20 "sha256" "0"}
                                          {"seq" 1 "at" (+ AT (* n 10000) 200) "kind" "tool_use" "toolName" (if (= (% n 2) 0) "Read" "Grep")
                                           "toolUseId" f"t{n}" "bytes" 30 "sha256" "0"}
                                          {"seq" 2 "at" (+ AT (* n 10000) 300) "kind" "tool_result" "toolUseId" f"t{n}" "bytes" 9 "sha256" "0"}]
                                         (+ AT (* n 10000))))))
  (setv source (HeadlineTurns :records records :reason "record service read failed (0: unreachable)"))
  (setv whole (run (rehydrate-history-of CONVERSATION messages source #("m-5") 65536 {} #() None)))
  (assert (and whole.thin (= whole.dropped-turns 0) (is whole.dropped-headline None)) whole)
  (setv fold (run (rehydrate-history-of CONVERSATION messages source #("m-5") 1900 {} #() None)))
  (assert fold.thin)
  (assert (<= fold.size-bytes 1900) fold.size-bytes)
  (assert (>= fold.dropped-turns 1) fold)
  (assert (= fold.cut-bytes 0) fold)
  (setv k fold.dropped-turns)
  (assert (= fold.dropped-items (* 2 k)) fold)
  (setv headline fold.dropped-headline)
  (assert (isinstance headline str) fold)
  (assert (= (.count fold.text headline) 1))
  (setv first-stamp (run (history-time-of AT)))
  (setv last-stamp (run (history-time-of (+ AT (* (- k 1) 10000) 300))))
  (assert (.startswith headline f"[{first-stamp}〜{last-stamp}] 古い手番 {k} 件(出来事と郵便 {(* 2 k)} 件)") headline)
  (setv tools (if (= k 1) "Read" "Read, Grep"))
  (assert (in f": {HISTORY-MAIL-KIND} {k}・text {k}・tool_use {k}・tool_result {k}(道具: {tools})。" headline) headline)
  ;; 残した手番の見出し(手番ごと)は同じ綴りで在り、落とした手番の見出しは区間に畳まれて個別には無い。
  (assert (in "(見出しだけ・本文は記録の service): text 1・tool_use 1・tool_result 1(道具: " fold.text) fold.text)
  (assert (not-in "手番 j-0(" fold.text) "落とした手番の見出しは区間に畳まれる")
  (assert (in f"手番 j-{k}(" fold.text) fold.text))


(defn #^ tuple tool-heavy-turns [#^ int turns]
  "R35 の材料: 手番ごとに郵便(短い)+ agent の text(短い)+ 大きな tool_use の入力(≈ 2 KB)+ 大きな tool_result(≈ 3 KB)。
   戻り = #(messages events)。最後の手番の郵便 m-{turns-1} はこの手番の inputs として exclude する前提(その出来事は 1 つ前の
   手番の群に入る)。"
  (setv messages (tuple (lfor n (range turns) (message-row f"m-{n}" CONVERSATION "operator" f"問い {n}" (+ AT (* n 10000))))))
  (setv events (tuple (+ (lfor n (range turns) (event-of (+ 1 (* n 3)) f"j-{n}#a1" 0 (+ AT (* n 10000) 100) "text" {"text" f"答え {n}"}))
                         (lfor n (range turns) (event-of (+ 2 (* n 3)) f"j-{n}#a1" 1 (+ AT (* n 10000) 200) "tool_use"
                                                         {"toolName" "Bash" "toolUseId" f"t{n}" "input" {"cmd" (* f"x{n}" 1000)}}))
                         (lfor n (range turns) (event-of (+ 3 (* n 3)) f"j-{n}#a1" 2 (+ AT (* n 10000) 300) "tool_result"
                                                         {"toolUseId" f"t{n}" "output" (* f"y{n}" 1500)})))))
  #(messages events))


(deftest test-tool-items-are-thinned-oldest-first-before-any-turn-is-dropped
  ;; R35(段 11 lane 11v 便 3・agora-redesign #225): 上限を超えたら、手番を丸ごと落とす前に古い手番から道具の項(tool_use の入力・
  ;; tool_result の本文)を薄くする。agent の text は全手番残り、最新の手番は全文のまま。便 1 の形(落とすだけ)なら 5 手番が消えていた。
  (setv made (tool-heavy-turns 8))
  (setv records (RecordedTurns :events (get made 1) :complete True))
  (setv whole (run (rehydrate-history-of CONVERSATION (get made 0) records #("m-7") 200000 {} #() None)))
  (assert (= #(whole.thinned-turns whole.dropped-turns whole.kept-turns) #(0 0 7)) whole)
  (setv budget 14000)
  (setv k (// budget HISTORY-THIN-DIVISOR))
  (setv fold (run (rehydrate-history-of CONVERSATION (get made 0) records #("m-7") budget {} #() None)))
  (assert (<= fold.size-bytes budget) fold.size-bytes)
  (assert (= #(fold.dropped-turns fold.cut-bytes fold.kept-turns) #(0 0 7)) fold)
  (assert (>= fold.thinned-turns 1) fold)
  (assert (< fold.thinned-turns 7) fold)
  (for [n (range 8)]
    (assert (in f"答え {n}" fold.text) f"agent の text は残る: 答え {n}"))
  (for [n (range 7)]
    (assert (in f"問い {n}" fold.text) f"郵便は残る: 問い {n}"))
  ;; 古い手番の道具の項は薄い(先頭 k byte・元の byte を名乗る)。最新の手番の道具の結果は全文。
  (assert (in f"(先頭 {k} byte だけ・元 " fold.text) fold.text)
  (assert (not-in (* "y0" 1500) fold.text) "最も古い手番の道具の結果は薄い")
  (assert (in (* "y6" 1500) fold.text) "最新の手番の道具の結果は全文")
  (assert (in (* "y7" 1500) fold.text) "最新の手番の道具の結果は全文")
  (assert (not-in "古い手番" fold.text) "落としていないので見出しは無い")
  (assert (is fold.dropped-headline None))
  ;; 薄くする順は古い方から: 薄い手番の番号は 0 から連続。
  (for [n (range fold.thinned-turns)]
    (assert (not-in (* f"y{n}" 1500) fold.text) f"手番 {n} は薄い"))
  (for [n (range fold.thinned-turns 7)]
    (assert (in (* f"y{n}" 1500) fold.text) f"手番 {n} は全文")))


(deftest test-when-thinning-everything-is-not-enough-turns-are-dropped-behind-the-headline-in-thin-form
  ;; R35 × R34: 全部を薄くしても超える時だけ落とす。残した手番は薄い形のまま、落とした区間は見出し 1 行。
  (setv made (tool-heavy-turns 8))
  (setv records (RecordedTurns :events (get made 1) :complete True))
  (setv budget 1500)
  (setv k (// budget HISTORY-THIN-DIVISOR))
  (setv fold (run (rehydrate-history-of CONVERSATION (get made 0) records #("m-7") budget {} #() None)))
  (assert (<= fold.size-bytes budget) fold.size-bytes)
  (assert (= fold.thinned-turns 7) fold)
  (assert (>= fold.dropped-turns 1) fold)
  (assert (= (+ fold.kept-turns fold.dropped-turns) 7) fold)
  (assert (isinstance fold.dropped-headline str) fold)
  (assert (= (.count fold.text fold.dropped-headline) 1))
  (assert (in f"(先頭 {k} byte だけ・元 " fold.text) "残した手番の道具の項は薄い形")
  (assert (in "答え 7" fold.text) "最新の手番の text は残る")
  (for [n (range 8)]
    (assert (not-in (* f"y{n}" 1500) fold.text) f"全文の道具の結果は残らない: {n}")))


(deftest test-a-thin-rehydrate-has-no-tool-bodies-to-thin-and-counts-none
  ;; R35 × 薄い再開: ACP の見出しだけの材料には薄くする本文が無い — thinned_turns は 0 のまま、上限は便 1 の落とし(見出し)で守る。
  (setv messages (tuple (lfor n (range 6) (message-row f"m-{n}" CONVERSATION "operator" (+ f"問い {n} " (* "う" 120)) (+ AT (* n 10000))))))
  (setv records (tuple (lfor n (range 6)
                             (record-row f"j-{n}" CONVERSATION
                                         [{"seq" 0 "at" (+ AT (* n 10000) 100) "kind" "text" "bytes" 20 "sha256" "0"}
                                          {"seq" 1 "at" (+ AT (* n 10000) 200) "kind" "tool_use" "toolName" "Read" "toolUseId" f"t{n}" "bytes" 3000 "sha256" "0"}
                                          {"seq" 2 "at" (+ AT (* n 10000) 300) "kind" "tool_result" "toolUseId" f"t{n}" "bytes" 9000 "sha256" "0"}]
                                         (+ AT (* n 10000))))))
  (setv source (HeadlineTurns :records records :reason "record service read failed (0: unreachable)"))
  (setv fold (run (rehydrate-history-of CONVERSATION messages source #("m-5") 1900 {} #() None)))
  (assert fold.thin)
  (assert (<= fold.size-bytes 1900) fold.size-bytes)
  (assert (= fold.thinned-turns 0) fold)
  (assert (>= fold.dropped-turns 1) fold)
  (assert (isinstance fold.dropped-headline str) fold)
  (assert (not-in "先頭" fold.text) "薄い再開に薄くした印は無い"))


(deftest test-thinned-items-keep-the-head-and-name-the-original-bytes-while-text-and-mail-stay-byte-identical
  ;; R35 の不変: 薄くしても郵便・agent の text・system / user の行は byte も変わらない。薄くなるのは道具の 2 行だけで、
  ;; 同じ頭 + 本文の先頭 k byte + 元の byte の名乗り。同じ材料からは同じ答え(決定的)。
  (setv big-input {"file_path" "a.txt" "content" (* "あ" 500)})
  (setv big-output (+ "結果の頭" (* "い" 800)))
  (setv messages #((message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" AT)
                   (message-row "m-2" CONVERSATION "operator" "次の問い" (+ AT 9000))))
  (setv events #((event-of 1 "j-1#a1" 0 (+ AT 1000) "text" {"text" "覚えました"})
                 (event-of 2 "j-1#a1" 1 (+ AT 1100) "tool_use" {"toolName" "Write" "toolUseId" "t1" "input" big-input})
                 (event-of 3 "j-1#a1" 2 (+ AT 1200) "tool_result" {"toolUseId" "t1" "output" big-output "isError" True})
                 (event-of 4 "j-1#a1" 3 (+ AT 1300) "system" {"text" (* "system の本文 " 20)})
                 (event-of 5 "j-1#a1" 4 (+ AT 1400) "user" {"text" (* "user の本文 " 20)})))
  (setv records (RecordedTurns :events events :complete True))
  (setv whole (run (rehydrate-history-of CONVERSATION messages records #("m-2") 65536 {} #() None)))
  (assert (= whole.thinned-turns 0) whole)
  (setv budget 2400)
  (setv k (// budget HISTORY-THIN-DIVISOR))
  (setv fold (run (rehydrate-history-of CONVERSATION messages records #("m-2") budget {} #() None)))
  (assert (= #(fold.thinned-turns fold.dropped-turns fold.cut-bytes fold.kept-turns) #(1 0 0 1)) fold)
  (assert (<= fold.size-bytes budget) fold.size-bytes)
  (setv whole-lines (.splitlines whole.text))
  (setv fold-lines (.splitlines fold.text))
  (assert (= (len whole-lines) (len fold-lines)) #(whole-lines fold-lines))
  (setv changed (lfor [a b] (zip whole-lines fold-lines) :if (!= a b) #(a b)))
  (assert (= (len changed) 2) changed)
  (for [[a b] changed]
    (assert (or (in "agent の道具 Write:" a) (in "道具の結果(誤り):" a)) a)
    (assert (.startswith b (get (.split a "] " 1) 0)) #(a b)))
  (setv raw-output (.encode big-output "utf-8"))
  (setv head (.decode (cut raw-output 0 k) "utf-8" :errors "ignore"))
  (assert (in f"道具の結果(誤り): {head}(先頭 {k} byte だけ・元 {(len raw-output)} byte)" fold.text) fold.text)
  (setv raw-input (.encode (json.dumps big-input :ensure-ascii False) "utf-8"))
  (assert (in f"(先頭 {k} byte だけ・元 {(len raw-input)} byte)" fold.text) fold.text)
  (assert (in "合言葉は ひまわり" fold.text))
  (assert (in "覚えました" fold.text))
  (assert (= (. (run (rehydrate-history-of CONVERSATION messages records #("m-2") budget {} #() None)) text) fold.text) "決定的"))


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
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
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
  (assert (= (get world.sessions.sends -1) #(fresh (mailed asked) True)))
  (assert (not-in #(warm (mailed asked) True) world.sessions.sends) "別の家の session に送らない")
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
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" warm "claude-opus-5"))
  (.tick world 1000)
  (assert (= world.sessions.cleanups []) world.local.logs)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 1) "同じ家の手番で session を起こした")
  (assert (= (.sid world "j-2") warm))
  (assert (= (get world.sessions.sends -1) #(warm (mailed asked) True)))
  (assert (= world.acp.history-reads []) "温かい send は ACP の記録を読まない")
  (assert (= world.acp.headline-reads []) "温かい send は ACP の見出しを読まない")
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
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
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
  (assert (= (get world.sessions.sends -1) #(fresh (mailed asked) True)))
  (assert (not-in #(warm (mailed asked) True) world.sessions.sends) "model の違う温かい session に送った")
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
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" "sid-on-another-node"))
  (.tick world 0)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (setv prompt (str-at (get world.sessions.launches 0) "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話") prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (.endswith prompt (+ "\n\n" (mailed asked))) "郵便の見出しと本文は最後(headless の 1 手番目)")
  (assert (= world.sessions.sends []))
  (assert (any (gfor line world.local.logs (in f"rehydrates conversation {CONVERSATION} from the record service" line))) world.local.logs))


(deftest test-unreachable-record-service-rehydrates-thinly-from-acp-headlines
  ;; service が届かない(か配線されていない)時は ACP の見出しで薄く再開し、prompt と log がそれを名乗る。本文は無い。
  (setv world (World "headless" True))
  (setv world.record-service.unreachable True)
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row world.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp asked)
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
  (assert (= world.acp.headline-reads [CONVERSATION]) "薄い再開の拍は見出し(turn-record の全量)を 1 度読む(段 9q)")
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
  (assert (= bare.record-service.reads []))
  (assert (= bare.acp.headline-reads [CONVERSATION]) "弁 off の薄い再開も見出しを 1 度読む(段 9q)"))


(deftest test-rehydrate-reads-no-turn-record-list-when-the-record-service-answers
  ;; 段 9q(agora-redesign #77): 記録の service が答えた拍は ACP の turn-record の全量(見出し)を読まない — 本番の kind は
  ;; 29,913 行 / 172 MB / 頭の応答 59 秒で、claim(0.3 秒)の後の準備が 134 秒になり node の lease(90 秒)が切れていた。
  ;; 材料 = 本文は service(RecordRead 1 頁)・郵便は kind message(AcpConversationMail 1 度)・見出しは読まない。
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (.put-row world.acp (record-row "j-old" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] "other" warm))
  (.tick world 1000)
  (assert (= world.sessions.cleanups [warm]) world.local.logs)
  (assert (= (len world.sessions.launches) 2) world.local.logs)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) world.record-service.reads)
  (assert (= world.acp.history-reads [CONVERSATION]) "郵便は 1 度読む")
  (assert (= world.acp.headline-reads []) "service が答えた拍に turn-record の全量(見出し)を読んだ(段 9q)")
  (assert (not-in TURN-RECORD-KIND world.acp.lists) "turn-record の全量 list(AcpGet)を撃った(段 9q)")
  (setv prompt (str-at (get world.sessions.launches -1) "prompt"))
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (not-in "見出しだけ" prompt) "service が答えたのに見出しの行を畳んだ")
  (setv line (get (lfor l world.local.logs :if (in f"rehydrates conversation {CONVERSATION} from the record service" l) l) -1))
  (assert (in ", history read " line) line))


(deftest test-home-change-claims-in-the-same-tick-as-a-warm-turn-without-a-turn-record-list
  ;; 段 9q(agora-redesign #77)の受け入れ: 家が変わる手番(履歴からの再開)の claim は温かい手番と同じ拍に着地し
  ;; (claim = 宣言の照合だけ・器の準備は claim の後)、その拍が ACP に撃つ全量 list は agent-job の 1 本と郵便の 1 度だけ
  ;; (turn-record の全量は撃たない)。2 つの会話 — CONVERSATION は家が変わる手番・OTHER は同じ家の温かい手番 — を同じ拍に
  ;; Bound にして、両方が同じ拍で Running(sessionHandle あり)になることを確かめる。
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (.put-row world.acp (message-row "o-1" OTHER "operator" "こんにちは" (- AT 400)))
  (.put-row world.acp (AcpRow :namespace AGENT-JOB-NAMESPACE :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:k-1"
                              :kind AGENT-JOB-KIND :resource-id "k-1" :version "v1" :generation 1 :created-at-ms 600
                              :labels {} :payload {}
                              :spec {"subject" OTHER "inputs" ["o-1"]
                                     "charter" {"agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
                              :status {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"}
                                       "conditions" []}))
  (.tick world 1000)
  (setv other-warm (.sid world "k-1"))
  (setv other-path f"/homes/claude/acct/projects/-work/{other-warm}.jsonl")
  (setv (get world.local.transcripts other-path)
        (+ (.get world.local.transcripts other-path "") (assistant-line "こんにちは")))
  (.tick world 1000)
  (.finish-turn world.sessions other-warm (+ world.local.now-ms 200))
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.local.logs)
  (setv lists-before (len world.acp.lists))
  ;; 同じ拍に 2 つの Bound: 家が変わる手番(CONVERSATION・account other)と温かい手番(OTHER・同じ家)。
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] "other" warm))
  (.put-row world.acp (message-row "o-2" OTHER "operator" "続き" (+ world.local.now-ms 100)))
  (.put-row world.acp (AcpRow :namespace AGENT-JOB-NAMESPACE :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:k-2"
                              :kind AGENT-JOB-KIND :resource-id "k-2" :version "v1" :generation 1 :created-at-ms 700
                              :labels {} :payload {}
                              :spec {"subject" OTHER "inputs" ["o-2"] "affinity" {"predecessor" other-warm}
                                     "charter" {"agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
                              :status {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"}
                                       "conditions" []}))
  (.tick world 1000)
  (assert (= (get (.job-status world "j-2") "phase") "Running") (.job-status world "j-2"))
  (assert (= (get (.job-status world "k-2") "phase") "Running") (.job-status world "k-2"))
  (assert (!= (.sid world "j-2") warm) "家が変わる手番は新しい session")
  (assert (= (.sid world "k-2") other-warm) "温かい手番は同じ session へ")
  (setv arms (dfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") (get m "agentJobId") (get m "arm")))
  (assert (= (get arms "j-2") "rehydrate") arms)
  (assert (= (get arms "k-2") "send") arms)
  (assert (= world.acp.history-reads [CONVERSATION]) "郵便の読みは家が変わる手番の 1 度だけ")
  (assert (= world.acp.headline-reads []) "service が答えた拍に turn-record の全量を読んだ(段 9q)")
  (setv lists-in-tick (cut world.acp.lists lists-before None))
  (assert (not-in TURN-RECORD-KIND lists-in-tick) lists-in-tick)
  (assert (not-in MESSAGE-KIND lists-in-tick) lists-in-tick)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) world.record-service.reads))


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


;; ---------------------------------------------------------------------------
;; 段 10f 便 1b(agora-redesign #82): 本文を記録の service に置いた郵便(bodyRef)を読み手が取り寄せる
;; ---------------------------------------------------------------------------

(defn #^ AcpRow ref-message-row [#^ str message-id #^ str to #^ str sender #^ str stream-conversation #^ int at]
  "契約 message の 1 通 — 本文は記録の service(spec.bodyRef + bytes・body は無い)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}"
          :kind MESSAGE-KIND :resource-id message-id :version "v1" :generation 1 :created-at-ms at
          :labels {} :payload {}
          :spec {"id" message-id "to" to "from" sender "kind" "ask" "items" [] "refs" []
                 "bodyRef" {"conversation" stream-conversation "stream" message-id} "bytes" 70000
                 "sha256" (* "0" 64) "at" at}
          :status {"state" "delivered"}))


(deftest test-a-body-ref-is-read-by-its-stream-and-folded-where-the-body-was
  ;; 判断(純関数): bodyRef の在処・stream の出来事から本文・本文の表を引く読み(手番の入力と履歴の 1 項)。
  (setv ref-row (ref-message-row "m-b" CONVERSATION "operator" OTHER AT))
  (assert (= (run (message-body-ref-of ref-row.spec)) #(OTHER "m-b")))
  (assert (is (run (message-body-ref-of (. (message-row "m-1" CONVERSATION "operator" "本文" AT) spec))) None))
  (assert (= (run (mail-text-of #((event-of 1 "m-b" 1 AT "message" {"text" "長い本文"})))) "長い本文"))
  (assert (is (run (mail-text-of #((event-of 1 "m-b" 1 AT "text" {"text" "手番の本文"})))) None))
  (setv inline (message-row "m-1" CONVERSATION "operator" "短い本文" AT))
  ;; 段 10 lane 10o: 戻りは #(bodies attachments missing)— 本文と添付は同じ 1 つの述語で並ぶ。
  ;; 段 10 lane 10r 追補: 本文の前に郵便の見出し(mail-turn-text-of の 1 点)。
  (assert (= (run (message-bodies-of #(inline ref-row) #("m-1" "m-b") {"m-b" "長い本文"} {}))
             #(#((mailed inline) (run (mail-turn-text-of "m-b" ref-row.spec "長い本文"))) #(#() #()) #())))
  (assert (= (run (message-bodies-of #(inline ref-row) #("m-1" "m-b") {} {}))
             #(#((mailed inline)) #(#()) #("m-b"))))
  (setv fold (run (rehydrate-history-of CONVERSATION #(ref-row) (RecordedTurns :events #() :complete True) #() 65536 {"m-b" "長い本文"} #() None)))
  (assert (in "長い本文" fold.text) fold.text))


(deftest test-the-first-turn-reads-a-body-ref-from-the-record-service
  ;; agentd の 1 点(mail-bodies-by-ref): bodyRef の郵便は RecordReadStream で stream を読み、本文を手番に畳む。
  (setv world (World "tmux" :record True))
  (setv (get world.record-service.stored #(OTHER "m-b" 1)) {"producerSeq" 1 "at" AT "kind" "message" "text" "長い本文の依頼"})
  (.put-row world.acp (ref-message-row "m-b" CONVERSATION "operator" OTHER (- AT 500)))
  (.put-row world.acp (bound-row "j-b" ["m-b"] "acct" None))
  (.tick world 0)
  (assert (in #(OTHER "m-b") world.record-service.stream-reads) world.local.logs)
  (setv carried (+ (lfor launch world.sessions.launches (str (.get launch "prompt" "")))
                   (lfor send world.sessions.sends (str (get send 1)))))
  (assert (any (gfor text carried (in "長い本文の依頼" text))) carried))


;; ---------------------------------------------------------------------------
;; 段 10 lane 10o(agora-redesign #96): 郵便の添付は見出し + 記録の service の中身
;; ---------------------------------------------------------------------------

(setv PNG-B64 "iVBORw0KGgo=")


(defn #^ AcpRow attached-message-row [#^ str message-id #^ str to #^ str sender #^ str body
                                      #^ str stream-conversation #^ int at #^ list headlines]
  "契約 message の 1 通 — 本文は行に、添付は見出しの列(中身は記録の service)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}"
          :kind MESSAGE-KIND :resource-id message-id :version "v1" :generation 1 :created-at-ms at
          :labels {} :payload {}
          :spec {"id" message-id "to" to "from" sender "kind" "ask" "items" [] "refs" []
                 "body" body "attachments" headlines
                 "sha256" (* "0" 64) "at" at}
          :status {"state" "delivered"}))


(defn #^ dict headline-of [#^ str conversation #^ str stream #^ int seq #^ str mime #^ str digest
                           #^ (| str None) [name None] #^ int [size 8]]
  (setv one {"ref" {"conversation" conversation "stream" stream} "seq" seq "mime" mime
             "bytes" size "sha256" digest})
  (when (is-not name None)
    (setv (get one "name") name))
  one)


(deftest test-attachment-headlines-are-read-and-only-the-matching-event-becomes-a-typed-value
  ;; 判断(純関数): 見出しの形 → #(conversation stream seq mime bytes sha256 name)・出来事 → 型つきの値。
  ;; 見出しと食い違う出来事(mime 違い・sha256 違い・中身なし)は値にしない(呼び手が AttachmentIgnored)。
  ;; ⚠ 見出しの bytes / sha256 は**画像の生の byte**(差出人の client の attachments-plan と同じ材料)。
  ;; 記録の service の出来事が名乗る値(本文の欄の compact JSON を測った物)ではない — 実弾 2026-09-15。
  (setv digest (.hexdigest (hashlib.sha256 (base64.b64decode PNG-B64))))
  (setv raw-size (len (base64.b64decode PNG-B64)))
  (setv row (attached-message-row "m-i" CONVERSATION "operator" "見て" OTHER AT
                                  [(headline-of OTHER "m-i" 1 "image/png" digest "red.png" raw-size)]))
  (setv headlines (run (message-attachments-of row.spec)))
  (assert (= (len headlines) 1) headlines)
  (assert (= (cut (get headlines 0) 0 4) #(OTHER "m-i" 1 "image/png")) headlines)
  ;; 形の外の項は落とす(ref が無い・seq が負・seq が bool)。
  (setv broken (attached-message-row "m-x" CONVERSATION "operator" "見て" OTHER AT
                                     [{"seq" 1 "mime" "image/png"}
                                      (headline-of OTHER "m-x" -1 "image/png" digest)
                                      "画像"]))
  (assert (= (run (message-attachments-of broken.spec)) #()) "形の外は落とす")
  ;; 見出しと合う出来事だけが型つきの値になる。
  ;; 記録の出来事は本文の欄を測った bytes / sha256 を名乗る(見出しとは別の値)— それでも通ることを見る。
  (setv good (event-of 2 "m-i" 1 AT "attachment"
                       {"mime" "image/png" "data" PNG-B64 "name" "red.png" "bytes" 999}))
  (setv carried (run (attachment-of #(good) (get headlines 0))))
  (assert (isinstance carried TurnAttachment) carried)
  (assert (= #(carried.mime carried.data carried.name) #("image/png" PNG-B64 "red.png")) carried)
  ;; 型つきの値が名乗る大きさと指紋も**生の byte**の物(出来事の 999 を写さない)。
  (assert (= carried.bytes raw-size) carried)
  (assert (= carried.sha256 digest) carried)
  ;; sha256 が食い違えば値にしない(中身を発明しない)。
  (setv forged (headline-of OTHER "m-i" 1 "image/png" (* "b" 64) "red.png" raw-size))
  (setv forged-headlines (run (message-attachments-of
                                (. (attached-message-row "m-i" CONVERSATION "operator" "見て" OTHER AT [forged]) spec))))
  (assert (is (run (attachment-of #(good) (get forged-headlines 0))) None) "見出しと食い違う中身は運ばない")
  ;; 出来事が無い(取り寄せられなかった)拍も None。
  (assert (is (run (attachment-of #() (get headlines 0))) None))
  ;; 起こす腕は畳んだ郵便の添付を 1 本に並べ、charter に載せる(添付が無ければ charter を変えない)。
  (assert (= (run (first-turn-attachments-of #(#(carried) #() #(carried)))) #(carried carried)))
  (assert (= (run (launch-charter-with-attachments {"prompt" "x"} #())) {"prompt" "x"}))
  ;; ⚠ charter は RPC へ出る object — **項の綴り**で載る(型つきの値のままではない)。
  (setv charter (run (launch-charter-with-attachments {"prompt" "x"} #(carried))))
  (assert (= charter {"prompt" "x"
                      "attachments" [{"mime" "image/png" "data" PNG-B64 "bytes" carried.bytes
                                      "sha256" carried.sha256 "name" "red.png"}]})
          charter)
  ;; 実弾 2026-09-15 03:08: 型つきの値のまま入れていたので RPC へ出す拍に
  ;; TypeError: Object of type TurnAttachment is not JSON serializable で tick ごと落ちていた。
  (json.dumps charter)
  ;; host の口は同じ 1 点で型つきに戻す(書き手と読み手が同じ綴りを見る)。
  (setv back (attachment-of-wire (get (get charter "attachments") 0)))
  (assert (= back carried) #(back carried)))


(deftest test-the-headline-is-measured-on-the-raw-image-not-on-the-stored-event
  ;; 実弾 2026-09-15 02:39(本番の e2e chat.send-image): 見出しの bytes / sha256 は差出人が測る**画像の生の byte**
  ;; なのに、記録の service の出来事が名乗る値(本文の欄の compact JSON を測った物)と比べていた。
  ;; ⇒ 必ず食い違い、本番の log に『attachment 1 of message … could not be read from the record service』が出て、
  ;; 画像が 1 枚も CLI へ渡らないまま手番が条件なしで終わっていた(黙って画像だけが落ちた)。
  ;; この検は「出来事が**別の値**を名乗っても、生の byte が見出しと合えば通る」ことを固定する。
  (setv raw (base64.b64decode PNG-B64))
  (setv digest (.hexdigest (hashlib.sha256 raw)))
  (setv row (attached-message-row "m-r" CONVERSATION "operator" "見て" OTHER AT
                                  [(headline-of OTHER "m-r" 1 "image/png" digest "red.png" (len raw))]))
  (setv headlines (run (message-attachments-of row.spec)))
  (assert (= (len headlines) 1) headlines)
  ;; 記録の service が名乗る bytes / sha256 は本文の欄を測った別の値(本番と同じ形)。
  (setv stored (event-of 7 "m-r" 1 AT "attachment"
                         {"mime" "image/png" "data" PNG-B64 "name" "red.png"
                          "bytes" (len (record-body-bytes {"data" PNG-B64}))}))
  (assert (!= stored.bytes (len raw)) "この検の前提: 出来事の値と生の byte は別物")
  (setv carried (run (attachment-of #(stored) (get headlines 0))))
  (assert (isinstance carried TurnAttachment) #(carried stored.bytes (len raw)))
  (assert (= #(carried.bytes carried.sha256) #((len raw) digest)) carried)
  ;; 反例: 生の byte が見出しと食い違う中身は運ばない(中身を発明しない)。
  (setv other (event-of 8 "m-r" 1 AT "attachment" {"mime" "image/png" "data" "AAAA"}))
  (assert (is (run (attachment-of #(other) (get headlines 0))) None) "生の byte が違えば運ばない")
  ;; 反例: base64 として解けない綴りも運ばない。
  (setv broken (event-of 9 "m-r" 1 AT "attachment" {"mime" "image/png" "data" "!!!not-base64!!!"}))
  (assert (is (run (attachment-of #(broken) (get headlines 0))) None) "解けない綴りは運ばない"))


(deftest test-every-arm-that-folds-the-mail-carries-the-attachment
  ;; 実弾 2026-09-15 09:5x(operator の会話 c-01M1XGMDHR35FBBC04W1JXM5KJ): 画像を添付して送ったのに
  ;; agent が画像を読まない。誤りも条件も出ず、手番は条件なしで終わっていた。根 = **腕が resume の時だけ**
  ;; 添付が落ちていた — resume-params-of が charter の欄を**名簿で**写すのに、attachments を名簿に入れ忘れ、
  ;; host の session.resume も launch.hy の resume も運んでいなかった(3 か所で黙って落ちる)。
  ;; 見落としの根は**検が launch の腕しか通していなかった**こと。⇒ 郵便を畳む腕を全部ここで固定する。
  (setv raw (base64.b64decode PNG-B64))
  (setv carried (TurnAttachment :mime "image/png" :data PNG-B64 :bytes (len raw)
                                :sha256 (.hexdigest (hashlib.sha256 raw)) :name "red.png"))
  (setv charter (run (launch-charter-with-attachments
                       {"session_id" "s-new" "prompt" "start" "model" "claude-opus-5"} #(carried))))
  (setv wire (get charter MESSAGE-ATTACHMENTS-KEY))
  ;; 起こす腕は 3 つ(launch / resume / rehydrate)。launch と rehydrate は charter そのものを運ぶ。
  (for [arm [NEXT-ARM-LAUNCH NEXT-ARM-RESUME NEXT-ARM-REHYDRATE]]
    (assert (run (first-turn-carries-inputs BACKEND-HEADLESS arm)) arm))
  ;; resume の params は名簿で写す — 添付が名簿から漏れると、この検が赤になる。
  (setv params (run (resume-params-of "s-old" charter)))
  (assert (in MESSAGE-ATTACHMENTS-KEY params)
          #("resume の params が添付を運ばない(名簿の漏れ)" (sorted (.keys params))))
  (assert (= (get params MESSAGE-ATTACHMENTS-KEY) wire) params)
  ;; 運ぶ形は launch と同じ項の綴り(host が同じ 1 点で型つきに戻せる)。
  (assert (= (attachment-of-wire (get (get params MESSAGE-ATTACHMENTS-KEY) 0)) carried))
  (json.dumps params)
  ;; 添付の無い手番の params は 1 byte も変わらない(欄を作らない)。
  (setv plain (run (resume-params-of "s-old" {"session_id" "s-new" "prompt" "start"})))
  (assert (not-in MESSAGE-ATTACHMENTS-KEY plain) plain))


(deftest test-the-first-turn-carries-the-attachment-to-the-substrate-as-a-typed-value
  ;; agentd の 1 点(mail-bodies-by-ref): 添付の見出しを持つ郵便は本文と同じ 1 回の stream の読みで中身も拾い、
  ;; 器へは型つきのまま渡る(agentd は CLI の綴りを組まない)。
  (setv world (World "tmux" :record True))
  (setv digest (* "a" 64))
  (setv (get world.record-service.stored #(OTHER "m-i" 1))
        {"producerSeq" 1 "at" AT "kind" "attachment" "mime" "image/png" "data" PNG-B64 "name" "red.png"})
  ;; 見出しは差出人が測る**生の byte**(記録の service が名乗る値ではない — 実弾 2026-09-15)。
  (setv raw (base64.b64decode PNG-B64))
  (setv stored-digest (.hexdigest (hashlib.sha256 raw)))
  (.put-row world.acp (attached-message-row "m-i" CONVERSATION "operator" "見て" OTHER (- AT 500)
                                            [(headline-of OTHER "m-i" 1 "image/png" stored-digest "red.png"
                                                          (len raw))]))
  (.put-row world.acp (bound-row "j-i" ["m-i"] "acct" None))
  (.tick world 0)
  (assert (in #(OTHER "m-i") world.record-service.stream-reads) world.local.logs)
  (setv handed world.sessions.sent-attachments)
  (assert (= (len handed) 1) handed)
  (setv carried (get (get handed 0) 1))
  (assert (= (len carried) 1) carried)
  (assert (isinstance (get carried 0) TurnAttachment) carried)
  (assert (= #((. (get carried 0) mime) (. (get carried 0) data) (. (get carried 0) name))
             #("image/png" PNG-B64 "red.png")) carried))


(deftest test-a-substrate-that-refuses-attachments-gets-the-condition-not-silence
  ;; 器が添付を落とした拍は条件 AttachmentIgnored が手番に付く(本文は届く・黙って落とさない)。
  (setv world (World "tmux" :record True))
  (setv world.sessions.attachments-ignored "this substrate cannot carry attachments")
  (setv (get world.record-service.stored #(OTHER "m-j" 1))
        {"producerSeq" 1 "at" AT "kind" "attachment" "mime" "image/png" "data" PNG-B64})
  (setv raw (base64.b64decode PNG-B64))
  (setv stored-digest (.hexdigest (hashlib.sha256 raw)))
  (.put-row world.acp (attached-message-row "m-j" CONVERSATION "operator" "見て" OTHER (- AT 500)
                                            [(headline-of OTHER "m-j" 1 "image/png" stored-digest None
                                                          (len raw))]))
  (.put-row world.acp (bound-row "j-j" ["m-j"] "acct" None))
  (.tick world 0)
  ;; 条件は手番の途中の事実として in-flight に積まれ、手番の終わりに Ended へ乗る(InputUnavailable と同じ路)。
  (setv in-flight (lfor job world.state.jobs :if (= job.job-id "j-j") job))
  (assert (= (len in-flight) 1) world.state.jobs)
  (assert (in "AttachmentIgnored"
              (lfor one (. (get in-flight 0) pending-conditions) (.get one "type")))
          #((. (get in-flight 0) pending-conditions) world.local.logs))
  ;; 本文そのものは器へ届いている。
  (setv said (+ (lfor launch world.sessions.launches (str (.get launch "prompt" "")))
                (lfor send world.sessions.sends (str (get send 1)))))
  (assert (any (gfor text said (in "見て" text))) said))


;; ---------------------------------------------------------------------------
;; 段 12 lane 12j 便 3(agora-redesign #233・ADR-012 R38): 要約(kind summary)は原文の前に区間の順で畳まれ、原文は要約の区間の後だけ
;; ---------------------------------------------------------------------------

(deftest test-summaries-fold-first-in-region-order-and-drop-only-after-raw-under-the-budget
  ;; 純関数(便 3 → 追補 5): 要約 2 区間(渡す順は逆)→ recordSeq の区間の順で原文と郵便より前・頭が「古い区間 2 つは要約」を名乗る・
  ;; summary_regions = 2。上限は原文の古い手番から落とし、要約は原文を最新の 1 手番まで落としても超える時だけ古い要約から落ちて
  ;; 見出しが kind 要約 を数え、頭の数も減る。見出しの期間は原文の時刻(要約の区間の番号を時刻に読まない)。要約なしは今日どおり。
  (setv messages #((message-row "m-1" CONVERSATION "operator" "問い" AT)
                   (message-row "m-2" CONVERSATION "operator" "続きの問い" (+ AT 200))))
  (setv events (tuple [(event-of 5 "j-2#a1" 0 (+ AT 100) "text" {"text" (+ "原文の答え " (* "あ" 1000))})
                       (event-of 6 "j-3#a1" 0 (+ AT 300) "text" {"text" (+ "新しい答え " (* "い" 1000))})]))
  (setv summaries (tuple [(HistorySummary :from-seq 3 :to-seq 4 :at (+ AT 50) :model "m" :text (+ "後の要約 " (* "う" 100)))
                          (HistorySummary :from-seq 0 :to-seq 2 :at (+ AT 40) :model "m" :text (+ "先の要約 " (* "え" 100)))]))
  (setv records (RecordedTurns :events events :complete True))
  (setv fold (run (rehydrate-history-of CONVERSATION messages records #() 65536 {} summaries None)))
  (assert (= fold.summary-regions 2))
  (assert (= fold.dropped-summaries 0))
  (assert (in "古い区間 2 つは要約" fold.text) fold.text)
  (setv first-at (.index fold.text "[要約 recordSeq 0〜2・m] 先の要約"))
  (setv second-at (.index fold.text "[要約 recordSeq 3〜4・m] 後の要約"))
  (assert (< first-at second-at) fold.text)
  (assert (< second-at (.index fold.text "問い")) "要約が郵便より後に並んだ")
  (assert (< second-at (.index fold.text "原文の答え")) "要約が原文より後に並んだ")
  (assert (= fold.thinned-turns 0) "要約は薄くならない")
  ;; 上限を 1 byte 下回る: 落ちるのは原文の古い手番(問い + 原文の答え)で、要約 2 本は残る。見出しの期間は原文の時刻。
  (setv raw-dropped (run (rehydrate-history-of CONVERSATION messages records #() (- fold.size-bytes 1) {} summaries None)))
  (assert (= raw-dropped.dropped-turns 1) raw-dropped.text)
  (assert (= raw-dropped.dropped-summaries 0))
  (assert (= raw-dropped.summary-regions 2))
  (assert (in "先の要約" raw-dropped.text))
  (assert (in "後の要約" raw-dropped.text))
  (assert (not-in "原文の答え" raw-dropped.text) "原文の古い手番が先に落ちていない")
  (assert (in "新しい答え" raw-dropped.text))
  (assert (isinstance raw-dropped.dropped-headline str))
  (assert (not-in HISTORY-SUMMARY-KIND raw-dropped.dropped-headline) raw-dropped.dropped-headline)
  (setv first-stamp (run (history-time-of AT)))
  (setv last-stamp (run (history-time-of (+ AT 100))))
  (assert (.startswith raw-dropped.dropped-headline f"[{first-stamp}〜{last-stamp}] 古い手番 1 件(出来事と郵便 2 件)") raw-dropped.dropped-headline)
  (assert (not-in "1970" raw-dropped.dropped-headline))
  ;; 見出しは頭の直後・要約と残した手番の前。
  (assert (= (get (.split raw-dropped.text "\n\n") 1) raw-dropped.dropped-headline) raw-dropped.text)
  (assert (< (.index raw-dropped.text raw-dropped.dropped-headline) (.index raw-dropped.text "先の要約")))
  ;; さらに下回る: 原文は最新の 1 手番だけなので、古い要約から落ちて見出しが kind 要約 を数え、頭の数も減る。
  (setv one-summary (run (rehydrate-history-of CONVERSATION messages records #() (- raw-dropped.size-bytes 1) {} summaries None)))
  (assert (= one-summary.dropped-summaries 1) one-summary.text)
  (assert (= one-summary.summary-regions 1))
  (assert (= one-summary.dropped-turns 1))
  (assert (not-in "先の要約" one-summary.text) "古い要約が先に落ちていない")
  (assert (in "後の要約" one-summary.text))
  (assert (in "古い区間 1 つは要約" one-summary.text) one-summary.text)
  (assert (in f": {HISTORY-SUMMARY-KIND} 1・" one-summary.dropped-headline) one-summary.dropped-headline)
  (assert (in "新しい答え" one-summary.text))
  (setv no-summary (run (rehydrate-history-of CONVERSATION messages records #() (- one-summary.size-bytes 1) {} summaries None)))
  (assert (= no-summary.dropped-summaries 2) no-summary.text)
  (assert (= no-summary.summary-regions 0))
  (assert (not-in "は要約" no-summary.text) no-summary.text)
  (assert (in f": {HISTORY-SUMMARY-KIND} 2・" no-summary.dropped-headline) no-summary.dropped-headline)
  (assert (in "新しい答え" no-summary.text))
  ;; 要約なし = 今日どおり
  (setv plain (run (rehydrate-history-of CONVERSATION messages records #() 65536 {} #() None)))
  (assert (= plain.summary-regions 0))
  (assert (not-in "要約" plain.text)))


(deftest test-the-production-shape-keeps-all-summaries-and-drops-raw-turns-with-a-real-period
  ;; 便 4 の実射 2026-09-16 18:08(aj-88JXQX…・operator の会話): 上限 65,536 byte・原文 1,500 出来事・要約 4 本(6〜8 KB)。旧の順では
  ;; 要約 4 本が最も古い項として最初に落ち(本文に 1 本も残らない)、見出しの期間は recordSeq 0 を時刻に読んで 1970-01-01 から始まった。
  ;; 新: 要約 4 本は残り、落ちるのは原文の古い手番で、見出しの期間は原文の時刻。
  (setv regions [[0 171] [172 390] [391 591] [592 600]])
  (setv summaries (tuple (lfor i (range 4)
                               (HistorySummary :from-seq (get (get regions i) 0) :to-seq (get (get regions i) 1) :at (+ AT (* i 1000))
                                               :model "claude-opus-5" :text (+ f"要約 {i} " (* "要" 2000))))))
  (setv messages (tuple (lfor n (range 30) (message-row f"m-{n}" CONVERSATION "operator" f"問い {n}" (+ AT 100000 (* n 10000))))))
  (setv events (tuple (lfor n (range 30) (event-of (+ 601 n) f"j-{n}#a1" 0 (+ AT 100000 (* n 10000) 500) "text" {"text" (+ f"答え {n} " (* "本" 2000))}))))
  (setv fold (run (rehydrate-history-of CONVERSATION messages (RecordedTurns :events events :complete True) #() 65536 {} summaries None)))
  (assert (<= fold.size-bytes 65536) fold.size-bytes)
  (assert (= fold.summary-regions 4) fold.text)
  (assert (= fold.dropped-summaries 0))
  (for [region regions]
    (assert (in f"[要約 recordSeq {(get region 0)}〜{(get region 1)}・claude-opus-5]" fold.text) f"要約 {region} が本文に残っていない"))
  (assert (>= fold.dropped-turns 1) fold.text)
  (assert (isinstance fold.dropped-headline str))
  (assert (not-in "1970" fold.dropped-headline) fold.dropped-headline)
  (assert (not-in HISTORY-SUMMARY-KIND fold.dropped-headline) fold.dropped-headline)
  (setv first-stamp (run (history-time-of (+ AT 100000))))
  (assert (.startswith fold.dropped-headline f"[{first-stamp}〜") fold.dropped-headline)
  (assert (< (.index fold.text "[要約 recordSeq 592〜600") (.index fold.text "問い 29")) "要約が原文より後に並んだ")
  (assert (in "答え 29" fold.text)))


(deftest test-rehydrate-folds-existing-summaries-first-and-reads-raw-only-after-their-floor
  ;; agentd を一周: kind summary の行([0, 1])とその本文(記録の service の stream summary#0-1)が在る会話の履歴からの再開は、
  ;; 要約を原文の前に畳み、要約が覆う区間の原文(1 手番目の本文)は読まない(読みの下限 = to)。行は AcpConversationSummaries で
  ;; 1 回、本文は RecordReadStream でその stream を 1 回。
  (setv world (World "tmux" True))
  (setv warm (run-first-turn world))
  (assert (= (len (.events-of world.record-service CONVERSATION "j-1#a1")) 1))
  (setv covered (max (lfor [key seq] (.items world.record-service.record-seqs) :if (= (get key 1) "j-1#a1") seq)))
  (.put-row world.acp (summary-row 0 covered AT))
  (setv (get world.record-service.stored #(CONVERSATION f"summary#0-{covered}" 0))
        {"producerSeq" 0 "at" AT "kind" "summary" "text" "要約: 合言葉は ひまわり と覚えた。" "model" "claude-opus-5"})
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (+ world.local.now-ms 100)))
  (.put-row world.acp asked)
  (.put-row world.acp (bound-row "j-2" ["m-2"] "other" warm))
  (.tick world 1000)
  (assert (= (len world.sessions.launches) 2) world.local.logs)
  (setv prompt (str-at (get world.sessions.launches -1) "prompt"))
  (assert (in f"[要約 recordSeq 0〜{covered}・claude-opus-5] 要約: 合言葉は ひまわり と覚えた。" prompt) prompt)
  (assert (in "古い区間 1 つは要約" prompt) prompt)
  (assert (not-in "agent: 覚えました" prompt) "要約が覆う区間の原文が畳みに残った")
  ;; 追補 6: 要約が覆う記録の終わり(recordSeq = covered の出来事の at)より古い郵便(m-1)は要約が担う — 畳みに並ばず、数だけ log に。
  (assert (not-in f"operator → {CONVERSATION}(note): 合言葉は ひまわり" prompt) "要約が覆う期間の郵便が畳みに残った")
  (assert (in #(CONVERSATION (- covered 1) 1 #()) world.record-service.since-reads) world.record-service.since-reads)
  (assert (= world.acp.summary-reads [CONVERSATION]))
  (assert (in #(CONVERSATION f"summary#0-{covered}") world.record-service.stream-reads))
  (assert (any (gfor line world.local.logs (in "(1 summaries," line))) world.local.logs)
  (assert (any (gfor line world.local.logs (in ", 1 mails left to the summaries" line))) world.local.logs)
  ;; 要約の無い会話は今日どおり(行の読みは 1 回・本文の stream は読まない)
  (setv plain (World "tmux" True))
  (setv warm2 (run-first-turn plain))
  (.put-row plain.acp (message-row "m-2" CONVERSATION "operator" "続き" (+ plain.local.now-ms 100)))
  (.put-row plain.acp (bound-row "j-2" ["m-2"] "other" warm2))
  (.tick plain 1000)
  (setv prompt2 (str-at (get plain.sessions.launches -1) "prompt"))
  (assert (in "agent: 覚えました" prompt2))
  (assert (not-in "要約" prompt2))
  (assert (= plain.acp.summary-reads [CONVERSATION]))
  (assert (= plain.record-service.stream-reads [])))


(deftest test-a-recorded-conversation-without-a-candidate-rehydrates-instead-of-launching
  ;; 段 12 lane 12j 追補 4(agora-redesign #233 / #176)— 実弾 2026-09-16 17:29(aj-545JP9E9ZMZHPM11ZW99KM51AC・operator の会話):
  ;; 宣言を変えた手番は Messaging の lineageFor(段 12 lane 12k)が predecessor を空にし、前の手番の agent-job の行は終了 300 s で
  ;; 回収済み — 候補なし。記録の service には会話の手番が在る。旧: 「候補なし → launch」で 2,100 出来事の記録も 4 本の要約も読まずに
  ;; 起きた。新: 記録の service へ 1 読み(since 0・limit 1・原文の kind)で在否を問い、在れば履歴から再開する。
  (setv world (World "headless" True))
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (setv (get world.record-service.stored #(CONVERSATION "j-0#a1" 0)) {"producerSeq" 0 "at" (- AT 8000) "kind" "text" "text" "覚えました"})
  (setv asked (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp asked)
  ;; predecessor なし・同じ会話の agent-job の行も無い(回収済み)。
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" None))
  (.tick world 0)
  (assert (= world.sessions.resumes []))
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (setv launch (get world.sessions.launches 0))
  (setv prompt (str-at launch "prompt"))
  (assert (.startswith prompt "start\n\nこれまでの会話") prompt)
  (assert (in "agent: 覚えました" prompt) prompt)
  (assert (.endswith prompt (+ "\n\n" (mailed asked))) "郵便の見出しと本文は最後(headless の 1 手番目)")
  (assert (= world.record-service.since-reads [#(CONVERSATION 0 1 RECORD-RAW-EVENT-KINDS)]) world.record-service.since-reads)
  (assert (= world.record-service.reads [#(CONVERSATION None RECORD-PAGE-MAX-LIMIT)]) world.record-service.reads)
  (setv stamp (dict-at (dict-at launch "launch_attribution") "agentd"))
  (assert (= (get stamp "arm") "rehydrate") stamp)
  (assert (any (gfor line world.local.logs (in "has no session to continue, but the record service holds the conversation's turns" line))) world.local.logs)
  (assert (any (gfor line world.local.logs (in f"rehydrates conversation {CONVERSATION} from the record service" line))) world.local.logs)
  (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
  (assert (= (get metric "arm") "rehydrate"))
  ;; 記録の無い会話(最初の手番)は今日どおり launch — 問いは 1 読みで、答えは空の頁。
  (setv fresh (World "tmux" True))
  (run-first-turn fresh)
  (assert (= fresh.record-service.since-reads [#(CONVERSATION 0 1 RECORD-RAW-EVENT-KINDS)]) fresh.record-service.since-reads)
  (assert (= (len fresh.sessions.launches) 1))
  (setv first-stamp (dict-at (dict-at (get fresh.sessions.launches 0) "launch_attribution") "agentd"))
  (assert (= (get first-stamp "arm") "launch") first-stamp)
  (assert (not (any (gfor line fresh.local.logs (in "rehydrates conversation" line)))) fresh.local.logs)
  ;; 記録の service が答えない拍は「在る」と読む(一過性の不達で履歴を失わない)— 再開の腕が薄い再開と名乗って見出しへ落ちる。
  (setv down (World "headless" True))
  (setv down.record-service.unreachable True)
  (.put-row down.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (.put-row down.acp (record-row "j-0" CONVERSATION [{"seq" 0 "at" (- AT 8000) "kind" "text" "bytes" 20 "sha256" "0"}] (- AT 8500)))
  (.put-row down.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row down.acp (bound-row "j-2" ["m-2"] "acct" None))
  (.tick down 0)
  (assert (= (len down.sessions.launches) 1) down.local.logs)
  (setv down-stamp (dict-at (dict-at (get down.sessions.launches 0) "launch_attribution") "agentd"))
  (assert (= (get down-stamp "arm") "rehydrate") down-stamp)
  (assert (any (gfor line down.local.logs (in "rehydrates thinly from ACP headlines" line))) down.local.logs))


(deftest test-mail-covered-by-the-summaries-is-left-to-them-and-does-not-eat-the-budget
  ;; 追補 6(実射 2026-09-16 18:45 aj-X92PW3ZHGW36ZCQWR2ZZPJNACS): 落ちた 77 手番の大半は郵便 105 通 — ACP の郵便は recordSeq を持たないので
  ;; 要約が覆う期間の郵便も原文として畳みの候補に入り、上限を食っていた。新: floor-at(要約が覆う記録の終わりの出来事の at)以前の郵便は
  ;; 畳まず summarized_mails に数える。floor-at = None(要約なし・読めない)は今日どおり全通。
  (setv summaries (tuple [(HistorySummary :from-seq 0 :to-seq 600 :at (+ AT 900000) :model "m" :text (+ "要約 " (* "要" 200)))]))
  (setv old-mails (lfor n (range 105) (message-row f"m-{n}" CONVERSATION "operator" f"古い問い {n}" (+ AT (* n 1000)))))
  (setv new-mails [(message-row "m-new-1" CONVERSATION "operator" "新しい問い 1" (+ AT 300000))
                   (message-row "m-new-2" CONVERSATION "operator" "新しい問い 2" (+ AT 400000))])
  (setv messages (tuple (+ old-mails new-mails)))
  (setv events (tuple [(event-of 601 "j-9#a1" 0 (+ AT 300500) "text" {"text" "新しい答え 1"})
                       (event-of 602 "j-10#a1" 0 (+ AT 400500) "text" {"text" "新しい答え 2"})]))
  (setv records (RecordedTurns :events events :complete True))
  ;; floor-at = 105 通目の郵便の直後(recordSeq 600 の出来事の at)。
  (setv fold (run (rehydrate-history-of CONVERSATION messages records #() 65536 {} summaries (+ AT 104000))))
  (assert (= fold.summarized-mails 105) fold.summarized-mails)
  (assert (= fold.dropped-turns 0) fold.text)
  (assert (is fold.dropped-headline None) fold.dropped-headline)
  (assert (not-in "古い問い" fold.text) "要約が覆う期間の郵便が畳みに残った")
  (assert (in "新しい問い 1" fold.text))
  (assert (in "新しい問い 2" fold.text))
  (assert (in "新しい答え 2" fold.text))
  (assert (= fold.kept-turns 2) fold)
  (assert (< (.index fold.text "[要約 recordSeq 0〜600") (.index fold.text "新しい問い 1")))
  ;; 境界: at = floor-at の郵便は要約の側(<=)。
  (setv edge (run (rehydrate-history-of CONVERSATION messages records #() 65536 {} summaries (+ AT 300000))))
  (assert (= edge.summarized-mails 106) edge.summarized-mails)
  (assert (not-in "新しい問い 1" edge.text))
  (assert (in "新しい問い 2" edge.text))
  ;; floor-at = None: 今日どおり全通が候補(古い郵便が上限を食い、落とした側に数えられる)。
  (setv plain (run (rehydrate-history-of CONVERSATION messages records #() 65536 {} summaries None)))
  (assert (= plain.summarized-mails 0))
  (assert (in "古い問い 0" plain.text) plain.text)
  ;; 純関数: floor の出来事の at の読み。
  (setv page (RecordPage :events #((event-of 600 "j-8#a1" 0 (+ AT 104000) "text" {"text" "x"})) :next None))
  (assert (= (run (summary-floor-at-of page 600)) (+ AT 104000)))
  (assert (is (run (summary-floor-at-of page 599)) None) "recordSeq が floor でない出来事を時刻に読んだ")
  (assert (is (run (summary-floor-at-of (RecordPage :events #() :next None) 600)) None))
  (assert (is (run (summary-floor-at-of (RecordUnread 0 "unreachable") 600)) None)))


(deftest test-the-record-probe-and-its-log-wait-for-the-claim-to-land
  ;; 追補 7(#233 の残債 a): claim が Conflict で流れた拍は、記録の 1 読み(RecordReadSince)も「has no session …」の判断の log も出さない —
  ;; 問いと解きは claim が着いた後(start-claimed)。次の拍で claim が着けば 1 読み・log 1 行・履歴からの再開(冪等)。
  (setv world (World "headless" True))
  (.put-row world.acp (message-row "m-1" CONVERSATION "operator" "合言葉は ひまわり" (- AT 9000)))
  (setv (get world.record-service.stored #(CONVERSATION "j-0#a1" 0)) {"producerSeq" 0 "at" (- AT 8000) "kind" "text" "text" "覚えました"})
  (.put-row world.acp (message-row "m-2" CONVERSATION "operator" "合言葉は何でしたか" (- AT 100)))
  (.put-row world.acp (bound-row "j-2" ["m-2"] "acct" None))
  (setv (get world.acp.conflict-once f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-2") 7)
  (.tick world 0)
  (assert (any (gfor line world.local.logs (in "claim of job j-2 did not land" line))) world.local.logs)
  (assert (= world.record-service.since-reads []) world.record-service.since-reads)
  (assert (not (any (gfor line world.local.logs (in "has no session to continue" line)))) world.local.logs)
  (assert (= world.sessions.launches []))
  (.tick world 1000)
  (assert (= (len world.sessions.launches) 1) world.local.logs)
  (assert (= world.record-service.since-reads [#(CONVERSATION 0 1 RECORD-RAW-EVENT-KINDS)]) world.record-service.since-reads)
  (assert (= (len (lfor line world.local.logs :if (in "has no session to continue" line) line)) 1) world.local.logs)
  (setv prompt (str-at (get world.sessions.launches 0) "prompt"))
  (assert (in "agent: 覚えました" prompt) prompt))


(deftest test-history-marks-erased-bodies-instead-of-inventing-them
  ;; 段 12 lane 12l(agora-redesign #383 粒 2): 本文が消された出来事(保存期間の係 retention か手の tombstone = storedEvent の
  ;; tombstonedAt)は空の本文に印 HISTORY-ERASED-MARK を付けて畳む — 「何も言わなかった」と「言ったが消えた」を agent が見分ける。
  ;; 消えていない出来事の綴りは変わらない(印なし)。
  (setv gone (event-of 1 "j-1#a1" 0 (+ AT 1000) "text" {"tombstonedAt" (+ AT 9000)}))
  (setv kept (event-of 2 "j-1#a1" 1 (+ AT 1100) "text" {"text" "残る"}))
  (setv gone-result (event-of 3 "j-1#a1" 2 (+ AT 1200) "tool_result" {"toolUseId" "t1" "tombstonedAt" (+ AT 9000)}))
  (assert (= gone.tombstoned-at (+ AT 9000)))
  (assert (is kept.tombstoned-at None))
  (setv gone-line (run (history-event-line gone)))
  (setv kept-line (run (history-event-line kept)))
  (setv result-line (run (history-event-line gone-result)))
  (assert (.endswith gone-line (+ "agent: " HISTORY-ERASED-MARK)) gone-line)
  (assert (.endswith kept-line "agent: 残る") kept-line)
  (assert (not-in HISTORY-ERASED-MARK kept-line) kept-line)
  (assert (.endswith result-line (+ "道具の結果: " HISTORY-ERASED-MARK)) result-line))
