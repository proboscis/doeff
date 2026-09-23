;;; 会話の記録の service への本文の二重書きの焦点の検(段 9f lane 9f-2 / 9f-4・agora-redesign #59・設計 §2.2 / §2.4)。
;;;
;;; 既知の形 = runner(agentd)の transactional outbox + claim check: 本文は切らずに spool(1 batch 1 file)へ耐久化してから
;;; service へ送り、受理(と 409)で消す・送れなければ残して周期の後に再送(冪等)。ACP の turn-record には**見出しだけ**
;;; (seq・at・kind・toolName・toolUseId・bytes・sha256・isError — 本文の欄は型に無い)と、受理の答えの写し
;;; (recordRef / recordedSeq)。ここで撃つのは
;;;   * 純関数: 本文は切らず見出しは本文を持たない(導く 1 点 headline-of-body・bytes / sha256 は service と同じ計算)・
;;;     採番(producerSeq = 見出しの seq・単調・batch の上限で分ける)・同じ拍の再送は同じ鍵と本文・attempt が変わると
;;;     stream が変わる・拾い直しの番と採番の下限・結末の語(= spool の扱い — 受理と 409 は消す / この batch だけの決まった断りは
;;;     隔離して次へ / 系の側は残して止める)・wire の応答の写し
;;;   * fake の handler で agentd を一周: ACP と service の両方に同じ seq が並び見出しの sha256 = service の本文の sha256・
;;;     1 entry ≤ TURN-ENTRY-MAX-BYTES・recordRef / recordedSeq が行に写る・送れない → spool に残る(recordedSeq は進まない)
;;;     → 周期の後の拍で送れて消える(進む)・409 は spool に残さず赤の計器・決まった断り(422)は隔離して同じ拍に後ろの batch を
;;;     送り手番に理由の condition(段 9f lane 9f-8)・弁 off は Record* を撃たない(見出しは同じ)
;;;   * spool の handler(tmp dir の実 file): 置く → 鍵の順に読める → 消える・壊れた file は消さずに名乗る・決まった断りは
;;;     given-up の置き場へ移り理由が隣に在る
;;;   * join の宣言の [record] → RECORD_SERVICE_URL と spool の置き場・settings の弁
;;;   * 参加の門(段 9f lane 9f-6): 宛先なし → 参加を断る(理由 = 宣言の置き場)・宛先あり → 参加・宛先あり届かない →
;;;     参加して spool(門は宣言の検で、届くかは検めない)
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import os)
(import tempfile)
(import typing [get-args])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  InFlightJob
  JOIN-SCHEMA
  JoinArgv
  JoinDeclaration
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  RECORD-BATCH-MAX-EVENTS
  RECORD-SPOOL-GIVEN-UP-DIR
  RECORD-SPOOL-DIR-ENV
  RECORD-URL-ENV
  RecordAppended
  RecordConflicted
  RecordEvent
  RecordPage
  RecordStreamKind
  RecordUnread
  RecordUnsent
  TURN-ENTRY-MAX-BYTES
  TURN-RECORD-KIND
  TurnEntryHeadline])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions record-body-bytes record-body-sha256])
(import doeff_agents.sessionhost.acp.handlers [
  HttpReply RECORD-SPOOL-SCHEMA RecordSpool decode-record-page decode-record-reply decode-spooled-batch
  record-append-body])
(import doeff_agents.sessionhost.acp.join [join-plan-of join-spec-of record-sink-of])
(import doeff_agents.sessionhost.acp.effects [JoinSpec])
(import doeff_agents.sessionhost.acp.judgment [
  entry-json-of
  headline-of-body
  record-append-word-of
  record-batches-of
  record-body-bytes-of
  record-ref-of
  record-stream-job-of
  recorded-mark-merged
  recovered-record-of
  text-body
  tool-result-body
  tool-use-body
  turn-record-marked-status
  turn-record-recorded-status])
(import doeff_agents.sessionhost.acp.runtime [AgentdPreflightError initial-state run-tick settings-from-env])


(setv NODE "mac-1")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv AT 1789000000000)
(setv STREAM "j-1#a1")


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record) "\n"))


(defn #^ str claude-events [#^ str session-id #^ str text]
  "claude の print mode(stream-json)の 1 手番の行(init・本文の delta・本文 + 道具・結果・result)。"
  (setv usage {"input_tokens" 3 "output_tokens" 7 "cache_creation_input_tokens" 1 "cache_read_input_tokens" 2})
  (.join "" [(stream-line {"type" "system" "subtype" "init" "session_id" session-id "model" "claude-opus-5"
                           "permissionMode" "bypassPermissions" "cwd" "/work" "tools" ["Bash" "Read"]})
             (stream-line {"type" "stream_event"
                           "event" {"type" "content_block_delta" "index" 0
                                    "delta" {"type" "text_delta" "text" text}}})
             (stream-line {"type" "assistant"
                           "message" {"id" "msg_1" "role" "assistant" "model" "claude-opus-5"
                                      "content" [{"type" "text" "text" text}
                                                 {"type" "tool_use" "id" "t1" "name" "Read"
                                                  "input" {"file_path" "/work/a.txt"}}]
                                      "usage" usage}})
             (stream-line {"type" "user"
                           "message" {"role" "user"
                                      "content" [{"type" "tool_result" "tool_use_id" "t1"
                                                  "content" "alpha" "is_error" False}]}})
             (stream-line {"type" "result" "subtype" "success" "is_error" False "usage" usage})]))


(defn #^ AcpRow row-of [#^ str namespace #^ str kind #^ str resource-id #^ dict spec #^ (| dict None) status]
  (AcpRow :namespace namespace :key f"{namespace}:{kind}:{resource-id}" :kind kind :resource-id resource-id
          :version "v1" :generation 1 :created-at-ms 500 :labels {} :payload {} :spec spec :status status))


