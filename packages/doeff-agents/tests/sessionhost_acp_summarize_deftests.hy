;;; agentd が charter.kind = summarize の job(会話の履歴の段階つき要約 — #55 案 D・operator 2026-09-16 "lets see if 1 will work")を担う腕の
;;; 焦点の検(段 12 lane 12j・agora-redesign #233・依頼者の裁定 2026-09-16・ADR-DOE-AGENTS-012 R37)。
;;;
;;; 形: この agentd(手番の終わりに文脈の大きさを測る)が ACP に agent-job(charter.kind = summarize・subject = 会話)を 1 つ書き、配置が
;;; 会話の profile の account で結び、その agentd が **会話の profile の札を借りて** claude を print モード(道具なし・session を残さない)で区間ごとに
;;; 1 回起こし、答えの本文を記録の service の stream(streamKind summary)へ、claim check を agora の kind summary の行へ書く。ここで撃つのは
;;;   * 受け: Running + sessionHandle{stream, summarize}・札を借りる(verify との違い)・**反例 = session を起こす形が赤**(launches == [])・
;;;     CommandStart の argv は sh の 1 行(pid → claude の print モード → rc・位置引数)・env に札と家・prompt の file に原文と残す / 落とすの規則
;;;   * 結末: 答えの JSON → 記録の service の出来事(kind summary)+ kind summary の行(spec の claim check・status の model / at / usage)・
;;;     Ended{regions}・札を返す
;;;   * 段階: 上限で区間を切り、要約済みの区間(既に在る行の to)の続きから・区間ごとに起こし直し・全区間で Ended
;;;   * 原文が無い = regions 0(条件なし・札を借りない)/ 答えが読めない = SummarizeOutputUnreadable + 札を返す
;;;   * 再起動: Running の行の sessionHandle.summarize から組み直し、process は起こし直さない / 取り下げ = CommandStop + 札 + Interrupted
;;;   * 純関数: 区間の切り方・答えの読み・prompt・job-kind-of
;;; fake の handler で同じ program(agentd.hy)を一周させる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  CHARTER-KIND-SUMMARIZE
  CHARTER-KIND-TURN
  CLAUDE-OAUTH-TOKEN-ENV
  CONDITION-INTERRUPTED
  CONDITION-SUMMARIZE-COMMAND-LOST
  CONDITION-SUMMARIZE-DEADLINE-EXCEEDED
  CONDITION-SUMMARIZE-OUTPUT-UNREADABLE
  CONDITION-SUMMARIZE-PLAN-INVALID
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  PHASE-WITHDRAWN
  RECORD-PAGE-MAX-LIMIT
  RECORD-RAW-EVENT-KINDS
  RecordEvent
  SUMMARY-EVENT-KIND
  SUMMARY-KIND
  SUMMARY-STREAM-PREFIX
  SummaryOutcome
  SummaryRegion])
(import doeff_agents.sessionhost.acp.fake [FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  job-kind-of
  summarize-argv-of
  summarize-output-of
  summarize-prompt-of
  summary-region-of])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "CA-20038667")
(setv HOME "/home/mac")
(setv RUNS "/state/acp-agentd/summary-runs")
(setv CID "c-01M28FFPKA9NDM1WCASFVC63W1")
(setv MODEL "claude-opus-5")
(setv ACCOUNT "acct")


(defn #^ AcpRow summarize-row [#^ str job-id #^ int until #^ str phase #^ (| int None) region-bytes]
  "配置が結んだ summarize の行(binding は手番と同じ 5 欄 — 会話の profile の account を持つ)。"
  (setv charter {"kind" CHARTER-KIND-SUMMARIZE "agent_type" "claude" "model" MODEL "until" until})
  (when (is-not region-bytes None)
    (setv (get charter "regionByteBudget") region-bytes))
  (AcpRow :namespace AGENT-JOB-NAMESPACE
          :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"
          :kind AGENT-JOB-KIND :resource-id job-id :version "v1" :generation 2 :created-at-ms 500
          :labels {} :payload {}
          :spec {"subject" CID "inputs" [] "charter" charter}
          :status {"phase" phase "binding" {"node" NODE "profile" "kento" "account" ACCOUNT "attempt" 1 "at" 0} "conditions" []}))


