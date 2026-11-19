;;; 預かりの process の effect(shared/intent/hosted_model の LaunchHosted・StopHosted・ListHosted)の模擬の答え手(模擬の環境)。本番の
;;; 答え手(worker の中 = worker/protocol/hosted_host の hosted-host・job の中 = worker/entry/hosted_socket の hosted-client)と同じ形の答えで
;;; 答え、起こした頼みと今の一覧を入れ物に残す(検と模擬の世界が、走らせた後に読む)。process は起こさない。
;;;
;;; 契約(本番の hosted-host と同じ形):
;;;   * 起こし: id(h- と 16 進 16 字)と pid を入れ物の順で振り、HostedProcess(ended = False)で答える。頼み(HostedLaunch)はそのまま
;;;     launches に残す。入れ物の down なら HostedRefused(口に届かない)。
;;;   * 止め: 知っている id は一覧から外し、止めた後の姿(ended = True)で答える。知らない id は HostedRefused。
;;;   * 一覧: 覚えている物の全部を id の順で答える。
(require doeff-hy.macros [defhandler defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses)
(import doeff_cluster._shared.intent.hosted_model [HostedLaunch HostedProcess HostedRefused LaunchHosted ListHosted StopHosted])

;; 模擬の pid の始まりと、置き場の dir の頭(本番の分け合う dir の下の hosted/<id> と同じ形の名)。
(val FIRST-PID 40000)
(val PLACE-ROOT "/sim/hosted")


(defclass HostedLog []
  "模擬の答え手 held-hosted-processes の入れ物 — 検と模擬の世界が、走らせた後に読むため。launches = 受けた起こしの頼み(受けた順)・
   held = 今覚えている預かりの process(id → HostedProcess)・started-ms = 起こした刻に置く値・down = worker の口に届かない世界か。"
  (defn #^ None __init__ [self #^ int [started-ms 0] #^ bool [down False]]  ; cannot be a defk: class の作り手(Python が呼ぶ口)
    "空の入れ物を作るため(started-ms = 起こした刻に置く値・down = 口に届かない世界)。"
    (setv #^ (get tuple #(HostedLaunch ...)) self.launches #()
          #^ (get dict #(str HostedProcess)) self.held {}
          #^ int self.started-ms started-ms
          #^ bool self.down down)))


(defk held-launched [log launch]
  {:pre [(: log HostedLog) (: launch HostedLaunch)] :post [(: % (| HostedProcess HostedRefused))] :tags {:context "doeff-cluster" :role "foundation"}}
  "起こしの頼み launch を入れ物 log に当て、答えを得るため(頭の註の契約)。"
  (when log.down
    (return (HostedRefused :detail "worker の口に届かない: 模擬の世界が down")))
  (val number (len log.launches))
  (val id (.format "h-{:016x}" (+ number 1)))
  (val view (HostedProcess :id id :pid (+ FIRST-PID number) :owner launch.owner :layer launch.layer :place (.format "{}/{}" PLACE-ROOT id)
                           :started-ms log.started-ms))
  (setv log.launches (+ log.launches #(launch))
        log.held (| log.held {id view}))
  view)


(defk held-stopped [log id]
  {:pre [(: log HostedLog) (: id str)] :post [(: % (| HostedProcess HostedRefused))] :tags {:context "doeff-cluster" :role "foundation"}}
  "止めの頼み id を入れ物 log に当て、答えを得るため(頭の註の契約)。"
  (val view (.get log.held id))
  (when (is view None)
    (return (HostedRefused :detail (.format "知らない id: {}" id))))
  (setv log.held (dfor #(k v) (.items log.held) :if (!= k id) k v))
  (dataclasses.replace view :ended True))


;; 引数に残す理由: 入れ物は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-hosted-processes [#^ HostedLog log]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 頼みを入れ物に当てる(頭の註の契約)。
  (LaunchHosted [launch]
    (val answer (! (held-launched log launch)))
    (resume answer))
  (StopHosted [id]
    (val answer (! (held-stopped log id)))
    (resume answer))
  (ListHosted []
    (resume (tuple (gfor id (sorted log.held) (get log.held id))))))
