;; 起動の script(deploy/boot.sh)の自己起動が、実行環境の準備(worker の EnsureNativeWheel)と同じ鍵・同じ置き場の doeff-vm の wheel を
;; 使うことの検(2026-10-06 00:02〜00:05 の版上げで、起動の uv sync が doeff-vm を source から 94 秒かけて組み、同じ PVC に在った同じ中身の
;; wheel を使わなかった — その間 利用者の画面が切れた)。
;;
;; 失敗ケース:
;;   1 鍵の値は今までと 1 文字も変わらない(本番の PVC に在る wheel の鍵 — 起動の commit 6af862bb の tree hash で c1bb153879baff5265c2f12a)。
;;   2 worker の native-key(Hy の defk)の本体は、起動の script が使う native_wheel.native_key そのもの(差し替えると worker の鍵も変わる)。
;;   3 起動の入口 boot_wheel が組んで置いた wheel を、worker の EnsureNativeWheel(本物の翻訳 env-translation と本物の答え手)が同じ鍵で
;;     組み直さずに使う(置き場の綴りが 1 か所)。
;;   4 boot.sh を 2 回通す: 1 回目は wheel が無いので組んで置き、2 回目(root だけを消す)は置いた wheel を使い uv build を撃たない。
;;     どちらも doeff-vm を uv sync で組まず(--no-install-package doeff-vm)、wheel を root の venv へ入れる。uv build の子は呼び手の
;;     venv を継がず、cache は state の下。
;;   5 image に焼いた起動の script(引き継ぐ前)は root を展開して宣言した commit の script へ引き継ぐだけで、uv を 1 度も撃たない
;;     (準備の手順の直しが image の作り直しなしで効く — 2026-10-06 に準備を image の script が持っていて、wheel の直しが本番の image では
;;     効かなかった)。
;;   6 準備は引き継いだ先(DOEFF_BOOT_FROM_ROOT=1)がする — 引き継ぐ先の script が image の script と同じ中身でも、違う中身でも。
;;   7 drain の役は root を展開も準備もしない(preStop で組まない)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import hashlib)
(import os)
(import shutil)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [reader state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.core.native_wheel :as native-wheel)
(import doeff_cluster.shared.core.runtime_env_rules [native-key])
(import doeff_cluster.shared.intent.runtime_env_model [NativeWheel EnvFailure])
(import doeff_cluster.worker.intent.env_prepare_model [EnsureNativeWheel WheelReady])
(import doeff_cluster.worker.protocol.env_translation [env-translation])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
(val PYTHON "3.14.3t")
(val WHEEL-NAME "doeff_vm-0.1.0-cp314-cp314t-linux_x86_64.whl")
;; doeff の native の package を宣言する実行環境の native の欄(doeff を repo の名 doeff で並べる宣言の形)。
(val DOEFF-VM (NativeWheel :package native-wheel.DOEFF-VM-PACKAGE :repo "doeff" :paths native-wheel.DOEFF-VM-PATHS))
;; 偽の uv: 呼ばれた引数と、子が継いだ VIRTUAL_ENV・UV_CACHE_DIR・DOEFF_BOOT_FROM_ROOT(引き継いだ先の script か)を log へ 1 行。sync は root の venv の python(検の python へ渡すだけ)を
;; 置き、build は --out-dir に wheel を 1 つ置く。pip は何もしない。
(val FAKE-UV (+ "#!/bin/sh\n"
                "echo \"$* venv=${VIRTUAL_ENV:-} cache=${UV_CACHE_DIR:-} from=${DOEFF_BOOT_FROM_ROOT:-}\" >>\"$FAKE_UV_LOG\"\n"
                "case \"$1\" in\n"
                "  sync) mkdir -p .venv/bin\n"
                "        printf '#!/bin/sh\\nexec %s \"$@\"\\n' \"$FAKE_UV_PYTHON\" >.venv/bin/python\n"
                "        chmod 755 .venv/bin/python ;;\n"
                "  build) out=''; prev=''\n"
                "         for a in \"$@\"; do [ \"$prev\" = --out-dir ] && out=$a; prev=$a; done\n"
                "         mkdir -p \"$out\" && : >\"$out/" WHEEL-NAME "\" ;;\n"
                "esac\n"))


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作り読むために git を 1 回撃つ。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* words]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk doeff-source [tmp [root-script None]]
  {:pre [(: tmp Path) (: root-script (| str None))] :post [(: % tuple)]}
  "doeff の形の repo(.python-version・doeff-vm の 2 つの dir・起動の script)を commit し、bare の mirror を作る。起動の script は
   root-script(None = この検の木の deploy/boot.sh そのもの — image の script と同じ中身)。答え = #(repo mirror sha)。"
  (val src (/ tmp "doeff"))
  (for [#(rel text) (.items {".python-version" (+ PYTHON "\n")
                             "packages/doeff-vm/Cargo.toml" "[package]\nname = \"doeff-vm\"\n"
                             "packages/doeff-vm-core/src/lib.rs" "// core\n"
                             "packages/doeff-cluster/deploy/boot.sh" (if (is root-script None) (.read-text (Path BOOT-SH)) root-script)})]
    (val path (/ src rel))
    (.mkdir path.parent :parents True :exist-ok True)
    (.write-text path text :encoding "utf-8"))
  (<- (git tmp "init" "-q" (str src)))
  (<- (git src "add" "-A"))
  (<- (git src "commit" "-q" "-m" "doeff"))
  (<- sha str (git src "rev-parse" "HEAD"))
  (val mirror (/ tmp "doeff.git"))
  (<- (git tmp "clone" "-q" "--bare" (str src) (str mirror)))
  #(src mirror sha))


