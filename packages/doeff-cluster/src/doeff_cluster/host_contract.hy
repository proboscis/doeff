;;; 宿(job の Program を走らせる所)が Program に提供する物の閉じた契約(ADR-DOE-CLUSTER-001・改訂 1 の H)。
;;;
;;; 宿は 2 つ: 本番の worker の子 process(job_entry)と、手元の sim-cluster の偽の宿。どちらも Program に提供するのは次の 3 つだけで、
;;; それ以外(scheduler・時計・記録係・業務の handler)は Program が自分の with-handlers で並べる(runner は handler を足さない — R2)。
;;;
;;;   1. run-context  = Ask HOST-CONTRACT.run-context-key の答え(job_context.RunContext — coordinator の URL・worker・job・世代)
;;;   2. environ      = 宣言の :environ(子の環境変数)。Program は Ask と os.environ を読む handler(env_var_ask)で読む
;;;   3. program-path = Ask HOST-CONTRACT.program-key の答え(この job の詰めた Program の file の path — 記録係が header に載せる)
;;;
;;; 本番では、この module の土台の handler host-reader が os.environ から 1 と 3 に答える(業務の側が土台の組に並べる)。
;;; host-reader は session val を使うので、その外側に状態の handler(doeff_core_effects.handlers の state)が要る — 土台の組の中で
;;; host-reader より外に置く。
;;; sim の偽の宿は同じ鍵に同じ型で答える。job_entry の文書・host-reader・sim の宿は、この値を参照する(写しを作らない)。
(require doeff-hy.macros [defhandler val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import doeff_core_effects.effects [Ask])
(import .job_context [RunContext context-from-env])


(defrecord HostContract
  "宿が提供する物の鍵。run-context-key / program-key = Ask の鍵・program-env = 子の process に Program の path を渡す環境変数の名。"
  (#^ str run-context-key)
  (#^ str program-key)
  (#^ str program-env))


(val HOST-CONTRACT (HostContract :run-context-key "doeff.cluster.run-context"
                                 :program-key "doeff.cluster.program"
                                 :program-env "DOEFF_WORKER_PROGRAM"))


(defhandler host-reader
  {:needs #{} :tags {:context "doeff-cluster" :role "foundation"}}
  ;; 本番の宿の答え(worker が子へ渡した環境変数を読む)。土台の handler なので os.environ を直に読む(ADR-DOE-CLUSTER-001 R5b —
  ;; 記録係の下に置く)。環境変数は process の間で変わらないので session で 1 回だけ読む。
  (session val context (context-from-env))
  (session val program-path (os.environ.get HOST-CONTRACT.program-env ""))
  (Ask [key]
    :when (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key))
    (resume (if (= key HOST-CONTRACT.run-context-key) context program-path))))
