;;; Executable ADR: the delivery laws of the cross-process event backend (doeff-events' notice_events_handler and the notice-operation
;;; handlers beneath it = memory / Redis Pub/Sub). The law scenarios (Programs) live in one place, packages/doeff-events/src/doeff_events/_notice_laws.py,
;;; and both the memory handler and a real redis-server run through the same scenarios. Breaking any one upper layer turns the corresponding law red
;;; (packages/doeff-events/tests/test_notice_laws_memory.py).
;;;
;;; React to events immediately, even if that means using redis or rabbitmq, and separate the record from the wake-up. Correctness is held by the record; the backend carries only
;;; non-persisted notices (Pub/Sub) and does not write confirmation queues [Streams, consumer group]. This adds, to the event source of ADR-DOE-EVENTS-001 (record-service
;;; notifications), a source that carries notices emitted by writers.
;;;
;;; Rollback: revert the commit that added this ADR, the 5 doeff-events files (effects/notices.py, handlers/memory_notices.py, handlers/redis_notices.py,
;;; handlers/notice_events.py, notice_laws.py) and their checks. The pre-existing surface (Publish, WaitForEvent,
;;; subscribed_event_handler) is unchanged, so users are not affected.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pathlib [Path])


(defadr ADR-DOE-EVENTS-002
  :title "Delivery laws of the cross-process notice backend: the subscription starts before the Program's first effect; start and reconnection are announced once, after the subscription is established; a notice reaches only readers connected when it is emitted (notices emitted while disconnected are not delivered)"
  :status "accepted"
  :scope ["packages/doeff-events/src/doeff_events/_notice_laws.py"
          "packages/doeff-events/src/doeff_events/_handlers/notice_events.py"
          "packages/doeff-events/src/doeff_events/_handlers/memory_notices.py"
          "packages/doeff-events/src/doeff_events/_handlers/redis_notices.py"
          "packages/doeff-events/src/doeff_events/_effects/notices.py"
          "docs/adr/defadr_doeff_events_002_notice_delivery_laws.hy"]
  :problem
    [(fact
       "Stop the design that writes to the record and polls the record (decided 2026-10-06). React to events immediately; redis or rabbitmq may be used. Separate the record from the wake-up.")
     (fact
       "Measured on a real redis-server 7.2.7 (2026-10-06): when 256 KB notices are emitted continuously to a subscriber that does not read, with client-output-buffer-limit pubsub set to 1 MB, Redis disconnects that subscriber (server log \"scheduled to be closed ASAP for overcoming of output buffer limits\"). Of the 18 notices for which PUBLISH answered \"1 receiver\", the subscriber could read 13; the rest were lost with the disconnection. It never happened that only notices in the middle were lost while the connection survived (the read notices were contiguous from the start)."
       :evidence "packages/doeff-events/tests/test_notice_laws_redis.py::test_subscriber_that_does_not_read_is_disconnected_on_redis")
     (interpretation
       "Non-persisted notices are not delivered later for the period when the receiver was disconnected. So the backend promises two things: \"from when delivery starts\" (after the subscription is established) and \"announcing that delivery stopped\" (disconnection -> SourceStalled -> SourceResumed); what was lost is filled by the receiving Program catching up from the record once, on SourceStarted and SourceResumed. PUBLISH's receiver count is \"the number subscribed at that moment\" and is not proof that the receiver read it.")]
  :decision
    [(rule R1 "The delivery-law scenarios live in one place, doeff_events._notice_laws. A change to the lower-layer handlers (memory, Redis) or the upper layer (notice_events_handler) must pass the same scenarios on both memory and a real redis-server.")
     (rule R2 "Business Programs do not expose channel names. They expose only Publish, WaitForEvent, and event values.")
     (rule R3 "Waits are only a blocking take (no deadline — stopped by the task's Cancel) and one deadline-bounded wait for the broker to come back (WaitWithin). Do not add a wake-on-interval-and-check shape (the only exceptions are the two places R6 and R8).")
     (rule R4 "The backend does not silently discard received notices: every received notice is Published to the in-process subscriber queue, and a notice the route table cannot read brings down its source by name. The receiver count of an emitted notice (PUBLISH's answer) is returned to the sender in Publish's answer NoticeSent.")
     (rule R5 "Failed sends are handled in one place only, notice_events_handler (the shape after the 2026-10-07 review): Publish raises no exception even when undelivered, and answers with one of three closed types (NoticeSent, NoticeGapMarked, NoticeDropped). A route chooses, via the required field when_unsent, MarkGap (a gap mark on the channel) or Drop (keep nothing). Only the channel mark is kept, never the event. A gap is one fixed notice (created by Announce.gap, recognized by Announce.is_gap and Announcement.is_gap — the name string is internal), emitted when the broker comes back, before the next Publish that goes through, and when the sender starts (MarkGap.start_channels); the receiving wrapper turns it into SourceMissed (the receiver catches up from the record once). The body's sends (including sends of tasks the body Spawned) and gap notices pass through a single exit in arrival order, and the exit is released on exception or cancellation too (fix points A, C, D of the 2026-10-07 review). Once the next Publish has emitted all gaps, the task waiting for the return stops. The sender does not re-send on its own based on Publish's answer.")
     (rule R6 "How Redis's return is detected (2026-10-07): only while a gap mark or a stalled subscription is waiting for the return, broker_back_by_retry (one place in redis_notices.py) tries ProbeBroker (connectivity only — on Redis, PING; no data is read or written) at the interval named by the assembly (no default), and answers AwaitBrokerBack once connected. If asked again before the interval has elapsed since the previous answer, it first waits out the remaining interval (so it does not repeat without an interval when PING passes but sends keep being refused — fix point B). Zero tries while nobody is waiting. The Redis sending client waits for connect and send answers up to the timeout named by the assembly (no default) (so an unresponsive server or a silent network cannot block the sender — fix point A). Alternative not adopted = a Kubernetes EndpointSlice watch (changes production permissions). Regarding the rule (2026-10-06) that places requiring polling are fixed and polling is a last resort, reading the case where no notice comes from a crashed peer as a last-resort case is an interpretation; the rule itself does not say so. Rollback = delete broker_back_by_retry and put something else in the assembly that answers AwaitBrokerBack.")
     (rule R7 "Gap for a write with unknown outcome (2026-10-20): a sender whose write answer did not arrive within the deadline, and which does not know whether the write landed, passes the event it would have emitted to PublishGap. The wrapper does not send the event; it puts a gap mark on that route's channel and emits it immediately through the same single exit as R5. The answer is one of three closed types (NoticeGapTold, NoticeGapMarked, NoticeDropped), and handling when the broker cannot accept is the same as R5. A PublishGap of a type not in the table is forwarded to the outer handler (the wrapper does not know its channel). Origin = 2026-10-19 12:24 UTC, the record service was slow and the answer to the write of an image-bearing send did not arrive. The row had landed in the record, yet neither the event nor a gap mark was emitted, and the dispatcher did not know about that input for 57 minutes (R5's gaps covered only the broker being unable to accept and the sender starting, not a write with unknown outcome).")
     (rule R8 "How a half-open subscription is detected (2026-10-22): a Redis subscription connection is counted as lost if nothing answers for redis_notice_handler's argument silence_seconds (default SUBSCRIPTION_SILENCE_SECONDS = 7 seconds), and NextAnnouncement answers with BrokerUnreachable (the source emits SourceStalled). Inside NextAnnouncement's wait, once half of silence has passed with no notice, one PING is sent on the subscription connection, and if neither a notice nor the PING answer arrives within the remaining half, the connection is lost. The confirmation wait of SubscribeChannels also fits within the same silence. PING checks connectivity only (no data is read or written), only while a waiter (NextAnnouncement) exists, in one place in redis_notices.py (_next_message). Reason: a subscribed Redis sends nothing while there are no notices, so the reader cannot tell a quiet connection from one whose peer stopped answering (network break, peer machine gone, peer process stopped). With the OS default TCP keep-alive (Linux = 9 probes every 75 seconds after 7200 seconds of silence), detecting a half-open connection takes about 2 h 11 min, and a stop where the peer's kernel still holds the connection (SIGSTOP) cannot be detected by keep-alive at all. The 7-second target is R10 of the design record for agora's read-replica role (detect subscription loss within 7 seconds). Alternative not adopted = socket_keepalive_options (TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT) alone — cannot detect a stopped peer process, and the selectable values differ outside Linux. DOEFF209 (doeff-linter's time-based re-fetch loop) is a rule that inspects production Hy code under :business-fakes in architecture.hy, so this Python handler is outside its population. It also does not hit the rule's intent (do not re-fetch on a timer from a peer that offers change notifications) — the subscription connection's peer (Redis) emits no liveness signal while quiet. Rollback = remove the PING in _next_message and return the wait to a no-deadline get_message.")]
  :laws
    [(law subscription-precedes-the-body
       :statement "for_all reader r: r's subscription is established on the broker side before the wrapped Program's first effect — the Program itself receives the first notice it emits."
       :counterexamples
         [(counterexample "An upper layer that delays subscribing until the Program's first wait — notices emitted between the Program reading state and starting to wait are dropped, and the Program is never woken")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_shared_signal_invariant_holds_across_processes"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_subscribing_after_the_first_read_breaks_the_shared_gap_invariant"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "Wired (2026-10-06) — the scenario notice_laws.law_subscription_precedes_the_body and the shared invariant in tests/event_signal_invariants.py, \"do not drop events between the read and the wait\". With a broken upper layer, the latter goes red on a scheduler dead end.")
     (law start-is-told-after-the-subscription
       :statement "for_all reader r: r's source emits SourceStarted (first connected) and SourceResumed (reconnected) only after the subscription is established on the broker side — notices emitted after the Program receives that announcement reach r."
       :counterexamples
         [(counterexample "An upper layer that emits SourceStarted before the subscription is established — after the Program catches up from the record on SourceStarted, notices emitted before the subscription is established are dropped (covered by neither the catch-up nor the notices)")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_telling_the_start_before_the_subscription_breaks_the_start_law"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "Wired (2026-10-06) — the scenario notice_laws.law_start_is_told_after_the_subscription (start) and, inside law_start_and_return_are_told_once, \"a notice emitted after reconnection has at least 1 receiver\" (reconnection). No check goes red for an upper layer broken on the reconnection side.")
     (law start-and-return-are-told-once
       :statement "for_all reader r: r's source emits SourceStarted once when the subscription is first established, and, when the broker connection drops and the subscription is rebuilt, SourceStalled and SourceResumed once each (per stall)."
       :counterexamples
         [(counterexample "The upper layer does not emit SourceResumed after reconnecting — the Program never receives the signal to catch up from the record on notices dropped while disconnected")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_not_telling_the_return_breaks_the_start_and_return_law"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "Wired (2026-10-06) — the scenario notice_laws.law_start_and_return_are_told_once. On the real server, a stall is produced by cutting all connections.")
     (law notice-reaches-only-current-subscribers
       :statement "for_all notice n, channel c: n reaches only readers that are subscribed to c and connected to the broker when it is emitted, and the sender learns that count. An n emitted before subscribing or while disconnected is not delivered later (Pub/Sub semantics). The backend does not fill what was lost, on the premise that the receiving Program catches up from the record once when it receives SourceStarted and SourceResumed. The receiver count is not proof of \"read\" (a subscriber that does not read is disconnected at the limit and loses counted notices)."
       :counterexamples
         [(counterexample "Treating notices as \"always delivered\" and handing off work via notices alone (not kept in the record) — work emitted while the reader is disconnected vanishes")
          (counterexample "Treating a PUBLISH receiver count of 1 or more as delivered and skipping the write to the record — notices whose reader was disconnected before reading vanish")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_broker_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_notice_announced_during_an_outage_is_not_delivered_later"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_broker_law_holds_on_redis"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_subscriber_that_does_not_read_is_disconnected_on_redis"]
       :wiring "Wired (2026-10-06) — the scenario notice_laws.law_notice_reaches_only_current_subscribers (before subscribing, 0 receivers and not delivered; after, 1 receiver and delivered), the check that notices during a stall are not delivered later (memory), and the check that a non-reading subscriber is disconnected (real server).")
     (law missed-notice-is-told-as-a-gap
       :statement "for_all sender s, channel c: when the broker cannot accept an event on c from s, s raises no exception and puts a gap mark on c, and when the broker comes back, or when s's next Publish goes through (before it), announces the gap (SourceMissed) to readers that read c and stayed connected. Events emitted after the gap do not arrive before the gap. If the broker immediately goes down again and the gap could not be emitted, the mark remains and is emitted on the next return."
       :counterexamples
         [(counterexample "An upper layer that turns a failed send into a Program exception — the reader stays connected so it never receives SourceResumed, and events go missing with no signal to catch up (work keeps being dispatched to a worker that is gone)")
          (counterexample "An upper layer that keeps the events themselves per key and re-emits only the last value — change-carrying events (inputs) erase other inputs, and a WorkerGone of the same boot arrives after WorkerBack")
          (counterexample "An upper layer that emits a Publish as-is while a gap is being emitted — a later event arrives before the gap")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_gap_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_not_telling_the_return_breaks_the_missed_notice_law"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_publish_answers_a_marked_gap_with_the_brokers_words_and_the_body_goes_on"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_dropped_route_marks_nothing_and_says_so"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_many_missed_notices_on_one_channel_are_told_as_one_gap"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_gap_left_when_a_body_ends_does_not_reach_the_next_body"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_sender_tells_a_gap_on_its_start_channels_when_it_starts"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_return_nobody_can_tell_ends_the_body_with_that_error_after_the_next_publish_told_the_gap"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_the_wait_for_the_return_stops_once_the_next_publish_told_the_gap"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_an_error_while_telling_a_gap_leaves_the_exit_free_for_the_next_publish"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_publish_made_while_another_task_tells_the_gap_waits_behind_it"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_gap_law_holds_on_redis"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_a_sender_cut_off_alone_tells_its_gap_to_a_connected_reader_when_it_reaches_the_server_on_redis"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "Wired (2026-10-07) — the 5 GAP_LAWS scenarios in notice_laws run on memory under a layer where only the sender side loses the broker (refuses_senders in tests — the reader stays connected). A layer that does not announce the return goes red on a dead end. On a real redis-server, only the sender is cut and restored via the test's TCP relay (the reader stays directly connected), confirming the gap arrives as SourceMissed (skipped with a reason on machines without redis-server).")
     (law unsure-send-is-told-as-a-gap
       :statement "for_all sender s, event e: when s emits PublishGap(e), e is not delivered, and a gap (SourceMissed) reaches readers that read e's route channel and stayed connected — immediately while the broker accepts sends (NoticeGapTold), and otherwise when the broker comes back or when s's next Publish goes through (NoticeGapMarked)."
       :counterexamples
         [(counterexample "An upper layer that sends the event of a write with unknown outcome as if the write had succeeded — the reader receives an event for a row that has not landed and starts work without the row (breaking the write-first-then-send semantics)")
          (counterexample "A sender that emits nothing for a write with unknown outcome — even if the write landed, the reader does not know about the row until its next start (the 57 minutes of 2026-10-19)")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_gap_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_sending_the_event_instead_of_a_gap_breaks_the_unsure_send_law"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_gap_of_a_dropped_route_is_not_kept_and_says_so"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_gap_of_an_unrouted_event_goes_outward"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_gap_law_holds_on_redis"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "Wired (2026-10-20) — the scenario notice_laws.law_unsure_send_is_told_as_a_gap is added to GAP_LAWS and run on memory and a real redis-server (skipped with a reason on machines without redis-server). Red for a layer that replaces PublishGap with a Publish of the event.")
     (law broker-return-is-tried-only-while-waited
       :statement "for_all time interval I: if nobody waits on AwaitBrokerBack during I, broker_back_by_retry makes 0 tries (ProbeBroker). While a waiter exists, it tries once per interval named by the assembly and answers at the first try that connects. After the next Publish has emitted all gaps, it does not try."
       :counterexamples
         [(counterexample "An answerer that tries reconnecting on a timer even with no mark held — a time-woken loop always remains")
          (counterexample "An upper layer that does not stop the task waiting for the return even after the next Publish emitted the gap — needless tries continue")]
       :enforced-by ["packages/doeff-events/tests/test_broker_back_by_retry.py::test_nothing_is_tried_while_no_gap_is_held"
                     "packages/doeff-events/tests/test_broker_back_by_retry.py::test_a_held_gap_is_tried_every_interval_and_told_at_the_first_try_after_the_return"
                     "packages/doeff-events/tests/test_broker_back_by_retry.py::test_trying_stops_once_the_next_publish_told_the_gap"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_a_sender_cut_off_alone_tells_its_gap_to_a_connected_reader_when_it_reaches_the_server_on_redis"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_a_restarted_server_is_tried_only_while_a_gap_is_held_and_told_one_gap_on_redis"
                     "packages/doeff-events/tests/test_broker_back_by_retry.py::test_a_broker_that_takes_the_ping_but_refuses_the_sends_is_tried_once_an_interval"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_a_server_that_stops_answering_cannot_hold_a_publish_past_the_timeout_on_redis"]
       :wiring "Wired (2026-10-07) — counts the try ticks under a memory broker (answers ProbeBroker by whether it is cut) and a virtual clock. On a real redis-server too, a check that cuts and restores only the sender's connection and a check that stops the server and restarts it on the same port count the tries (0 before the stall, one per interval during the stall, no increase after the gap is emitted) and exactly one gap notice (skipped with a reason on machines without redis-server).")
     (law half-open-subscription-is-told-within-the-silence
       :statement "for_all reader r: when the peer of r's subscription connection stops answering (network break, peer stopped — the connection is not closed), r's source emits SourceStalled within silence_seconds (default 7 seconds). While the peer answers, r's subscription does not drop and no SourceStalled is emitted, however many times the notice-free time exceeds silence."
       :counterexamples
         [(counterexample "A lower layer that puts no deadline on the subscription wait and relies on the OS default TCP keep-alive — it does not notice the half-open connection for about 2 h 11 min, during which the read replica stays stale yet answers \"usable\"")
          (counterexample "A lower layer that drops the connection on quiet time alone (no PING) — subscriptions to low-traffic channels drop every silence, and SourceStalled and SourceResumed repeat")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_redis.py::test_a_half_open_subscription_is_told_as_stalled_within_the_default_silence_on_redis"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_a_quiet_subscription_that_answers_its_pings_stays_subscribed_on_redis"]
       :wiring "Wired (2026-10-22) — silences a real redis-server with SIGSTOP (the peer kernel keeps the connection) and confirms, on both RESP2 and RESP3, that with the default silence SourceStalled arrives within 7 + 1 seconds, and that on an answering server a subscription quiet for 6 times silence does not drop (PING answers are counted) and receives the next notice (skipped with a reason on machines without redis-server).")]
  :enforcement
    [(deftest test-adr-doe-events-002-laws-are-declared
       ;; Probe: the law scenarios live in one place, notice_laws.py, and are listed in the law sets EVENT_LAWS, GAP_LAWS, BROKER_LAWS.
       ;; doeff-events is not installed in the repo-root environment, so read the file instead of importing it (same as ADR-DOE-EVENTS-001).
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/_notice_laws.py") :encoding "utf-8"))
       (for [name ["law_subscription_precedes_the_body" "law_start_is_told_after_the_subscription"
                   "law_start_and_return_are_told_once" "law_notice_reaches_only_current_subscribers"
                   "law_missed_notice_is_told_once_the_broker_is_back" "law_gap_is_told_before_the_next_notice"
                   "law_latest_state_wins_after_a_gap" "law_gap_keeps_the_order_of_later_notices" "law_gap_survives_a_second_cut"
                   "law_unsure_send_is_told_as_a_gap"]]
         (assert (in (+ "def " name "(") text)
                 (+ "Delivery-law scenario missing from notice_laws.py (ADR-DOE-EVENTS-002 R1): " name))
         (assert (in (+ name ",") text)
                 (+ "Delivery-law scenario not listed in a law set (EVENT_LAWS / GAP_LAWS / BROKER_LAWS) (ADR-DOE-EVENTS-002 R1): " name))))]
  :plans [])
