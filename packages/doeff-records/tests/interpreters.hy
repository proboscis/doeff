;;; 検の解釈器(composition root)— 同じ法の Program を、handler の組だけ替えて走らせる。
;;;
;;;   plain   scheduler だけ(純関数の検)。
;;;   memory  memory の handler + 仮想の時計(sim-time-handler)。
;;;   pg      PostgreSQL の handler + 仮想の時計。SQL の effect の答え手は doeff の postgres-sql-handler(呼び 1 つに thread 1 本)。
;;;           env DOEFF_RECORDS_TEST_PG_DSN の置き場に、検ごとに乱数の接頭辞の表を作り、終わりに消す。env が無ければ skip
;;;           (psycopg は依存に無い — `uv run --with psycopg` で足す)。
;;;   pg-pooled
;;;           pg と同じ置き場で、SQL の effect の答え手だけを pooled-postgres-sql-handler(scheduler を塞がない版)にする。
;;;   http-memory / http-pg
;;;           記録の service の HTTP の口(127.0.0.1 の空き port)を memory / PostgreSQL の置き場の上に開き、法の Program は
;;;           client の handler(http-records-handler)で公開 effect を撃つ。書き手の名は身元の名簿の token で運ぶ(LAW-TOKENS)。
;;;           service と client は同じ仮想の時計(SimClock 1 つ)を読む。検の口と手入れの effect(AdvanceStoreEpoch・SweepExpired・
;;;           PruneChanges — HTTP の口に出さない)は、client の外側に被せた置き場の handler が直に答える。
;;;   http-effect-memory
;;;           http-memory と同じ口と置き場で、client の要求の送り方だけを EffectTransport にする(要求は HttpRequest の effect —
;;;           答え手は外側の await-handler と http-production-handler)。送り方が替わっても法の答えが同じことを確かめる。
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
(import doeff_records.pg [pg-records-handler drop-records-tables prepare-records-store DEFAULT-POLL-SECONDS])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase postgres-sql-handler])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_records.principals [Roster token-digest])
(import doeff_records.http_server [RecordsServerConfig start-records-server])
(import doeff_records.http_client [RecordsEndpoint BlockingTransport EffectTransport http-records-handler])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_hy.frozen [FrozenMap])
(import contextlib [contextmanager nullcontext])

(setv PLAIN "plain" MEMORY "memory" PG "pg" PG-POOLED "pg-pooled" HTTP-MEMORY "http-memory" HTTP-PG "http-pg"
      HTTP-EFFECT-MEMORY "http-effect-memory")
(setv PG-DSN-VARIABLE "DOEFF_RECORDS_TEST_PG_DSN")
;; 検の置き場の database の名(SqlQuery に書く名 — 答え手の宣言と揃える)と、行に刻む機体の名。
(setv DATABASE "records" ORIGIN-HOST "law-host")


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
    (and (in name #(PG PG-POOLED HTTP-PG)) (not (.get os.environ PG-DSN-VARIABLE)))
      (.format "PostgreSQL の法は env {} に使い捨ての置き場の DSN を置いた時だけ走る" PG-DSN-VARIABLE)
    True ""))


(defn open-postgres []
  "psycopg は依存に無い(接続は composition root が開く)ので、ここで名指しで読む。自動 commit の接続(検が素の文を流す時だけ)。"
  (setv psycopg (importlib.import-module "psycopg"))
  (psycopg.connect (get os.environ PG-DSN-VARIABLE) :autocommit True))


(defn postgres-connections [[size 4]]
  "検の置き場の接続の貸し出し(SQL の effect の答え手に渡す)。"
  (PostgresConnections #((PostgresDatabase :name DATABASE :dsn (get os.environ PG-DSN-VARIABLE))) :size size))


(defn fresh-prefix []
  "検ごとの乱数の接頭辞(同じ database の別の検の表と混ざらない)。"
  (+ "t" (cut (. (uuid.uuid4) hex) 12) "_"))


(defn run-sql [connections program]
  "SQL の答え手(postgres-sql-handler)と仮想の時計の下で program を 1 回走らせる(検の組み立てと後片付け)。"
  (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (postgres-sql-handler connections)] program))))


(defn prepared-store [connections #^ str prefix]  ; defk にできない: 検の解釈器の組み立て(Program の外)で呼ぶ
  "表を用意して(prepare-records-store)置き場を返す — 検の置き場 1 つにつき 1 度。"
  (run-sql connections (prepare-records-store DATABASE LAW-SCHEMA prefix)))


(defn records-handler-for [store]
  "書き手の名 → その書き手の PostgreSQL の置き場の handler(検の値の機体の名と読み直しの間隔で)。"
  (fn [writer] (pg-records-handler store writer ORIGIN-HOST DEFAULT-POLL-SECONDS)))


(defn harness-of [wrap]
  (LawHarness (fn [writer program] (with_handlers [(wrap writer)] program))))


(defn interpreter-over [harness stack close]
  (BuiltInterpreter (fn [program]
                      (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock))] stack [(law-setup harness)])
                                                     program))))
                    close
                    (fn [] harness)))


