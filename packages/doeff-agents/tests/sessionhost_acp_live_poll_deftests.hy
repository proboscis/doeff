;;; 実況の push の周期の焦点の検(段 8 lane 4aa・agora-redesign #63)。
;;;
;;; headless の器の実況は events file の行の増分で、file の追記は合図を持たない —— agentd が offset から
;;; 読んで中継へ押す**拍の周期がそのまま push の間隔**になる。購読者が居る間は events の周期(≤ 50 ms —
;;; AgentdSettings.events_poll_seconds)、居なければ transcript の周期(記録の追記だけ)。tui の器(frames)の
;;; capture の周期(frame_interval_seconds・2〜5 Hz)は変えない。判断は judgment.wait-seconds-for の 1 点。
;;; HTTP も subprocess も無い(fake の handler で agentd を一周)—— 最後の 1 つ
;;; (test-agentd-acp-http-reuses-one-connection-per-host)だけは loopback の HTTP server を立てる:
;;; 接続を使い回すかは socket の accept の数でしか見えないので、fake では撃てない。
;;;
;;; card acp:kanban-issue:ki-6eb745f6d528(実況の粒を拍の直列から外す)の焦点の検もここに居る: 拍の中で
;;; **全 job の push がどの store への書きよりも先**・frame の at は**その job の**時計読み・spool の flush は
;;; **拍あたりの上限**を持つ。手番 20 本の agentd の実測(2026-09-19)は 拍の周期 p50 1.243 秒・at→着 p50 0.554 秒・
;;; 同じ拍の job 間のずれ p50 0.453 秒で、手番 1 本では at→着 p50 0.17 秒 —— 粒は同時に持つ手番の数に比例して粗くなる。

(require doeff-hy.macros [defk deftest <-])

(import json)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import doeff [run])
(import doeff_vm [PyVM])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpGetRow
  AcpRow
  AcpStreamPush
  AgentdSettings
  InFlightJob
  LIST-MODE-NONE
  MESSAGE-KIND
  METRIC-TICK-MS
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  Pushed
  RECORD-STREAM-TURN
  RecordBatch
  RecordStream
  Refused
  STREAM-SOURCE-HEADER
  TICK-ARMS
  TICK-LINE-FIELDS
  TURN-RECORD-KIND
  WatchAdvance])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeRecord FakeSessions])
(import doeff_agents.sessionhost.acp.handlers [AcpHttp])
(import doeff_agents.sessionhost.acp.judgment [list-mode-for wait-seconds-for])
(import doeff_agents.sessionhost.acp.runtime [initial-state install run-tick])
(import dataclasses [replace])


(setv NODE "mac-1")
;;; 会話は手番ごとに別(同じ機体が同時に持つ手番は別々の会話 — 実運転と同じ形)。
(setv CONVERSATION-PREFIX "c-01ARZ3NDEKTSV4RRFFQ69G5FA")
(setv CONVERSATION (+ CONVERSATION-PREFIX "V"))


(defn #^ str subject-of [#^ int index]
  "手番 index の会話の id(ULID の末尾 1 字だけ変える — 綴りは 26 字のまま)。"
  (+ CONVERSATION-PREFIX (get "VWXYZ" index)))


(defn #^ str stream-line [#^ dict record]
  (+ (json.dumps record) "\n"))


(defn #^ AcpRow row-of [#^ str namespace #^ str kind #^ str resource-id #^ dict spec #^ (| dict None) status]
  (AcpRow :namespace namespace :key f"{namespace}:{kind}:{resource-id}" :kind kind :resource-id resource-id
          :version "v1" :generation 1 :created-at-ms 500 :labels {} :payload {} :spec spec :status status))


(defn #^ str init-line [#^ str session-id]
  (stream-line {"type" "system" "subtype" "init" "session_id" session-id "model" "claude-opus-5"}))


(defn #^ str delta-line [#^ str text]
  "assistant の message の行 = 実況の frame(text)と記録の出来事(entry)の両方になる行。"
  (stream-line {"type" "assistant"
                "message" {"id" (+ "msg-" text) "role" "assistant" "model" "claude-opus-5"
                           "content" [{"type" "text" "text" text}]
                           "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0
                                    "cache_read_input_tokens" 0}}}))


