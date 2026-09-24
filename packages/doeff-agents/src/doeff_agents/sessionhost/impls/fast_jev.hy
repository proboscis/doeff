;;; 会話の圧縮 plugin(fast-jev-compaction)の綴りと純関数 — 借りた家(pod)への据え付けと、続きの手番の
;;; 前に圧縮を撃ってよいかの読み(2026-09-22)。
;;;
;;; 何のために: plugin は Jev(TypeSafe の判定 API)で古い tool の結果だけを消す(要約文なし・model 0 回)。
;;; Mac の profile には人が settings.json で入れてあるが、pod の借りた家 <homes-root>/claude/<account> は
;;; sessionhost が組むので、ここで同じ形(enabledPlugins / extraKnownMarketplaces / pluginConfigs / env)を
;;; 書く。鍵は Secret を file で mount し plugin が自分で読む(apiKeyFile)— sessionhost は値を読まないし、
;;; *_API_KEY の env を CLI に渡す規則違反も作らない。
;;;
;;; substrate-clean: 生 IO 禁止。ここは綴りと純関数だけ(読み書きは呼び手の effect)。

(require doeff-hy.macros [defk])

(import json)
(import math)

(setv FAST-JEV-PLUGIN-ID "fast-jev-compaction@fast-jev-compaction")
(setv FAST-JEV-MARKETPLACE-NAME "fast-jev-compaction")
(setv FAST-JEV-MARKETPLACE-URL "https://github.com/proboscis/fast-jev-compaction.git")
;; agentd の env: 鍵の file の path(pod は Secret の mount・Mac は名乗らない = 何もしない)。
(setv FAST-JEV-KEY-FILE-ENV "FAST_JEV_COMPACTION_API_KEY_FILE")
;; cache の TTL(分)— この口座の prompt cache は 1 時間(Mac の profile と同じ値)。
(setv FAST-JEV-CACHE-TTL-MINUTES 60)

;; 借りた家に据える plugin の版の pin(宣言点はここ 1 つ)。据え済みの家(PVC で持ち越される)は、家の中の
;; plugin.json の版がこの値と違う時だけ `claude plugin update` で揃える — 毎回の起動で git fetch を払わず、
;; pin を進めた便でだけ更新が走る。実測 2026-09-22 23:1x: fork の 0.5.0(残量の上限・効き目の門)を push しても
;; pod の家は 0.4.6 のままだった(据え付けは「effective でない時だけ install」で、更新の口が無かった)。
(setv FAST-JEV-PLUGIN-VERSION "0.6.0")
;; 家の中の plugin.json の path(CLAUDE_CONFIG_DIR からの相対)— `claude plugin install` が置く marketplace の clone。
(setv FAST-JEV-PLUGIN-JSON-SUFFIX "plugins/marketplaces/fast-jev-compaction/.claude-plugin/plugin.json")

;; plugin の状態 file(session ごとの温冷の記憶・journal)の置き場 — 家(HOME)の下の `.local/state` は pod の
;; StatefulSet が序数ごとの PVC で持ち越す(ACP acpcluster.yaml seat-home-state)ので、pod が入れ替わっても
;; 「前の応答からの経過」が残り、温かい cache を「記録が無い = 冷えた」と誤って圧縮しない。既定の
;; `~/.cache/fast-jev-compaction` は持ち越されない(実測 2026-09-22 08:1x: 07:4x の入れ替えで journal ごと消えた)。
(setv FAST-JEV-STATE-DIR-SUFFIX ".local/state/fast-jev-compaction")


;; plugin の TTL の既定(分)— options に cacheTtlMinutes が無い家で plugin が使う値(hooks/fast-jev.ts の
;; HOOK_DEFAULTS.cacheTtlMinutes)。借りた家は options に FAST-JEV-CACHE-TTL-MINUTES を必ず書く。
(setv FAST-JEV-PLUGIN-DEFAULT-TTL-MINUTES 5)
;; 「確かに温かい」と読む時の余白(ms): 読んでから手番の最初の model 呼び出しまでの数秒で TTL を跨がないよう、
;; TTL の終わりの 60 秒は温かいと読まない(その拍は今日どおり圧縮の process を起こし、plugin 自身に決めさせる)。
(setv FAST-JEV-WARM-MARGIN-MS 60000)


(defk fast-jev-plugin-options [settings-text]
  {:pre [(: settings-text (| str None))]
   :post [(: % dict)]}
  "profile の settings.json の本文から plugin の options(pluginConfigs[plugin].options)を読む(純関数)。
   読めない・無い時は {}。"
  (setv parsed None)
  (when (isinstance settings-text str)
    (try
      (setv parsed (json.loads settings-text))
      (except [Exception]
        (setv parsed None))))
  (setv configs (when (isinstance parsed dict) (.get parsed "pluginConfigs")))
  (setv entry (when (isinstance configs dict) (.get configs FAST-JEV-PLUGIN-ID)))
  (setv options (when (isinstance entry dict) (.get entry "options")))
  (if (isinstance options dict) options {}))


