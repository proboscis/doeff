;;; 境目の記録係の組(ADR-DOE-CLUSTER-001 R5・R5b)— 宿の契約の run-context(RunContext)を記録の頭書きに使う所を、foundation/record_handlers
;;; から入口の側へ移した(#2981・#2167 の子。foundation の層は foundation しか読めず、RunContext の型は intent に在る)。
;;; 記録係と再生係そのもの(effect-recorder・effect-replayer・recording-handler)と、記録か再生かを選ぶ鍵(RECORD-MODE-KEY ほか)は
;;; foundation/record_handlers のまま。置き場と並べ方は record_handlers の「境目の記録係」の節。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.foundation.host_contract [HostContract])
(import doeff_cluster.foundation.record_handlers [RecordingInstaller ReplayState recording-handler effect-replayer
                                                  RECORD-MODE-KEY RECORD-OTLP-KEY REPLAY-STATE-KEY RECORD-MODES])


(defk recording-header [ctx program-path versions]
  {:pre [(: ctx RunContext) (: program-path str) (: versions dict)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "main"}}
  "記録の run の行に載せる欄 — 宿の契約の run-context(世代)と Program の置き場のキー(path の file の名)と版。再生の道具は
   program のキーで同じ Program を /programs から取り直せる(R3b — 記録は Program の中身を持たない)。"
  (val name (.rsplit program-path "/" 1))
  (val sha (if program-path (.removesuffix (get name -1) ".json") ""))
  {"worker" ctx.worker "instance" ctx.instance "attempt" ctx.attempt "specHash" ctx.spec-hash "placement" ctx.placement
   "revision" ctx.revision "program" sha "versions" versions})


(defk boundary-recorder [contract]
  {:pre [(: contract HostContract)] :post [(: % list)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "境目の記録係の組(0 か 1 つ)を作る — Ask RECORD-MODE-KEY で off / record / replay を選ぶ。業務の Program が翻訳の handler と
   土台の handler の間に並べる(ADR-DOE-CLUSTER-001 R5)。contract = 宿の契約の鍵(record の header の run-context・Program の path・版を
   Ask で読む鍵)。record の枝は、記録係の設定(recording-handler が読む JSON の形 {\"otlp\": URL})を綴る。"
  (<- mode str (Ask RECORD-MODE-KEY))
  (match mode
    "off" []
    "record" (do (<- url str (Ask RECORD-OTLP-KEY))
                 (<- ctx RunContext (Ask contract.run-context-key))
                 (<- program-path str (Ask contract.program-key))
                 (<- versions dict (Ask contract.versions-key))
                 (<- header dict (recording-header ctx program-path versions))
                 (<- installer RecordingInstaller (recording-handler {"otlp" url} ctx.job header))
                 [installer])
    "replay" (do (<- state ReplayState (Ask REPLAY-STATE-KEY))
                 [(effect-replayer state)])
    _ (raise (ValueError (.format "{} は {} のどれか: {!r}" RECORD-MODE-KEY (list RECORD-MODES) mode)))))
