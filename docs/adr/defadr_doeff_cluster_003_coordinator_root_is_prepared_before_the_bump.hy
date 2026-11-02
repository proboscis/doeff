;;; Executable ADR: doeff-cluster の coordinator の版上げの前の準備(上げ先の版の自己起動の root を組む事)は、coordinator の Pod の外の
;;; Job で行い、Job が組み終えてから Deployment coordinator を入れ替える。上に載る系の repo に在った DaemonSet coordinator-root-prepare の
;;; 代わり。
;;;
;;; 訳: 版上げのたびに全部の worker が 105〜141 秒 coordinator に届かなかった(10-12 の調べ)。Job coordinator-prepare-<sha> は
;;; Flux の Kustomization で置く。業務に専用の Pod を立てない決まりは上に載る系の業務への物で、この Job は doeff-cluster 自身の
;;; 仕組みなので当たらない。
;;;
;;; 戻し方: この ADR と deploy/k8s/coordinator-prepare を足した commit を revert し、上に載る系の Flux の宣言から Kustomization
;;; doeff-cluster-coordinator-prepare と dependsOn の行を外す(順は Flux の宣言が先 — 先に dir を消すと、待つ先の Kustomization が
;;; Ready にならず coordinator が当たらない)。戻すと、版上げはまた古い Pod を止めてから新しい Pod の中で組む。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import pathlib [Path])
(import shutil)
(import subprocess)
(import yaml)


(val K8S "packages/doeff-cluster/deploy/k8s")
(val JOB-NAME-PREFIX "coordinator-prepare-")


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
  (val want-name (+ JOB-NAME-PREFIX coordinator-commit))
  (+ (if (= job-commit coordinator-commit) []
         [(.format "Job の WORKER_DOEFF_COMMIT {} が coordinator の {} と違う" job-commit coordinator-commit)])
     (if (= name want-name) []
         [(.format "Job の名 {} が {} でない" name want-name)])))


