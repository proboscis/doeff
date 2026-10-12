;;; Executable ADR: doeff-cluster の coordinator の版上げの前の準備(上げ先の版の自己起動の root を組む事)は、coordinator の Pod の外の
;;; Job で行い、Job が組み終えてから Deployment coordinator を入れ替える。上に載る系の repo に在った DaemonSet coordinator-root-prepare の
;;; 代わり。
;;;
;;; 出自 = card ki-5808173477cf(10-12 の調べ — 版上げのたびに全部の worker が 105〜141 秒 coordinator に届かない)と、調整役 cisco-c8 の
;;; 答え lt-V7DT(2026-10-12 17:35 JST — Flux の Kustomization で Job coordinator-prepare-<sha> を置いてよい。利用者の決定 2026-10-09
;;; "never on dedicated pods" は上に載る系の業務への物で、この Job は doeff-cluster 自身の仕組み)。
;;;
;;; 戻し方: この ADR と deploy/k8s/coordinator-prepare を足した commit を revert し、上に載る系の Flux の宣言から Kustomization
;;; doeff-cluster-coordinator-prepare と dependsOn の行を外す(順は Flux の宣言が先 — 先に dir を消すと、待つ先の Kustomization が
;;; Ready にならず coordinator が当たらない)。戻すと、版上げはまた古い Pod を止めてから新しい Pod の中で組む。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import pathlib [Path])
(import yaml)


(val K8S "packages/doeff-cluster/deploy/k8s")
(val JOB-NAME-PREFIX "coordinator-prepare-")
(val JOB-NAME-COMMIT-CHARS 12)


