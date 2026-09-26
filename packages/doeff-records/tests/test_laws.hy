;; 節 3 の手順 1 の法を、memory の handler・PostgreSQL の handler・記録の service の HTTP の口越し(memory と PostgreSQL の置き場)の
;; 4 つの組で同じ筋書き(doeff_records.laws)で確かめる。
;; PostgreSQL は env DOEFF_RECORDS_TEST_PG_DSN が在る時だけ(無ければ skip と表示し、緑とは数えない)。
(require doeff-hy.macros [deftest <-])
(import doeff_records.laws [law-stale-put-conflicts law-committed-changes-appear-once-in-order law-epoch-change-resets
                            law-undeclared-writes-are-refused law-operator-paths-need-an-operator law-transient-rows-expire
                            law-indexed-list-equals-filtered-scan law-append-is-idempotent law-watch-waits-for-a-change
                            law-none-removes-a-field law-maintenance-prunes-and-sweeps law-put-rows-is-all-or-nothing])
(import tests.interpreters [LawSetup])


(deftest test-stale-put-conflicts
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-stale-put-conflicts harness))
  (assert transcript))

(deftest test-committed-changes-appear-once-in-order
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-committed-changes-appear-once-in-order harness))
  (assert transcript))

(deftest test-epoch-change-resets
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-epoch-change-resets harness))
  (assert transcript))

(deftest test-undeclared-writes-are-refused
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-undeclared-writes-are-refused harness))
  (assert transcript))

(deftest test-transient-rows-expire
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-transient-rows-expire harness))
  (assert transcript))

(deftest test-indexed-list-equals-filtered-scan
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-indexed-list-equals-filtered-scan harness))
  (assert transcript))

(deftest test-append-is-idempotent
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-append-is-idempotent harness))
  (assert transcript))

(deftest test-watch-waits-for-a-change
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-watch-waits-for-a-change harness))
  (assert transcript))

(deftest test-none-removes-a-field
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-none-removes-a-field harness))
  (assert transcript))

(deftest test-maintenance-prunes-and-sweeps
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-maintenance-prunes-and-sweeps harness))
  (assert transcript))

(deftest test-operator-paths-need-an-operator
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-operator-paths-need-an-operator harness))
  (assert transcript))

(deftest test-put-rows-is-all-or-nothing
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-put-rows-is-all-or-nothing harness))
  (assert transcript))
