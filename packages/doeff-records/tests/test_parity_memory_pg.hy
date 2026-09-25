;; 同じ法の筋書きを memory の handler と PostgreSQL の handler で回し、答えの列(transcript)が等しいことを確かめる
;; (番号・版・epoch・時刻まで同じ — どちらの置き場を選んでも Program に見える答えは変わらない)。
(require doeff-hy.macros [deftest])
(import os)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_records.laws [LAWS])
(import tests.interpreters [build-interpreter PG-DSN-VARIABLE])

(setv PG-DSN (.get os.environ PG-DSN-VARIABLE))


(defn transcript-on [#^ str name law]
  (setv built (build-interpreter name))
  (try
    (built.run (law (built.harness)))
    (finally (built.close))))


(deftest test-every-law-gives-the-same-answers-on-memory-and-postgres
  {:skip-if (not PG-DSN)
   :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (for [#(name law) (.items LAWS)]
    (setv on-memory (transcript-on "memory" law)
          on-pg (transcript-on "pg" law))
    (assert (= on-memory on-pg) (.format "{}: memory {!r}\n pg {!r}" name on-memory on-pg))))
