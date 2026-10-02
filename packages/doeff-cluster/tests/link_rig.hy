;;; 検の coordinator への口の道具(#2427 — 前は検が handlers.CoordinatorLink を直に作って poll / accept-tasks / report を呼んでいた)。
;;; 本番と同じ入れ物 LinkState と宛先の入れ物を持ち、操作ごとに本番の Program(worker/protocol/coordinator_link)を 1 回の run で回す。
;;; 送りは検の HTTP の答え手 transport-http(transport を渡さなければ本物の網の transport)、file は本物の os-file-handler、時計は実時間。
;;; 名指しの待ちの背景の task は run をまたいで生きない — 待ちの性質は 1 回の run の筋書きで確かめる(test_heartbeat_link)。
(require doeff-hy.macros [deff defk <- val])
(import os)
(import typing)
(import time)
(import uuid)
(import pathlib [Path])
(import httpx)
(import doeff [EffectBase Program run with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.worker.intent.worker_model [ReadDesired DesiredJobs DesiredUnreadable])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link accepted-tasks fetched-programs status-rows])
(import doeff_cluster.worker.core.launch [program-file program-file-text])
(import tests.transport_http [transport-http])
(import doeff_cluster.foundation.coordinator_http [RESEND-PAUSE-SECONDS])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])

;; 口の送り方(本番の組み立てと同じく一巡し直さない — 届かない拍は次の拍で送り直す)。
(val LINK-ROUTE (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS :resend-pause-seconds RESEND-PAUSE-SECONDS :connect-retries 0 :recheck-ms 60000 :actor "test-worker"))


(defk cell-of [url]
  {:pre [(: url str)] :post [(: % RouteCell)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宛先の指定(`,` で並べた URL)から、口の handler に渡す宛先の入れ物を作るため。"
  (<- route CoordinatorRoute (route-of url 0))
  (RouteCell route))


(defclass LinkRig []
  "検の coordinator への口: state = 本番と同じ入れ物・cell / watch-cell = heartbeat と待ちの宛先・transport = 送り先(None = 本物の網)。"
  (defn #^ None __init__ [self #^ str url #^ str name #^ tuple provides #^ int capacity #^ int fence-ms
                          #^ (| str None) [task-dir None] #^ (| dict None) [versions None] #^ (| httpx.BaseTransport None) [transport None]
                          #^ (| dict None) [tools None] #^ bool [handles-envs False] #^ tuple [exclusive #()] #^ str [node ""]
                          #^ bool [watch False]]
    (setv now-ms (int (* 1000 (time.time))))
    (setv self.state (LinkState name provides capacity fence-ms (or task-dir "tasks") (. (uuid.uuid4) hex) now-ms now-ms
                                :versions versions :tools tools :handles-envs handles-envs :exclusive exclusive :node node :watch watch)
          self.cell (run (cell-of url)) self.watch-cell (run (cell-of url))
          self.transport (or transport (httpx.HTTPTransport))))

  (defn #^ object on-link [self #^ object program]
    "program を口の handler と検の答え手の下で 1 回走らせる(本体は run-on-link)。"
    (run (scheduled (run-on-link self program))))

  (defn #^ object poll [self]
    "拍 1 つの ReadDesired(root の名乗りは今の state.env-report のまま)。"
    (.on-link self (ReadDesired :env-report self.state.env-report)))

  ;; defk を呼んだ結果の Program は method の引数でなく run-on-link(Program を受けて走らせる関数)へ渡す — method の引数は答えとして
  ;; 使う所と区別できない(DOEFF126・#2821 の案 A-1 で検の dir も判じるようになった)。
  (defn #^ tuple accept-tasks [self #^ list tasks]
    (run (scheduled (run-on-link self (accepted-tasks self.state tasks)))))

  (defn #^ None accept-programs [self #^ tuple specs]
    (run (scheduled (run-on-link self (fetched-programs self.state self.cell LINK-ROUTE specs)))))

  (defn #^ list report [self #^ tuple statuses]
    (run (scheduled (run-on-link self (status-rows self.state statuses)))))

  (defn #^ Path program-dir [self]
    (Path self.state.program-dir))

  (defn #^ str endpoint [self]
    "いま heartbeat を送る宛先。"
    (get self.cell.route.urls self.cell.route.active)))


(defk run-on-link [rig program]
  {:pre [(: rig LinkRig) (: program (| Program EffectBase))] :post [(: % (| tuple list dict DesiredJobs DesiredUnreadable None))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "program(defk を呼んだ結果の Program か effect)を rig の口の handler と検の答え手の下で回し、その答えを返すため(scheduler は呼び手が
   被せる — LinkRig の method は (run (scheduled …)) で 1 回走らせる)。"
  ;; 環境変数の読み(ReadEnvironment — 準備の file の名 DOEFF_WORKER_READY_FILE)は本番の worker の入口と同じ本物の subprocess-handler(#3014)。
  (<- answer (with-handlers [(await-handler) (transport-http rig.transport) os-file-handler subprocess-handler slog-handler (sync-time-handler)
                             (coordinator-link rig.state rig.cell LINK-ROUTE rig.watch-cell)]
                            program))
  answer)


(deff write-program-file [#^ Path program-dir #^ str sha #^ str blob #^ (get dict #(str typing.Any)) versions]  ; defk にできない: 検が Program の外で file を置く道具
  {:pre [(: program-dir Path) (: sha str) (: blob str) (: versions dict)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator への口が /programs/<sha> から取って置くのと同じ cache の file を置くため(置き場と中身の形の定義点は worker/core/launch の
   program-file と program-file-text — handlers.hy の同じ名の道具を #2427 でここへ移した)。"
  (let [path (program-file program-dir sha)]
    (.mkdir program-dir :parents True :exist-ok True)
    (.write-text path (program-file-text blob versions) :encoding "utf-8")
    path))