(defk worker-key [mirror sha]
  {:pre [(: mirror Path) (: sha str)] :post [(: % str)]}
  "worker の準備(env_prepare の stage-native)と同じ材料で、doeff-vm の wheel の鍵を worker の native-key で求める。"
  (<- trees str (git mirror "rev-parse" #* (gfor path DOEFF-VM.paths (.format "{}:{}" sha path))))
  (<- key str (native-key DOEFF-VM (tuple (.splitlines trees)) PYTHON (native-wheel.current-platform)))
  key)


(defk fake-uv [tmp]
  {:pre [(: tmp Path)] :post [(: % Path)]}
  "偽の uv を tmp/bin に置く。答え = その path(log は tmp/uv.log)。"
  (val bin (/ tmp "bin"))
  (.mkdir bin :exist-ok True)
  (val uv (/ bin "uv"))
  (.write-text uv FAKE-UV)
  (os.chmod uv 0o755)
  uv)


(defk uv-log [tmp]
  {:pre [(: tmp Path)] :post [(: % tuple)]}
  "偽の uv の log の行(呼ばれた順)。"
  (val path (/ tmp "uv.log"))
  (tuple (if (.is-file path) (.splitlines (.read-text path)) [])))


(defk ensure-wheel [key source-dir]
  {:pre [(: key str) (: source-dir str)] :post [(: % (| WheelReady EnvFailure))]}
  "worker の準備が出す EnsureNativeWheel を 1 回出す。"
  (<- ready (| WheelReady EnvFailure) (EnsureNativeWheel key native-wheel.DOEFF-VM-PACKAGE source-dir))
  ready)


(defk worker-wheel [state-dir uv key source-dir]
  {:pre [(: state-dir Path) (: uv Path) (: key str) (: source-dir str)] :post [(: % (| WheelReady EnvFailure))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の準備の process と同じ並び(本物の答え手 + 翻訳 env-translation — worker/entry/env_tool)で wheel を用意する。"
  (val settings {"runtime-env.state" (str state-dir) "runtime-env.repo-keys" {} "runtime-env.code-prepare" ""
                 "runtime-env.uv" (str uv) "runtime-env.progress" "" "runtime-env.notes" "/dev/null"})
  (<- ready (| WheelReady EnvFailure)
      (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                     (ensure-wheel key source-dir)))
  ready)


;; --- 1・2 鍵の関数は 1 つ ----------------------------------------------------------------------

(deftest test-the-wheel-key-is-unchanged-for-the-production-wheel []
  (val trees #("a44f7a5bb5e16ccad1fc48eb84b39e8b21a6ab49" "befe962d60c3adebda4f8941ca306c5afda6cc07"))
  (val want "c1bb153879baff5265c2f12a")
  (assert (= (native-wheel.native-key native-wheel.DOEFF-VM-PACKAGE native-wheel.DOEFF-VM-PATHS trees PYTHON "linux-x86_64") want))
  (<- worker str (native-key DOEFF-VM trees PYTHON "linux-x86_64"))
  (assert (= worker want) worker))


(deftest test-the-worker-native-key-is-the-boot-key-function [monkeypatch]
  ;; worker の native-key は鍵を自分で綴らず、起動の script と同じ native_wheel.native_key を呼ぶ(複製が在れば差し替えが効かない)。
  ;; 差し替えた関数は受けた引数の綴り(repr)の sha256 の頭を返す — 引数がそのまま渡ったことも同じ値で確かめる。
  (val spelled (fn [args] (cut (.hexdigest (hashlib.sha256 (.encode (repr args)))) 0 native-wheel.ENV-KEY-LENGTH)))
  (monkeypatch.setattr native-wheel "native_key" (fn [#* args] (spelled args)))
  (<- key str (native-key DOEFF-VM #("t1" "t2") PYTHON "linux-x86_64"))
  (assert (= key (spelled #(native-wheel.DOEFF-VM-PACKAGE native-wheel.DOEFF-VM-PATHS #("t1" "t2") PYTHON "linux-x86_64"))) key))


;; --- 3 置き場は 1 つ ---------------------------------------------------------------------------

(deftest test-the-worker-uses-the-wheel-the-boot-built [tmp-path monkeypatch]
  (<- made tuple (doeff-source tmp-path))
  (val src (get made 0))
  (val mirror (get made 1))
  (val sha (get made 2))
  (<- uv Path (fake-uv tmp-path))
  (val state-dir (/ tmp-path "state"))
  (monkeypatch.setenv "FAKE_UV_LOG" (str (/ tmp-path "uv.log")))
  ;; 起こす入口は checkout の中に bytecode を書かない(検の後の検めが checkout の .pyc を赤にする)。
  (monkeypatch.setenv "PYTHONDONTWRITEBYTECODE" "1")
  (val booted (subprocess.run [sys.executable "-m" "doeff_cluster.worker.entry.boot_wheel" "--root" (str src) "--mirror" (str mirror)
                               "--commit" sha "--state" (str state-dir) "--uv" (str uv)]
                              :capture-output True :text True :timeout 60))
  (assert (= booted.returncode 0) booted.stderr)
  (val answer (.split (.strip booted.stdout) " " 1))
  (val how (get answer 0))
  (val path (get answer -1))
  (assert (= how "組んだ") booted.stdout)
  (<- key str (worker-key mirror sha))
  (val target (native-wheel.wheel-dir (str state-dir) native-wheel.DOEFF-VM-PACKAGE key))
  (assert (= path (os.path.join target WHEEL-NAME)) #(path target))
  (assert (os.path.isfile (os.path.join target native-wheel.WHEEL-USED)) "起動も使った印を置く(掃除が 7 日で消さない)")
  ;; worker は同じ鍵で同じ置き場の wheel を見つけ、組み直さない。
  (<- ready (| WheelReady EnvFailure) (worker-wheel state-dir uv key (str (/ src (get native-wheel.DOEFF-VM-PATHS 0)))))
  (assert (= ready (WheelReady :path path :built False)) ready)
  (<- log tuple (uv-log tmp-path))
  (assert (= (len (lfor line log :if (.startswith line "build") line)) 1) log))


;; --- 4 boot.sh の 2 回 --------------------------------------------------------------------------

(defk boot-once [tmp sha [role "records"]]
  {:pre [(: tmp Path) (: sha str) (: role str)] :post [(: % subprocess.CompletedProcess)]}
  "image の起動の script を role の役(既定 records)で起こす(root の準備の後は、検の PATH に hy が無いので役の起動で落ちる)。"
  (subprocess.run ["sh" BOOT-SH]
                  :env {"PATH" (+ (str (/ tmp "bin")) ":/usr/bin:/bin") "HOME" (str tmp) "ROLE" role
                        "WORK_DIR" (str (/ tmp "work")) "WORKER_DOEFF_COMMIT" sha "WORKER_DOEFF_URL" (str (/ tmp "doeff"))
                        "FAKE_UV_LOG" (str (/ tmp "uv.log")) "FAKE_UV_PYTHON" sys.executable "PYTHONDONTWRITEBYTECODE" "1"
                        ;; 呼び手の venv(uv build の子へ継がせない物)
                        "VIRTUAL_ENV" (str (/ tmp "caller-venv"))}
                  :capture-output True :text True :timeout 60))


(deftest test-boot-sh-builds-the-wheel-once-and-reuses-it [tmp-path]
  (<- made tuple (doeff-source tmp-path))
  (val mirror (get made 1))
  (val sha (get made 2))
  (<- (fake-uv tmp-path))
  (<- key str (worker-key mirror sha))
  (val state-dir (str (/ tmp-path "work" "state")))
  (val wheel (os.path.join (native-wheel.wheel-dir state-dir native-wheel.DOEFF-VM-PACKAGE key) WHEEL-NAME))
  (val python (str (/ tmp-path "work" "boot" "roots" sha ".venv" "bin" "python")))
  (<- first subprocess.CompletedProcess (boot-once tmp-path sha))
  (assert (in "root を準備した" first.stderr) first.stderr)
  (assert (in "組んだ" first.stderr) first.stderr)
  (<- once tuple (uv-log tmp-path))
  (val syncs (lfor line once :if (.startswith line "sync") line))
  (assert (and (= (len syncs) 1) (in "--no-install-package doeff-vm" (get syncs 0))) once)
  (val builds (lfor line once :if (.startswith line "build") line))
  (assert (= (len builds) 1) #(once first.stderr))
  (assert (in (.format " venv= cache={}/uv-cache from=1" state-dir) (get builds 0)) builds)
  (assert (= (lfor line once :if (.startswith line "pip") line)
             [(.format "pip install --no-deps --python {} {} venv={} cache={}/uv-cache from=1"
                       python wheel (/ tmp-path "caller-venv") state-dir)])
          once)
  ;; 2 回目: root だけを消す(同じ node の次の Pod が別の commit の root を持つ時と同じ)— wheel は置き場に在るので組まない。
  (shutil.rmtree (/ tmp-path "work" "boot" "roots" sha))
  (<- second subprocess.CompletedProcess (boot-once tmp-path sha))
  (assert (in "root を準備した" second.stderr) second.stderr)
  (assert (in "使った" second.stderr) second.stderr)
  (<- twice tuple (uv-log tmp-path))
  (assert (= (len (lfor line twice :if (.startswith line "build") line)) 1) #(twice second.stderr))
  (assert (= (len (lfor line twice :if (.startswith line "pip") line)) 2) twice))


;; --- 5・6・7 展開は image の script・準備は引き継いだ先 -------------------------------------------------

(val STUB-ROOT-SCRIPT "echo \"root の script: from=$DOEFF_BOOT_FROM_ROOT\"\n")


(deftest test-the-image-script-only-extracts-and-hands-over [tmp-path]
  ;; 宣言した commit の起動の script が名乗るだけの物なら、uv は 1 度も呼ばれない — image の script は展開と引き継ぎだけをする。
  (<- made tuple (doeff-source tmp-path STUB-ROOT-SCRIPT))
  (val sha (get made 2))
  (<- (fake-uv tmp-path))
  (<- done subprocess.CompletedProcess (boot-once tmp-path sha))
  (assert (= done.returncode 0) done.stderr)
  (assert (in "root の script: from=1" done.stdout) #(done.stdout done.stderr))
  (<- log tuple (uv-log tmp-path))
  (assert (= log #()) #(log done.stderr))
  (val root (/ tmp-path "work" "boot" "roots" sha))
  (assert (.is-file (/ root ".doeff-boot-extracted")) "展開の済んだ印")
  (assert (not (.exists (/ root ".doeff-boot-ready"))) "準備は引き継いだ先の受け持ち"))


(deftest test-the-handed-over-script-prepares-whether-or-not-it-differs [tmp-path]
  ;; 引き継ぐ先の script が image の script と同じ中身(cmp が同じ)でも、違う中身でも、準備(uv sync・build・pip)は引き継いだ先がする。
  (for [#(name script) #(#("same" None) #("changed" (+ (.read-text (Path BOOT-SH)) "# 版の違う起動の script\n")))]
    (val tmp (/ tmp-path name))
    (.mkdir tmp)
    (<- made tuple (doeff-source tmp script))
    (<- (fake-uv tmp))
    (<- done subprocess.CompletedProcess (boot-once tmp (get made 2)))
    (assert (in "root を準備した" done.stderr) #(name done.stderr))
    (<- log tuple (uv-log tmp))
    (assert (= (len log) 3) #(name log))
    (assert (all (gfor line log (.endswith line " from=1"))) #(name log))))


(deftest test-drain-neither-extracts-nor-prepares [tmp-path]
  (<- made tuple (doeff-source tmp-path))
  (<- (fake-uv tmp-path))
  (<- done subprocess.CompletedProcess (boot-once tmp-path (get made 2) "drain"))
  (assert (!= done.returncode 0) done.stderr)
  (assert (in "drain は準備しない" done.stderr) done.stderr)
  (<- log tuple (uv-log tmp-path))
  (assert (= log #()) log)
  (assert (= (list (.iterdir (/ tmp-path "work" "boot" "roots"))) []) "drain は展開もしない"))
