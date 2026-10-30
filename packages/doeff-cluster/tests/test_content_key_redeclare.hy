;; 中身の鍵(宣言の行の content — card ki-81bf84ac6590): 送り手が「その job の process が読む code の全部」から計った鍵を宣言に載せると、
;; 指紋 spec-hash と worker の比べは版(revision)と実行環境の repo の commit の代わりに鍵を読む。読む code の変わらない版で宣言し直されても、
;; worker は process を起こし直さず、その版の木・root も用意しない。
;;
;; 出自: 2026-10-27 01:16〜01:50 JST に main へ 5 commit が入り(test の綴りだけ・計器の数え方だけの commit を含む)、worker は版が替わるたびに
;; 全部の service の process を退かせて起こし直した(10 回)。前の判定は「版が替わったか」で、「その job が読む code が変わったか」でなかった。
;;
;; 速い検: JobSpec の比べと指紋・宣言の行 → coordinator → 返事 → worker の運び・鍵の綴りの断り。
;; 筋書き(tests/test_handoff_deadline.hy の Sim — 本物の coordinator の判断と worker の判断・子 process の中身だけ模す):
;;   (a) 鍵の同じ版へ宣言し直す → 起こし直さず・木を用意せず・Ready のまま・動いている版は宣言の版。
;;       失敗ケース: 鍵を載せない宣言(版で判じる今までの形)は同じ宣言し直しで起こし直す — 版で判じる形に戻すと (a) が落ちる。
;;   (b) 鍵の変わった版へ宣言し直す → 今までどおり直ちに起こし直す(入れ替えの形 recreate も handoff も)。
;;   (c) 鍵の同じ版が続けて入る → 間の版の木を用意せず、鍵の変わった最新の版の木だけを用意する。
;;   (d) 鍵の欄を読まない古い worker(版の指紋を報告する)でも、coordinator は今の宣言の process と数えて Ready にする。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import dataclasses [replace])
(import pytest)
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.core.job_rules [spec-hash spec-hash-matches content-refusal])
(import doeff_cluster.shared.entry.service_build [job system-of system-declaration])
(import doeff_cluster.shared.intent.service_model [CallShape System])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.coordinator.core.cluster_policy [job-from-json job-to-json spec-of-declaration])
(import doeff_cluster.coordinator.protocol.replies [spec-json])
(import doeff_cluster.worker.protocol.declared [DeclaredReply declared-reply-of-json declared-job-spec declared-job-specs]
        doeff_cluster.worker.protocol.heartbeat [status-rows-json])
(import doeff_cluster.worker.core.policy [plan records-after statuses])
(import tests.test_handoff_deadline [Sim HANDOFF RECREATE V steps started])

(val ENV-A (json.dumps {"format" 1 "importRoots" ["app/."] "repos" [{"name" "app" "url" "https://example.invalid/app.git" "commit" (* "a" 40)}]
                        "project" {"repo" "app" "path" "." "lockSha256" (* "0" 64) "python" "3.14"}}
                       :sort-keys True :ensure-ascii False))
(val ENV-B (.replace ENV-A (* "a" 40) (* "b" 40)))
(val ENV-LOCK (.replace ENV-A (* "0" 64) (* "1" 64)))


