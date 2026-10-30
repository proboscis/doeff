;;; worker が job の子 process を起こす形の判断 — 命令の並び(shim の下の hy -m — 木の job は worker の hy・実行環境の job は root の venv の hy)・cwd・子の環境変数・
;;; 実行環境の job の空の作業 dir と使った印(handlers.hy の ProcessHost.launch から分けた・#2464)。I/O は呼び手(ProcessHost — 後に
;;; worker/protocol の言い換え)が行い、ここは渡された値だけから決める。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import collections.abc [Mapping])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import posixpath)
(import pathlib [Path])
(import doeff_core_effects [EnvEntry EnvMode])
(import doeff_cluster.shared.core.run_context_rules [process-context-environ])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.runtime_env_model [WITHHELD-ENV-NAMES])
(import doeff_cluster._worker.intent.worker_model [CodeLayout])


;; 子 process へ継ぐ worker の環境変数の許可表(実行環境の job — 2026-09-26)。これ以外(PYTHON*・HY_*・UV_* の他・LD_*・VIRTUAL_ENV・
;; 資格を運ぶ変数)は継がない。worker の build の保存先(Rust の wheel の DOEFF_WHEEL_CACHE・Hy の bytecode の DOEFF_HY_CODE_STORE — 値は
;; 起動の script の 1 か所)と cargo / rustup の場所は継ぐ: 子の uv sync が worker と同じ wheel と bytecode を使い、doeff-vm を組み直さない
;; (#3972 — 継がなかった時、job の子の uv sync が 5 分半)。DOEFF_ の名は宣言の env-vars に書けない(予約の接頭辞)ので worker が継ぐ。宣言の env-vars と worker が組む DOEFF_WORKER_*・DOEFF_RUNTIME_ENV* を足す。LC_* は名を前もって知らない
;; 一族なので頭で拾う(CHILD-ENV-PREFIXES — ReadEnvironment の prefixes に渡せる形)。
;; job の log の送り先(DOEFF_JOB_LOG_OTLP)と配備が足す属性(DOEFF_JOB_LOG_ATTRIBUTES)も継ぐ: 子の shim と待ちの子から分かれた子 A の
;; 刻つきの書き手が、worker の環境に在る時だけ job の log の行を OTLP でも送る(card ki-ae919abe5b26 — 名の読み手は
;; worker/entry/line_stamp の JOB_LOG_ENV_NAMES。木の job は worker の環境を EXTEND で継ぐので、この表は実行環境の job と待ちの子の分)。
(setv CHILD-ENV-ALLOWED (frozenset #("PATH" "HOME" "USER" "LOGNAME" "SHELL" "LANG" "LANGUAGE" "TZ" "TMPDIR" "TERM"
                                     "SSL_CERT_FILE" "SSL_CERT_DIR" "UV_CACHE_DIR" "UV_PYTHON_INSTALL_DIR"
                                     "DOEFF_WHEEL_CACHE" "DOEFF_HY_CODE_STORE" "CARGO_HOME" "RUSTUP_HOME"
                                     "DOEFF_JOB_LOG_OTLP" "DOEFF_JOB_LOG_ATTRIBUTES")))
