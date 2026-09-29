;;; 記録の service の composition root — env と Secret の file を読み、身元の名簿・PostgreSQL の答え手・待ち受けを組んで、入口の Program
;;; (http_server.hy の serve-records)を本番の土台の下で走らせる(agora-redesign #880 U7)。判断はここに無い(流れ = service.hy・
;;; 判断 = admission.hy・綴り = wire.hy・待ち受けの形 = http_server.hy)。
;;;
;;;   serve-records-service  入口: env を読み、土台の部品(RecordsParts)と入口の設定(RecordsServing)を作り、records-process を撃つ。止まった後に
;;;                          PostgreSQL の接続と pool を閉じる。答え = process の終わりの code
;;;   records-process        本体の組み立て — 土台 foundation を引数で受け、serve-records を土台の下で走らせる(#834 の形・会話の記録の
;;;                          service の record-process と同じ)。本番と検が同じ 1 つを通る
;;;   records-foundation     本番の土台: scheduler・Await の橋・session の値の置き場・async-time-handler(scheduler を塞がない時計・#880 A1)・
;;;                          止めの合図(os-signal-stop-handler)・待ち受け(aiohttp-http-server)・名乗りの印字・PostgreSQL
;;;                          (pooled-postgres-sql-handler — 接続の許可は scheduler の semaphore・driver の I/O だけを pool の thread へ)
;;;
;;; 置き場の宣言(RecordsSchema)と接続 URL の file の読み方は呼び手の系が持つので、呼び手の系の入口がこの部品を呼ぶ:
;;;
;;;   (import myapp.tables [SCHEMA])
;;;   (defk dsn-of [text] {:pre [(: text str)] :post [(: % str)]} (.strip text))
;;;   (when (= __name__ "__main__")
;;;     (sys.exit (run (with-handlers [subprocess-handler os-file-handler] (serve-records-service SCHEMA dsn-of)))))
;;;
;;; dsn-of = 接続 URL の file の中身 → PostgreSQL の DSN の Program(file の綴りは呼び手の系ごとに違う — 例: env の 1 行 KEY=URL)。
;;; env の読み(ReadEnvironment)と file の読み(ReadText)は呼び手が外側に置く答え手(doeff_core_effects の subprocess-handler・os-file-handler)が答える。
;;;
;;; 受け取る env(宣言はここ 1 点・既定の宿の literal を持たない):
;;;   DOEFF_RECORDS_PG_URL_FILE            PostgreSQL の接続 URL の file(必須・Secret の mount)
;;;   DOEFF_RECORDS_PRINCIPALS_FILE        身元の名簿 principals.json(必須・{version: 1, principals: [{name, tokenSha256}]})
;;;   DOEFF_RECORDS_PREFIX                 表の名の接頭辞(既定 records_ — 同じ database の別の置き場の表と混ざらない)
;;;   DOEFF_RECORDS_HOST / _PORT           HTTP の口(既定 0.0.0.0 / 8875)
;;;   DOEFF_RECORDS_POOL_SIZE              要求に同時に貸す接続の上限(既定 8 — 手入れの係の 1 本を足した数を開く)
;;;   DOEFF_RECORDS_MAINTENANCE_SECONDS    手入れの間隔(既定 60)
;;;   DOEFF_RECORDS_KEEP_CHANGES_SECONDS   変更の列に残す秒(既定 604800 = 7 日 — これより遅れた読み手は Reset で一覧から読み直す)
;;;   DOEFF_RECORDS_ORIGIN_HOST            行に刻む機体の名(既定 = env HOSTNAME — k8s は pod の名を置く。どちらも無ければ起動しない)
;;; 表は起動時に CREATE ... IF NOT EXISTS だけを流す(既存の表を消さない・変えない)。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord])
(import sys)
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import concurrent.futures [Executor ThreadPoolExecutor])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.handlers [await-handler state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.process_effects [ReadEnvironment])
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.stop_signal_handlers [os-signal-stop-handler])
(import doeff_core_effects.aiohttp_http_server [aiohttp-http-server])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import doeff_time [async-time-handler])
(import doeff_records.values [RecordsSchema])
(import doeff_records.principals [Roster decode-roster])
(import doeff_records.pg [pg-records-handler prepare-records-store DEFAULT-POLL-SECONDS])
(import doeff_records.pg_sql [DEFAULT-PREFIX])
(import doeff_records.http_server [MaintenancePlan RecordsServing RecordsListening REQUEST-MAX-BYTES serve-records])

(val MODULE-TAGS {:context "records" :role "entry"})

(val ENV-PG-URL-FILE "DOEFF_RECORDS_PG_URL_FILE")
(val ENV-PRINCIPALS-FILE "DOEFF_RECORDS_PRINCIPALS_FILE")
(val ENV-PREFIX "DOEFF_RECORDS_PREFIX")
(val ENV-HOST "DOEFF_RECORDS_HOST")
(val ENV-PORT "DOEFF_RECORDS_PORT")
(val ENV-POOL-SIZE "DOEFF_RECORDS_POOL_SIZE")
(val ENV-MAINTENANCE-SECONDS "DOEFF_RECORDS_MAINTENANCE_SECONDS")
(val ENV-KEEP-CHANGES-SECONDS "DOEFF_RECORDS_KEEP_CHANGES_SECONDS")
(val ENV-ORIGIN-HOST "DOEFF_RECORDS_ORIGIN_HOST")
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


