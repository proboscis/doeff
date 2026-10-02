;;; 宿の読み(Ask)の契約テストの解釈器(composition root)— 同じ契約の Program を、宿の答え手だけ替えて走らせる。
;;;
;;;   host-process  本物: 本番の子の土台と同じ並び (environ-reader)(既定 = この process の os.environ)+ host-reader(os.environ の
;;;                 worker の文脈と Program の path)。host-reader は session の値を使うので外側に state を置く
;;;   sim-host      fake: sim の宿の子と同じ並び(local.hy の run-fenced)host-answers(SimChild の文脈と Program の path)+
;;;                 (environ-reader 値の表)(子の spec.environ)
;;;
;;; 契約の世界は解釈器ごとに同じ形で用意する:
;;;   * 宿の文脈 CONTEXT と Program の path PROGRAM-PATH: 本物 = worker が子へ渡す環境変数の名(job_context.context-from-env が読む名)に
;;;     置く・fake = SimChild の ctx と program-path
;;;   * 宣言の :environ DECLARED: 本物 = os.environ に置く・fake = 値の表
;;;   * 置かない名(MISSING・OUTER-NAME): 本物 = 走る間だけ os.environ から外す・fake = 表に無い
;;;   本物の側は走る間だけ os.environ を書き換え、走った後に前の値へ戻す。
;;;   * 外側: 両方とも一番外に同じ reader(OUTER — 置き場に無い名の Ask の答え手・無い名は KeyError で断る)と、検の effect Outside の答え手
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(import dataclasses [dataclass])
(import os)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [reader state])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT environ-reader host-reader])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.job_context [RunContext])
(import doeff_cluster.sim.local [SimChild SimLink host-answers])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue])

(val HOST-PROCESS "host-process")
(val SIM-HOST "sim-host")

;; 宿の文脈(欄は全部 空でない値 — 読み落とした欄が既定の空と混ざらない)。runtime-env は宣言の JSON の文字列のまま運ぶ。
(val CONTEXT (RunContext "http://coordinator.contract" "w-contract" "r-contract" "job-contract"
                         :instance "1-0123456789ab" :attempt "1" :spec-hash "spec-contract" :placement "3"
                         :runtime-env "{\"repos\": []}" :env-key "env-contract"))
(val PROGRAM-PATH "/cache/programs/contract.json")

;; 宣言の :environ の名と値: JSON の object(字面どおり — parse しない・{…} を import として解かない)・空白と日本語を含む値・空の値。
(val JSON-NAME "HOST_CONTRACT_JSON")
(val JSON-VALUE "{\"a\": 1}")
(val PLAIN-NAME "HOST_CONTRACT_PLAIN")
(val PLAIN-VALUE " 前後に 空白 ")
(val EMPTY-NAME "HOST_CONTRACT_EMPTY")
(val DECLARED {JSON-NAME JSON-VALUE PLAIN-NAME PLAIN-VALUE EMPTY-NAME ""})
;; 置き場に無い名: MISSING は外側も答えない(KeyError)・OUTER-NAME は外側の reader が答える。
(val MISSING "HOST_CONTRACT_MISSING")
(val OUTER-NAME "HOST_CONTRACT_OUTER")
(val OUTER {OUTER-NAME "外側" int "型の鍵"})
(val OUTSIDE-ANSWER "外側の答え")


(defclass [(dataclass :frozen True)] Outside [EffectBase]
  "Ask でない検の effect(宿の答え手は答えず外側へ渡す)。")


(defhandler outer-answers
  ;; 一番外の答え手(両方の解釈器で同じ): Ask でない検の effect に答える。
  (Outside []
    (resume OUTSIDE-ANSWER)))


(defk context-environment [ctx program-path]
  {:pre [(: ctx RunContext) (: program-path str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "宿の文脈と Program の path を、worker が子へ渡す環境変数の名 → 値にする(契約の側で独りで書く — 読み手の関数を借りない)。"
  {"DOEFF_WORKER_COORDINATOR" ctx.coordinator-url "DOEFF_WORKER_NAME" ctx.worker "DOEFF_WORKER_REVISION" ctx.revision
   "DOEFF_WORKER_JOB" ctx.job "DOEFF_WORKER_INSTANCE" ctx.instance "DOEFF_WORKER_ATTEMPT" ctx.attempt
   "DOEFF_WORKER_SPEC_HASH" ctx.spec-hash "DOEFF_WORKER_PLACEMENT" ctx.placement "DOEFF_RUNTIME_ENV" ctx.runtime-env
   "DOEFF_RUNTIME_ENV_KEY" ctx.env-key HOST-CONTRACT.program-env program-path})


(defk restore-environment [saved]
  {:pre [(: saved dict)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "os.environ の名を saved の値(None = 無かった)へ戻す。"
  (for [#(name value) (.items saved)]
    (match value
      None (.pop os.environ name None)
      _ (.update os.environ {name value})))
  None)


(defk under-host-process [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の宿の読み手(本番の子の土台の並び)の下で program を走らせる。文脈・Program の path・DECLARED は走る間だけ os.environ に置き、
   置かない名は走る間だけ外し、走った後に前の値へ戻す。"
  (<- worker-given dict (context-environment CONTEXT PROGRAM-PATH))
  (val placed (| worker-given DECLARED))
  (val saved (dfor name (+ (list placed) [MISSING OUTER-NAME]) name (.get os.environ name)))
  (var answer None)
  (.update os.environ placed)
  (.pop os.environ MISSING None)
  (.pop os.environ OUTER-NAME None)
  (try
    (<- ran (with_handlers [(reader OUTER) outer-answers (state) (environ-reader) host-reader] program))
    (:= answer ran)
    (finally
      (<- (restore-environment saved))))
  answer)


(defk under-sim-host [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の宿の子の答え手(run-fenced と同じ並び: host-answers の内側に値の表の environ-reader)の下で program を走らせる。"
  (val link (SimLink :queue (RequestQueue) :actor CONTEXT.job :revision CONTEXT.revision :peer CONTEXT.worker
                     :versions (! (process-versions os.environ))))
  (val child (SimChild :ctx CONTEXT :program-path PROGRAM-PATH :environ (dict DECLARED) :link link :pid 1 :passable #()))
  (<- answer (with_handlers [(reader OUTER) outer-answers (host-answers child) (environ-reader child.environ)] program))
  answer)


(val INTERPRETERS {HOST-PROCESS under-host-process
                   SIM-HOST under-sim-host})