(defn #^ AcpRow summary-row [#^ int from-seq #^ int to-seq]
  "既に在る kind summary の行(要約済みの区間)。"
  (AcpRow :namespace AGORA-KINDS-NAMESPACE
          :key f"{AGORA-KINDS-NAMESPACE}:{SUMMARY-KIND}:sum-{CID}-{to-seq}"
          :kind SUMMARY-KIND :resource-id f"sum-{CID}-{to-seq}" :version "v1" :generation 1 :created-at-ms 100
          :labels {} :payload {}
          :spec {"conversationId" CID "from" from-seq "to" to-seq "recordRef" f"record:{CID}/summary#{from-seq}-{to-seq}" "bytes" 10 "sha256" (* "0" 64)}
          :status {"state" "current" "model" MODEL "at" 100}))


(defn #^ dict text-event [#^ int seq #^ str text]
  {"producerSeq" seq "at" (+ 1000 seq) "kind" "text" "text" text "model" MODEL})


(defn #^ str claude-answer [#^ str text]
  "claude の print モード(--output-format json)の答え(result の object)。本番の答えは本文 + usage + modelUsage で数千 byte(実弾 2026-09-16 16:04:
   9,207 byte)— 小さな file の読みの既定(256 字)では切れるので、検の答えも既定より長い形にする(usage の内訳を本番の欄で運ぶ)。"
  (json.dumps {"type" "result" "subtype" "success" "is_error" False "result" text
               "duration_ms" 59068 "duration_api_ms" 62784 "num_turns" 1 "total_cost_usd" 1.547655 "session_id" "7b48491f-599a-4611-871e-ef0000000000"
               "usage" {"input_tokens" 1200 "output_tokens" 80 "cache_creation_input_tokens" 0 "cache_read_input_tokens" 0
                        "output_tokens_details" {"thinking_tokens" 0} "server_tool_use" {"web_search_requests" 0 "web_fetch_requests" 0}
                        "service_tier" "standard" "cache_creation" {"ephemeral_1h_input_tokens" 0 "ephemeral_5m_input_tokens" 0}}
               ;; CLI の下読み(haiku・出力 数十 token)が先に並ぶ — 要約を書いた model は出力 token の最も多い方(実弾 2026-09-16 16:36)。
               "modelUsage" {"claude-haiku-4-5-20251001" {"inputTokens" 101075 "outputTokens" 17 "contextWindow" 200000}
                             MODEL {"inputTokens" 1200 "outputTokens" 80 "cacheReadInputTokens" 0 "cacheCreationInputTokens" 0
                                    "contextWindow" 1000000 "maxOutputTokens" 64000 "costUSD" 1.44 "provider" "firstParty"}}}
              :ensure-ascii False))


(defclass World []
  "fake の 5 handler(記録の service を含む)+ 値の宣言 + Node の行(summarize の腕を撃つ最小の世界)。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes" :home HOME :summarize-runs-dir RUNS
                                        :node-capacity 6 :custody-declared True :places #("company" "personal")
                                        :record-enabled True))
    (setv self.acp (FakeAcp :births {}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"
                               :kind NODE-KIND :resource-id NODE :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" NODE "labels" {} "places" ["company" "personal"] "capacity" 6 "streamCapability" "events"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {ACCOUNT "tok"}))
    (setv self.sessions (FakeSessions))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.record (FakeRecord))
    (setv self.state (initial-state)))

  (defn #^ None seed [self #^ int count]
    "会話の記録に手番 aj-1 の text の出来事を count 件(recordSeq は最初の読みで 1 から採番)。"
    (for [i (range count)]
      (setv (get self.record.stored #(CID "aj-1" i)) (text-event i f"raw-{i}: 決定と進みの本文 {i}")))
    None)

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch self.record.dispatch]))
    None)

  (defn #^ AcpRow job [self #^ str job-id]
    (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))

  (defn #^ dict status [self #^ str job-id]
    (setv row (.job self job-id))
    (if (isinstance row.status dict) row.status {}))

  (defn #^ list conditions [self #^ str job-id]
    (setv found (.get (.status self job-id) "conditions"))
    (if (isinstance found list) found []))

  (defn #^ (| dict None) handle [self #^ str job-id]
    (setv handle (.get (.status self job-id) "sessionHandle"))
    (if (isinstance handle dict) (.get handle "summarize") None))

  (defn #^ list summary-rows [self]
    (sorted (lfor row (.values self.acp.rows) :if (= row.kind SUMMARY-KIND) row) :key (fn [row] (get row.spec "to"))))

  (defn current [self #^ str job-id]
    (setv found None)
    (for [command self.state.summaries]
      (when (= command.job-id job-id)
        (setv found command)))
    found)

  (defn #^ None finish [self #^ str job-id #^ int rc #^ (| str None) answer]
    "claude の print モードが答え(out の file)と rc を書いて終わった。answer = None は out の file を書かない。"
    (setv command (.current self job-id))
    (assert (is-not command None) f"{job-id} は走っていない")
    (when (is-not answer None)
      (setv (get self.local.files command.out-path) answer))
    (setv (get self.local.files command.rc-path) f"{rc}\n")
    (when (is-not command.pid None)
      (.discard self.local.alive-pids command.pid))
    None))


