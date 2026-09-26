;;; 検の解釈器(composition root)— 同じ法の Program を、handler の組だけ替えて走らせる。
;;;
;;;   plain   scheduler だけ(純関数の検)。
;;;   memory  memory の handler + 仮想の時計(sim-time-handler)。
;;;   pg      PostgreSQL の handler + 仮想の時計。env DOEFF_RECORDS_TEST_PG_DSN の置き場に、検ごとに乱数の接頭辞の表を作り、
;;;           終わりに消す。env が無ければ skip(psycopg は依存に無い — `uv run --with psycopg` で足す)。
;;;   http-memory / http-pg
;;;           記録の service の HTTP の口(127.0.0.1 の空き port)を memory / PostgreSQL の置き場の上に開き、法の Program は
;;;           client の handler(http-records-handler)で公開 effect を撃つ。書き手の名は身元の名簿の token で運ぶ(LAW-TOKENS)。
;;;           service と client は同じ仮想の時計(SimClock 1 つ)を読む。検の口と手入れの effect(AdvanceStoreEpoch・SweepExpired・
;;;           PruneChanges — HTTP の口に出さない)は、client の外側に被せた置き場の handler が直に答える。
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
(import doeff_records.pg_pool [PgHostPool])
(import doeff_records.principals [Roster token-digest])
(import doeff_records.http_server [RecordsServerConfig start-records-server])
(import doeff_records.http_client [RecordsEndpoint http-records-handler])
(import doeff_hy.frozen [FrozenMap])
(import contextlib [contextmanager])

(setv PLAIN "plain" MEMORY "memory" PG "pg" HTTP-MEMORY "http-memory" HTTP-PG "http-pg")
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
    (and (in name #(PG HTTP-PG)) (not (.get os.environ PG-DSN-VARIABLE)))
      (.format "PostgreSQL の法は env {} に使い捨ての置き場の DSN を置いた時だけ走る" PG-DSN-VARIABLE)
    True ""))


(defn open-postgres []
  "psycopg は依存に無い(接続は composition root が開く)ので、ここで名指しで読む。自動 commit の接続。"
  (setv psycopg (importlib.import-module "psycopg"))
  (psycopg.connect (get os.environ PG-DSN-VARIABLE) :autocommit True))


(defn pg-errors []
  "psycopg の接続の失敗の型(PgRecordsHost に渡す — psycopg は依存に無いので PostgreSQL の検の時だけ読む)。"
  (setv psycopg (importlib.import-module "psycopg"))
  #(psycopg.OperationalError psycopg.InterfaceError))


(defn harness-of [wrap]
  (LawHarness (fn [writer program] (with_handlers [(wrap writer)] program))))


(defn interpreter-over [harness stack close]
  (BuiltInterpreter (fn [program]
                      (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))] stack [(law-setup harness)])
                                                     program))))
                    close
                    (fn [] harness)))


;; 法の書き手の名 → 身元の token(検だけの値 — 名簿には sha256 だけを載せる)。
(setv LAW-TOKENS (dfor writer ["maker" "painter" "closer" "stranger"] writer (+ "law-token-" writer)))
(setv HTTP-POLL-SECONDS 0.05)


(defn law-roster []
  "法の書き手 4 人の身元の名簿(service の組み立てに渡す)。"
  (Roster (FrozenMap (dfor #(writer token) (.items LAW-TOKENS) writer (run (token-digest token))))))


(defn sim-runner [clock]
  "service が要求ごとに Program を走らせる関数 — 法の側と同じ仮想の時計を読む。"
  (fn [program] (run (scheduled (with_handlers [(sim-time-handler :clock clock)] program)))))


(defn http-interpreter [lease-handlers backing concurrent close-store]
  "HTTP の口を開き、法の書き手を client の handler(その書き手の token)で包む組。backing = 書き手の名 → 置き場の handler
   (検の口と手入れの effect に直に答える — client の外側に被せる)。"
  (setv clock (SimClock)
        server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) lease-handlers (sim-runner clock)
                                                          :concurrent concurrent))
        harness (LawHarness (fn [writer program]
                              (with_handlers [(backing writer)
                                              (http-records-handler (RecordsEndpoint server.url (get LAW-TOKENS writer)
                                                                                     :poll-seconds HTTP-POLL-SECONDS))]
                                             program))))
  (defn close []
    (.close server)
    (close-store))
  (BuiltInterpreter (fn [program]
                      (run (scheduled (with_handlers [(sim-time-handler :clock clock) (law-setup harness)] program))))
                    close
                    (fn [] harness)))


(defn build-interpreter [#^ str name]
  (cond
    (= name MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (interpreter-over (harness-of (fn [writer] (memory-records-handler store writer))) [] (fn [] None)))
    (= name PG)
      (do (setv connection (open-postgres)
                host (PgRecordsHost connection LAW-SCHEMA :unreachable-errors (pg-errors)
                                    :prefix (+ "t" (cut (. (uuid.uuid4) hex) 12) "_")))
          (defn close []
            (drop-records-tables host)
            (.close connection))
          (interpreter-over (harness-of (fn [writer] (pg-records-handler host writer))) [] close))
    (= name HTTP-MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (defn [contextmanager] memory-lease []
            (yield (fn [writer] (memory-records-handler store writer))))
          (http-interpreter memory-lease (fn [writer] (memory-records-handler store writer)) False (fn [] None)))
    (= name HTTP-PG)
      (do (setv prefix (+ "t" (cut (. (uuid.uuid4) hex) 12) "_")
                connection (open-postgres)
                host (PgRecordsHost connection LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix)
                pool (PgHostPool open-postgres LAW-SCHEMA :unreachable-errors (pg-errors) :prefix prefix :size 4))
          (defn close-pg []
            (.close pool)
            (drop-records-tables host)
            (.close connection))
          (http-interpreter pool.lease-handlers (fn [writer] (pg-records-handler host writer)) True close-pg))
    True (BuiltInterpreter (fn [program] (run (scheduled program))) (fn [] None) (fn [] None))))
