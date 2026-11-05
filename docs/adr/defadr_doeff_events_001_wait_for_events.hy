;;; Executable ADR: a repeatedly running loop waits on events, not on time — it waits for events with doeff-events' WaitForEvent,
;;; and the wake-up-on-time-and-check shape lives only in boundary handlers. Waiting across the cluster is answered by swapping the
;;; event source's handler (memory / record-service notification), and the Program side does not change (only the connection values differ).
;;; An event carries only a notification (key) of "what changed", and the receiver re-reads the record.
;;;
;;; Revision 2026-10-03 12:0x (cisco-c8's reversible decision): the event source was changed from Redis Streams to
;;; the record service (PostgreSQL LISTEN / NOTIFY and the HTTP WatchChanges long-poll). Why the alternative was not chosen: see the :problem facts.
;;;
;;; What was decided: see the :problem facts.
;;; The record of this decision on the seat side = dotfiles ADR-DOTFILES-027 R-c9c11f05 (the writing-time advice is code-quality-policy-when-writing-code).
;;; The full text of the line on wait deadlines and on expected events that never arrive (business deadlines become events, infrastructure
;;; faults are not surfaced to the Program, sim turns a dead end red immediately) is in the c3-w49 design notes.
;;;
;;; Rollback: roll back the event source by swapping the handler (a Redis Streams handler, or the memory one — business code only
;;; fires WaitForEvent, so it is not touched). To roll back the record, revert the commit that added this ADR (one ADR
;;; file and the enforcement ledger count disappear).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pathlib [Path])


(defadr ADR-DOE-EVENTS-001
  :title "A repeatedly running loop waits for events with WaitForEvent. Time lives only in boundary handlers. The event source is a handler swap (memory / record-service notification); only the connection values differ"
  :status "accepted"
  :scope ["packages/doeff-events/src/doeff_events"
          "docs/adr/defadr_doeff_events_001_wait_for_events.hy"]
  :problem
    [(fact
       "Loops and waits use doeff-events so they can wait on events rather than on time. Its handlers have cluster-aware backends such as redis or rabbitmq.")
     (fact
       "Event source = the record service (PostgreSQL LISTEN / NOTIFY and the HTTP WatchChanges long-poll). An event carries only a notification (key) of \"what changed\", and the receiver re-reads the record — even if a notification arrives twice, out of order, or goes missing, the next notification or the read at startup catches up, so state is not corrupted and redelivery after a crash (consumer group, pending, ack) is not needed. Why chosen = nothing new to deploy; a single change log suffices; NOTIFY commits in the same transaction as the write, so there is no \"written but never notified\" gap; no password is added."
       :evidence "a reversible decision taken before this ADR was written")
     (fact
       "The 11:4x decision (the Mac coordinator) = the backend is Redis Streams (has a cursor so nothing is missed; runs without a password; 1 Pod; same handler even on a single local cluster. RabbitMQ stayed a candidate only because the product requires a user / password). This was revised at 12:0x. Why not chosen = it yields two change logs, the record-service table and the Redis stream, and the gap between the two writes (write and emit) must be closed with an outbox. The advantage of Redis Streams (events that do not pass through the record service still reach across processes) is not needed, since in today's agora every cross-process event is expected to pass through the record service (not all verified)."
       :evidence "cisco-c8's relay (2026-10-03 11:4x) and the 12:0x revision")
     (interpretation
       "A loop that wakes on time and checks pays a cost on every wake-up (the class of root cause that exceeds the check's step limit) and a delay of up to one interval. Waiting on events wakes only when something changed, and can be tested in the same shape in simulation and in production.")]
  :decision
    [(rule R1 "A repeatedly running loop waits for events with doeff-events' WaitForEvent. Do not newly write the wake-on-time-and-check shape (WaitUntil, sleep, interval settings) into a loop. Time lives only in boundary handlers. Waiting across the cluster is answered by swapping the event source's handler (memory / record-service notification — PostgreSQL LISTEN / NOTIFY and the HTTP WatchChanges long-poll), and the Program side does not change — only the connection values differ (consistent with the same-API decision of ADR-DOE-CLUSTER-001 R8). An event carries only a notification (key) of \"what changed\", and the receiver re-reads the record.")]
  :laws
    [(law loops-wait-for-events
       :statement "for_all repeatedly running loop l: l wakes only when WaitForEvent returns an event, and l contains no wake-on-time-and-check wait (WaitUntil, sleep, interval settings). for_all event source b ∈ {memory, record-service notification}: the same Program runs with only b's handler swapped, and the receiver of a notification re-reads the record"
       :counterexamples
         [(counterexample "Writing the coordinator's quiet phase as a loop that wakes every 10 seconds and re-reads the table — every wake-up costs something, and changes are picked up up to 10 seconds late")
          (counterexample "Writing a LISTEN connection or a long-poll position branch into the loop to receive record-service notifications — the difference between event sources leaks into the Program instead of the handler")
          (counterexample "Putting the record's contents into the notification, and the receiver changing state from it without re-reading — state is corrupted when a notification arrives twice, out of order, or goes missing")]
       :enforced-by ["test-adr-doe-events-001-public-surface"
                     "dotfiles advice code-quality-policy-when-writing-code (ADR-DOTFILES-027 R-c9c11f05)"]
       :wiring "Partially wired (2026-10-03) — the check only covers the existence of the doeff-events public surface the decision relies on (publish an event, wait, the memory handler). A probe counting time-based waits inside loops, and the record-service notification handler (milestone P), are not yet implemented")]
  :enforcement
    [(deftest test-adr-doe-events-001-public-surface
       ;; Probe: the doeff-events public surface the decision relies on (Publish, WaitForEvent, the memory handler event_handler) is in __all__.
       ;; doeff-events is not installed in the repo-root environment, so read the public-surface file instead of importing it.
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/__init__.py") :encoding "utf-8"))
       (for [name ["Publish" "WaitForEvent" "event_handler"]]
         (assert (in (+ "\"" name "\",") text)
                 (+ "doeff-events public surface is missing (ADR-DOE-EVENTS-001 R1): " name))))]
  :plans [])
