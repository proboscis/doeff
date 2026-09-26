;;; 丁寧な模擬の fake の uv(test_env_careful.hy が PATH の先頭に置く sh の包みから起こす)。本物の local-env が呼ぶ 4 つの命令だけを模す:
;;;
;;;   uv sync --locked --project P --python X --no-default-groups [--group g]… [--no-install-package n]…
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
(import json os sys zipfile)
(import pathlib [Path])

(setv HOME (Path (get os.environ "FAKE_UV_DIR")))


(defn log-line [#^ str text]  ; defk にできない: 検の道具の process の入口(Program の外)
  (with [f (open (/ HOME "log") "a" :encoding "utf-8")] (.write f (+ text "\n"))))


(defn failure [] ; defk にできない: 検の道具の process の入口(Program の外)
  (setv path (/ HOME "fail"))
  (if (.is-file path) (.strip (.read-text path :encoding "utf-8")) ""))


(defn option [#^ list args #^ str flag]  ; defk にできない: 検の道具の process の入口(Program の外)
  (get args (+ (.index args flag) 1)))


(defn sync [#^ list args]  ; defk にできない: 検の道具の process の入口(Program の外)
  (setv kind (failure))
  (cond
    (= kind "lock-stale")
      (do (print "error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided." :file sys.stderr)
          (sys.exit 2))
    (= kind "python-unavailable")
      (do (print "error: No interpreter found for Python 9.9 in managed installations" :file sys.stderr) (sys.exit 2))
    (= kind "sync-failed")
      (do (print "error: Failed to build `broken==1.0`" :file sys.stderr) (sys.exit 1)))
  (setv project (Path (option args "--project")) venv (/ project ".venv")
        real (os.path.realpath (get os.environ "FAKE_UV_PYTHON"))
        version (.format "{}.{}" sys.version-info.major sys.version-info.minor)
        abi (if (getattr sys "_is_gil_enabled" None) (if (sys._is-gil-enabled) "" "t") "")
        site (/ venv "lib" (.format "python{}{}" version abi) "site-packages"))
  (.mkdir (/ venv "bin") :parents True :exist-ok True)
  (.mkdir site :parents True :exist-ok True)
  (.write-text (/ venv "pyvenv.cfg")
               (.format "home = {}\ninclude-system-site-packages = false\nversion = {}\n" (os.path.dirname real) version))
  (setv python (/ venv "bin" "python"))
  (when (not (.exists python)) (os.symlink real python))
  (.write-text (/ site "_fake_base.pth") (.format "import site; site.addsitedir({!r})\n" (get os.environ "FAKE_UV_SITE")))
  (setv cache-path (/ HOME "cache") cache (set (if (.is-file cache-path) (.split (.read-text cache-path)) [])))
  (setv lines (lfor line (.splitlines (.read-text (/ project "uv.lock") :encoding "utf-8"))
                    :if (and (.strip line) (not (.startswith (.strip line) "#"))) (.split (.strip line))))
  (setv skip (set (gfor #(i a) (enumerate args) :if (= a "--no-install-package") (get args (+ i 1)))))
  (setv wanted (lfor parts lines :if (not-in (get (.split (get parts 0) "==") 0) skip) (get parts 0))
        missing (lfor name wanted :if (not-in name cache) name))
  (.write-text cache-path (.join "\n" (sorted (| cache (set missing)))))
  (for [parts lines]
    (for [extra (cut parts 1 None)]
      (when (.startswith extra "fake-top=")
        (setv package (/ site (cut extra 9 None)))
        (.mkdir package :exist-ok True)
        (.write-text (/ package "__init__.py") "SHADOW = True\n"))))
  (log-line (.format "sync project={} downloads={}" project (len missing)))
  (print (.format "Prepared {} packages in 1ms" (len missing)) :file sys.stderr))


(defn build [#^ list args]  ; defk にできない: 検の道具の process の入口(Program の外)
  (when (= (failure) "native-build-failed")
    (print "error: could not compile `core` (lib) due to 1 previous error" :file sys.stderr)
    (sys.exit 1))
  (setv out (Path (option args "--out-dir")) source (Path (get args -1)))
  (.mkdir out :parents True :exist-ok True)
  (with [z (zipfile.ZipFile (/ out (.format "{}-0-py3-none-any.whl" (.replace source.name "-" "_"))) "w")]
    (.writestr z "EMPTY" ""))
  (log-line (.format "build source={}" source)))


(defn run-command [#^ list args]  ; defk にできない: 検の道具の process の入口(Program の外)
  (setv project (Path (option args "--project"))
        rest (cut args (+ (.index args "--project") 2) None)
        python (str (/ project ".venv" "bin" "python"))
        command (get rest 0)
        argv (cond (= command "hy") [python "-m" "hy" #* (cut rest 1 None)]
                   (= command "python") [python #* (cut rest 1 None)]
                   True rest))
  (log-line (.format "run project={} command={}" project command))
  (setv env (| (dict os.environ) {"VIRTUAL_ENV" (str (/ project ".venv"))
                                  "PATH" (+ (str (/ project ".venv" "bin")) ":" (.get os.environ "PATH" ""))}))
  (os.execve python argv env))


(defn main []  ; defk にできない: 検の道具の process の入口(Program の外)
  (setv args (cut sys.argv 1 None) verb (get args 0))
  (cond
    (= verb "sync") (sync args)
    (= verb "build") (build args)
    (= verb "pip") (log-line (.format "pip-install {}" (.join " " (cut args 1 None))))
    (= verb "run") (run-command args)
    True (do (print (.format "fake uv: 知らない命令 {}" args) :file sys.stderr) (sys.exit 2))))


(when (= __name__ "__main__")
  (main))
