;;; lifeline_starter.py を Hy の入口から走らせる(agora-redesign #3866)。Hy の入口は自分の process の sys.executable を hy の起動口に
;;; 書き換える(hy/cmdline.py)ので、doeff-cluster の worker(boot.sh の `exec hy -m …`)と同じく、sys.executable が python でない
;;; 起こす側になる。使い方: hy lifeline_starter_hy.hy <mode> <out-path> -(子は sleep だけ — 孫を起こす形は sys.executable を使うので
;;; ここでは使わない)
(require doeff-hy.macros [val])
(import os runpy sys)

(val STARTER (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter.py"))
(setv sys.argv [STARTER #* (cut sys.argv 1 None)])
(runpy.run-path STARTER :run-name "__main__")
