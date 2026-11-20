;; 準備の確かめ(PROBE-PROGRAM)と焼く道具が、root の venv の bin/hy の script でなく venv の python で Hy を起こす事の検。
;;
;; 何のため: 共有の venv を使う薄い venv は bin が共有の venv の bin への symlink で、ある root の venv で動いた uv が共有の bin の script を
;; その root の python の shebang で書き直すと、同じ共有の venv を使う全部の root で bin/hy が別の root の python と site で起きる。
;; 確かめは別の root に解けた名を「根の外」と読み、正しい root を名前の影(env-incompatible)で断り続けた。子の job は venv の python を
;; 明示して起きるので、確かめも同じ起こし方にする。
;; 筋書き: 一時の dir に偽の venv の bin(hy = 書き直された古い script の代役 — 起きると 3 で終わる・python = 検の python を起こす薄い
;; script)と、package 1 つの root を作る。probe-argv の命令の uv run の後ろを、uv run と同じく偽の bin を PATH の先頭に置いて撃つ。
;; 直す前の命令(`hy -c …`)は古い script で起きて 3 で終わり、直した命令(`python -m hy -c …`)は root の package を根の中に読む。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import os)
(import shutil)
(import stat)
(import subprocess)
(import sys)
(import tempfile)
(import pathlib [Path])
(import doeff_cluster._worker.intent.env_prepare_model [BytecodeTree])
(import doeff_cluster._worker.protocol.env_translation [VENV-HY compile-argv probe-argv])

;; uv run の頭(`uv run --no-sync --frozen --project <dir>`)の長さ — この後ろが venv の bin で解かれる命令。
(val UV-RUN-HEAD 6)


(defk executable-written [path text]
  {:pre [(: path Path) (: text str)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "path に中身 text の実行できる file を書くため。"
  (.write-text path text)
  (.chmod path (| (. (.stat path) st-mode) stat.S-IXUSR stat.S-IXGRP stat.S-IXOTH))
  path)


(deftest test-the-probe-starts-hy-with-the-venv-python-not-the-bin-script
  (val base (Path (tempfile.mkdtemp :prefix "probe-venv-")))
  (try
    (val bin (/ base "venv" "bin"))
    (.mkdir bin :parents True)
    (<- (executable-written (/ bin "hy") "#!/bin/sh\necho 'stale shebang: another root python' >&2\nexit 3\n"))
    (<- (executable-written (/ bin "python") (.format "#!/bin/sh\nexec '{}' \"$@\"\n" sys.executable)))
    (val root (/ base "root"))
    (.mkdir (/ root "app") :parents True)
    (.write-text (/ root "app" "__init__.py") "")
    (val work (/ base "cwd"))
    (.mkdir work)
    (<- argv tuple (probe-argv "uv" "/s/roots/k/app" #((str root))))
    (assert (= (cut argv 0 UV-RUN-HEAD) #("uv" "run" "--no-sync" "--frozen" "--project" "/s/roots/k/app")) argv)
    (val done (subprocess.run (list (cut argv UV-RUN-HEAD None)) :cwd (str work) :capture-output True :text True :timeout 120
                              :env (| (dict os.environ) {"PATH" (+ (str bin) os.pathsep (.get os.environ "PATH" ""))
                                                         "PYTHONPATH" (str root)})))
    (assert (= done.returncode 0) done.stderr)
    (val seen (json.loads (get (.splitlines (.strip done.stdout)) -1)))
    (assert (= (get seen "misplaced") []) seen)
    (finally
      (shutil.rmtree base :ignore-errors True))))


(deftest test-the-compile-tool-starts-hy-with-the-venv-python
  (val trees #((BytecodeTree :tree "/s/roots/k/app" :roots #(".") :declared True)))
  (<- argv tuple (compile-argv "uv" "/w/code_prepare.hy" "/s/roots/k/app" trees #() None ""))
  (assert (= (cut argv UV-RUN-HEAD (+ UV-RUN-HEAD (len VENV-HY) 1)) (+ VENV-HY #("/w/code_prepare.hy"))) argv))
