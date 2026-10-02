;;; 記録の service の composition root — env と Secret の file を読み、PostgreSQL の答え手・待ち受けを組んで、入口の Program
;;; (http_server.hy の serve-records)を本番の土台の下で走らせる。判断はここに無い(流れ = service.hy・
;;; 判断 = admission.hy・綴り = wire.hy・待ち受けの形 = http_server.hy)。
;;;
;;; 割り方(#1280 — 呼び手の系が自分の process の外側〔scheduler・時計・止めの合図〕の下へ、土台の口だけを差せるように):
;;;   records-settings       設定の読み: env と Secret の file を effect(ReadEnvironment・ReadText)で読み、設定の値 RecordsSettings にする
;;;   records-serving        本体の設定: 表の宣言 schema と設定の値と置き場の選び(StoreChoice — PostgreSQL = PG-STORE・memory =
;;;                          doeff_records.memory の memory-store-choice)から、入口の Program の設定(RecordsServing)を作る(#1608)
;;;   records-process        本体の組み立て — 土台 foundation を引数で受け、serve-records を土台の下で走らせる(#834 の形・会話の記録の
;;;                          service の record-process と同じ)。本番と検が同じ 1 つを通る
;;;   records-connected      土台の口(待ち受けと置き場): PostgreSQL の接続の貸し出しと pool を開き、待ち受け(aiohttp-http-server)・名乗りの
;;;                          印字・PostgreSQL の答え手(pooled-postgres-sql-handler — 接続の許可は scheduler の semaphore・driver の I/O だけを
;;;                          pool の thread へ)の下で本体を走らせ、終われば接続と pool を閉じる。外側(scheduler・Await の橋・session の値の
;;;                          置き場・時計・止めの合図)は持たない — 呼び手が自分の外側の内側に差す
;;;   records-foundation     単独で起こす時の本番の土台の全部 = 外側(scheduler・Await の橋・session の値の置き場・async-time-handler
;;;                          〔scheduler を塞がない時計・#880 A1〕・止めの合図 os-signal-stop-handler)+ records-connected
;;;   serve-records-service  単独で起こす入口: records-settings → records-serving → records-foundation の下で records-process。
;;;                          答え = process の終わりの code
;;;
;;; 置き場の宣言(RecordsSchema)と接続 URL の file の読み方は呼び手の系が持つので、呼び手の系の入口がこの部品を呼ぶ:
;;;
;;;   (import myapp.tables [SCHEMA])
;;;   (defk dsn-of [text] {:pre [(: text str)] :post [(: % str)]} (.strip text))
;;;   (when (= __name__ "__main__")
;;;     (sys.exit (run (with-handlers [subprocess-handler os-file-handler] (serve-records-service SCHEMA dsn-of)))))
;;;
;;; 自分の process の外側を持つ系は、records-settings と records-serving(置き場の選びを渡す)で値を作り、(records-process (fn [body] (<自分の外側>
;;; (records-connected settings body))) serving) を撃つ(外側に scheduled・await-handler・state・時計・StopRequested の答え手が要る)。
;;;
;;; dsn-of = 接続 URL の file の中身 → PostgreSQL の DSN の Program(file の綴りは呼び手の系ごとに違う — 例: env の 1 行 KEY=URL)。
;;; env の読み(ReadEnvironment)と file の読み(ReadText)は呼び手が外側に置く答え手(doeff_core_effects の subprocess-handler・os-file-handler)が答える。
;;;
;;; 受け取る env(宣言はここ 1 点・既定の宿の literal を持たない)。名は RECORDS_ で始める — DOEFF_ で始まる名は doeff-cluster が worker の
;;; 組む環境変数として予約し、job の宣言の :environ に置けない(doeff_cluster/runtime_env_model.hy の RESERVED-ENV-PREFIXES)ので、この service を
;;; doeff-cluster の job として動かせるよう、以前の DOEFF_RECORDS_* から改めた(2026-10-01)。
;;;   RECORDS_PG_URL_FILE            PostgreSQL の接続 URL の file(必須・Secret の mount)
;;;   RECORDS_PREFIX                 表の名の接頭辞(既定 records_ — 同じ database の別の置き場の表と混ざらない)
;;;   RECORDS_HOST / _PORT           HTTP の口(既定 0.0.0.0 / 8875)
;;;   RECORDS_POOL_SIZE              要求に同時に貸す接続の上限(既定 8 — 手入れの係の 1 本を足した数を開く)
;;;   RECORDS_MAINTENANCE_SECONDS    手入れの間隔(既定 60)
;;;   RECORDS_KEEP_CHANGES_SECONDS   変更の列に残す秒(既定 604800 = 7 日 — これより遅れた読み手は Reset で一覧から読み直す)
;;;   RECORDS_ORIGIN_HOST            行に刻む機体の名(既定 = env HOSTNAME — k8s は pod の名を置く。どちらも無ければ起動しない)
;;; 表は起動時に CREATE ... IF NOT EXISTS だけを流す(既存の表を消さない・変えない)。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import sys)
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import concurrent.futures [ThreadPoolExecutor])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.process_effects [ReadEnvironment])
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.stop_signal_handlers [os-signal-stop-handler])
(import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase])
(import doeff_core_effects.sql_effects [SqlQuery SqlRows SqlFailed SqlUnreachable])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_time [async-time-handler])
(import doeff_records.values [RecordsSchema])
(import doeff_records.pg [pg-records-handler prepare-records-store DEFAULT-POLL-SECONDS])
(import doeff_records.pg_sql [DEFAULT-PREFIX])
(import doeff_records.http_server [MaintenancePlan RecordsServing RecordsListening REQUEST-MAX-BYTES serve-records])
(import doeff_records.store_choice [StoreChoice StorePressure PressureUnread])

