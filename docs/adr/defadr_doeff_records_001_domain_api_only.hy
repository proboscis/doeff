;;; Executable ADR: the raw operations of the records service (doeff-records) — operations that let the caller choose table and column names and keys and read/write directly —
;;; are not offered outside the cluster. External callers (networks outside the cluster, CLI, webapp) use only the domain API.
;;;
;;; This ADR is a record of a decision and does not change the behavior of doeff-records' code or wire. Whether the pattern of jobs inside the cluster reading and writing with doeff-records'
;;; effects falls under this decision is still being confirmed with the maintainer, and the shape on the inside is undecided (R3).
;;; The checks that nothing is exposed outside live outside doeff: the dotfiles linter (defjevrule) and the declaration tests of the agora repo.
;;; On the doeff side we only fix the list of raw operations the wire exposes, so that adding one turns the test red (R4).
;;;
;;; Rollback: revert this file (code and wire are unchanged, so that alone rolls it back).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import doeff_records._wire [OPERATIONS PATH-PREFIX])


;; The list of raw operations the wire exposes under /v1/records/ (10 on doeff main as of 2026-10-07). A change that adds one goes red as missing from this list.
;; A change that removes one deletes it from this list in the same change.
;; 2026-10-22: added append-events (stores a batch of appends in one round trip — a bundle of append-event. Idempotency key, limits, and refusals give the same answers as append-event).
;; A change fixing the daily root failing aligns this: the addition of AppendEvents (described in the README) had entered mainline without updating the list.
;; 2026-10-31: added read-batch (reads a batch of row reads in one round trip — only read-row and list-rows may go in the batch), count-rows (the number of rows under
;; the same filter as a list read), and put-rows-append-events (stores a batch of row writes and a batch of appends all-or-nothing). 3 effects (2026-10-27 to 30) had
;; entered mainline without updating the list; a change fixing the daily root failing aligns this.
(val RAW-OPERATIONS
  (frozenset #("read-row" "list-rows" "put-row" "put-rows" "watch-changes"
               "append-event" "append-events" "read-events" "read-event-by-key" "read-stream-end" "watch-events"
               "read-batch" "count-rows" "put-rows-append-events")))


(defadr ADR-DOE-RECORDS-001
  :title "Raw operations of the records service (operations that read and write tables and columns directly) are not offered outside the cluster. External callers use only the domain API. The shape inside the cluster is still being confirmed with the maintainer and is undecided"
  :status "accepted"
  :scope ["packages/doeff-records/src/doeff_records/_wire.hy"
          "packages/doeff-records/src/doeff_records/_service.hy"
          "docs/adr/defadr_doeff_records_001_domain_api_only.hy"]
  :problem
    [(fact
       "Decision 2026-10-07: a general-purpose DB API must not be offered. Raw APIs such as read-row and list-rows are not exposed; everything is a domain API only. Offering a raw API is found and banned by defjevrule (dotfiles' rule declarations) or a linter, turned red, and fixed.")
     (fact
       "On doeff main as of 2026-10-07, doeff-records' wire exposes 10 raw operations under /v1/records/: read-row, list-rows, put-row, put-rows, watch-changes, append-event, read-events, read-event-by-key, read-stream-end, watch-events (OPERATIONS in _wire.hy). service.hy receives them over HTTP."
       :evidence "packages/doeff-records/src/doeff_records/_wire.hy:26-42")]
  :context
    [(interpretation
       "Raw operations are general-purpose reads and writes that let the caller choose table and column names and keys, and carry no domain meaning (who may do what, what shape a value has). If external callers use them directly, records can be read and written without going through domain rules.")
     (interpretation
       "\"Outside\" means networks outside the cluster (including the tailnet), CLI, and webapp. Whether jobs inside the cluster reading and writing with doeff-records' effects (ReadRow, etc.) counts as offering a raw API is not settled by this decision — it is still being confirmed with the maintainer.")]
  :decision
    [(rule R1 "The records service's raw operations are not offered outside the cluster (external networks, CLI, webapp).")
     (rule R2 "External callers use only the domain API (APIs with per-purpose names and types). No new path is created for calling raw operations from outside.")
     (rule R3 "Whether the pattern of jobs inside the cluster reading and writing with doeff-records' effects falls under this decision is undecided. Until the confirmation is answered, this ADR does not decide the shape on the inside (once answered, add a rule or make a separate ADR).")
     (rule R4 "On the doeff side, the list of raw operations the wire exposes is fixed in RAW-OPERATIONS. A change that adds an operation not in the list goes red in the test. A change that removes an operation deletes it from the list in the same change.")
     (rule R5 "The checks that nothing is exposed outside live outside doeff: the dotfiles linter (defjevrule declarations, on the ADR-DOTFILES-027 side) and the declaration tests of the agora repo (which turn red a declaration exposing the records service to the tailnet).")]
  :laws
    [(law no-raw-operation-is-offered-outside
       :statement "for_all publication p of the records service: p is reachable from outside the cluster ⇒ p does not answer raw operations"
       :counterexamples
         [(counterexample "A declaration exposes the records service endpoint (8875) to the tailnet, and a Mac CLI or webapp calls read-row and list-rows directly")]
       :enforced-by ["declaration tests of the agora repo"
                     "dotfiles linter (defjevrule)"]
       :wiring "Unwired (2026-10-07) — the checks are built by the 2 parties outside doeff. This file is the record of the decision")
     (law the-raw-operation-list-does-not-grow
       :statement "the set of OPERATIONS in _wire.hy = RAW-OPERATIONS"
       :counterexamples
         [(counterexample "Adding one raw operation to the wire, which external callers then use — without comparing against a list, raw operations silently grow even after the decision")]
       :enforced-by ["docs/adr/defadr_doeff_records_001_domain_api_only.hy::test-adr-doe-records-001-raw-operations-do-not-grow"]
       :wiring "Wired (2026-10-07) — the test in this file")]
  :enforcement
    [(deftest test-adr-doe-records-001-raw-operations-do-not-grow
       ;; The raw operations the wire exposes match the list (adding goes red; a change that removes deletes from the list in the same change — R4).
       (assert (= PATH-PREFIX "/v1/records/") PATH-PREFIX)
       (assert (= (frozenset OPERATIONS) RAW-OPERATIONS)
               (+ "The wire's raw operations differ from the list (ADR-DOE-RECORDS-001 R4 — do not add raw operations. A change that removes one deletes it from RAW-OPERATIONS in the same change): "
                  (str (sorted OPERATIONS)))))]
  :plans [])