(defn #^ AcpRow bound-job [#^ str job-id #^ list inputs]
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id
          {"subject" CONVERSATION "inputs" inputs
           "charter" {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                      "agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
          {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"} "conditions" []}))


(defn #^ InFlightJob job-of [#^ int attempt]
  "純関数の検の手番(memory の状態 — 欄は in-flight-job-of と同じ形)。"
  (InFlightJob :job-key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1" :job-namespace AGENT-JOB-NAMESPACE
               :job-id "j-1" :subject CONVERSATION :session-id "sid-1" :agent-type "claude" :node NODE
               :profile "personal" :model "claude-opus-5" :started-ms AT :turn-floor-ms AT :start-offset 0
               :transcript-offset 0 :delta-seq 0 :lease-id None :lease-kind None :lease-account None
               :lease-hold-ms None :capturing False :stream-gone False :last-frame-ms 0 :last-probe-ms 0
               :pending-conditions #() :materials-cover-the-turn True :record-attempt attempt))


(defclass RecordWorld []
  "backend = headless の器(events file が実況の正本)で agentd を一周させる世界。record = True なら二重書き on
   (FakeRecord を handler の列に足す — off の世界は FakeRecord を持たないので Record* を撃てば未処理で落ちる)。"
  (defn #^ None __init__ [self #^ bool record]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes"
                                        :backend-kind "headless" :stream-capability "events"
                                        :record-enabled record))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND NODE
                               {"name" NODE "labels" {} "capacity" 1 "streamCapability" "events"}
                               {"state" "joined"}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND "m-1" {"id" "m-1" "body" "first"} {"state" "inbox"}))
    (.put-row self.acp (bound-job "j-1" ["m-1"]))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-secret"}))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.record (FakeRecord))
    (setv self.state (initial-state)))

  (defn #^ list dispatchers [self]
    (setv base [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch])
    (if self.settings.record-enabled (+ [self.record.dispatch] base) base))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state (run-tick self.settings self.state (.dispatchers self)))
    None)

  (defn #^ str sid [self]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:j-1") status))
    (assert (isinstance status dict))
    (setv handle (get status "sessionHandle"))
    (assert (isinstance handle dict))
    (setv session-id (get handle "sessionId"))
    (assert (isinstance session-id str))
    session-id)

  (defn #^ None write-events [self #^ str text]
    (setv (get self.local.transcripts f"/events/{(.sid self)}.events.jsonl") text)
    None)

  (defn #^ dict record-status [self]
    (setv status (. (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1") status))
    (assert (isinstance status dict))
    status)

  (defn #^ int record-generation [self]
    "turn-record の行の generation(行の書き 1 回で 1 進む — 書きの回数の物差し)。"
    (. (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1") generation))

  (defn #^ list record-entries [self]
    (setv entries (.get (.record-status self) "entries" []))
    (assert (isinstance entries list))
    (list entries))

  (defn #^ list metrics-named [self #^ str name]
    (lfor line self.local.metrics :if (= (.get line "metric") name) line)))


;; ---------------------------------------------------------------------------
;; 純関数
;; ---------------------------------------------------------------------------

(deftest test-bodies-are-uncut-and-headlines-carry-the-digest-and-no-body
  ;; text: 本文は切らない・見出しは本文を持たず bytes / sha256(service の同一性の計算と同じ綴り)を持つ。
  (setv long-text (* "y" 70000))
  (setv body (run (text-body 8 AT long-text "claude-opus-5")))
  (assert (= body {"producerSeq" 8 "at" AT "kind" "text" "text" long-text "model" "claude-opus-5"}))
  (setv head (run (headline-of-body body)))
  (assert (isinstance head TurnEntryHeadline))
  (assert (= head.seq 8) "見出しの seq は本文の producerSeq(採番は 1 点)")
  (setv material (run (record-body-bytes-of body)))
  (assert (= material (.encode (json.dumps {"text" long-text} :sort-keys True :separators #("," ":") :ensure-ascii False) "utf-8"))
          "同一性の材料は本文の欄(effects.RECORD_BODY_FIELDS)のうち在るものだけ・compact・鍵 sort")
  (assert (= head.bytes (len material)))
  (assert (= head.sha256 (record-body-sha256 body)) "見出しの sha256 が service の計算と違う")
  (setv item (run (entry-json-of head)))
  (assert (= item {"seq" 8 "at" AT "kind" "text" "bytes" head.bytes "sha256" head.sha256}))
  (assert (<= (len (.encode (json.dumps item :ensure-ascii False :separators #("," ":")) "utf-8")) TURN-ENTRY-MAX-BYTES))
  ;; 道具: 本文は入力 / 出力そのもの(要約の欄を持たない)・見出しは toolName / toolUseId と同一性だけ。
  (setv long-input {"command" (* "x" 5000)})
  (setv use (run (tool-use-body 4 AT "t2" "Bash" long-input)))
  (assert (= (get use "input") long-input))
  (assert (not (in "summary" use)))
  (setv use-item (run (entry-json-of (run (headline-of-body use)))))
  (assert (= (set (.keys use-item)) #{"seq" "at" "kind" "toolName" "toolUseId" "bytes" "sha256"}))
  (assert (= (get use-item "sha256") (record-body-sha256 use)))
  (setv result (run (tool-result-body 5 AT "t2" [{"type" "text" "text" "boom"}] True)))
  (assert (= result {"producerSeq" 5 "at" AT "kind" "tool_result" "output" [{"type" "text" "text" "boom"}]
                     "toolUseId" "t2" "isError" True}))
  (setv result-item (run (entry-json-of (run (headline-of-body result)))))
  (assert (= (set (.keys result-item)) #{"seq" "at" "kind" "toolUseId" "bytes" "sha256" "isError"}))
  ;; 同じ本文は同じ sha256・違う本文は違う(冪等の判断と同じ物差し)。
  (assert (= (. (run (headline-of-body (run (text-body 9 (+ AT 1) long-text None)))) sha256) head.sha256))
  (assert (!= (. (run (headline-of-body (run (text-body 8 AT (+ long-text "!") None)))) sha256) head.sha256))
  ;; recordRef の綴りと stream id → job id の逆写像・recordedSeq は進む時だけ書く。
  (assert (= (run (record-ref-of CONVERSATION STREAM)) f"record:{CONVERSATION}/{STREAM}"))
  (assert (= (run (record-stream-job-of STREAM)) "j-1"))
  (assert (= (run (record-stream-job-of "aj-x#a12")) "aj-x"))
  (setv marked (run (turn-record-recorded-status {"state" "running" "entries" []} "record:c/s" 7)))
  (assert (= marked {"state" "running" "entries" [] "recordRef" "record:c/s" "recordedSeq" 7}))
  (assert (is (run (turn-record-recorded-status marked "record:c/s" 7)) None) "同じ値は書かない")
  (assert (is (run (turn-record-recorded-status marked "record:c/s" 3)) None) "後ろへ戻さない")
  (assert (= (get (run (turn-record-recorded-status marked "record:c/s2" 9)) "recordedSeq") 9)))


(deftest test-batches-carry-the-job-counter-as-producer-seq-and-rebuild-identically
  (setv job (job-of 1))
  (setv bodies (tuple (lfor seq [3 4 7] (run (text-body seq AT f"t{seq}" None)))))
  (setv batches (run (record-batches-of job bodies)))
  (assert (= (len batches) 1))
  (setv batch (get batches 0))
  (assert (= batch.conversation-id CONVERSATION))
  (assert (= batch.stream.stream-id STREAM))
  (assert (= batch.stream.kind "turn"))
  (assert (= batch.stream.started-at-ms AT))
  (assert (= (lfor event batch.events (get event "producerSeq")) [3 4 7]))
  (assert (= batch.events bodies) "本文をそのまま運ぶ(欄を足さない・切らない)")
  ;; 同じ拍の再送 = 同じ鍵と本文(純関数)
  (assert (= (run (record-batches-of job bodies)) batches))
  ;; batch の上限で分ける — 鍵の辞書順 = 送る順・producerSeq は通して単調
  (setv many (tuple (lfor seq (range (+ (* 2 RECORD-BATCH-MAX-EVENTS) 5)) (run (text-body seq AT "x" None)))))
  (setv split (run (record-batches-of job many)))
  (assert (= (lfor part split (len part.events)) [RECORD-BATCH-MAX-EVENTS RECORD-BATCH-MAX-EVENTS 5]))
  (setv keys (lfor part split part.spool-key))
  (assert (= keys (sorted keys)))
  (assert (= (len (set keys)) 3))
  (assert (= (lfor part split event part.events (get event "producerSeq")) (list (range (len many)))))
  (assert (= (run (record-batches-of job #())) #())))


(deftest test-attempt-changes-the-stream-and-recovery-derives-it-from-the-row
  (setv bodies #((run (text-body 0 AT "t" None))))
  (setv original (get (run (record-batches-of (job-of 1) bodies)) 0))
  (setv again (get (run (record-batches-of (job-of 2) bodies)) 0))
  (assert (= again.stream.stream-id "j-1#a2"))
  (assert (= again.stream.attempt 2))
  (assert (!= again.spool-key original.spool-key))
  (assert (= again.events original.events))
  ;; 拾い直し: 行が無ければ最初の受けと同じ・在れば generation + 1 と見出しの seq の続き
  (assert (= (run (recovered-record-of None)) #(1 0)))
  (setv row (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:j-1"
                    :kind TURN-RECORD-KIND :resource-id "j-1" :version "v1" :generation 5 :created-at-ms 500
                    :labels {} :payload {} :spec {}
                    :status {"state" "running"
                             "entries" (lfor seq [0 1 2 9] {"seq" seq "at" AT "kind" "text" "text" "t"})}))
  (assert (= (run (recovered-record-of row)) #(6 10))))


(deftest test-outcomes-name-the-count-and-decide-the-spool
  (setv ok (RecordAppended :highest-producer-seq 7 :appended #(6 7) :ignored #(5)))
  (setv conflicted (RecordConflicted :conflicts #({"producerSeq" 5})))
  (setv unreachable (RecordUnsent :status 0 :error "unreachable"))
  (setv malformed (RecordUnsent :status 400 :error "malformed"))
  (setv unstorable (RecordUnsent :status 422 :error "unstorable: SQLSTATE 22003: NumericValueOutOfRange: bigint out of range"))
  (setv forbidden (RecordUnsent :status 403 :error "forbidden: not a writer"))
  (setv throttled (RecordUnsent :status 429 :error "window-exceeded"))
  (setv unavailable (RecordUnsent :status 503 :error "store-unavailable"))
  (setv outcomes [ok conflicted unreachable malformed unstorable forbidden throttled unavailable])
  ;; 語 = 計器の outcome と spool の扱いの 1 点(段 9f lane 9f-8): この batch だけの決まった断り(400 / 422)は given-up(隔離して
  ;; 次へ)・札・窓・届かない・5xx は error(残して止める — 機体の設定か一時的)。
  (assert (= (lfor outcome outcomes (run (record-append-word-of outcome)))
             ["ok" "conflict" "error" "given-up" "given-up" "error" "error" "error"]))
  ;; wire の応答の写し(契約 appendAnswer / conflictAnswer / 届かない)
  (assert (= (decode-record-reply (HttpReply 200 {"recordSeq" {"6" 11 "7" 12} "highestProducerSeq" 7
                                                  "appended" [6 7] "ignored" [5]}))
             ok))
  (assert (= (decode-record-reply (HttpReply 409 {"error" "sha256-conflict" "conflicts" [{"producerSeq" 5}]}))
             conflicted))
  (assert (= (decode-record-reply (HttpReply 0 {"error" "unreachable: refused"}))
             (RecordUnsent :status 0 :error "unreachable: refused")))
  (setv refusal (decode-record-reply (HttpReply 403 {"error" "forbidden" "reason" "not a writer"})))
  (assert (isinstance refusal RecordUnsent))
  (assert (= refusal.error "forbidden: not a writer"))
  (assert (= (decode-record-reply (HttpReply 422 {"error" "unstorable" "reason" "SQLSTATE 22P05: UntranslatableCharacter"}))
             (RecordUnsent :status 422 :error "unstorable: SQLSTATE 22P05: UntranslatableCharacter")))
  ;; readEvents の応答の写し(契約 eventsAnswer / storedEvent — required の欠けた項は落とす・cursor.next は None も)
  (setv page (decode-record-page (HttpReply 200 {"cid" CONVERSATION
                                                 "events" [{"recordSeq" 3 "streamId" STREAM "streamKind" "turn" "producerSeq" 0
                                                            "at" AT "kind" "text" "text" "t" "bytes" 12 "sha256" "ab" "version" 1}
                                                           {"recordSeq" 4 "streamId" STREAM "streamKind" "turn" "producerSeq" 1
                                                            "at" AT "kind" "tool_use" "toolName" "Read" "toolUseId" "t1"
                                                            "input" {"file_path" "/a"} "bytes" 30 "sha256" "cd" "version" 1
                                                            "truncated" True}
                                                           {"recordSeq" 5 "streamId" STREAM}]
                                                 "cursor" {"direction" "before" "next" 3}})))
  (assert (isinstance page RecordPage))
  (assert (= page.next 3))
  (assert (= (len page.events) 2))
  (assert (= (get page.events 0) (RecordEvent :record-seq 3 :stream-id STREAM :stream-kind "turn" :producer-seq 0 :at AT
                                              :kind "text" :bytes 12 :sha256 "ab" :text "t")))
  (assert (= (. (get page.events 1) input) {"file_path" "/a"}))
  (assert (is (. (get page.events 1) truncated) True))
  (setv last-page (decode-record-page (HttpReply 200 {"cid" CONVERSATION "events" [] "cursor" {"direction" "before" "next" None}})))
  (assert (isinstance last-page RecordPage))
  (assert (is last-page.next None))
  (assert (= (decode-record-page (HttpReply 0 {"error" "unreachable: refused"})) (RecordUnread :status 0 :error "unreachable: refused")))
  (assert (isinstance (decode-record-page (HttpReply 200 {"cid" CONVERSATION})) RecordUnread))
  ;; appendRequest の綴り(契約 $defs.streamRef / eventIn)
  (setv body (record-append-body (get (run (record-batches-of (job-of 1) #((run (text-body 0 AT "t" None))))) 0)))
  (assert (= (get body "stream") {"kind" "turn" "id" STREAM "startedAt" AT "node" NODE "profile" "personal" "attempt" 1}))
  (assert (= (get body "events") [{"producerSeq" 0 "at" AT "kind" "text" "text" "t"}])))


(deftest test-every-word-of-the-stream-vocabulary-survives-the-read-back
  ;; card acp:kanban-issue:ki-9fc7d4bca4dc(法 ACP 575b1e の便 3 の穴): 読み手の語彙が**手で並べた arm**
  ;; だったので、記憶の語(memory)を型(effects.RecordStreamKind)と fake に足した便が decoder を見落とし、
  ;; append の直後の読み戻しが本番で 100% 空振りした(生きた pod の 90 分 = 畳み戻し 13 回・書けた行 0)。
  ;; fake は stream の id の前置きから自分で語を組むので、この穴は deftest からは構造上見えない。
  ;; ⇒ ここで撃つのは 1 語ずつではなく **語彙の全語**が往復すること。語を足した拍に母集団が自動で
  ;; 増える(手で並べた arm は増えない)。
  (setv kinds (get-args RecordStreamKind))
  (assert (in "memory" kinds) "語彙に記憶の語が無い")
  (for [kind kinds]
    (setv stream-id f"{kind}#x")
    ;; readEvents の応答の 1 項(記録の service から読み戻す側)
    (setv page (decode-record-page (HttpReply 200 {"cid" CONVERSATION
                                                   "events" [{"recordSeq" 3 "streamId" stream-id "streamKind" kind
                                                              "producerSeq" 0 "at" AT "kind" "text" "text" "t"
                                                              "bytes" 12 "sha256" "ab" "version" 1}]
                                                   "cursor" {"direction" "before" "next" None}})))
    (assert (isinstance page RecordPage) f"{kind} の頁が読めない")
    (assert (= (len page.events) 1) f"{kind} の項が落ちた — 語彙から外れて読まれている")
    (assert (= (. (get page.events 0) stream-kind) kind) f"{kind} が別の語になった")
    ;; spool の file の本文(送る前の outbox を読み戻す側 — 消費点はこの 2 つ)
    (setv batch (decode-spooled-batch {"schema" RECORD-SPOOL-SCHEMA "spoolKey" "k" "conversationId" CONVERSATION
                                       "request" {"stream" {"kind" kind "id" stream-id "startedAt" AT "attempt" 1}
                                                  "events" [{"producerSeq" 0 "at" AT "kind" "text" "text" "t"}]}}))
    (assert (is-not batch None) f"{kind} の batch が読めない — spool に落ちた本文が捨てられる")
    (assert (= (. batch stream kind) kind) f"{kind} の batch の語が変わった"))
  ;; 語彙の外は通さない(発明しない — 落とす側の腕は残る)
  (setv unknown (decode-record-page (HttpReply 200 {"cid" CONVERSATION
                                                    "events" [{"recordSeq" 3 "streamId" "x#1" "streamKind" "shout"
                                                               "producerSeq" 0 "at" AT "kind" "text" "bytes" 1 "sha256" "ab"}]
                                                    "cursor" {"direction" "before" "next" None}})))
  (assert (= (len unknown.events) 0) "語彙の外の語が通った")
  (assert (is (decode-spooled-batch {"schema" RECORD-SPOOL-SCHEMA "spoolKey" "k" "conversationId" CONVERSATION
                                     "request" {"stream" {"kind" "shout" "id" "x#1" "startedAt" AT "attempt" 1}
                                                "events" []}})
              None)
          "語彙の外の語の batch が通った"))


;; ---------------------------------------------------------------------------
;; fake の handler で agentd を一周
;; ---------------------------------------------------------------------------

(deftest test-dual-write-lands-headlines-in-acp-and-bodies-in-the-record-service
  (setv world (RecordWorld True))
  (setv plain (RecordWorld False))
  (for [each [world plain]]
    (.tick each 0)
    (.write-events each (claude-events (.sid each) "hello"))
    (.tick each 1000))
  (setv acp-entries (.record-entries world))
  (assert (= (lfor entry acp-entries (get entry "kind")) ["system" "text" "tool_use" "tool_result"]))
  (assert (= acp-entries (.record-entries plain)) "二重書きの有無で ACP の見出しが変わった")
  ;; 見出しは本文の欄を持たず、1 entry の compact JSON は上限の中。
  (for [entry acp-entries]
    (assert (<= (len (set.intersection (set (.keys entry)) #{"text" "summary" "input" "output" "model"})) 0)
            f"見出しに本文の欄が在る: {entry}")
    (assert (<= (len (.encode (json.dumps entry :ensure-ascii False :separators #("," ":")) "utf-8")) TURN-ENTRY-MAX-BYTES) entry))
  (assert (not-in "hello" (json.dumps acp-entries :ensure-ascii False)) "本文が ACP へ漏れた")
  (assert (= (get (get acp-entries 2) "toolName") "Read"))
  (assert (= (get (get acp-entries 2) "toolUseId") "t1"))
  (setv stored (.events-of world.record CONVERSATION STREAM))
  (assert (= (lfor event stored (get event "producerSeq")) (lfor entry acp-entries (get entry "seq")))
          "ACP の見出しの seq と service の producerSeq が違う")
  (assert (= (lfor event stored (get event "kind")) (lfor entry acp-entries (get entry "kind"))))
  ;; 見出しの bytes / sha256 = service へ送った本文の同一性(service と同じ計算)。
  (for [[entry event] (zip acp-entries stored)]
    (assert (= (get entry "sha256") (record-body-sha256 event)) f"sha256 が本文と一致しない: {entry}")
    (assert (= (get entry "bytes") (len (run (record-body-bytes-of event))))))
  (assert (= (get (get stored 2) "input") {"file_path" "/work/a.txt"}) "道具の本文は入力そのもの")
  (assert (= (get (get stored 3) "output") "alpha"))
  (assert (= world.record.spool {}) "受理された batch が spool に残っている")
  ;; 受理の答え(recordRef = 本文の在処・recordedSeq = 受理済みの最大 producerSeq)は、走っている手番では memory の刻印に
  ;; 在り、行を単独では書かない(次の追記か手番の終わりの書きに同乗 — card acp:kanban-issue:ki-c418e597017a 便 3)。
  (setv status (.record-status world))
  (setv last-seq (get (get acp-entries -1) "seq"))
  (assert (not-in "recordedSeq" status) "受理のためだけに行を書いた(status の全体の post-image が journal に 1 本増える)")
  (assert (= (. (get world.state.jobs 0) recorded-mark) #(f"record:{CONVERSATION}/{STREAM}" last-seq)) world.state.jobs)
  (assert (not-in "recordRef" (.record-status plain)) "弁 off の世界に recordRef が在る")
  (assert (= (lfor line (.metrics-named world "agentd_record_append_total") (get line "outcome")) ["ok"]))
  (assert (= (lfor line (.metrics-named world "agentd_record_spool_depth") (get line "depth")) [0]))
  (assert (= (lfor line (.metrics-named world "agentd_record_lag_seq") (get line "lag")) [0]))
  (assert (= plain.record.appends []) "弁 off の世界で Record* を撃った")
  (assert (= plain.record.spooled []))
  ;; 手番の終わりの書き(ended)は recordRef / recordedSeq を写す(置換しない)。
  (.finish-turn world.sessions (.sid world) (+ world.local.now-ms 100))
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.local.logs)
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (= (get (.record-status world) "recordedSeq") last-seq))
  (assert (= (get (.record-status world) "recordRef") f"record:{CONVERSATION}/{STREAM}")))


;; card acp:kanban-issue:ki-c418e597017a 便 3: 受理の刻印は行の次の書きに同乗する(単独の書きを撃たない)。
;; 実測 2026-09-21(本番の journal の最新 2,000 件): turn-record の連続する書き 745 対のうち 375 対が recordedSeq だけの差で、
;; journal の全 byte の 24.7% — 行の書きは status の全体(見出しの配列ごと)の post-image だから。
(deftest test-the-acceptance-mark-rides-the-next-row-write-and-never-costs-a-write-of-its-own
  (setv world (RecordWorld True))
  (.tick world 0)
  (setv first-burst (claude-events (.sid world) "hello"))
  (.write-events world first-burst)
  (.tick world 1000)
  (setv after-first (.record-generation world))
  (setv first-last (get (get (.record-entries world) -1) "seq"))
  (assert (= world.record.spool {}) "1 つ目の batch が受理されていない")
  (assert (not-in "recordedSeq" (.record-status world)))
  ;; 出来事の無い拍は行を 1 度も書かない(刻印を書くためだけの書きが無い)。
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (.record-generation world) after-first) "出来事の無い拍に行を書いた")
  ;; 次の出来事の追記の書き 1 回に、前の受理の刻印が同乗する。
  (.write-events world (+ first-burst
                          (stream-line {"type" "assistant"
                                        "message" {"id" "msg_2" "role" "assistant" "model" "claude-opus-5"
                                                   "content" [{"type" "text" "text" "again"}]
                                                   "usage" {"input_tokens" 1 "output_tokens" 1}}})))
  (.tick world 1000)
  (assert (= (.record-generation world) (+ after-first 1)) "追記と受理で行を 2 回書いた")
  (assert (= (get (.record-status world) "recordedSeq") first-last) "前の受理の刻印が追記の書きに乗っていない")
  (assert (= (get (.record-status world) "recordRef") f"record:{CONVERSATION}/{STREAM}"))
  (setv second-last (get (get (.record-entries world) -1) "seq"))
  (assert (> second-last first-last))
  (assert (= (. (get world.state.jobs 0) recorded-mark) #(f"record:{CONVERSATION}/{STREAM}" second-last))
          "2 つ目の受理が memory の刻印に重なっていない")
  ;; 手番の終わりの書きに最後の刻印が同乗する ⇒ 終わった手番の行は正確。
  (setv before-end (.record-generation world))
  (.finish-turn world.sessions (.sid world) (+ world.local.now-ms 100))
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.local.logs)
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (= (get (.record-status world) "recordedSeq") second-last))
  (assert (= (.record-generation world) (+ before-end 1)) "手番の終わりに行を 2 回以上書いた")
  ;; 純関数: 刻印は後ろへ戻さない・進まない刻印は status を変えない。
  (assert (= (run (recorded-mark-merged None "record:c/s" 4)) #("record:c/s" 4)))
  (assert (= (run (recorded-mark-merged #("record:c/s" 9) "record:c/s" 4)) #("record:c/s" 9)))
  (assert (= (run (recorded-mark-merged #("record:c/s" 4) "record:c/s" 9)) #("record:c/s" 9)))
  (setv base {"state" "running" "entries" [] "recordRef" "record:c/s" "recordedSeq" 7})
  (assert (is (run (turn-record-marked-status base None)) base))
  (assert (is (run (turn-record-marked-status base #("record:c/s" 7))) base))
  (assert (= (get (run (turn-record-marked-status base #("record:c/s" 8))) "recordedSeq") 8))
  (assert (= (get base "recordedSeq") 7) "元の status を書き換えた"))


(deftest test-turn-end-persists-cache-observation-and-record-acceptance-together
  ;; 同じ ended 書込みを使う二つの機能を合成しても、どちらの観測も失わない。
  (setv world (RecordWorld True))
  (.tick world 0)
  (.write-events world
    (stream-line {"type" "assistant" "timestamp" "1970-01-01T00:00:02Z"
                  "parent_tool_use_id" None
                  "message" {"id" "cache-main" "role" "assistant" "model" "claude-opus-5"
                             "content" [{"type" "text" "text" "ping"}]
                             "usage" {"input_tokens" 1 "output_tokens" 1
                                      "cache_read_input_tokens" 64000
                                      "cache_creation_input_tokens" 20
                                      "cache_creation" {"ephemeral_1h_input_tokens" 20
                                                        "ephemeral_5m_input_tokens" 0}}}}))
  (.tick world 1000)
  (setv accepted-seq (get (get (.record-entries world) -1) "seq"))
  (setv before-end (.record-generation world))
  (.finish-turn world.sessions (.sid world) (+ world.local.now-ms 100))
  (.tick world 1000)
  (setv status (.record-status world))
  (assert (= world.state.jobs #()) world.local.logs)
  (assert (= (get status "state") "ended"))
  (assert (= (get status "recordedSeq") accepted-seq))
  (assert (= (get status "recordRef") f"record:{CONVERSATION}/{STREAM}"))
  (assert (= (get status "cacheObservation")
             {"responseId" "cache-main" "at" 2000 "ttlSeconds" 3600
              "model" "claude-opus-5" "cacheRead" 64000 "cacheWrite" 20
              "requestStartedAtLowerBound" 1000}))
  (assert (= (.record-generation world) (+ before-end 1))
          "終了時の二つの観測は1回の書込みに同乗する"))


(deftest test-unsent-batch-stays-in-the-spool-and-lands-after-the-retry-period
  ;; 宛先あり届かない → 参加して spool(段 9f lane 9f-6): 参加の門は宣言の検で、届くかは検めない。
  (assert (. (settings-from-env {"DOEFF_AGENTD_NODE_NAME" NODE RECORD-URL-ENV "http://127.0.0.1:1"
                                  "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "personal"}) record-enabled)
          "届かない宛先でも宣言が在れば参加する(届かないのは spool が受ける)")
  (setv world (RecordWorld True))
  (.tick world 0)
  (setv world.record.unreachable True)
  (.write-events world (claude-events (.sid world) "hello"))
  (.tick world 1000)
  (assert (= (len world.record.spool) 1) "送れない batch が spool に無い")
  (assert (= (len (.record-entries world)) 4) "service が届かなくても ACP の見出しの追記は今日どおり")
  (assert (not-in "recordedSeq" (.record-status world)) "受理していないのに recordedSeq が在る")
  (assert (= (lfor line (.metrics-named world "agentd_record_append_total") (get line "outcome")) ["error"]))
  (assert (= (get (get (.metrics-named world "agentd_record_spool_depth") -1) "depth") 1))
  (assert (any (gfor line world.local.logs (in "kept in the spool" line))))
  ;; 届くようになっても backoff の周期までは撃たない(拍ごとに届かない service を叩かない)
  (setv world.record.unreachable False)
  (.tick world 1000)
  (assert (= (len world.record.appends) 1))
  (assert (= (len world.record.spool) 1))
  ;; 手番が終わって job が memory から消えても spool は残る。
  (.finish-turn world.sessions (.sid world) (+ world.local.now-ms 100))
  (.tick world 1000)
  (assert (= world.state.jobs #()) world.local.logs)
  (assert (= (get (.record-status world) "state") "ended"))
  (assert (not-in "recordedSeq" (.record-status world)))
  ;; 周期の後の拍で送れて消える(同じ鍵と本文の再送 — 冪等)。手番の終わりの後の受理も行に写る(鍵で読む)。
  (.tick world (int (* 1000 world.settings.record-retry-seconds)))
  (assert (= world.record.spool {}))
  (assert (= (len world.record.appends) 2))
  (assert (= (. (get world.record.appends 0) spool-key) (. (get world.record.appends 1) spool-key)))
  (assert (= (. (get world.record.appends 0) events) (. (get world.record.appends 1) events)))
  (assert (= (lfor event (.events-of world.record CONVERSATION STREAM) (get event "producerSeq"))
             (lfor entry (.record-entries world) (get entry "seq"))))
  (assert (= (get (.record-status world) "recordedSeq") (get (get (.record-entries world) -1) "seq"))
          "再送の受理が recordedSeq に写らない")
  (assert (= (get (.record-status world) "recordRef") f"record:{CONVERSATION}/{STREAM}"))
  (assert (= (get (.record-status world) "state") "ended") "受理の写しが state を変えた")
  (assert (= (get (get (.metrics-named world "agentd_record_spool_depth") -1) "depth") 0))
  (assert (is world.state.record-backoff-ms None)))


(deftest test-conflict-drops-the-spool-file-and-counts-red
  (setv world (RecordWorld True))
  (.tick world 0)
  ;; 同じ鍵(会話・stream・producerSeq)に違う本文が既に在る形 — 拾い直しの番を焼き損ねた時の 409。
  (for [seq (range 32)]
    (setv (get world.record.stored #(CONVERSATION STREAM seq))
          {"producerSeq" seq "at" AT "kind" "text" "text" "other"}))
  (.write-events world (claude-events (.sid world) "hello"))
  (.tick world 1000)
  (assert (= world.record.spool {}) "409 の batch を spool に残した(再送しても積めない)")
  (assert (= (lfor line (.metrics-named world "agentd_record_append_total") (get line "outcome")) ["conflict"]))
  (assert (any (gfor line world.local.logs (in "conflicted" line))))
  (assert (= (len (.record-entries world)) 4) "409 でも ACP の見出しの追記は今日どおり")
  (assert (not-in "recordedSeq" (.record-status world)) "409 は受理ではない(recordedSeq を書かない)")
  (assert (is world.state.record-backoff-ms None) "409 は送れなさではない(backoff しない)"))


(deftest test-a-batch-the-service-refuses-by-its-data-is-given-up-and-the-spool-moves-on
  ;; 段 9f lane 9f-8(実弾 lane 9f-7): 記録の service がこの batch の値を持てないと決めた断り(422 unstorable — 旧くは本文の NUL を
  ;; 503 と取り違えていた)を撃ち直し続けると、spool の先頭で同じ batch が詰まり後ろの本文も送れない。given-up = 隔離して理由を
  ;; 名乗り、同じ拍に後ろの batch を送る・手番の condition RecordUnavailable に理由・backoff しない。
  (setv world (RecordWorld True))
  (.tick world 0)
  (setv world.record.unreachable True)
  (.write-events world (claude-events (.sid world) "hello"))
  (.tick world 1000)
  (.write-events world (+ (claude-events (.sid world) "hello") (claude-events (.sid world) "again")))
  (.tick world (int (* 1000 world.settings.record-retry-seconds)))
  (assert (= (len world.record.spool) 2) (sorted world.record.spool))
  (setv [first-key second-key] (sorted world.record.spool))
  (setv world.record.unreachable False)
  (setv world.record.refusals [(RecordUnsent :status 422 :error "unstorable: SQLSTATE 22P05: UntranslatableCharacter: unsupported Unicode escape sequence")])
  (.tick world (int (* 1000 world.settings.record-retry-seconds)))
  (assert (= world.record.spool {}) "決まった断りの batch が先頭を塞いだ / 後ろの batch を送らなかった")
  (assert (= (list (.keys world.record.given-up)) [first-key]))
  (assert (in "422" (get world.record.given-up first-key)))
  (assert (= (lfor line (cut (.metrics-named world "agentd_record_append_total") -2 None) (get line "outcome")) ["given-up" "ok"]))
  (assert (= (. (get world.record.appends -1) spool-key) second-key))
  (assert (= (get (get (.metrics-named world "agentd_record_spool_depth") -1) "depth") 0))
  (assert (is world.state.record-backoff-ms None) "決まった断りは送れなさではない(backoff しない)")
  (assert (any (gfor line world.local.logs (in "moved to the given-up spool" line))))
  ;; 手番の condition RecordUnavailable に理由(Ended の書きに乗る)— 同じ型は 1 つだけ。
  (setv job (get world.state.jobs 0))
  (setv notes (lfor c job.pending-conditions :if (= (.get c "type") "RecordUnavailable") c))
  (assert (= (len notes) 1) job.pending-conditions)
  (setv note-reason (get (get notes 0) "reason"))
  (assert (and (isinstance note-reason str) (in first-key note-reason)) note-reason))

;; ---------------------------------------------------------------------------
;; spool の handler(実 file)と join の宣言
;; ---------------------------------------------------------------------------

(deftest test-spool-files-hold-one-batch-each-and-list-in-key-order
  (with [tmp (tempfile.TemporaryDirectory)]
    (setv directory (os.path.join tmp "record-spool"))
    (setv spool (RecordSpool directory))
    (assert (= (. (.listing spool) batches) #()) "置き場が無い spool は空")
    (setv job (job-of 1))
    (setv early (get (run (record-batches-of job (tuple (lfor seq [5 6] (run (text-body seq AT f"t{seq}" None)))))) 0))
    (setv late (get (run (record-batches-of job #((run (tool-use-body 9 AT "t9" "Read" {"file_path" "/a"}))))) 0))
    (.put spool late)
    (.put spool early)
    (setv listing (.listing spool))
    (assert (= listing.batches #(early late)) "鍵の順に読めない / 本文が往復で変わった")
    (assert (= listing.unreadable #()))
    (assert (= (sorted (os.listdir directory)) (sorted (lfor part [early late] (+ part.spool-key ".json"))))
            "1 batch 1 file でない / temp が残った")
    (.remove spool early.spool-key)
    (.remove spool early.spool-key)
    (assert (= (. (.listing spool) batches) #(late)))
    (with [handle (open (os.path.join directory "broken.json") "w")]
      (.write handle "{"))
    (setv damaged (.listing spool))
    (assert (= damaged.unreadable #("broken.json")))
    (assert (= damaged.batches #(late)))
    ;; 決まった断りの batch は送る順から外れて given-up の置き場へ(本文は消さない・理由が隣に在る — 段 9f lane 9f-8)。
    (.give-up spool late.spool-key "422: unstorable")
    (.give-up spool late.spool-key "422: unstorable")
    (assert (= (. (.listing spool) batches) #()) "隔離した batch がまだ送る順に在る")
    (setv given-up-dir (os.path.join directory RECORD-SPOOL-GIVEN-UP-DIR))
    (assert (= (sorted (os.listdir given-up-dir)) [(+ late.spool-key ".json") (+ late.spool-key ".reason.txt")]))
    (with [handle (open (os.path.join given-up-dir (+ late.spool-key ".json")) "rb")]
      (assert (= (decode-spooled-batch (json.loads (.read handle))) late) "隔離で本文が変わった"))
    (with [handle (open (os.path.join given-up-dir (+ late.spool-key ".reason.txt")) "r" :encoding "utf-8")]
      (assert (= (.read handle) "422: unstorable")))))


(deftest test-join-allow-metered-billing-is-a-closed-word-and-only-true-raises-the-flag
  ;; 従量課金の便 lane A(ADR-DOE-AGENTS-004 R9 改訂): 従量課金の binding kind を受けるかは
  ;; 宣言 file の [agentd].allow_metered_billing / flag --allow-metered-billing の
  ;; 1 点で、join は真のときだけ host の argv に値なしの旗を足す。
  ;; 既定は false(旗を立てない = 今日どおりの起動の形ちょうど)。
  ;; 綴りは閉語彙 true | false で、外は参加しない(黙って false にしない —
  ;; 黙った false は「許したはずなのに全部断られる」を無音で作る)。
  ;; ⚠ env は作らない: 課金の方針を env の 1 語で変えられる形は R10(d) が退けた形。
  (defn #^ dict tables-with [#^ (| str None) value]
    (setv agentd {"server" "http://acp:8868" "token_file" "/t/agentd.token"
                  ;; 段 11 lane 11u(#224): 置き場は集合の鍵 places(1 値の place は宣言に無い鍵として断られる)
                  "state_dir" "/s" "capacity" "2" "places" "company"})
    (when (is-not value None)
      (setv (get agentd "allow_metered_billing") value))
    {"schema" JOIN-SCHEMA "agentd" agentd
     "record" {"url" "http://agora-record.example:8874"}})
  (defn #^ JoinSpec spec-of [#^ dict tables #^ list items]
    (setv spec (run (join-spec-of (JoinArgv :items (tuple items)) (JoinDeclaration :tables tables) "/state")))
    (assert (isinstance spec JoinSpec))
    spec)
  ;; 既定(宣言なし)= 受けない・argv に旗は無い
  (setv bare (spec-of (tables-with None) []))
  (assert (is bare.allow-metered-billing False))
  (setv bare-plan (run (join-plan-of bare)))
  (assert (not (in "--allow-metered-billing" bare-plan.host-argv)))
  ;; 宣言 true = 受ける・argv の末尾の serve の直前に値なしの旗が立つ
  (setv allowed (spec-of (tables-with "true") []))
  (assert (is allowed.allow-metered-billing True))
  (setv plan (run (join-plan-of allowed)))
  (assert (in "--allow-metered-billing" plan.host-argv))
  (assert (= (get plan.host-argv -1) "serve"))
  (assert (= (get plan.host-argv -2) "--allow-metered-billing"))
  ;; 旗は argv だけ — env の束には同名の名が 1 つも現れない
  (for [[name _] plan.env]
    (assert (not-in "METERED" (.upper name)) name))
  ;; 宣言 false / 空 = 受けない
  (assert (is (. (spec-of (tables-with "false") []) allow-metered-billing) False))
  (assert (is (. (spec-of (tables-with "") []) allow-metered-billing) False))
  ;; flag が宣言に勝つ(両向き)
  (assert (is (. (spec-of (tables-with "false") ["--allow-metered-billing" "true"])
                 allow-metered-billing)
              True))
  (assert (is (. (spec-of (tables-with "true") ["--allow-metered-billing" "false"])
                 allow-metered-billing)
              False))
  ;; 語彙の外は参加しない(綴り違いを黙って false にしない)
  (for [bad ["yes" "1" "True" "on"]]
    (setv refused "")
    (try
      (spec-of (tables-with bad) [])
      (except [error ValueError]
        (setv refused (str error))))
    (assert (in "allow_metered_billing" refused) f"{bad} を断らなかった")
    (assert (in "true" refused))))


(deftest test-join-record-table-derives-the-record-env-and-settings-read-the-valve
  (setv tables {"schema" JOIN-SCHEMA
                "agentd" {"server" "http://acp:8868" "token_file" "/t/agentd.token" "state_dir" "/s"
                          "capacity" "1" "places" "personal"}
                "record" {"url" "http://agora-record.example:8874"}})
  (setv spec (run (join-spec-of (JoinArgv :items #()) (JoinDeclaration :tables tables) "/state")))
  (assert (= spec.record-url "http://agora-record.example:8874"))
  (setv env (dict (. (run (join-plan-of spec)) env)))
  (assert (= (get env RECORD-URL-ENV) "http://agora-record.example:8874"))
  (assert (= (get env RECORD-SPOOL-DIR-ENV) "/s/record-spool"))
  ;; flag が宣言に勝つ
  (setv flagged (run (join-spec-of (JoinArgv :items #("--record" "http://other:8874"))
                                   (JoinDeclaration :tables tables) "/state")))
  (assert (= flagged.record-url "http://other:8874"))
  ;; 空の宛先 = env に現れない = 弁 off
  (setv bare-tables (dict tables))
  (setv (get bare-tables "record") {"url" ""})
  (setv bare (run (join-plan-of (run (join-spec-of (JoinArgv :items #()) (JoinDeclaration :tables bare-tables) "/state")))))
  (assert (not (in RECORD-URL-ENV (dict bare.env))))
  (assert (not (in RECORD-SPOOL-DIR-ENV (dict bare.env))))
  ;; [record] に宛先以外の鍵は置けない(札は [agentd].token_file の再利用)
  (setv bad-tables (dict tables))
  (setv (get bad-tables "record") {"url" "http://r:8874" "token_file" "/x"})
  (setv refused "")
  (try
    (run (join-spec-of (JoinArgv :items #()) (JoinDeclaration :tables bad-tables) "/state"))
    (except [error ValueError]
      (setv refused (str error))))
  (assert (in "[record].token_file" refused) "宣言に無い鍵を断らなかった")
  ;; 宛先あり → 参加(settings の弁は on — 段 9f lane 9f-6 の門を通った形)
  (assert (. (settings-from-env {"DOEFF_AGENTD_NODE_NAME" "n" RECORD-URL-ENV "http://r:8874"
                                  "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "personal"}) record-enabled)))


(deftest test-join-without-a-record-sink-is-refused-with-the-reason
  ;; 段 9f lane 9f-6(agora-redesign #59): 本文の行き先(会話の記録の service の宛先)を持たない agentd は参加を断る。
  ;; 判断は join.record-sink-of の純関数 1 点(宣言 → 参加可否)で、理由は宣言の置き場を名指す。
  (defn #^ str refusal-of [#^ (| str None) url]
    (try
      (run (record-sink-of url))
      (except [error ValueError]
        (return (str error))))
    "")
  (for [absent [None "" "   "]]
    (setv reason (refusal-of absent))
    (assert reason f"宛先 {absent !r} を断らなかった")
    (assert (in "[record].url" reason) reason)
    (assert (in "--record" reason) reason)
    (assert (in RECORD-URL-ENV reason) reason)
    (assert (in "見出し" reason) "理由に『見出しだけを書いて本文を失う』が無い"))
  (assert (= (run (record-sink-of " http://r:8874 ")) "http://r:8874"))
  ;; env の読みの 1 点(settings-from-env)が同じ門を撃つ = serve --acp の経路も join の経路も同じ断り。
  (defn #^ str preflight-refusal-of [#^ dict env]
    (try
      (settings-from-env env)
      (except [error AgentdPreflightError]
        (return (str error))))
    "")
  (setv unset (preflight-refusal-of {"DOEFF_AGENTD_NODE_NAME" NODE "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "personal"}))
  (assert (in "refuses to join" unset) unset)
  (assert (in "[record].url" unset) unset)
  (assert (in RECORD-URL-ENV (preflight-refusal-of {"DOEFF_AGENTD_NODE_NAME" NODE RECORD-URL-ENV " "
                                                 "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "personal"})))
  ;; 宣言 file に [record] が無い → join の env の束に宛先が無い → 同じ門で断る(宣言 → env → 門の一周)。
  (setv tables {"schema" JOIN-SCHEMA
                "agentd" {"server" "http://acp:8868" "token_file" "/t/agentd.token" "state_dir" "/s"
                          "capacity" "1" "places" "personal"}})
  (setv plan (run (join-plan-of (run (join-spec-of (JoinArgv :items #()) (JoinDeclaration :tables tables) "/state")))))
  (setv env (dict plan.env))
  (assert (not-in RECORD-URL-ENV env))
  (setv (get env "DOEFF_AGENTD_NODE_NAME") NODE)
  (assert (in "[record].url" (preflight-refusal-of env)) "宣言 file に [record] が無い agentd が参加した")
  ;; flag --record が宣言 file を補えば参加する。
  (setv flagged (run (join-plan-of (run (join-spec-of (JoinArgv :items #("--record" "http://r:8874"))
                                                     (JoinDeclaration :tables tables) "/state")))))
  (setv env (dict flagged.env))
  (setv (get env "DOEFF_AGENTD_NODE_NAME") NODE)
  (assert (. (settings-from-env env flagged.host-argv) record-enabled)))



;; ---------------------------------------------------------------------------
;; 本文の欄の選択(card acp:kanban-issue:ki-651086f48560・依頼 lt-GH3YPP5NEY8HDGVB0AQY677RY6 の D5)
;; 本線(judgment.record-body-bytes-of)と fake(fake.record_body_bytes)は同じ定数 effects.RECORD_BODY_FIELDS を読む。
;; 両方が同じ定数を読むと「本線 = fake」は自明なので、式の誤りは性質と golden(中央と同じ入力・同じ hex)で捕まえる。
;; ---------------------------------------------------------------------------

;; 中央のテスト test-an-attachment-is-stored-with-its-image-and-read-by-its-producer-seq と同じ画像の base64。
(setv PNG-B64 "iVBORw0KGgo=")
;; 中央(agora-controllers services/record — 契約の入口 decode-event → prepare-event)が同じ入力に返す値(2026-09-23 に中央で出した)。
(setv ATTACHMENT-GOLDEN-SHA256 "0f820be1cbce81b7bbe408e8fa4bcca4b51ddea0d419e7bda5b87c580c23e08d")
(setv ATTACHMENT-GOLDEN-BYTES 23)
;; 契約 eventFields.head の欄と、それぞれの正当な値(見出しの欄 = 本文の digest の材料に入らない)。
(setv HEAD-SAMPLES {"toolName" "Read" "toolUseId" "toolu_1" "model" "claude-opus-5" "isError" True "mime" "image/jpeg" "name" "a.png"})
(setv EMPTY-BODY-BYTES (.encode (json.dumps {} :sort-keys True :separators #("," ":") :ensure-ascii False) "utf-8"))


(defn #^ dict attachment-body [#^ str data]
  {"producerSeq" 1 "at" AT "kind" "attachment" "mime" "image/png" "data" data})


(defn #^ dict digests-of [#^ dict body]
  ;; 本線と fake の両方の本文の綴り(見出しを導く点と fake の冪等の判断が使う 2 つの読み手)。
  {"本線" (run (record-body-bytes-of body)) "fake" (record-body-bytes body)})


(deftest test-an-attachment-only-body-is-not-the-empty-body
  ;; 性質 1: 添付だけの本文(data だけが本文の欄)の digest は空の辞書の digest ではない — 本線が添付を {} に潰すと
  ;; 中身の違う 2 通が同じ指紋になる(基準の断面で本線は 44136fa3… = 空の辞書を返していた・evidence/E1)。
  (for [[reader material] (.items (digests-of (attachment-body PNG-B64)))]
    (assert (!= material EMPTY-BODY-BYTES) f"{reader}: 添付だけの本文の綴りが空の辞書 {material !r}")))


(deftest test-attachments-with-different-data-have-different-digests
  ;; 性質 2: data の違う 2 通は違う digest。
  (setv one (digests-of (attachment-body PNG-B64)))
  (setv other (digests-of (attachment-body "aGk=")))
  (for [reader ["本線" "fake"]]
    (assert (!= (get one reader) (get other reader)) f"{reader}: data の違う 2 通が同じ綴り {(get one reader) !r}")))


(deftest test-head-fields-do-not-move-the-body-digest
  ;; 性質 3: 見出しの欄(契約 eventFields.head の 6 欄)をどれ足しても・全部足しても本文の digest は動かない。
  (for [base [(attachment-body PNG-B64) (run (text-body 3 AT "本文" None))]]
    (setv plain (digests-of base))
    (for [[field value] (+ (list (.items HEAD-SAMPLES)) [#("*" None)])]
      (setv headed (if (= field "*") (| base HEAD-SAMPLES) (| base {field value})))
      (assert (= (digests-of headed) plain) f"見出しの欄 {field} を足すと本文の digest が動いた: {base}"))))


(deftest test-the-attachment-digest-is-the-golden-of-the-central-service
  ;; golden: 中央のテストと同じ入力に中央と同じ hex(値の literal — どちらかの repo が式を変えれば自分の repo で赤)。
  (setv body (attachment-body PNG-B64))
  (setv head (run (headline-of-body body)))
  (assert (= #(head.sha256 head.bytes) #(ATTACHMENT-GOLDEN-SHA256 ATTACHMENT-GOLDEN-BYTES))
          f"本線の見出しの digest {head.sha256}/{head.bytes} ≠ 中央の golden {ATTACHMENT-GOLDEN-SHA256}/{ATTACHMENT-GOLDEN-BYTES}")
  (assert (= (record-body-sha256 body) ATTACHMENT-GOLDEN-SHA256) f"fake の digest {(record-body-sha256 body)} ≠ 中央の golden"))
