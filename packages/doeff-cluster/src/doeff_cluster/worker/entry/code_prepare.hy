;;; 展開したコードの木の bytecode を、木の中だけに「実行時に source の hash を検める」方式で用意する。
;;;
;;; worker は版ごとに木を展開する。前の版の木から、中身の変わっていない file の .pyc を hardlink で
;;; 引き継ぎ、残りだけを焼く。検める方式(PEP 552 の checked hash)なので、引き継ぎを誤っても import が
;;; source の hash を突き合わせて焼き直す — 古い bytecode が黙って使われることはない。
;;; 共有の venv・doeff・標準 library には書かない(本番の image の焼き方 deploy/bytecode.py は木の外も歩き、
;;; 実行時に検めない方式で焼くので、中身の動く手元の環境には使えない)。
;;;
;;; 形: 判断(module 名・引き継ぐ組・焼く物)は純粋な関数、走査・hardlink・焼きは木の効果(言い換え = worker/protocol/tree_files の
;;; tree-files が汎用の file の効果へ出し直し、main が本物の os-file-handler を被せる — #2468)。
;;; 経過の秒は doeff-time の GetMonotonic(main が sync-time-handler を被せる)。
;;;
;;; 完成の印: 焼いた後に木を走査し直し、焼くべき source ごとに .pyc が在ること(焼けなかった file は理由つきで
;;; 印に載せる)を検めてから、木の根に印の file(MARKER)を置く。検めが通らなければ印を置かず、0 でない終了で
;;; 終わる。worker(worker/protocol/code_store の code-host)は印の在る木だけを完成品として公開し、読む時にも印を検める。
;;;
;;; 道具は worker 自身のコードから file の path で起動する(準備する版の木から -m で起動すると、道具を持たない
;;; 古い版では道具が見つからない — 2026-09-23 に atlas の版 8d7181f が bytecode 0 のまま完成品になった原因)。
;;;
;;;   PYTHONDONTWRITEBYTECODE=1 hy <worker のコード>/doeff_cluster/worker/entry/code_prepare.hy <新しい木> --revision <版>
;;;       [--from <前の木> --changed <変わった path の一覧 file>] [--import-roots .,sub/dir]
;;;
;;; import の根(木の中の dir・`,` で並べる・既定 `.`)は業務の repo の形で、worker の CodeLayout(worker_model)が渡す。
;;;
;;; 焼く範囲(2026-09-26・#664 の実測): --entries <module,…> を渡すと、その module たちの import の閉包(Hy の import / require と
;;; Python の import を静的に辿る)だけを焼く。閉包の外の module は子が import した時に作られる(焼く物が減るだけで正しさは変わらない)。
;;; 並列数の既定は cgroup の CPU の上限(pod の limits)— node の CPU の数で焼くと、上限 4 の pod で 16 並列になり周期の 97% が絞られた。
(require doeff-hy.macros [defk val <-])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import math)
(import os)
(import sys)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_time [sync-time-handler])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.file_effects [ReadText file-done])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.python_bytecode [prepare-compile-path])
(import doeff_cluster.worker.core.code_prepare [prepare-tree])
(import doeff_cluster.worker.protocol.tree_files [tree-files])


;; --- 焼きの並列数 -----------------------------------------------------------------------

(defn #^ int cpu-limit-of [#^ (| str None) cpu-max #^ int available]
  "cgroup v2 の cpu.max の中身(\"<quota> <period>\" か \"max <period>\")と使える CPU の数 → 焼きの並列数(pod の上限を越えないため)。"
  (setv parts (if cpu-max (.split cpu-max) []))
  (if (and (= (len parts) 2) (!= (get parts 0) "max"))
      (max 1 (min available (math.ceil (/ (int (get parts 0)) (int (get parts 1))))))
      (max 1 available)))


(defk usable-cpus []
  {:pre [] :post [(: % int)] :tags {:context "worker" :role "main"}}
  "この process が使える CPU の数(affinity と cgroup の上限の小さい方)— 焼きの並列数の既定。cgroup の cpu.max は file の効果で読む
   (答え手 = 入口の os-file-handler・無い / 読めない = 上限なし)。"
  (val available (if (hasattr os "sched_getaffinity") (len (os.sched-getaffinity 0)) (or (os.cpu-count) 1)))
  (<- cpu-max (ReadText "/sys/fs/cgroup/cpu.max"))
  (cpu-limit-of (if (isinstance cpu-max str) cpu-max None) available))


(defk changed-paths [path]
  {:pre [(: path str)] :post [(: % frozenset)] :tags {:context "worker" :role "main"}}
  "前の版の木から変わった path の一覧(--changed の file・空白で区切る)を読むため — 引き継がない file を決める材料。読めなければ OSError。"
  (<- text (file-done (ReadText path)))
  (frozenset (.split text)))


(defn #^ None main []
  ;; file の読み(cgroup の上限・変わった path の一覧)は本物の file の答え手(os-file-handler)の下で。
  (setv default-jobs (run (with-handlers [os-file-handler] (usable-cpus))))
  (setv parser (argparse.ArgumentParser))
  (.add-argument parser "tree")
  (.add-argument parser "--revision" :required True)
  (.add-argument parser "--from" :dest "old")
  (.add-argument parser "--changed")
  (.add-argument parser "--jobs" :type int :default default-jobs :help "焼きの並列数(既定 = cgroup の CPU の上限)")
  (.add-argument parser "--entries" :default "" :help "焼く範囲の入口の module(`,` で並べる・空 = 根の下を全部)")
  (.add-argument parser "--import-roots" :default "." :help "木の中の import の根(`,` で並べる・前が先)")
  (setv args (.parse-args parser))
  (setv roots (tuple (gfor r (.split args.import-roots ",") :if r r)))
  (setv tree (str (.resolve (Path args.tree))))
  ;; 焼く途中の import(Hy の require 等)が timestamp 方式の .pyc を書かないようにする。
  (setv sys.dont-write-bytecode True)
  ;; file の path で起動すると、道具の dir(worker 自身のコードの doeff_cluster/worker/entry)が sys.path の先頭に入る。
  ;; 焼く木の module 名がそこで解けてしまわないよう外す。
  (setv here (. (.resolve (Path __file__)) parent))
  (setv (cut sys.path) (lfor p sys.path :if (not (and p (= (.resolve (Path p)) here))) p))
  (prepare-compile-path tree roots)
  (setv changed (if args.changed (run (with-handlers [os-file-handler] (changed-paths args.changed))) (frozenset)))
  (setv old (if args.old (str (.resolve (Path args.old))) None))
  (setv entries (tuple (gfor e (.split args.entries ",") :if e e)))
  ;; 木の効果は言い換え tree-files(worker/protocol/tree_files)が汎用の file の効果へ出し直し、本物の os-file-handler が答える(#2468)。
  ;; Note の行は slog-handler が stderr へ出す(worker の code-host が失敗の理由に読む)。
  (setv summary (run (with-handlers [(sync-time-handler) slog-handler os-file-handler tree-files]
                                    (prepare-tree tree args.revision old changed args.jobs roots entries))))
  (when (is-not (get summary "problem") None)
    (print (+ "準備に失敗: " (get summary "problem")) :file sys.stderr :flush True)
    (sys.exit 1)))


(when (= __name__ "__main__")
  (main))
