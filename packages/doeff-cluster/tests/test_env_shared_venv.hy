;; 依存のキーごとに 1 つの共有の venv(env_translation の ShareVenv・UseSharedVenv と、env_store の掃除の unused-venvs)の検 — 本物の
;; file の答え手(os-file-handler)と tmp の下の本物の dir・本物の python。
;;
;; 前は root の準備のたびに、依存のキーの同じ完成済みの root の venv を file ごとに hardlink で写した(venv 1 つ約 4,400 file — 家の server
;; の SSD の inode の表の block の 6%)。だから:
;;   - 1 つ目の root の venv は共有の venv の置き場へ移り、root には薄い venv が残る(bin は共有の venv の bin への symlink・site-packages に
;;     共有の site-packages を足す .pth と、root の path を持つ .pth だけ)
;;   - 依存のキーの同じ 2 つ目の root が venv のために置く物は 100 以下で、共有の venv の file に hardlink を張らない
;;   - どちらの root の python も sys.prefix が自分の薄い venv で、共有の venv の package と自分の root の editable の package を import
;;     でき、bin の script も自分の薄い venv の python で起きる
;;   - 共有の venv は、使う root が無くなった後の掃除で消える(錠を取られている間・使う root が在る間は消えない)
;; 反例 = 直す前の形(root ごとに venv を写す)は、2 つ目の root が venv の file の数(ここでは 900 を越える)だけ張って赤。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import fcntl)
(import os)
(import subprocess)
(import sys)
(import sysconfig)
(import time)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [os-file-handler inline-compute-handler slog-handler reader state subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster [env-translation])
(import doeff_cluster._worker.intent.env_prepare_model [ShareVenv UseSharedVenv VenvShared SHARED-VENV-PTH])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster._worker.protocol.env_store [EnvSettings sweep-leftovers])

(val NAME "0123456789abcdef01234567-89abcdef01234567")
(val PACKAGES 300)
;; この interpreter の venv の site-packages の dir の名(free-threaded の版は t が付く)。
(val PY-DIR (.format "python{}.{}{}" (get sys.version-info 0) (get sys.version-info 1)
                     (if (sysconfig.get-config-var "Py_GIL_DISABLED") "t" "")))


(defk made-tree [root]
  {:pre [(: root Path)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "program"}}
  "root の下に editable で入る依存の repo の木(lib/src/libpkg — 自分の file の path を答える)を置き、その dir を返すため。"
  (val src (/ root "lib" "src"))
  (.mkdir (/ src "libpkg") :parents True)
  (.write-text (/ src "libpkg" "__init__.py") "WHERE = __file__\n")
  src)


