;;; 記録の service の composition root の部品 — env と Secret の file を読み、身元の名簿・SQL の答え手・HTTP の口・手入れの係を
;;; 組んで立て、止めの合図まで答える。判断はここに無い(流れ = service.hy・判断 = admission.hy・綴り = wire.hy)。
;;;
;;; 置き場の宣言(RecordsSchema)と接続 URL の file の読み方は呼び手の系が持つので、呼び手の系の入口がこの部品を呼ぶ:
;;;
;;;   (import myapp.tables [SCHEMA])
;;;   (defk dsn-of [text] {:pre [(: text str)] :post [(: % str)]} (.strip text))
;;;   (serve-records-service SCHEMA dsn-of)
;;;
;;; dsn-of = 接続 URL の file の中身 → PostgreSQL の DSN の Program(file の綴りは呼び手の系ごとに違う — 例: env の 1 行 KEY=URL)。
;;; 接続・driver・接続の失敗の読み分けは doeff の postgres-sql-handler(doeff_core_effects.postgres_sql)が持つ(#880 U6)。
;;;
;;; 受け取る env(宣言はここ 1 点・既定の宿の literal を持たない):
;;;   DOEFF_RECORDS_PG_URL_FILE            PostgreSQL の接続 URL の file(必須・Secret の mount)
;;;   DOEFF_RECORDS_PRINCIPALS_FILE        身元の名簿 principals.json(必須・{version: 1, principals: [{name, tokenSha256}]})
;;;   DOEFF_RECORDS_PREFIX                 表の名の接頭辞(既定 records_ — 同じ database の別の置き場の表と混ざらない)
;;;   DOEFF_RECORDS_HOST / _PORT           HTTP の口(既定 0.0.0.0 / 8875)
;;;   DOEFF_RECORDS_POOL_SIZE              要求に同時に貸す接続の上限(既定 8 — 手入れの係の 1 本を足した数を開く)
;;;   DOEFF_RECORDS_MAINTENANCE_SECONDS    手入れの間隔(既定 60)
;;;   DOEFF_RECORDS_KEEP_CHANGES_SECONDS   変更の列に残す秒(既定 604800 = 7 日 — これより遅れた読み手は Reset で一覧から読み直す)
;;; 表は起動時に CREATE ... IF NOT EXISTS だけを流す(既存の表を消さない・変えない)。
(import collections.abc [Callable])
(import contextlib [nullcontext])
(import os)
(import signal)
(import socket)
(import threading)
(import types [FrameType])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.postgres_sql [PostgresConnections PostgresDatabase postgres-sql-handler])
(import doeff_time [sync-time-handler])
(import doeff_records.values [RecordsSchema])
(import doeff_records.principals [decode-roster])
(import doeff_records.pg [pg-records-handler prepare-records-store DEFAULT-POLL-SECONDS])
(import doeff_records.pg_sql [DEFAULT-PREFIX])
(import doeff_records.maintenance [maintenance-loop])
(import doeff_records.http_server [RecordsServerConfig start-records-server])

(setv ENV-PG-URL-FILE "DOEFF_RECORDS_PG_URL_FILE"
      ENV-PRINCIPALS-FILE "DOEFF_RECORDS_PRINCIPALS_FILE"
      ENV-PREFIX "DOEFF_RECORDS_PREFIX"
      ENV-HOST "DOEFF_RECORDS_HOST"
      ENV-PORT "DOEFF_RECORDS_PORT"
      ENV-POOL-SIZE "DOEFF_RECORDS_POOL_SIZE"
      ENV-MAINTENANCE-SECONDS "DOEFF_RECORDS_MAINTENANCE_SECONDS"
      ENV-KEEP-CHANGES-SECONDS "DOEFF_RECORDS_KEEP_CHANGES_SECONDS")
