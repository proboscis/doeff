;;; Test declaration — 5 Jev lines for rules DOEFF201, 202 and 205, with one firing example and one silent example per line (10 in total).
;;; The line texts = verbatim from the comment section "Lines for Jev rules DOEFF201 / 202 / 205" of agora-controllers' architecture.hy (main 8bad3b59f).
;;; For the two points taken on rows that could not be decided, the first was appended after the text of line 5 and the second after the text of line 4. The examples = an excerpt of the definitions from the same main,
;;; taken from the "47 real examples" of the document analysis-jev-judgments-47-by-lines-2026-10-01.html (sha256 4c93c656…) (:tags, docstrings and
;;; comments dropped; omitted parts are `;; …`). The layer descriptions are copies of core and protocol from the same main.
;;; The 2 examples of line 4 are `{:code … :why …}` pairs (the why appears in the question paired with the code; the order of :code and :why does not matter),
;;; and the other 8 examples are only the code string (the earlier form is still readable).
(defarchitecture lines-sample
  :root "app"
  :layers [(layer core :summary "Business judgment and Programs" :knows "Business judgment (when, to whom, what)"
                         :does-not-know "Who the counterparty is and how to communicate" :question "If the way of communicating changes, does this code stay the same?" :roles [type judgment program])
           (layer protocol :summary "Handlers that translate an intent into the counterparty's way of speaking" :knows "The counterparty's way of speaking (rewriting a request into generic effects)"
                         :does-not-know "Real or simulated" :question "Is it only rewriting the request into another generic effect?" :roles [protocol])]
  :semantic-lines [(line "Line 1" :rules [DOEFF201]
                     :text "What a protocol definition may do is only: check the shape of a value that came from outside (read it into a type, check the presence and format of fields), read and write, return an error value when what was looked up is missing, and turn the answer of a judgment function into a value. Looking at business state (a record's value, deadline, owner, how the state transitions, whether it is already done) and deciding the answer to return (hand over, refuse, write, which result) is business judgment, and goes in a pure function of core. This is the same line as the 9/30 decision written in architecture.hy (a translation handler only reads and writes, and calls a pure function of core for judgment). custody has no core, so adopting this turns into the work of creating a core. A row of the same shape that was judged a false positive on 9/29 (record-end-precondition) also turns into a violation."
                     :fires [#[code[(defk access-of-held [ledger lease-id]
  {:pre [(: ledger CustodyHttpLedger) (: lease-id str)] :post [(: % (| TurnCredential TurnCredentialUnavailable))]}
  (<- at datetime (GetTime))
  (setv held (.get ledger.held lease-id) now (! (epoch-ms-of-time at)))
  (cond
    (is held None) (TurnCredentialUnavailable :reason (+ "lease " lease-id " is not borrowed or has already been returned"))
    (<= (min held.access-expires-at held.expires-at) now) (TurnCredentialUnavailable :reason (+ "lease " lease-id " has a token past its expiry"))
    True (TurnCredential held.access-token)))]code]]
                     :silent [#[code[(defk send-task [placement job assignment workers]
  {:pre [(: placement TaskPlacement) (: job TaskJob) (: assignment TaskAssignment) (: workers tuple)]
   :post [(: % (| TaskSent TaskRefused TaskUnreachable))]}
  (val worker (next (gfor w workers :if (= w.name assignment.node) w) None))
  (var worker-open False)
  (when (is-not worker None)
    (<- ok bool (open-worker? worker))
    (:= worker-open ok))
  (cond
    (and (is-not worker None) worker-open) (do (<- sent (| TaskSent TaskUnreachable) (submit-task placement job assignment))
                                                sent)
    (or (is worker None) (not worker.live)) (TaskRefused :reason (+ "no machine: " assignment.node))
    True (TaskRefused :reason (+ "machine is draining: " assignment.node))))]code]])
                   (line "Line 2" :rules [DOEFF202]
                     :text "core does not know the spelling of JSON. Building and reading JSON strings (json.dumps, json.loads, dump-json) is done by protocol or an entry-point part; core only receives and returns typed values. The same applies to values computed from the JSON body (hash, byte count) and to reading a local contract document. This is the same line as the 9/29 judgment that treated the JSON normalization (RFC 8785) in the classifier's core as a violation. The only difference from the original judgment is row 33, which reads a local contract document."
                     :fires [#[code[(defk tag-families-of [core-text]
  {:pre [(: core-text str)]
   :post [(: % TagFamilies)]}
  (setv document (json.loads core-text))
  (when (not (isinstance document dict))
    (raise (ValueError "kanban-core.json is not an object")))
  (setv section (.get (.get document CORE-PARTICIPATION {}) CORE-TAG-FAMILIES))
  (when (not (isinstance section dict))
    (raise (ValueError f"kanban-core.json has no {CORE-PARTICIPATION}.{CORE-TAG-FAMILIES}")))
  ;; …
  (TagFamilies :families (tuple families)
               :table rows
               :source (TagFamiliesSource :contract TAG-FAMILIES-SOURCE-FILE
                                          :sha256 (.hexdigest (hashlib.sha256 (.encode core-text "utf-8"))))))]code]]
                     :silent [#[code[(defk beat-status [row owner now ttl-ms]
  {:pre [(: row (| RunRecord Malformed None)) (: owner str) (: now int) (: ttl-ms int)] :post [(: % (| RunStatus None))]}
  (when (not (isinstance row RunRecord))
    (return None))
  (val lease row.status.lease)
  (when (or (is lease None) (!= lease.owner owner))
    (return None))
  (<- next RunStatus (renewed-status row.status owner now ttl-ms))
  next)]code]])
                   (line "Line 3" :rules [DOEFF202]
                     :text "core does not know HTTP. Building or holding URLs, paths and queries, choosing or holding HTTP methods, statuses and headers, and knowing the shape of an outside party's URL (such as a GitHub commit URL) is done in protocol. The same applies to a service whose job is communication itself, such as an HTTP relay or checking the HTTP contract of another service; core holds \"where to\", \"whether it is ready\" and \"which item does not match\" as typed values. The differences from the original judgment are the type that checks another service's HTTP contract (row 2, which was undecided) and row 32, which builds a GitHub commit link."
                     :fires [#[code[(defk relay-url-of [row target]
  {:pre [(: row RelayRow) (: target str)] :post [(: % str)]}
  (val base (.rstrip row.upstream "/"))
  (val rest (cut target (len (.rstrip row.prefix "/")) None))
  (if (or (= rest "") (.startswith rest "?"))
      (+ base "/" rest)
      (+ base rest)))]code]]
                     :silent [#[code[(defk admit-writer [raw-key]
  {:pre [(: raw-key (| str None))] :post [(: % (| WriterAdmitted WriterRefused BorrowerBookUnreadable))]}
  (<- key (| str None) (presented-key raw-key))
  (when (is key None)
    (return (WriterRefused :refusal WriterRefusal.NO-KEY)))
  (<- book (| BorrowerBook ObservationUnavailable) (ReadBorrowerBook))
  (when (isinstance book ObservationUnavailable)
    (return (BorrowerBookUnreadable :reason book.reason)))
  (<- verdict (| WriterAdmitted WriterRefused) (writer-verdict key book))
  verdict)]code]])
                   (line "Line 4" :rules [DOEFF205]
                     :text "Checking the shape of input means reading fields from an untyped value (dict, JSON value, raw string), checking the type with isinstance, and checking the presence, emptiness, length, format or vocabulary of fields. Even for a field after it has been read into a type, checks of emptiness, length, format and vocabulary count as shape checks. Branching on the arms of a type union does not count as a shape check. Shape checks are finished at the entry point (protocol, or a single function that reads into a type) and are not brought into the definition of a judgment. This reverses the 9/30 manager decision that \"restrictions on a field after reading into a type do not count as shape checks\" (the strict side). With this, rows 35, 37 and 44 change from false positives to violations. An untyped dict that did not come from outside, such as a constant inside the code or a tally of its own, does not count as \"input\" in line 4."
                     :fires [{:why "A shape check that verifies the form of the id of a typed request with a regular expression and a judgment that decides the owner are in the same definition."
                              :code #[code[(defk classify-message [request]
  {:pre [(: request ClassifyRequest)] :post [(: % (| Accepted Rejected Unavailable))]}
  (when (not (MESSAGE-ID-RE.match request.message))
    (return (Rejected :reason "payload-invalid" :detail "id is not lt- + 26 Crockford characters")))
  (<- policy (| DeliveryPolicy None Unavailable) (ReadDeliveryPolicy))
  (when (isinstance policy Unavailable)
    (return policy))
  (<- message (| Message MessageAbsent Unavailable) (ReadMessageRecord request.message))
  ;; …
  (val answered (replace message :request-class request.request-class))
  (<- rule (| ReceptionRule None) (machine-rule-for policy answered None))
  (when (is rule None)
    (return (Rejected :reason REFUSE-CLASS-NOT-IN-RULES :detail (+ "class " request.request-class " has no open rule"))))
  ;; …
  (<- forwarded Answer (forward-message (ForwardRequest :by MESSAGING-PRINCIPAL :message request.message :to cid)))
  (if (isinstance forwarded Accepted)
      (Accepted :subject cid :changed forwarded.changed)
      forwarded))]code]}]
                     :silent [{:code #[code[(defk join-chat [request]
  {:pre [(: request JoinRequest)] :post [(: % (| Joined Rejected Unavailable))]
   :effects [ReadDeliveryPolicy GetTime ReadChatRoomRecord ReadChatParticipants WriteChatParticipant]}
  (<- problem (| str None) (join-problem request))
  (when problem
    (return (Rejected :reason "payload-invalid" :detail problem)))
  (<- allowed (| bool Unavailable) (may-manage? request.by))
  (match allowed
    (Unavailable) (return allowed)
    False (return (Rejected :reason "opener-not-allowed" :detail (+ "principal " request.by " may not add participants"))))
  (<- now int (now-ms))
  (<- joined (| Joined Rejected Unavailable)
      (join-record (ParticipantRecord :chat request.chat :agent request.agent :origin request.origin :joined-at now :kind request.kind
                                      :defaults request.defaults)))
  joined)]code]
                               :why "The shape check is left to join-problem, and this only returns a refusal value from its answer. There is no shape check, so nothing is mixed."}])
                   (line "Line 5" :rules [DOEFF205 DOEFF201]
                     :text "Business judgment means deciding the answer by business rules (who may do what, which result to give, what to write). Deciding which value to treat an unreadable value as (falling back to a default, skipping it) and assembling for display or tallying (projection, aggregation) also count as business judgment. \"Falling back an unreadable value to a default is judgment\" applies to rule 201 as well (if protocol falls back an unreadable value to a default, it is a violation). Representing an unreadable value as \"none (None)\" and passing it to a judgment function is not included in line 5's \"which value to treat an unreadable value as\"."
                     :fires [#[code[(defk progress-items [material args limit offset now]
  {:pre [(: material ProgressMaterial) (: args dict) (: limit int) (: offset int) (: now int)]
   :post [(: % (| EntityItems EntityRefused))]}
  (setv board material.board
        namespace (.get args ENTITY-ARG-NAMESPACE))
  (when (not (isinstance namespace str))
    (return (EntityRefused :reason (+ KANBAN-ENTITY-PROGRESS " requires args." ENTITY-ARG-NAMESPACE " (the card namespace)"))))
  (<- since (| int None EntityRefused) (since-of args))
  (when (isinstance since EntityRefused)
    (return since))
  (<- members dict (milestone-members (list (.values board.relations))))
  (for [[key milestone] (.items board.milestones)]
    (setv own (.get members key []))
    (when (not (any (gfor card own
                          :if (in card board.cards)
                          (= (. (get board.cards card) namespace) namespace))))
      (continue))
    ;; …
    (when (and (not open?) (or (is since None) (< last-at since)))
      (continue))
    ;; …
    )
  (setv ordered (sorted items :key (fn [item] #((get item "order") (get item "id")))))
  (<- page EntityItems (page-of (lfor item ordered (OpaqueJson.of item)) offset limit))
  ;; …
  (EntityItems :items page.items :total page.total :landed (OpaqueJson.of landed-json)))]code]]
                     :silent [#[code[(defk list-limit-of [text]
  {:pre [(: text (| str None))] :post [(: % (| int RequestRefused))]}
  (val limit (cond
               (is text None) LIST-DEFAULT-LIMIT
               (.fullmatch DECIMAL text) (int text)
               True None))
  (if (and (is-not limit None) (<= 1 limit LIST-MAX-LIMIT))
      limit
      (RequestRefused :refusal RequestRefusal.LIMIT-OUT-OF-RANGE :detail (str LIST-MAX-LIMIT))))]code]])])
(defservice lines {:layers [core protocol]})