(defk made-venv [venv root]
  {:pre [(: venv Path) (: root Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "uv sync が root の venv に置く形(bin の python の symlink と venv の python を頭に書いた script・lib64・site-packages の package と
   editable の .pth)を手で作り、venv の file と symlink の数を返すため。"
  (val site (/ venv "lib" PY-DIR "site-packages"))
  (.mkdir site :parents True)
  (.mkdir (/ venv "bin"))
  (val python (os.path.realpath sys.executable))
  (os.symlink python (/ venv "bin" "python"))
  (os.symlink "lib" (/ venv "lib64"))
  (.write-text (/ venv "pyvenv.cfg") (.format "home = {}\ninclude-system-site-packages = false\n" (os.path.dirname python)))
  (.write-text (/ venv "bin" "where") (.format "#!{}/bin/python\nimport sys\nprint(sys.prefix)\n" venv))
  (os.chmod (/ venv "bin" "where") 0o755)
  (for [i (range PACKAGES)]
    (.mkdir (/ site (.format "pkg_{}" i)))
    (.write-text (/ site (.format "pkg_{}" i) "__init__.py") (.format "N = {}\n" i))
    (.mkdir (/ site (.format "pkg_{}-1.0.dist-info" i)))
    (.write-text (/ site (.format "pkg_{}-1.0.dist-info" i) "METADATA") "Name: pkg\n")
    (.write-text (/ site (.format "pkg_{}-1.0.dist-info" i) "RECORD") ""))
  (<- src Path (made-tree root))
  (.write-text (/ site "_editable_impl_lib.pth") (+ (str src) "\n"))
  (.write-text (/ site "lib_guard.pth") "import libpkg; libpkg.STARTED = True\n")
  (len (lfor p (.rglob venv "*") :if (or (.is-symlink p) (.is-file p)) p)))


(defk entries-under [venv]
  {:pre [(: venv Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "venv の下の file と symlink(symlink の先には入らない)の path の列を読むため。"
  (tuple (gfor #(d dirs files) (os.walk venv) name (+ dirs files)
               :setv path (Path d name)
               :if (or (.is-symlink path) (.is-file path))
               path)))


(defk prefix-and-imports [venv]
  {:pre [(: venv Path)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "venv の python で sys.prefix と、共有の venv の package と editable の package の解け先を読み、venv の bin の script の答え(sys.prefix)を
   足した行の列を返すため。"
  (val code "import sys, pkg_7, libpkg; assert libpkg.STARTED; print(sys.prefix); print(pkg_7.N); print(libpkg.WHERE)")
  (val ran (subprocess.run [(str (/ venv "bin" "python")) "-c" code] :capture-output True :text True :env {"PATH" "/usr/bin:/bin"}))
  (assert (= ran.returncode 0) ran.stderr)
  (val script (subprocess.run [(str (/ venv "bin" "where"))] :capture-output True :text True :env {"PATH" "/usr/bin:/bin"}))
  (assert (= script.returncode 0) script.stderr)
  (+ (.splitlines ran.stdout) (.splitlines script.stdout)))


(defk translation-settings [state-dir]
  {:pre [(: state-dir Path)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "翻訳の設定(runtime-env.*)を、分け合う dir を state-dir にして作るため。"
  {"runtime-env.state" (str state-dir) "runtime-env.shared" (str state-dir) "runtime-env.uv-cache" (str (/ state-dir "uv-cache"))
   "runtime-env.repo-keys" {} "runtime-env.code-prepare" "" "runtime-env.uv" "uv" "runtime-env.progress" ""
   "runtime-env.notes" "/dev/null" "runtime-env.tree-mount" "" "runtime-env.tree-unmount" ""})


(deftest test-a-second-root-of-one-dependency-key-places-a-thin-venv-of-at-most-100-files [tmp-path]
  (val state-dir (/ tmp-path "state"))
  (val r1 (/ state-dir "roots" "r1"))
  (val r2 (/ state-dir "roots" "r2"))
  (<- total int (made-venv (/ r1 ".venv") r1))
  (<- src2 Path (made-tree r2))
  (<- settings dict (translation-settings state-dir))
  (val handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation])
  (<- first (with-handlers handlers (ShareVenv NAME (str (/ r1 ".venv")) (str r1))))
  (assert (and (isinstance first VenvShared) first.published) first)
  (val shared (/ state-dir "venvs" NAME))
  (assert (= first.shared (str shared)) first)
  (<- second (with-handlers handlers (UseSharedVenv NAME (str (/ r2 ".venv")) (str r2))))
  (assert (and (isinstance second VenvShared) (not second.published)) second)
  ;; 2 つ目の root が venv のために置いた物: 100 以下(写せば venv の file の数だけ)・共有の venv の file に hardlink を張らない。
  (<- placed tuple (entries-under (/ r2 ".venv")))
  (assert (> total 900) #("venv の file の数(写せば 100 を越える)" total))
  (assert (<= (len placed) 100) #("2 つ目の root が venv のために置いた物" (len placed) placed))
  (assert (= second.written (len placed)) #(second.written placed))
  (for [p placed]
    (assert (or (.is-symlink p) (= (. (.stat p) st-nlink) 1)) #("薄い venv の file に hardlink が在る" p)))
  (assert (.is-symlink (/ r2 ".venv" "bin")) "薄い venv の bin は共有の venv の bin への symlink")
  (val pth (/ r2 ".venv" "lib" PY-DIR "site-packages" "_editable_impl_lib.pth"))
  (assert (= (.read-text pth) (+ (str src2) "\n")) "editable の .pth は 2 つ目の root の木を指す")
  (assert (.exists (/ r2 ".venv" "lib" PY-DIR "site-packages" SHARED-VENV-PTH)))
  (assert (not (.exists (/ shared "lib" PY-DIR "site-packages" "_editable_impl_lib.pth")))
          "root の path を持つ .pth は共有の site-packages に残さない")
  ;; どちらの root の python も、自分の薄い venv の prefix で、共有の package と自分の root の editable の package を import する。
  (for [#(root src) #(#(r1 (/ r1 "lib" "src")) #(r2 src2))]
    (<- lines list (prefix-and-imports (/ root ".venv")))
    (assert (= lines [(str (/ root ".venv")) "7" (str (/ src "libpkg" "__init__.py")) (str (/ root ".venv"))]) #(root lines))))


(deftest test-the-sweep-removes-shared-venvs-no-root-uses [tmp-path]
  (val state-dir (/ tmp-path "state"))
  (val venvs (/ state-dir "venvs"))
  (val locks (/ state-dir "locks"))
  (.mkdir locks :parents True)
  (for [name #("used" "idle" "held" ".half.making")]
    (.mkdir (/ venvs name "bin") :parents True))
  ;; root r1 の薄い venv が used を使う(bin が used の bin への symlink)。
  (val r1 (/ state-dir "roots" "r1"))
  (.mkdir (/ r1 ".venv") :parents True)
  (os.symlink (/ venvs "used" "bin") (/ r1 ".venv" "bin"))
  (val settings (EnvSettings :state (str state-dir) :uv-cache (str (/ tmp-path "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :roots-cap-bytes 0))
  (val now-ms (int (* 1000 (time.time))))
  ;; 錠を取られている共有の venv(準備が置いている最中): 検の殻が準備と同じ錠の file を持つ間に掃除する。
  (val fd (os.open (str (/ locks "venv-held")) (| os.O-RDWR os.O-CREAT) 0o644))
  (fcntl.flock fd fcntl.LOCK-EX)
  (try
    (<- removed tuple (with-handlers [slog-handler os-file-handler inline-compute-handler] (sweep-leftovers settings now-ms)))
    (finally (fcntl.flock fd fcntl.LOCK-UN) (os.close fd)))
  (assert (= (sorted removed) (sorted [(str (/ venvs ".half.making")) (str (/ venvs "idle"))])) removed)
  (assert (.exists (/ venvs "used")) "root が使う共有の venv を消した")
  (assert (.exists (/ venvs "held")) "錠を取られている共有の venv を消した")
  ;; root が消え、錠が放された後の掃除では、使う root の無くなった共有の venv も消える。
  (os.unlink (/ r1 ".venv" "bin"))
  (<- again tuple (with-handlers [slog-handler os-file-handler inline-compute-handler] (sweep-leftovers settings now-ms)))
  (assert (= (sorted again) (sorted [(str (/ venvs "held")) (str (/ venvs "used"))])) again))
