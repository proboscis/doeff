;;; 記録の service の composition root の部品 — env と Secret の file を読み、身元の名簿・接続の貸し出し・HTTP の口・手入れの係を
;;; 組んで立て、止めの合図まで答える。判断はここに無い(流れ = service.hy・判断 = admission.hy・綴り = wire.hy)。
;;;
;;; 置き場の宣言(RecordsSchema)と接続の開き方は呼び手の系が持つので、呼び手の系の入口がこの部品を呼ぶ:
;;;
;;;   (import psycopg)
;;;   (import myapp.tables [SCHEMA])
;;;   (serve-records-service SCHEMA (fn [url] (psycopg.connect url :autocommit True))
;;;                          #(psycopg.OperationalError psycopg.InterfaceError))
;;;
;;; 受け取る env(宣言はここ 1 点・既定の宿の literal を持たない):
;;;   DOEFF_RECORDS_PG_URL_FILE            PostgreSQL の接続 URL の file(必須・Secret の mount)
;;;   DOEFF_RECORDS_PRINCIPALS_FILE        身元の名簿 principals.json(必須・{version: 1, principals: [{name, tokenSha256}]})
;;;   DOEFF_RECORDS_PREFIX                 表の名の接頭辞(既定 records_ — 同じ database の別の置き場の表と混ざらない)
;;;   DOEFF_RECORDS_HOST / _PORT           HTTP の口(既定 0.0.0.0 / 8875)
;;;   DOEFF_RECORDS_POOL_SIZE              同時に貸す接続の上限(既定 8)
;;;   DOEFF_RECORDS_MAINTENANCE_SECONDS    手入れの間隔(既定 60)
;;;   DOEFF_RECORDS_KEEP_CHANGES_SECONDS   変更の列に残す秒(既定 604800 = 7 日 — これより遅れた読み手は Reset で一覧から読み直す)
;;; 表は起動時に CREATE ... IF NOT EXISTS だけを流す(既存の表を消さない・変えない)。
(import collections.abc [Callable])
(import typing [TypeVar])
(import os)
(import signal)
(import threading)
(import types [FrameType])
(import doeff [Program run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_records.values [RecordsSchema])
(import doeff_records.principals [decode-roster])
(import doeff_records.pg [PgRecordsHost pg-records-handler])
(import doeff_records.pg_pool [PgHostPool DEFAULT-POOL-SIZE])
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
(setv DEFAULT-HOST "0.0.0.0" DEFAULT-PORT 8875 DEFAULT-MAINTENANCE-SECONDS 60 DEFAULT-KEEP-CHANGES-SECONDS 604800)
;; 手入れの係が handler を組む時の書き手の名(手入れの effect は書き手の許可を通らない — 行を書かない)。
(setv MAINTAINER "records-maintenance")
(setv T (TypeVar "T"))


(defn #^ str required-env [#^ str name]  ; defk にできない: process の入口で Program を組む前に読む設定
  "必須の env を読む(無ければ起動を止める — 黙って既定へ倒さない)。"
  (setv value (.get os.environ name))
  (when (not value) (raise (SystemExit (.format "記録の service: env {} が要る" name))))
  value)


(defn #^ str read-file [#^ str path]  ; defk にできない: process の入口で Program を組む前に読む設定
  "Secret の mount の file を読む(末尾の改行を除く)。"
  (with [handle (open path :encoding "utf-8")] (.strip (.read handle))))


(defn #^ T real-time-runner [#^ (get Program T) program]  ; defk にできない: HTTP の口と手入れの係が Program を走らせる関数(Program の外の入口)
  "実時間の時計と scheduler を被せて Program を走らせる。"
  (run (scheduled (with_handlers [(sync-time-handler)] program))))


(defn #^ None serve-records-service [#^ RecordsSchema schema #^ Callable connect-url #^ tuple unreachable-errors]  ; defk にできない: process の入口
  "記録の service を起動し、SIGTERM / SIGINT まで答え続ける。connect-url = 接続 URL → 自動 commit の psycopg の接続 /
   unreachable-errors = 接続の失敗の例外の型(Unreachable の答えに写す)。"
  (setv url (read-file (required-env ENV-PG-URL-FILE))
        roster (real-time-runner (decode-roster (read-file (required-env ENV-PRINCIPALS-FILE))))
        prefix (.get os.environ ENV-PREFIX DEFAULT-PREFIX)
        connect (fn [] (connect-url url))
        pool (PgHostPool connect schema :unreachable-errors unreachable-errors :prefix prefix :size (int (.get os.environ ENV-POOL-SIZE DEFAULT-POOL-SIZE)))
        server (start-records-server (RecordsServerConfig schema roster pool.lease-handlers real-time-runner
                                                          :host (.get os.environ ENV-HOST DEFAULT-HOST)
                                                          :port (int (.get os.environ ENV-PORT DEFAULT-PORT))
                                                          :concurrent True))
        maintainer (PgRecordsHost (connect) schema :unreachable-errors unreachable-errors :prefix prefix)
        interval (float (.get os.environ ENV-MAINTENANCE-SECONDS DEFAULT-MAINTENANCE-SECONDS))
        keep (float (.get os.environ ENV-KEEP-CHANGES-SECONDS DEFAULT-KEEP-CHANGES-SECONDS))
        stop (threading.Event))
  (defn #^ None maintain []  ; defk にできない: thread の target
    "手入れの係: 止めるまで maintenance-loop を回す(自分の接続 1 本で)。"
    (real-time-runner (with_handlers [(pg-records-handler maintainer MAINTAINER)] (maintenance-loop interval keep None))))
  (defn #^ None request-stop [#^ int signum #^ (| FrameType None) frame]  ; defk にできない: signal の callback
    "止めの合図を受ける。"
    (.set stop))
  (.start (threading.Thread :target maintain :name "doeff-records-maintenance" :daemon True))
  (for [sig [signal.SIGTERM signal.SIGINT]]
    (signal.signal sig request-stop))
  (print (.format "記録の service: {} で答える(接頭辞 {})" server.url prefix) :flush True)
  (.wait stop)
  (.close server)
  (.close pool)
  (.close maintainer.connection))