(val MODULE-TAGS {:context "records" :role "entry"})

(val ENV-PG-URL-FILE "RECORDS_PG_URL_FILE")
(val ENV-PREFIX "RECORDS_PREFIX")
(val ENV-HOST "RECORDS_HOST")
(val ENV-PORT "RECORDS_PORT")
(val ENV-POOL-SIZE "RECORDS_POOL_SIZE")
(val ENV-MAINTENANCE-SECONDS "RECORDS_MAINTENANCE_SECONDS")
(val ENV-KEEP-CHANGES-SECONDS "RECORDS_KEEP_CHANGES_SECONDS")
(val ENV-ORIGIN-HOST "RECORDS_ORIGIN_HOST")
(val ENV-HOSTNAME "HOSTNAME")
(val DEFAULT-HOST "0.0.0.0")
(val DEFAULT-PORT 8875)
(val DEFAULT-POOL-SIZE 8)
(val DEFAULT-MAINTENANCE-SECONDS 60.0)
(val DEFAULT-KEEP-CHANGES-SECONDS 604800.0)
;; SqlQuery に書く database の名(この service の置き場は 1 つ — 答え手の宣言と揃える)。
(val DATABASE "records")
;; 止めの合図を問い直す間隔の秒と、待ち受けを閉じる時に送りの箱を流し切る上限の秒(ws を持たないので 0)。
(val STOP-POLL-SECONDS 0.5)
(val DRAIN-SECONDS 0.0)