(setv DEFAULT-HOST "0.0.0.0" DEFAULT-PORT 8875 DEFAULT-POOL-SIZE 8 DEFAULT-MAINTENANCE-SECONDS 60 DEFAULT-KEEP-CHANGES-SECONDS 604800)
;; SqlQuery に書く database の名(この service の置き場は 1 つ — 答え手の宣言と揃える)。
(setv DATABASE "records")
;; 手入れの係が handler を組む時の書き手の名(手入れの effect は書き手の許可を通らない — 行を書かない)。
(setv MAINTAINER "records-maintenance")


(defn #^ str required-env [#^ str name]  ; defk にできない: process の入口で Program を組む前に読む設定
  "必須の env を読む(無ければ起動を止める — 黙って既定へ倒さない)。"
  (setv value (.get os.environ name))
  (when (not value) (raise (SystemExit (.format "記録の service: env {} が要る" name))))
  value)


(defn #^ str read-file [#^ str path]  ; defk にできない: process の入口で Program を組む前に読む設定
  "Secret の mount の file を読む(末尾の改行を除く)。"
  (with [handle (open path :encoding "utf-8")] (.strip (.read handle))))


(defn #^ Callable real-time-runner [#^ PostgresConnections connections]  ; defk にできない: HTTP の口と手入れの係が Program を走らせる関数を作る(Program の外の入口)
  "実時間の時計・PostgreSQL の答え手・scheduler を被せて Program を走らせる関数を作る(要求ごとの thread で 1 回ずつ run する —
   答え手は同期の postgres-sql-handler。接続は connections から要求ごとに 1 本借りる)。"
  (fn [program] (run (scheduled (with_handlers [(sync-time-handler) (postgres-sql-handler connections)] program)))))


(defn #^ None serve-records-service [#^ RecordsSchema schema #^ Callable dsn-of]  ; defk にできない: process の入口
  "記録の service を起動し、SIGTERM / SIGINT まで答え続ける。dsn-of = 接続 URL の file の中身 → DSN の Program。"
  (setv size (int (.get os.environ ENV-POOL-SIZE DEFAULT-POOL-SIZE))
        prefix (.get os.environ ENV-PREFIX DEFAULT-PREFIX)
        dsn (run (dsn-of (read-file (required-env ENV-PG-URL-FILE))))
        ;; 要求に貸す size 本と、手入れの係の 1 本。
        connections (PostgresConnections #((PostgresDatabase :name DATABASE :dsn dsn)) :size (+ size 1))
        runner (real-time-runner connections)
        roster (runner (decode-roster (read-file (required-env ENV-PRINCIPALS-FILE))))
        ;; 表の用意(移行)は process ごとに 1 度 — 要求を受ける前に済ませる。
        store (runner (prepare-records-store DATABASE schema prefix))
        origin-host (socket.gethostname)
        handler-for (fn [writer] (pg-records-handler store writer origin-host DEFAULT-POLL-SECONDS))
        server (start-records-server (RecordsServerConfig schema roster (fn [] (nullcontext handler-for)) runner
                                                          :host (.get os.environ ENV-HOST DEFAULT-HOST)
                                                          :port (int (.get os.environ ENV-PORT DEFAULT-PORT))
                                                          :concurrent True))
        interval (float (.get os.environ ENV-MAINTENANCE-SECONDS DEFAULT-MAINTENANCE-SECONDS))
        keep (float (.get os.environ ENV-KEEP-CHANGES-SECONDS DEFAULT-KEEP-CHANGES-SECONDS))
        stop (threading.Event))
  (defn #^ None maintain []  ; defk にできない: thread の target
    "手入れの係: 止めるまで maintenance-loop を回す。"
    (runner (with_handlers [(handler-for MAINTAINER)] (maintenance-loop interval keep None))))
  (defn #^ None request-stop [#^ int signum #^ (| FrameType None) frame]  ; defk にできない: signal の callback
    "止めの合図を受ける。"
    (.set stop))
  (.start (threading.Thread :target maintain :name "doeff-records-maintenance" :daemon True))
  (for [sig [signal.SIGTERM signal.SIGINT]]
    (signal.signal sig request-stop))
  (print (.format "記録の service: {} で答える(接頭辞 {})" server.url prefix) :flush True)
  (.wait stop)
  (.close server)
  (.close connections))
