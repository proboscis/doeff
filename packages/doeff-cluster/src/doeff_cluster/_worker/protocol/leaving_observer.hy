;;; この Pod の止まりの観測(AL-D2p): worker の観測 ObserveLeaving に、preStop が書いた止まりの印の世代がこの
;;; process の世代と同じかで答える答え手 leaving-observer。worker の入口(worker/entry/main)が並べるのはこの答え手だけで、印の書き手・
;;; path・形(worker/protocol/leaving_mark)は import しない — 入口から書き手と形への依存は module の契約
;;; (.agents/code-quality.d/doeff-cluster-worker-leaving-mark.json)が断る。書き手は preStop の入口(drain_main)だけ。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster._worker.protocol.observations [ObserveLeaving])
(import doeff_cluster._worker.protocol.leaving_mark [leaving-mark-read])


(defhandler leaving-observer [#^ (| str None) boot-file #^ str boot]
  ;; 引数に残す理由: 世代の file の path と、この process の世代は起動の時に 1 度だけ決まる入口の値(worker/entry/main)。
  ;; 印は拍ごとに読み直す(file 1 つの読み — preStop は拍の間にいつでも始まる)。
  (ObserveLeaving []
    (<- leaving bool (leaving-mark-read boot-file boot))
    (resume leaving)))
