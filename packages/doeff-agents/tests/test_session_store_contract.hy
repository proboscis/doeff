;;; session の行の置き場(SessionStore* の effect)の契約テスト — 本物(sqlite-session-store: 一時 dir の SQLite)と fake
;;; (memory-session-store: memory の置き場)が同じ deftest を通る。解釈器の組み立ては session_store_contract_handlers.hy。
;;;
;;;   * 作る: 読まずに書いた行は、書いた欄のまま読める(backend-ref の None は空の dict で戻る)
;;;   * 無い行: 読み・結果の読みは None、一覧と会話 ID は空
;;;   * 差分の重ね方: 読んでから書き戻した行は、読んだ時点から変えた欄だけが重なる(読みと書きの間の他の書きは残る)。
;;;     読まずに書いた行(launch の形)は保護の無い欄を全部置き換える
;;;   * 列の保護: 最初に書いた事実(結果・終端の理由・実効 identity・会話・launch の意図・上限と provider 失敗の初回観測)は後の書きと
;;;     None の書き戻しで消えない・観測断の刻印は新しい値で進み None では消えない・水準の欄(待ちの基点・手番の終わり・手番の失敗の文・
;;;     検めの誤り)は None で消える
;;;   * terminal の行は active へ戻らない(書きが断られ、行は元のまま)・terminal の間の書き(掃き取りの刻印)は通る
;;;   * 一覧と絞り込み: active の一覧は非終端だけを新しい順(同じ時刻は session id の順)・掃き取りの一覧は未刻印の終端の
;;;     run_to_completion の非 adopted 行を古い順・刻印すると一覧から消える・会話 ID は終端を含む全部の行から 1 度ずつ昇順
;;;   * 出来事は書いた行の wire 形を載せ、行が無ければ渡された行の欄を載せる
;;; 契約の外: 行を消す effect は無い(置き場から行が消える経路は無い — 掃き取りは刻印するだけ)。命令の監査・lease・report_result の直の
;;; UPDATE・起動時の latch の解除・履歴の刈り取りは SQLite の store の actor の op で、effect ではない(sessionhost_store_deftests.hy)。
(require doeff-hy.macros [defk deftest <- val var])
(import dataclasses [replace])
(import doeff_agents.sessionhost.effects [
  SessionRow
  TerminalCause
  SessionStoreGet
  SessionStoreUpsert
  SessionStoreListActive
  SessionStoreListCleanupPending
  SessionStoreResultPayload
  SessionStoreRecordEvent
  SessionStoreKnownConversationIds])
(import session_store_contract_handlers [RecordedEvents])

(val STARTED "2026-09-29T00:00:00+00:00")
(val FIRST-CAUSE (TerminalCause :category "run_failed" :reason "boom" :retryable False :observed-at "2026-09-29T00:01:00+00:00"
                                :limit-scope None :limit-reason None :limit-resets-at-ms None))
(val LATER-CAUSE (TerminalCause :category "lost" :reason None :retryable True :observed-at "2026-09-29T00:02:00+00:00"
                                :limit-scope None :limit-reason None :limit-resets-at-ms None))


