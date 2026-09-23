;;; Claude Code の OpenTelemetry の送り設定を、借りた家の settings.json へ据える綴りと純関数(2026-09-23)。
;;;
;;; 何のために: operator 2026-09-23(会話 c-01M365WH4HZ03GNV2P5YZ5SGZJ)の決定 — 全機体・全アカウントの
;;; Claude Code の通信ごとの数値(キャッシュの読み書き・費用・時間・エラー)を OTel で集め、会社用と
;;; 個人用の 2 つの DB に溜める(card acp:kanban-issue:ki-b47afa9311a6)。人が起こす profile の家は dotfiles の
;;; agentcli.claude_telemetry が配るが、agentd の借りた家 <homes-root>/claude/<account> は sessionhost が
;;; 組むので、ここで同じ env の組を書く。手番の CLI は機体の env を継がない(policy.SPAWN-INHERITED-ENV-KEYS)
;;; ので、容器の env に置いても届かない — 家の settings.json の env が CLI に届く唯一の口。
;;;
;;; 振り分け: 収集係は agora.tenant=personal と名乗った行だけを個人用へ、それ以外を会社用へ入れる。
;;; agentd の env CLAUDE_OTEL_TENANT が personal の機体(pool・個人の Mac)だけが名乗る。会社の口座は
;;; 会社の機体にしか来ないので、会社の行が個人用へ落ちる経路は無い(会社の機体は名乗らない)。
;;; 通信の本文(OTEL_LOG_RAW_API_BODIES)は付けない。
;;;
;;; substrate-clean: 生 IO 禁止。ここは綴りと純関数だけ(読み書きは呼び手の effect)。

(require doeff-hy.macros [defk])

(import json)

;; agentd の env: 送り先(名乗らない機体では何もしない)・所属・機体名。
(setv OTEL-ENDPOINT-ENV "CLAUDE_OTEL_ENDPOINT")
(setv OTEL-TENANT-ENV "CLAUDE_OTEL_TENANT")
(setv OTEL-HOST-ENV "CLAUDE_OTEL_HOST")
(setv OTEL-TENANT-PERSONAL "personal")

;; この module が所有する env の鍵(dotfiles agentcli/claude_telemetry.py MANAGED_KEYS と同じ組)。
(setv OTEL-MANAGED-KEYS
      #("CLAUDE_CODE_ENABLE_TELEMETRY" "OTEL_METRICS_EXPORTER" "OTEL_LOGS_EXPORTER"
        "OTEL_EXPORTER_OTLP_PROTOCOL" "OTEL_EXPORTER_OTLP_ENDPOINT" "OTEL_METRIC_EXPORT_INTERVAL"
        "OTEL_LOGS_EXPORT_INTERVAL" "OTEL_RESOURCE_ATTRIBUTES"))


(defk otel-env [endpoint tenant host]
  {:pre [(: endpoint str) (> (len endpoint) 0) (: tenant str) (: host str)]
   :post [(: % dict)]}
  "借りた家に配る env の組(純関数)。tenant が personal の時だけ agora.tenant を名乗る。"
  (setv attrs [])
  (when (= tenant OTEL-TENANT-PERSONAL)
    (.append attrs f"agora.tenant={OTEL-TENANT-PERSONAL}"))
  (when host
    (.append attrs f"agora.host={host}"))
  (.append attrs "agora.runner=agentd")
  {"CLAUDE_CODE_ENABLE_TELEMETRY" "1"
   "OTEL_METRICS_EXPORTER" "otlp"
   "OTEL_LOGS_EXPORTER" "otlp"
   "OTEL_EXPORTER_OTLP_PROTOCOL" "http/protobuf"
   "OTEL_EXPORTER_OTLP_ENDPOINT" endpoint
   "OTEL_METRIC_EXPORT_INTERVAL" "60000"
   "OTEL_LOGS_EXPORT_INTERVAL" "5000"
   "OTEL_RESOURCE_ATTRIBUTES" (.join "," attrs)})


(defk otel-home-settings [settings-text endpoint tenant host]
  {:pre [(: settings-text (| str None)) (: endpoint str) (> (len endpoint) 0) (: tenant str) (: host str)]
   :post [(: % str)]}
  "借りた家の settings.json の本文に OTel の env を合流させた本文(純関数・冪等)。
   所有する鍵は入れ替え(もう配らない鍵は消す)、他の env と他の欄は触らない。
   壊れた本文(JSON でない・object でない)は {} から組む(fast-jev-home-settings と同じ扱い)。"
  (setv settings None)
  (when (isinstance settings-text str)
    (try
      (setv settings (json.loads settings-text))
      (except [Exception]
        (setv settings None))))
  (when (not (isinstance settings dict))
    (setv settings {}))
  (setv env (.get settings "env"))
  (when (not (isinstance env dict)) (setv env {}))
  (setv merged (dfor [k v] (.items env) :if (not-in k OTEL-MANAGED-KEYS) k v))
  (.update merged (! (otel-env endpoint tenant host)))
  (setv (get settings "env") merged)
  (json.dumps settings :indent 2 :ensure-ascii False))