(defrecord RecordsParts
  "本番の土台の部品(records-foundation が handler を字面で作る材料): connections = PostgreSQL の接続の貸し出し・pool = driver の I/O を回す
   thread の pool(worker の数 ≥ 宣言した接続の数の合計)・prefix = 名乗りに載せる表の接頭辞。資源(connections・pool)の持ち主は
   serve-records-service(作って閉じる)。"
  (#^ PostgresConnections connections)
  (#^ Executor pool)
  (#^ str prefix))


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
  "行に刻む機体の名を読むため(DOEFF_RECORDS_ORIGIN_HOST → HOSTNAME。どちらも無ければ起動を止める)。"
  (<- declared str (env-text ENV-ORIGIN-HOST ""))
  (when (!= declared "")
    (return declared))
  (<- hostname str (required-env ENV-HOSTNAME))
  hostname)


;; --- 本番の用意と土台 ----------------------------------------------------------------------------------------------------------------

(defk pg-handlers-of [schema prefix host]
  {:pre [(: schema RecordsSchema) (: prefix str) (: host str)] :post [(: % Callable)] :tags {:context "records" :role "entry"}}
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


(defk records-foundation [parts body]
  {:pre [(: parts RecordsParts) (: body (| Program EffectBase))] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "記録の service の本番の土台(#834 の形)で本体(serve-records — 答え = process の終わりの code)を走らせるため。並び(外側が先):
   scheduler → Await の橋 → session の値の置き場 → 時計 → 止めの合図 → 待ち受け → 名乗り → PostgreSQL。"
  (<- answer (scheduled (with-handlers [(await-handler) (state) (async-time-handler) os-signal-stop-handler aiohttp-http-server
                                        (printed-listening parts.prefix) (pooled-postgres-sql-handler parts.connections parts.pool)]
                                       body)))
  answer)


(defk records-process [foundation serving]
  {:pre [(: foundation Callable) (: serving RecordsServing)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "記録の service の Program: 土台 foundation(本体 → 土台の下で走らせる Program — 本番 = 部品を渡した records-foundation・検 = 台本の
   待ち受けの土台)の下で serve-records を走らせ、process の終わりの code を返すため。"
  (<- code int (foundation (serve-records serving)))
  code)


(defk serve-records-service [schema dsn-of]
  {:pre [(: schema RecordsSchema) (: dsn-of Callable)] :post [(: % int)] :tags {:context "records" :role "entry"}}
  "記録の service を起こすため: env を読み、土台の部品と入口の設定を作って records-process を撃ち、止まった後に接続と pool を閉じる。
   dsn-of = 接続 URL の file の中身 → DSN の Program。答え = process の終わりの code(用意の失敗は例外のまま上げる)。"
  (<- url-text str (read-secret (! (required-env ENV-PG-URL-FILE))))
  (<- dsn str (dsn-of url-text))
  (<- roster Roster (decode-roster (! (read-secret (! (required-env ENV-PRINCIPALS-FILE))))))
  (<- prefix str (env-text ENV-PREFIX DEFAULT-PREFIX))
  (<- host str (origin-host))
  (val size (int (! (env-number ENV-POOL-SIZE (float DEFAULT-POOL-SIZE)))))
  ;; 要求に貸す size 本と、手入れの係の 1 本。pool の worker の数 = 宣言した接続の数(pooled-postgres-sql-handler の契約)。
  (val connections (PostgresConnections #((PostgresDatabase :name DATABASE :dsn dsn)) :size (+ size 1)))
  (val pool (ThreadPoolExecutor :max-workers (+ size 1) :thread-name-prefix "records-pg"))
  (val serving (RecordsServing :address (HttpAddress :host (! (env-text ENV-HOST DEFAULT-HOST))
                                                     :port (int (! (env-text ENV-PORT (str DEFAULT-PORT)))))
                               :schema schema :roster roster :prepare (pg-handlers-of schema prefix host) :request-handlers #()
                               :max-bytes REQUEST-MAX-BYTES
                               :maintenance (MaintenancePlan :interval-seconds (! (env-number ENV-MAINTENANCE-SECONDS DEFAULT-MAINTENANCE-SECONDS))
                                                             :keep-seconds (! (env-number ENV-KEEP-CHANGES-SECONDS DEFAULT-KEEP-CHANGES-SECONDS)))
                               :stop-poll-seconds STOP-POLL-SECONDS :drain-seconds DRAIN-SECONDS))
  (try
    (val parts (RecordsParts :connections connections :pool pool :prefix prefix))
    (<- code int (records-process (fn [body] (records-foundation parts body)) serving))
    (print "記録の service: 止まった" :file sys.stderr :flush True)
    code
    (finally
      (.close connections)
      (.shutdown pool :wait False :cancel-futures True))))