;; 法の書き手の名 → 身元の token(検だけの値 — 名簿には sha256 だけを載せる)。
(setv LAW-TOKENS (dfor writer ["maker" "painter" "closer" "stranger" "overseer"] writer (+ "law-token-" writer)))
(setv HTTP-POLL-SECONDS 0.05)


(defn law-roster []
  "法の書き手 4 人の身元の名簿(service の組み立てに渡す)。"
  (Roster (FrozenMap (dfor #(writer token) (.items LAW-TOKENS) writer (run (token-digest token))))))


(defn sim-runner [clock [answerers []]]
  "service が要求ごとに Program を走らせる関数 — 法の側と同じ仮想の時計を読む。answerers = 置き場の外側に置く答え手(SQL の答え手)。"
  (fn [program] (run (scheduled (with_handlers (+ [(sim-time-handler :clock clock)] answerers) program)))))


(defn http-interpreter [lease-handlers backing concurrent close-store [transport (BlockingTransport)] [answerers []]]
  "HTTP の口を開き、法の書き手を client の handler(その書き手の token)で包む組。backing = 書き手の名 → 置き場の handler
   (検の口と手入れの effect に直に答える — client の外側に被せる)。transport = client の要求の送り方(EffectTransport なら
   HttpRequest の答え手 await-handler と http-production-handler を組の最も外側に置く)。"
  (setv clock (SimClock)
        server (start-records-server (RecordsServerConfig LAW-SCHEMA (law-roster) lease-handlers (sim-runner clock answerers)
                                                          :concurrent concurrent))
        harness (LawHarness (fn [writer program]
                              (with_handlers [(backing writer)
                                              (http-records-handler (RecordsEndpoint server.url (get LAW-TOKENS writer)
                                                                                     :poll-seconds HTTP-POLL-SECONDS
                                                                                     :transport transport))]
                                             program))))
  (defn close []
    (.close server)
    (close-store))
  (BuiltInterpreter (fn [program]
                      (run (scheduled (with_handlers (+ (if (isinstance transport EffectTransport)
                                                            [(await-handler) (http-production-handler)]
                                                            [])
                                                        [(sim-time-handler :clock clock)] answerers [(law-setup harness)])
                                                     program))))
                    close
                    (fn [] harness)))


(defn build-interpreter [#^ str name]
  (cond
    (= name MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (interpreter-over (harness-of (fn [writer] (memory-records-handler store writer))) [] (fn [] None)))
    (= name PG)
      (do (setv connections (postgres-connections)
                store (prepared-store connections (fresh-prefix)))
          (defn close []
            (run-sql connections (drop-records-tables store))
            (.close connections))
          (interpreter-over (harness-of (records-handler-for store)) [(postgres-sql-handler connections)] close))
    (= name PG-POOLED)
      (do (setv connections (postgres-connections)
                pool (ThreadPoolExecutor :max-workers 4)
                store (prepared-store connections (fresh-prefix)))
          (defn close []
            (run-sql connections (drop-records-tables store))
            (.shutdown pool)
            (.close connections))
          (interpreter-over (harness-of (records-handler-for store)) [(state) (pooled-postgres-sql-handler connections pool)] close))
    (= name HTTP-MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (defn [contextmanager] memory-lease []
            (yield (fn [writer] (memory-records-handler store writer))))
          (http-interpreter memory-lease (fn [writer] (memory-records-handler store writer)) False (fn [] None)))
    (= name HTTP-EFFECT-MEMORY)
      (do (setv store (MemoryStore LAW-SCHEMA))
          (defn [contextmanager] memory-lease []
            (yield (fn [writer] (memory-records-handler store writer))))
          (http-interpreter memory-lease (fn [writer] (memory-records-handler store writer)) False (fn [] None) (EffectTransport)))
    (= name HTTP-PG)
      (do (setv connections (postgres-connections)
                store (prepared-store connections (fresh-prefix))
                handler-for (records-handler-for store))
          (defn close-pg []
            (run-sql connections (drop-records-tables store))
            (.close connections))
          (http-interpreter (fn [] (nullcontext handler-for)) handler-for True close-pg
                            :answerers [(postgres-sql-handler connections)]))
    True (BuiltInterpreter (fn [program] (run (scheduled program))) (fn [] None) (fn [] None))))
