;; 節 3 の手順 1 の法を、memory の handler・PostgreSQL の handler・記録の service の HTTP の口越し(memory と PostgreSQL の置き場)の
;; 4 つの組で同じ筋書き(doeff_records.laws)で確かめる。
;; PostgreSQL は env DOEFF_RECORDS_TEST_PG_DSN が在る時だけ(無ければ skip と表示し、緑とは数えない)。
(require doeff-hy.macros [deftest <-])
(import doeff_records.laws [law-stale-put-conflicts law-committed-changes-appear-once-in-order law-epoch-change-resets
                            law-undeclared-writes-are-refused law-transient-rows-expire
                            law-indexed-list-equals-filtered-scan law-descending-list-is-the-head-of-the-sorted-scan law-append-is-idempotent law-key-head-read-equals-filtered-scan
                            law-any-of-list-equals-the-union-of-equal-lists law-descending-pages-follow-the-sorted-scan
                            law-watch-waits-for-a-change
                            law-watch-events-waits-for-an-append
                            law-none-removes-a-field law-maintenance-prunes-and-sweeps law-prune-stops-at-the-archived-seq law-change-log-reads-every-change
                            law-put-rows-is-all-or-nothing
                            law-grouped-events-expire-together law-expired-keys-are-remembered
                            law-expired-records-are-unseen-before-a-sweep law-a-write-clears-the-expired-row-it-touches
                            law-an-expired-key-answers-the-same-before-and-after-a-sweep law-short-page-ends-the-stream])
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

(deftest test-grouped-events-expire-together
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-grouped-events-expire-together harness))
  (assert transcript))

(deftest test-expired-keys-are-remembered
  ;; #3022: 保持の期限で出来事を消した後の同じ冪等キー — 別の本文は断り、同じ本文は前の番号で列の出来事を増やさない(memory と PostgreSQL)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-expired-keys-are-remembered harness))
  (assert transcript))

(deftest test-expired-records-are-unseen-before-a-sweep
  ;; #3561: 保持の期限を過ぎた行と出来事は回収の前でも読みに出ず、消えた は回収(SweepExpired)が 1 回だけ積む — 別の表への書きは積まない
  ;; (#3605 の D・memory と PostgreSQL)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-expired-records-are-unseen-before-a-sweep harness))
  (assert transcript))

(deftest test-a-write-clears-the-expired-row-it-touches
  ;; #3605 の D: 回収されていない期限を過ぎた行への書きは、その行を消して 消えた を積んでから無い行として判じる(回収の後と同じ答え)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-a-write-clears-the-expired-row-it-touches harness))
  (assert transcript))

(deftest test-an-expired-key-answers-the-same-before-and-after-a-sweep
  ;; #3605 の D: 回収されていない期限を過ぎた鍵への追記は、回収の後と同じ答え・期限を過ぎた組の古い出来事は新しい鍵の追記で読みに戻らない。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-an-expired-key-answers-the-same-before-and-after-a-sweep harness))
  (assert transcript))

(deftest test-indexed-list-equals-filtered-scan
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-indexed-list-equals-filtered-scan harness))
  (assert transcript))

(deftest test-descending-list-is-the-head-of-the-sorted-scan
  ;; 上から読む ListRows(descending-by): 全件を読んで並べた上から limit 件と同じ行を同じ順で 1 頁に返し、続きは limit 件を越える時だけ
  ;; 返す(4 つの組で同じ)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-descending-list-is-the-head-of-the-sorted-scan harness))
  (assert transcript))

(deftest test-descending-pages-follow-the-sorted-scan
  ;; 上から読む ListRows の続き: next-cursor を辿った頁を連ねると全件を (値, 鍵) の大きい順に並べた物と同じ(重なりも欠けも無い)・頁の間の
  ;; 書きは位置より小さい組だけが後の頁に出る・古い版の位置は Reset(4 つの組で同じ)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-descending-pages-follow-the-sorted-scan harness))
  (assert transcript))

(deftest test-any-of-list-equals-the-union-of-equal-lists
  ;; 値の組の ListRows(where-any): 頁を辿った全件は組の値ごとの等号の読みの和を鍵の順に並べた物・上から読む読みはその上から limit 件
  ;; (4 つの組で同じ)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-any-of-list-equals-the-union-of-equal-lists harness))
  (assert transcript))

(deftest test-append-is-idempotent
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-append-is-idempotent harness))
  (assert transcript))

(deftest test-key-head-read-equals-filtered-scan
  ;; 冪等キーの頭で引く ReadEvents(key-head): 全部の出来事を読んで頭で絞った列の上から limit 件と同じ出来事を返す(4 つの組で同じ)。
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-key-head-read-equals-filtered-scan harness))
  (assert transcript))

(deftest test-watch-waits-for-a-change
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-watch-waits-for-a-change harness))
  (assert transcript))

(deftest test-watch-events-waits-for-an-append
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-watch-events-waits-for-an-append harness))
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

(deftest test-prune-stops-at-the-archived-seq
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-prune-stops-at-the-archived-seq harness))
  (assert transcript))

(deftest test-change-log-reads-every-change
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-change-log-reads-every-change harness))
  (assert transcript))

(deftest test-put-rows-is-all-or-nothing
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-put-rows-is-all-or-nothing harness))
  (assert transcript))

(deftest test-short-page-ends-the-stream
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  (<- harness (LawSetup))
  (<- transcript (law-short-page-ends-the-stream harness))
  (assert transcript))
