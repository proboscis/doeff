;;; 丁寧な模擬の fake の uv(test_env_careful.hy が PATH の先頭に置く sh の包みから起こす)。本物の準備の process(env_handlers の翻訳 env-translation)が呼ぶ 4 つの命令だけを模す:
;;;
;;;   uv sync --frozen --project P --python X --no-default-groups [--group g]… [--no-install-package n]…
;;;       P/.venv を作る: bin/python は検の interpreter への symlink、site-packages の .pth が検の環境の site-packages を足す
;;;       (doeff・hy・cloudpickle を本物のまま使う)。uv.lock の行(名==版)のうち cache に無い物を「download」と数えて log に書く。
;;;       行に ` fake-top=名` が在れば、その名の package を site-packages に置く(根の名前の影の反例)。
;;;   uv build --wheel --out-dir D S        D に空の wheel を置き、log に build と書く
;;;   uv pip install --no-deps --python PY W…   log に書くだけ
;;;   uv run --no-sync --frozen --project P CMD ARGS…   P/.venv の python で CMD(hy / python)を exec する
;;;
;;; 設定は包みが export する環境変数: FAKE_UV_PYTHON(検の interpreter)・FAKE_UV_SITE(検の環境の site-packages)・
;;; FAKE_UV_DIR(log・cache・失敗の指定の file を置く dir)。FAKE_UV_DIR/fail に kind(lock-stale・sync-failed・python-unavailable・
;;; native-build-failed)を書くと、その命令を失敗させる。
;;; 命令ごとの処理は defk で、process の入口(下の __main__)が doeff の run で 1 回だけ回す(素の関数を持たない — #2915)。
(require doeff-hy.macros [defk val])
(import json os sys zipfile)
(import pathlib [Path])
(import doeff [run])

(setv HOME (Path (get os.environ "FAKE_UV_DIR")))


(defk log-line [text]
  {:pre [(: text str)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "FAKE_UV_DIR の log に 1 行を足すため(検が命令の回数と引数を数える)。"
  (with [f (open (/ HOME "log") "a" :encoding "utf-8")] (.write f (+ text "\n")))
  None)


(defk failure []
  {:pre [] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検が FAKE_UV_DIR/fail に書いた失敗の種類を読むため(無ければ空の文字列)。"
  (val path (/ HOME "fail"))
  (if (.is-file path) (.strip (.read-text path :encoding "utf-8")) ""))


(defk option [args flag]
  {:pre [(: args list) (: flag str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "命令の引数の列から flag の次の値を引くため。"
  (get args (+ (.index args flag) 1)))


(defk sync [args]
  {:pre [(: args list)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "uv sync の代わり: P/.venv を作り、cache に無い lock の行を download と数えて log に書く。"
  (<- kind str (failure))
  (cond
    (= kind "lock-stale")
      (do (print "error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided." :file sys.stderr)
          (sys.exit 2))
    (= kind "python-unavailable")
      (do (print "error: No interpreter found for Python 9.9 in managed installations" :file sys.stderr) (sys.exit 2))
    (= kind "sync-failed")
      (do (print "error: Failed to build `broken==1.0`" :file sys.stderr) (sys.exit 1)))
  (val project (Path (! (option args "--project"))))
  (val venv (/ project ".venv"))
  (val real (os.path.realpath (get os.environ "FAKE_UV_PYTHON")))
  (val version (.format "{}.{}" sys.version-info.major sys.version-info.minor))
  (val abi (if (getattr sys "_is_gil_enabled" None) (if (sys._is-gil-enabled) "" "t") ""))
  (val site (/ venv "lib" (.format "python{}{}" version abi) "site-packages"))
  (.mkdir (/ venv "bin") :parents True :exist-ok True)
  (.mkdir site :parents True :exist-ok True)
  (.write-text (/ venv "pyvenv.cfg")
               (.format "home = {}\ninclude-system-site-packages = false\nversion = {}\n" (os.path.dirname real) version))
  (val python (/ venv "bin" "python"))
  (when (not (.exists python)) (os.symlink real python))
  (.write-text (/ site "_fake_base.pth") (.format "import site; site.addsitedir({!r})\n" (get os.environ "FAKE_UV_SITE")))
  (val cache-path (/ HOME "cache"))
  (val cache (set (if (.is-file cache-path) (.split (.read-text cache-path)) [])))
  (val lines (lfor line (.splitlines (.read-text (/ project "uv.lock") :encoding "utf-8"))
                   :if (and (.strip line) (not (.startswith (.strip line) "#"))) (.split (.strip line))))
  (val skip (set (gfor #(i a) (enumerate args) :if (= a "--no-install-package") (get args (+ i 1)))))
  (val wanted (lfor parts lines :if (not-in (get (.split (get parts 0) "==") 0) skip) (get parts 0)))
  (val missing (lfor name wanted :if (not-in name cache) name))
  (.write-text cache-path (.join "\n" (sorted (| cache (set missing)))))
  (for [parts lines]
    (for [extra (cut parts 1 None)]
      (when (.startswith extra "fake-top=")
        (val package (/ site (cut extra 9 None)))
        (.mkdir package :exist-ok True)
        (.write-text (/ package "__init__.py") "SHADOW = True\n"))))
  (<- (log-line (.format "sync project={} downloads={}" project (len missing))))
  (print (.format "Prepared {} packages in 1ms" (len missing)) :file sys.stderr)
  None)


(defk build [args]
  {:pre [(: args list)] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "uv build --wheel の代わり: 出力の dir に空の wheel を置き、log に build と書く。"
  (when (= (! (failure)) "native-build-failed")
    (print "error: could not compile `core` (lib) due to 1 previous error" :file sys.stderr)
    (sys.exit 1))
  (val out (Path (! (option args "--out-dir"))))
  (val source (Path (get args -1)))
  (.mkdir out :parents True :exist-ok True)
  (with [z (zipfile.ZipFile (/ out (.format "{}-0-py3-none-any.whl" (.replace source.name "-" "_"))) "w")]
    (.writestr z "EMPTY" ""))
  (<- (log-line (.format "build source={}" source)))
  None)


;; 答えない: 最後に os.execve でこの process を CMD に置き換える。
(defk run-command [args]
  {:pre [(: args list)] :post [(: % "答えない — os.execve でこの process を CMD に置き換える")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "uv run --project P CMD の代わり: P/.venv の python で CMD(hy / python)を exec する。"
  (val project (Path (! (option args "--project"))))
  (val rest (cut args (+ (.index args "--project") 2) None))
  (val python (str (/ project ".venv" "bin" "python")))
  (val command (get rest 0))
  (val argv (cond (= command "hy") [python "-m" "hy" #* (cut rest 1 None)]
                  (= command "python") [python #* (cut rest 1 None)]
                  True rest))
  (<- (log-line (.format "run project={} command={}" project command)))
  (val env (| (dict os.environ) {"VIRTUAL_ENV" (str (/ project ".venv"))
                                 "PATH" (+ (str (/ project ".venv" "bin")) ":" (.get os.environ "PATH" ""))}))
  (os.execve python argv env))


(defk main []
  {:pre [] :post [(: % (type None))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "命令の名(引数の先頭)で処理を選ぶため。"
  (val args (cut sys.argv 1 None))
  (val verb (get args 0))
  (cond
    (= verb "sync") (! (sync args))
    (= verb "build") (! (build args))
    (= verb "pip") (! (log-line (.format "pip-install {}" (.join " " (cut args 1 None)))))
    (= verb "run") (! (run-command args))
    True (do (print (.format "fake uv: 知らない命令 {}" args) :file sys.stderr) (sys.exit 2)))
  None)


(when (= __name__ "__main__")
  (run (main)))
