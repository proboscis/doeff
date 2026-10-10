;; 新しい worker が古い coordinator の heartbeat の返事を読んだ時(配備の順の誤り — card ki-01d1f8cb0391・#3767)。
;;   doeff 7decfe5f7 から、Program の job の行は送り手の版 versions を必ず持つ(coordinator が宣言の行の run.versions を載せ、worker は
;;   cache の file を (sha・版) ごとに決める)。古い coordinator の返事の job の行には versions が無い。配備は coordinator を先に上げるが、
;;   順を誤って worker を先に上げた時に、その拍は黙って止まってはいけない。直す前は、拍が ready の file を書いた後に declared-job-spec の
;;   (get job "versions") が KeyError で落ち、「名乗れない: KeyError('versions')」の 1 行だけで、Pod は Ready のまま job も task も受けなかった。
;;   直した後は、版の欄の無い Program の job の行を DeclaredReplyMalformed で名乗り(欄と、coordinator を先に上げる事)、ready の file を
;;   書かない(拍の註「読めない返事で版を入れ替えない・ready の file は書かない」のとおり)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pathlib [Path])
(import httpx)
(import doeff_cluster.worker.intent.worker_model [DesiredUnreadable])
(import tests.link_rig [LinkRig])


(val PROGRAM-SHA (* "a" 64))
(val OLD-COORDINATOR-ROW {"name" "web" "entry" "m" "revision" "r2" "program" PROGRAM-SHA})
(val NEW-COORDINATOR-ROW (| OLD-COORDINATOR-ROW {"versions" {"python" "3.14.3"}}))


(defk link-answering [tmp-path row]
  {:pre [(: tmp-path Path) (: row dict)] :post [(: % LinkRig)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "heartbeat に、job の行 row 1 本の返事(drain 中でない)で答え、/programs には 404 で答える coordinator を相手にする worker の口。"
  (val handle (fn #^ httpx.Response [#^ httpx.Request request]
                (if (= request.url.path "/heartbeat")
                    (httpx.Response 200 :json {"jobs" [row] "draining" False "tasks" [] "warm" []})
                    (httpx.Response 404 :json {"error" "no such program"}))))
  (LinkRig "http://coord" "w" #() 1 0 20000 :task-dir (str (/ tmp-path "tasks")) :transport (httpx.MockTransport handle)))


(deftest test-a-program-job-row-without-versions-is-refused-by-name-and-leaves-the-worker-not-ready [tmp-path monkeypatch]
  ;; 失敗ケース(配備の順の誤り): 古い coordinator の返事(Program の job の行に versions が無い)を受けた拍は、ready の file を書かず、
  ;; 「読めない」(DesiredUnreadable)の理由に DeclaredReplyMalformed と欄の名 versions を出し、返事の job を宣言にしない。
  (val ready (/ tmp-path "doeff-worker-ready"))
  (.setenv monkeypatch "DOEFF_WORKER_READY_FILE" (str ready))
  (val link (! (link-answering tmp-path OLD-COORDINATOR-ROW)))
  (val seen (.poll link))
  (assert (isinstance seen DesiredUnreadable) seen)
  (assert (in "DeclaredReplyMalformed" seen.reason) seen.reason)
  (assert (in "versions" seen.reason) seen.reason)
  (assert (in "coordinator" seen.reason) seen.reason)
  (assert (not (.exists ready)) "版の欄の無い返事で ready の file を書いた")
  (assert (= link.state.last-jobs #()) link.state.last-jobs))


(deftest test-a-program-job-row-with-versions-is-taken-and-the-worker-is-ready [tmp-path monkeypatch]
  ;; 対の形: 新しい coordinator の返事(Program の job の行に versions が在る)は読めて、ready の file を書き、job を宣言にする。
  (val ready (/ tmp-path "doeff-worker-ready"))
  (.setenv monkeypatch "DOEFF_WORKER_READY_FILE" (str ready))
  (val link (! (link-answering tmp-path NEW-COORDINATOR-ROW)))
  (val seen (.poll link))
  (assert (not (isinstance seen DesiredUnreadable)) seen)
  (assert (= (.read-text ready :encoding "utf-8") "ready\n"))
  (assert (= (lfor spec link.state.last-jobs spec.name) ["web"]) link.state.last-jobs)
  (assert (= (. (get link.state.last-jobs 0) versions) #(#("python" "3.14.3"))) link.state.last-jobs))