(defk fast-jev-session-state-path [settings-text session-id]
  {:pre [(: settings-text (| str None)) (: session-id str)]
   :post [(: % (| str None))]}
  "会話(claude の session id)ごとの plugin の状態 file の path(純関数・plugin hooks/cold.ts の statePath と同じ綴り:
   id の [A-Za-z0-9._-] 以外を _ に置き、<stateDir>/<id>.json)。options に stateDir を宣言していない家は None
   (plugin の既定の置き場は plugin の process の HOME 次第なので、ここでは導かない — 読めない = 温かいと言わない)。"
  (import re)
  (setv state-dir (.get (! (fast-jev-plugin-options settings-text)) "stateDir"))
  (if (or (not (isinstance state-dir str)) (= (.strip state-dir) "") (= (.strip session-id) ""))
      None
      (+ (.rstrip state-dir "/") "/" (re.sub r"[^A-Za-z0-9._-]" "_" session-id) ".json")))


(defk fast-jev-cache-surely-warm [settings-text state-text config-dir model now-ms]
  {:pre [(: settings-text (| str None)) (: state-text (| str None)) (: config-dir str) (: model (| str None))
         (: now-ms int)]
   :post [(: % bool)]}
  "続きの手番の前の `/compact fast-jev-if-cold` が**何もしない**と確かに言えるか(純関数)。
   plugin の温冷の判断(hooks/cold.ts の coldReason — 状態 file の前回の {at, configDir, model} と今の値)と
   同じ規則: 状態が在り、configDir(= CLAUDE_CONFIG_DIR)と model が前回と同じで、前回からの経過が TTL 以内なら
   温かい(plugin は 'cache warm; untouched' で何も書かずに終わる)。ここは**温かいと確かに言える時だけ** True を返す:
   状態が読めない・壊れている・model が分からない・TTL の終わりの余白(FAST-JEV-WARM-MARGIN-MS)に入っている時は
   False(= 今日どおり圧縮の process を起こし、判断は plugin 自身がする)。冷えた再開(別の機体・別の口座・別の
   model・TTL 切れ)は必ず False なので、「別の機体で続ける時は手番の前に必ず圧縮」を壊さない。"
  (setv parsed None)
  (when (isinstance state-text str)
    (try
      (setv parsed (json.loads state-text))
      (except [Exception]
        (setv parsed None))))
  (setv ttl-minutes (.get (! (fast-jev-plugin-options settings-text)) "cacheTtlMinutes"
                          FAST-JEV-PLUGIN-DEFAULT-TTL-MINUTES))
  (when (or (isinstance ttl-minutes bool) (not (isinstance ttl-minutes #(int float))))
    (setv ttl-minutes FAST-JEV-PLUGIN-DEFAULT-TTL-MINUTES))
  (if (not (isinstance parsed dict))
      False
      (do
        (setv at (.get parsed "at"))
        (bool (and (isinstance at #(int float))
                   (not (isinstance at bool))
                   (math.isfinite at)
                   (isinstance model str) (!= model "")
                   (= (.get parsed "configDir") config-dir)
                   (= (.get parsed "model") model)
                   (<= (- now-ms at) (- (* ttl-minutes 60000) FAST-JEV-WARM-MARGIN-MS)))))))


(defk fast-jev-state-dir [home]
  {:pre [(: home str) (> (len home) 0)]
   :post [(: % str)]}
  "家(HOME)から plugin の状態 file の置き場を組む(純関数)。"
  (+ (.rstrip home "/") "/" FAST-JEV-STATE-DIR-SUFFIX))


(defk fast-jev-compaction-enabled [settings-text]
  {:pre [(: settings-text (| str None))]
   :post [(: % bool)]}
  "profile の settings.json の本文から、圧縮 plugin が**実際に効く**形かを読む(純関数):
   enabledPlugins に plugin が真で、env に CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 が在ること。
   どちらか欠けると `/compact` は組込みの要約(model 1 回・会話全体を読む)に落ちるので、
   その profile では圧縮の prompt を撃ってはならない — 会社の profile には plugin が無い。"
  (setv parsed None)
  (when (isinstance settings-text str)
    (try
      (setv parsed (json.loads settings-text))
      (except [Exception]
        (setv parsed None))))
  (if (not (isinstance parsed dict))
      False
      (do
        (setv plugins (.get parsed "enabledPlugins"))
        (setv env (.get parsed "env"))
        (bool (and (isinstance plugins dict)
                   (is (.get plugins FAST-JEV-PLUGIN-ID) True)
                   (isinstance env dict)
                   (= (str (.get env "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS" "")) "1"))))))


(defk fast-jev-home-settings [settings-text key-file state-dir]
  {:pre [(: settings-text (| str None)) (: key-file str) (> (len key-file) 0) (: state-dir str) (> (len state-dir) 0)]
   :post [(: % str)]}
  "借りた家の settings.json の本文に plugin の宣言を合流させた本文(純関数・冪等)。
   足す欄: enabledPlugins[plugin]=true・extraKnownMarketplaces[name]={source git url}・
   pluginConfigs[plugin].options = {cacheTtlMinutes, apiKeyFile, stateDir}・env.CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=\"1\"。
   他の欄は触らない。壊れた本文(JSON でない・object でない)は {} から組む(CLI は壊れた settings に
   loud に落ちるので、直す方が今日より悪くならない)。"
  (setv settings None)
  (when (isinstance settings-text str)
    (try
      (setv settings (json.loads settings-text))
      (except [Exception]
        (setv settings None))))
  (when (not (isinstance settings dict))
    (setv settings {}))
  (setv plugins (.setdefault settings "enabledPlugins" {}))
  (when (not (isinstance plugins dict)) (setv plugins {}) (setv (get settings "enabledPlugins") plugins))
  (setv (get plugins FAST-JEV-PLUGIN-ID) True)
  (setv markets (.setdefault settings "extraKnownMarketplaces" {}))
  (when (not (isinstance markets dict)) (setv markets {}) (setv (get settings "extraKnownMarketplaces") markets))
  (setv (get markets FAST-JEV-MARKETPLACE-NAME) {"source" {"source" "git" "url" FAST-JEV-MARKETPLACE-URL}})
  (setv configs (.setdefault settings "pluginConfigs" {}))
  (when (not (isinstance configs dict)) (setv configs {}) (setv (get settings "pluginConfigs") configs))
  (setv (get configs FAST-JEV-PLUGIN-ID)
        {"options" {"cacheTtlMinutes" FAST-JEV-CACHE-TTL-MINUTES "apiKeyFile" key-file "stateDir" state-dir}})
  (setv env (.setdefault settings "env" {}))
  (when (not (isinstance env dict)) (setv env {}) (setv (get settings "env") env))
  (setv (get env "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS") "1")
  (json.dumps settings :indent 2 :ensure-ascii False))


(defk fast-jev-install-command [config-dir]
  {:pre [(: config-dir str) (> (len config-dir) 0)]
   :post [(: % str)]}
  "plugin を家に据える 1 命令(sh -c 用・純関数): marketplace を登録し(登録済みなら失敗を無視)、install する。
   `claude plugin install` は settings.json に enabledPlugins と marketplace を自分で書く — その後で
   fast-jev-home-settings が options と env を合流させる。"
  (import shlex)
  (setv home (shlex.quote config-dir))
  (+ f"CLAUDE_CONFIG_DIR={home} claude plugin marketplace add {(shlex.quote FAST-JEV-MARKETPLACE-URL)} >/dev/null 2>&1; "
     f"CLAUDE_CONFIG_DIR={home} claude plugin install {(shlex.quote FAST-JEV-PLUGIN-ID)} --scope user"))


(defk fast-jev-plugin-json-path [config-dir]
  {:pre [(: config-dir str) (> (len config-dir) 0)]
   :post [(: % str)]}
  "家(CLAUDE_CONFIG_DIR)から据え済み plugin の plugin.json の path を組む(純関数)。"
  (+ (.rstrip config-dir "/") "/" FAST-JEV-PLUGIN-JSON-SUFFIX))


(defk fast-jev-installed-version [plugin-json-text]
  {:pre [(: plugin-json-text (| str None))]
   :post [(: % (| str None))]}
  "据え済み plugin の plugin.json の本文から版を読む(純関数)。file が無い・壊れている・version が無い時は None
   (= 読めない。\"古い\" とは区別する — 読めない家は update を撃たず、据え付けの経路に任せる)。"
  (setv parsed None)
  (when (isinstance plugin-json-text str)
    (try
      (setv parsed (json.loads plugin-json-text))
      (except [Exception]
        (setv parsed None))))
  (if (not (isinstance parsed dict))
      None
      (do
        (setv v (.get parsed "version"))
        (if (and (isinstance v str) (.strip v)) (.strip v) None))))


(defk fast-jev-plugin-outdated [installed-version]
  {:pre [(: installed-version (| str None))]
   :post [(: % bool)]}
  "据え済みの版が pin と違うか(純関数)。None(読めない)は False — 読めない家に update を撃たない。"
  (and (isinstance installed-version str) (!= installed-version FAST-JEV-PLUGIN-VERSION)))


(defk fast-jev-update-command [config-dir]
  {:pre [(: config-dir str) (> (len config-dir) 0)]
   :post [(: % str)]}
  "据え済み plugin を pin の版へ揃える 1 命令(sh -c 用・純関数): marketplace の clone を fetch してから
   plugin を入れ直す。settings.json は触らない(options と env は fast-jev-home-settings が合流済み)。"
  (import shlex)
  (setv home (shlex.quote config-dir))
  (+ f"CLAUDE_CONFIG_DIR={home} claude plugin marketplace update {(shlex.quote FAST-JEV-MARKETPLACE-NAME)} >/dev/null 2>&1; "
     f"CLAUDE_CONFIG_DIR={home} claude plugin update {(shlex.quote FAST-JEV-PLUGIN-ID)}"))
