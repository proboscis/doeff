;; worker の宣言の読みの口(worker/protocol/declared)を、前の置き場の名でも読めるように読み直す所と、詰めた Program の cache の file を
;; Program の外から書く道具 write-program-file(検・agora の模擬の世界が使う)。coordinator への口(heartbeat・名指しの待ち・task と Program の
;; 受け取り)は worker/protocol/coordinator_link・lease の返しは worker/protocol/lease_release へ移した(#2427)。
(require doeff-hy.macros [deff])
(import os)
(import pathlib [Path])
(import doeff_cluster.worker.core.launch [program-file program-file-text])
(import doeff_cluster.worker.protocol.declared [env-placement declared-job-spec task-spec JOB-ENTRY])


(deff write-program-file [#^ Path program-dir #^ str sha #^ str blob #^ dict versions]  ; defk にできない: 検の道具(Program の外)が呼ぶ
  {:pre [(: program-dir Path) (: sha str) (: blob str) (: versions dict)] :post [(: % Path)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の /programs/<sha> から取った詰めた Program と同じ cache の file を書くため(中身の形は worker/core/launch の
   program-file-text)。書きかけを子に見せないよう rename で置く。"
  (let [path (program-file program-dir sha)
        tmp (Path (+ (str path) ".tmp"))]
    (.mkdir program-dir :parents True :exist-ok True)
    (.write-text tmp (program-file-text blob versions) :encoding "utf-8")
    (os.replace tmp path)
    path))
