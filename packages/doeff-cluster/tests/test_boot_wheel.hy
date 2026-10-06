;; 起動の script(deploy/boot.sh)の自己起動と実行環境の準備(worker の EnsureNativeWheel)が、どちらも `uv build --wheel` で build の口
;; (doeff の tools/doeff_cargo_backend.py — Rust の部品を組む・引く入口の 1 つ・ADR-DOE-BUILD-001)を通り、口の保存先の同じ wheel を
;; 使うことの検(2026-10-06 00:02〜00:05 の版上げで、起動の uv sync が doeff-vm を source から 94 秒かけて組み、同じ PVC に在った同じ中身の
;; wheel を使わなかった — その間 利用者の画面が切れた・#3860 で worker の自前の git tree hash の鍵と置き場を消した)。偽の uv の build は
;; 本物の口と同じ形で答える: source の中身の鍵で DOEFF_WHEEL_CACHE の <package>-<鍵>/ を引き、無ければ置き、--out-dir に写しを置き、
;; DOEFF_WHEEL_REPORT へ保存先の中の wheel と組んだかの 1 行を足す。入れる wheel は --out-dir に出た file で、報告は観測だけ。
;;
;; 失敗ケース:
;;   1 口の報告の行の読み(native_wheel の reported-built)は package の最後の行の「組んだか」を答え、行が無い時は「報告が無い」
;;     (NotReported — 組んだ・使ったのどちらにも埋めない)、形が違う時は理由の文。報告を書かない版の口(宣言の古い doeff の root)で
;;     用意しても、--out-dir に出た wheel を受け、由来は UNREPORTED(native-build-failed で止まらない)。
;;   2 起動の入口 boot_wheel が口を通して置かせた wheel を、worker の EnsureNativeWheel(本物の翻訳 env-translation と本物の答え手)が
;;     同じ口の保存先から組ませずに使う — 自前の鍵(git の tree hash)を引く形に戻すと、口の保存先の wheel を見つけられず赤。source の
;;     中身を変えると口が 1 度だけ組む。
;;   3 起動の入口は --mirror・--commit を受けない(tree hash を読む道を持たない)。
;;   4 boot.sh を 2 回通す: 1 回目は口が組んで置き、2 回目(root だけを消す)は口が置いた wheel を使う。どちらも doeff-vm を uv sync で
;;     組まず(--no-install-package doeff-vm)、root の下の --out-dir に出た wheel を root の venv へ入れる。uv build の子は呼び手の venv を継がず、
;;     cache は state の下。
;;   5 image に焼いた起動の script(引き継ぐ前)は root を展開して宣言した commit の script へ引き継ぐだけで、uv を 1 度も撃たない
;;     (準備の手順の直しが image の作り直しなしで効く — 2026-10-06 に準備を image の script が持っていて、wheel の直しが本番の image では
;;     効かなかった)。
;;   6 準備は引き継いだ先(DOEFF_BOOT_FROM_ROOT=1)がする — 引き継ぐ先の script が image の script と同じ中身でも、違う中身でも。
;;   7 drain の役は root を展開も準備もしない(preStop で組まない)。
;;   8 新しい root の準備は uv sync に --compile-bytecode を付け(site-packages の .pyc)、root の中の source は焼く道具(root の
;;     worker/entry/code_prepare.hy)を root の venv の hy で 1 回起こして BOOT_ENTRIES の閉包だけを検める方式(PEP 552 の checked
;;     hash)で焼く。焼く根は venv の .pth が書く root の中の dir だけ(import の行と root の外の dir は根にしない・末尾の改行の無い
;;     .pth も読む)。閉包の外の module は焼かない。準備の行に焼いた数を載せる(#3725 — 前は起動の exec から import の終わりまでに
;;     15〜20 秒、全部の Hy の module を import の時に compile していた)。
;;   9 次の版の root は、前の準備済みの root(完成の印と焼く道具の印が在り、Python と Hy の版が同じ)から、変わっていない file の .pyc を
;;     hardlink で引き継ぎ、変わった file(前の sha との git diff)だけを焼く。変わった path の一覧の file は焼いた後に消す。
;;  10 BOOT_ENTRIES は、boot.sh が root の venv の hy で起こす module の全部と、worker が同じ venv で起こす準備の process(env_tool)・
;;     shim・job の子の入口(job_entry)と、workspace の package が site-packages へ入れる .pth の import の行が起こす module
;;     (doeff-hy の doeff_hy_bytecode_guard — interpreter の起動ごとに import される)を名指す。boot.sh に綴った焼く道具の印の名は
;;     code_plan の MARKER と同じ。
;;  11 uv の cache の dir は呼び手が渡せる: worker の準備の uv の子は設定 runtime-env.uv-cache(worker の --uv-cache)を、起動の script の
;;     uv の子は呼び手の DOEFF_UV_CACHE_DIR を UV_CACHE_DIR として継ぐ(既定は $WORK_DIR/state/uv-cache — 件ごとに worker を起こす
;;     テストは件をまたいで同じ cache を渡し、依存を件ごとに取り直さない・#3858)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import importlib.util)
(import os)
(import re)
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
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure])
(import doeff_cluster.worker.core.code_plan [MARKER cache-rel])
(import doeff_cluster.worker.core.launch [shim-argv])
(import doeff_cluster.worker.intent.env_prepare_model [EnsureNativeWheel WheelOrigin WheelReady])
(import doeff_cluster.worker.protocol.declared [JOB-ENTRY])
(import doeff_cluster.worker.protocol.env_store [ENV-TOOL])
(import doeff_cluster.worker.protocol.env_translation [env-translation])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
;; この検の木の doeff_cluster(焼く道具の本物の file を検の doeff の root へ写すため)と、root の中の doeff_cluster の置き場。
(val CLUSTER-SRC (/ (. (Path __file__) (resolve) parent parent) "src" "doeff_cluster"))
;; workspace の package の dir の並ぶ所(doeff の repo の packages)。
(val PACKAGES (. (Path __file__) (resolve) parent parent parent))
(val ROOT-CLUSTER "packages/doeff-cluster/src/doeff_cluster")
;; 焼く道具の file(入口・判断の module・焼きの道具 — 入口は後の 2 つを自分の位置から求めた path で読む)。
(val TOOL-FILES #("worker/entry/code_prepare.hy" "worker/core/bake_plan.hy" "foundation/bytecode_pool.hy"))
;; 焼く道具が同じ checkout の doeff-hy から path で読む保存先の入口(root の中の置き場 — 検の doeff の root へ本物の file を写す)。
(val STORE-FILE "packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py")
;; 起動の入口と同じ名の小さな module(worker/entry/main は worker/core/boot_helper を import する — 閉包)と、どこからも import されない
;; worker/core/unused(閉包の外)。
(val ENTRY-MODULES {"worker/entry/main.hy" "(import doeff_cluster.worker.core.boot_helper [answer])\n(setv V answer)\n"
                    "worker/core/boot_helper.hy" "(setv answer 1)\n"
                    "worker/entry/drain_main.hy" "(setv V 1)\n"
                    "coordinator/entry/main.hy" "(setv V 1)\n"
                    "record_store/entry/main.hy" "(setv V 1)\n"
                    "worker/entry/env_tool.hy" "(setv V 1)\n"
                    "worker/entry/job_entry.hy" "(setv V 1)\n"
                    "worker/entry/shim.py" "V = 1\n"
                    "worker/core/unused.hy" "(setv U 2)\n"})
(val OUTSIDE-CLOSURE "worker/core/unused.hy")
(val BAKED (tuple (gfor rel ENTRY-MODULES :if (!= rel OUTSIDE-CLOSURE) rel)))
;; PEP 552 の hash 方式の .pyc の頭の flags(bit 0 = hash 方式・bit 1 = import の時に source の hash を検める)。
(val CHECKED-HASH 0b11)
(val PYTHON "3.14.3t")
(val WHEEL-NAME "doeff_vm-0.1.0-cp314-cp314t-linux_x86_64.whl")
;; 偽の uv: 呼ばれた引数と、子が継いだ VIRTUAL_ENV・UV_CACHE_DIR・DOEFF_BOOT_FROM_ROOT(引き継いだ先の script か)を log へ 1 行。sync は root の venv の python(検の python へ渡すだけ)・
;; hy(引数を FAKE_HY_LOG へ 1 行書き、file を起こす時 = 焼く道具は検の python の hy へ渡す・-m で役を起こす時は終わり 3 で止まる — 検の役の起動は
;; root の準備の後で落ちる)・Hy の dist-info と、uv と同じく末尾に改行の無い .pth(root そのもの・root の中の dir・root の外の dir・import の行だけの
;; 物)を置く。build は本物の build の口と同じ形(source の中身の鍵で DOEFF_WHEEL_CACHE の doeff-vm-<鍵>/ を引き、無ければ置き、--out-dir に
;; 写しを置き、DOEFF_WHEEL_REPORT へ 1 行)。pip は何もしない。
(val FAKE-UV (+ "#!/bin/sh\n"
                "echo \"$* venv=${VIRTUAL_ENV:-} cache=${UV_CACHE_DIR:-} from=${DOEFF_BOOT_FROM_ROOT:-}\" >>\"$FAKE_UV_LOG\"\n"
                "case \"$1\" in\n"
                "  sync) site=.venv/lib/python3.14t/site-packages\n"
                "        mkdir -p .venv/bin \"$site/hy-1.0.0.dist-info\"\n"
                "        printf '#!/bin/sh\\nexec %s \"$@\"\\n' \"$FAKE_UV_PYTHON\" >.venv/bin/python\n"
                "        printf '#!/bin/sh\\necho \"$*\" >>\"$FAKE_HY_LOG\"\\n[ \"$1\" != -m ] || exit 3\\nexec %s -m hy \"$@\"\\n' \"$FAKE_UV_PYTHON\" >.venv/bin/hy\n"
                "        chmod 755 .venv/bin/python .venv/bin/hy\n"
                "        printf '%s' \"$PWD\" >\"$site/_editable_impl_doeff.pth\"\n"
                "        printf '%s/packages/doeff-cluster/src' \"$PWD\" >\"$site/_editable_impl_doeff_cluster.pth\"\n"
                "        printf '%s' /usr >\"$site/_outside_root.pth\"\n"
                "        printf '%s' 'import doeff_hy_bytecode_guard; doeff_hy_bytecode_guard.install()' >\"$site/doeff_hy_bytecode_guard.pth\"\n"
                ;; uv の焼きの子と同じく、venv の .pth が名指す root の中の module(ここでは shim)を import の口(SourceFileLoader)で
                ;; 読む — 呼び手が PYTHONDONTWRITEBYTECODE を立てていなければ、その source の隣に timestamp の方式の .pyc が書かれる。
                ;; package の名で引かず path で読む(検の環境の doeff_cluster に解けて検の外の木へ書かないため)。
                "        shim=packages/doeff-cluster/src/doeff_cluster/worker/entry/shim.py\n"
                "        [ ! -f \"$shim\" ] || \"$FAKE_UV_PYTHON\" -c 'import importlib.machinery as m, sys; m.SourceFileLoader(\"shim\", sys.argv[1]).get_code(\"shim\")' \"$shim\" ;;\n"
                "  build) out=''; prev=''; src=''\n"
                "         for a in \"$@\"; do [ \"$prev\" = --out-dir ] && out=$a; prev=$a; src=$a; done\n"
                ;; 報告の約束の無い版の口(FAKE_UV_OLD_BACKEND — #3860 の前の doeff の root の口): 保存先を引かず --out-dir に組み、報告を書かない。
                "         if [ -n \"${FAKE_UV_OLD_BACKEND:-}\" ]; then mkdir -p \"$out\" && : >\"$out/" WHEEL-NAME "\"; exit 0; fi\n"
                "         key=$(cd \"$src\" && find . -type f | LC_ALL=C sort | xargs cat | sha256sum | cut -c1-32)\n"
                "         slot=\"$DOEFF_WHEEL_CACHE/doeff-vm-$key\"; built=false\n"
                "         if [ ! -f \"$slot/" WHEEL-NAME "\" ]; then mkdir -p \"$slot\" && : >\"$slot/" WHEEL-NAME "\"; built=true; fi\n"
                "         mkdir -p \"$out\" && cp \"$slot/" WHEEL-NAME "\" \"$out/" WHEEL-NAME "\"\n"
                "         [ -z \"${DOEFF_WHEEL_REPORT:-}\" ] || printf '{\"project\": \"doeff-vm\", \"wheel\": \"%s\", \"built\": %s}\\n' \"$slot/" WHEEL-NAME "\" \"$built\" >>\"$DOEFF_WHEEL_REPORT\" ;;\n"
                "esac\n"))


(defk git [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "検の repo を作り読むために git を 1 回撃つ。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* words]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk doeff-source [tmp [root-script None] * [bake False]]
  {:pre [(: tmp Path) (: root-script (| str None)) (: bake bool)] :post [(: % tuple)]}
  "doeff の形の repo(.python-version・doeff-vm の 2 つの dir・起動の script)を commit し、bare の mirror を作る。起動の script は
   root-script(None = この検の木の deploy/boot.sh そのもの — image の script と同じ中身)。bake = 焼く道具の本物の file と起動の入口と
   同じ名の小さな module も commit する(偽なら root の準備は焼く道具が無いので焼かずに注記だけ — 焼きの検の外の検の秒を
   増やさない)。答え = #(repo mirror sha)。"
  (val src (/ tmp "doeff"))
  (val tools (if bake
                 (| (dfor rel TOOL-FILES (.format "{}/{}" ROOT-CLUSTER rel) (.read-text (/ CLUSTER-SRC rel) :encoding "utf-8"))
                    {STORE-FILE (.read-text (/ (. PACKAGES parent) STORE-FILE) :encoding "utf-8")})
                 {}))
  (val modules (if bake (dfor #(rel text) (.items ENTRY-MODULES) (.format "{}/{}" ROOT-CLUSTER rel) text) {}))
  (for [#(rel text) (.items (| {".python-version" (+ PYTHON "\n")
                                "packages/doeff-vm/Cargo.toml" "[package]\nname = \"doeff-vm\"\n"
                                "packages/doeff-vm-core/src/lib.rs" "// core\n"
                                "packages/doeff-cluster/deploy/boot.sh" (if (is root-script None) (.read-text (Path BOOT-SH)) root-script)}
                               tools modules))]
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


(defk hy-log [tmp]
  {:pre [(: tmp Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "root の venv の偽の hy が受けた引数の行(呼ばれた順・log は tmp/hy.log)— 焼く道具へ渡した --roots などを外から読むため。"
  (val path (/ tmp "hy.log"))
  (tuple (if (.is-file path) (.splitlines (.read-text path)) [])))


(defk argument-of [line flag]
  {:pre [(: line str) (: flag str)] :post [(: % (| str None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "偽の hy の log の 1 行(空白で並べた引数)から flag の次の値を読むため(無ければ None)。"
  (val words (.split line))
  (val at (next (gfor #(i w) (enumerate words) :if (= w flag) i) None))
  (if (or (is at None) (>= (+ at 1) (len words))) None (get words (+ at 1))))


(defk prepared-line [stderr]
  {:pre [(: stderr str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "boot.sh の stderr から「root を準備した」の行を読むため(無ければ空文字 — 断言が stderr を名指す)。"
  (next (gfor line (.splitlines stderr) :if (in "root を準備した" line) line) ""))


(defk pyc-of [root rel]
  {:pre [(: root Path) (: rel str)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "root の中の doeff_cluster の source(rel)の .pyc の path を求めるため(import が探す __pycache__ の名)。"
  (/ root ROOT-CLUSTER (cache-rel rel)))


(defk pyc-flags [path]
  {:pre [(: path Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "焼いた .pyc の頭の flags(PEP 552 — magic の後の 4 byte)を読むため。"
  (int.from-bytes (cut (.read-bytes path) 4 8) "little"))


(defk ensure-wheel [source-dir out-dir]
  {:pre [(: source-dir str) (: out-dir str)] :post [(: % (| WheelReady EnvFailure))]}
  "worker の準備が出す EnsureNativeWheel を 1 回出す。"
  (<- ready (| WheelReady EnvFailure) (EnsureNativeWheel native-wheel.DOEFF-VM-PACKAGE source-dir out-dir))
  ready)


(defk worker-wheel [state-dir uv-cache uv source-dir]
  {:pre [(: state-dir Path) (: uv-cache Path) (: uv Path) (: source-dir str)] :post [(: % (| WheelReady EnvFailure))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker の準備の process と同じ並び(本物の答え手 + 翻訳 env-translation — worker/entry/env_tool)で wheel を用意する(--out-dir は
   state の隣の worker-root の下 — 準備の stage-native と同じ wheel-out-dir・uv-cache = worker の uv の cache の dir — 起動の script の
   DOEFF_UV_CACHE_DIR)。"
  (val out-dir (native-wheel.wheel-out-dir (str (/ state-dir.parent "worker-root")) native-wheel.DOEFF-VM-PACKAGE))
  (val settings {"runtime-env.state" (str state-dir) "runtime-env.uv-cache" (str uv-cache) "runtime-env.repo-keys" {} "runtime-env.code-prepare" ""
                 "runtime-env.uv" (str uv) "runtime-env.progress" "" "runtime-env.notes" "/dev/null"})
  (<- ready (| WheelReady EnvFailure)
      (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                     (ensure-wheel source-dir out-dir)))
  ready)


(defk out-path [root stdout]
  {:pre [(: root Path) (: stdout str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "起動の入口の答えの 1 行(「<組んだ|使った|…> <path>」)の path が root の下の --out-dir(<root>/.native-wheels/doeff-vm/)に出た wheel の
   file である事を確かめて返すため。"
  (val path (get (.split (.strip stdout) " " 1) -1))
  (assert (= path (os.path.join (native-wheel.wheel-out-dir (str root) native-wheel.DOEFF-VM-PACKAGE) WHEEL-NAME)) path)
  (assert (os.path.isfile path) path)
  path)


;; --- 1 口の報告の読み ------------------------------------------------------------------------------

(deftest test-the-report-names-whether-the-backend-built-the-package
  (val line (fn [project wheel built] (+ "{\"project\": \"" project "\", \"wheel\": \"" wheel "\", \"built\": " built "}\n")))
  (val text (+ (line "doeff-vm" "/s/a.whl" "true") (line "other" "/s/o.whl" "true") (line "doeff-vm" "/s/b.whl" "false")))
  (assert (is (native-wheel.reported-built text "doeff-vm") False))
  ;; 反例: 行が無い報告(報告を書かない版の口・他の package の行だけ)は「報告が無い」— 組んだ扱いにも使った扱いにもしない。
  (assert (= (native-wheel.reported-built "" "doeff-vm") (native-wheel.NotReported)))
  (assert (= (native-wheel.reported-built (line "other" "/s/o.whl" "true") "doeff-vm") (native-wheel.NotReported)))
  ;; 反例: JSON でない・形が違う行は理由の文(報告を書く版の口の約束の破れ)。
  (assert (isinstance (native-wheel.reported-built "{not json\n" "doeff-vm") str))
  (assert (isinstance (native-wheel.reported-built "{\"project\": \"doeff-vm\", \"wheel\": 1, \"built\": true}\n" "doeff-vm") str)))


;; --- 2・3 保存先は build の口の 1 つ -------------------------------------------------------------------

(deftest test-the-worker-uses-the-wheel-the-boot-had-the-backend-store [tmp-path monkeypatch]
  (<- made tuple (doeff-source tmp-path))
  (val src (get made 0))
  (<- uv Path (fake-uv tmp-path))
  (val state-dir (/ tmp-path "state"))
  (monkeypatch.setenv "FAKE_UV_LOG" (str (/ tmp-path "uv.log")))
  ;; 起こす入口は checkout の中に bytecode を書かない(検の後の検めが checkout の .pyc を赤にする)。
  (monkeypatch.setenv "PYTHONDONTWRITEBYTECODE" "1")
  (val booted (subprocess.run [sys.executable "-m" "doeff_cluster.worker.entry.boot_wheel" "--root" (str src)
                               "--state" (str state-dir) "--uv-cache" (str (/ state-dir "uv-cache")) "--uv" (str uv)]
                              :capture-output True :text True :timeout 60))
  (assert (= booted.returncode 0) booted.stderr)
  (assert (.startswith booted.stdout "組んだ ") booted.stdout)
  (<- (out-path src booted.stdout))
  ;; worker は同じ口の保存先の wheel を受け(口は組まない — 自前の鍵で別の置き場を引かない)、--out-dir に出た file を入れる。
  (val source-dir (str (/ src native-wheel.DOEFF-VM-SOURCE)))
  (val uv-cache (/ state-dir "uv-cache"))
  (<- ready (| WheelReady EnvFailure) (worker-wheel state-dir uv-cache uv source-dir))
  (assert (and (isinstance ready WheelReady) (= ready.origin WheelOrigin.STORED) (os.path.isfile ready.path)) ready)
  ;; source の中身を変えると、口が 1 度だけ組む(次は使う)。
  (.write-text (/ src native-wheel.DOEFF-VM-SOURCE "src.rs") "// changed\n" :encoding "utf-8")
  (<- changed (| WheelReady EnvFailure) (worker-wheel state-dir uv-cache uv source-dir))
  (assert (and (isinstance changed WheelReady) (= changed.origin WheelOrigin.BUILT)) changed)
  (<- again (| WheelReady EnvFailure) (worker-wheel state-dir uv-cache uv source-dir))
  (assert (and (isinstance again WheelReady) (= again.origin WheelOrigin.STORED) (os.path.isfile again.path)) again)
  (<- log tuple (uv-log tmp-path))
  (assert (= (len (lfor line log :if (.startswith line "build") line)) 4) log)
  ;; 報告の file は読んだら消す(--out-dir の隣に残さない)。
  (val outs (. (Path again.path) parent parent))
  (assert (= (lfor e (.iterdir outs) :if (.endswith e.name ".jsonl") e.name) []) (list (.iterdir outs))))


(deftest test-a-backend-without-the-report-still-gives-its-wheel-with-an-unreported-origin [tmp-path monkeypatch]
  ;; 反例: 報告を書かない版の口(宣言の古い doeff の root — 本番の job は古い版を宣言している物が在る)で用意しても、uv build が --out-dir に
  ;; 出した wheel を受け、組んだかは閉じた型の「報告が無い」になる(native-build-failed で止まらない・組んだ / 使ったのどちらにも埋めない)。
  (<- made tuple (doeff-source tmp-path))
  (val src (get made 0))
  (<- uv Path (fake-uv tmp-path))
  (monkeypatch.setenv "FAKE_UV_LOG" (str (/ tmp-path "uv.log")))
  (monkeypatch.setenv "FAKE_UV_OLD_BACKEND" "1")
  (<- ready (| WheelReady EnvFailure) (worker-wheel (/ tmp-path "state") (/ tmp-path "uv-cache") uv (str (/ src native-wheel.DOEFF-VM-SOURCE))))
  (assert (isinstance ready WheelReady) ready)
  (assert (and (.endswith ready.path WHEEL-NAME) (os.path.isfile ready.path)) ready)
  (assert (= ready.origin WheelOrigin.UNREPORTED) ready))


(deftest test-the-boot-entry-takes-no-tree-hash-arguments [tmp-path]
  ;; 反例: 起動の入口は --mirror・--commit(git の tree hash を読む材料)を受けない — 自前の鍵の道が残れば受けて赤。
  (val booted (subprocess.run [sys.executable "-m" "doeff_cluster.worker.entry.boot_wheel" "--root" (str tmp-path)
                               "--mirror" (str tmp-path) "--commit" "HEAD" "--state" (str (/ tmp-path "state"))
                               "--uv-cache" (str (/ tmp-path "uv-cache"))]
                              :capture-output True :text True :timeout 60 :env (| (dict os.environ) {"PYTHONDONTWRITEBYTECODE" "1"})))
  (assert (= booted.returncode 2) booted.stderr)
  (assert (in "unrecognized arguments" booted.stderr) booted.stderr))


;; --- 4 boot.sh の 2 回 --------------------------------------------------------------------------

(defk boot-once [tmp sha [role "records"] * [writes-bytecode False] [given #()]]
  {:pre [(: tmp Path) (: sha str) (: role str) (: writes-bytecode bool) (: given tuple)] :post [(: % subprocess.CompletedProcess)]}
  "image の起動の script を role の役(既定 records)で起こす(root の準備の後は、root の venv の偽の hy が役の起動を断るので落ちる)。
   writes-bytecode = 呼び手の PYTHONDONTWRITEBYTECODE を空にする(本番の Pod と同じ — 準備の間の Python に立てるのは boot.sh の受け持ち)。
   given = 呼び手が足す環境変数の (名 値) の組の列(配備の env や、件をまたいで同じ dir を渡すテストの呼び手が置く値)。"
  (subprocess.run ["sh" BOOT-SH]
                  :env (| (dict given) {"PATH" (+ (str (/ tmp "bin")) ":/usr/bin:/bin") "HOME" (str tmp) "ROLE" role
                        "WORK_DIR" (str (/ tmp "work")) "WORKER_DOEFF_COMMIT" sha "WORKER_DOEFF_URL" (str (/ tmp "doeff"))
                        "FAKE_UV_LOG" (str (/ tmp "uv.log")) "FAKE_HY_LOG" (str (/ tmp "hy.log"))
                        "FAKE_UV_PYTHON" sys.executable "PYTHONDONTWRITEBYTECODE" (if writes-bytecode "" "1")
                        ;; 呼び手の venv(uv build の子へ継がせない物)
                        "VIRTUAL_ENV" (str (/ tmp "caller-venv"))})
                  :capture-output True :text True :timeout 60))


(deftest test-boot-sh-builds-the-wheel-once-and-reuses-it [tmp-path]
  (<- made tuple (doeff-source tmp-path))
  (val sha (get made 2))
  (<- (fake-uv tmp-path))
  (val state-dir (str (/ tmp-path "work" "state")))
  (val python (str (/ tmp-path "work" "boot" "roots" sha ".venv" "bin" "python")))
  (<- first subprocess.CompletedProcess (boot-once tmp-path sha))
  (assert (in "root を準備した" first.stderr) first.stderr)
  (assert (in "組んだ" first.stderr) first.stderr)
  ;; 焼く道具の無い root(この検の doeff の root)は焼かずに注記だけで準備を終える。
  (assert (in "bytecode を焼かない" first.stderr) first.stderr)
  (<- once tuple (uv-log tmp-path))
  (val syncs (lfor line once :if (.startswith line "sync") line))
  (assert (and (= (len syncs) 1) (in "--no-install-package doeff-vm" (get syncs 0))) once)
  (val builds (lfor line once :if (.startswith line "build") line))
  (assert (= (len builds) 1) #(once first.stderr))
  (assert (in (.format " venv= cache={}/uv-cache from=1" state-dir) (get builds 0)) builds)
  (val pips (lfor line once :if (.startswith line "pip") line))
  (val wheel (os.path.join (native-wheel.wheel-out-dir (str (/ tmp-path "work" "boot" "roots" sha)) native-wheel.DOEFF-VM-PACKAGE) WHEEL-NAME))
  (assert (= pips [(.format "pip install --no-deps --python {} {} venv={} cache={}/uv-cache from=1"
                            python wheel (/ tmp-path "caller-venv") state-dir)])
          once)
  ;; 2 回目: root だけを消す(同じ node の次の Pod が別の commit の root を持つ時と同じ)— 口が保存先の wheel を使い、組まない。
  (shutil.rmtree (/ tmp-path "work" "boot" "roots" sha))
  (<- second subprocess.CompletedProcess (boot-once tmp-path sha))
  (assert (in "root を準備した" second.stderr) second.stderr)
  (assert (in "使った" second.stderr) second.stderr)
  (<- twice tuple (uv-log tmp-path))
  (val second-pips (lfor line twice :if (.startswith line "pip") line))
  (assert (= (len second-pips) 2) twice)
  (assert (= (get second-pips 0) (get second-pips 1)) "2 回目も root の下の --out-dir に出た wheel を入れる")
  (assert (= (len (lfor line twice :if (.startswith line "build") line)) 2) twice))


;; --- 11 uv の cache の dir は呼び手が渡せる -------------------------------------------------------------

(deftest test-the-uv-cache-given-to-the-worker-reaches-its-uv-children [tmp-path monkeypatch]
  ;; worker の準備の process(本物の翻訳 env-translation)が起こす uv の子は、設定 runtime-env.uv-cache の dir を UV_CACHE_DIR として
  ;; 継ぐ(state の下に固定しない — 手元の 1 台の cluster を件ごとに起こすテストは、件をまたいで同じ cache を渡して依存を取り直さない)。
  (<- made tuple (doeff-source tmp-path))
  (val src (get made 0))
  (<- uv Path (fake-uv tmp-path))
  (val shared (/ tmp-path "shared-uv-cache"))
  (monkeypatch.setenv "FAKE_UV_LOG" (str (/ tmp-path "uv.log")))
  (<- ready (| WheelReady EnvFailure) (worker-wheel (/ tmp-path "state") shared uv (str (/ src native-wheel.DOEFF-VM-SOURCE))))
  (assert (isinstance ready WheelReady) ready)
  (<- log tuple (uv-log tmp-path))
  (val builds (lfor line log :if (.startswith line "build") line))
  (assert (= (len builds) 1) log)
  (assert (in (.format " cache={} " shared) (get builds 0)) builds))


(deftest test-boot-sh-gives-the-uv-cache-of-the-caller-to-its-uv-children [tmp-path]
  ;; 起動の script は呼び手が置いた DOEFF_UV_CACHE_DIR を root の準備の uv の子(sync・build・pip)の UV_CACHE_DIR にする(置かなければ
  ;; 既定 $WORK_DIR/state/uv-cache — 上の 4 の検)。
  (<- made tuple (doeff-source tmp-path))
  (<- (fake-uv tmp-path))
  (val shared (/ tmp-path "shared-uv-cache"))
  (<- done subprocess.CompletedProcess (boot-once tmp-path (get made 2) :given #(#("DOEFF_UV_CACHE_DIR" (str shared)))))
  (assert (in "root を準備した" done.stderr) done.stderr)
  (<- log tuple (uv-log tmp-path))
  (val children (lfor line log :if (.startswith line #("sync" "build" "pip")) line))
  (assert (= (len children) 3) log)
  (assert (all (gfor line children (in (.format " cache={} " shared) line))) children))


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


;; --- 8・9・10 起動の root の bytecode -----------------------------------------------------------------

(deftest test-boot-sh-bakes-the-entry-closure-of-the-new-root [tmp-path]
  (<- made tuple (doeff-source tmp-path :bake True))
  (val sha (get made 2))
  (<- (fake-uv tmp-path))
  ;; 呼び手は PYTHONDONTWRITEBYTECODE を立てない(本番の Pod と同じ)— uv の焼きの子が .pth の module を timestamp の方式で先に書くと、
  ;; 焼く道具がその .py を焼く物から外す(下の flags の断言が赤になる)。
  (<- done subprocess.CompletedProcess (boot-once tmp-path sha :writes-bytecode True))
  (<- log tuple (uv-log tmp-path))
  (val syncs (lfor line log :if (.startswith line "sync") line))
  (assert (and (= (len syncs) 1) (in "--compile-bytecode" (get syncs 0))) #("uv sync が site-packages の .pyc を焼かない" log))
  (assert (= (len log) 3) #("焼きは uv を通さない(sync・build・pip の 3 回だけ)" log))
  (val root (/ tmp-path "work" "boot" "roots" sha))
  (for [rel BAKED]
    (<- pyc Path (pyc-of root rel))
    (assert (.is-file pyc) #("入口の閉包の module が焼かれていない" rel done.stderr))
    (<- flags int (pyc-flags pyc))
    (assert (= flags CHECKED-HASH) #("import の時に source の hash を検める方式でない" rel flags)))
  (<- outside Path (pyc-of root OUTSIDE-CLOSURE))
  (assert (not (.exists outside)) "閉包の外の module まで焼いた")
  (assert (.is-file (/ root MARKER)) #("焼く道具の完成の印が無い" done.stderr))
  (<- line str (prepared-line done.stderr))
  (assert (in "rebuilt=" line) #("準備の行に焼いた数が無い" done.stderr))
  ;; 焼く道具は 1 回だけ起こし、根は venv の .pth が書く root の中の dir だけ(root そのものは `.`)— import の行と root の外の dir を
  ;; 根と読まない。末尾に改行の無い .pth の行も読む。
  (<- calls tuple (hy-log tmp-path))
  (val bakes (lfor c calls :if (in "code_prepare.hy" c) c))
  (assert (= (len bakes) 1) calls)
  (<- roots (| str None) (argument-of (get bakes 0) "--roots"))
  (assert (= (frozenset (.split (or roots "") ",")) (frozenset #("." "packages/doeff-cluster/src"))) #("焼く根の読みが違う" roots))
  (<- entries (| str None) (argument-of (get bakes 0) "--entries"))
  (assert (is-not entries None) bakes)
  (<- revision (| str None) (argument-of (get bakes 0) "--revision"))
  (assert (= revision sha) bakes))


(deftest test-boot-sh-writes-the-unchanged-bytecode-of-the-next-root-from-the-store [tmp-path]
  ;; 失敗ケース(#3858): 1 file だけ変えた次の版の root は、変わっていない file の .pyc を source の中身で引く保存先
  ;; ($WORK_DIR/state/doeff-hy-code-store — 起動の script の既定)から書き(stored)、変えた file だけを焼く。前の root の .pyc を hardlink で
  ;; 引き継がない(前の root が消えた・別の /work でも同じに効く)。
  (<- made tuple (doeff-source tmp-path :bake True))
  (val src (get made 0))
  (val sha1 (get made 2))
  (<- (fake-uv tmp-path))
  (<- first subprocess.CompletedProcess (boot-once tmp-path sha1))
  (<- first-line str (prepared-line first.stderr))
  (assert (in "rebuilt=" first-line) first.stderr)
  (assert (.is-dir (/ tmp-path "work" "state" "doeff-hy-code-store")) #("起動の script の既定の保存先に code が足されていない" first.stderr))
  (val changed-rel "worker/entry/shim.py")
  (val changed-text "V = 2\n")
  (.write-text (/ src ROOT-CLUSTER changed-rel) changed-text :encoding "utf-8")
  (<- (git src "commit" "-q" "-a" "-m" "1 file を変える"))
  (<- sha2 str (git src "rev-parse" "HEAD"))
  (<- second subprocess.CompletedProcess (boot-once tmp-path sha2))
  (val root1 (/ tmp-path "work" "boot" "roots" sha1))
  (val root2 (/ tmp-path "work" "boot" "roots" sha2))
  (for [rel (gfor r BAKED :if (!= r changed-rel) r)]
    (<- old Path (pyc-of root1 rel))
    (<- new Path (pyc-of root2 rel))
    (assert (.is-file new) #("変えていない file の .pyc が無い" rel second.stderr))
    (assert (not (os.path.samefile old new)) #("前の root の .pyc を hardlink で引き継いだ" rel)))
  (<- new-changed Path (pyc-of root2 changed-rel))
  (assert (.is-file new-changed) #("変えた file が焼かれていない" second.stderr))
  (assert (= (cut (.read-bytes new-changed) 8 16) (importlib.util.source-hash (.encode changed-text "utf-8")))
          "変えた file の .pyc の頭の hash が新しい source と合わない")
  (<- line str (prepared-line second.stderr))
  (val stored (re.search r"stored=(\d+)" line))
  (assert (and (is-not stored None) (>= (int (.group stored 1)) 1)) #("変えていない file を保存先から書いていない" line)))


(deftest test-the-boot-entries-name-every-module-the-root-venv-starts []
  (val text (.read-text (Path BOOT-SH) :encoding "utf-8"))
  (val listed (re.search r"(?m)^BOOT_ENTRIES=(\S+)$" text))
  (assert (is-not listed None) "boot.sh の頭に BOOT_ENTRIES が無い")
  (val entries (frozenset (.split (.group listed 1) ",")))
  ;; boot.sh が root の venv の hy で起こす役の module の全部(`hy -m <module>`)と、worker が同じ venv で起こす入口。
  (val started (frozenset (re.findall r"\bhy -m (doeff_cluster(?:\.\w+)+)" text)))
  (assert (>= (len started) 4) started)
  (<- shim tuple (shim-argv "python" 1000 :stamp-lines False :notice-env None))
  ;; workspace の package の .pth(packages/<名>/src/*.pth — site-packages へ入る)の import の行が起こす module。
  (val pth-imports (frozenset (gfor pth (.glob PACKAGES "*/src/*.pth")
                                    line (.splitlines (.read-text pth :encoding "utf-8"))
                                    :setv found (re.match r"import\s+([\w.]+)" line)
                                    :if (is-not found None)
                                    (.group found 1))))
  (assert (in "doeff_hy_bytecode_guard" pth-imports) pth-imports)
  (val wanted (| started pth-imports (frozenset #(ENV-TOOL JOB-ENTRY (get shim 3)))))
  (assert (<= wanted entries) #("BOOT_ENTRIES に無い入口" (sorted (- wanted entries))))
  (val marker (re.search r"(?m)^CODE_MARKER=(\S+)$" text))
  (assert (and (is-not marker None) (= (.group marker 1) MARKER)) #("boot.sh の焼く道具の印の名が code_plan の MARKER と違う" MARKER)))
