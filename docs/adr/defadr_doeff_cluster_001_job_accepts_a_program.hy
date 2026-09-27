;;; Executable ADR: doeff-cluster の job が受け取るのは Program の値 1 つだけ — handler は Program の中の
;;; with-handlers で与え、実行器(job_entry)は既定の handler を 1 つも足さない。service と task は同じ入口で、
;;; 設定は Program の中の Ask と、それに答える os.environ を読む handler で読む。
;;;
;;; 出自 = operator 裁定 2026-09-27(Claude Code の会話・agora-redesign #829・逐語は :problem の fact)。
;;;
;;; 戻し方: この ADR を足した commit を revert する(ADR の file 1 つと enforcement 台帳の数が消える)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; ratchet 台帳 — 2026-09-27 時点の job_entry.hy で、実行器が Program の外から handler を足している
;; 呼び出しの数(origin/main faf81b97 の実測)。終端は全部 0(R2)。針は「現在数 <= 台帳数」を強制し、
;; 減らした便は同じ便で台帳を削る(削り忘れも赤)。
;;   scheduled       — scheduler の handler を runner が包む(service と task で 1 つずつ)
;;   env-handlers    — env の関数(import path)が組んだ handler の組を runner が包む(service と task で 1 つずつ)
;;   recording-layer — effect の記録係を runner が足す(service だけ・扱いは未決 — R5)
;; ---------------------------------------------------------------------------

(val RUNNER-HANDLER-ROSTER
  {"scheduled" 2
   "env-handlers" 2
   "recording-layer" 1})

(val JOB-ENTRY "packages/doeff-cluster/src/doeff_cluster/job_entry.hy")

(defk count-runner-handler-sites [text]
  {:pre [(: text str)]
   :post [(: % dict)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "job_entry の本文で、runner が Program の外から handler を足す呼び出しを種類ごとに数える(針と台帳の共通の物差し)。"
  (dfor name RUNNER-HANDLER-ROSTER
        name (len (re.findall (+ r"\(" (re.escape name) r"\s") text))))


(defadr ADR-DOE-CLUSTER-001
  :title "doeff-cluster の job(service も task も同じ API)が受け取るのは Program の値 1 つ(defk の関数を呼んだ結果)だけ。handler は Program の中の with-handlers で与え、実行器 job_entry は既定の handler を 1 つも足さない(scheduled と env の関数の包みも外す)。service の :env・:config・:env-config をやめ、設定は Program の中の Ask と os.environ を読む handler で読む。宣言が process へ渡す環境変数は宣言の値として持つ"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/job_entry.hy"
          "packages/doeff-cluster/src/doeff_cluster/service_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/remote_model.hy"
          "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy"]
  :problem
    [(fact
       "operator 裁定 2026-09-27(逐語 3 つ): \"okay such handlers/env is not supposed to be passed as job. what a job should accept is a Program. not explicit program 'and' handler. because handlers are dynamic scoped expressions in doeff\" / \"a doeff job runner should not have any 'default' handler.\" / \"i dont find any reason to have different api for services. if any configuration is needed we can always have Ask effect and a handler reading os.environ\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #829)— coordinator 経由")
     (fact
       "今の job_entry は service と task で入口が違う: service は --factory(関数の import path)と --config(本体の引数)と --env(handler の組を組む関数の import path)を受け、task は --blob(Program の値)と --env を受ける。どちらも runner が (scheduled (with-handlers <env の handler> program)) で包み、service はさらに記録係(recording-layer)を足す。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/job_entry.hy(run-service・task-outcome・env-handlers・recording-layer)")
     (fact
       "service の宣言は :env(env の関数の import path)・:config(本体の引数)・:env-config(env だけが読む設定)を持ち、coordinator へは 2 つを重ねた平たい run.config が渡る。"
       :evidence "packages/doeff-cluster/src/doeff_cluster/service_model.hy(ServiceDef の env・config・env-config)")]
  :context
    [(interpretation
       "doeff の handler は動的な scope の式であり、Program の外に「handler の組」を別の値として持つと、同じ Program が実行器ごとに違う意味になる。handler を Program の中の with-handlers に置けば、job の意味は Program の値だけで決まり、手元の run・模擬・cluster の実行が同じ値を同じ意味で走らせる。")
     (interpretation
       "runner の既定の handler(scheduler・env の組)は、Program が自分で並べるべき物を実行器が暗黙に足している形で、どれが効いているかが Program から読めない。既定を 0 にすれば、足りない handler は Program の中で未処理の effect として表に出る。")
     (interpretation
       "設定を宣言の欄(:config・:env-config)で渡す形は、設定を読む専用の口を job API に増やす。設定の読みは Ask の effect と、それに答える os.environ を読む handler で書けるので、service だけ別の API を持つ理由は無い。process へ渡す環境変数そのものは、宣言の値(どの process に何を渡すか)として残す。")]
  :decision
    [(rule R1 "doeff-cluster の job が受け取るのは Program の値 1 つ(defk の関数を呼んだ結果)だけ。Program と handler の組を別々に受け取らない。handler は Program の中の with-handlers で与える。")
     (rule R2 "実行器 job_entry は既定の handler を 1 つも足さない。今の scheduled の包みと env の関数(--env)の包みも外す。")
     (rule R3 "service と task は同じ API(Program の値 1 つ)で起こす。service だけの --factory・--config の入口はやめる。")
     (rule R4 "service の宣言の :env・:config・:env-config をやめる。設定は Program の中の Ask と、それに答える os.environ を読む handler(Program の側で並べる)で読む。宣言が process へ渡す環境変数は、宣言の値として持つ。")
     (rule R5 "記録係(recording-handler・job_entry の recording-layer)の扱いは未決(operator と議論中 — 案 A = Program の側で包む・案 B = 実行器の観測の口)。決まるまで今の記録係は RUNNER-HANDLER-ROSTER に数えて残し、新しく足さない。")
     (rule R6 "移行の間、runner が足している handler の呼び出しは RUNNER-HANDLER-ROSTER の数を超えない(新設は赤)。減らした便は同じ便で台帳を削る。")]
  :laws
    [(law job-entry-adds-no-handler
       :statement "for_all job j (service / task): job_entry が j を走らせる時に足す handler の数 = 0 — j の Program の外で効く handler は無い"
       :counterexamples
         [(counterexample "job_entry が (scheduled (with-handlers (env-handlers …) program)) で包む — Program の外で scheduler と env の handler が効く(2026-09-27 の本線の形)")
          (counterexample "runner が『既定で』時計や記録係を足す — Program を読んでもどの handler が効くか分からない")]
       :enforced-by ["test-adr-doe-cluster-001-runner-handler-ratchet"]
       :wiring "未配線(2026-09-27)— 針は runner の handler の呼び出しの増加を赤にする台帳だけで、0 の強制は無い。0 へ移す便(agora-redesign #829)が着地して台帳が空になった時に、この law の全体が配線される")
     (law service-and-task-share-one-entry
       :statement "for_all job j: j の入口の引数は Program の値 1 つ — service と task で入口の形が同じ"
       :counterexamples
         [(counterexample "service は --factory と --config、task は --blob で起こす — 同じ job なのに入口が 2 つある(2026-09-27 の本線の形)")
          (counterexample "service の宣言に :env-config を書いて設定を渡す — 設定は Program の中の Ask と os.environ を読む handler で読める")]
       :enforced-by []
       :wiring "未配線(2026-09-27)— 機械で検める針は無い。入口を 1 つにする便(agora-redesign #829)で検を足す")]
  :enforcement
    [(deftest test-adr-doe-cluster-001-runner-handler-ratchet
       ;; 針: job_entry が Program の外から handler を足す呼び出しは台帳を超えない(新設は赤)・台帳の削り忘れも赤。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root JOB-ENTRY) :encoding "utf-8"))
       (val counts (run (count-runner-handler-sites text)))
       (val grown (lfor [name n] (sorted (.items counts))
                         :if (> n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: {n} > 台帳 {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= grown [])
               (+ "job_entry が Program の外から足す handler を増やした(ADR-DOE-CLUSTER-001 R2・R6 — "
                  "handler は Program の中の with-handlers で与える): " (str grown)))
       (val stale (lfor [name n] (sorted (.items counts))
                         :if (< n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: 実体 {n} < 台帳 {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= stale [])
               (+ "台帳の削り忘れ(ADR-DOE-CLUSTER-001 R6 — 減らした便は RUNNER-HANDLER-ROSTER を同じ便で削る): "
                  (str stale))))
     (deftest test-adr-doe-cluster-001-ratchet-measure
       ;; 物差しの実演: 包みの呼び出しは数え、同じ名の定義や別の名は数えない。
       (val sample (+ "(defn env-handlers [env config ctx] None)\n"
                       "(run (scheduled (with-handlers (env-handlers e {} ctx) program)))\n"
                       "(recording-layers x)\n"))
       (assert (= (run (count-runner-handler-sites sample))
                  {"scheduled" 1 "env-handlers" 1 "recording-layer" 0})))]
  :plans ["agora-redesign #829"])