(defadr ADR-DOE-CLUSTER-003
  :title "coordinator の版上げの前に、上げ先の版の自己起動の root を coordinator の Pod の外の Job(coordinator-prepare-<commit>)が同じ node・同じ hostPath・同じ /work で組み、Flux は Job が終わるまで Deployment coordinator を当てない。Job の版は組み立ての時に coordinator の版の行から写す(版の定義元は coordinator.yaml の 1 行)。Job は置けない間は Pending で待ち(期限は Pod の spec が持つ)、Job と coordinator は既定の 0 より高い PriorityClass で atlas の置き場を持つ"
  :status "accepted"
  :scope ["packages/doeff-cluster/deploy/k8s/coordinator-prepare/coordinator-prepare.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator-prepare/priority-class.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator-prepare/kustomization.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator/coordinator.yaml"
          "packages/doeff-cluster/deploy/k8s/kustomization.yaml"
          "packages/doeff-cluster/tests/test_k8s_declarations.py"
          "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy"]
  :problem
    [(fact
       "coordinator の版上げ(Deployment の WORKER_DOEFF_COMMIT の変更・strategy Recreate)のたびに、全部の機体の worker が同じ秒に coordinator に届かなくなる。10-12 12:39 JST(fb32909f3 → 74ebb50b6)は止まっていた 141.1 秒のうち bytecode の焼きが 111 秒(stored=83 rebuilt=155 reused=0)、14:43 JST(74ebb50b6 → 475ee1e5d)は 105.2 秒のうち 79 秒(stored=16 rebuilt=223 reused=0)。"
       :evidence "coordinator の Pod の log の 2 行(boot: … の root を準備した・coordinator: 止まっていた N 秒)")
     (fact
       "焼き直す数が変わった file の 4〜8 倍になるのは、doeff-hy の印の上げ(14:43 は CODE_TAG 2→3 で保存先が 1 本も当たらない・12:39 は RECORD_TAG 5→6 で中身の変わらない Hy 133 本が全部外れる)と、ほぼ全部の module が使う macro(defk・deff)の閉包の digest の変化(印を上げない 475ee1e5d → 6533804cf でも中身の変わらない Hy 126 本が外れる)。外れ方は正しい(macro が変われば展開が変わりうる)。"
       :evidence "10-12 の調べ(手元で boot.sh の役 prepare を本番と同じ順に走らせ、stored/rebuilt を本番の数で再現した)")
     (fact
       "上に載る系の repo の DaemonSet coordinator-root-prepare(2026-10-09)が上げ先の版を先に組んでいたが、coordinator の宣言をこの repo の Kustomization へ移した 2026-10-10 に代わり無しで外れた。"
       :evidence "上に載る系の commit bab798f50")
     (fact
       "2026-10-09 に coordinator の Pod の中で上げ先の root を組んだ時、Pod の CPU の上限 2 を走っている coordinator と食い合い、絞りの見張りが組みを止めた。"
       :evidence "上に載る系の bab798f50^:deploy/agent-worker/coordinator-root-prepare.yaml の頭の註")
     (fact
       "最初の形(749c11015)は Job の版と名を coordinator-prepare.yaml に手で書き、版上げの commit が 3 か所を揃える約束を ADR の検で見張った。10-12 18:37 JST の a53f417cc は coordinator.yaml の版だけを 3fcba1939 へ上げ、Job は前の版 a1d17b0dd のまま — prepare の Kustomization は前の Job のまま Ready になり、coordinator は上げ先の root を Pod の中で組んで入れ替わった。着地の列は doeff の検を走らせない(.agents/land-queue.toml の gate は衝突の印だけを見る)ので、ADR の検は次の日次の全体検証で初めて赤になった(6800d9f5e が揃え直した)。"
       :evidence "doeff 6800d9f5e の本文")
     (fact
       "2026-11-02 17:44 JST、Flux が coordinator の版を 6e1996649 → fffb250ee へ上げる bd09a669b を当て、Job coordinator-prepare-fffb250ee… を作った。atlas の memory の requests の和は 19904Mi / allocatable 20263Mi(98%・空き 359Mi)で、request 512Mi の Pod は置けなかった(FailedScheduling「Insufficient memory … No preemption victims found」— priority は既定の 0 で、atlas の他の Pod と同じなので追い出せる相手が居ない)。Job の spec の activeDeadlineSeconds 1200 は Pending の間も数え、Pod は一度も走らずに 18:04 JST に DeadlineExceeded で Failed になった。prepare の Kustomization は HealthCheckFailed、coordinator の Kustomization は DependencyNotReady。Job の名は版で決まり、Flux は Failed の Job を変わっていないと読んで作り直さないので、次の版上げ(9f10594e・18:13 JST に Job を作り、置けて 18:28 JST に組み終えた)まで固まった。atlas には他の repo が宣言する Pod(object store の 6 本・DB・測りの系)が固定で載り、固定の Pod が増えるたびに組む Job の置き場が消える形だった。"
       :evidence "kubectl の Job・Event と kustomize-controller の log(2026-11-02)")]
  :context
    [(interpretation
       "焼きの秒は CPU を足しても縮まない(保存先なし・並列 8 で 実 93 秒 = 走査 10・閉包の歩み 21・焼き 29・道具の起動 約 32)。組み済みの root(印 .doeff-boot-ready)なら boot.sh は焼かずに起動するので、止まる間から組む秒を外すには、古い Pod を止める前に組み終えるしかない。")
     (interpretation
       "Deployment を RollingUpdate にして新しい Pod の中で組む形は採らない — 状態の file の書き手が 2 つになるか、新しい Pod が Ready にならず入れ替えが詰まる。")
     (interpretation
       "置けない事と組めない事は別の失敗で、同じ Failed にしない。組めない(boot.sh が非 0 で終わる・焼きが止まる)は版を直すまで直らないので Job を失敗させ、coordinator を古い版に留める。置けない(node の空きが足りない)は空けば直るので、Pending のまま待てば人の手なしに組み終える。Pod の spec の activeDeadlineSeconds は kubelet が Pod を受けた刻(status.startTime)から数えるので、止まりは今と同じ 20 分で切り、Pending の間は数えない。待つ間は prepare の Kustomization が Ready にならないので、Flux の Ready を見る見回りの気付きは残る。")
     (interpretation
       "置き場を守る形は 2 つを比べ、PriorityClass を採る。(a) atlas の memory の予算に組む Job の 512Mi を書いて検で守る形は採らない — atlas に載る他の workload は別の repo が宣言し、この repo の検はその requests を読めないので、書いた予算を守る者が居ない(11-02 も、他の repo の固定の Pod が増えて空きが 359Mi になった)。(b) PriorityClass は scheduler が守る: 空きが足りない時だけ、既定の 0 の Pod を足りる分だけ追い出す(値の高い物・早く起きた物から残す)。害は、atlas が満ちた時の版上げ 1 回ごとに 0 の Pod が少し追い出される事 — 追い出された Pod は自分の controller が作り直し、組む Job が終わる(秒〜数分)と置き場が戻る。長く走る Job(12 時間の copy など)が追い出されると進みを失うが、atlas が満ちている事の害の 1 つで、版上げが人の手まで止まる害より小さい、と判じた。")
     (interpretation
       "coordinator も同じ class を持つ。Recreate は旧い Pod を止めてから新しい Pod を作るので、その拍に空いた 1536Mi を Pending だった atlas の他の Pod が先に取ると、新しい coordinator は置けず、全部の worker が coordinator に届かないままになる(組む Job が置けない事より重い)。値は既定の 0 より上で、storage と node の system の class(9 億〜20 億)より十分下の 1000 万 — 追い出すのは一般の Pod だけ。")]
  :decision
    [(rule R1 "coordinator の上げ先の版の root は、Job coordinator-prepare-<commit>(deploy/k8s/coordinator-prepare)が boot.sh の役 prepare で組む。Job は Deployment coordinator と同じ node・同じ hostPath を同じ絶対 path /work に mount し(root は組んだ時の絶対 path を持つ)、記録の /work/coord は mount せず、資格(ServiceAccount の token・上に載る系の ConfigMap)を持たない。自分の cgroup で組み、走っている coordinator の CPU を食わない。")
     (rule R2 "Flux は deploy/k8s/coordinator-prepare を別の Kustomization(wait: true)で当て、deploy/k8s/coordinator の Kustomization は dependsOn でその Kustomization が同じ revision で Ready(Job が終わった)になるまで当たらない。Job が失敗すれば coordinator は古い版のまま動き続ける。Job は ttlSecondsAfterFinished を持たない — TTL が消すと Flux が次の照合で作り直すので、古い版の Job は名の版が変わって宣言から外れた時に Flux の prune が消す(組んだ root と bytecode の保存先は hostPath に残る)。Job は Flux の印 kustomize.toolkit.fluxcd.io/force: Enabled を持つ — Job の template は変えられないので、版を変えずに template を変える commit は当てが断られて coordinator が当たらなくなる。印があれば Flux は断られた Job を消して作り直す(組み済みの root なら秒で終わる)。")
     (rule R3 "版の定義元は coordinator/coordinator.yaml の WORKER_DOEFF_COMMIT の行 1 つ。Job の env WORKER_DOEFF_COMMIT と名の末尾は coordinator-prepare.yaml に書かず、coordinator-prepare/ の組み立ての時に ../coordinator を読んで replacements で写す(読んだ coordinator の物は local-config の印で出力から外し、この Kustomization は R5 の PriorityClass と Job だけを当てる)。版上げの commit は coordinator.yaml の 1 行だけを変える — 揃える約束を検に頼まない(着地の列は検を走らせない)。")
     (rule R4 "組む Job の期限は Pod の template の spec.activeDeadlineSeconds(1200 秒 — 1 回の試しの止まりを切る)が持ち、Job の spec は activeDeadlineSeconds を持たない。置けない間(node の空きが足りない Pending)は Job は失敗せずに待ち、置けた時に組み終えて prepare の Kustomization が人の手なしに Ready に戻る。Job が失敗するのは組めない時(非 0 で終わる・試しが期限を越える)だけ。")
     (rule R5 "組む Job と Deployment coordinator は PriorityClass doeff-cluster-coordinator(値 1000 万・PreemptLowerPriority・globalDefault false)を持ち、node の memory の空きが足りない時は既定の 0 の Pod を追い出して置かれる。class は coordinator-prepare/ が持つ(Flux が coordinator/ より先に当てる — coordinator/ が持つと、初めて当てる時に Job の Pod が無い class を名指して作られず、coordinator/ の Kustomization はその Job を待つので互いを待って止まる)。")]
  :laws
    [(law the-prepare-job-builds-the-coordinators-version
       :statement "for_all coordinator.yaml の版の行の値 v: coordinator-prepare/ を組み立てた物の役 prepare の Job はちょうど 1 つ ∧ その WORKER_DOEFF_COMMIT = v ∧ その名 = coordinator-prepare- + v"
       :counterexamples
         [(counterexample "coordinator の版だけを上げ、Job の版を前の版のまま残す — Flux は Job を変えずに Ready と見て coordinator を当て、新しい Pod がまた中で組む(止まる間が戻る・10-12 18:37 の a53f417cc)")
          (counterexample "Job の env だけを上げて名を変えない — Job の template は変えられないので Flux の当てが断られ、coordinator が当たらない")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version"
                     "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-version-mismatches-are-named"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_prepare_job_follows_a_bump_of_the_coordinator_line_alone"]
       :wiring "配線済み(2026-10-12)。2026-10-16 に版を写す形へ替えた — 守るのは組み立てで、検は形が崩れた時の知らせ")
     (law the-prepare-job-shares-only-the-root-with-the-coordinator
       :statement "for_all 役 prepare の Job J と Deployment coordinator C: node(J) = node(C) ∧ hostPath(J の work) = hostPath(C の work) ∧ J の mount = {/work} ∧ J は ServiceAccount の token と envFrom を持たない ∧ J は ttlSecondsAfterFinished を持たない"
       :counterexamples
         [(counterexample "別の path(例 /prewarm/coordinator)で組む — 完成の印を持つ root の venv の python が coordinator の Pod で見つからず起動できない(上に載る系の DaemonSet の頭の註)")
          (counterexample "ttlSecondsAfterFinished で終わった Job を消す — Flux が次の照合で作り直し、組み直しの Pod を起こし続ける")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_root_is_prepared_by_one_job_beside_it"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_job_is_not_removed_by_ttl"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_builds_alone_with_only_the_job_and_its_priority_class"]
       :wiring "配線済み(2026-10-12)。Flux の順(R2 の wait と dependsOn)は上に載る系の repo の Flux の宣言が持ち、この repo の検の外")
     (law the-prepare-job-waits-while-it-cannot-be-placed
       :statement "for_all 役 prepare の Job J: J の spec は activeDeadlineSeconds を持たない ∧ J の Pod の template の spec.activeDeadlineSeconds = 1200"
       :counterexamples
         [(counterexample "期限を Job の spec に置く — 置けずに Pending の間も数え、Pod が一度も走らずに Failed になる。Job の名は版で決まり Flux は Failed の Job を作り直さないので、次の版上げか人の手まで coordinator の Kustomization が当たらない(2026-11-02)")
          (counterexample "期限を外す — 組みが止まった(hang)時に Job が終わらず、coordinator は古い版のまま、prepare の Kustomization は失敗の形を持たずに待ち続ける")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_job_waits_while_unplaced_and_cuts_a_hang_of_its_run"]
       :wiring "配線済み(2026-11-02)")
     (law the-coordinator-and-its-prepare-job-outrank-the-default-pods
       :statement "for_all 役 prepare の Job J と Deployment coordinator C: priorityClassName(J) = priorityClassName(C) = doeff-cluster-coordinator ∧ 0 < value(その class) < 10 億 ∧ preemptionPolicy = PreemptLowerPriority ∧ globalDefault = false ∧ その class は coordinator-prepare/ だけが持つ"
       :counterexamples
         [(counterexample "class を持たない(既定の 0)— atlas の他の Pod と同じ値なので scheduler は追い出せる相手を持たず、空きが 512Mi を切ると組む Job が置けない(2026-11-02 の空き 359Mi)")
          (counterexample "class を coordinator/ に置く — 初めて当てる時に組む Job の Pod が無い class を名指して作られず、coordinator/ の Kustomization はその Job の終わりを待つので、2 つが互いを待って止まる")
          (counterexample "globalDefault を true にする — 一般の Pod も同じ値になり、追い出せる相手が居なくなる")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_and_its_prepare_job_outrank_the_default_pods_on_their_node"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_the_priority_class_is_applied_before_the_pods_that_name_it"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_builds_alone_with_only_the_job_and_its_priority_class"]
       :wiring "配線済み(2026-11-02)")
     (law a-prepare-job-changed-at-the-same-version-is-recreated
       :statement "for_all 役 prepare の Job J: J の annotations の kustomize.toolkit.fluxcd.io/force = Enabled"
       :counterexamples
         [(counterexample "印を持たずに、版を変えずに Job の template を変える(例: 期限を Pod の spec へ移す commit そのもの)— Flux の当てが immutable で断られ、prepare の Kustomization が Ready にならず、coordinator が次の版上げまで当たらない")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_a_prepare_job_changed_at_the_same_version_is_recreated_not_refused"]
       :wiring "配線済み(2026-11-02)")]
  :enforcement
    [(deftest test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version
       ;; 今の宣言を Flux と同じく kubectl kustomize で組み立て、組む Job の版と名が coordinator の版と合う(R3)。
       (val k8s (/ (. (Path __file__) parent parent parent) K8S))
       (val kubectl (shutil.which "kubectl"))
       (assert (is-not kubectl None) "kubectl が無い — 宣言を組み立てて確かめられない(この検は飛ばさない)")
       (setv [coordinators jobs]
         (lfor #(part kind) [#("coordinator" "Deployment") #("coordinator-prepare" "Job")]
               :setv done (subprocess.run [kubectl "kustomize" (str (/ k8s part))] :capture-output True :text True :timeout 25 :check False)
               (do (assert (= done.returncode 0) (+ "kubectl kustomize " part " が失敗した: " done.stderr))
                   (lfor doc (yaml.safe-load-all done.stdout) :if (and (is-not doc None) (= (get doc "kind") kind)) doc))))
       (assert (= (len coordinators) 1) coordinators)
       (val problems (run (version-mismatches (run (commit-of (get coordinators 0))) jobs)))
       (assert (= problems [])
               (+ "組む Job の版が coordinator の版と合わない(ADR-DOE-CLUSTER-003 R3 — Job の版は coordinator-prepare/ の replacements が "
                  "coordinator.yaml の版の行から写す): " (str problems))))
     (deftest test-adr-doe-cluster-003-version-mismatches-are-named
       ;; 反例の 2 つ(coordinator だけを上げた宣言・Job の env だけを上げた宣言)を、食い違いの判じが名指して断る。
       (val old "475ee1e5d7d5894f6ba4c73907be57b057ea70e9")
       (val new "a1d17b0dd3047d1111f442e5a1ba4348ea3eab05")
       (setv [matching coordinator-only env-only]
         (lfor #(name commit) [#((+ "coordinator-prepare-" new) new) #((+ "coordinator-prepare-" old) old)
                               #((+ "coordinator-prepare-" old) new)]
               {"metadata" {"name" name}
                "spec" {"template" {"spec" {"containers" [{"env" [{"name" "WORKER_DOEFF_COMMIT" "value" commit}]}]}}}}))
       (assert (= (run (version-mismatches new [matching])) []))
       (assert (= (len (run (version-mismatches new [coordinator-only]))) 2))
       (assert (= (run (version-mismatches new [env-only]))
                  [(.format "Job の名 coordinator-prepare-{} が coordinator-prepare-{} でない" old new)]))
       (assert (= (run (version-mismatches new [])) ["組む Job がちょうど 1 つ無い(0 個)"])))]
  :plans [])
