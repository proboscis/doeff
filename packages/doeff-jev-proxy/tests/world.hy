;;; 検の世界 — 代理の答え手の組(置き場・同時の問いのまとめ・身元の名簿)を、検ごとの一時の置き場の file と台本の本物の Jev
;;; (scripted-upstream-handler)で組む。本番の組(main.hy)と違うのは、本物の Jev の答え手と HTTP の実体だけ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import os)
(import tempfile)
(import threading)
(import time)
(import doeff [run with_handlers])
(import doeff_core_effects [await_handler try_handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.principals [Roster])
(import doeff_jev_proxy.values [Header ProxyRequest ProxyReply UpstreamReply])
(import doeff_jev_proxy.effects [AskJev PrepareStore])
(import doeff_jev_proxy.handlers [sqlite-store-handler roster-handler single-flight-handler Flights])
(import doeff_jev_proxy.main [proxy-runner])

(val OPERATOR-TOKEN "token-of-operator")
(val WORKER-TOKEN "token-of-worker")


(defrecord Script
  "台本の本物の Jev: calls = 届いた本文の列 / answer = 本文 → 答え / delay = 答える前に待つ秒(同時の問いの検)。"
  (#^ list calls)
  (#^ Callable answer)
  (setv #^ float delay 0.0))


(defhandler scripted-upstream-handler [script]
  ;; 引数に残す理由: 台本は検の本体が届いた本文を読むため、世界の外から渡して共有する
  (AskJev [body]
    (.append script.calls body)
    (when (> script.delay 0) (time.sleep script.delay))
    (resume (script.answer body))))


(defrecord World
  "検の世界 1 つ: run = ProxyRequest → ProxyReply / script = 台本の本物の Jev / path = 置き場の file。"
  (#^ Callable run)
  (#^ Script script)
  (#^ str path))


(defk world-handlers [path script]
  {:pre [(: path str) (: script Script)] :post [(: % list)]}
  "置き場の file と台本の上の答え手の組を作るため(同時の問いの表は組ごとに 1 つ — 本番の起動 1 回と同じ)。"
  (val digest (fn [token] (.hexdigest (hashlib.sha256 (.encode token "utf-8")))))
  [(sqlite-store-handler path)
   (scripted-upstream-handler script)
   (roster-handler (Roster (FrozenMap {"operator" (digest OPERATOR-TOKEN) "worker" (digest WORKER-TOKEN)})) (frozenset ["operator"]))
   (single-flight-handler (Flights :table {} :lock (threading.Lock)))])


(defk open-world [answer delay]
  {:pre [(: answer Callable) (: delay float)] :post [(: % World)]}
  "一時の置き場の file(TMPDIR の下)を作り、台本の答え answer で世界を組むため。"
  (val path (os.path.join (tempfile.mkdtemp :prefix "jev-proxy-test-") "answers.sqlite"))
  (val script (Script :calls [] :answer answer :delay delay))
  (<- handlers (world-handlers path script))
  (run (scheduled (with_handlers (+ [(await_handler) try_handler] handlers) (PrepareStore))))
  (World :run (proxy-runner handlers) :script script :path path))


(defk reopen-world [world]
  {:pre [(: world World)] :post [(: % World)]}
  "同じ置き場の file の上に答え手の組を作り直すため(Pod の入れ替えと同じ)。"
  (<- handlers (world-handlers world.path world.script))
  (World :run (proxy-runner handlers) :script world.script :path world.path))


(defk json-bytes [document]
  {:pre [(: document dict)] :post [(: % bytes)]}
  "JSON の本文を作るため。"
  (.encode (json.dumps document :ensure-ascii False) "utf-8"))


(defk jev-answers [probability]
  {:pre [(: probability float)] :post [(: % Callable)]}
  "いつも同じ確率を答える台本を作るため(model を名乗る)。"
  (<- body (json-bytes {"answers" {"q" {"noul" probability}} "model" "jev-1.13.0"}))
  (fn [_] (UpstreamReply :status 200 :body body)))


(defk question [source model]
  {:pre [(: source str) (: model str)] :post [(: % bytes)]}
  "linter が撃つのと同じ形の問いの本文を作るため。"
  (<- body (json-bytes {"state" {"definition" {"name" "f" "kind" "defk" "file" "a.hy" "source" source}}
                        "questions" {"q" {"type" "noul" "instructions" "Does it?" "criteria" {"true" "yes" "false" "no"}}}
                        "model" model}))
  body)


(defk request-of [method path body token cache-control]
  {:pre [(: method str) (: path str) (: body bytes) (: token (| str None)) (: cache-control (| str None))] :post [(: % ProxyRequest)]}
  "要求を組むため(token が None なら Authorization を付けない・cache-control が None なら名乗らない)。"
  (val headers (+ [(Header :name "content-type" :value "application/json")]
                  (if (is token None) [] [(Header :name "authorization" :value (+ "Bearer " token))])
                  (if (is cache-control None) [] [(Header :name "cache-control" :value cache-control)])))
  (ProxyRequest :method method :path path :headers (tuple headers) :body body))


(defk header [reply name]
  {:pre [(: reply ProxyReply) (: name str)] :post [(: % (| str None))]}
  "答えの見出しの値を読むため(無ければ None)。"
  (next (gfor h reply.headers :if (= h.name name) h.value) None))


(defk stats-of [world]
  {:pre [(: world World)] :post [(: % dict)]}
  "計器の JSON を operator の token で読むため。"
  (<- request (request-of "GET" "/v1/stats" b"" OPERATOR-TOKEN None))
  (json.loads (.decode (. (world.run request) body) "utf-8")))
