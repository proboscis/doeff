;;; worker が job の子 process を起こす形の判断 — 命令の並び(shim の下の hy -m か、実行環境の root の venv の uv run)・cwd・子の環境変数・
;;; 実行環境の job の空の作業 dir と使った印(handlers.hy の ProcessHost.launch から分けた・#2464)。I/O は呼び手(ProcessHost — 後に
;;; worker/protocol の言い換え)が行い、ここは渡された値だけから決める。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [dataclass])
(import json)
(import pathlib [Path])
(import doeff_core_effects.process_effects [EnvEntry EnvMode])
(import doeff_cluster.job_context [process-context-environ])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout])


;; 子 process へ継ぐ worker の環境変数の許可表(実行環境の job — 2026-09-26)。これ以外(PYTHON*・HY_*・UV_* の他・LD_*・VIRTUAL_ENV・
;; 資格を運ぶ変数)は継がない。宣言の env-vars と worker が組む DOEFF_WORKER_*・DOEFF_RUNTIME_ENV* を足す。LC_* は名を前もって知らない
;; 一族なので頭で拾う(CHILD-ENV-PREFIXES — ReadEnvironment の prefixes に渡せる形)。
(setv CHILD-ENV-ALLOWED (frozenset #("PATH" "HOME" "USER" "LOGNAME" "SHELL" "LANG" "LANGUAGE" "TZ" "TMPDIR" "TERM"
                                     "SSL_CERT_FILE" "SSL_CERT_DIR" "UV_CACHE_DIR" "UV_PYTHON_INSTALL_DIR")))
(setv CHILD-ENV-PREFIXES #("LC_"))


(defn #^ dict child-environment [#^ dict base #^ dict extra #^ dict declared #^ dict worker]  ; defk にできない: ProcessHost(Program の外の I/O の道具)が呼ぶ
  "実行環境の job の子の環境変数: base(worker の環境)のうち許可表の物と LC_* だけ → worker の文脈(extra)→ 宣言の env-vars(declared)
   → worker が組む DOEFF_*(worker)の順に重ねる。PYTHONPATH は置かない(import の解け先は root の venv と .pth だけ)。"
  (| (dfor #(k v) (.items base) :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))) k v)
     extra declared worker))


(defn #^ str env-project-dir [#^ str root #^ dict declared]  ; defk にできない: ProcessHost・ProbeStore(Program の外の I/O の道具)が呼ぶ
  "root と宣言の JSON → uv の --project に渡す project の dir(env_prepare.project-dir と同じ規則)。"
  (setv project (get declared "project"))
  (if (= (get project "path") ".")
      (.format "{}/{}" root (get project "repo"))
      (.format "{}/{}/{}" root (get project "repo") (get project "path"))))


(defn #^ Path program-file [#^ Path program-dir #^ str sha]  ; defk にできない: 検の道具と言い換えの handler が値として呼ぶ
  "詰めた Program の置き場のキー → この worker の cache の file(coordinator への口が取って書き、子へ渡す — 定義点は 1 つ)。"
  (/ program-dir (+ sha ".json")))


(defn #^ str program-file-text [#^ str blob #^ dict versions]  ; defk にできない: 検の道具と言い換えの handler が値として呼ぶ
  "cache の file の中身(子の入口 job_entry の read-program が読む形 {\"blob\" \"versions\"} — service と task で同じ・定義点は 1 つ)。"
  (json.dumps {"blob" blob "versions" versions}))


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
  (#^ (| str None) last-used))


(defk job-launch [spec code-path instance attempt * python hy-command uv extra-env layout allowed-env worker-pid program-path program-env work-dir]
  {:pre [(: spec JobSpec) (: code-path str) (: instance str) (: attempt int) (: python str) (: hy-command str) (: uv str)
         (: extra-env dict) (: layout CodeLayout) (: allowed-env dict) (: worker-pid int) (: program-path (| str None)) (: program-env str) (: work-dir str)]
   :post [(: % JobLaunch)] :tags {:context "doeff-cluster" :role "judgment"}}
  "job の子 process の起こし方を、渡された値だけから決めるため(ProcessHost と、後の言い換えの handler が同じ形で起こす)。
   子の文脈の環境変数は sim の宿(local.run-context-of)と同じ関数 process-context-environ で作る(実行環境の job だけが DOEFF_RUNTIME_ENV・
   DOEFF_RUNTIME_ENV_KEY を受ける)。Program の job(改訂 1 の F・H)は詰めた Program の file(program-path)を引数と環境変数(宿の契約
   HOST-CONTRACT の program-env — 呼び手が名を渡す。core は foundation の宿の契約を読まない)で渡す。実行環境の job は root の venv の uv run(PYTHONPATH を置かない・子の環境変数は許可表で組む・cwd = work-dir)、
   それ以外は木の PYTHONPATH(layout)を足して worker の環境を継ぐ(EXTEND)。allowed-env = worker の環境のうち許可表の名と LC_* の分
   (実行環境の job だけが読む — 読むのは呼び手: ReadEnvironment の names = CHILD-ENV-ALLOWED・prefixes = CHILD-ENV-PREFIXES)。"
  (<- context dict (process-context-environ spec instance attempt))
  (val worker-env (| context {"DOEFF_WORKER_PID" (str worker-pid)}
                     (if program-path {program-env program-path} {})))
  (val program-args (if program-path #("--program" program-path) #()))
  (val environ (dict spec.environ))
  (val shim #(python "-B" "-m" "doeff_cluster.shim" "10" "--"))
  (if spec.runtime-env
      (do (val declared (json.loads spec.runtime-env))
          (val child-env (child-environment allowed-env extra-env (| (dfor v (.get declared "envVars" []) (get v "name") (get v "value")) environ)
                                      worker-env))
          (JobLaunch :argv (+ shim #(uv "run" "--no-sync" "--frozen" "--project" (env-project-dir code-path declared) "hy" "-m" spec.entry)
                              spec.args program-args)
                     :cwd work-dir
                     :env (tuple (gfor k (sorted child-env) (EnvEntry :name k :value (get child-env k))))
                     :env-mode EnvMode.REPLACE
                     :work-dir work-dir
                     :last-used (+ code-path "/.last-used")))
      (do (val tree-env (| extra-env environ {"PYTHONPATH" (.pythonpath layout code-path)} worker-env))
          (JobLaunch :argv (+ shim #(hy-command "-m" spec.entry) spec.args program-args)
                     :cwd code-path
                     :env (tuple (gfor k (sorted tree-env) (EnvEntry :name k :value (get tree-env k))))
                     :env-mode EnvMode.EXTEND
                     :work-dir None
                     :last-used None))))
