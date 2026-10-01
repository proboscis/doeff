;;; 入口の検めの材料(ReadRuntimeFacts)に、渡した材料で答える handler(検と模擬 — runtime_identity から分けた・agora-redesign #2344)。
;;; この process を読んで答える本番の handler は doeff_cluster.runtime_identity_process(汎用の効果への言い換えへ直すのは別の子)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff_cluster.shared.intent.runtime_identity_model [ModuleOrigin ProcessFacts ReadRuntimeFacts])


(defhandler given-runtime-facts [#^ ProcessFacts facts]  ;; 引数に残す理由: 検と模擬が渡す材料そのもの(Ask で読む設定ではない)
  "渡した材料 facts(ProcessFacts)で ReadRuntimeFacts に答える handler — 問われた module だけを、渡した置き場から答える
   (無い module は import できない物)。"
  (ReadRuntimeFacts [modules]
    (val by-name (dfor o facts.origins o.module o))
    (resume (ProcessFacts :declared-json facts.declared-json :key facts.key :root facts.root :marker-json facts.marker-json
                          :origins (tuple (gfor m modules (.get by-name m (ModuleOrigin :module m :file ""))))
                          :pid facts.pid))))
