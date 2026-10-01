;;; readinessProbe が sh で読む file を書く汎用の I/O(worker の CoordinatorLink が heartbeat の届いた拍ごとに呼ぶ)。handlers.hy から
;;; 分けた(#2026)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import os)
(import pathlib [Path])


(defn #^ None write-ready-file [#^ (| str None) path #^ bool draining]
  "readinessProbe が sh で読む file(2026-09-25)へ、heartbeat が届いた拍ごとに「ready」か「draining」を書く(mtime = 最後に届いた時刻)。
   probe は中身が ready で新しい時だけ Ready — hy を起こさない(込んだ node で 10 秒の timeout を越えて両方の Pod が NotReady に
   なり、DaemonSet が 2 台を同時に消した実弾)。file は Pod の中(container の /tmp)— 同じ node の前の Pod の物と混ざらない。"
  (when path
    (setv tmp (Path (+ path ".tmp")))
    (.write-text tmp (if draining "draining\n" "ready\n") :encoding "utf-8")
    (os.replace tmp path)))
