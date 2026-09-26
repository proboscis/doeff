;; 同じ法の筋書きを memory の handler・PostgreSQL の handler・記録の service の HTTP の口越し(memory / PostgreSQL の置き場)で回し、
;; 答えの列(transcript)が等しいことを確かめる(番号・版・epoch・時刻まで同じ — どの置き場を選んでも、口越しでも、Program に見える
;; 答えは変わらない)。
(require doeff-hy.macros [deftest])
(import os)
(import doeff_records.laws [LAWS])
(import tests.interpreters [build-interpreter PG-DSN-VARIABLE])

(setv PG-DSN (.get os.environ PG-DSN-VARIABLE))


(defn transcript-on [#^ str name law]
  "解釈器 name の組で法 1 つを回し、答えの列を返す(比べの片側)。"
  (setv built (build-interpreter name))
  (try
    (built.run (law (built.harness)))
    (finally (built.close))))


(defn assert-same-transcripts [#^ list names]
  "全部の法を names の組で回し、memory の答えの列と 1 つも違わないことを確かめる。"
  (for [#(name law) (.items LAWS)]
    (setv on-memory (transcript-on "memory" law))
    (for [other names]
      (setv on-other (transcript-on other law))
      (assert (= on-memory on-other) (.format "{}: memory {!r}\n {} {!r}" name on-memory other on-other)))))


(deftest test-every-law-gives-the-same-answers-over-http-on-memory
  (assert-same-transcripts ["http-memory"]))


(deftest test-every-law-gives-the-same-answers-on-memory-and-postgres
  {:skip-if (not PG-DSN)
   :skip-reason "DOEFF_RECORDS_TEST_PG_DSN が無い(PostgreSQL の検は走っていない)"}
  (assert-same-transcripts ["pg" "http-pg"]))
