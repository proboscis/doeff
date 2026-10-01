;;; 記録の service の入口を割った口の検(#1280 — main.hy の records-settings・records-serving・records-connected)。
;;;
;;; 確かめること:
;;;   - 設定の読み records-settings は env と Secret の file を effect(ReadEnvironment・ReadText)で読み、既定を埋めた設定の値を作る。
;;;     必須の env が欠ければ起動を止める(黙って既定へ倒さない)
;;;   - 本体の設定 records-serving は設定の値と表の宣言と置き場の選びだけから作る(宛先・名簿・手入れ・本文の上限)。表の用意と /readyz の
;;;     問いは置き場の選び(PostgreSQL = PG-STORE・memory = memory-store-choice)が決める
;;;   - 土台の口 records-connected は外側(scheduler・Await の橋・session の値の置き場・時計・止めの合図)を持たず、呼び手が置いた外側の
;;;     内側に差すだけで本体 serve-records が閉じる — 本物の待ち受け(127.0.0.1 の空き port)で口を開き、止めの合図で 0 で終わる
(require doeff-hy.macros [deftest defhandler defk <- val])
(import dataclasses)
(import collections.abc [Callable])
(import doeff [Program EffectBase run with-handlers])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.process_effects [ReadEnvironment EnvEntry])
(import doeff_core_effects.file_effects [ReadText])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_time [async-time-handler])
(import doeff_records.laws [LAW-SCHEMA])
(import doeff_records.memory [MemoryStore memory-store-choice])
(import doeff_records.pg_sql [DEFAULT-PREFIX])
(import doeff_records.http_server [MaintenancePlan RecordsServing REQUEST-MAX-BYTES])
(import doeff_records.main [RecordsSettings records-settings records-serving records-connected records-process PG-STORE store-reachable
                            ENV-PG-URL-FILE ENV-PRINCIPALS-FILE ENV-HOSTNAME ENV-HOST ENV-PORT ENV-POOL-SIZE DEFAULT-PORT
                            DEFAULT-POOL-SIZE DEFAULT-MAINTENANCE-SECONDS DEFAULT-KEEP-CHANGES-SECONDS])

(val PG-URL-PATH "/secrets/pg-url")
(val PRINCIPALS-PATH "/secrets/principals.json")
(val DSN "postgresql://records@db/records")
;; 名簿の綴り(64 hex の digest — 中身は検の値)。
(val PRINCIPALS-JSON "{\"version\": 1, \"principals\": [{\"name\": \"maker\", \"tokenSha256\": \"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\"}]}")
(val FILES {PG-URL-PATH (+ DSN "\n") PRINCIPALS-PATH PRINCIPALS-JSON})


(defhandler scripted-environment [#^ dict environ #^ dict files]
  "env と file の読みに、検の表から答えるため(I/O なし)。"
  {:tags {:context "records" :role "foundation"}}
  ;; 引数に残す理由: env と file の中身は検の筋書きごとの値。
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name environ) (EnvEntry :name name :value (get environ name))))))
  (ReadText [path]
    (resume (get files path))))


(defhandler stop-at-once
  "止めの合図を初めから立てておくため(待ち受けを開いた後の最初の問いで閉じる)。"
  {:tags {:context "records" :role "foundation"}}
  (StopRequested []
    (resume "検の止め")))


(defk stripped [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "records" :role "judgment"}}
  "接続 URL の file の中身から DSN を読む(検の呼び手の綴り — 1 行の URL)。"
  (.strip text))


(defk settings-under [environ]
  {:pre [(: environ dict)] :post [(: % RecordsSettings)] :tags {:context "records" :role "foundation"}}
  "検の env と file の表の下で設定を読むため。"
  (<- settings RecordsSettings (with-handlers [(scripted-environment environ FILES)] (records-settings stripped)))
  settings)


(val REQUIRED-ENV {ENV-PG-URL-FILE PG-URL-PATH ENV-PRINCIPALS-FILE PRINCIPALS-PATH ENV-HOSTNAME "records-0"})