(setv CHILD-ENV-PREFIXES #("LC_"))


(defk child-environment [base extra declared worker]
  {:pre [(: base (get dict #(str str))) (: extra (get dict #(str str))) (: declared (get dict #(str str))) (: worker (get dict #(str str)))]
   :post [(: % (get dict #(str str)))] :tags {:context "worker" :role "judgment"}}
  "実行環境の job の子の環境変数を組むため: base(worker の環境)のうち許可表の物と LC_* だけ → worker の文脈(extra)→ 宣言の env-vars(declared)
   → worker が組む DOEFF_*(worker)の順に重ねる。PYTHONPATH は置かない(import の解け先は root の venv と .pth だけ)。重ねた後に、どの層から
   来た物でも WITHHELD-ENV-NAMES の名を外す(許可表・--pass-env・宣言のどれを広げても子へ届かない)。"
  (val merged (| (dfor #(k v) (.items base) :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))) k v)
                 extra declared worker))
  (dfor #(k v) (.items merged) :if (not-in k WITHHELD-ENV-NAMES) k v))


(defk env-project-dir [root declared]
  {:pre [(: root str) (: declared dict)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "root と宣言の JSON から、uv の --project に渡す project の dir を決めるため(env_prepare.project-dir と同じ規則)。"
  (val project (get declared "project"))
  (if (= (get project "path") ".")
      (.format "{}/{}" root (get project "repo"))
      (.format "{}/{}/{}" root (get project "repo") (get project "path"))))


(defk project-venv [project-dir]
  {:pre [(: project-dir str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "実行環境の root の project の venv の path を 1 か所で決めるため: <project-dir>/.venv — uv が --project の path を symlink を解かずに
   字面で正規化して置く所と同じ(uv sync が作る — tests/test_task_venv_on_both_paths.hy が本物の uv で確かめる)。"
  (posixpath.join (posixpath.normpath project-dir) ".venv"))


(defk project-hy [project-dir]
  {:pre [(: project-dir str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "実行環境の job と入口の検めの本体を起こす hy(root の venv の bin/hy — uv sync が置く入口の script)を決めるため。uv run を挟まずに
   この path を直に起こす(card ki-869cdf21ec85 — uv run は子を spawn して待つだけで 1 本 6 MB を持っていた)。"
  (<- venv str (project-venv project-dir))
  (posixpath.join venv "bin" "hy"))


(defk uv-run-environ [project-dir env]
  {:pre [(: project-dir str) (: env (get dict #(str str)))] :post [(: % (get dict #(str str)))] :tags {:context "worker" :role "judgment"}}
  "`uv run --project <project-dir>` が子へ渡していた環境を、渡した env から導くため(card ki-b884608b96e4): project の venv
   (project-venv)の bin を PATH の頭に足し(PATH が無ければ bin だけ)、VIRTUAL_ENV にその venv を置く。実行環境の job は shim の下の
   道も待ちの子から分ける道も uv を通らない(card ki-869cdf21ec85 — shim の下は root の venv の hy を直に起こす)ので、job-launch と
   probe-command がこの関数で 2 つを足した環境を子に置く — 名で hy・python を起こす task が、どの道でも同じ root の venv を引く。
   uv の足し方と字面が合う事は tests/test_task_venv_on_both_paths.hy が本物の uv で確かめる。"
  (<- venv str (project-venv project-dir))
  (val bin (posixpath.join venv "bin"))
  (val path (.get env "PATH" ""))
  (| env {"PATH" (if path (+ bin ":" path) bin) "VIRTUAL_ENV" venv}))


(defn #^ Path program-file [#^ Path program-dir #^ str sha]  ; defk にできない: 検の道具と言い換えの handler が値として呼ぶ
  "詰めた Program の置き場のキー → この worker の cache の file(coordinator への口が取って書き、子へ渡す — 定義点は 1 つ)。"
  (/ program-dir (+ sha ".json")))


(defn #^ str program-file-text [#^ str blob #^ (get Mapping #(str object)) versions]  ; defk にできない: 検の道具と言い換えの handler が値として呼ぶ
  "cache の file の中身(子の入口 job_entry の read-program が読む形 {\"blob\" \"versions\"} — service と task で同じ・定義点は 1 つ)。"
  (json.dumps {"blob" blob "versions" versions}))


(defk spec-program-name [spec]
  {:pre [(: spec JobSpec) (isinstance spec.program str) (isinstance spec.versions tuple)] :post [(: % str)]
   :tags {:context "worker" :role "judgment"}}
  "Program の job 1 本の cache の file の置き方を 1 か所で決めるため(Program の cache の dir からの相対 path — coordinator への口が
   取って書き、task の印 <id>.program に残して掃除に使い、子 process の言い換えが spec-program-file で --program に渡す)。
   service も task も <版の指紋>/<sha>.json — 版は service が宣言の行の版・task が task の行の版(spec.versions)。同じ sha の Program を
   版の違う 2 本の task が使っても file が上書きし合わず(#3762)、同じ sha の Program を別の版で宣言し直した service は別の file を
   取り直す(card ki-172e63fed4c7 — sha だけのキーでは前の版の file を持ち続け、子の入口が版の違いで断り続けた)。file の名は <sha>.json
   のまま(記録係の header が file の名から Program のキーを読む — shared/entry/boundary_recorder)。
   版の指紋 = 名の順の #(名 版) の組の正規 JSON の sha256 の頭 16 桁。"
  (val canonical (json.dumps (lfor #(k v) spec.versions [k v]) :ensure-ascii False :separators #("," ":")))
  (val digest (cut (.hexdigest (hashlib.sha256 (.encode canonical "utf-8"))) 0 16))
  (str (program-file (Path digest) spec.program)))


(defk spec-program-file [program-dir spec]
  {:pre [(: program-dir Path) (: spec JobSpec) (isinstance spec.program str)] :post [(: % Path)] :tags {:context "worker" :role "judgment"}}
  "Program の job 1 本の子へ渡す cache の file の path を返すため(Program の cache の dir program-dir の下の spec-program-name)。"
  (<- name str (spec-program-name spec))
  (/ program-dir name))


(defk shim-argv [python grace-ms * stamp-lines notice-env]
  {:pre [(: python str) (: grace-ms int) (: stamp-lines bool) (: notice-env (| str None))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "worker" :role "judgment"}}
  "job の子と入口の検めを shim の下で起こす命令の頭を 1 つの形にするため(猶予は shim が秒の小数で読む — 値は worker の方針から
   worker/core/shim_timing の shim-spans が導く・#2940)。stamp-lines = 子の出力の 1 行ごとに壁の時計の刻の頭を付けるか(#3714 — job の
   log は人が刻で読むので True・入口の検めは worker が stdout の行を読んで判じるので False)。notice-env = 退きの知らせの pipe の読み口の
   fd の番号を job へ渡す環境変数の名(#3672 — 宿の契約 HOST-CONTRACT の notice-env・呼び手が名を渡す。shim は worker が標準入力へ
   書いた行をその pipe へ中継する)— 入口の検めは知らせを受けないので None。"
  (+ #(python "-B" "-m" "doeff_cluster._worker.entry.shim" (str (/ grace-ms 1000)))
     (if stamp-lines #("--stamp-lines") #())
     (if (is notice-env None) #() #("--notice-env" notice-env))
     #("--")))


(defrecord JobLaunch
  "job の子 process 1 本の起こし方: argv = 命令の並び・cwd = 作業 dir・env と env-mode = 子の環境変数(StartProcess の env と env-mode に
   そのまま渡す — 木の job は EXTEND で worker の環境を継いで上書きの分だけ・実行環境の job は REPLACE で許可表で絞った全部。名の順)・
   work-dir = 起こす前に空にして
   作り直す dir(実行環境の job だけ — 他は None)・last-used = 起こす前に書く使った印の file(実行環境の job だけ — 掃除が最後に使った
   時刻で古い root から消す。env_upkeep.sweep-choice)。"
  (#^ tuple argv)
  (#^ str cwd)
  (#^ (get tuple #(EnvEntry ...)) env)
  (#^ EnvMode env-mode)
  (#^ (| str None) work-dir)
  (#^ (| str None) last-used)
  ;; 入口の module の後ろに並べる引数(job の args と詰めた Program の --program)。待ちの子から分ける task(#3646)は argv を使わず、
  ;; 読み込み済みの入口をこの引数で走らせる(ForkFromWarm の args)— 2 つの道が同じ引数を読む。入口の検めは使わない(空)。
  (setv #^ tuple entry-args #())
  ;; 入口が走る環境(実行環境の job だけ — 名の順): 許可表で組んだ環境に root の venv の 2 つ(PATH の頭・VIRTUAL_ENV —
  ;; uv-run-environ)を足した物で、shim の下の道の env と同じ値。待ちの子から分ける task はこの環境を ForkFromWarm の env に載せ、2 つの道の
  ;; 入口が同じ venv を名で引く(card ki-b884608b96e4)。分ける service も同じ値を使う(card ki-de44efdb9aa4)。木の job と入口の検めは
  ;; 待ちの子から分けないので空。
  (setv #^ (get tuple #(EnvEntry ...)) entry-env #()))


(defk job-launch [spec code-path instance attempt * python hy-command extra-env layout allowed-env worker-pid program-path program-env work-dir
                  shim-grace-ms notice-env]
  {:pre [(: spec JobSpec) (: code-path str) (: instance str) (: attempt int) (: python str) (: hy-command str)
         (: extra-env dict) (: layout CodeLayout) (: allowed-env dict) (: worker-pid int) (: program-path (| str None)) (: program-env str) (: work-dir str)
         (: shim-grace-ms int) (: notice-env str)]
   :post [(: % JobLaunch)] :tags {:context "worker" :role "judgment"}}
  "job の子 process の起こし方を、渡された値だけから決めるため(ProcessHost と、後の言い換えの handler が同じ形で起こす)。
   子の文脈の環境変数は sim の宿(local.run-context-of)と同じ関数 process-context-environ で作る(実行環境の job だけが DOEFF_RUNTIME_ENV・
   DOEFF_RUNTIME_ENV_KEY を受ける)。Program の job(改訂 1 の F・H)は詰めた Program の file(program-path)を引数と環境変数(宿の契約
   HOST-CONTRACT の program-env — 呼び手が名を渡す。core は foundation の宿の契約を読まない)で渡す。実行環境の job は root の venv の hy を
   uv run を挟まずに直に起こす(PYTHONPATH を置かない・子の環境変数は許可表で組み、root の venv の 2 つ(uv-run-environ)を足す・
   cwd = work-dir・待ちの子から分ける道の環境も同じ entry-env — card ki-869cdf21ec85)、
   それ以外は木の PYTHONPATH(layout)を足して worker の環境を継ぐ(EXTEND)。allowed-env = worker の環境のうち許可表の名と LC_* の分
   (実行環境の job だけが読む — 読むのは呼び手: ReadEnvironment の names = CHILD-ENV-ALLOWED・prefixes = CHILD-ENV-PREFIXES)。
   shim-grace-ms = shim の猶予(worker の方針から shim_timing.shim-spans が導いた値 — 呼び手が渡す)。notice-env = 退きの知らせの pipe の
   fd の番号を子へ渡す環境変数の名(宿の契約 HOST-CONTRACT の notice-env — 呼び手が名を渡す・shim の旗 --notice-env・#3672)。"
  (<- context dict (process-context-environ spec instance attempt))
  (val worker-env (| context {"DOEFF_WORKER_PID" (str worker-pid)}
                     (if program-path {program-env program-path} {})))
  (val program-args (if program-path #("--program" program-path) #()))
  (val environ (dict spec.environ))
  ;; job の log は 1 行ごとに刻を付ける(待ちの子から分ける task の log も、分かれた子 A が同じ部品で付ける — worker/entry/warm_child)。
  ;; job は退きの知らせの pipe を受ける(shim が worker の標準入力の行を中継する — #3672)。
  (<- shim (get tuple #(str ...)) (shim-argv python shim-grace-ms :stamp-lines True :notice-env notice-env))
  (if spec.runtime-env
      (do (val declared (json.loads spec.runtime-env))
          (<- child-env dict (child-environment allowed-env extra-env (| (dfor v (.get declared "envVars" []) (get v "name") (get v "value")) environ)
                                                worker-env))
          (<- project-dir str (env-project-dir code-path declared))
          (<- entry-env dict (uv-run-environ project-dir child-env))
          (<- root-hy str (project-hy project-dir))
          (val entry-entries (tuple (gfor k (sorted entry-env) (EnvEntry :name k :value (get entry-env k)))))
          (JobLaunch :argv (+ shim #(root-hy "-m" spec.entry) spec.args program-args)
                     :cwd work-dir
                     :env entry-entries
                     :env-mode EnvMode.REPLACE
                     :work-dir work-dir
                     :last-used (+ code-path "/.last-used")
                     :entry-args (+ spec.args program-args)
                     :entry-env entry-entries))
      ;; 木の job も、足す環境変数から WITHHELD-ENV-NAMES の名を外す。EXTEND で継ぐ worker の環境そのものからは外せない(StartProcess に名を
      ;; 落とす口が無い)— worker の環境に置かない事は雛形(deploy/)の側が守る。
      (do (val tree-env (dfor #(k v) (.items (| extra-env environ {"PYTHONPATH" (.pythonpath layout code-path)} worker-env))
                              :if (not-in k WITHHELD-ENV-NAMES) k v))
          (JobLaunch :argv (+ shim #(hy-command "-m" spec.entry) spec.args program-args)
                     :cwd code-path
                     :env (tuple (gfor k (sorted tree-env) (EnvEntry :name k :value (get tree-env k))))
                     :env-mode EnvMode.EXTEND
                     :work-dir None
                     :last-used None
                     :entry-args (+ spec.args program-args)))))
