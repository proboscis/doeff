;;; agentd の program — 参加(join)・agent-job の受け・記録(turn-record)・実況(TurnDelta)・
;;; 借用(custody)を effect の列として書く(段 2・agora-redesign #19 / #20)。
;;;
;;; ここは要求を並べるだけで、判断は judgment.hy の純関数(自分に結ばれた job か・自分が持つ
;;; Running か・その次の 1 手・capture の是非・待ちの長さ)、I/O は handlers.py(実)/
;;; fake.py(test)。handler の選択は runtime.py の composition root ちょうど。1 tick =
;;; agentd-tick で、状態(AgentdState)は値として出入りする(loop は runtime.py が回す)。
;;;
;;; job の進みは行から導く(ADR-DOE-AGENTS-012 R7): memory の InFlightJob は cache で、
;;; 再起動で消えても agent-job の行(phase Running・sessionHandle)と器の現況(session.get)
;;; から組み直す(recover-job)。次の 1 手は memory の有無に依らず judgment.job-step-of の
;;; 1 点(器が無い → fail-missing / 終端 → record-end / 走っている → observe)。
;;; capture の gone は終端の合図で例外ではない(R8)。tick の縁(heartbeat・受け・job ごと)は
;;; 互いの I/O の失敗で止まらない(R9)。backend の生死は host の観測(眺めの backend_alive)で
;;; 決め、status の語から推測しない(段 10 lane 10h・agora-redesign #84: 再起動で死んだ手番が
;;; running のまま残り、recover-job が observe・next-arm-for-job が defer を返し続けた)—
;;; job-step-of の session-lost は記録の腕と SessionLost で閉じ、next-arm-for-job は手番の途中でも
;;; backend が死んでいれば待たずに候補を片付けて resume / rehydrate する。
;;;
;;; 温かい session(R10・設計 17.4): session は会話の資源で job は手番。会話 → 生きている
;;; session の対応は行(agent-job の subject と sessionHandle)から導き、Bound の job の起こし方は
;;; judgment.next-arm-for-job の 1 点(launch | send | resume | defer)。同じ会話の次の手番は
;;; launch せず session.send(awaiting)だけ。手番の終わりは host の monitor が刻む
;;; turn_ended_at(policy.hy の turn-end の連言)を job-step-of が読む(turn-end)— session は
;;; 生かしたまま turn-record を ended・job を Ended にする。idle の寿命は
;;; AgentdSettings.session_idle_ttl_seconds(heartbeat の拍に sessions-to-retire で片付ける)。
;;;
;;; headless の 1 手番目(R16): headless の器は 1 手番 = 1 prompt で、走っている手番の途中に次の本文を
;;; 積めない。起こす腕(launch / resume)は inputs の郵便を charter の prompt に畳んで起こし(判定は
;;; judgment.first-turn-carries-inputs・畳みは first-turn-prompt-of の 1 点)、after-start は send を
;;; 撃たない。tui は今日どおり launch の後に send。郵便の読み(mail-of)は腕を選んだ後・起こす前。
;;;
;;; profile の残量(段 7 lane 7d-3・既知の形 = kubelet の node status): この機体が持つ資格の profile ごとに
;;; 残量を読み(ReadProfileUsage — 読み口は agentcli の usage の 1 点・会社境界はその葉)、profile の
;;; status.observed を post-image で書く(ifGeneration・変わった時だけ・世代の競合は 1 拍見送る)。
;;; 断られた / 単位の違う profile は書かず理由を log に 1 行、この機体に無い profile は黙って書かない。
;;; 判断(窓・残量・post-image)は judgment.profile-observed-of の 1 点、周期は
;;; AgentdSettings.profile_observe_seconds(heartbeat より遅い別の腕)。枯渇の判断は controller(agora-budget)。
;;; 器の profile の集合は先に読む(段 8e lane 4j — ListProfileHomes = 登録簿 × 家の実在・判断は
;;; judgment.profile-rows-held): 家の在る profile が 1 つも無い機体(pool の pod)は usage を撃たず、
;;; 「観測する profile なし」を 1 度だけ名乗る(周期ごとに読み口の落ち方を吐かない)。
;;;
;;; 手番の出来事の耐久化(段 8 lane 4u・agora-redesign #49): 実況の材料を読む拍ごとに、その拍の
;;; entries(text / tool_use / tool_result / system / error)を turn-record の status.entries へ追記する
;;; (append-entries — 耐久化は手番の終わりを待たない。落ちた手番も読んだ所までは残る)。書きは informer と
;;; 同じ CAS(ifGeneration = 最後に知った行の generation)で、Conflict は行を読み直して同じ出来事を
;;; 積み直し、断られた / 行がまだ無い拍の出来事は pending-entries に持ち越して次の拍か手番の終わりに乗せる。
;;; 判断(出来事 → entry・上限・切り詰め・追記の post-image・採番の衝突)は judgment の純関数。
;;; 手番の終わりは最後の材料を同じ拍で読み(実況の frame も押す)、残りを追記した上で ended と usage を書く
;;; (usage は手番の全材料の読み直しから — message ごとの重複を跨いで数えない)。
;;;
;;; 会話の引き継ぎ(R20・段 8q・既知の形 = virtual actor の状態の移送): profile / 機体を変えた手番でも会話は続く。
;;; 起こす session には会話・手番・家を launch_attribution に刻み(session の行が覚える — 回収される agent-job の行に
;;; 頼らない)、turn-record の spec に sessionId を書く(Messaging が次の手番の affinity.predecessor に名指す)。
;;; 起こし方は judgment.next-arm-for-job の 1 点: cache(温かい send / --resume)を保つのは同じ機体 ∧ 同じ家(account・
;;; binding・model の組 — 段 9o lane 9o-3)の時だけで、
;;; 家か機体が違えば cache の失効を受け入れ(operator 決定 #54)、家の違う温かい session は片付けて rehydrate(ACP の
;;; 会話の記録を最初の本文に畳む — judgment.rehydrate-history-of・上限は AgentdSettings.rehydrate_history_byte_budget・
;;; 文脈の圧縮は別 issue #55)、resume が断られたら rehydrate(judgment.fallback-arm-of)。再開の材料の読みは名指しの順(段 9q・#77):
;;; 段 10 lane 10o(agora-redesign #96・依頼者の追補): 郵便の添付(画像)は行が見出しだけを運び、中身は本文と同じ 1 回の
;;; stream の読み(mail-bodies-by-ref)で拾う。器へは**型つき**(TurnAttachment)のまま SessionSend / SessionInterject で
;;; 渡すだけで、CLI の綴り(claude の content の block・codex の input の項)はこの module に 1 語も無い — 組むのは
;;; sessionhost/headless_protocol.py の kind ごとの Dialogue(法 012 R30・R21 と同じ形)。器が添付を落とした拍は
;;; 条件 AttachmentIgnored を手番に付ける(黙って落とさない・本文は届いている)。
;;; 手番の本文は記録の service(RecordRead・会話 1 つ)、郵便は ACP の kind message(AcpConversationMail)、ACP の turn-record の
;;; 見出し(AcpTurnHeadlines = kind の全量・実測 29,913 行 / 172 MB / 59 秒)は service が答えなかった薄い再開の拍にだけ読む —
;;; claim(AcpPutStatus)は宣言の照合だけで即時(実測 0.3 秒)、器の準備(再開の読み・畳み)は claim の後の腕で、node の
;;; lease(TTL 90 秒)より長く tick を塞いではならない。node の observations は sessions に account、transcripts に「終端だが
;;; transcript がこの機体に残る会話」を載せ、Scheduling は (node, account) で親和と起こし方の語を決める。
;;;
;;; 会話の記録の service への二重書き(段 9f lane 9f-2・agora-redesign #59・設計 §2.4・既知の形 = runner の transactional
;;; outbox): 本文は切らずに組み(DeltaBatch.bodies — 契約 record-service eventIn・producerSeq = entry の seq = InFlightJob の
;;; delta-seq の 1 点)、読んだ拍に spool へ耐久化し(spool-record-bodies — 1 batch 1 file)、拍の終わりの flush-record-spool が
;;; service へ送って受理(と 409)で消す。送れない batch は残し record_retry_seconds の後に再送(冪等 — 同じ鍵と本文は ignored)。
;;; ACP の turn-record への追記は**見出しだけ**(段 9f lane 9f-4・設計 §2.2: entries は本文から judgment.headline-of-body の
;;; 1 点で導く TurnEntryHeadline — seq・at・kind・toolName・toolUseId・bytes・sha256・isError。本文の欄は型に無い)。service が
;;; 受理した答え(highestProducerSeq)は mark-recorded が status.recordRef / recordedSeq に写す。履歴からの再開は service の
;;; before=latest から読み(record-turns-for)、届かなければ ACP の見出しで薄く再開すると名乗る。stream = 手番
;;; `<jobId>#a<attempt>`(拾い直しは turn-record の行の generation + 1 — judgment.recovered-record-of)。弁 =
;;; AgentdSettings.record_enabled(RECORD_SERVICE_URL の在否)— off の間は Record* を 1 つも撃たない。実運転の off は
;;; 無い(段 9f lane 9f-6: 宛先を持たない agentd は参加の門 join.record-sink-of が断る)— off は test の対照(二重書きの
;;; 有無で ACP の見出しが一致する検)だけ。
;;;
;;; 割り込みの本文(段 8 lane 4x・agora-redesign #56): Messaging が走っている手番の agent-job の
;;; status.interrupts に載せた Message の id を、自分が走らせている job について行の cache の差分で読み
;;; (deliver-interrupts — 判断は judgment.pending-interrupts-of: 行の interruptsDelivered にも memory の
;;; interrupts-sent にも無い id)、本文を鍵で 1 行ずつ読んで session.send の mode = interrupt で器へ即座に
;;; 渡す(claude = stream-json の stdin の user の行・codex = turn/interrupt → 同じ thread へ turn/start)。
;;; 渡せたら鍵で読み直した行に CAS で interrupts から消し interruptsDelivered へ足す(同じ 1 回の書き —
;;; 判断は interrupts-delivered-status-of)。器が断った(走っている手番が無い)id は行に残す —
;;; 手番が終わればその行は終端の phase で interrupts を持ち、Messaging が queued として積み直す。
;;;
;;; 割り込みの約束 = 「期限までに model が読む」(段 10 lane 10n・agora-redesign #93・既知の形 = cooperative cancel →
;;; hard cancel の 2 段): 注入の行の名 = Message の id(claude の user の行の uuid — CLI の command_lifecycle がこの綴りで
;;; 運命を名乗る・実測 conformance/interrupt-physics.md 2026-09-14)。読んだ証拠 = 材料の中の started(claude)/ 止めた後の
;;; turn/started(codex)— judgment.claude-deltas-of / codex-event-deltas-of が kind system の entry と DeltaBatch.interrupt-reads
;;; にし、stream-records が interrupt-reads-of で memory に写す。期限 = job の charter.interruptEscalationSeconds ちょうど
;;; (方策の行の値を Messaging が会話の宣言で重ねて charter に写す — agentd は方策も会話も読まず、code に既定を置かない。
;;; 無い job は注入だけ + 条件 InterruptEscalationUndeclared)。期限を過ぎて未読なら SessionEscalate(session.escalate =
;;; claude の control_request interrupt・codex は注入の段が無く host が断る)を 1 度出し、未読の id 全部に止めた印。
;;; 印は行の status.interruptsRead {id: seq} / interruptsEscalated {id: ms} へ CAS で写す(record-interrupt-marks —
;;; 断られた拍は memory の dirty で持ち越す)。判断は judgment の純関数 1 点ずつ(interrupts-due-for-escalation・
;;; interrupt-reads-of・interrupt-marks-status-of)。受け取りは watch(AcpWatchSse が changed で即座に拍を起こし、同じ拍の
;;; window の読み直しで interrupts が cache に載る — 拍の周期は保険)。
;;;
;;; 書く欄は契約の writers どおり: agent-job の phase / sessionHandle / result / conditions /
;;; interrupts / interruptsDelivered / interruptsRead / interruptsEscalated、node の status.lease / status.observations、turn-record の create と
;;; status、profile の status.observed。binding は書かない・Node の行は作らない(scheduling の欄と動詞)。

(require doeff-hy.macros [defk <-])

(import dataclasses [replace])

(import doeff_agents.sessionhost.attachment [TurnAttachment])
(import doeff_agents.sessionhost.acp.effects [
  CONDITION-ATTACHMENT-IGNORED
  AGENT-JOB-KIND
  AGORA-KINDS-NAMESPACE
  AcpConversationMail
  AcpCreate
  AcpEventWindow
  AcpGet
  AcpGetRow
  AcpPutSpec
  AcpPutStatus
  AcpRow
  AcpStreamPush
  AcpTurnHeadlines
  AcpWatchSse
  AgentdSettings
  AgentdState
  ArmChoice
  CaptureFrame
  CaptureGone
  ClockNowMs
  Conflict
  CustodyLeaseBorrow
  CustodyLeaseRevoke
  DeltaBatch
  EVENT-WINDOW-LIMIT
  EventWindow
  FsCanonicalPath
  FsFileSize
  FsWritePrivateText
  HeadlineTurns
  HistoryFold
  IO-FAILURES
  InFlightJob
  Interjected
  JOB-STEP-FAIL-MISSING
  JOB-STEP-OBSERVE
  JOB-STEP-RECORD-END
  JOB-STEP-SESSION-LOST
  JOB-STEP-TURN-END
  JobOutcome
  LIFECYCLE-MULTI-TURN
  LIST-MODE-FULL
  LIST-MODE-NONE
  LIST-MODE-WINDOW
  LaunchPlan
  LeaseGrant
  LeaseRefused
  ListProfileHomes
  LogLine
  MESSAGE-KIND
  METRIC-COMPACTIONS-TOTAL
  METRIC-RECORD-APPEND-TOTAL
  METRIC-RECORD-LAG-SEQ
  METRIC-RECORD-SPOOL-DEPTH
  MetricLine
  MintId
  RECORD-PAGE-MAX-LIMIT
  RecordAppend
  RecordAppended
  RecordConflicted
  RecordPage
  RecordRead
  RecordReadStream
  RecordSpoolList
  RecordSpoolListing
  RecordSpoolPut
  RecordSpoolGiveUp
  RecordSpoolRemove
  RecordUnread
  RecordUnsent
  RecordedTurns
  CONDITION-CREDENTIAL-PLACE-MISMATCH
  CONDITION-CREDENTIAL-SOURCE-MISSING
  CONDITION-INTERRUPT-ESCALATION-UNDECLARED
  CREDENTIAL-SOURCE-MISSING
  NEXT-ARM-DEFER
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME
  NEXT-ARM-SEND
  NODE-KIND
  PROFILE-KIND
  PROFILE-USAGE-KIND
  ProfileNotHeld
  ProfileObservation
  ProfileUnobserved
  Pushed
  ReadProfileUsage
  RECORD-APPEND-CONFLICT
  RECORD-APPEND-ERROR
  RECORD-APPEND-GIVEN-UP
  RECORD-APPEND-OK
  RECORD-CREATE-GIVEN-UP
  RECORD-CREATE-PENDING
  Refused
  INTERRUPT-ARM-INTERRUPT
  STREAM-SOURCE-EVENTS
  Escalated
  SessionCapture
  SessionCleanup
  SessionEscalate
  SessionEvents
  SessionGet
  SessionInterject
  SessionInterrupt
  SessionLaunch
  SessionList
  SessionRefused
  SessionResume
  SessionSend
  SessionTranscript
  SessionView
  TURN-RECORD-KIND
  TranscriptChunk
  WatchAdvance
  Written])
(import doeff_agents.sessionhost.acp.judgment [
  birth-ms-of
  births-of-rows
  births-with
  capture-verdict
  cleanup-after-end
  condition-of
  credential-place-mismatch
  credential-place-of
  credential-from-custody
  credential-source-of
  deltas-of
  due
  record-due
  record-append-word-of
  record-batches-of
  record-create-applied
  record-create-due
  record-flush-due
  record-given-up-noted
  record-history-satisfied
  record-lag-of
  record-page-advances
  record-ref-of
  record-stream-job-of
  recovered-record-of
  ended-status-of
  entries-of-status
  next-seq-after
  renumbered-entries
  effort-of-plan
  fallback-arm-of
  first-turn-carries-inputs
  frame-lines-of
  session-affinity-key-of
  session-lost-condition-of
  in-flight-ids
  in-flight-job-of
  incarnation-charter-of
  inputs-of
  interrupt-arm-for
  interrupt-escalation-undeclared-reason
  interrupt-marks-status-of
  interrupt-reads-of
  interrupted-status-of
  interrupts-delivered-status-of
  interrupts-due-for-escalation
  recovered-interrupts-of
  status-with-condition
  with-injected-interrupts
  job-row-keyed
  job-outcome-of
  job-rows-bound-to
  job-rows-running-on
  job-step-of
  launch-plan-of
  lease-renew-due
  list-mode-for
  merge-rows
  mail-text-of
  attachment-of
  first-turn-attachments-of
  launch-charter-with-attachments
  message-attachments-of
  message-bodies-of
  message-body-ref-of
  message-key-of
  ignored-settings-of
  next-arm-for-job
  compact-at-of
  compaction-due
  conversation-opener-of
  context-percent-for
  context-percent-of
  conversation-key-of
  with-context-percent
  node-resource-id-of
  node-row-named
  node-spec-declared
  node-spec-of
  node-status-with-lease
  pane-frame
  pending-interrupts-of
  profile-observed-changed
  profile-observed-of
  profile-key-of
  profile-rows-active
  profile-rows-held
  profile-status-with-observed
  recovered-arm-of
  rehydrate-history-of
  restart-condition-of
  retire-reason-of
  resume-params-of
  rows-of-kind
  running-status-of
  session-alive
  session-attribution-of
  session-id-of-handle
  session-observations-of
  sessions-to-retire
  status-frame
  status-object-of
  stream-source-of
  turn-record-appended-status
  transcript-candidates-of
  transcript-observation-of
  transcript-path-of
  turn-record-ended-status
  turn-record-key-of
  turn-record-recorded-status
  turn-record-spec-of
  turn-session-env-of
  usage-by-profile
  wait-seconds-for
  warm-candidate-of
  with-job
  withdrawn-rows-of
  without-job])


;; ---------------------------------------------------------------------------
;; 参加(join): Node の行は読むだけ・lease と観測を書く
;; ---------------------------------------------------------------------------

(defk retire-sessions [session-ids reason]
  {:pre [(: session-ids tuple) (: reason str)]
   :post [(: % int)]}
  "温かい session を片付ける(session.cleanup — pane を消し、非終端なら stopped)。
   戻り = host が受けた数。"
  (setv accepted-count 0)
  (for [session-id session-ids]
    (<- accepted bool (SessionCleanup :session-id session-id))
    (when accepted
      (setv accepted-count (+ accepted-count 1)))
    (<- (LogLine :text (+ f"agentd: session {session-id} cleaned up ({reason})"
                               (if accepted "" " — host did not accept")))))
  accepted-count)


(defk join-tick [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "heartbeat の腕: 自分の Node の行を読み、無ければ機体の宣言から作り、在れば spec を宣言へ揃えてから status.lease と
   status.observations(器の眺めと session に刻んだ帰属から導いた会話の session の一覧と、transcript が残る会話の一覧 —
   R20)を書き、idle が TTL を過ぎた温かい session を片付ける。R28(段 10 lane 10d・agora-redesign #85): node の spec の
   書き手は agentd(既知の形 = kubelet の Node の自己登記・spec の形は judgment.node-spec-of / node-spec-declared の 1 点)。
   行が無い拍は、node が退いた以上は温かい session を残さずに作る。作れない・揃えられない拍(書き手の断り等)は 1 度だけ
   log して次の周期に撃ち直す — 揃えられなくても lease は書く(参加の生存を spec の書きの成否に結ばない)。"
  (<- rows tuple (AcpGet :kind NODE-KIND))
  (<- node (| AcpRow None) (node-row-named rows settings.node-name))
  (<- views tuple (SessionList :lifecycle LIFECYCLE-MULTI-TURN))
  (setv next (replace state :last-heartbeat-ms now-ms))
  (if (is node None)
      (do
        (setv alive [])
        (for [view views]
          (<- live bool (session-alive view))
          (when live
            (.append alive view.session-id)))
        (<- (retire-sessions (tuple alive) "node is not in ACP"))
        (<- spec dict (node-spec-of settings))
        ;; 段 10 lane 10d 便 4(#107 の (3)): 配車から外された行が名の鍵を占めていたら、同じ身元
        ;; (spec.name)の新しい incarnation の鍵で作る — 鍵は器の名で、身元ではない。
        (<- resource-id str (node-resource-id-of rows settings.node-name))
        (<- created (| Written Conflict Refused)
            (AcpCreate :namespace AGORA-KINDS-NAMESPACE :kind NODE-KIND :resource-id resource-id :spec spec
                       :declaration-sha256 settings.declaration-sha256))
        (if (isinstance created Written)
            (do
              (<- (LogLine :text (+ f"agentd: registered node row {settings.node-name !r} from the declaration "
                                    f"(capacity {settings.node-capacity}, streamCapability {settings.stream-capability}"
                                    (if (= resource-id settings.node-name) "" f", re-joined as {resource-id !r}") ")")))
              (replace next :node-missing-logged False))
            (do
              (setv why (if (isinstance created Refused)
                            f"{created.status}: {created.error}"
                            f"conflict at generation {created.current-generation}"))
              (when (not state.node-missing-logged)
                (<- (LogLine :text (+ f"agentd: node row {settings.node-name !r} is not in ACP and could not be "
                                      f"registered ({why}); re-trying each heartbeat"))))
              (replace next :node-missing-logged True))))
      (do
        (<- declared dict (node-spec-declared node.spec settings))
        (setv row node)
        (setv spec-refusal-logged state.node-spec-refusal-logged)
        (when (!= declared node.spec)
          (setv was (.get node.spec "capacity"))
          (<- aligned (| Written Conflict Refused) (AcpPutSpec :row node :spec declared :declaration-sha256 settings.declaration-sha256))
          (if (isinstance aligned Written)
              (do
                (<- (LogLine :text (+ f"agentd: node row {settings.node-name !r} spec aligned to the declaration "
                                      f"(capacity {was} -> {settings.node-capacity})")))
                (<- fresh (| AcpRow None) (AcpGetRow :key node.key))
                (when (is-not fresh None)
                  (setv row fresh))
                (setv spec-refusal-logged False))
              (do
                (setv why (if (isinstance aligned Refused)
                              f"{aligned.status}: {aligned.error}"
                              f"conflict at generation {aligned.current-generation}"))
                (when (not spec-refusal-logged)
                  (<- (LogLine :text (+ f"agentd: node row {settings.node-name !r} spec differs from the declaration "
                                        f"and could not be aligned ({why}); the lease is still written"))))
                (setv spec-refusal-logged True))))
        ;; 片付けてから観測を書く(観測 = 片付けた後の現況)。
        (<- expired tuple (sessions-to-retire views now-ms settings.session-idle-ttl-seconds))
        (<- (retire-sessions expired f"idle past {settings.session-idle-ttl-seconds}s"))
        (setv kept (tuple (lfor view views :if (not-in view.session-id expired) view)))
        (<- observations list (session-observations-of kept))
        (<- transcripts list (observe-transcripts settings views observations))
        (<- status dict (node-status-with-lease row settings now-ms observations transcripts))
        (<- outcome (| Written Conflict Refused) (AcpPutStatus :row row :status status))
        (when (isinstance outcome Refused)
          (<- (LogLine :text f"agentd: node lease refused ({outcome.status}): {outcome.error}")))
        (replace next :node-missing-logged False :node-spec-refusal-logged spec-refusal-logged))))


(defk observe-transcripts [settings views sessions]
  {:pre [(: settings AgentdSettings) (: views tuple) (: sessions list)]
   :post [(: % list)]}
  "node の observations.transcripts(段 8q・R20): 候補(judgment.transcript-candidates-of — 終端・帰属あり・
   生きた session の無い会話の最新・上限 AgentdSettings.transcripts_observed_max)のうち transcript の file が
   この機体に在る(大きさ > 0)ものを {conversationId, sessionId, account} に写す。"
  (<- candidates tuple (transcript-candidates-of views sessions settings.transcripts-observed-max))
  (setv out [])
  (for [view candidates]
    (<- canon str (FsCanonicalPath :path view.work-dir))
    (<- path (| str None) (transcript-path-of view canon))
    (when (is-not path None)
      (<- size int (FsFileSize :path path))
      (when (> size 0)
        (<- item dict (transcript-observation-of view))
        (.append out item))))
  out)


;; ---------------------------------------------------------------------------
;; profile の残量の観測(段 7 lane 7d-3): この機体が持つ資格の profile の status.observed を書く
;; ---------------------------------------------------------------------------

(defk observe-profiles [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "観測の腕: 生きている profile の行を読み、この機体の家の在否(ListProfileHomes)で観測する行を
   絞り(profile-rows-held — 空なら usage を撃たず『観測する profile なし』を 1 度だけ名乗る)、
   この機体が持つ資格の残量を 1 度読み(ReadProfileUsage — 読み口は agentcli の 1 点・会社境界は
   その葉)、行ごとに判断の 1 点(profile-observed-of)で書く観測を決めて、committed の observed と
   違う時だけ post-image を ifGeneration で書く。世代の競合(Conflict)はこの拍は見送り(次の周期に
   読み直す)、断り(Refused)と書かない理由は log に 1 行。行が無ければ家も usage も読まない。
   計器 profile-observed を 1 行。"
  (<- rows tuple (AcpGet :kind PROFILE-KIND))
  (<- active tuple (profile-rows-active rows))
  (setv next (replace state :last-profile-observed-ms now-ms))
  (when active
    (<- homes tuple (ListProfileHomes :kind PROFILE-USAGE-KIND))
    (<- held-rows tuple (profile-rows-held active homes settings))
    (setv counts {"held" 0 "written" 0 "unchanged" 0 "conflicts" 0 "refused" 0 "unobserved" 0})
    (if (not held-rows)
        (do
          (when (not state.no-profile-homes-logged)
            (<- (LogLine :text (+ f"agentd: no profile has a home on node {settings.node-name} — usage not read "
                                       f"(registry {(len homes)} profiles, {(len active)} live rows)"))))
          (setv next (replace next :no-profile-homes-logged True)))
        (do
          (setv next (replace next :no-profile-homes-logged False))
          (<- outcomes tuple (ReadProfileUsage :kind PROFILE-USAGE-KIND
                                               :cache-ttl-seconds settings.profile-observe-seconds))
          (<- by-name dict (usage-by-profile outcomes))
          (<- observed-counts dict (observe-held-profiles settings held-rows by-name counts))
          (setv counts observed-counts)))
    (<- (MetricLine :fields {"metric" "profile-observed"
                                    "node" settings.node-name
                                    "rows" (len active)
                                    "homes" (len held-rows)
                                    "held" (get counts "held")
                                    "written" (get counts "written")
                                    "unchanged" (get counts "unchanged")
                                    "conflicts" (get counts "conflicts")
                                    "refused" (get counts "refused")
                                    "unobserved" (get counts "unobserved")
                                    "atMs" now-ms})))
  next)


(defk observe-held-profiles [settings held-rows by-name counts]
  {:pre [(: settings AgentdSettings) (: held-rows tuple) (: by-name dict) (: counts dict)]
   :post [(: % dict)]}
  "家の在る profile の行ごとに、usage の答えから書く観測を決めて書く(observe-profiles の内側 —
   数えた結果を返す)。"
  (setv counts (dict counts))
  (for [row held-rows]
    (setv name (str (.get row.spec "name" row.resource-id)))
    (<- verdict (| ProfileObservation ProfileUnobserved ProfileNotHeld)
        (profile-observed-of row (.get by-name name) settings.node-name))
    (cond
      (isinstance verdict ProfileNotHeld) None
      (isinstance verdict ProfileUnobserved)
      (do
        (setv (get counts "unobserved") (+ (get counts "unobserved") 1))
        (<- (LogLine :text f"agentd: profile {name} not observed: {verdict.reason}")))
      True
      (do
        (setv (get counts "held") (+ (get counts "held") 1))
        (<- changed bool (profile-observed-changed row verdict.observed))
        (if (not changed)
            (setv (get counts "unchanged") (+ (get counts "unchanged") 1))
            (do
              (<- status dict (profile-status-with-observed row verdict.observed))
              (<- outcome (| Written Conflict Refused) (AcpPutStatus :row row :status status))
              (cond
                (isinstance outcome Written)
                (setv (get counts "written") (+ (get counts "written") 1))
                (isinstance outcome Conflict)
                (do
                  (setv (get counts "conflicts") (+ (get counts "conflicts") 1))
                  (<- (LogLine :text (+ f"agentd: profile {name} observed not written — generation moved "
                                             f"({row.generation} → {outcome.current-generation}); re-reading next period"))))
                True
                (do
                  (setv (get counts "refused") (+ (get counts "refused") 1))
                  (<- (LogLine :text f"agentd: profile {name} observed refused ({outcome.status}): {outcome.error}")))))))))
  counts)


;; ---------------------------------------------------------------------------
;; agent-job の受け: Bound → Running → session を起こす → inputs を送る
;; ---------------------------------------------------------------------------

(defk end-job-now [settings row reason-type reason pending now-ms]
  {:pre [(: settings AgentdSettings) (: row AcpRow) (: reason-type str) (: reason str)
         (: pending tuple) (: now-ms int)]
   :post [(: % bool)]}
  "session の結末なしに job を Ended + condition で閉じる(fresh な行の generation で書く)。
   pending = 手番の途中で判った事実(inputs の欠け等)を condition に添える。
   戻り = Ended の書きが着地したか。"
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (setv target (if (is fresh None) row fresh))
  (<- status dict (status-object-of target))
  (<- condition dict (condition-of reason-type reason))
  (<- ended dict (ended-status-of status None (+ pending #(condition))))
  (<- outcome (| Written Conflict Refused) (AcpPutStatus :row target :status ended))
  (when (not (isinstance outcome Written))
    (<- (LogLine :text f"agentd: could not end job {row.resource-id}: {outcome}")))
  (<- (LogLine :text f"agentd: job {row.resource-id} ended without a session: {reason-type} — {reason}"))
  (isinstance outcome Written))


(defk borrow-lease [plan purpose]
  {:pre [(: plan LaunchPlan) (: purpose str)]
   :post [(: % tuple)]}
  "binding.account が在れば預かり所から借りる。戻り = #(lease-grant-or-None refusal-or-None)
   (借りた札で charter を組むのは judgment.incarnation-charter-of の 1 点)。"
  (if (or (is plan.lease-kind None) (is plan.account None))
      #(None None)
      (do
        (<- lease (| LeaseGrant LeaseRefused)
            (CustodyLeaseBorrow :kind plan.lease-kind :account plan.account :purpose purpose))
        (if (isinstance lease LeaseRefused)
            #(None lease)
            #(lease None)))))


(defk headline-turns-for [subject reason]
  {:pre [(: subject str) (: reason str)]
   :post [(: % HeadlineTurns)]}
  "薄い再開の材料(段 9f lane 9f-4・段 9q・agora-redesign #77): ACP の turn-record の見出しは、記録の service が答えなかった
   拍にだけ読む(AcpTurnHeadlines = kind の全量 list — 実測 2026-09-14: 29,913 行・172 MB・頭の応答 59 秒)。service が
   答えた拍に撃つと、claim は 0.3 秒で着地しているのに送るまで 134 秒かかり、node の lease(TTL 90 秒)が切れて
   Scheduling が Running の行を Pending に戻す(実弾 aj-E61AWHDW…・aj-HV9TMD3D…)。読む前に薄い再開と理由を名乗る。"
  (<- (LogLine :text f"agentd: conversation {subject} rehydrates thinly from ACP headlines — {reason}"))
  (<- records tuple (AcpTurnHeadlines :conversation-id subject))
  (HeadlineTurns :records records :reason reason))


(defk record-turns-for [settings subject]
  {:pre [(: settings AgentdSettings) (: subject str)]
   :post [(: % (| RecordedTurns HeadlineTurns))]}
  "履歴からの再開の手番の材料(段 9f lane 9f-4・設計 §2.4): 会話の記録の service を before=latest から後向きに読み
   (RecordRead — 1 頁 = RECORD-PAGE-MAX-LIMIT)、畳みの上限に届くか会話の最初まで読めたら止める(判断 =
   judgment.record-history-satisfied)。service が配線されていない(弁 off)・届かない・頁の途中で読めなくなった時は
   ACP の見出し(turn-record の行)で**薄く再開する**と名乗る(HeadlineTurns — 本文の無い行を本文として扱わない・型で
   分ける)。見出しを読むのは headline-turns-for の 1 点で、service が答えた拍には読まない(段 9q)。"
  (when (not settings.record-enabled)
    (<- unconfigured HeadlineTurns
        (headline-turns-for subject "record service is not configured (RECORD_SERVICE_URL is unset)"))
    (return unconfigured))
  (setv events [])
  (setv before None)
  (setv complete False)
  (while True
    (<- page (| RecordPage RecordUnread) (RecordRead :conversation-id subject :before before :limit RECORD-PAGE-MAX-LIMIT))
    (when (isinstance page RecordUnread)
      (<- unread HeadlineTurns
          (headline-turns-for subject f"record service read failed ({page.status}: {page.error})"))
      (return unread))
    (setv events (+ (list page.events) events))
    (when (is page.next None)
      (setv complete True)
      (break))
    (<- enough bool (record-history-satisfied (tuple events) settings.rehydrate-history-byte-budget))
    (when enough
      (break))
    (<- advances bool (record-page-advances before page.next))
    (when (or (not advances) (not page.events))
      (<- (LogLine :text f"agentd: record service page for {subject} does not advance (before {before} → next {page.next}); stopping the read"))
      (break))
    (setv before page.next))
  (RecordedTurns :events (tuple events) :complete complete))


(defk history-for [settings subject exclude]
  {:pre [(: settings AgentdSettings) (: subject str) (: exclude tuple)]
   :post [(: % HistoryFold)]}
  "履歴からの再開の「これまでの会話」(R20・段 9f lane 9f-4・段 9q): 手番の本文は会話の記録の service から
   (record-turns-for — 届かなければ ACP の見出しで薄い再開・見出しはその拍にだけ読む)、郵便は ACP の kind message を
   1 度読み(AcpConversationMail — 手番を起こし直す時だけ)、畳みは judgment.rehydrate-history-of の 1 点(上限
   AgentdSettings.rehydrate_history_byte_budget)。"
  (<- source (| RecordedTurns HeadlineTurns) (record-turns-for settings subject))
  (<- messages tuple (AcpConversationMail :conversation-id subject))
  (<- read tuple (mail-bodies-by-ref settings messages))
  (<- fold HistoryFold (rehydrate-history-of subject messages source exclude
                                             settings.rehydrate-history-byte-budget (get read 0)))
  fold)


(defk incarnate [settings plan choice view session-id lease bodies carried job-id subject exclude opener]
  {:pre [(: settings AgentdSettings) (: plan LaunchPlan) (: choice ArmChoice)
         (: view (| SessionView None)) (: session-id str) (: lease (| LeaseGrant None))
         (: bodies tuple) (: carried tuple) (: job-id str) (: subject str) (: exclude tuple)
         (: opener (| str None))]
   :post [(: % (| SessionView SessionRefused))]}
  "起こし方の腕を器に写す: send = 既存の session(眺めはそのまま)/ resume = 会話の前の session
   (choice.source)から同じ家で cold に起こし直す(cache を保つ)/
   launch・rehydrate = charter で起こす(rehydrate は ACP の会話の記録を最初の本文に畳む)。charter は
   judgment.incarnation-charter-of の 1 点で組み(帰属 = session-attribution-of を刻む)、codex の借りた
   auth.json はここで家の中へ書く。"
  (if (and (= choice.arm NEXT-ARM-SEND) (isinstance view SessionView))
      view
      (do
        (setv history "")
        (when (= choice.arm NEXT-ARM-REHYDRATE)
          (<- read-started int (ClockNowMs))
          (<- fold HistoryFold (history-for settings subject exclude))
          (<- read-ended int (ClockNowMs))
          (setv history fold.text)
          (<- (LogLine :text (+ f"agentd: job {job-id} rehydrates conversation {subject} "
                                     (if fold.thin "thinly from ACP headlines " "from the record service ")
                                     f"({fold.kept-turns} turns kept, {fold.dropped-turns} dropped, "
                                     f"{fold.size-bytes} bytes, history read {(- read-ended read-started)} ms)"))))
        (<- attribution dict (session-attribution-of plan job-id subject choice.arm))
        (<- built tuple (incarnation-charter-of plan choice session-id bodies history attribution
                                                settings.backend-kind lease settings.homes-root opener))
        (setv charter (get built 0))
        (setv auth-file (get built 1))
        (when (and (is-not auth-file None) (is-not lease None) (is-not lease.auth-json None))
          (<- (FsWritePrivateText :path auth-file :text lease.auth-json)))
        ;; 段 10 lane 10o(agora-redesign #96): 起こす腕は郵便を 1 手番目の本文に畳む(first-turn-carries-inputs)。
        ;; その郵便の添付も同じ 1 手番に載せる — 綴りは器の Dialogue が組む(agentd は型つきのまま運ぶ)。
        (<- first-turn tuple (first-turn-attachments-of carried))
        (<- charter dict (launch-charter-with-attachments charter first-turn))
        (if (and (= choice.arm NEXT-ARM-RESUME) (is-not choice.source None))
            (do
              (<- params dict (resume-params-of choice.source charter))
              (<- resumed (| SessionView SessionRefused) (SessionResume :params params))
              resumed)
            (do
              (<- launched (| SessionView SessionRefused) (SessionLaunch :params charter))
              launched)))))


(defk start-claimed [settings state row plan choice view session-id now-ms opener]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: plan LaunchPlan)
         (: choice ArmChoice) (: view (| SessionView None)) (: session-id str) (: now-ms int) (: opener (| str None))]
   :post [(: % AgentdState)]}
  "claim が着地した job を起こす: inputs の郵便を読み、札を借り、家の違う温かい session を片付け
   (choice.retire)、腕を器に写す。resume が断られたら judgment.fallback-arm-of の腕(rehydrate)で同じ id の
   session を起こし直す。起こせなければ札を返して LaunchFailed。headless の起こす腕は郵便を 1 手番目の本文に
   畳み(first-turn-carries-inputs)、それ以外は after-start が send する。"
  (setv job-id row.resource-id)
  (setv subject (str (.get row.spec "subject" job-id)))
  (<- mail tuple (mail-of settings row))
  (setv bodies (get mail 0))
  ;; 段 10 lane 10o: 郵便の添付(bodies と同じ並び)。畳む腕は 1 手番目に、送る腕は SessionSend に載る。
  (setv carried (get mail 1))
  (<- exclude tuple (inputs-of row))
  (<- borrowed tuple (borrow-lease plan f"agent-job {job-id}"))
  (setv lease (get borrowed 0))
  (setv refusal (get borrowed 1))
  (if (is-not refusal None)
      (do
        (<- (end-job-now settings row "CredentialUnavailable"
                                f"custody refused ({refusal.status}): {refusal.error}"
                                #() now-ms))
        state)
      (do
        (when (is-not choice.retire None)
          (<- why str (retire-reason-of choice view job-id))
          (<- (retire-sessions #(choice.retire) why)))
        (<- attempted (| SessionView SessionRefused)
            (incarnate settings plan choice view session-id lease bodies carried job-id subject exclude opener))
        (setv outcome attempted)
        (setv used choice)
        (when (isinstance attempted SessionRefused)
          (<- fallback (| ArmChoice None) (fallback-arm-of choice))
          (when (is-not fallback None)
            (<- (LogLine :text (+ f"agentd: resume of session {choice.source} for job {job-id} refused "
                                       f"({attempted.error-code}): {attempted.error}; rehydrating")))
            (<- retried (| SessionView SessionRefused)
                (incarnate settings plan fallback None session-id lease bodies carried job-id subject exclude opener))
            (setv outcome retried)
            (setv used fallback)))
        (if (isinstance outcome SessionRefused)
            (do
              (when (is-not lease None)
                (<- (CustodyLeaseRevoke :lease-id lease.lease-id)))
              (<- (end-job-now settings row "LaunchFailed" outcome.error #() now-ms))
              state)
            (do
              (<- folds bool (first-turn-carries-inputs settings.backend-kind used.arm))
              (<- started AgentdState
                  (after-start settings state row plan outcome lease used.arm now-ms
                               (if folds #() bodies) (if folds #() carried) (get mail 2)))
              started)))))


(defk claim-job [settings state rows row previously-deferred now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: rows tuple) (: row AcpRow)
         (: previously-deferred tuple) (: now-ms int)]
   :post [(: % AgentdState)]}
  "1 つの Bound の行を受ける: 会話の前の session の候補(affinity.predecessor か行から)を器で眺め、
   この job の session を使い回す鍵(session-affinity-key-of)と合わせて起こし方を next-arm-for-job の 1 点で決める(R10 / R20)。
   その前に手番の資格の出所を credential-source-of の 1 点で読み、預かり所を宣言した node で account の無い job は起こさず
   条件 CredentialSourceMissing で閉じる(段 10c・R23)。
   defer(会話の session が手番の途中)なら claim せず次の list へ。それ以外は Running + sessionHandle を
   CAS で書き(負けたら次の list へ)、start-claimed で起こす — 所要を計器に 1 行、turn-record を作り、
   status frame を 1 つ押す。起こす session の id は agentd が鋳造する(MintId — charter の id は読まない)。"
  (<- plan LaunchPlan (launch-plan-of row))
  ;; 段 10c(agora-redesign #80・R23): 手番の資格の出所。預かり所を宣言した node で account の無い job は起こさない
  ;; (Running も sessionHandle も書かず、条件 CredentialSourceMissing で Ended に閉じる — 黙って charter の家へ落ちない)。
  (<- source str (credential-source-of plan settings.custody-declared))
  (when (= source CREDENTIAL-SOURCE-MISSING)
    (<- (end-job-now settings row CONDITION-CREDENTIAL-SOURCE-MISSING
                     (+ f"agent-job {row.resource-id} carries no custody account (status.binding.account) and node "
                        f"{settings.node-name} declares the custody service — the turn's credential is the custody lease only")
                     #() now-ms))
    (return state))
  ;; 段 10 lane 10d 便 2(agora-redesign #85・不変条件 I5): 自分の置き場と違う置き場の口座の job は起こさない。
  ;; 置き場は結ばれた profile の行の名乗り(spec.boundary)を鍵で 1 行読む — 判らない拍は進む(封じた資格はその
  ;; 置き場の worker にしか無く、最後の門は預かり所の redeem)。走行係自身の知識による前段の門で、第 2 の方策点ではない。
  (<- from-custody bool (credential-from-custody source))
  (when from-custody
    (<- profile-key str (profile-key-of plan.profile))
    (<- profile-row (| AcpRow None) (AcpGetRow :key profile-key))
    (<- boundary (| str None) (credential-place-of profile-row))
    (<- mismatched bool (credential-place-mismatch settings.place boundary))
    (when mismatched
      (<- (end-job-now settings row CONDITION-CREDENTIAL-PLACE-MISMATCH
                       (+ f"agent-job {row.resource-id} binds profile {plan.profile} (place {boundary}) but node "
                          f"{settings.node-name} is place {settings.place} — 資格はその置き場の外へ出さない")
                       #() now-ms))
      (return state)))
  (setv job-id row.resource-id)
  (setv subject (str (.get row.spec "subject" job-id)))
  (<- candidate (| str None)
      (warm-candidate-of plan rows subject settings.node-name settings.principal))
  (setv view None)
  (when (is-not candidate None)
    (<- looked (| SessionView None) (SessionGet :session-id candidate))
    (setv view looked))
  (<- home dict (session-affinity-key-of plan))
  (<- effort (| str None) (effort-of-plan plan))
  ;; 段 10f 便 2(agora-redesign #82): 会話の行を鍵で 1 回読む — 追補 3 の手番の env(spec.opener)と自己圧縮の材料
  ;; (status.agent.compactAt)。行が無い(読めない)手番は env の opener を置かず、圧縮もしない(発明しない)。
  (<- conversation-key str (conversation-key-of subject))
  (<- conversation-row (| AcpRow None) (AcpGetRow :key conversation-key))
  (<- opener (| str None) (conversation-opener-of conversation-row))
  ;; 自己圧縮: 会話の宣言 compactAt と、候補の session の直前の手番の文脈の使用率(手番の終わりに測った memory の cache)。
  (setv compact False)
  (when (is-not candidate None)
    (<- compact-at (| int None) (compact-at-of conversation-row))
    (<- percent (| int None) (context-percent-for state candidate))
    (<- due bool (compaction-due compact-at percent))
    (setv compact due))
  (<- choice ArmChoice (next-arm-for-job candidate view home effort compact))
  (when choice.compacts
    (<- (LogLine :text (+ f"agentd: job {job-id} of conversation {subject} starts compacted — the last turn of session "
                          f"{candidate} used {(context-percent-for state candidate)}% of the context window, "
                          f"at or above the conversation's compactAt")))
    (<- (MetricLine :fields {"metric" METRIC-COMPACTIONS-TOTAL "conversation" subject
                             "agentJobId" job-id "sessionId" candidate})))
  (if (= choice.arm NEXT-ARM-DEFER)
      (do
        (when (not-in job-id previously-deferred)
          (<- (LogLine :text (+ f"agentd: claim of job {job-id} deferred — session {candidate} of "
                                     f"conversation {subject} is mid-turn; re-listing"))))
        (replace state :deferred (+ state.deferred #(job-id))))
      (do
        (setv session-id choice.source)
        (when (!= choice.arm NEXT-ARM-SEND)
          (<- minted str (MintId))
          (setv session-id minted))
        (if (not (isinstance session-id str))
            (do
              (<- (end-job-now settings row "LaunchFailed" "no session id could be minted" #() now-ms))
              state)
            (do
              (<- running dict (running-status-of row session-id settings.principal))
              (<- claimed (| Written Conflict Refused) (AcpPutStatus :row row :status running))
              (if (not (isinstance claimed Written))
                  (do
                    (<- (LogLine :text f"agentd: claim of job {job-id} did not land ({claimed}); will re-list"))
                    state)
                  (do
                    (<- started AgentdState
                        (start-claimed settings state row plan choice view session-id now-ms opener))
                    started)))))))


(defk mail-bodies-by-ref [settings messages]
  {:pre [(: settings AgentdSettings) (: messages tuple)]
   :post [(: % tuple)]}
  "段 10f 便 1b(agora-redesign #82): 本文を記録の service に置いた郵便(message.spec.bodyRef)の本文を stream ごとに
   読み集める — 戻り = #(郵便 id → 本文, 郵便 id → 添付の並び)(段 10 lane 10o: 添付の中身も同じ 1 回の読みで拾う)。判断(どの郵便を読むか・出来事から本文)は judgment の message-body-ref-of /
   mail-text-of の 1 点で、ここは RecordReadStream を撃つだけ。service が配線されていない・読めない郵便は表に載せず
   1 行 log する(読み手が missing と名乗る — 本文を発明しない)。手番の入力・履歴の 1 項・割り込みの本文の 3 か所が借りる。"
  (setv fetched {})
  (setv carried-attachments {})
  (for [message messages]
    (<- ref (| tuple None) (message-body-ref-of message.spec))
    ;; 段 10 lane 10o(agora-redesign #96): 添付の見出しも本文と同じ stream(郵便の id)を名指すので、
    ;; 読みは 1 郵便 1 回のまま — 本文が行に在っても添付が在れば読む。
    (<- headlines tuple (message-attachments-of message.spec))
    (setv attachment-ref (if (and (is ref None) headlines)
                             #((get (get headlines 0) 0) (get (get headlines 0) 1))
                             ref))
    (when (is-not attachment-ref None)
      (setv message-id (.get message.spec "id" message.resource-id))
      (if (not settings.record-enabled)
          (<- (LogLine :text f"agentd: message {message-id} keeps its body in the record service, which is not configured; body unavailable"))
          (do
            (<- page (| RecordPage RecordUnread)
                (RecordReadStream :conversation-id (get attachment-ref 0) :stream-id (get attachment-ref 1)))
            (if (isinstance page RecordUnread)
                (<- (LogLine :text f"agentd: body of message {message-id} could not be read from the record service ({page.status}: {page.error})"))
                (do
                  (when (is-not ref None)
                    (<- text (| str None) (mail-text-of page.events))
                    (when (is-not text None)
                      (setv (get fetched message-id) text)))
                  (when headlines
                    (setv carried [])
                    (for [headline headlines]
                      (<- one (| TurnAttachment None) (attachment-of page.events headline))
                      (if (is one None)
                          (<- (LogLine :text f"agentd: attachment {(get headline 2)} of message {message-id} could not be read from the record service"))
                          (.append carried one)))
                    (setv (get carried-attachments message-id) (tuple carried)))))))))
  #(fetched carried-attachments))


(defk mail-of [settings row]
  {:pre [(: settings AgentdSettings) (: row AcpRow)]
   :post [(: % tuple)]}
  "inputs の郵便の本文・添付・見つからなかった id: #(bodies attachments missing)。本文は鍵で 1 行ずつ読む
   (郵便の全量 list を watch の拍ごとに撃たない — R14)。添付は段 10 lane 10o(型つき — 綴りは Dialogue)。"
  (<- inputs tuple (inputs-of row))
  (setv found [])
  (for [input-id inputs]
    (<- key str (message-key-of input-id))
    (<- message (| AcpRow None) (AcpGetRow :key key))
    (when (is-not message None)
      (.append found message)))
  (<- read tuple (mail-bodies-by-ref settings (tuple found)))
  ;; 段 10 lane 10o: 本文と添付は同じ 1 つの判断で inputs の順に並ぶ(並びがずれる第 2 の述語を置かない)。
  (<- triple tuple (message-bodies-of (tuple found) inputs (get read 0) (get read 1)))
  triple)


(defk start-offset-of [view arm]
  {:pre [(: view SessionView) (: arm str)]
   :post [(: % tuple)]}
  "手番の始まりの実況の材料(transcript / events)の offset と path: send(温かい)と resume の
   手番は前の手番の行を entries に混ぜない — 今の file の大きさが始まり。戻り =
   #(path-or-None offset)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (setv path (get source 1))
  (setv start-offset 0)
  (when (and (in arm #{NEXT-ARM-SEND NEXT-ARM-RESUME}) (is-not path None))
    (<- size int (FsFileSize :path path))
    (setv start-offset size))
  #(path start-offset))


(defk after-start [settings state row plan view lease arm now-ms bodies carried missing]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: plan LaunchPlan)
         (: view SessionView) (: lease (| LeaseGrant None)) (: arm str) (: now-ms int)
         (: bodies tuple) (: carried tuple) (: missing tuple)]
   :post [(: % AgentdState)]}
  "手番の始まり(session を起こした後・温かい session ならそのまま): 郵便の本文(bodies — headless の
   起こす腕では空: 本文は起こした prompt に畳んである)を送る(awaiting — 送った本文は owed)・
   見つからなかった id(missing)は condition InputUnavailable・計器・turn-record・status frame・
   in-flight に登記。手番の始まりの offset は送る前の file の大きさ(send / resume)。"
  (setv job-id row.resource-id)
  (setv pending [])
  (<- start tuple (start-offset-of view arm))
  ;; 段 10 lane 10d 便 2 追補 2(実弾 #92): 温かい session への送りは、降りた process を器が
  ;; `--resume` で起こし直すことがある — その起こしに **この手番で借りた札** を載せる
  ;; (行に残った誕生の札で起こすと、更新で回った後は 401 を食う)。判断は judgment の 1 点。
  (<- turn-env dict (turn-session-env-of lease))
  ;; 段 10 lane 10o(agora-redesign #96・依頼者の追補): 添付は型つきのまま器へ渡す(綴りは Dialogue)。
  ;; 器が受けなかった(SessionSend の答えが断りを名乗った)拍は条件 AttachmentIgnored に写す。
  (for [[index body] (enumerate bodies)]
    (setv attachments (if (< index (len carried)) (get carried index) #()))
    (<- ignored (| str None)
        (SessionSend :session-id view.session-id :text body :awaiting True
                     :session-env turn-env
                     :attachments attachments))
    (when (and (isinstance ignored str) ignored)
      (<- condition dict (condition-of CONDITION-ATTACHMENT-IGNORED ignored))
      (.append pending condition)))
  (when missing
    (<- condition dict (condition-of "InputUnavailable"
                                     (+ "messages not found: " (.join ", " missing))))
    (.append pending condition))
  ;; 段 10 lane 10e: この手番で効かない会話の宣言の欄は黙って落とさず条件に(判断は ignored-settings-of の 1 点)。
  (<- ignored tuple (ignored-settings-of plan view arm))
  (for [condition ignored]
    (<- (LogLine :text f"agentd: job {job-id} ignores an agent setting — {(get condition "reason")}"))
    (.append pending condition))
  (<- sent-ms int (ClockNowMs))
  ;; 始点 = 行の生まれの着地(generation 1 の image の landed_at・ns 精度 — 秒の粒度の
  ;; createdAt ではない。判断は birth-ms-of の 1 点・欄が無ければ今日の値)。
  (<- born-ms int (birth-ms-of row state.births))
  (<- (MetricLine :fields {"metric" "agent-job-to-send"
                                  "agentJobId" job-id
                                  "sessionId" view.session-id
                                  "arm" arm
                                  "createdAtMs" born-ms
                                  "sentAtMs" sent-ms
                                  "ms" (- sent-ms born-ms)}))
  (<- job InFlightJob
      (in-flight-job-of row plan view settings.node-name now-ms sent-ms (get start 1) lease
                        (tuple pending)))
  (<- spec dict (turn-record-spec-of job))
  (<- created (| Written Conflict Refused)
      (AcpCreate :namespace AGORA-KINDS-NAMESPACE :kind TURN-RECORD-KIND :resource-id job-id :spec spec))
  ;; 段 9p(agora-redesign #76): 作れなかった結末は腕の状態に写す(pending = 頭が答えない → observe の拍が作り直す /
  ;; given-up = 決定論的 → condition)。log の 1 行は結末の語で(記録なしで黙って進まない)。
  (<- job InFlightJob (record-create-applied job created sent-ms settings.turn-record-create-deadline-seconds))
  (when (not (isinstance created Written))
    (<- (LogLine :text f"agentd: turn-record for job {job-id} was not created ({created}); record-create = {job.record-create}")))
  (<- job InFlightJob (probe-subscribers settings job sent-ms "running"))
  (<- next AgentdState (with-job state job))
  next)


;; ---------------------------------------------------------------------------
;; 実況(TurnDelta): transcript の追記と pane の frame
;; ---------------------------------------------------------------------------

(defk push-frames [settings job frames]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: frames tuple)]
   :post [(: % (| int None))]}
  "frame を中継へ押し、応答の購読者の数を返す(断られたら None)。"
  (<- pushed (| Pushed Refused)
      (AcpStreamPush :owner settings.principal :name job.session-id :frames frames))
  (if (isinstance pushed Pushed)
      pushed.subscribers
      (do
        (<- (LogLine :text f"agentd: stream push for {job.session-id} refused ({pushed.status}): {pushed.error}"))
        None)))


(defk probe-subscribers [settings job now-ms phase]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int) (: phase str)]
   :post [(: % InFlightJob)]}
  "status frame を 1 つ押して購読者の数を読み直し、capture の是非を決める(純関数 1 点)。"
  (<- frame dict (status-frame job.job-id job.delta-seq now-ms phase))
  (<- subscribers (| int None) (push-frames settings job #(frame)))
  (<- verdict str (capture-verdict subscribers))
  (replace job :delta-seq (+ job.delta-seq 1)
               :last-probe-ms now-ms
               :capturing (and (not job.stream-gone) (= verdict "continue"))))


(defk read-stream [source path offset]
  {:pre [(: source str) (: path str) (: offset int)]
   :post [(: % TranscriptChunk)]}
  "実況の材料の追記を offset から読む(events = headless の stdout の行の file・transcript =
   tui の transcript)。どちらも完全な行だけ。"
  (if (= source STREAM-SOURCE-EVENTS)
      (do (<- events TranscriptChunk (SessionEvents :path path :offset offset))
          events)
      (do (<- lines TranscriptChunk (SessionTranscript :path path :offset offset))
          lines)))


(defk append-entries [job entries]
  {:pre [(: job InFlightJob) (: entries tuple)]
   :post [(: % InFlightJob)]}
  "手番の出来事を turn-record の status.entries へ追記する(段 8 lane 4u): 行の最後の image
   (job.record — 無ければ鍵で読む)に CAS(ifGeneration)で post-image を書く。Conflict は行を
   読み直して同じ出来事を 1 度だけ積み直す。断られた・行が無い拍は出来事を pending-entries に
   持ち越す(落とさない)。拾い直した job(seq が 0 から)の採番が行の seq と衝突すれば、行の次から
   振り直す(判断は next-seq-after / renumbered-entries)。"
  (when (not entries)
    (return job))
  (setv record job.record)
  (when (is record None)
    (<- key str (turn-record-key-of job.job-id))
    (<- found (| AcpRow None) (AcpGetRow :key key))
    (setv record found))
  (when (is record None)
    (<- (LogLine :text f"agentd: turn-record of job {job.job-id} is not readable; keeping {(len entries)} entries for the next tick"))
    (return (replace job :pending-entries entries)))
  (<- record-status dict (status-object-of record))
  (<- existing tuple (entries-of-status record-status))
  (<- floor int (next-seq-after existing 0))
  (<- numbered tuple (renumbered-entries entries floor))
  (setv delta-seq (max job.delta-seq (+ (. (get numbered -1) seq) 1)))
  (<- appended dict (turn-record-appended-status record-status numbered))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status appended))
  (when (isinstance wrote Conflict)
    (<- key str (turn-record-key-of job.job-id))
    (<- fresh (| AcpRow None) (AcpGetRow :key key))
    (when (is-not fresh None)
      (setv record fresh)
      (<- record-status dict (status-object-of record))
      (<- existing tuple (entries-of-status record-status))
      (<- floor int (next-seq-after existing 0))
      (<- numbered tuple (renumbered-entries entries floor))
      (setv delta-seq (max job.delta-seq (+ (. (get numbered -1) seq) 1)))
      (<- appended dict (turn-record-appended-status record-status numbered))
      (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status appended))))
  (if (isinstance wrote Written)
      (replace job :record (replace record :generation (+ record.generation 1) :status appended)
                   :pending-entries #()
                   :delta-seq delta-seq)
      (do
        (<- (LogLine :text f"agentd: turn-record append for job {job.job-id} did not land ({wrote}); keeping {(len entries)} entries for the next tick"))
        (replace job :record None :pending-entries entries :delta-seq delta-seq))))


(defk stream-records [settings job source path now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: source str) (: path str)
         (: now-ms int)]
   :post [(: % InFlightJob)]}
  "実況の材料の追記を読み、TurnDelta(text / tool_use / tool_result / usage)を押し、その拍の
   出来事(entries)を turn-record へ追記する(events は text の delta が 1 行ずつ・判断は純関数
   deltas-of / events-to-deltas)。持ち越しの出来事(pending-entries)は先頭に乗る。
   ⚠ 追記の拍は transcript の周期(judgment.record-due — 段 8 lane 4aa): push は events の周期
   (≤ 50 ms)で押すが、記録の書き(= ACP の event 1 つ)を拍ごとにすると journal と画面の糊の
   watch の拍が飽和する。書かない拍の出来事は pending-entries に持ち越す(落とさない)。"
  (<- chunk TranscriptChunk (read-stream source path job.transcript-offset))
  (<- batch DeltaBatch (deltas-of job.agent-type source chunk.text job.job-id job.delta-seq now-ms))
  (setv next (replace job :transcript-offset chunk.offset :delta-seq batch.next-seq))
  ;; 段 10 lane 10n: 材料の中の「model が割り込みを読んだ」証拠を memory に写す(行への書きは settle-interrupts)。
  (<- read tuple (interrupt-reads-of next batch.interrupt-reads))
  (when read
    (setv next (replace next :interrupts-read (+ next.interrupts-read read) :interrupt-marks-dirty True))
    (for [[message-id seq] read]
      (<- (MetricLine :fields {"metric" "agent-job-interrupt-read" "agentJobId" job.job-id
                                      "sessionId" job.session-id "messageId" message-id "seq" seq "atMs" now-ms}))))
  ;; 段 9f lane 9f-2: 本文(切る前)は読んだ拍に spool へ(送るのは拍の終わりの flush-record-spool の 1 点)。
  (when (and settings.record-enabled batch.bodies)
    (<- (spool-record-bodies next batch.bodies)))
  (when batch.frames
    (<- subscribers (| int None) (push-frames settings next batch.frames))
    (<- verdict str (capture-verdict subscribers))
    (setv next (replace next :capturing (and (not next.stream-gone) (= verdict "continue")))))
  (setv carried (+ next.pending-entries batch.entries))
  (<- write-now bool (record-due next now-ms settings))
  (if (and carried write-now)
      (do
        (<- recorded InFlightJob (append-entries next carried))
        (replace recorded :last-record-ms now-ms))
      (replace next :pending-entries carried)))


(defk spool-record-bodies [job bodies]
  {:pre [(: job InFlightJob) (: bodies tuple)]
   :post [(: % int)]}
  "拍で読んだ本文を、会話の記録の service へ送る前に spool へ耐久化する(段 9f lane 9f-2・outbox の書き): 1 batch 1 file。
   送りと消し込みは拍の終わりの flush-record-spool の 1 点。spool の I/O の失敗は log して手番を止めない(ACP の
   turn-record は今日どおり出来事を持つ — 追いつきの差の計器が名乗る)。戻り = spool に置けた batch の数。"
  (<- batches tuple (record-batches-of job bodies))
  (setv spooled 0)
  (try
    (for [batch batches]
      (<- (RecordSpoolPut :batch batch))
      (setv spooled (+ spooled 1)))
    (except [e IO-FAILURES]
      (<- (LogLine :text f"agentd: record spool for job {job.job-id} failed after {spooled} of {(len batches)} batches: {(. (type e) __name__)}: {e}"))))
  spooled)


(defk flush-record-spool [settings state now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int)]
   :post [(: % AgentdState)]}
  "spool の batch を鍵の順に会話の記録の service へ送り、受理(と 409)で消す(段 9f lane 9f-2・outbox の送り)。拍の終わりに
   撃つので、送れている間は出来事を読んだ拍に送られる。送れなかった batch は残して backoff(judgment.record-flush-due)、
   この batch だけの決まった断り(400 / 422)は隔離して理由を名乗り後ろの batch へ進み、系の側の送れなさ(届かない・5xx・札)は
   この拍の残りも撃たない(扱いは judgment.record-append-word-of の 1 点・段 9f lane 9f-8)。計器: 追記の結末
   (agentd_record_append_total)・追いつきの差(agentd_record_lag_seq)・spool の深さ(agentd_record_spool_depth — 変わった時)。"
  (<- listing RecordSpoolListing (RecordSpoolList))
  (for [name listing.unreadable]
    (<- (LogLine :text f"agentd: record spool file {name} is unreadable; left in place")))
  (setv remaining (+ (len listing.batches) (len listing.unreadable)))
  (setv failed False)
  (for [batch listing.batches]
    (setv stream-id batch.stream.stream-id)
    (<- outcome (| RecordAppended RecordConflicted RecordUnsent) (RecordAppend :batch batch))
    (<- word str (record-append-word-of outcome))
    (<- (MetricLine :fields {"metric" METRIC-RECORD-APPEND-TOTAL "outcome" word
                                    "conversationId" batch.conversation-id "streamId" stream-id
                                    "events" (len batch.events)}))
    (when (in word #(RECORD-APPEND-OK RECORD-APPEND-CONFLICT))
      (<- (RecordSpoolRemove :spool-key batch.spool-key))
      (setv remaining (- remaining 1)))
    (when (isinstance outcome RecordAppended)
      (<- lag (| int None) (record-lag-of state.jobs stream-id outcome.highest-producer-seq))
      (when (is-not lag None)
        (<- (MetricLine :fields {"metric" METRIC-RECORD-LAG-SEQ "conversationId" batch.conversation-id
                                        "streamId" stream-id "lag" lag})))
      (<- marked AgentdState (mark-recorded state batch.conversation-id stream-id outcome.highest-producer-seq))
      (setv state marked))
    (when (isinstance outcome RecordConflicted)
      (<- (LogLine :text f"agentd: record append for {stream-id} conflicted (same key, different body); dropped from the spool: {outcome.conflicts}")))
    (when (isinstance outcome RecordUnsent)
      (if (= word RECORD-APPEND-GIVEN-UP)
          (do
            ;; この batch だけの決まった断り(段 9f lane 9f-8): 隔離して理由を名乗り、後ろの batch へ進む(先頭を塞がない)。
            (setv reason f"record service refused the body batch {batch.spool-key} ({outcome.status}: {outcome.error}); moved to the given-up spool")
            (<- (RecordSpoolGiveUp :spool-key batch.spool-key :reason reason))
            (setv remaining (- remaining 1))
            (<- (LogLine :text f"agentd: {reason}"))
            (<- noted AgentdState (record-given-up-noted state stream-id reason))
            (setv state noted))
          (do
            (setv failed True)
            (<- (LogLine :text f"agentd: record append for {stream-id} was not accepted ({outcome.status}: {outcome.error}); kept in the spool")))))
    (when (= word RECORD-APPEND-ERROR)
      (break)))
  (when (!= remaining state.record-spool-depth)
    (<- (MetricLine :fields {"metric" METRIC-RECORD-SPOOL-DEPTH "depth" remaining})))
  (replace state :record-backoff-ms (if failed now-ms None) :record-spool-depth remaining))


(defk mark-recorded [state conversation-id stream-id highest]
  {:pre [(: state AgentdState) (: conversation-id str) (: stream-id str) (: highest int)]
   :post [(: % AgentdState)]}
  "service が本文を受理した答えを turn-record の行へ写す(段 9f lane 9f-4・設計 §2.2): status.recordRef = 本文の在処
   (judgment.record-ref-of)・status.recordedSeq = 受理済みの最大 producerSeq(judgment.turn-record-recorded-status — 進む時
   だけ・後ろへ戻さない)。行は走っている手番の memory の image(InFlightJob.record)があればそれに CAS(次の追記も
   その image から続く)、無ければ鍵で読む(手番の終わりの後に届いた受理も行に写る)。Conflict は image を捨てて次の拍
   (追記の腕が読み直す)。"
  (<- job-id str (record-stream-job-of stream-id))
  (<- key str (turn-record-key-of job-id))
  (<- ref str (record-ref-of conversation-id stream-id))
  (setv held None)
  (for [job state.jobs]
    (when (and (= job.job-id job-id) (is-not job.record None))
      (setv held job)))
  (setv record (if (is held None) None held.record))
  (when (is record None)
    (<- found (| AcpRow None) (AcpGetRow :key key))
    (setv record found))
  (when (is record None)
    (<- (LogLine :text f"agentd: turn-record of job {job-id} is not readable; recordedSeq {highest} not written"))
    (return state))
  (<- record-status dict (status-object-of record))
  (<- recorded (| dict None) (turn-record-recorded-status record-status ref highest))
  (when (is recorded None)
    (return state))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status recorded))
  (when (not (isinstance wrote Written))
    (<- (LogLine :text f"agentd: recordedSeq {highest} for job {job-id} did not land ({wrote}); next flush retries")))
  (when (is held None)
    (return state))
  (setv image (if (isinstance wrote Written)
                  (replace record :generation (+ record.generation 1) :status recorded)
                  None))
  (<- next AgentdState (with-job state (replace held :record image)))
  next)


(defk capture-frame [settings job now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "pane の断面を 1 枚押す(購読者が居る間だけ呼ばれる)。答えが gone(pane も server も無い)
   なら実況の終わり — 例外ではない(R8): capture を止め、器の終端を待って記録の腕へ。"
  (<- outcome (| CaptureFrame CaptureGone)
      (SessionCapture :session-id job.session-id :lines settings.frame-lines))
  (if (isinstance outcome CaptureGone)
      (do
        (<- (LogLine :text (+ f"agentd: stream of job {job.job-id} is gone ({outcome.reason}); "
                                   "waiting for the session to end")))
        (replace job :capturing False :stream-gone True :last-frame-ms now-ms))
      (do
        (<- lines tuple (frame-lines-of outcome.text))
        (<- frame dict (pane-frame job.job-id job.delta-seq now-ms lines))
        (<- subscribers (| int None) (push-frames settings job #(frame)))
        (<- verdict str (capture-verdict subscribers))
        (replace job :delta-seq (+ job.delta-seq 1)
                     :last-frame-ms now-ms
                     :capturing (= verdict "continue")))))


(defk ensure-turn-record [settings job now-ms force]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: now-ms int) (: force bool)]
   :post [(: % InFlightJob)]}
  "turn-record の行を作り直す腕(段 9p・agora-redesign #76): 腕が pending(頭が答えず作れていない)の job に、
   record_retry_seconds の周期(record-create-due)か force(手番の終わり — 周期に依らず最後に 1 度)で create を撃ち直し、
   結末を record-create-applied の 1 点で写す。created / given-up の job は 1 bit も触らない。作れた拍の出来事は
   pending-entries に持ち越されているので、次の追記(append-entries)が鍵から行を読んで乗せる。"
  (if (!= job.record-create RECORD-CREATE-PENDING)
      job
      (do
        (<- due-now bool (record-create-due job now-ms settings))
        (if (not (or due-now force))
            job
            (do
              (<- spec dict (turn-record-spec-of job))
              (<- created (| Written Conflict Refused)
                  (AcpCreate :namespace AGORA-KINDS-NAMESPACE :kind TURN-RECORD-KIND :resource-id job.job-id :spec spec))
              (<- applied InFlightJob
                  (record-create-applied job created now-ms settings.turn-record-create-deadline-seconds))
              (cond
                (= applied.record-create RECORD-CREATE-PENDING)
                (<- (LogLine :text f"agentd: turn-record for job {job.job-id} still not created ({created}); will retry"))
                (= applied.record-create RECORD-CREATE-GIVEN-UP)
                (<- (LogLine :text f"agentd: turn-record for job {job.job-id} given up ({created}); condition RecordUnavailable"))
                True
                (<- (LogLine :text f"agentd: turn-record for job {job.job-id} created after retry ({(len job.pending-entries)} entries carried)")))
              applied)))))


(defk record-interrupt-marks [job]
  {:pre [(: job InFlightJob)]
   :post [(: % InFlightJob)]}
  "読んだ / 止めた印を行へ写す(段 10 lane 10n): 鍵で読み直した行に CAS で interruptsRead / interruptsEscalated を
   足す(1 回の書き・append-only — 判断は interrupt-marks-status-of)。Conflict は 1 度読み直して撃ち直す。着地しなければ
   dirty のまま次の拍が撃ち直す。"
  (<- fresh (| AcpRow None) (AcpGetRow :key job.job-key))
  (when (is fresh None)
    (<- (LogLine :text f"agentd: agent-job {job.job-id} vanished before its interrupt marks could be recorded"))
    (return (replace job :interrupt-marks-dirty False)))
  (<- status dict (status-object-of fresh))
  (<- marked dict (interrupt-marks-status-of status job.interrupts-read job.interrupts-escalated))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row fresh :status marked))
  (when (isinstance wrote Conflict)
    (<- again (| AcpRow None) (AcpGetRow :key job.job-key))
    (when (is-not again None)
      (<- status-again dict (status-object-of again))
      (<- marked-again dict (interrupt-marks-status-of status-again job.interrupts-read job.interrupts-escalated))
      (<- wrote (| Written Conflict Refused) (AcpPutStatus :row again :status marked-again))))
  (when (not (isinstance wrote Written))
    (<- (LogLine :text f"agentd: interrupt marks of job {job.job-id} not recorded ({wrote}); recording again next tick")))
  (replace job :interrupt-marks-dirty (not (isinstance wrote Written))))


(defk settle-interrupts [job now-ms]
  {:pre [(: job InFlightJob) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "割り込みの約束の拍(段 10 lane 10n): 注入から期限(charter の値)が経って読んだ証拠の無い id が在れば停止の合図
   (SessionEscalate)を 1 度出し、未読の id 全部に止めた印(時刻)。器が断った(出す物が無い — 既に読んでいて証拠が
   次の材料に在る・手番が終わった)拍は印を付けず log 1 行(次の拍が判断し直す)。印(読んだ / 止めた)のうち行へまだ
   書けていないものが在れば record-interrupt-marks。"
  (<- due tuple (interrupts-due-for-escalation job now-ms))
  (setv current job)
  (when due
    (<- outcome (| Escalated SessionRefused) (SessionEscalate :session-id job.session-id))
    (if (isinstance outcome Escalated)
        (do
          (setv marks (tuple (lfor message-id due #(message-id now-ms))))
          (setv current (replace current :interrupts-escalated (+ current.interrupts-escalated marks)
                                         :interrupt-marks-dirty True))
          (<- (LogLine :text (+ f"agentd: job {job.job-id}: interrupt(s) {(.join ", " due)} not read by the model within "
                                     f"{job.interrupt-escalation-seconds} s; stop signal sent")))
          (for [message-id due]
            (<- (MetricLine :fields {"metric" "agent-job-interrupt-escalated" "agentJobId" job.job-id
                                            "sessionId" job.session-id "messageId" message-id "atMs" now-ms}))))
        (<- (LogLine :text (+ f"agentd: job {job.job-id}: stop signal for interrupt(s) {(.join ", " due)} not accepted "
                                   f"by the session ({outcome.error}); judging again next tick")))))
  (when current.interrupt-marks-dirty
    (<- current InFlightJob (record-interrupt-marks current)))
  current)


(defk stream-job [settings job view now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: view SessionView) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "走っている 1 つの job の実況の拍: 材料(transcript / events)の追記 → frame(tui で
   購読者が居れば)→ 購読の読み直し(止まっていれば)→ 札の延長。実況が終わった
   (stream-gone)job は frame も読み直しも撃たない。headless(events)の器に pane は
   無いので frame の capture は撃たない(実況は events の行そのもの)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (setv path (get source 1))
  ;; 段 9p: 行を作れていない job は先に作り直す(周期は record_retry_seconds)— この拍の出来事が乗る先を用意する。
  (<- current InFlightJob (ensure-turn-record settings job now-ms False))
  (when (is-not path None)
    (<- current InFlightJob (stream-records settings current (get source 0) path now-ms)))
  ;; 段 10 lane 10n: 割り込みの約束(期限の判断・停止の合図・印の書き)— 材料を読んだ後の同じ拍。
  (when (or current.interrupts-injected current.interrupt-marks-dirty)
    (<- current InFlightJob (settle-interrupts current now-ms)))
  (setv frames-possible (!= (get source 0) STREAM-SOURCE-EVENTS))
  (<- frame-due bool (due current.last-frame-ms now-ms settings.frame-interval-seconds))
  (when (and frames-possible current.capturing frame-due)
    (<- current InFlightJob (capture-frame settings current now-ms)))
  (<- probe-due bool (due current.last-probe-ms now-ms settings.subscriber-recheck-seconds))
  (when (and frames-possible (not current.capturing) (not current.stream-gone) probe-due)
    (<- current InFlightJob (probe-subscribers settings current now-ms "running")))
  (<- renew bool (lease-renew-due current now-ms settings))
  (when (and renew (is-not current.lease-kind None) (is-not current.lease-account None))
    (<- lease (| LeaseGrant LeaseRefused)
        (CustodyLeaseBorrow :kind current.lease-kind :account current.lease-account
                            :purpose f"agent-job {current.job-id} (renew)"))
    (if (isinstance lease LeaseGrant)
        (setv current (replace current :lease-id lease.lease-id
                                       :lease-hold-ms lease.hold-expires-at-ms))
        (<- (LogLine :text f"agentd: lease renew for job {current.job-id} refused ({lease.status}): {lease.error}"))))
  current)


;; ---------------------------------------------------------------------------
;; 記録(turn-record)と手番の終わり
;; ---------------------------------------------------------------------------

(defk end-turn-record [job-id usage entries]
  {:pre [(: job-id str) (: usage (| dict None)) (: entries tuple)]
   :post [(: % bool)]}
  "turn-record を ended に(usage・残りの entries を行の entries に追記)。行は鍵で読み直す
   (正本は行)。戻り = 行が在って書けたか(無ければ False — 受けた直後に落ちた job には記録が
   無いのが普通なので、ここでは log しない)。"
  (<- key str (turn-record-key-of job-id))
  (<- record (| AcpRow None) (AcpGetRow :key key))
  (if (is record None)
      False
      (do
        (<- record-status dict (status-object-of record))
        (<- ended-record dict (turn-record-ended-status record-status usage entries))
        (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status ended-record))
        (when (not (isinstance wrote Written))
          (<- (LogLine :text f"agentd: turn-record of job {job-id} not ended ({wrote})")))
        (isinstance wrote Written))))


(defk drain-stream [settings job source path now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: source (| str None))
         (: path (| str None)) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "手番の終わりの拍: まだ読んでいない材料の残り(最後の本文・result)を同じ拍で読み、実況の
   frame を押し、出来事を追記する(stream-records と同じ 1 点)。材料が無ければそのまま。"
  (if (or (is path None) (is source None))
      job
      (do
        (<- drained InFlightJob (stream-records settings job source path now-ms))
        drained)))


(defk turn-batch-of [job source path now-ms]
  {:pre [(: job InFlightJob) (: source (| str None)) (: path (| str None)) (: now-ms int)]
   :post [(: % DeltaBatch)]}
  "手番の始まりから今までの材料(transcript / events)を読み直し、usage を組む(frame は押さない
   — 実況は拍ごとに押した。entries も使わない — 出来事は拍ごとに行へ追記した側が正本で、ここは
   message ごとの重複を跨いで数える usage のためだけ)。材料が無ければ空。"
  (if (or (is path None) (is source None))
      (DeltaBatch :frames #() :entries #() :usage None :next-seq 0 :model None)
      (do
        (<- chunk TranscriptChunk (read-stream source path job.start-offset))
        (<- whole DeltaBatch (deltas-of job.agent-type source chunk.text job.job-id 0 now-ms))
        whole)))


(defk finalize-job [settings state job view source path step now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view SessionView) (: source (| str None)) (: path (| str None)) (: step str)
         (: now-ms int)]
   :post [(: % AgentdState)]}
  "手番の終わり(記録の腕): transcript から entries と usage を組み turn-record を ended に、
   agent-job を Ended(result / conditions)に、status frame ended を押し、札を返す。
   turn-end(温かい session の手番の終わり)は session を生かしたまま。record-end(器が終端)
   で器が multi_turn なら、host の掃き取りの対象外なので agentd が片付ける。
   session-lost(段 10 lane 10h: 行は非終端だが host の観測で backend が死んでいる)は結末を
   器から読まず condition SessionLost(judgment.session-lost-condition-of — session・pid・時刻)で
   Ended・result なし。session は片付けない(host の monitor が終端に倒す — 終端の cause は
   host の観測の方が詳しい)。"
  (setv outcome None)
  (if (= step JOB-STEP-SESSION-LOST)
      (do
        (<- lost dict (session-lost-condition-of view now-ms))
        (setv lost-reason (get lost "reason"))
        (<- (LogLine :text f"agentd: job {job.job-id} lost its session — {lost-reason}"))
        (setv outcome (JobOutcome :ended True :result None :conditions #(lost))))
      (do
        (<- read JobOutcome (job-outcome-of view))
        (setv outcome read)))
  (<- settled AgentdState (settle-record settings state job view source path outcome step now-ms))
  settled)


(defk settle-record [settings state job view source path outcome step now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view (| SessionView None)) (: source (| str None)) (: path (| str None))
         (: outcome JobOutcome) (: step str) (: now-ms int)]
   :post [(: % AgentdState)]}
  "記録の腕の本体(finalize-job と close-jobs-for-stop の共有 — 結末は呼び手が決める): 最後の材料を読んで
   turn-record を ended に、agent-job を Ended(result / conditions)に、status frame ended を押し、札を返し、
   memory から外す。record-end で器が multi_turn なら agentd が片付ける(他の step は session を残す)。"
  ;; 段 9p: 行を作れていないまま終わりに来た job は周期に依らず最後に 1 度作り直す(記録なしで終わらない —
  ;; それでも作れなければ given-up の condition が pending-conditions に乗り、下の Ended の書きが運ぶ)。
  (<- ensured InFlightJob (ensure-turn-record settings job now-ms True))
  ;; 最後の材料(まだ読んでいない本文・result)を同じ拍で読み、実況を押し、出来事を追記する。
  (<- drained InFlightJob (drain-stream settings ensured source path now-ms))
  (<- batch DeltaBatch (turn-batch-of drained source path now-ms))
  ;; 段 10f 便 2: 手番の終わりの文脈の使用率(材料の末尾の実測)を session の cache に置く — 次の手番の claim が
  ;; 会話の宣言 compactAt と比べる材料。測れなかった手番(window が無い)は消す(古い値で圧縮しない)。
  (<- percent (| int None) (context-percent-of batch.context))
  (<- measured AgentdState (with-context-percent state job.session-id percent))
  ;; turn-record → ended(追記できずに持ち越した出来事があれば最後の書きに乗せる)
  (<- recorded bool (end-turn-record drained.job-id batch.usage drained.pending-entries))
  (when (not recorded)
    (<- (LogLine :text f"agentd: turn-record for job {job.job-id} is missing at turn end")))
  ;; agent-job → Ended
  (<- fresh (| AcpRow None) (AcpGetRow :key job.job-key))
  (if (is fresh None)
      (<- (LogLine :text f"agentd: agent-job {job.job-id} vanished before Ended"))
      (do
        (<- job-status dict (status-object-of fresh))
        ;; conditions は最新の写し(drained — 段 9p の given-up の RecordUnavailable を含む)から。
        (<- ended dict (ended-status-of job-status outcome.result
                                        (+ drained.pending-conditions outcome.conditions)))
        (<- wrote-job (| Written Conflict Refused) (AcpPutStatus :row fresh :status ended))
        (when (not (isinstance wrote-job Written))
          (<- (LogLine :text f"agentd: agent-job {job.job-id} not ended ({wrote-job})")))))
  ;; 実況の終わりの印(seq は最後の材料の読みの続き)
  (<- frame dict (status-frame job.job-id drained.delta-seq now-ms "ended"))
  (<- (push-frames settings job #(frame)))
  ;; 札を返す
  (when (is-not job.lease-id None)
    (<- (CustodyLeaseRevoke :lease-id job.lease-id)))
  (<- (MetricLine :fields {"metric" "agent-job-turn"
                                  "agentJobId" job.job-id
                                  "sessionId" job.session-id
                                  "status" (if (isinstance view SessionView) view.status "missing")
                                  "step" step
                                  "ms" (- now-ms job.started-ms)}))
  (when (and (= step JOB-STEP-RECORD-END) (isinstance view SessionView))
    (<- retire bool (cleanup-after-end view))
    (when retire
      (<- (retire-sessions #(job.session-id) f"session {view.status} at the end of job {job.job-id}"))))
  (<- next AgentdState (without-job measured job.job-id))
  next)


;; ---------------------------------------------------------------------------
;; agentd の停止(段 10 lane 10h 便 2): 走っている手番を黙って残さない
;; ---------------------------------------------------------------------------

(defk close-jobs-for-stop [settings state now-ms reason]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: now-ms int) (: reason str)]
   :post [(: % AgentdState)]}
  "agentd の停止(TERM)の前の腕(段 10 lane 10h 便 2・agora-redesign #84): headless の子 process は host と共に
   降りるので、memory の走っている job は手番の途中のまま残せない — job ごとに記録の腕(残りの材料を読んで
   turn-record を ended)と条件 AgentdRestart(judgment.restart-condition-of の 1 点 — node・理由・session・時刻)で
   Ended にし、status frame ended を押し、札を返す(settle-record — finalize-job と同じ本体)。session は片付けない
   (host が降ろし、行は host の停止の腕が倒す)。黙って残さない: job ごとに log 1 行。戻り = jobs を空にした state。"
  (setv current state)
  (for [job (list state.jobs)]
    (<- view (| SessionView None) (SessionGet :session-id job.session-id))
    (setv source None)
    (setv path None)
    (when (isinstance view SessionView)
      (<- canon str (FsCanonicalPath :path view.work-dir))
      (<- found tuple (stream-source-of view canon))
      (setv source (get found 0))
      (setv path (get found 1)))
    (<- condition dict (restart-condition-of job settings.node-name reason now-ms))
    (setv condition-reason (get condition "reason"))
    (<- (LogLine :text f"agentd: job {job.job-id} closed for the stop of agentd — {condition-reason}"))
    (<- settled AgentdState
        (settle-record settings current job view source path
                       (JobOutcome :ended True :result None :conditions #(condition))
                       "agentd-stop" now-ms))
    (setv current settled))
  current)


(defk fail-missing-arm [settings job-key job-id pending lease-id now-ms]
  {:pre [(: settings AgentdSettings) (: job-key str) (: job-id str) (: pending tuple)
         (: lease-id (| str None)) (: now-ms int)]
   :post [(: % bool)]}
  "器に session が無い job の腕: 記録が在れば ended に、job は SessionFailed で Ended、
   借りていた札は返す。戻り = Ended の書きが着地したか。"
  (<- (end-turn-record job-id None #()))
  (<- fresh (| AcpRow None) (AcpGetRow :key job-key))
  (setv landed False)
  (if (is fresh None)
      (<- (LogLine :text f"agentd: agent-job {job-id} vanished before Ended"))
      (<- landed bool (end-job-now settings fresh "SessionFailed"
                                   "session is not registered in the host" pending now-ms)))
  (when (is-not lease-id None)
    (<- (CustodyLeaseRevoke :lease-id lease-id)))
  landed)


(defk settle-known [settings state job view step now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob)
         (: view (| SessionView None)) (: step str) (: now-ms int)]
   :post [(: % AgentdState)]}
  "job-step-of の答えを腕に写す: fail-missing → 記録と SessionFailed で閉じる /
   record-end・turn-end・session-lost → 記録の腕(finalize)/ observe → memory に置く(観測は次の拍)。"
  (cond
    (= step JOB-STEP-FAIL-MISSING)
    (do
      (<- (fail-missing-arm settings job.job-key job.job-id job.pending-conditions job.lease-id now-ms))
      (<- dropped AgentdState (without-job state job.job-id))
      dropped)
    (and (in step #{JOB-STEP-RECORD-END JOB-STEP-TURN-END JOB-STEP-SESSION-LOST}) (isinstance view SessionView))
    (do
      (<- canon str (FsCanonicalPath :path view.work-dir))
      (<- source tuple (stream-source-of view canon))
      (<- finished AgentdState
          (finalize-job settings state job view (get source 0) (get source 1) step now-ms))
      finished)
    True
    (do
      (<- kept AgentdState (with-job state job))
      kept)))


(defk progressed-of [job view]
  {:pre [(: job InFlightJob) (: view SessionView)]
   :post [(: % bool)]}
  "この手番の記録が進んだか(送った本文が届いて手番が始まった証拠)。記録の path を引けない
   器(材料の欠け)は進みを読めないので True(host の判定だけを信じる)。"
  (<- canon str (FsCanonicalPath :path view.work-dir))
  (<- source tuple (stream-source-of view canon))
  (or (is (get source 1) None) (> job.transcript-offset job.start-offset)))


(defk observe-job [settings state job now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob) (: now-ms int)]
   :post [(: % AgentdState)]}
  "走っている 1 つの job の拍: 器の眺め → 次の 1 手(純関数 1 点)。observe なら実況を回し、
   その拍で実況が終わった(capture が gone)なら器を読み直して同じ拍で腕を決める。
   record-end / turn-end / fail-missing は実況を撃たずに記録の腕へ(片付いた pane を
   capture しない)。"
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (setv progressed True)
  (when (isinstance view SessionView)
    (<- moved bool (progressed-of job view))
    (setv progressed moved))
  (<- step str (job-step-of view job.turn-floor-ms progressed))
  (if (and (= step JOB-STEP-OBSERVE) (isinstance view SessionView))
      (do
        (<- current InFlightJob (stream-job settings job view now-ms))
        (if (and current.stream-gone (not job.stream-gone))
            (do
              (<- again (| SessionView None) (SessionGet :session-id job.session-id))
              (<- step-again str (job-step-of again job.turn-floor-ms progressed))
              (<- settled AgentdState (settle-known settings state current again step-again now-ms))
              settled)
            (do
              (<- kept AgentdState (with-job state current))
              kept)))
      (do
        (<- settled AgentdState (settle-known settings state job view step now-ms))
        settled)))


;; ---------------------------------------------------------------------------
;; 自分の Running の拾い直し(再起動後・memory に無い行)
;; ---------------------------------------------------------------------------

(defk recover-job [settings state row now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: row AcpRow) (: now-ms int)]
   :post [(: % AgentdState)]}
  "自分が持つ Running の行(memory に無い)の続きを行と器の現況から決める(R7): 器が無ければ
   記録と SessionFailed で閉じる / 終端なら記録の腕だけ / 行は非終端でも host の観測で backend が
   死んでいれば(段 10 lane 10h — job-step-of の session-lost)記録の腕と SessionLost で閉じる /
   走っていれば札を借り直して InFlightJob を行から組み、観測を続ける。launch も send もし直さない。
   生死は status の語ではなく眺めの backend_alive(host の観測)で決める。"
  (<- session-id (| str None) (session-id-of-handle row))
  (if (is session-id None)
      (do
        (<- (fail-missing-arm settings row.key row.resource-id #() None now-ms))
        state)
      (do
        (<- view (| SessionView None) (SessionGet :session-id session-id))
        ;; 拾い直しは記録の進みを知らない(下限 = 行の createdAt・進み = host の判定だけ)。
        (<- step str (job-step-of view row.created-at-ms True))
        (if (not (isinstance view SessionView))
            (do
              (<- (fail-missing-arm settings row.key row.resource-id #() None now-ms))
              state)
            (do
              (<- plan LaunchPlan (launch-plan-of row))
              (setv lease None)
              (when (= step JOB-STEP-OBSERVE)
                (<- borrowed tuple (borrow-lease plan f"agent-job {row.resource-id} (recovered)"))
                (setv lease (get borrowed 0))
                (setv refusal (get borrowed 1))
                (when (is-not refusal None)
                  (<- (LogLine :text (+ f"agentd: lease for recovered job {row.resource-id} refused "
                                             f"({refusal.status}): {refusal.error}; observing without it")))))
              (<- recovered-arm str (recovered-arm-of plan view row.resource-id))
              (<- start tuple (start-offset-of view recovered-arm))
              (<- job InFlightJob
                  (in-flight-job-of row plan view settings.node-name row.created-at-ms
                                    row.created-at-ms (get start 1) lease #()))
              ;; 段 9f lane 9f-2: 本文の stream の拾い直しの番と採番の下限は turn-record の行から(judgment.recovered-record-of)。
              (<- record-key str (turn-record-key-of row.resource-id))
              (<- record-row (| AcpRow None) (AcpGetRow :key record-key))
              (<- resumed tuple (recovered-record-of record-row))
              (setv job (replace job :record-attempt (get resumed 0) :delta-seq (get resumed 1)))
              ;; 段 10 lane 10n: 渡したが読まれていない割り込みは拾い直した時刻から期限を数える(行の印は写す)。
              (<- job InFlightJob (recovered-interrupts-of job row now-ms))
              (<- (LogLine :text f"agentd: recovered running job {row.resource-id} from its row ({step})"))
              (<- settled AgentdState (settle-known settings state job view step now-ms))
              settled)))))


;; ---------------------------------------------------------------------------
;; 1 tick
;; ---------------------------------------------------------------------------

(defk interrupt-job [settings state job row now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: job InFlightJob) (: row AcpRow)
         (: now-ms int)]
   :post [(: % AgentdState)]}
  "取り下げられた自分の走っている job の腕(agora-redesign #37): 手番の途中なら
   session.interrupt(判断は interrupt-arm-for の 1 点 — headless = SIGINT / turn/interrupt・
   tmux = Escape)、記録の腕(ここまでの entries と usage で turn-record を ended)、agent-job に
   condition Interrupted(phase は書かない — Withdrawn のまま・書き手は作った側)、status frame
   ended、札を返し、観測をやめる。session は残す(温かい — 次の手番は send)。"
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (<- arm str (interrupt-arm-for job view))
  (when (= arm INTERRUPT-ARM-INTERRUPT)
    (<- (SessionInterrupt :session-id job.session-id)))
  (setv source None)
  (setv path None)
  (when (isinstance view SessionView)
    (<- canon str (FsCanonicalPath :path view.work-dir))
    (<- found tuple (stream-source-of view canon))
    (setv source (get found 0))
    (setv path (get found 1)))
  (<- drained InFlightJob (drain-stream settings job source path now-ms))
  (<- batch DeltaBatch (turn-batch-of drained source path now-ms))
  ;; 段 10f 便 2: 割り込みで終わる手番も文脈の実測を session の cache に置く(settle-record と同じ 1 点の判断)。
  (<- percent (| int None) (context-percent-of batch.context))
  (<- measured AgentdState (with-context-percent state job.session-id percent))
  (<- (end-turn-record drained.job-id batch.usage drained.pending-entries))
  (<- fresh (| AcpRow None) (AcpGetRow :key row.key))
  (setv target (if (is fresh None) row fresh))
  (<- status dict (status-object-of target))
  (<- interrupted dict (interrupted-status-of status))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row target :status interrupted))
  (when (not (isinstance wrote Written))
    (<- (LogLine :text f"agentd: Interrupted condition of job {job.job-id} not written ({wrote})")))
  (<- frame dict (status-frame job.job-id drained.delta-seq now-ms "ended"))
  (<- (push-frames settings job #(frame)))
  (when (is-not job.lease-id None)
    (<- (CustodyLeaseRevoke :lease-id job.lease-id)))
  (<- (LogLine :text (+ f"agentd: job {job.job-id} withdrawn ({arm}); turn-record ended, "
                             f"session {job.session-id} kept")))
  (<- dropped AgentdState (without-job measured job.job-id))
  dropped)


(defk withdraw-jobs [settings state rows now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: rows tuple) (: now-ms int)]
   :post [(: % AgentdState)]}
  "取り下げ(phase Withdrawn — 書き手は作った側)の行のうち自分が観測している job を
   interrupt-job の腕へ。処理した job の id を memory に置いて同じ行に撃ち直さない。
   session は片付けない(取り下げは中断の合図 — 温かい session の寿命は sessions-to-retire)。
   agent-job の phase は書かない。"
  (<- withdrawn tuple (withdrawn-rows-of rows settings.node-name settings.principal))
  (setv current state)
  (for [row withdrawn]
    (setv job-id row.resource-id)
    (when (not-in job-id current.retired)
      (for [job (list current.jobs)]
        (when (= job.job-id job-id)
          (<- interrupted AgentdState (interrupt-job settings current job row now-ms))
          (setv current interrupted)))
      (setv current (replace current :retired (+ current.retired #(job-id))))))
  current)


(defk delivered-status-with-undeclared [job status ids]
  {:pre [(: job InFlightJob) (: status dict) (: ids tuple)]
   :post [(: % dict)]}
  "渡した印の status(interrupts-delivered-status-of)に、期限の宣言が無い job なら条件 InterruptEscalationUndeclared を
   同じ 1 回の書きで足す(段 10 lane 10n — 注入だけで停止の合図は出さないことを行に名乗る)。"
  (<- delivered dict (interrupts-delivered-status-of status ids))
  (if (is job.interrupt-escalation-seconds None)
      (do
        (<- reason str (interrupt-escalation-undeclared-reason job))
        (<- with-condition dict (status-with-condition delivered CONDITION-INTERRUPT-ESCALATION-UNDECLARED reason))
        with-condition)
      delivered))


(defk record-interrupts-delivered [job ids]
  {:pre [(: job InFlightJob) (: ids tuple)]
   :post [(: % bool)]}
  "渡した割り込みを行へ写す(段 8 lane 4x): 鍵で読み直した行に CAS で interrupts から消し
   interruptsDelivered へ足す(1 回の書き・期限の宣言が無い job は条件 InterruptEscalationUndeclared も同じ書き —
   段 10 lane 10n)。Conflict は 1 度だけ読み直して撃ち直す。戻り =
   着地したか(しなければ memory の interrupts-sent が二度渡しを防ぎ、次の拍が同じ id で撃ち直す)。"
  (<- fresh (| AcpRow None) (AcpGetRow :key job.job-key))
  (when (is fresh None)
    (<- (LogLine :text f"agentd: agent-job {job.job-id} vanished before its interrupts could be recorded"))
    (return False))
  (<- status dict (status-object-of fresh))
  (<- delivered dict (delivered-status-with-undeclared job status ids))
  (<- wrote (| Written Conflict Refused) (AcpPutStatus :row fresh :status delivered))
  (when (isinstance wrote Conflict)
    (<- again (| AcpRow None) (AcpGetRow :key job.job-key))
    (when (is-not again None)
      (<- status-again dict (status-object-of again))
      (<- delivered-again dict (delivered-status-with-undeclared job status-again ids))
      (<- wrote (| Written Conflict Refused) (AcpPutStatus :row again :status delivered-again))))
  (when (not (isinstance wrote Written))
    (<- (LogLine :text f"agentd: interrupts of job {job.job-id} delivered but not recorded ({wrote}); recording again next tick")))
  (isinstance wrote Written))


(defk deliver-interrupts-of [settings job row now-ms]
  {:pre [(: settings AgentdSettings) (: job InFlightJob) (: row AcpRow) (: now-ms int)]
   :post [(: % InFlightJob)]}
  "1 つの走っている job の割り込み(段 8 lane 4x): 行の interrupts のうちまだ渡していない id を
   載せた順に、本文を鍵で読み(mail-of と同じ 1 行ずつの読み)、session.send の mode = interrupt で
   器へ渡す。器が断った(走っている手番が無い)id はそこで止めて行に残す(順を跨いで後の id を先に
   渡さない)。渡せた id は memory に写し、行へ CAS で記録する。本文の無い id(Message の行が無い)は
   渡せない — 1 行 log して memory に写す(行には残す = Messaging が queued で積み直した時に
   InputUnavailable として名指す)。"
  (<- pending tuple (pending-interrupts-of row job.interrupts-sent))
  (when (not pending)
    (return job))
  (setv handed [])
  (setv unreadable [])
  (setv stopped False)
  (for [message-id pending]
    (when (not stopped)
      (<- key str (message-key-of message-id))
      (<- message (| AcpRow None) (AcpGetRow :key key))
      (setv body (if (is message None) None (.get message.spec "body")))
      ;; 段 10f 便 1b: 本文を記録の service に置いた郵便は stream から読む(mail-bodies-by-ref の同じ 1 点)。
      (setv carried #())
      (when (is-not message None)
        ;; 読みの表の鍵は郵便の identityKey(spec.id・無ければ行の resourceId)— 割り込みの列の id は上書きしない。
        (setv fetch-key (str (.get message.spec "id" message.resource-id)))
        (<- headlines tuple (message-attachments-of message.spec))
        (when (or (not (isinstance body str)) headlines)
          (<- read tuple (mail-bodies-by-ref settings #(message)))
          (when (not (isinstance body str))
            (setv body (.get (get read 0) fetch-key)))
          ;; 段 10 lane 10o: 割り込みの郵便の添付も型つきで運ぶ(綴りは Dialogue)。
          (setv carried (.get (get read 1) fetch-key #()))))
      (if (not (isinstance body str))
          (do
            (<- (LogLine :text f"agentd: interrupt {message-id} for job {job.job-id} has no readable Message; not delivered"))
            (.append unreadable message-id))
          (do
            ;; 段 10 lane 10n: 注入の行の名 = Message の id(CLI の command_lifecycle がこの綴りで運命を名乗る)。
            (<- outcome (| Interjected SessionRefused)
                (SessionInterject :session-id job.session-id :text body :ref message-id
                                  :attachments carried))
            (if (isinstance outcome Interjected)
                (do
                  (.append handed message-id)
                  (<- (MetricLine :fields {"metric" "agent-job-interrupt"
                                                  "agentJobId" job.job-id
                                                  "sessionId" job.session-id
                                                  "messageId" message-id
                                                  "atMs" now-ms})))
                (do
                  (<- (LogLine :text (+ f"agentd: interrupt {message-id} for job {job.job-id} not accepted by the session "
                                             f"({outcome.error}); left on the row")))
                  (setv stopped True)))))))
  (setv next (replace job :interrupts-sent (+ job.interrupts-sent (tuple handed) (tuple unreadable))))
  (when handed
    ;; 段 10 lane 10n: 注入した時刻を memory に(期限の判断の材料)。
    (<- next InFlightJob (with-injected-interrupts next (tuple handed) now-ms))
    (<- (record-interrupts-delivered next (tuple handed)))
    (<- (LogLine :text (+ f"agentd: job {job.job-id} received {(len handed)} interrupt(s): {(.join ", " handed)}"
                               (if (is next.interrupt-escalation-seconds None)
                                   " (no interruptEscalationSeconds in the charter — injected only, no stop signal)"
                                   f" (stop signal after {next.interrupt-escalation-seconds} s unless read)")))))
  next)


(defk deliver-interrupts [settings state rows now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: rows tuple) (: now-ms int)]
   :post [(: % AgentdState)]}
  "自分が走らせている job(memory)ごとに、行の cache の割り込みを器へ渡す(段 8 lane 4x)。
   行が cache に無い job は何もしない(次の拍)。"
  (setv current state)
  (for [job (list state.jobs)]
    (<- row (| AcpRow None) (job-row-keyed rows job.job-key))
    (when (is-not row None)
      (<- delivered InFlightJob (deliver-interrupts-of settings job row now-ms))
      (<- current AgentdState (with-job current delivered))))
  current)


(defk refresh-rows [state mode]
  {:pre [(: state AgentdState) (: mode str)]
   :post [(: % AgentdState)]}
  "知っている agent-job の行の cache を読み直す: full = 全量 list で置き換える / window =
   watch の since から続く event-window の post-image で差し替える(窓が読めなければ全量 list
   に落ちる・窓が尽きるまで続けて読む)。生まれの着地の時刻(generation 1 の image)は表に足す。"
  (if (= mode LIST-MODE-WINDOW)
      (do
        (setv after state.last-window-seq)
        (setv changed [])
        (setv retired [])
        (setv born [])
        (setv complete True)
        (setv exhausted False)
        (while (and complete (not exhausted))
          (<- window EventWindow (AcpEventWindow :after after :limit EVENT-WINDOW-LIMIT))
          (setv complete window.complete)
          (when complete
            (.extend changed window.rows)
            (.extend retired window.retired)
            (.extend born window.births)
            (setv exhausted (or window.exhausted (= window.through after)))
            (setv after window.through)))
        (if complete
            (do
              (<- job-rows tuple (rows-of-kind (tuple changed) AGENT-JOB-KIND))
              (<- merged tuple (merge-rows state.rows job-rows (tuple retired)))
              (<- births tuple (births-with state.births (tuple born)))
              (replace state :rows merged :births births :last-window-seq after))
            (do
              (<- listed tuple (AcpGet :kind AGENT-JOB-KIND))
              (<- pairs tuple (births-of-rows listed))
              (<- births tuple (births-with state.births pairs))
              (replace state :rows listed :births births :last-window-seq state.since))))
      (do
        (<- listed tuple (AcpGet :kind AGENT-JOB-KIND))
        (<- pairs tuple (births-of-rows listed))
        (<- births tuple (births-with state.births pairs))
        (replace state :rows listed :births births :last-window-seq state.since))))


(defk receive-bound-jobs [settings state mode now-ms]
  {:pre [(: settings AgentdSettings) (: state AgentdState) (: mode str) (: now-ms int)]
   :post [(: % AgentdState)]}
  "行の cache を読み直し(mode = full | window — judgment.list-mode-for の 1 点)、自分に結ばれた
   Bound の行と自分が持つ Running の行のうち、まだ memory に無い行を行の順に受ける(Bound =
   claim・Running = 行からの拾い直し)。取り下げられた行は先に止める腕へ。claim を
   持ち越した job(defer)は拍ごとに読み直す。"
  (<- refreshed AgentdState (refresh-rows state mode))
  (setv rows refreshed.rows)
  (<- withdrawn-handled AgentdState (withdraw-jobs settings refreshed rows now-ms))
  (<- bound tuple (job-rows-bound-to rows settings.node-name))
  (<- running tuple (job-rows-running-on rows settings.node-name settings.principal))
  (<- known set (in-flight-ids withdrawn-handled))
  (setv previously-deferred withdrawn-handled.deferred)
  (setv current (replace withdrawn-handled :deferred #()))
  (for [row bound]
    (when (not-in row.resource-id known)
      (<- current AgentdState (claim-job settings current rows row previously-deferred now-ms))))
  (for [row running]
    (when (not-in row.resource-id known)
      (<- current AgentdState (recover-job settings current row now-ms))))
  (replace current :last-resync-ms now-ms))


(defk agentd-tick [settings state]
  {:pre [(: settings AgentdSettings) (: state AgentdState)]
   :post [(: % AgentdState)]}
  "1 拍: watch を待つ → 参加の heartbeat → profile の残量の観測(遅い周期)→ 結ばれた job の受け →
   走っている job への割り込みの配達(段 8 lane 4x)→ 走っている job の観測。腕は互いの I/O の失敗で
   止まらない(R9): 失敗は log して次の周期 / 次の拍へ持ち越す(heartbeat・観測・受けは周期の刻印を
   進めて洪水を避ける)。I/O より広い例外(bug)は捕まえない。"
  (<- wait float (wait-seconds-for state settings))
  (<- signal WatchAdvance (AcpWatchSse :since state.since :wait-seconds wait))
  (<- now-ms int (ClockNowMs))
  (setv #^ AgentdState current (replace state :since signal.sequence))
  (<- heartbeat bool (due current.last-heartbeat-ms now-ms settings.node-heartbeat-seconds))
  (when heartbeat
    (try
      (<- joined AgentdState (join-tick settings current now-ms))
      (setv current joined)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: heartbeat failed: {(. (type e) __name__)}: {e}"))
        (setv current (replace current :last-heartbeat-ms now-ms)))))
  (<- profiles-due bool (due current.last-profile-observed-ms now-ms settings.profile-observe-seconds))
  (when profiles-due
    (try
      (<- profiled AgentdState (observe-profiles settings current now-ms))
      (setv current profiled)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: profile observation failed: {(. (type e) __name__)}: {e}"))
        (setv current (replace current :last-profile-observed-ms now-ms)))))
  (<- mode str (list-mode-for signal current now-ms settings))
  (when (!= mode LIST-MODE-NONE)
    (try
      (<- received AgentdState (receive-bound-jobs settings current mode now-ms))
      (setv current received)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: receive failed: {(. (type e) __name__)}: {e}"))
        (setv current (replace current :last-resync-ms now-ms)))))
  ;; 段 8 lane 4x: 走っている自分の job に載った割り込みを器へ — 毎拍・行の cache から(level-
  ;; triggered: 器が断った id は cache に残り、次の拍が同じ id で撃ち直す。受けの拍でなくてもよい)。
  (try
    (<- interrupted AgentdState (deliver-interrupts settings current current.rows now-ms))
    (setv current interrupted)
    (except [e IO-FAILURES]
      (<- (LogLine :text f"agentd: interrupt delivery failed: {(. (type e) __name__)}: {e}"))))
  (for [job (list current.jobs)]
    (try
      (<- observed AgentdState (observe-job settings current job now-ms))
      (setv current observed)
      (except [e IO-FAILURES]
        (<- (LogLine :text f"agentd: job {job.job-id} tick failed: {(. (type e) __name__)}: {e}")))))
  ;; 段 9f lane 9f-2: この拍で spool に置いた本文(と前の拍に送れなかった残り)を会話の記録の service へ — 拍の終わりの 1 点。
  (when settings.record-enabled
    (<- flush bool (record-flush-due current now-ms settings))
    (when flush
      (try
        (<- flushed AgentdState (flush-record-spool settings current now-ms))
        (setv current flushed)
        (except [e IO-FAILURES]
          (<- (LogLine :text f"agentd: record flush failed: {(. (type e) __name__)}: {e}"))
          (setv current (replace current :record-backoff-ms now-ms))))))
  current)
