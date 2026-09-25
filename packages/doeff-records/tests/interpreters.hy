;;; 検の解釈器(composition root)— 同じ法の Program を、handler の組だけ替えて走らせる。
;;;
;;;   plain   scheduler だけ(純関数の検)。
;;;   memory  memory の handler + 仮想の時計(sim-time-handler)。
;;;   pg      PostgreSQL の handler + 仮想の時計。env DOEFF_RECORDS_TEST_PG_DSN の置き場に、検ごとに乱数の接頭辞の表を作り、
;;;           終わりに消す。env が無ければ skip(psycopg は依存に無い — `uv run --with psycopg` で足す)。
;;;
;;; 法は LawSetup の effect で自分の LawHarness(書き手の名 → その書き手の handler で包む関数)を読む。
(require doeff-hy.macros [defhandler])
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import importlib)
(import os)
(import uuid)
(import doeff [EffectBase run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records.laws [LAW-SCHEMA LawHarness])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.pg [PgRecordsHost pg-records-handler drop-records-tables])

(setv PLAIN "plain" MEMORY "memory" PG "pg")
(setv PG-DSN-VARIABLE "DOEFF_RECORDS_TEST_PG_DSN")


(defclass [(dataclass :frozen True)] LawSetup [EffectBase])

(defhandler law-setup [harness]
  (LawSetup []
    (resume harness)))


(defclass [(dataclass :frozen True)] BuiltInterpreter []
  "run = Program を走らせる関数 / close = 後片付け / harness = 法に渡す LawHarness を返す関数(plain は None)。"
  (#^ Callable run)
  (#^ Callable close)
  (#^ Callable harness))


(defn skip-reason [#^ str name]
  (cond
    (and (= name PG) (not (.get os.environ PG-DSN-VARIABLE)))
      (.format "PostgreSQL の法は env {} に使い捨ての置き場の DSN を置いた時だけ走る" PG-DSN-VARIABLE)
    True ""))


(defn open-postgres []
  "psycopg は依存に無い(接続は composition root が開く)ので、ここで名指しで読む。自動 commit の接続。"
  (setv psycopg (importlib.import-module "psycopg"))
  (psycopg.connect (get os.environ PG-DSN-VARIABLE) :autocommit True))


(defn harness-of [wrap]
  (LawHarness (fn [writer program] (with_handlers [(wrap writer)] program))))


(defn interpreter-over [harness stack close]
  (BuiltInterpreter (fn [program]
                      (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))] stack [(law-setup harness)])
                                                     program))))
                    close
                    (fn [] harness)))


(defn build-interpreter [#^ str name]
  (cond
    (= name MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (interpreter-over (harness-of (fn [writer] (memory-records-handler store writer))) [] (fn [] None)))
    (= name PG)
      (do (setv connection (open-postgres)
                host (PgRecordsHost connection LAW-SCHEMA :prefix (+ "t" (cut (. (uuid.uuid4) hex) 12) "_")))
          (defn close []
            (drop-records-tables host)
            (.close connection))
          (interpreter-over (harness-of (fn [writer] (pg-records-handler host writer))) [] close))
    True (BuiltInterpreter (fn [program] (run (scheduled program))) (fn [] None) (fn [] None))))
