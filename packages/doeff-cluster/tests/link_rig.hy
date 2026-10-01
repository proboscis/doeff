;;; 検の coordinator への口の道具(#2427 — 前は検が handlers.CoordinatorLink を直に作って poll / accept-tasks / report を呼んでいた)。
;;; 本番と同じ入れ物 LinkState と宛先の入れ物を持ち、操作ごとに本番の Program(worker/protocol/coordinator_link)を 1 回の run で回す。
;;; 送りは検の HTTP の答え手 transport-http(transport を渡さなければ本物の網の transport)、file は本物の os-file-handler、時計は実時間。
;;; 名指しの待ちの背景の task は run をまたいで生きない — 待ちの性質は 1 回の run の筋書きで確かめる(test_heartbeat_link)。
(require doeff-hy.macros [deff val])
(import os)
(import typing)
(import time)
(import uuid)
(import pathlib [Path])
(import httpx)
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.worker.intent.worker_model [ReadDesired])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link accepted-tasks fetched-programs status-rows])
(import doeff_cluster.worker.core.launch [program-file program-file-text])
(import tests.transport_http [transport-http])

;; 口の送り方(本番の組み立てと同じく一巡し直さない — 届かない拍は次の拍で送り直す)。
(val LINK-ROUTE (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :connect-retries 0 :recheck-ms 60000 :actor "test-worker"))


(defn #^ RouteCell cell-of [#^ str url]  ; defk にできない: 組み立て(Program を走らせる前)が handler の引数を作る準備
  "宛先の指定(`,` で並べた URL)から宛先の入れ物を作る。"
  (RouteCell (run (route-of url 0))))


(defclass LinkRig []
  "検の coordinator への口: state = 本番と同じ入れ物・cell / watch-cell = heartbeat と待ちの宛先・transport = 送り先(None = 本物の網)。"
  (defn #^ None __init__ [self #^ str url #^ str name #^ tuple provides #^ int capacity #^ int fence-ms
                          #^ (| str None) [task-dir None] #^ (| dict None) [versions None] #^ (| httpx.BaseTransport None) [transport None]
                          #^ (| dict None) [tools None] #^ bool [handles-envs False] #^ tuple [exclusive #()] #^ str [node ""]
                          #^ bool [watch False]]
    (setv now-ms (int (* 1000 (time.time))))
    (setv self.state (LinkState name provides capacity fence-ms (or task-dir "tasks") (. (uuid.uuid4) hex) now-ms now-ms
                                :versions versions :tools tools :handles-envs handles-envs :exclusive exclusive :node node :watch watch)
          self.cell (cell-of url) self.watch-cell (cell-of url)
          self.transport (or transport (httpx.HTTPTransport))))

  (defn #^ object on-link [self #^ object program]
    "program を口の handler と検の答え手の下で 1 回走らせる。"
    (run (scheduled (with-handlers [(await-handler) (transport-http self.transport) os-file-handler slog-handler (sync-time-handler)
                                    (coordinator-link self.state self.cell LINK-ROUTE self.watch-cell)]
                                   program))))

  (defn #^ object poll [self]
    "拍 1 つの ReadDesired(root の名乗りは今の state.env-report のまま)。"
    (.on-link self (ReadDesired :env-report self.state.env-report)))

  (defn #^ tuple accept-tasks [self #^ list tasks]
    (.on-link self (accepted-tasks self.state tasks)))

  (defn #^ None accept-programs [self #^ tuple specs]
    (.on-link self (fetched-programs self.state self.cell LINK-ROUTE specs)))

  (defn #^ list report [self #^ tuple statuses]
    (.on-link self (status-rows self.state statuses)))

  (defn #^ Path program-dir [self]
    (Path self.state.program-dir))

  (defn #^ str endpoint [self]
    "いま heartbeat を送る宛先。"
    (get self.cell.route.urls self.cell.route.active)))


(deff write-program-file [#^ Path program-dir #^ str sha #^ str blob #^ (get dict #(str typing.Any)) versions]  ; defk にできない: 検が Program の外で file を置く道具
  {:pre [(: program-dir Path) (: sha str) (: blob str) (: versions dict)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "coordinator への口が /programs/<sha> から取って置くのと同じ cache の file を置くため(置き場と中身の形の定義点は worker/core/launch の
   program-file と program-file-text — handlers.hy の同じ名の道具を #2427 でここへ移した)。"
  (let [path (program-file program-dir sha)]
    (.mkdir program-dir :parents True :exist-ok True)
    (.write-text path (program-file-text blob versions) :encoding "utf-8")
    path))