(deftest test-settings-are-read-from-the-environment-with-defaults
  (<- settings RecordsSettings (settings-under REQUIRED-ENV))
  (assert (= settings.dsn DSN) settings)
  (assert (= (tuple (.keys settings.roster.digests)) #("maker")) settings.roster)
  (assert (= settings.prefix DEFAULT-PREFIX) settings)
  (assert (= settings.origin-host "records-0") settings)
  (assert (= settings.pool-size DEFAULT-POOL-SIZE) settings)
  (assert (= settings.address.port DEFAULT-PORT) settings)
  (assert (= settings.maintenance (MaintenancePlan :interval-seconds DEFAULT-MAINTENANCE-SECONDS
                                                  :keep-seconds DEFAULT-KEEP-CHANGES-SECONDS))
          settings))


(deftest test-settings-take-the-declared-values-over-the-defaults
  (<- settings RecordsSettings (settings-under (| REQUIRED-ENV {ENV-HOST "127.0.0.1" ENV-PORT "9001" ENV-POOL-SIZE "3"})))
  (assert (= settings.address (HttpAddress :host "127.0.0.1" :port 9001)) settings)
  (assert (= settings.pool-size 3) settings))


(deftest test-a-missing-required-environment-stops-the-start
  (val environ (dict REQUIRED-ENV))
  (del (get environ ENV-PRINCIPALS-FILE))
  (var refused None)
  (try
    (! (settings-under environ))
    (except [e SystemExit]
      (:= refused e)))
  (assert (is-not refused None) "必須の env が欠けても設定が読めた")
  (assert (in ENV-PRINCIPALS-FILE (str refused)) refused))


(deftest test-serving-is-built-from-the-schema-and-the-settings-only
  (<- settings RecordsSettings (settings-under REQUIRED-ENV))
  (<- serving RecordsServing (records-serving LAW-SCHEMA settings PG-STORE))
  (assert (= serving.address settings.address) serving)
  (assert (is serving.schema LAW-SCHEMA) serving)
  (assert (is serving.roster settings.roster) serving)
  (assert (= serving.maintenance settings.maintenance) serving)
  (assert (= serving.max-bytes REQUEST-MAX-BYTES) serving)
  (assert (isinstance serving.prepare (| Program EffectBase)) serving)
  ;; PostgreSQL の選びは /readyz で置き場を問う。
  (assert (is serving.readiness store-reachable) serving))


(deftest test-serving-takes-the-store-from-the-choice
  ;; 置き場の選びを替えると、表の用意と /readyz の問いがその選びの物になる(PostgreSQL に固定しない — #1608)。memory の選びは
  ;; /readyz を問わず(用意が済めば ready)、表の用意は memory の置き場の handler の関数を返す(I/O なし)。
  (<- settings RecordsSettings (settings-under REQUIRED-ENV))
  (val store (MemoryStore LAW-SCHEMA))
  (<- serving RecordsServing (records-serving LAW-SCHEMA settings (! (memory-store-choice store))))
  (assert (is serving.readiness None) serving)
  (<- handler-for Callable serving.prepare)
  (assert (callable (handler-for "maker")) handler-for))


(defk callers-outer [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "records" :role "foundation"}}
  "呼び手の系が持つ外側の代役(scheduler・Await の橋・session の値の置き場・時計・止めの合図 — 止めは初めから立つ)。"
  (<- answer (scheduled (with-handlers [(await-handler) (state) (async-time-handler) stop-at-once] body)))
  answer)


(deftest test-the-connected-port-closes-the-entry-program-under-the-callers-outer
  (<- settings RecordsSettings (settings-under (| REQUIRED-ENV {ENV-HOST "127.0.0.1" ENV-PORT "0"})))
  ;; 置き場は memory の選びで渡す(PostgreSQL に届かない検の機体でも、土台の口と本体の組み立ては本番と同じ)。手入れは立てない。
  (<- serving-read RecordsServing (records-serving LAW-SCHEMA settings (! (memory-store-choice (MemoryStore LAW-SCHEMA)))))
  (val serving (dataclasses.replace serving-read :maintenance None))
  (val code (run (records-process (fn [body] (callers-outer (records-connected settings body))) serving)))
  (assert (= code 0) code))
