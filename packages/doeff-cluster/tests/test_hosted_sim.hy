;; 預かりの process の模擬の答え手(_sim/hosted_processes の held-hosted-processes)を、package の根の公開名だけで確かめる
;; (上に載る系が、公開名で頼みと答え手を組み、模擬で検を回す)。
;;
;; 筋書き: 起こす → 一覧に在る → 止める(止めた後の姿)→ 一覧から消える。知らない id の止めと、口に届かない世界の起こしは断りの値。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "program"})
(import doeff [run with-handlers])
(import doeff_core_effects [EnvEntry])
(import doeff_cluster [HostedLaunch HostedLog HostedProcess HostedRefused LaunchHosted ListHosted StopHosted held-hosted-processes
                       hosted-client hosted-socket-path])

(val LAUNCH (HostedLaunch :argv #("python" "-S" "holder.py") :owner "service-a" :env #((EnvEntry :name "A" :value "1")) :layer "clis"))


(defk launched-then-stopped []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "起こし・一覧・止め・一覧・知らない id の止めを順に頼み、答えを並べるため。"
  (<- started (| HostedProcess HostedRefused) (LaunchHosted LAUNCH))
  (<- seen tuple (ListHosted))
  (<- stopped (| HostedProcess HostedRefused) (StopHosted started.id))
  (<- after tuple (ListHosted))
  (<- unknown (| HostedProcess HostedRefused) (StopHosted started.id))
  #(started seen stopped after unknown))


(deftest test-held-hosted-processes-launch-list-stop []
  (val log (HostedLog :started-ms 7))
  (val answers (run (with-handlers [(held-hosted-processes log)] (launched-then-stopped))))
  (setv #(started seen stopped after unknown) answers)
  (assert (isinstance started HostedProcess) (repr started))
  (assert (= #(started.owner started.layer started.ended started.started-ms) #("service-a" "clis" False 7)) (repr started))
  (assert (= log.launches #(LAUNCH)) (repr log.launches))
  (assert (= seen #(started)) (repr seen))
  (assert (and (isinstance stopped HostedProcess) (= stopped.id started.id) stopped.ended) (repr stopped))
  (assert (= after #()) (repr after))
  (assert (isinstance unknown HostedRefused) (repr unknown)))


(deftest test-held-hosted-processes-down-refuses-the-launch []
  (val log (HostedLog :down True))
  (val answer (run (with-handlers [(held-hosted-processes log)] (LaunchHosted LAUNCH))))
  (assert (isinstance answer HostedRefused) (repr answer))
  (assert (= log.launches #()) (repr log.launches)))


(deftest test-hosted-client-without-a-socket-refuses-by-value []
  ;; job の中の答え手は、worker の口の path が無ければ(hosted-socket-path の None)全部を断りの値で答える — 頼み手は今の形へ戻れる。
  (val answer (run (with-handlers [(hosted-client None)] (LaunchHosted LAUNCH))))
  (assert (isinstance answer HostedRefused) (repr answer))
  (assert (callable hosted-socket-path)))