;; ---------------------------------------------------------------------------
;; 受け: 札を借りて claude を print モードで起こす — session は無い(反例)
;; ---------------------------------------------------------------------------

(deftest test-summarize-job-borrows-the-conversation-lease-and-runs-claude-print-without-a-session
  ;; R37 (1)(2)(4): session を起こさない・会話の profile の札を借りる・Running + sessionHandle{stream, summarize}・
  ;; CommandStart の argv は sh の 1 行(pid → claude の print モード → rc・位置引数)・env に札と家・prompt に原文と規則。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (assert (= world.sessions.launches []) "summarize の job が session を起こした(反例)")
  (assert (= world.custody.borrowed [#("claude" ACCOUNT "summarize sj-1")]) world.custody.borrowed)
  (assert (= (get (.status world "sj-1") "phase") PHASE-RUNNING))
  (setv handle (.handle world "sj-1"))
  (assert (isinstance handle dict))
  (assert (= (get handle "conversationId") CID))
  (assert (= (get handle "from") 0))
  (assert (= (get handle "to") 5))
  (assert (= (get handle "until") 5))
  (assert (= (get handle "model") MODEL))
  (assert (= (get handle "account") ACCOUNT))
  (assert (= (get handle "regionsDone") 0))
  (setv stream (get (get (.status world "sj-1") "sessionHandle") "stream"))
  (assert (= stream {"owner" "agentd" "name" "sj-1"}) "running-on-me の鍵(stream.owner)が無い")
  ;; 読み: この会話の summary の行(下端)と、記録の service を前向きに原文の kind だけ。
  (assert (= world.acp.summary-reads [CID]))
  (assert (= (len world.record.since-reads) 1))
  (assert (= (get world.record.since-reads 0) #(CID 0 RECORD-PAGE-MAX-LIMIT RECORD-RAW-EVENT-KINDS)))
  ;; 起こし方
  (assert (= (len world.local.commands) 1))
  (setv argv (get world.local.commands 0))
  (assert (= (cut argv 0 2) #("/bin/sh" "-c")))
  (assert (in "-p --model" (get argv 2)))
  (assert (in "--output-format json" (get argv 2)))
  (assert (in "--tools \"\"" (get argv 2)))
  ;; file の名は job・区間・起こした時刻で一意(便 4 の実弾: 同じ job の id が GC の後に再び走ると前の走の rc を読んだ)。
  (setv started (get handle "startedAtMs"))
  (setv stem f"{RUNS}/sj-1-0-5-{started}")
  (assert (= (get argv 3) f"{stem}.pid"))
  (assert (= (get argv 4) "claude"))
  (assert (= (get argv 5) MODEL))
  (assert (= (get argv 6) f"{stem}.prompt.txt"))
  (assert (= (get argv 7) f"{stem}.out.json"))
  (assert (= (get argv 9) f"{stem}.rc"))
  (assert (= (get handle "rcPath") f"{stem}.rc"))
  (assert (= (get world.local.command-cwds 0) RUNS))
  (assert (in RUNS world.local.made-dirs) "結末の置き場を作っていない")
  (assert (= (get world.local.command-env-names 0) #(CLAUDE-OAUTH-TOKEN-ENV "CLAUDE_CONFIG_DIR")))
  (assert (= (get (get world.local.command-envs 0) CLAUDE-OAUTH-TOKEN-ENV) "tok"))
  (assert (= (get (get world.local.command-envs 0) "CLAUDE_CONFIG_DIR") "/homes/claude/acct"))
  ;; prompt: 原文(5 件・履歴からの再開と同じ綴り)と残す / 落とすの規則。
  (setv prompt (get world.local.files f"{stem}.prompt.txt"))
  (for [i (range 5)]
    (assert (in f"raw-{i}" prompt) f"prompt に原文 {i} が無い"))
  (assert (in "agent: raw-0" prompt) "原文の綴りが history-event-line と違う")
  (assert (in "残す情報" prompt))
  (assert (in "落とす情報" prompt))
  (assert (in f"recordSeq 0〜5" prompt))
  ;; memory
  (assert (= (len world.state.summaries) 1))
  (setv command (get world.state.summaries 0))
  (assert (= command.conversation-id CID))
  (assert (= command.lease-id "lease-1"))
  (assert (= command.source-events 5))
  ;; 2 拍目: まだ走っている — 起こし直さない
  (.tick world 1000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-RUNNING))
  (assert (= (len world.local.commands) 1) "走っている process を起こし直した"))


;; ---------------------------------------------------------------------------
;; 結末: 本文は記録の service・claim check は kind summary の行・Ended{regions}
;; ---------------------------------------------------------------------------

(deftest test-summarize-job-writes-the-summary-body-to-the-record-service-and-the-claim-check-row
  ;; R37 (6)(7): 答えの本文 → 記録の service の stream summary#0-5(kind summary・producerSeq 0)→ kind summary の行(spec の claim check・
  ;; status の current / model / at / usage)→ Ended{kind summarize, regions 1, lastTo 5}・札を返す・memory から外す。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (.finish world "sj-1" 0 (claude-answer "operator は要約の置き場を別の kind summary に決め、agentd が担う。"))
  (.tick world 2000)
  ;; 記録の service
  (setv events (.events-of world.record CID "summary#0-5"))
  (assert (= (len events) 1) events)
  (setv body (get events 0))
  (assert (= (get body "kind") SUMMARY-EVENT-KIND))
  (assert (= (get body "producerSeq") 0))
  (assert (= (get body "text") "operator は要約の置き場を別の kind summary に決め、agentd が担う。"))
  (assert (= (get body "model") MODEL))
  (setv batch (get world.record.appends -1))
  (assert (= batch.stream.kind "summary"))
  (assert (= batch.stream.stream-id "summary#0-5"))
  ;; kind summary の行
  (setv rows (.summary-rows world))
  (assert (= (len rows) 1) rows)
  (setv row (get rows 0))
  (assert (= row.resource-id f"sum-{CID}-5"))
  (assert (= (get row.spec "conversationId") CID))
  (assert (= (get row.spec "from") 0))
  (assert (= (get row.spec "to") 5))
  (assert (= (get row.spec "recordRef") f"record:{CID}/summary#0-5"))
  (assert (> (get row.spec "bytes") 0))
  (assert (= (len (get row.spec "sha256")) 64))
  (assert (= (get row.spec "sha256") (.sha256-of world.record CID "summary#0-5" 0)) "行の sha256 が service の計算と違う")
  (assert (= (get row.spec "sourceEvents") 5))
  (assert (> (get row.spec "sourceBytes") 0))
  (assert (= (get row.spec "agentJobId") "sj-1"))
  (assert (isinstance row.status dict))
  (assert (= (get row.status "state") "current"))
  (assert (= (get row.status "model") MODEL))
  (assert (= (get row.status "usage") {"input" 1200 "output" 80 "cacheWrite" 0 "cacheRead" 0}))
  ;; job の結末
  (setv status (.status world "sj-1"))
  (assert (= (get status "phase") PHASE-ENDED))
  (setv result (get status "result"))
  (assert (= (get result "kind") CHARTER-KIND-SUMMARIZE))
  (assert (= (get result "conversationId") CID))
  (assert (= (get result "regions") 1))
  (assert (= (get result "lastTo") 5))
  (assert (= (get result "until") 5))
  (assert (= (.conditions world "sj-1") []) "緑の結末に条件が付いた")
  (assert (= world.custody.revoked ["lease-1"]) "札を返していない")
  (assert (= world.state.summaries #()))
  (assert (= world.sessions.launches [])))


(deftest test-a-long-summary-answer-is-read-whole-not-cut-at-the-small-file-default
  ;; 実弾 2026-09-16 16:04(便 4 の 1 発目): claude の print モードの答え 9,207 byte を rc / pid 用の既定 256 字で読んで「non-JSON」と断り、要約が 1 つも
  ;; 書かれなかった。答えの読みは SUMMARY_ANSWER_MAX_CHARS の器で、本文が数 KB でも丸ごと行に載る。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (setv long-text (* "決定と進みと未解決の問いを 1 段落に。" 300))
  (assert (> (len (claude-answer long-text)) 256))
  (.finish world "sj-1" 0 (claude-answer long-text))
  (.tick world 2000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-ENDED))
  (assert (= (.conditions world "sj-1") []) (.conditions world "sj-1"))
  (setv rows (.summary-rows world))
  (assert (= (len rows) 1))
  (setv body (get (.events-of world.record CID "summary#0-5") 0))
  (assert (= (get body "text") long-text) "要約の本文が切れた")
  (assert (> (get (. (get rows 0) spec) "bytes") (len (.encode long-text "utf-8"))) "行の bytes が本文の欄の JSON の大きさでない"))


(deftest test-a-rerun-of-the-same-job-id-never-reads-the-previous-runs-result-files
  ;; 実弾 2026-09-16 16:23(便 4 の 2 発目): 同じ id の job(GC の後に create-only で再び書いた)が前の走の rc の file(0)を読んで即 Exited と
  ;; 判じ、まだ空の out を「答えが無い」と断った。file の名に起こした時刻が入るので、前の走の file は読まれない。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (.finish world "sj-1" 0 (claude-answer "1 回目の要約"))
  (.tick world 2000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-ENDED))
  (assert (= (len (.summary-rows world)) 1))
  ;; 前の走の file(rc = 0・out = 答え)が残ったまま、同じ id の job が再び Bound(行は GC で消えて作り直された = 同じ id の新しい行)。
  (setv stale (lfor [path text] (.items world.local.files) :if (.endswith path ".rc") path))
  (assert (= (len stale) 1) stale)
  (del (get world.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:sj-1"))
  (for [row (.summary-rows world)]
    (del (get world.acp.rows row.key)))
  (.tick world 60000)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (assert (= (get (.status world "sj-1") "phase") PHASE-RUNNING) (.status world "sj-1"))
  (assert (= (.conditions world "sj-1") []) "前の走の file を読んで閉じた")
  (setv command (.current world "sj-1"))
  (assert (is-not command None))
  (assert (not-in command.rc-path stale) "2 回目の走が前の走の rc の file を名指した")
  ;; 2 回目の答えが書かれてから終わる
  (.tick world 1000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-RUNNING))
  (.finish world "sj-1" 0 (claude-answer "2 回目の要約"))
  (.tick world 1000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-ENDED))
  (assert (= (.conditions world "sj-1") []))
  (assert (= (get (get (.events-of world.record CID "summary#0-5") 0) "text") "1 回目の要約") "冪等: 同じ区間の本文は最初の走のものが正"))


;; ---------------------------------------------------------------------------
;; 段階: 上限で区間を切り、要約済みの続きから、区間ごとに起こし直す
;; ---------------------------------------------------------------------------

(deftest test-summarize-job-advances-region-by-region-and-skips-what-is-already-summarized
  ;; R37 (3)(7): 既に在る summary の行(to = 2)の続き(from = 3)から・1 区間の上限(charter.regionByteBudget = 1 出来事分)で区間を切り・
  ;; 区間ごとに claude を print モードで起こし直し・札は区間ごとに借り直して返す・全区間で Ended{regions}。
  (setv world (World))
  (.seed world 6)
  (.put-row world.acp (summary-row 0 2))
  (.put-row world.acp (summarize-row "sj-2" 6 PHASE-BOUND 1))
  (.tick world 0)
  (setv first (.handle world "sj-2"))
  (assert (= (get first "from") 3) first)
  (assert (= (get first "to") 3) "上限 1 byte なら 1 出来事で区間が閉じる")
  (setv rounds 0)
  (while (and (= (get (.status world "sj-2") "phase") PHASE-RUNNING) (< rounds 10))
    (setv command (.current world "sj-2"))
    (.finish world "sj-2" 0 (claude-answer f"区間 {command.from-seq}〜{command.to-seq} の要約"))
    (.tick world 1000)
    (setv rounds (+ rounds 1)))
  (assert (= (get (.status world "sj-2") "phase") PHASE-ENDED))
  (setv result (get (.status world "sj-2") "result"))
  (assert (= (get result "regions") 4) result)
  (assert (= (get result "lastTo") 6))
  ;; 行: 既在の 1 + 新しい 4・from は前の to + 1・隙間なく 6 まで。
  (setv rows (.summary-rows world))
  (assert (= (lfor row rows #((get row.spec "from") (get row.spec "to"))) [#(0 2) #(3 3) #(4 4) #(5 5) #(6 6)]) (lfor row rows row.spec))
  (assert (= (len world.local.commands) 4) "区間ごとに 1 回起こす")
  (assert (= (len world.custody.borrowed) 4) "区間ごとに借り直す")
  (assert (= (len world.custody.revoked) 4) "区間ごとに返す")
  ;; 記録の service には区間ごとの stream
  (for [[from-seq to-seq] [#(3 3) #(4 4) #(5 5) #(6 6)]]
    (assert (= (len (.events-of world.record CID f"summary#{from-seq}-{to-seq}")) 1)))
  ;; 要約の出来事は原文の読みに混ざらない(kinds の絞り)— 次の job は 7 以降が無いので regions 0。
  (.put-row world.acp (summarize-row "sj-3" 6 PHASE-BOUND 1))
  (.tick world 1000)
  (assert (= (get (.status world "sj-3") "phase") PHASE-ENDED))
  (assert (= (get (get (.status world "sj-3") "result") "regions") 0)))


(deftest test-summarize-job-with-nothing-left-ends-with-zero-regions-and-no-lease
  ;; R37 (3): 全部が要約済み(行の to ≥ until)= 結末 regions 0・条件なし・札を借りない・process を起こさない(作り手の冪等の答え)。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summary-row 0 5))
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (assert (= (get (.status world "sj-1") "phase") PHASE-ENDED))
  (setv result (get (.status world "sj-1") "result"))
  (assert (= (get result "regions") 0) result)
  (assert (= (get result "from") 6))
  (assert (= (.conditions world "sj-1") []))
  (assert (= world.custody.borrowed []))
  (assert (= world.local.commands []))
  (assert (= world.sessions.launches []))
  (assert (= world.state.summaries #()))
  ;; 欄が読めない行(binding に profile / account が無い)は起こさず SummarizePlanInvalid。
  (setv bare (World))
  (.seed bare 2)
  (setv row (summarize-row "sj-9" 2 PHASE-BOUND None))
  (.put-row bare.acp (replace row :status {"phase" PHASE-BOUND "binding" {"node" NODE "attempt" 1 "at" 0} "conditions" []}))
  (.tick bare 0)
  (assert (= (get (.status bare "sj-9") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions bare "sj-9") -1) "type") CONDITION-SUMMARIZE-PLAN-INVALID))
  (assert (= bare.custody.borrowed [])))


;; ---------------------------------------------------------------------------
;; 答えが読めない・消えた・期限
;; ---------------------------------------------------------------------------

(deftest test-summarize-output-that-cannot-be-read-ends-with-a-condition-and-returns-the-lease
  ;; R37 (7): JSON でない答え / 誤りの答え / rc != 0 は条件 SummarizeOutputUnreadable で Ended・札を返す・行は書かない。
  (setv broken (World))
  (.seed broken 3)
  (.put-row broken.acp (summarize-row "sj-1" 3 PHASE-BOUND None))
  (.tick broken 0)
  (.finish broken "sj-1" 0 "this is not json")
  (.tick broken 10)
  (assert (= (get (.status broken "sj-1") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions broken "sj-1") -1) "type") CONDITION-SUMMARIZE-OUTPUT-UNREADABLE))
  (assert (in "non-JSON" (get (get (.conditions broken "sj-1") -1) "reason")))
  (assert (= (.summary-rows broken) []))
  (assert (= broken.custody.revoked ["lease-1"]))
  (assert (= (get (get (.status broken "sj-1") "result") "regions") 0))
  ;; 誤りの答え(is_error)
  (setv errored (World))
  (.seed errored 3)
  (.put-row errored.acp (summarize-row "sj-2" 3 PHASE-BOUND None))
  (.tick errored 0)
  (.finish errored "sj-2" 0 (json.dumps {"type" "result" "subtype" "error_during_execution" "is_error" True "result" "limit reached"}))
  (.tick errored 10)
  (assert (= (get (get (.conditions errored "sj-2") -1) "type") CONDITION-SUMMARIZE-OUTPUT-UNREADABLE))
  (assert (in "limit reached" (get (get (.conditions errored "sj-2") -1) "reason")))
  ;; rc != 0
  (setv red (World))
  (.seed red 3)
  (.put-row red.acp (summarize-row "sj-3" 3 PHASE-BOUND None))
  (.tick red 0)
  (.finish red "sj-3" 2 None)
  (.tick red 10)
  (assert (= (get (get (.conditions red "sj-3") -1) "type") CONDITION-SUMMARIZE-OUTPUT-UNREADABLE))
  (assert (in "rc=2" (get (get (.conditions red "sj-3") -1) "reason")))
  (assert (= red.custody.revoked ["lease-1"])))


(deftest test-summarize-process-that-vanishes-or-runs-past-the-deadline-is-closed-with-a-condition
  ;; R37 (7): 消えた(rc 無し・pid 死)= SummarizeCommandLost / 期限超過 = CommandStop + SummarizeDeadlineExceeded。どちらも札を返す。
  (setv gone (World))
  (.seed gone 3)
  (.put-row gone.acp (summarize-row "sj-1" 3 PHASE-BOUND None))
  (.tick gone 0)
  (setv pid (. (.current gone "sj-1") pid))
  (.discard gone.local.alive-pids pid)
  (.tick gone 1000)
  (assert (= (get (.status gone "sj-1") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions gone "sj-1") -1) "type") CONDITION-SUMMARIZE-COMMAND-LOST))
  (assert (= gone.custody.revoked ["lease-1"]))
  (setv slow (World))
  (setv slow.settings (replace slow.settings :summarize-deadline-seconds 10))
  (.seed slow 3)
  (.put-row slow.acp (summarize-row "sj-2" 3 PHASE-BOUND None))
  (.tick slow 0)
  (setv slow-pid (. (.current slow "sj-2") pid))
  (.tick slow 20000)
  (assert (= (get (.status slow "sj-2") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions slow "sj-2") -1) "type") CONDITION-SUMMARIZE-DEADLINE-EXCEEDED))
  (assert (in slow-pid slow.local.stopped-pids))
  (assert (= slow.custody.revoked ["lease-1"])))


;; ---------------------------------------------------------------------------
;; 再起動・取り下げ
;; ---------------------------------------------------------------------------

(deftest test-running-summarize-is-recovered-from-its-row-after-a-restart
  ;; R37 (7)(R7): memory を失っても Running の行の sessionHandle.summarize と pid の file から組み直し、process は起こし直さない・
  ;; 札は借り直さない(次の区間で借る)。結末が付けば同じく行を書いて Ended。
  (setv world (World))
  (.seed world 5)
  (.put-row world.acp (summarize-row "sj-1" 5 PHASE-BOUND None))
  (.tick world 0)
  (setv pid (. (.current world "sj-1") pid))
  (assert (is-not pid None))
  (setv world.state (initial-state))
  (.tick world 1000)
  (assert (= (len world.local.commands) 1) "拾い直しで process を起こし直した")
  (assert (= (len world.custody.borrowed) 1) "拾い直しで札を借り直した")
  (assert (= (len world.state.summaries) 1))
  (assert (= (. (get world.state.summaries 0) pid) pid) "pid の file から pid を読み戻していない")
  (assert (= (. (get world.state.summaries 0) to-seq) 5))
  (assert (= (get (.status world "sj-1") "phase") PHASE-RUNNING))
  (assert (any (gfor line world.local.logs (in "recovered running summarize job sj-1" line))))
  (.finish world "sj-1" 0 (claude-answer "再起動の後の要約"))
  (.tick world 1000)
  (assert (= (get (.status world "sj-1") "phase") PHASE-ENDED))
  (assert (= (get (get (.status world "sj-1") "result") "regions") 1))
  (assert (= (len (.summary-rows world)) 1))
  ;; sessionHandle.summarize が無い Running の行は組み直せない — 結末なしで SummarizeCommandLost
  (setv bare (World))
  (setv row (summarize-row "sj-9" 5 PHASE-RUNNING None))
  (setv status (dict row.status))
  (setv (get status "sessionHandle") {"stream" {"owner" "agentd" "name" "sj-9"}})
  (.put-row bare.acp (replace row :status status))
  (.tick bare 0)
  (assert (= (get (.status bare "sj-9") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions bare "sj-9") -1) "type") CONDITION-SUMMARIZE-COMMAND-LOST)))


(deftest test-withdrawn-summarize-stops-the-process-returns-the-lease-and-marks-interrupted
  ;; R37 (7): 取り下げ(Withdrawn — 書き手は作った側)= CommandStop + 札を返す + Interrupted(phase は書かない)。
  (setv world (World))
  (.seed world 3)
  (.put-row world.acp (summarize-row "sj-1" 3 PHASE-BOUND None))
  (.tick world 0)
  (setv pid (. (.current world "sj-1") pid))
  (setv row (.job world "sj-1"))
  (setv status (dict row.status))
  (setv (get status "phase") PHASE-WITHDRAWN)
  (.put-row world.acp (replace row :status status))
  (.tick world 1000)
  (assert (in pid world.local.stopped-pids))
  (assert (= world.custody.revoked ["lease-1"]))
  (assert (= (get (.status world "sj-1") "phase") PHASE-WITHDRAWN) "取り下げの phase を書き換えた")
  (assert (= (get (get (.conditions world "sj-1") -1) "type") CONDITION-INTERRUPTED))
  (assert (= world.state.summaries #())))


;; ---------------------------------------------------------------------------
;; 純関数
;; ---------------------------------------------------------------------------

(defn #^ RecordEvent event-of [#^ int seq #^ str kind #^ int size]
  (RecordEvent :record-seq seq :stream-id "aj-1" :stream-kind "turn" :producer-seq seq :at (+ 1000 seq) :kind kind
               :bytes size :sha256 (* "0" 64) :text (if (= kind "text") "t" None)))


(deftest test-summarize-judgments-are-pure
  ;; job-kind-of の 3 語目・区間の切り方(上限・下端・上端・原文の kind)・答えの読み・prompt・起こし方の位置引数。
  (assert (= (run (job-kind-of (summarize-row "s" 3 PHASE-BOUND None))) CHARTER-KIND-SUMMARIZE))
  (setv events (tuple [(event-of 1 "text" 40) (event-of 2 "frame" 500) (event-of 3 "tool_result" 70) (event-of 4 "text" 40) (event-of 5 "text" 40)]))
  ;; 上限 100: 1(40)+3(70) = 110 ≥ 100 で閉じる → [1, 3](frame は原文でない)
  (setv region (run (summary-region-of events 0 5 100)))
  (assert (isinstance region SummaryRegion))
  (assert (= #(region.from-seq region.to-seq) #(0 3)) region)
  (assert (= (lfor event region.events event.record-seq) [1 3]))
  (assert (= region.source-bytes 110))
  ;; 下端 4・上端 5 → [4, 5]
  (setv tail (run (summary-region-of events 4 5 1000)))
  (assert (= #(tail.from-seq tail.to-seq) #(4 5)))
  ;; 上端 0 / 下端が上端を越える / 原文が無い(frame だけ)→ None
  (assert (is (run (summary-region-of events 0 0 100)) None))
  (assert (is (run (summary-region-of events 6 5 100)) None))
  (assert (is (run (summary-region-of (tuple [(event-of 1 "frame" 5)]) 0 5 100)) None))
  ;; 1 つで上限を超える出来事も 1 区間
  (setv big (run (summary-region-of (tuple [(event-of 1 "text" 5000)]) 0 5 100)))
  (assert (= #(big.from-seq big.to-seq) #(0 1)))
  ;; 答えの読み
  (setv good (run (summarize-output-of (claude-answer "要約。"))))
  (assert (isinstance good SummaryOutcome))
  (assert (= good.text "要約。"))
  (assert (= good.model MODEL) "要約を書いた model は出力 token の最も多い model(下読みの haiku ではない)")
  (assert (= good.usage {"input" 1200 "output" 80 "cacheWrite" 0 "cacheRead" 0}))
  (assert (isinstance (run (summarize-output-of None)) str))
  (assert (isinstance (run (summarize-output-of "")) str))
  (assert (isinstance (run (summarize-output-of "[1, 2]")) str))
  (assert (isinstance (run (summarize-output-of (json.dumps {"type" "result" "subtype" "success" "result" "   "}))) str))
  (assert (isinstance (run (summarize-output-of (json.dumps {"type" "result" "subtype" "error_max_turns" "result" "x"}))) str))
  ;; usage が欠けた答えは本文だけ(usage None)
  (setv thin (run (summarize-output-of (json.dumps {"type" "result" "subtype" "success" "result" "本文" "usage" {"input_tokens" 1}}))))
  (assert (= thin.text "本文"))
  (assert (is thin.usage None))
  ;; prompt は原文をそのまま含み、規則の語を名乗る
  (setv prompt (run (summarize-prompt-of CID region "line-a\nline-b")))
  (assert (in "line-a\nline-b" prompt))
  (assert (in "残す情報" prompt))
  (assert (in "recordSeq 0〜3" prompt))
  ;; 起こし方は位置引数(文字列に path を埋めない)
  (setv argv (run (summarize-argv-of "claude" MODEL {"prompt" "/p" "out" "/o" "log" "/l" "rc" "/r" "pid" "/d"})))
  (assert (= (cut argv 3 None) #("/d" "claude" MODEL "/p" "/o" "/l" "/r")))
  (assert (not-in "/p" (get argv 2))))