(defk a-row [session-id fields]
  {:pre [(: session-id str) (: fields dict)] :post [(: % SessionRow)] :tags {:context "session-store-test" :role "program"}}
  "契約の行を作るため(同じ下地に fields — SessionRow の欄の名 → 値 — を重ねる。置き場から読んでいない行なので、書くと全欄が重なる)。"
  (replace (SessionRow :session-id session-id :session-name "name-s" :pane-id "%1" :agent-type "codex" :lifecycle "run_to_completion"
                       :status "running" :started-at STARTED :work-dir "/w"
                       :backend-ref {"session_name" "name-s" "pane_id" "%1" "command" "codex"})
           #** fields))


(defk write [row]
  {:pre [(: row SessionRow)] :post [(: % None)] :tags {:context "session-store-test" :role "program"}}
  "行を置き場へ書くため。"
  (<- (SessionStoreUpsert :row row))
  None)


(defk read-row [session-id]
  {:pre [(: session-id str)] :post [(: % SessionRow)] :tags {:context "session-store-test" :role "program"}}
  "在るはずの行を読むため(無ければ契約の誤り)。"
  (<- row (SessionStoreGet :session-id session-id))
  (assert (is-not row None) session-id)
  row)


(defk ids-of [rows]
  {:pre [(: rows list)] :post [(: % list)] :tags {:context "session-store-test" :role "program"}}
  "一覧の答えを session id の並びで比べるため。"
  (lfor row rows row.session-id))


(deftest test-a-new-row-reads-back-with-the-fields-it-was-written-with
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- row (a-row "s1" {"conversation" {"session_id" "c1"} "terminal_cause" FIRST-CAUSE "expected_result" {"type" "object"}
                       "awaiting_response" True "generation" 2 "effective_identity" {"CODEX_HOME" "/h"}
                       "launch_overlay" {"model" "m"} "output_snippet" "tail" "result_solicitations_used" 1
                       "paste_resubmit_attempts" 2 "resumed_from_session_id" "s0"}))
  (<- (write row))
  (<- back (read-row "s1"))
  (assert (= back row) back)
  (assert (is-not back.read-base None) "読んだ行は書き戻しの重ねに使う読んだ時点の写しを持つ")
  (<- (write (! (a-row "s2" {"backend_ref" None}))))
  (<- bare (read-row "s2"))
  (assert (= bare.backend-ref {}) bare.backend-ref))


(deftest test-a-missing-row-answers-nothing
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- missing (SessionStoreGet :session-id "none"))
  (assert (is missing None) missing)
  (<- payload (SessionStoreResultPayload :session-id "none"))
  (assert (is payload None) payload)
  (<- active list (SessionStoreListActive))
  (<- pending list (SessionStoreListCleanupPending))
  (<- known list (SessionStoreKnownConversationIds))
  (assert (= #(active pending known) #([] [] [])) #(active pending known))
  (<- (write (! (a-row "s1" {}))))
  (<- other (SessionStoreGet :session-id "none"))
  (assert (is other None) other))


(deftest test-a-write-back-overlays-only-the-fields-changed-since-its-read
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {}))))
  (<- monitor (read-row "s1"))
  (<- sender (read-row "s1"))
  (<- (write (replace sender :pane-id "%9")))
  (<- (write (replace monitor :status "blocked" :output-snippet "tail")))
  (<- back (read-row "s1"))
  (assert (= #(back.pane-id back.status back.output-snippet) #("%9" "blocked" "tail")) back))


(deftest test-a-row-written-without-a-read-replaces-every-unprotected-field
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {"last_validation_error" "bad" "awaiting_response_since" STARTED "output_snippet" "tail"
                          "pane_id" "%2" "result_payload" "{\"ok\":true}"}))))
  (<- (write (! (a-row "s1" {}))))
  (<- back (read-row "s1"))
  (assert (= #(back.last-validation-error back.awaiting-response-since back.output-snippet back.pane-id) #(None None None "%1")) back)
  (assert (= back.result-payload "{\"ok\":true}") back.result-payload))


(deftest test-first-written-facts-survive-later-writes-and-none-write-backs
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (val facts [#("result_payload" "{\"n\":1}" "{\"n\":2}")
              #("terminal_cause" FIRST-CAUSE LATER-CAUSE)
              #("effective_identity" {"CODEX_HOME" "/a"} {"CODEX_HOME" "/b"})
              #("conversation" {"session_id" "c1"} {"session_id" "c2"})
              #("launch_overlay" {"model" "a"} {"model" "b"})
              #("api_limit_observed_at" "2026-09-29T01:00:00+00:00" "2026-09-29T02:00:00+00:00")
              #("provider_failure_class" "reauth-required" "transport-failure")
              #("provider_failure_observed_at" "2026-09-29T01:00:00+00:00" "2026-09-29T02:00:00+00:00")])
  (for [#(field first later) facts]
    (<- (write (! (a-row field {field first}))))
    (<- read (read-row field))
    (<- (write (replace read #** {field later})))
    (<- after-later (read-row field))
    (assert (= (getattr after-later field) first) #(field (getattr after-later field)))
    (<- (write (replace after-later #** {field None})))
    (<- after-none (read-row field))
    (assert (= (getattr after-none field) first) #(field (getattr after-none field)))))


(deftest test-an-observation-gap-moves-forward-but-a-none-keeps-it
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {"observation_gap_at" "2026-09-29T01:00:00+00:00"}))))
  (<- first (read-row "s1"))
  (<- (write (replace first :observation-gap-at "2026-09-29T02:00:00+00:00")))
  (<- moved (read-row "s1"))
  (assert (= moved.observation-gap-at "2026-09-29T02:00:00+00:00") moved.observation-gap-at)
  (<- (write (replace moved :observation-gap-at None)))
  (<- kept (read-row "s1"))
  (assert (= kept.observation-gap-at "2026-09-29T02:00:00+00:00") kept.observation-gap-at))


(deftest test-level-fields-clear-with-a-none-write-back
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {"lifecycle" "multi_turn" "awaiting_response" True "awaiting_response_since" STARTED
                          "turn_ended_at" STARTED "turn_error" "error_during_execution" "last_validation_error" "bad"}))))
  (<- read (read-row "s1"))
  (<- (write (replace read :awaiting-response False :awaiting-response-since None :turn-ended-at None :turn-error None
                      :last-validation-error None)))
  (<- back (read-row "s1"))
  (assert (= #(back.awaiting-response back.awaiting-response-since back.turn-ended-at back.turn-error back.last-validation-error)
             #(False None None None None))
          back))


(deftest test-a-terminal-row-is-never-reactivated
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {"status" "done" "finished_at" STARTED}))))
  (<- done (read-row "s1"))
  (var refusal None)
  (try
    (<- (write (replace done :status "running")))
    (except [e RuntimeError]
      (:= refusal (str e))))
  (assert (and (is-not refusal None) (in "may not be reactivated" refusal)) refusal)
  (<- still (read-row "s1"))
  (assert (= still.status "done") still.status)
  (<- (write (replace still :cleaned-at "2026-09-29T03:00:00+00:00")))
  (<- swept (read-row "s1"))
  (assert (= #(swept.status swept.cleaned-at) #("done" "2026-09-29T03:00:00+00:00")) swept))


(deftest test-the-active-list-holds-active-rows-newest-first
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (for [#(session-id status started) [#("a" "running" "2026-09-29T01:00:00+00:00")
                                      #("d" "blocked" "2026-09-29T02:00:00+00:00")
                                      #("b" "booting" "2026-09-29T02:00:00+00:00")
                                      #("c" "done" "2026-09-29T03:00:00+00:00")
                                      #("e" "pending" "2026-09-29T00:00:00+00:00")
                                      #("f" "blocked_api" "2026-09-29T00:30:00+00:00")]]
    (<- (write (! (a-row session-id {"status" status "started_at" started})))))
  (<- active list (SessionStoreListActive))
  (<- ids (ids-of active))
  (assert (= ids ["b" "d" "a" "f" "e"]) ids))


(deftest test-the-cleanup-list-holds-unswept-terminal-rows-oldest-first
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (for [#(session-id fields) [#("f1" {"status" "failed" "started_at" "2026-09-29T02:00:00+00:00"})
                              #("e1" {"status" "exited" "started_at" "2026-09-29T01:00:00+00:00"})
                              #("x1" {"status" "cancelled" "started_at" "2026-09-29T01:00:00+00:00"})
                              #("swept" {"status" "done" "cleaned_at" STARTED})
                              #("interactive" {"status" "stopped" "lifecycle" "interactive"})
                              #("adopted" {"status" "stopped" "adopted" True})
                              #("live" {"status" "running"})]]
    (<- (write (! (a-row session-id fields)))))
  (<- pending list (SessionStoreListCleanupPending))
  (<- ids (ids-of pending))
  (assert (= ids ["e1" "x1" "f1"]) ids)
  (<- e1 (read-row "e1"))
  (<- (write (replace e1 :cleaned-at "2026-09-29T03:00:00+00:00")))
  (<- after list (SessionStoreListCleanupPending))
  (<- left (ids-of after))
  (assert (= left ["x1" "f1"]) left))


(deftest test-known-conversation-ids-cover-terminal-rows-once-in-order
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (for [#(session-id fields) [#("s1" {"conversation" {"session_id" "conv-b"}})
                              #("s2" {"status" "done" "conversation" {"session_id" "conv-a"}})
                              #("s3" {})
                              #("s4" {"conversation" {"session_id" "conv-b" "rollout_path" "/r.jsonl"}})]]
    (<- (write (! (a-row session-id fields)))))
  (<- known list (SessionStoreKnownConversationIds))
  (assert (= known ["conv-a" "conv-b"]) known))


(deftest test-the-result-payload-read-follows-the-stored-row
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {}))))
  (<- before (SessionStoreResultPayload :session-id "s1"))
  (assert (is before None) before)
  (<- read (read-row "s1"))
  (<- (write (replace read :result-payload "{\"ok\":true}")))
  (<- after (SessionStoreResultPayload :session-id "s1"))
  (assert (= after "{\"ok\":true}") after))


(deftest test-an-event-carries-the-stored-row-or-the-given-row-when-missing
  {:interpreters ["sqlite-session-store" "memory-session-store"]}
  (<- (write (! (a-row "s1" {"status" "done" "result_payload" "{}"}))))
  (<- stale (a-row "s1" {}))
  (<- (SessionStoreRecordEvent :session-id "s1" :event-type "session_done" :row stale))
  (<- ghost (a-row "ghost" {"output_snippet" "tail"}))
  (<- (SessionStoreRecordEvent :session-id "ghost" :event-type "session_observed" :row ghost))
  (<- events list (RecordedEvents))
  (assert (= (lfor #(session-id event-type _) events #(session-id event-type))
             [#("s1" "session_done") #("ghost" "session_observed")])
          events)
  (val stored (get (get events 0) 2))
  (assert (= #((get stored "status") (get stored "result_payload") (get stored "pr_url")) #("done" "{}" None)) stored)
  (assert (not-in "terminal_cause" stored) "wire 形は None の optional の欄を載せない")
  (val given (get (get events 1) 2))
  (assert (= #((get given "session_id") (get given "status") (get given "output_snippet")) #("ghost" "running" "tail")) given)
  (assert (not-in "pr_url" given) "行が無い時は policy の行の欄だけ(wire 形ではない)"))
