;;; CallJob(job 向けの effect)の契約テストの解釈器(composition root)— 同じ契約の Program を、handler だけ替えて走らせる。
;;;
;;;   job-call-handover  模擬: job-call-handover が要求を試しの API notes の答えの 1 点(notes-answer)へ直に渡す
;;;   job-call-sender    本物: job-call-sender が宛先の環境変数 NOTES-ENVIRON の URL へ HttpRequest を 1 本出し、その HttpRequest には
;;;                      served-notes が HTTP の待ち受けと同じく要求の行を読み直して(path を %-decode・query を名と値の組に分ける・根を外す)
;;;                      同じ答えの 1 点へ渡して答える(同じ process の中 — socket は開かない)
;;;
;;; 両方とも、表に無い API の CallJob は外側の答え手 elsewhere-answer が答える(外側へ回す性質を同じ Program で見る)。
;;; 試しの API notes(根 NOTES-ROOT): GET <根>/items/<鍵> は 200 と、受けた鍵・query の組・見出し X-Asker の値の JSON・POST <根>/items/<鍵> は
;;; 202 と、受けた本文の JSON・ほかの path は 404。答えの見出し X-Notes-Version に版を載せる。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import urllib.parse [parse-qsl unquote :as url-unquote urlsplit])
(import doeff [Program with_handlers])
(import doeff_core_effects [Ask])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.intent.job_call_model [CallJob JobAnswer JobField JobRequest JobUnreachable])
(import doeff_cluster.shared.protocol.job_call_handlers [Counterpart JobDestination job-call-handover job-call-sender])

;; 試しの API の名・口の根・宛先の環境変数の名と値(宛先の URL — scheme・host・port だけ)。
(val NOTES-API "notes")
(val NOTES-ROOT "/v1/notes")
(val NOTES-ENVIRON "NOTES_URL")
(val NOTES-URL "http://notes.test:8080")
;; 答えの見出しの名と値・JSON の content-type。
(val VERSION-HEADER "X-Notes-Version")
(val VERSION "v7")
(val ASKER-HEADER "X-Asker")
(val JSON-TYPE "application/json; charset=utf-8")
;; 表に無い API の CallJob に外側が返す答え(外側へ回ったかを読む印)。
(val ELSEWHERE (JobUnreachable :reason "外側の答え手が答えた"))
;; items の path の頭。
(val ITEMS "/items/")


(defk json-answer [status value]
  {:pre [(: status int) (: value dict)] :post [(: % JobAnswer)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "試しの API の答え 1 つを、JSON の本文と版の見出しで作るため。"
  (JobAnswer :status status :content-type JSON-TYPE :body (.encode (json.dumps value :ensure-ascii False :sort-keys True) "utf-8")
             :headers #((JobField :name VERSION-HEADER :value VERSION))))


(defk notes-answer [request]
  {:pre [(: request JobRequest)] :post [(: % JobAnswer)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "試しの API notes の答えの 1 点(頭の註)— 送る側も直に渡す側も、要求をここへ渡す。"
  (val asker (next (gfor field request.headers :if (= (.lower field.name) (.lower ASKER-HEADER)) field.value) None))
  (cond (not (.startswith request.path ITEMS))
        (! (json-answer 404 {"missing" request.path}))
        (= request.method "GET")
        (! (json-answer 200 {"key" (cut request.path (len ITEMS) None) "query" (lfor field request.query [field.name field.value]) "asker" asker}))
        (= request.method "POST")
        (! (json-answer 202 {"key" (cut request.path (len ITEMS) None) "body" (.decode (or request.body b"") "utf-8")}))
        True
        (! (json-answer 405 {"method" request.method}))))


(defk request-of-line [method url headers body]
  {:pre [(: method str) (: url str) (: headers (| dict None)) (: body (| bytes str dict None))] :post [(: % JobRequest)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "HttpRequest の要求の行を、HTTP の待ち受けが読むのと同じく API の要求に読み直すため(path は %-decode して根を外す・query は名と値の組に
   分ける — 空の値も残す・見出しはそのまま)。"
  (val parts (urlsplit url))
  (val path (url-unquote parts.path))
  (when (not (.startswith path NOTES-ROOT))
    (raise (ValueError (.format "試しの API の根の外の要求: {}" url))))
  (JobRequest :method method :path (cut path (len NOTES-ROOT) None)
              :query (tuple (gfor #(name value) (parse-qsl parts.query :keep-blank-values True) (JobField :name name :value value)))
              :headers (tuple (gfor #(name value) (.items (or headers {})) (JobField :name name :value value)))
              :body (if (isinstance body str) (.encode body "utf-8") body)))


(defhandler served-notes
  ;; 本物の側の HttpRequest に、試しの API の待ち受けの代わりに答えるため(頭の註 — 宛先の host の要求だけ・ほかは外側へ)。
  (HttpRequest [method url headers body]
    (if (!= (. (urlsplit url) netloc) (. (urlsplit NOTES-URL) netloc))
        (reperform effect)
        (do (<- request JobRequest (request-of-line method url headers body))
            (<- answer JobAnswer (notes-answer request))
            (resume (HttpResponse :status answer.status
                                  :headers (dict (+ #(#("Content-Type" answer.content-type)) (tuple (gfor field answer.headers #(field.name field.value)))))
                                  :content answer.body :text (.decode answer.body "utf-8") :url url :elapsed-seconds 0.0))))))


(defhandler notes-environ
  ;; 本物の側の宛先の環境変数の読みに答えるため(名 NOTES-ENVIRON だけ・ほかは外側へ)。
  (Ask [key]
    (if (= key NOTES-ENVIRON)
        (resume NOTES-URL)
        (reperform effect))))


(defhandler elsewhere-answer
  ;; 表に無い API の CallJob に、両方の解釈器で同じ印の答えを返すため(頭の註)。
  (CallJob [api request]
    (resume ELSEWHERE)))


(defk under-handover [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "模擬の組: 相手の表に notes の答えの 1 点を置いた job-call-handover の下で program を走らせるため。"
  (<- answer (with_handlers [elsewhere-answer (job-call-handover #((Counterpart :api NOTES-API :answer notes-answer)))] program))
  answer)


(defk under-sender [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の組: 宛先の表に notes を置いた job-call-sender の下で program を走らせるため(HttpRequest と宛先の読みは served-notes・notes-environ)。"
  (<- answer (with_handlers [elsewhere-answer served-notes notes-environ
                             (job-call-sender #((JobDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON)))]
               program))
  answer)


(val INTERPRETERS {"job-call-handover" under-handover
                   "job-call-sender" under-sender})