(defn #^ AcpRow bound-job [#^ str job-id #^ list inputs [subject CONVERSATION]]
  (row-of AGENT-JOB-NAMESPACE AGENT-JOB-KIND job-id
          {"subject" subject "inputs" inputs
           "charter" {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                      "agent_type" "claude" "work_dir" "/work" "prompt" "start" "model" "claude-opus-5"}}
          {"phase" PHASE-BOUND "binding" {"node" NODE "profile" "personal" "account" "acct"} "conditions" []}))


(defclass World []
  "backend = headless の器(events file が実況の正本)で agentd を一周させる最小の世界。

  job-ids に 2 つ以上渡すと、同じ機体が同時に持つ手番(それぞれ別の会話)の拍を組める —— card
  acp:kanban-issue:ki-6eb745f6d528 の根はここで、粒は同時に持つ手番の数に比例して粗くなる。
  clock-step-ms > 0 は時計が読むたびに進む世界(どの読みがどの frame の at になったかを検が区別できる)。
  record = True は会話の記録の service への二重書き on(FakeRecord を handler の列に足す)。"
  (defn #^ None __init__ [self [job-ids #("j-1")] [clock-step-ms 0] [record False] [flush-max 0]]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes"
                                        :backend-kind "headless" :stream-capability "events"
                                        :record-url (when record "http://record.test:8874")))
    (when flush-max
      (setv self.settings (replace self.settings :record-flush-max-batches flush-max)))
    (setv self.job-ids (tuple job-ids))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE NODE-KIND NODE
                               {"name" NODE "labels" {} "capacity" (len self.job-ids)
                                "streamCapability" "events"}
                               {"state" "joined"}))
    (for [[index job-id] (enumerate self.job-ids)]
      (setv message-id f"m-{(+ index 1)}")
      (.put-row self.acp (row-of AGORA-KINDS-NAMESPACE MESSAGE-KIND message-id
                                 {"id" message-id "body" "first"} {"state" "inbox"}))
      (.put-row self.acp (bound-job job-id [message-id] (subject-of index))))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-secret"}))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))
    ;; 時計の読みは書き・押しと同じ 1 本の列に並べる(押しの直前の読みを検が引ける)。
    (setv self.local (FakeLocal :now-ms 1000 :clock-step-ms clock-step-ms :trace self.acp.trace))
    (setv self.record (FakeRecord))
    (setv self.state (initial-state)))

  (defn #^ list dispatchers [self]
    (setv base [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch])
    (if self.settings.record-enabled (+ [self.record.dispatch] base) base))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state (run-tick self.settings self.state (.dispatchers self)))
    None)

  (defn #^ str sid [self [job-id "j-1"]]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}") status))
    (assert (isinstance status dict))
    (setv handle (get status "sessionHandle"))
    (assert (isinstance handle dict))
    (setv session-id (get handle "sessionId"))
    (assert (isinstance session-id str))
    session-id)

  (defn #^ None write-events [self #^ str job-id #^ str text]
    (setv (get self.local.transcripts f"/events/{(.sid self job-id)}.events.jsonl") text)
    None)

  (defn #^ float last-wait [self]
    (get self.acp.waits -1))

  (defn #^ dict record-status [self [job-id "j-1"]]
    (setv status (. (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{TURN-RECORD-KIND}:{job-id}") status))
    (assert (isinstance status dict))
    status)

  (defn #^ str job-phase [self [job-id "j-1"]]
    (setv status (. (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}") status))
    (assert (isinstance status dict))
    (setv phase (get status "phase"))
    (assert (isinstance phase str))
    phase)

  (defn #^ list record-entries [self [job-id "j-1"]]
    (setv entries (.get (.record-status self job-id) "entries" []))
    (assert (isinstance entries list))
    (list entries))

  (defn #^ list record-writes [self]
    (lfor [key status] self.acp.writes :if (in f":{TURN-RECORD-KIND}:" key) status)))


(deftest test-events-are-polled-every-50ms-while-someone-watches
  ;; 起動の拍: job が無いので idle の上限。受けの後: 購読者 0 → 記録の周期(1 s)。購読者が現れて frame を押した拍の後:
  ;; events の周期(50 ms)。購読者が消えた拍の後: 記録の周期へ戻る。
  (setv world (World))
  (.tick world 0)
  (assert (= (.last-wait world) world.settings.idle-wait-seconds) world.acp.waits)
  (setv sid (.sid world))
  (.tick world 1000)
  (assert (= (.last-wait world) world.settings.transcript-poll-seconds) world.acp.waits)
  ;; 購読者が現れる(画面が attach した)— 次の push(本文の delta の frame)が 1 を返し、その次の拍から events の周期。
  ;; ⚠ headless の capturing は push の答え(購読者の数)でしか動かない — frame にならない行(init だけ)では動かない。
  (setv (get world.acp.subscribers sid) 1)
  (setv part-1 (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
                  (stream-line {"type" "stream_event"
                                "event" {"type" "content_block_delta" "index" 0
                                         "delta" {"type" "text_delta" "text" "hel"}}})))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") part-1)
  (.tick world 1000)
  (assert (. (get world.state.jobs 0) capturing))
  (.tick world 50)
  (assert (= (.last-wait world) world.settings.events-poll-seconds) world.acp.waits)
  (assert (<= world.settings.events-poll-seconds 0.05) "events の周期は 50 ms の中(出来事ごとの push に最も近い有界の拍)")
  ;; 購読者が消える — 次の push が 0 を返し、その次の拍から記録の周期。
  (setv (get world.acp.subscribers sid) 0)
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
        (+ part-1
           (stream-line {"type" "stream_event"
                         "event" {"type" "content_block_delta" "index" 0
                                  "delta" {"type" "text_delta" "text" "lo"}}})))
  (.tick world 50)
  (assert (not (. (get world.state.jobs 0) capturing)))
  (.tick world 50)
  (assert (= (.last-wait world) world.settings.transcript-poll-seconds) world.acp.waits))


(deftest test-frames-keep-the-capture-interval-and-events-take-the-poll
  ;; 判断の 1 点: capturing の job が在る時、tui(frames)は capture の間隔(2〜5 Hz)のまま・headless(events)は events の
  ;; 周期。capturing の job が無ければ器の種類に依らず記録の周期 / idle。
  (setv job (InFlightJob :job-key "k" :job-namespace AGENT-JOB-NAMESPACE :job-id "j" :subject CONVERSATION :session-id "s"
                         :agent-type "claude" :node NODE :profile "p" :model "m" :started-ms 0 :turn-floor-ms 0
                         :start-offset 0 :transcript-offset 0 :delta-seq 0 :lease-id None :lease-kind None
                         :lease-account None :lease-hold-ms None :capturing True :stream-gone False :last-frame-ms 0
                         :last-probe-ms 0 :pending-conditions #() :materials-cover-the-turn True))
  (setv state (replace (initial-state) :jobs #(job)))
  (setv frames (AgentdSettings :node-name NODE :backend-kind "tmux" :stream-capability "frames"))
  (setv events (AgentdSettings :node-name NODE :backend-kind "headless" :stream-capability "events"))
  (assert (= (run (wait-seconds-for state frames)) frames.frame-interval-seconds))
  (assert (= (run (wait-seconds-for state events)) events.events-poll-seconds))
  (assert (< events.events-poll-seconds frames.frame-interval-seconds))
  (setv idle-job (replace job :capturing False))
  (setv resting (replace state :jobs #(idle-job)))
  (assert (= (run (wait-seconds-for resting events)) events.transcript-poll-seconds))
  (assert (= (run (wait-seconds-for resting frames)) frames.transcript-poll-seconds)))


(deftest test-record-appends-keep-the-transcript-period-while-pushes-follow-the-poll
  ;; 記録(turn-record)への追記は transcript の周期(1 s)のまま — 50 ms の拍ごとに書くと走っている手番 1 つで毎秒 10〜20 の
  ;; event が ACP の journal に並び、画面の糊の watch の拍が飽和する(実弾 2026-09-13 18:3x)。push は拍ごと・書かない拍の
  ;; 出来事は持ち越して次の追記に乗る(落とさない)。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  (.tick world 1000)
  (setv (get world.acp.subscribers sid) 1)
  ;; assistant の message の行 = 実況の frame(text)と記録の出来事(entry)の両方になる行。
  (defn #^ str delta-line [#^ str text]
    (stream-line {"type" "assistant"
                  "message" {"id" (+ "msg-" text) "role" "assistant" "model" "claude-opus-5"
                             "content" [{"type" "text" "text" text}]
                             "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0
                                      "cache_read_input_tokens" 0}}}))
  (setv lines (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"}))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
  (.tick world 1000)
  (setv writes-before (len (.record-writes world))
        pushes-before (len world.acp.pushes))
  ;; 50 ms 刻みで 10 拍(合計 500 ms・追記の周期の中): push は拍ごと・記録の書きは増えない・出来事は持ち越し。
  (for [i (range 10)]
    (setv lines (+ lines (delta-line f"w{i} ")))
    (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
    (.tick world 50))
  (assert (>= (- (len world.acp.pushes) pushes-before) 10) "push は拍ごと")
  (assert (= (len (.record-writes world)) writes-before) "記録の書きは周期の中では増えない")
  (assert (= (len (. (get world.state.jobs 0) pending-entries)) 10) "書かない拍の出来事は持ち越す")
  ;; 周期が経った拍に 1 回で書く(持ち越した 10 件が乗る)。
  (setv lines (+ lines (delta-line "last ")))
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl") lines)
  (.tick world 600)
  (assert (= (len (.record-writes world)) (+ writes-before 1)) "周期が経てば 1 回で書く")
  (assert (= (len (. (get world.state.jobs 0) pending-entries)) 0))
  (assert (= (len (lfor e (.record-entries world) :if (= (get e "kind") "text") e)) 11) "持ち越した出来事は落ちない"))


(deftest test-a-session-wake-settles-the-turn-end-in-that-tick-without-relisting
  ;; 段 12 lane 12b(agora-redesign #207 根 1): host の monitor が手番の終わり(turn_ended_at)を刻んだ拍に、器の出来事の
  ;; 合図(WatchAdvance kind session)が拍を起こす。その拍は ACP の行を読み直さず(list-mode none — ACP の sequence は
  ;; 進んでいない)、走っている job の観測だけで turn-record を ended・job を Ended にする。拍の周期(transcript_poll_seconds)
  ;; を待たない — 周期は保険。
  (setv world (World))
  (.tick world 0)
  (setv sid (.sid world))
  ;; 手番の材料(記録が進んだ証拠 = 送った本文が届いて手番が始まった)。
  (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
        (+ (stream-line {"type" "system" "subtype" "init" "session_id" sid "model" "claude-opus-5"})
           (stream-line {"type" "assistant"
                         "message" {"id" "msg-1" "role" "assistant" "model" "claude-opus-5"
                                    "content" [{"type" "text" "text" "done"}]
                                    "usage" {"input_tokens" 1 "output_tokens" 1 "cache_creation_input_tokens" 0
                                             "cache_read_input_tokens" 0}}})))
  (.tick world 1000)
  (assert (= (.job-phase world) "Running"))
  ;; ACP を静める(前の拍の書きで進んだ sequence に since を追いつかせる)— 次の拍を起こすのは器の合図だけ。
  (.tick world 100)
  (assert (= world.state.since world.acp.sequence))
  ;; host が手番の終わりを刻む → 器の合図だけが届く(ACP の sequence は進めない)。
  (.finish-turn world.sessions sid (+ world.local.now-ms 200))
  (setv since world.state.since)
  (setv lists-before (len world.acp.lists))
  (.append world.acp.wakes (WatchAdvance :kind "session" :sequence since))
  (.tick world 100)
  (assert (= (len world.acp.lists) lists-before) "器の合図の拍は一覧(AcpGet)を撃たない")
  (assert (= (.job-phase world) PHASE-ENDED) "合図の拍で job は Ended")
  (assert (= (get (.record-status world) "state") "ended") "合図の拍で turn-record は ended")
  (assert (= world.state.jobs #()))
  (assert (= world.state.since since) "器の合図は ACP の since を進めない"))


(deftest test-list-mode-for-reads-a-session-wake-as-none
  ;; 判断の 1 点: 器の合図(kind session)は行の読み直しを求めない(ACP の sequence が進んでいない)— 周期の保険が来て
  ;; いなければ none。
  (setv settings (AgentdSettings :node-name NODE :backend-kind "headless" :stream-capability "events"))
  (setv state (replace (initial-state) :last-resync-ms 1000 :since 7))
  (assert (= (run (list-mode-for (WatchAdvance :kind "session" :sequence 7) state 1500 settings)) LIST-MODE-NONE)))


;; ---------------------------------------------------------------------------
;; 実況の粒を「拍の直列」から外す(card acp:kanban-issue:ki-6eb745f6d528)
;; ---------------------------------------------------------------------------

(deftest test-agentd-all-jobs-push-before-any-turn-record-write
  ;; 由来 (b) 直列: 拍の中の job の loop が直列だと、job k の生の frame は job k−1 の turn-record への
  ;; CAS(頭への 1 往復)の**後ろ**に並ぶ。手番 20 本の agentd の実測(2026-09-19): 同じ拍の中の job 間の
  ;; ずれ p50 0.453 秒・max 1.432(12/20 本ぶん)。拍を 2 段に割り、1 周目で全 job の push を出す。
  (setv world (World :job-ids #("j-1" "j-2" "j-3")))
  (.tick world 0)
  (for [job-id world.job-ids]
    (.write-events world job-id (+ (init-line (.sid world job-id)) (delta-line "hello"))))
  (setv start (len world.acp.trace))
  (.tick world 1000)
  (setv tail (cut world.acp.trace start None))
  (setv pushes (lfor [index entry] (enumerate tail) :if (= (get entry 0) "push") index))
  (setv writes (lfor [index entry] (enumerate tail)
                     :if (and (in (get entry 0) #("status" "create"))
                              (in f":{TURN-RECORD-KIND}:" (get entry 1)))
                     index))
  (assert (= (len pushes) 3) f"3 本の job が 1 拍に 1 度ずつ押す: {tail}")
  (assert (= (len writes) 3) f"3 本の turn-record が 1 拍に 1 度ずつ書かれる: {tail}")
  (assert (< (max pushes) (min writes))
          f"実況の push はどの turn-record への書きよりも先(card ki-6eb745f6d528): {tail}"))


(deftest test-agentd-frame-at-is-the-clock-of-its-own-push
  ;; 由来 (a) 拍の頭で時計を 1 度だけ読み全 frame の at にする: 手番 20 本の agentd では、最後の job の frame が
  ;; 「0.45 秒前に起きた」と名乗る(面が読む粒はその分ぶれる)。at はその frame を押す**その job の**時計読み。
  (setv world (World :job-ids #("j-1" "j-2" "j-3") :clock-step-ms 100))
  (.tick world 0)
  (for [job-id world.job-ids]
    (.write-events world job-id (+ (init-line (.sid world job-id)) (delta-line "hello"))))
  (setv start (len world.acp.trace))
  (setv pushes-before (len world.acp.pushes))
  (.tick world 1000)
  (setv tail (cut world.acp.trace start None))
  ;; 押しごとに「その押しの直前の時計読み」を拾う(書き・押し・時計が同じ 1 本の列に並んでいる)。
  (setv last-clock None)
  (setv before-push [])
  (for [entry tail]
    (when (= (get entry 0) "clock")
      (setv last-clock (int (get entry 1))))
    (when (= (get entry 0) "push")
      (.append before-push last-clock)))
  (assert (= (len before-push) 3) f"3 本の job が 1 拍に 1 度ずつ押す: {tail}")
  (setv frame-ats [])
  (for [pushed (cut world.acp.pushes pushes-before None)]
    (setv frames (get pushed 2))
    (assert (= (len frames) 1) frames)
    (.append frame-ats (get (get frames 0) "at")))
  (assert (= (len (set frame-ats)) 3)
          f"job ごとに frame の at が違う(拍の頭の 1 度の読みではない): {frame-ats}")
  (assert (= frame-ats before-push)
          f"frame の at はその job の押しの直前の時計読み: at={frame-ats} 読み={before-push}"))


(deftest test-agentd-record-spool-flush-is-bounded-per-tick
  ;; 由来 (d) spool の上限なし: 拍の終わりの flush が spool の全部を上限なく回すと、溜まった拍の周期が
  ;; spool の深さで決まる(24 秒級の外れ値の候補)。上限は AgentdSettings の 1 点で宣言し、残りは次の拍へ
  ;; 持ち越す(落とさない — 今日の規律は変えない)。
  (setv world (World :record True :flush-max 4))
  (for [index (range 10)]
    (setv key f"b-{index :02d}")
    (setv (get world.record.spool key)
          (RecordBatch :spool-key key :conversation-id CONVERSATION
                       :stream (RecordStream :kind RECORD-STREAM-TURN :stream-id "j-1#a1"
                                             :started-at-ms 1000 :node NODE :profile "personal" :attempt 1)
                       :events #({"producerSeq" index "at" 1000 "kind" "text" "text" f"body-{index}"}))))
  (setv bound world.settings.record-flush-max-batches)
  (assert (= bound 4))
  (.tick world 0)
  (assert (= (len world.record.appends) bound)
          f"1 拍の追記は上限ちょうど: {(len world.record.appends)}")
  (assert (= (len world.record.spool) (- 10 bound)) "残りは spool に残る(落とさない)")
  (.tick world 1)
  (assert (= (len world.record.appends) (* 2 bound)) "次の拍が続きを送る")
  (assert (= (len world.record.spool) (- 10 (* 2 bound)))))


(defclass CountingServer [ThreadingHTTPServer]
  "accept の数を数える loopback の server(接続を使い回すかは socket の accept でしか見えない)。
  押しと読みが名乗った機体の名(header)も順に貯める — 名乗りは要求の中にしか無い。"
  (defn #^ None __init__ [self #^ tuple address handler]
    (setv self.accepts 0)
    (setv self.push-sources [])
    (setv self.read-sources [])
    (.__init__ (super) address handler))

  (defn get-request [self]
    (setv self.accepts (+ self.accepts 1))
    (.get-request (super))))


(defclass EchoHandler [BaseHTTPRequestHandler]
  "ACP の engine の代わり(keep-alive の HTTP/1.1・Content-Length つき)。"
  (setv protocol-version "HTTP/1.1")

  (defn #^ None do-GET [self]
    (.append self.server.read-sources (.get self.headers STREAM-SOURCE-HEADER))
    (.reply self {"resourceNamespace" "agora-kinds" "resourceKey" "agora-kinds:node:mac-1"
                  "resourceKind" "node" "resourceId" "mac-1" "resourceVersion" "v1"
                  "resourceGeneration" 1 "resourceCreatedAt" "1970-01-01T00:00:00Z"}))

  (defn #^ None do-POST [self]
    (.append self.server.push-sources (.get self.headers STREAM-SOURCE-HEADER))
    (setv length (int (.get self.headers "Content-Length" "0")))
    (when length
      (.read self.rfile length))
    (.reply self {"seq" 1 "subscribers" 0}))

  (defn #^ None reply [self #^ dict body]
    (setv raw (.encode (json.dumps body) "utf-8"))
    (.send-response self 200)
    (.send-header self "Content-Type" "application/json")
    (.send-header self "Content-Length" (str (len raw)))
    (.end-headers self)
    (.write self.wfile raw))

  (defn #^ None log-message [self #^ str fmt #* args]
    None))


(defk acp-five-calls []
  {:pre []
   :post [(: % int)]}
  "AcpHttp の口で同じ host へ 5 回読み書きする(読み 3・押し 2)— 戻りは撃った数。"
  (setv key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}")
  (setv frame {"agentJobId" "j-1" "seq" 0 "at" 1000 "kind" "status" "payload" {}})
  (<- read-1 (| AcpRow None) (AcpGetRow :key key))
  (<- read-2 (| AcpRow None) (AcpGetRow :key key))
  (<- read-3 (| AcpRow None) (AcpGetRow :key key))
  (<- push-1 (| Pushed Refused) (AcpStreamPush :owner "agentd" :name "sid-1" :frames #(frame)))
  (<- push-2 (| Pushed Refused) (AcpStreamPush :owner "agentd" :name "sid-1" :frames #(frame)))
  (len [read-1 read-2 read-3 push-1 push-2]))


(deftest test-agentd-acp-http-reuses-one-connection-per-host
  ;; 由来 (c) HTTP が毎回 TCP を張り直す = **1 発ごとに名前を引き直す**(依頼者の実射 2026-09-19: この宿は
  ;; ndots:5 + search 4 つで、点で終わらない綴りは探索の列を歩き 名引き 23.61 ms・全体 25.20 ms の 94 %。
  ;; 保った接続は名引きを接続 1 本につき 1 度に畳んで 1 発 0.46 ms ⇒ 1 拍 約 35 往復で 0.882 秒 → 0.016 秒)。
  ;; host ごとに 1 本の接続を保つ(壊れたら 1 度だけ張り直す)。⚠ この検だけは loopback の HTTP server を立てる。
  (setv server (CountingServer #("127.0.0.1" 0) EchoHandler))
  (setv thread (threading.Thread :target server.serve-forever :daemon True))
  (.start thread)
  (try
    (setv port (get server.server-address 1))
    (setv acp (AcpHttp f"http://127.0.0.1:{port}" None))
    (setv answers (.run (PyVM) (install (acp-five-calls) [acp.dispatch])))
    (assert (= answers 5) answers)
    (assert (= server.accepts 1)
            f"host ごとに 1 本の接続を使い回す(accept は 1 回): {server.accepts}")
    (finally
      (.close acp)
      (.shutdown server)
      (.server-close server))))


(deftest test-agentd-tick-emits-one-metric-line-with-the-arm-split
  ;; card acp:kanban-issue:ki-6eb745f6d528(依頼者の便 2026-09-19 lt-BM9E73V8EWSK72K9E0JMQ1RXPT):
  ;; ACP 側の acp_stream_push_interval_seconds は「粒が 26 秒だった」とは言えても**どの腕が遅かったか**は
  ;; 言えない。だから拍は自分で 1 行名乗る —— 腕の名の集合は宣言 TICK-ARMS ちょうど・拍の総所要は total・
  ;; **腕の和は total に等しい**(名の付いていない仕事が拍の中に残らない = この 1 行で拍を説明しきる)。
  (assert (= TICK-ARMS #("watch" "heartbeat" "profiles" "receive" "sweep" "interrupts" "cancel"
                         "ends" "jobs-fast" "jobs-slow" "commands" "summaries" "flush"))
          TICK-ARMS)
  ;; 時計が読むたびに進む世界(拍の中で腕の所要が 0 でない)。
  (setv world (World :clock-step-ms 1))
  (.tick world 0)
  (setv lines (lfor line world.local.metrics :if (= (.get line "metric") METRIC-TICK-MS) line))
  (assert (= (len lines) 1) f"1 拍 = 1 行: {world.local.metrics}")
  (setv line (get lines 0))
  (assert (= (set (.keys line)) (set TICK-LINE-FIELDS)) (sorted (.keys line)))
  (assert (= (get line "node") NODE) line)
  (assert (> (get line "total") 0) line)
  (assert (= (sum (lfor name TICK-ARMS (get line name))) (get line "total"))
          f"腕の和 = 拍の総所要(名の無い仕事を残さない): {line}")
  ;; 2 拍目も 1 行(拍ごとに 1 行 — 溜めない・落とさない)。
  (.tick world 1000)
  (assert (= (len (lfor entry world.local.metrics :if (= (.get entry "metric") METRIC-TICK-MS) entry)) 2)
          world.local.metrics))


(deftest test-agentd-stream-push-names-the-machine-that-pushed
  ;; card acp:kanban-issue:ki-6eb745f6d528(依頼者の便 2026-09-19 lt-BM9E73V8EWSK72K9E0JMQ1RXPT 足す (A)):
  ;; ACP 側の acp_stream_push_interval_seconds は「粒が 26 秒だった」とは言えても**どの機体が**押したかは
  ;; 言えない。中継は store を読めない(ACP 法 stage0_stream_relay_ephemeral_owner_pushed_fabff2)ので行から
  ;; node を引くこともできない —— だから押す側が要求に名乗る(header は effects.STREAM-SOURCE-HEADER の 1 点)。
  ;; 名乗るのは**押す拍だけ**(読み書きには付けない — 要る問いは「実況の粒がどの機体で粗いか」1 つ)。
  (setv server (CountingServer #("127.0.0.1" 0) EchoHandler))
  (setv thread (threading.Thread :target server.serve-forever :daemon True))
  (.start thread)
  (try
    (setv port (get server.server-address 1))
    (setv acp (AcpHttp f"http://127.0.0.1:{port}" None None NODE))
    (assert (= (.run (PyVM) (install (acp-five-calls) [acp.dispatch])) 5))
    (assert (= server.push-sources [NODE NODE]) server.push-sources)
    (assert (= server.read-sources [None None None])
            f"読み書きは機体を名乗らない: {server.read-sources}")
    (finally
      (.close acp)
      (.shutdown server)
      (.server-close server)))
  ;; 名を知らない口(検体・名の無い機体)は header そのものを持たない — 空の label を engine へ送らない。
  (setv anonymous (CountingServer #("127.0.0.1" 0) EchoHandler))
  (setv anonymous-thread (threading.Thread :target anonymous.serve-forever :daemon True))
  (.start anonymous-thread)
  (try
    (setv acp-2 (AcpHttp f"http://127.0.0.1:{(get anonymous.server-address 1)}" None))
    (assert (= (.run (PyVM) (install (acp-five-calls) [acp-2.dispatch])) 5))
    (assert (= anonymous.push-sources [None None]) anonymous.push-sources)
    (finally
      (.close acp-2)
      (.shutdown anonymous)
      (.server-close anonymous))))