(defrecord RecordsSettings
  "env と Secret の file から読んだ設定の値(records-settings が作る — 土台の口 records-connected と本体の設定 records-serving の材料):
   dsn = PostgreSQL の DSN・prefix = 表の名の接頭辞・origin-host = 行に刻む機体の名・pool-size = 要求に同時に貸す
   接続の上限(手入れの係の 1 本は別に足す)・address = 待ち受けの宛先・maintenance = 手入れの周期。資源(接続の貸し出しと pool)は持たない —
   records-connected が開いて閉じる。"
  {:check [(> (len dsn) 0) (> (len prefix) 0) (> (len origin-host) 0) (> pool-size 0)]}
  (#^ str dsn)
  (#^ str prefix)
  (#^ str origin-host)
  (#^ int pool-size)
  (#^ HttpAddress address)
  (#^ MaintenancePlan maintenance))


;; --- env と Secret の file の読み --------------------------------------------------------------------------------------------------

(defk env-text [name default]
  {:pre [(: name str) (: default str)] :post [(: % str)] :tags {:context "records" :role "entry"}}
  "env の文字列の値を読むため(空・未宣言 = 既定)。"
  (<- found tuple (ReadEnvironment #(name)))
  (val value (if found (.strip (. (get found 0) value)) ""))
  (if (= value "") default value))


(defk required-env [name]
  {:pre [(: name str)] :post [(: % str)] :tags {:context "records" :role "entry"}}
  "必須の env を読むため(無ければ起動を止める — 黙って既定へ倒さない)。"
  (<- value str (env-text name ""))
  (when (= value "")
    (raise (SystemExit (.format "記録の service: env {} が要る" name))))
  value)


(defk env-number [name default]
  {:pre [(: name str) (: default float)] :post [(: % float)] :tags {:context "records" :role "entry"}}
  "env の 0 より大きい数を読むため(空・未宣言 = 既定・0 以下は起動を止める)。"
  (<- text str (env-text name ""))
  (when (= text "")
    (return default))
  (val parsed (float text))
  (when (<= parsed 0)
    (raise (SystemExit (.format "記録の service: env {} は 0 より大きい数(読んだ値 {})" name text))))
  parsed)


(defk read-secret [path]
  {:pre [(: path str)] :post [(: % str)] :tags {:context "records" :role "entry"}}
  "Secret の mount の file を読むため(末尾の改行を除く。読めなければ起動を止める)。"
  (<- content (| str FileFailed) (ReadText path))
  (when (isinstance content FileFailed)
    (raise (SystemExit (.format "記録の service: file {} を読めない({})" path content.detail))))
  (.strip content))


(defk origin-host []
  {:pre [] :post [(: % str)] :tags {:context "records" :role "entry"}}
  "行に刻む機体の名を読むため(RECORDS_ORIGIN_HOST → HOSTNAME。どちらも無ければ起動を止める)。"
  (<- declared str (env-text ENV-ORIGIN-HOST ""))
  (when (!= declared "")
    (return declared))
  (<- hostname str (required-env ENV-HOSTNAME))
  hostname)


;; --- 本番の用意と土台 ----------------------------------------------------------------------------------------------------------------

(defk pg-handlers-of [schema prefix host]
  {:pre [(: schema RecordsSchema) (: prefix str) (: host str)] :post [(: % (get Callable #([str] object)))] :tags {:context "records" :role "entry"}}
  "表を用意し(移行の錠の中で CREATE ... IF NOT EXISTS)、書き手の名 → PostgreSQL の置き場の handler の関数を返すため(入口の用意の task が
   1 度だけ撃つ)。"
  (<- store (prepare-records-store DATABASE schema prefix))
  (fn [writer] (pg-records-handler store writer host DEFAULT-POLL-SECONDS)))


(defhandler printed-listening [#^ str prefix]
  "本番の土台で、待ち受けが結んだ宛先を 1 行名乗るため。"
  {:tags {:context "records" :role "entry"}}
  ;; 引数に残す理由: prefix は名乗りの行に載せる組み立ての値(Ask で読む設定ではない)。
  (RecordsListening [address]
    (print (.format "記録の service: {}:{} で待ち受ける(接頭辞 {}・表の用意は task)" address.host address.port prefix)
           :file sys.stderr :flush True)
    (resume None)))


(defk records-connected [settings body]
  {:tp [T] :pre [(: settings RecordsSettings) (: body (| (get Program #(T object)) (get EffectBase T)))] :post [(: % T)]
   :tags {:context "records" :role "entry"}}
  "土台の口(頭の註 — 待ち受けと置き場)の下で本体を走らせるため: PostgreSQL の接続の貸し出しと pool を開き、待ち受け → 名乗り →
   PostgreSQL の並び(外側が先)で本体を走らせ、終われば(例外でも)接続と pool を閉じる。外側(scheduler・Await の橋・session の値の
   置き場・時計・止めの合図)は呼び手が置く。答えは本体の答え(型の引数 T — 呼び手の型検査へ本体の答えの型を運ぶ・#2893)。"
  ;; 要求に貸す pool-size 本と、手入れの係の 1 本。pool の worker の数 = 宣言した接続の数(pooled-postgres-sql-handler の契約)。
  ;; 接続の貸し出しは借りた時に初めて開く(作るだけでは繋がない)。
  (val connections (PostgresConnections #((PostgresDatabase :name DATABASE :dsn settings.dsn)) :size (+ settings.pool-size 1)))
  (val pool (ThreadPoolExecutor :max-workers (+ settings.pool-size 1) :thread-name-prefix "records-pg"))
  (try
    (<- answer (with-handlers [aiohttp-http-server (printed-listening settings.prefix) (pooled-postgres-sql-handler connections pool)] body))
    answer
    (finally
      (.close connections)
      (.shutdown pool :wait False :cancel-futures True))))


(defk records-foundation [settings body]
  {:pre [(: settings RecordsSettings) (: body (| Program EffectBase))] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "単独で起こす時の本番の土台の全部(#834 の形)で本体(serve-records — 答え = process の終わりの code)を走らせるため。並び(外側が先):
   scheduler → Await の橋 → session の値の置き場 → 時計 → 止めの合図 → 土台の口 records-connected(待ち受け → 名乗り → PostgreSQL)。"
  (<- answer (scheduled (with-handlers [(await-handler) (state) (async-time-handler) os-signal-stop-handler]
                                       (records-connected settings body))))
  answer)


(defk records-settings [dsn-of]
  {:pre [(: dsn-of (get Callable #([str] (get Program #(str object)))))] :post [(: % RecordsSettings)] :tags {:context "records" :role "entry"}}
  "env と Secret の file を読み、設定の値を作るため(頭の註の env の一覧 — 必須が欠ければ起動を止める)。dsn-of = 接続 URL の file の中身 →
   DSN の Program。読みは ReadEnvironment・ReadText(呼び手の外側の答え手が答える)。"
  (<- url-text str (read-secret (! (required-env ENV-PG-URL-FILE))))
  (<- dsn str (dsn-of url-text))
  (<- prefix str (env-text ENV-PREFIX DEFAULT-PREFIX))
  (<- host str (origin-host))
  (<- size float (env-number ENV-POOL-SIZE (float DEFAULT-POOL-SIZE)))
  (<- listen-host str (env-text ENV-HOST DEFAULT-HOST))
  (<- listen-port str (env-text ENV-PORT (str DEFAULT-PORT)))
  (<- interval float (env-number ENV-MAINTENANCE-SECONDS DEFAULT-MAINTENANCE-SECONDS))
  (<- keep float (env-number ENV-KEEP-CHANGES-SECONDS DEFAULT-KEEP-CHANGES-SECONDS))
  (RecordsSettings :dsn dsn :prefix prefix :origin-host host :pool-size (int size)
                   :address (HttpAddress :host listen-host :port (int listen-port))
                   :maintenance (MaintenancePlan :interval-seconds interval :keep-seconds keep)))


(defk store-reachable []
  {:pre [] :post [(: % bool)] :tags {:context "records" :role "entry"}}
  "/readyz の問い: PostgreSQL の置き場へ SELECT 1 を撃ち、答えが行なら True(届かない・断られた なら False)— 置き場に届くかを口の外から
   見分けるため。"
  (<- answer (SqlQuery DATABASE "SELECT 1" #()))
  (isinstance answer SqlRows))


;; /readyz の詰まりの読み(#1858): この置き場の database で錠(pg_advisory_xact_lock を含む)を待っている接続の本数と、transaction を開いた
;; まま止まっている接続のうち最も長い秒。2026-10-01 19:4x の着地の台帳の事故では readyz が「1.0 秒の内に答えない」だけで、錠を待つ本数も
;; 持ち主も見えなかった。
(val PRESSURE-SQL
  (+ "SELECT (SELECT count(DISTINCT l.pid) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
     " WHERE NOT l.granted AND a.datname = current_database()),"
     " (SELECT COALESCE(EXTRACT(EPOCH FROM max(now() - state_change)), 0)::float8 FROM pg_stat_activity"
     " WHERE state IN ('idle in transaction', 'idle in transaction (aborted)') AND datname = current_database())"))


(defk store-pressure-of-rows [answer]
  {:pre [(: answer (| SqlRows SqlFailed SqlUnreachable))] :post [(: % (| StorePressure PressureUnread))] :tags {:context "records" :role "entry"}}
  "詰まりの読みの SQL の答えを型へ読むため: 1 行 2 列(本数・秒)だけを StorePressure にし、それ以外(失敗・届かない・形の外)は理由を名乗る。"
  (match answer
    (SqlRows :rows #(#(waiters idle)))
      (if (and (isinstance waiters int) (not (isinstance waiters bool)) (isinstance idle #(int float)) (not (isinstance idle bool)))
          (StorePressure :lock-waiters waiters :idle-in-transaction-max-seconds (float idle))
          (PressureUnread :reason (.format "詰まりの読みの答えの形が違う: {!r}" answer.rows)))
    (SqlRows) (PressureUnread :reason (.format "詰まりの読みの答えが 1 行 2 列でない: {!r}" answer.rows))
    (SqlFailed :reason reason) (PressureUnread :reason (+ "詰まりの読みが断られた: " reason))
    (SqlUnreachable :reason reason) (PressureUnread :reason (+ "詰まりの読みが届かない: " reason))
    _ (PressureUnread :reason (.format "詰まりの読みの知らない答え: {!r}" answer))))


(defk store-pressure-pg []
  {:pre [] :post [(: % (| StorePressure PressureUnread))] :tags {:context "records" :role "entry"}}
  "/readyz の詰まりの読み(PostgreSQL): 錠を待つ接続の本数と idle in transaction の最長の秒を 1 本の SQL で読むため(#1858)。"
  (<- answer (SqlQuery DATABASE PRESSURE-SQL #()))
  (! (store-pressure-of-rows answer)))


;; PostgreSQL の置き場の選び(表の用意 pg-handlers-of・/readyz の問い store-reachable・詰まりの読み store-pressure-pg)— 単独の本番と、
;; PostgreSQL の土台の口を差す呼び手が records-serving に渡す(答える SQL の答え手は土台の口 records-connected が置く)。
(val PG-STORE (StoreChoice :prepare-of pg-handlers-of :readiness store-reachable :pressure store-pressure-pg))


(defk records-serving [schema settings choice]
  {:pre [(: schema RecordsSchema) (: settings RecordsSettings) (: choice StoreChoice)] :post [(: % RecordsServing)]
   :tags {:context "records" :role "entry"}}
  "本体(serve-records)の設定を、表の宣言 schema と設定の値と置き場の選び choice から作るため。表の用意(prepare)と /readyz の問い
   (readiness)は choice が決める — PostgreSQL = PG-STORE・memory = doeff_records.memory の memory-store-choice。
   以前は PostgreSQL に固定で、使い手が dataclasses.replace で上書きしていた。"
  (RecordsServing :address settings.address :schema schema
                  :prepare (choice.prepare-of schema settings.prefix settings.origin-host) :request-handlers #()
                  :max-bytes REQUEST-MAX-BYTES :maintenance settings.maintenance
                  :stop-poll-seconds STOP-POLL-SECONDS :drain-seconds DRAIN-SECONDS :readiness choice.readiness
                  :pressure choice.pressure))


(defk records-process [foundation serving]
  {:pre [(: foundation Callable) (: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "記録の service の Program: 土台 foundation(本体 → 土台の下で走らせる Program — 単独の本番 = 設定を渡した records-foundation・外側を
   持つ呼び手 = 自分の外側 + records-connected・検 = 台本の待ち受けの土台)の下で serve-records を走らせ、process の終わりの code を返すため。"
  (<- code int (foundation (serve-records serving)))
  code)


(defk serve-records-service [schema dsn-of]
  {:pre [(: schema RecordsSchema) (: dsn-of (get Callable #([str] (get Program #(str object)))))] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "記録の service を単独で起こすため: env を読んで設定の値を作り(records-settings)、本体の設定(records-serving)を単独の本番の土台
   (records-foundation — 接続と pool は土台の口が開いて閉じる)の下で records-process に撃つ。dsn-of = 接続 URL の file の中身 → DSN の
   Program。答え = process の終わりの code(用意の失敗は例外のまま上げる)。"
  (<- settings RecordsSettings (records-settings dsn-of))
  (<- serving RecordsServing (records-serving schema settings PG-STORE))
  (<- code int (records-process (fn [body] (records-foundation settings body)) serving))
  (print "記録の service: 止まった" :file sys.stderr :flush True)
  code)