(defk spec-at [revision content runtime-env]
  {:pre [(: revision str) (: content (| str None)) (: runtime-env (| str None))] :post [(: % JobSpec)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "版・中身の鍵・実行環境の宣言だけを変えた job の宣言を作るため。"
  (JobSpec "writer-a" "doeff_cluster.worker.entry.job_entry" #("service" "--identity" "0123456789abcdef") revision
           :runtime-env runtime-env :content content))


(deftest test-a-content-key-replaces-the-revision-and-the-commits-in-the-fingerprint
  ;; 鍵の同じ宣言は、版と repo の commit が違っても同じ(比べも指紋も hash も)。鍵・lock の sha256(実行環境の commit の外の欄)が違えば違う。
  (<- a JobSpec (spec-at "r1" "sha256:k1" ENV-A))
  (<- b JobSpec (spec-at "r2" "sha256:k1" ENV-B))
  (assert (= a b) #(a b))
  (assert (= (spec-hash a) (spec-hash b)))
  (assert (= (hash a) (hash b)))
  (assert (= (len #{a b}) 1))
  (<- other JobSpec (spec-at "r2" "sha256:k2" ENV-B))
  (assert (!= a other))
  (assert (!= (spec-hash a) (spec-hash other)))
  (<- relocked JobSpec (spec-at "r2" "sha256:k1" ENV-LOCK))
  (assert (!= a relocked) "lock の変わった実行環境は鍵が同じでも違う")
  ;; 失敗ケース: 鍵の無い宣言は今どおり版と commit で判じる(版だけ違えば違う)。
  (<- plain-1 JobSpec (spec-at "r1" None ENV-A))
  (<- plain-2 JobSpec (spec-at "r2" None ENV-A))
  (assert (!= plain-1 plain-2))
  (assert (!= (spec-hash plain-1) (spec-hash plain-2)))
  (assert (!= (spec-hash plain-1) (spec-hash a)) "鍵の在る宣言と無い宣言の指紋は混ざらない")
  ;; 比べない欄(置き場の世代)は今どおり比べない。
  (assert (= a (replace a :placement 3)))
  ;; coordinator は、鍵を読まない古い worker の版の指紋(鍵を外した宣言の指紋)も受ける — 合うのは宣言の版そのものの process だけ。
  (assert (spec-hash-matches b (spec-hash a)))
  (assert (spec-hash-matches b (spec-hash (! (spec-at "r2" None ENV-B)))))
  (assert (not (spec-hash-matches b (spec-hash (! (spec-at "r1" None ENV-A))))) "前の版の版の指紋は受けない")
  (assert (not (spec-hash-matches plain-2 (spec-hash plain-1)))))


(deftest test-the-content-key-travels-from-the-declaration-to-the-worker
  ;; 宣言の組み立て(system-declaration の :contents)→ coordinator の宣言の読み・保存の形 → heartbeat の返事 → worker の宣言。両側の指紋が同じ。
  (val program-job (job "quiet" (spec-at "r1" None None) :call (CallShape :function spec-at :args ["r1" None None] :kwargs {})
                        :replicas 1 :peak-mib 200 :settle-mib 100 :needs #{"net"}))
  (val system (system-of "lab" #(program-job)))
  (val versions (! (process-versions os.environ)))
  (val row (get (. (system-declaration system "r1" :versions versions :contents {"quiet" "sha256:k1"}) rows) 0))
  (assert (= (get row "content") "sha256:k1") row)
  (assert (not-in "content" (get (. (system-declaration system "r1" :versions versions) rows) 0)) "鍵を渡さない宣言の行は今の形")
  (val held (job-from-json row))
  (assert (= held.spec.content "sha256:k1") held.spec)
  (assert (= (get (job-to-json held) "content") "sha256:k1") "coordinator の状態に残る")
  (assert (= (job-from-json (job-to-json held)) held) "読み戻しで同じ job")
  (<- wire dict (spec-json held.spec))
  (assert (= (get wire "content") "sha256:k1") wire)
  (<- received JobSpec (declared-job-spec wire False))
  (assert (= received.content "sha256:k1") received)
  (assert (= (spec-hash received) (spec-hash held.spec)) "worker と coordinator の指紋が同じ")
  ;; 系に無い job の鍵・綴りの誤りは送る前に断り、coordinator も同じ規則で断る。
  (with [(pytest.raises ValueError)]
    (system-declaration system "r1" :versions versions :contents {"loud" "sha256:k1"}))
  (for [bad ["" "has space" (* "x" 129) 7]]
    (assert (is-not (content-refusal bad) None) bad)
    (with [(pytest.raises ValueError)]
      (system-declaration system "r1" :versions versions :contents {"quiet" bad}))
    (with [(pytest.raises ValueError)]
      (job-from-json (| row {"content" bad}))))
  (assert (is (content-refusal None) None)))


;; --- 筋書き ---------------------------------------------------------------------------------

(defk redeclared [sim declaration seconds]
  {:pre [(: sim Sim) (: declaration dict) (: seconds int)] :post [(: % Sim)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言し直して seconds 拍進めた世界を返すため。"
  (sim.redeclare declaration)
  (<- (steps sim seconds))
  sim)


(defk settled [declaration]
  {:pre [(: declaration dict)] :post [(: % Sim)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言 declaration で起こし、Ready まで進めた世界を作るため。"
  (val sim (Sim declaration))
  (<- (steps sim 12))
  (assert (= (get (sim.status) "ready") "Ready") sim.log)
  sim)


(defk running-revisions [sim]
  {:pre [(: sim Sim)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Service の status.version.running の版を読むため(宣言の道具が「今 動いている版」として読む値)。"
  (lfor r (get (get (sim.status) "version") "running") (get r "revision")))


(deftest test-a-redeclaration-at-a-revision-with-the-same-content-key-restarts-nothing
  ;; (a) recreate と handoff の両方: 鍵の同じ版へ宣言し直しても、起こし直さず(r2 の起動 0)・r2 の木も用意せず・最初の process が動き続け、
  ;; Ready のまま・版の判定は Current で、動いている版は宣言の版 r2(読む code が同じ)。
  (for [shape [RECREATE HANDOFF]]
    (<- sim Sim (settled (| shape {"content" "sha256:k1"})))
    (val first (get (sim.live "r1") 0))
    (<- (redeclared sim (| shape {"revision" "r2" "content" "sha256:k1"}) 20))
    (assert (= (! (started sim "r2")) 0) sim.starts)
    (assert (not-in "r2" sim.codes) "間の版の木を用意しない")
    (assert (= (lfor p sim.processes :if (is p.exit-code None) p.pid) [first.pid]) sim.processes)
    (assert (= (get (sim.status) "ready") "Ready") (sim.status))
    (assert (= (get (get (sim.status) "version") "state") "Current") (sim.status))
    (assert (= (! (running-revisions sim)) ["r2"]) (sim.status))
    (assert (all (gfor #(_ live) (cut sim.log 12 None) (= live 1))) "宣言し直しの間もずっと 1 つの process が動いている")))


(deftest test-a-redeclaration-without-a-content-key-restarts-as-before
  ;; 失敗ケース: 鍵を載せない宣言は、版だけ替わっても今までどおり起こし直す(r2 の process が起き、r1 は止まる)。判定を版に戻すと、上の (a) が
  ;; この形になって落ちる。
  (for [shape [RECREATE HANDOFF]]
    (<- sim Sim (settled shape))
    (<- (redeclared sim (| shape {"revision" "r2"}) 20))
    (assert (= (! (started sim "r2")) 1) sim.starts)
    (assert (= (sim.live "r1") []) sim.processes)))


(deftest test-a-redeclaration-with-a-changed-content-key-restarts-at-once
  ;; (b) 鍵の変わった版は今までどおり直ちに起こし直す(配備の直しは main に入ったらすぐ当てる決まり)。
  (for [shape [RECREATE HANDOFF]]
    (<- sim Sim (settled (| shape {"content" "sha256:k1"})))
    (<- (redeclared sim (| shape {"revision" "r2" "content" "sha256:k2"}) 20))
    (assert (= (! (started sim "r2")) 1) sim.starts)
    (assert (= (sim.live "r1") []) sim.processes)
    (assert (= (get (sim.status) "ready") "Ready") (sim.status))
    (assert (= (! (running-revisions sim)) ["r2"]) (sim.status))))


(deftest test-revisions-landing-in-a-row-prepare-only-the-latest-changed-tree
  ;; (c) 鍵の同じ版 r2・r3 が続けて入り、その後に鍵の変わった r4 が入る: r2・r3 の木は用意せず、r4 の木だけを用意して r4 で起こす。
  (<- sim Sim (settled (| RECREATE {"content" "sha256:k1"})))
  (<- (redeclared sim (| RECREATE {"revision" "r2" "content" "sha256:k1"}) 2))
  (<- (redeclared sim (| RECREATE {"revision" "r3" "content" "sha256:k1"}) 2))
  (<- (redeclared sim (| RECREATE {"revision" "r4" "content" "sha256:k2"}) 20))
  (assert (= (sorted sim.codes) ["r1" "r4"]) sim.codes)
  (assert (= (lfor s sim.starts (get s 1)) ["r1" "r4"]) sim.starts)
  (assert (= (get (sim.status) "ready") "Ready") (sim.status)))


;; 鍵の欄を読まない古い worker: 返事の job の行から content を落としてから読む(欄の無い worker の読みと同じ)。
(defk legacy-step [sim]
  {:pre [(: sim Sim)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "鍵を読まない古い worker の本物の判断で模擬の世界を 1 秒進めるため(返事の job の content を捨てて読む — 版の指紋で起こし、版の指紋を報告する)。"
  (+= sim.now 1000)
  (val actions (! (plan sim.now sim.desired (sim.world) sim.records sim.policy)))
  (for [a actions] (sim.apply a))
  (setv sim.records (! (records-after sim.now sim.records actions sim.policy)))
  (<- rows tuple (status-rows-json (tuple (! (statuses sim.now sim.desired (sim.world) sim.records sim.policy)))))
  (val reply (sim.call "POST" "/heartbeat" {"name" "zeus" "provides" ["net"] "backgroundRoom" 10 "taskReserve" 0 "versions" V "statuses" (list rows)}
                       :actor None))
  (val stripped (| reply {"jobs" (lfor j (get reply "jobs") (dfor #(k v) (.items j) :if (!= k "content") k v))}))
  (<- declared DeclaredReply (declared-reply-of-json stripped))
  (<- desired tuple (declared-job-specs declared))
  (setv sim.desired desired)
  (sim.processes-tick)
  (setv sim.log (+ sim.log [#(sim.now (len (lfor p sim.processes :if (is p.exit-code None) p)))]))
  None)


(deftest test-an-old-worker-that-ignores-the-content-key-still-becomes-ready
  ;; (d) 配備の順(coordinator を先に上げる)の間: 鍵を読まない worker は版の指紋で起こし、版の指紋を報告する。coordinator は今の宣言の
  ;; process と数えて Ready にする。鍵の同じ版への宣言し直しでは、古い worker は今までどおり起こし直し、新しい版でまた Ready になる。
  (val sim (Sim (| RECREATE {"content" "sha256:k1"})))
  (for [_ (range 12)] (<- (legacy-step sim)))
  (assert (= (get (sim.status) "ready") "Ready") (sim.status))
  (sim.redeclare (| RECREATE {"revision" "r2" "content" "sha256:k1"}))
  (for [_ (range 20)] (<- (legacy-step sim)))
  (assert (= (! (started sim "r2")) 1) sim.starts)
  (assert (= (get (sim.status) "ready") "Ready") (sim.status))
  (assert (= (! (running-revisions sim)) ["r2"]) (sim.status)))