(defk commit-of [manifest]
  {:pre [(: manifest dict)]
   :post [(: % str)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "Deployment か Job の 1 つ目の container の env WORKER_DOEFF_COMMIT の値(版の一致を判じる物差し)。"
  (val container (get manifest "spec" "template" "spec" "containers" 0))
  (val found (lfor entry (get container "env") :if (= (get entry "name") "WORKER_DOEFF_COMMIT") (get entry "value")))
  (assert (= (len found) 1) (+ "env WORKER_DOEFF_COMMIT がちょうど 1 つ無い: " (str found)))
  (get found 0))


(defk version-mismatches [coordinator-commit jobs]
  {:pre [(: coordinator-commit str) (: jobs list)]
   :post [(: % list)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "組む Job の版の食い違いの文の列(空 = 一致)— Job はちょうど 1 つで、env の版と名の末尾が coordinator の版と同じでなければならない(R3)。"
  (when (!= (len jobs) 1)
    (return [(.format "組む Job がちょうど 1 つ無い({} 個)" (len jobs))]))
  (val job (get jobs 0))
  (<- job-commit (commit-of job))
  (val name (get job "metadata" "name"))
  (val want-name (+ JOB-NAME-PREFIX (cut coordinator-commit 0 JOB-NAME-COMMIT-CHARS)))
  (+ (if (= job-commit coordinator-commit) []
         [(.format "Job の WORKER_DOEFF_COMMIT {} が coordinator の {} と違う" job-commit coordinator-commit)])
     (if (= name want-name) []
         [(.format "Job の名 {} が {} でない" name want-name)])))


(defadr ADR-DOE-CLUSTER-003
  :title "coordinator の版上げの前に、上げ先の版の自己起動の root を coordinator の Pod の外の Job(coordinator-prepare-<commit の頭 12 字>)が同じ node・同じ hostPath・同じ /work で組み、Flux は Job が終わるまで Deployment coordinator を当てない。Job の版は coordinator の版と同じ"
  :status "accepted"
  :scope ["packages/doeff-cluster/deploy/k8s/coordinator-prepare/coordinator-prepare.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator-prepare/kustomization.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator/coordinator.yaml"
          "packages/doeff-cluster/deploy/k8s/kustomization.yaml"
          "packages/doeff-cluster/tests/test_k8s_declarations.py"
          "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy"]
  :problem
    [(fact
       "coordinator の版上げ(Deployment の WORKER_DOEFF_COMMIT の変更・strategy Recreate)のたびに、全部の機体の worker が同じ秒に coordinator に届かなくなる。10-12 12:39 JST(fb32909f3 → 74ebb50b6)は止まっていた 141.1 秒のうち bytecode の焼きが 111 秒(stored=83 rebuilt=155 reused=0)、14:43 JST(74ebb50b6 → 475ee1e5d)は 105.2 秒のうち 79 秒(stored=16 rebuilt=223 reused=0)。"
       :evidence "card ki-5808173477cf の本文・coordinator の Pod の log の 2 行(boot: … の root を準備した・coordinator: 止まっていた N 秒)")
     (fact
       "焼き直す数が変わった file の 4〜8 倍になるのは、doeff-hy の印の上げ(14:43 は CODE_TAG 2→3 で保存先が 1 本も当たらない・12:39 は RECORD_TAG 5→6 で中身の変わらない Hy 133 本が全部外れる)と、ほぼ全部の module が使う macro(defk・deff)の閉包の digest の変化(印を上げない 475ee1e5d → 6533804cf でも中身の変わらない Hy 126 本が外れる)。外れ方は正しい(macro が変われば展開が変わりうる)。"
       :evidence "card ki-5808173477cf の発言(10-12 の調べ — 手元で boot.sh の役 prepare を本番と同じ順に走らせ、stored/rebuilt を本番の数で再現した)")
     (fact
       "上に載る系の repo の DaemonSet coordinator-root-prepare(2026-10-09)が上げ先の版を先に組んでいたが、coordinator の宣言をこの repo の Kustomization へ移した 2026-10-10 に代わり無しで外れた。"
       :evidence "card ki-dc05afc53f9e・上に載る系の commit bab798f50")
     (fact
       "2026-10-09 に coordinator の Pod の中で上げ先の root を組んだ時、Pod の CPU の上限 2 を走っている coordinator と食い合い、絞りの見張りが組みを止めた。"
       :evidence "上に載る系の bab798f50^:deploy/agent-worker/coordinator-root-prepare.yaml の頭の註")]
  :context
    [(interpretation
       "焼きの秒は CPU を足しても縮まない(保存先なし・並列 8 で 実 93 秒 = 走査 10・閉包の歩み 21・焼き 29・道具の起動 約 32)。組み済みの root(印 .doeff-boot-ready)なら boot.sh は焼かずに起動するので、止まる間から組む秒を外すには、古い Pod を止める前に組み終えるしかない。")
     (interpretation
       "Deployment を RollingUpdate にして新しい Pod の中で組む形は採らない — 状態の file の書き手が 2 つになるか、新しい Pod が Ready にならず入れ替えが詰まる。")]
  :decision
    [(rule R1 "coordinator の上げ先の版の root は、Job coordinator-prepare-<commit の頭 12 字>(deploy/k8s/coordinator-prepare)が boot.sh の役 prepare で組む。Job は Deployment coordinator と同じ node・同じ hostPath を同じ絶対 path /work に mount し(root は組んだ時の絶対 path を持つ)、記録の /work/coord は mount せず、資格(ServiceAccount の token・上に載る系の ConfigMap)を持たない。自分の cgroup で組み、走っている coordinator の CPU を食わない。")
     (rule R2 "Flux は deploy/k8s/coordinator-prepare を別の Kustomization(wait: true)で当て、deploy/k8s/coordinator の Kustomization は dependsOn でその Kustomization が同じ revision で Ready(Job が終わった)になるまで当たらない。Job が失敗すれば coordinator は古い版のまま動き続ける。Job は ttlSecondsAfterFinished を持たない — TTL が消すと Flux が次の照合で作り直すので、古い版の Job は名の版が変わって宣言から外れた時に Flux の prune が消す(組んだ root と bytecode の保存先は hostPath に残る)。")
     (rule R3 "Job の env WORKER_DOEFF_COMMIT と名の末尾は、Deployment coordinator の WORKER_DOEFF_COMMIT と同じ。版上げの commit は 3 か所(coordinator の env・Job の env・Job の名)を一緒に変える。")]
  :laws
    [(law the-prepare-job-builds-the-coordinators-version
       :statement "for_all deploy/k8s の宣言: 役 prepare の Job はちょうど 1 つ ∧ その WORKER_DOEFF_COMMIT = Deployment coordinator の WORKER_DOEFF_COMMIT ∧ その名 = coordinator-prepare- + その commit の頭 12 字"
       :counterexamples
         [(counterexample "coordinator の版だけを上げ、Job の版を前の版のまま残す — Flux は Job を変えずに Ready と見て coordinator を当て、新しい Pod がまた中で組む(止まる間が戻る)")
          (counterexample "Job の env だけを上げて名を変えない — Job の template は変えられないので Flux の当てが断られ、coordinator が当たらない")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version"
                     "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-version-mismatches-are-named"]
       :wiring "配線済み(2026-10-12・card ki-5808173477cf)")
     (law the-prepare-job-shares-only-the-root-with-the-coordinator
       :statement "for_all 役 prepare の Job J と Deployment coordinator C: node(J) = node(C) ∧ hostPath(J の work) = hostPath(C の work) ∧ J の mount = {/work} ∧ J は ServiceAccount の token と envFrom を持たない ∧ J は ttlSecondsAfterFinished を持たない"
       :counterexamples
         [(counterexample "別の path(例 /prewarm/coordinator)で組む — 完成の印を持つ root の venv の python が coordinator の Pod で見つからず起動できない(上に載る系の DaemonSet の頭の註)")
          (counterexample "ttlSecondsAfterFinished で終わった Job を消す — Flux が次の照合で作り直し、組み直しの Pod を起こし続ける")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_root_is_prepared_by_one_job_beside_it"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_job_is_not_removed_by_ttl"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_builds_alone_with_only_the_job"]
       :wiring "配線済み(2026-10-12・card ki-5808173477cf)。Flux の順(R2 の wait と dependsOn)は上に載る系の repo の Flux の宣言が持ち、この repo の検の外")]
  :enforcement
    [(deftest test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version
       ;; 今の宣言で、組む Job の版と名が coordinator の版と合う(R3)。
       (val k8s (/ (. (Path __file__) parent parent parent) K8S))
       (setv [coordinators jobs]
         (lfor #(file kind) [#(#("coordinator" "coordinator.yaml") "Deployment") #(#("coordinator-prepare" "coordinator-prepare.yaml") "Job")]
               (lfor doc (yaml.safe-load-all (.read-text (/ k8s #* file) :encoding "utf-8"))
                     :if (and (is-not doc None) (= (get doc "kind") kind)) doc)))
       (assert (= (len coordinators) 1) coordinators)
       (val problems (run (version-mismatches (run (commit-of (get coordinators 0))) jobs)))
       (assert (= problems [])
               (+ "組む Job の版が coordinator の版と合わない(ADR-DOE-CLUSTER-003 R3 — 版上げの commit は coordinator の env・Job の env・"
                  "Job の名の 3 か所を一緒に変える): " (str problems))))
     (deftest test-adr-doe-cluster-003-version-mismatches-are-named
       ;; 反例の 2 つ(coordinator だけを上げた宣言・Job の env だけを上げた宣言)を、食い違いの判じが名指して断る。
       (val old "475ee1e5d7d5894f6ba4c73907be57b057ea70e9")
       (val new "a1d17b0dd3047d1111f442e5a1ba4348ea3eab05")
       (setv [matching coordinator-only env-only]
         (lfor #(name commit) [#("coordinator-prepare-a1d17b0dd304" new) #("coordinator-prepare-475ee1e5d7d5" old)
                               #("coordinator-prepare-475ee1e5d7d5" new)]
               {"metadata" {"name" name}
                "spec" {"template" {"spec" {"containers" [{"env" [{"name" "WORKER_DOEFF_COMMIT" "value" commit}]}]}}}}))
       (assert (= (run (version-mismatches new [matching])) []))
       (assert (= (len (run (version-mismatches new [coordinator-only]))) 2))
       (assert (= (run (version-mismatches new [env-only]))
                  ["Job の名 coordinator-prepare-475ee1e5d7d5 が coordinator-prepare-a1d17b0dd304 でない"]))
       (assert (= (run (version-mismatches new [])) ["組む Job がちょうど 1 つ無い(0 個)"])))]
  :plans ["card ki-5808173477cf"])
