;; 節 3 の手順 1 の法を、memory の handler と PostgreSQL の handler の両方で同じ筋書き(doeff_records.laws)で確かめる。
;; PostgreSQL は env DOEFF_RECORDS_TEST_PG_DSN が在る時だけ(無ければ skip と表示し、緑とは数えない)。
(require doeff-hy.macros [deftest <-])
(import doeff_records.laws [law-stale-put-conflicts law-committed-changes-appear-once-in-order law-epoch-change-resets
                            law-undeclared-writes-are-refused law-transient-rows-expire
                            law-indexed-list-equals-filtered-scan law-append-is-idempotent law-watch-waits-for-a-change])
(import tests.interpreters [LawSetup])


(deftest test-stale-put-conflicts
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-stale-put-conflicts harness))
  (assert transcript))

(deftest test-committed-changes-appear-once-in-order
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-committed-changes-appear-once-in-order harness))
  (assert transcript))

(deftest test-epoch-change-resets
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-epoch-change-resets harness))
  (assert transcript))

(deftest test-undeclared-writes-are-refused
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-undeclared-writes-are-refused harness))
  (assert transcript))

(deftest test-transient-rows-expire
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-transient-rows-expire harness))
  (assert transcript))

(deftest test-indexed-list-equals-filtered-scan
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-indexed-list-equals-filtered-scan harness))
  (assert transcript))

(deftest test-append-is-idempotent
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-append-is-idempotent harness))
  (assert transcript))

(deftest test-watch-waits-for-a-change
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-watch-waits-for-a-change harness))
  (assert transcript))
