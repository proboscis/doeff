;;; worker の設定の file(Pod を作り直さずに ConfigMap の volume mount で渡す `名=値` の file)の読みと判断 — I/O は呼び手
;;; (worker/protocol/process_host と入口 worker/entry/main)が行う。
;;;
;;; 訳: ConfigMap を envFrom で読むと、Pod の起動の時に値が固定され、後から足した設定や替えた値は Pod を作り直すまで効かない。
;;; subPath の無い ConfigMap の volume mount は kubelet が Pod を作り直さずに中身を入れ替えるので、worker が task の回収と起動の
;;; 時に設定の file を読み直し、container の env に重ねて解決する(まず task の終わりの行の送り先 DOEFF_WORKER_TASK_RECORD_SERVICE・
;;; DOEFF_JOB_LOG_OTLP・DOEFF_JOB_LOG_ATTRIBUTES)。
;;;
;;; 優先の順: 設定の file > container の env。
;;;   - file に在る key は container の env より勝つ(空値 `KEY=` は env の値を明示的に無効化する)。
;;;   - file に無い key と file 不在時は container の env を使う。
;;;   - file の書式: kustomize の env file と同じ(1 行に `名=値`・値は行末までで引用符は外さない・空行と `#` で始まる行は飛ばす)。
;;;   - 崩れた行(`=` が無い・名が空)は行番号だけの訳の文を返し、行の中身や値は文に出さない。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import posixpath)
(import urllib.parse [urlsplit])
(import doeff_cluster._worker.core.task_record [TaskRecordTarget task-record-target])


;; 既定の設定の file の名(WORKER_SETTINGS_FILE / --settings-file が空で WORKER_ROOM_FILE が在る時、同じ dir のこの file を読む)。
(val SETTINGS-FILE-NAME "worker-settings.env")
;; task の終わりの行(task 1 本ごとの CPU 秒と memory の山)の設定の env 名(worker/core/task_record)。
(val TASK-RECORD-SERVICE-VAR "DOEFF_WORKER_TASK_RECORD_SERVICE")
(val JOB-LOG-OTLP-VAR "DOEFF_JOB_LOG_OTLP")
(val JOB-LOG-ATTRIBUTES-VAR "DOEFF_JOB_LOG_ATTRIBUTES")
(val LOGS-PATH "/v1/logs")
;; task の終わりの行の送り先を決める env の名の組(入口 main が container の env から集めて宿へ渡す)。
(val TASK-RECORD-ENV-NAMES #(TASK-RECORD-SERVICE-VAR JOB-LOG-OTLP-VAR JOB-LOG-ATTRIBUTES-VAR))


(defk default-settings-file [settings-file room-file]
  {:pre [(: settings-file (| str None)) (: room-file (| str None))] :post [(: % (| str None))]
   :tags {:context "worker" :role "judgment"}}
  "worker が読む設定の file の path を決めるため: --settings-file が在ればそれ、空で --room-file が在れば同じ dir の
   worker-settings.env(既に subPath 無しで mount 済みの ConfigMap volume を使う)、どちらも空なら None(読まない)。"
  (val explicit (.strip (or settings-file "")))
  (when explicit
    (return explicit))
  (val room (.strip (or room-file "")))
  (when room
    (return (posixpath.join (posixpath.dirname room) SETTINGS-FILE-NAME)))
  None)


(defk parse-env-file [text]
  {:pre [(: text str)] :post [(: % (| dict str))] :tags {:context "worker" :role "judgment"}}
  "kustomize の env file 形式(1 行 `名=値`・空行と `#` 行は無視・値の引用符は外さない)を名 → 値の dict に読むため。
   `=` の無い行・名の空の行は行番号だけを名指す訳の文を返す(行の中身や値は文に出さない)。後の同じ key は前の行を上書きする。"
  (val out {})
  (for [#(index raw) (enumerate (.splitlines text) 1)]
    (val stripped (.strip raw))
    (when (or (not stripped) (.startswith stripped "#"))
      (continue))
    (setv #(key sep value) (.partition raw "="))
    (val name (.strip key))
    (when (or (not sep) (not name))
      (return (.format "設定の file の {} 行目が `名=値` の形でない" index)))
    (setv (get out name) value))
  out)


(defk merged-worker-settings [base-env file-values]
  {:pre [(: base-env dict) (: file-values dict)] :post [(: % dict)] :tags {:context "worker" :role "judgment"}}
  "container の env(base-env)の上に設定の file の値(file-values)を重ねるため(優先の順: file > env)。
   file に在る key は空値を含めて env を上書きし、file に無い key は env の値を保つ。"
  (| base-env file-values))


(defk settings-endpoint-usable [endpoint]
  {:pre [(: endpoint str)] :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "送り先の URL が POST できる形(http か https・host の在る絶対の URL・port が読める)かを判じるため。"
  (try
    (val parts (urlsplit endpoint))
    (val port parts.port)
    (and (in parts.scheme #("http" "https")) (bool parts.hostname) (or (is port None) (> port 0)))
    (except [ValueError]
      False)))


(defk settings-extra-attributes [text]
  {:pre [(: text str)] :post [(: % (| (get tuple #((get tuple #(str str)) ...)) str))] :tags {:context "worker" :role "judgment"}}
  "DOEFF_JOB_LOG_ATTRIBUTES(`名=値` を `,` で並べる)を resource 属性の #(名 値) の並びにするため。
   空の項は飛ばす。`=` の無い項・名の空の項は何番目の項かを名指す訳の文を返す(値は文に出さない)。"
  (val items (tuple (gfor item (.split text ",") :if (.strip item) (.strip item))))
  (for [#(index item) (enumerate items 1)]
    (setv #(key sep _value) (.partition item "="))
    (when (or (not sep) (not (.strip key)))
      (return (.format "{} の {} 番目の項が `名=値` の形でない" JOB-LOG-ATTRIBUTES-VAR index))))
  (tuple (gfor item items #((.strip (get (.partition item "=") 0)) (.strip (get (.partition item "=") 2))))))


(defk resolve-task-record-target [env node]
  {:pre [(: env dict) (: node str)] :post [(: % (| TaskRecordTarget str None))] :tags {:context "worker" :role "judgment"}}
  "設定(file と container の env を重ねた dict)と解決済みの機体の名 node から task の終わりの行の送り先を決めるため:
   service.name(DOEFF_WORKER_TASK_RECORD_SERVICE)が無い・空なら送らない(None)。在るのに DOEFF_JOB_LOG_OTLP が無い・URL の形でない・
   DOEFF_JOB_LOG_ATTRIBUTES の並べ方が崩れていれば訳の文(値は出さない)。使える時は TaskRecordTarget を返す。"
  (val service (.strip (.get env TASK-RECORD-SERVICE-VAR "")))
  (when (not service)
    (return None))
  (val base (.strip (.get env JOB-LOG-OTLP-VAR "")))
  (when (not base)
    (return (.format "{} が在るのに {} が無い" TASK-RECORD-SERVICE-VAR JOB-LOG-OTLP-VAR)))
  (val endpoint (+ (.rstrip base "/") LOGS-PATH))
  (when (not (! (settings-endpoint-usable endpoint)))
    (return (.format "{} が http か https の絶対の URL でない" JOB-LOG-OTLP-VAR)))
  (<- extra (| (get tuple #((get tuple #(str str)) ...)) str) (settings-extra-attributes (.get env JOB-LOG-ATTRIBUTES-VAR "")))
  (when (isinstance extra str)
    (return extra))
  (<- target TaskRecordTarget (task-record-target endpoint extra service node))
  target)
