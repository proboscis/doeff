;; 同じ法の筋書きを memory の handler・PostgreSQL の handler(SQL の答え手は同期の版と scheduler を塞がない版の 2 つ)・記録の service の HTTP の口越し(memory / PostgreSQL の置き場)で回し、
;; 答えの列(transcript)が等しいことを確かめる(番号・版・epoch・時刻まで同じ — どの置き場を選んでも、口越しでも、Program に見える
;; 答えは変わらない)。
;; PostgreSQL の置き場は、用意の直後に版の行を memory の置き場の初めの版 1 に揃えてから回す(build-interpreter の :aligned-epoch —
;; 時刻を仮想の時計で揃えるのと同じ、始まりの状態の揃え)。この検は memory と PostgreSQL の答えが同じかを見る検で、PostgreSQL の版が
;; 作った時の server の時計の ms で始まり置き場ごとに違うこと(#3632)は test_pg_store_epoch.hy と test_laws.hy の pg の組が見る。
(require doeff-hy.macros [deftest])
(import doeff_records.laws [LAWS])
(import tests.interpreters [session-dsn build-interpreter PG-DSN-VARIABLE pg-skip-reason])

(setv PG-DSN (session-dsn PG-DSN-VARIABLE))


(defn transcript-on [#^ str name law]
  "解釈器 name の組で法 1 つを回し、答えの列を返す(比べの片側)。"
  (setv built (build-interpreter name :aligned-epoch True))
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
   :skip-reason (pg-skip-reason)}
  (assert-same-transcripts ["pg" "pg-pooled" "http-pg"]))
