;; 代理の反例: 鍵の正規化・覚えている時だけの印・本物の Jev に届かない時・同じ鍵の同時の問いを 1 回にまとめる・身元と管理者・
;; 問い直し・答えた model の版が変わった時・置き場が起動をまたいで残ること・覚えている時だけの問いの束・呼び手と同じ鍵の見本。
;; 本物の Jev は台本(tests/world.hy)。
(require doeff-hy.macros [deftest defk <- val var])
(import contextlib [closing])
(import json)
(import pathlib [Path])
(import sqlite3)
(import threading)
(import doeff_jev_proxy.values [ProxyReply UpstreamReply UpstreamUnreachable])
(import doeff_jev_proxy.key [BadRequest normalize-request])
(import tests.world [OPERATOR-TOKEN WORKER-TOKEN World open-world reopen-world spied-world jev-answers question json-bytes
                     request-of header stats-of])


(defk asking [world body cache-control]
  {:pre [(: world World) (: body bytes) (: cache-control (| str None))] :post [(: % ProxyReply)]}
  "operator の token で問いを 1 つ撃つため。"
  (<- request (request-of "POST" "/v1/systemone" body OPERATOR-TOKEN cache-control))
  (world.run request))


(deftest test-key-ignores-key-order-and-spacing-but-not-meaning
  ;; 同じ問いを鍵の順と空白を変えて綴っても鍵は同じ。model・source の中の空白・model の省略(既定の名と同じ)は区別どおり。
  (<- body (question "(defk f [x] x)" "jev-latest"))
  (<- a (normalize-request body))
  (<- b (normalize-request (.encode (+ "{ \"model\" : \"jev-latest\",\n  \"questions\": {\"q\": {\"criteria\": {\"false\": \"no\", \"true\": \"yes\"},"
                                       " \"instructions\": \"Does it?\", \"type\": \"noul\"}},"
                                       " \"state\": {\"definition\": {\"source\": \"(defk f [x] x)\", \"kind\": \"defk\", \"file\": \"a.hy\", \"name\": \"f\"}} }")
                                    "utf-8")))
  (assert (= a.key b.key) "鍵の順・空白・改行だけの違いは同じ鍵")
  (<- other-model (normalize-request (! (question "(defk f [x] x)" "jev-1.13.0"))))
  (assert (!= a.key other-model.key) "model が違えば鍵が違う")
  (<- other-source (normalize-request (! (question "(defk f [x]  x)" "jev-latest"))))
  (assert (!= a.key other-source.key) "文字列の中の空白は意味の内 — 鍵が違う")
  (val without-model (json.loads body))
  (del (get without-model "model"))
  (<- defaulted (normalize-request (! (json-bytes without-model))))
  (assert (= a.key defaulted.key) "model を省いた問いは既定の model(jev-latest)を名指した問いと同じ鍵")
  (<- broken (normalize-request b"{\"questions\": "))
  (assert (isinstance broken BadRequest) "JSON でない本文は BadRequest")
  (<- empty (normalize-request (! (json-bytes {"state" {} "questions" {}}))))
  (assert (isinstance empty BadRequest) "questions が空の本文は BadRequest"))


(deftest test-only-if-cached-never-calls-jev
  ;; 覚えている時だけの印(Cache-Control: only-if-cached)は、覚えが無ければ 504 で答え、本物の Jev を 1 度も呼ばない。
  (<- world (open-world (! (jev-answers 0.9)) 0.0))
  (<- body (question "(defk g [] 1)" "jev-latest"))
  (<- peek (asking world body "only-if-cached"))
  (assert (= #(peek.status (! (header peek "x-jev-proxy"))) #(504 "absent")) peek)
  (assert (= (len world.script.calls) 0) "覚えている時だけの問いは Jev を呼ばない")
  (<- first (asking world body None))
  (assert (= #(first.status (! (header first "x-jev-proxy"))) #(200 "miss")))
  (<- second (asking world body None))
  (assert (= #(second.status (! (header second "x-jev-proxy"))) #(200 "hit")))
  (assert (= second.body first.body) "2 回目は 1 回目と同じ本文")
  (<- peek-again (asking world body "max-age=0, only-if-cached"))
  (assert (= #(peek-again.status (! (header peek-again "x-jev-proxy"))) #(200 "hit")))
  (assert (= (len world.script.calls) 1) "本物の Jev は最初の 1 回だけ")
  (<- stats (stats-of world))
  (assert (= (get stats "events") {"hit" 1 "miss" 1 "peek-hit" 1 "peek-miss" 1}) stats)
  (assert (= #((get stats "requests") (get stats "saved_calls") (get stats "upstream_calls")) #(4 1 1)) stats)
  (assert (= (get stats "hit_ratio") 0.5) stats))


(deftest test-unreachable-or-failing-jev-is-not-remembered
  ;; 本物の Jev に届かない(502)・5xx・答えとして読めない 200 は覚えない — 次の問いはもう一度本物の Jev へ行く。
  (val replies [(UpstreamUnreachable :detail "ConnectError: refused")
                (UpstreamReply :status 503 :body b"{\"error\":\"busy\"}")
                (UpstreamReply :status 200 :body b"not json")
                (UpstreamReply :status 200 :body b"{\"answers\":{\"q\":{\"noul\":0.1}},\"model\":\"jev-1\"}")])
  (<- world (open-world (fn [_] (.pop replies 0)) 0.0))
  (<- body (question "(defk h [] 2)" "jev-latest"))
  (<- unreachable (asking world body None))
  (assert (= #(unreachable.status (! (header unreachable "x-jev-proxy"))) #(502 "upstream-error")) unreachable)
  (<- busy (asking world body None))
  (assert (= busy.status 503) "本物の Jev の status をそのまま返す")
  (<- garbage (asking world body None))
  (assert (= #(garbage.status garbage.body) #(200 b"not json")) "読めない 200 はそのまま返すが覚えない")
  (<- good (asking world body None))
  (assert (= (! (header good "x-jev-proxy")) "miss"))
  (<- again (asking world body None))
  (assert (= (! (header again "x-jev-proxy")) "hit"))
  (assert (= (len world.script.calls) 4) "覚えたのは読める 200 の 1 つだけ")
  (<- stats (stats-of world))
  (assert (= (get (get stats "events") "upstream-failed") 2) stats))


(deftest test-concurrent-same-key-asks-jev-once
  ;; 同じ鍵の問いが同時に 8 本来ても、本物の Jev は 1 回だけ呼ばれ、全員が同じ答えを受け取る。
  (<- world (open-world (! (jev-answers 0.7)) 0.4))
  (<- request (request-of "POST" "/v1/systemone" (! (question "(defk slow [] 3)" "jev-latest")) OPERATOR-TOKEN None))
  (val start (threading.Barrier 8))
  (val replies [])
  (defn ask-once []  ; defk にできない: threading.Thread が呼ぶ callback
    (.wait start)
    (.append replies (world.run request)))
  (val threads (lfor _ (range 8) (threading.Thread :target ask-once)))
  (for [t threads] (.start t))
  (for [t threads] (.join t 30))
  (assert (= (len replies) 8))
  (assert (= (len world.script.calls) 1) (.format "本物の Jev は 1 回だけ(実際 {} 回)" (len world.script.calls)))
  (assert (= (len (set (gfor r replies r.body))) 1) "全員が同じ本文")
  (assert (all (gfor r replies (= r.status 200))))
  (val outcomes (lfor r replies (next (gfor h r.headers :if (= h.name "x-jev-proxy") h.value) None)))
  (assert (= (.count outcomes "miss") 1) outcomes)
  (assert (= (+ (.count outcomes "coalesced") (.count outcomes "hit")) 7) outcomes))


(deftest test-identity-and-admin-only-forget
  ;; token の無い・名簿に無い問いは 401 で Jev を呼ばない。答えを消せるのは管理者だけ(worker は 403)。消した鍵は次に本物へ行く。
  (<- world (open-world (! (jev-answers 0.2)) 0.0))
  (<- body (question "(defk k [] 4)" "jev-latest"))
  (assert (= (. (world.run (! (request-of "POST" "/v1/systemone" body None None))) status) 401))
  (assert (= (. (world.run (! (request-of "POST" "/v1/systemone" body "stolen" None))) status) 401))
  (assert (= (len world.script.calls) 0) "身元の引けない問いは Jev を呼ばない")
  (<- asked-request (request-of "POST" "/v1/systemone" body WORKER-TOKEN None))
  (val asked (world.run asked-request))
  (assert (= asked.status 200))
  (<- key (header asked "x-jev-proxy-key"))
  (val by-worker (world.run (! (request-of "DELETE" (+ "/v1/answers/" key) b"" WORKER-TOKEN None))))
  (assert (= by-worker.status 403) "管理者でない身元は消せない")
  (val shown (world.run (! (request-of "GET" (+ "/v1/answers/" key) b"" OPERATOR-TOKEN None))))
  (assert (= (get (json.loads shown.body) "key") key))
  (val by-operator (world.run (! (request-of "DELETE" (+ "/v1/answers/" key) b"" OPERATOR-TOKEN None))))
  (assert (= #(by-operator.status (get (json.loads by-operator.body) "forgotten")) #(200 True)))
  (val after (world.run asked-request))
  (assert (= (! (header after "x-jev-proxy")) "miss") "消した答えは次の問いで本物の Jev へ")
  (val by-question (world.run (! (request-of "POST" "/v1/answers/forget" body OPERATOR-TOKEN None))))
  (assert (= (get (json.loads by-question.body) "key") key) "本文からも同じ鍵で消せる")
  (assert (= (len world.script.calls) 2)))


(deftest test-no-cache-refreshes-and-model-version-change-retires-old-answers
  ;; no-cache は覚えを使わずに問い直して覚え直す。答えた model の版が変わると、古い版で覚えた別の鍵の答えも当たらなくなる。
  (val answers (lfor #(p m) [#(0.1 "jev-1") #(0.2 "jev-1") #(0.3 "jev-2") #(0.4 "jev-2")]
                     (UpstreamReply :status 200 :body (.encode (json.dumps {"answers" {"q" {"noul" p}} "model" m}) "utf-8"))))
  (<- world (open-world (fn [_] (.pop answers 0)) 0.0))
  (<- a (question "(defk a [] 1)" "jev-latest"))
  (<- b (question "(defk b [] 2)" "jev-latest"))
  (<- (asking world a None))
  (<- (asking world b None))
  (<- a-again (asking world a None))
  (assert (= (! (header a-again "x-jev-proxy")) "hit"))
  (<- refreshed (asking world b "no-cache"))
  (assert (= (! (header refreshed "x-jev-proxy")) "refreshed"))
  (assert (in b"jev-2" refreshed.body))
  (<- retired (asking world a None))
  (assert (= (! (header retired "x-jev-proxy")) "miss") "jev-1 で覚えた a は、今の版 jev-2 では当たらない")
  (assert (= (len world.script.calls) 4)))


(deftest test-answers-survive-a-restart
  ;; 置き場は file なので、組み直した答え手の組(= Pod の入れ替え)も前の答えで答える。
  (<- world (open-world (! (jev-answers 0.6)) 0.0))
  (<- body (question "(defk p [] 5)" "jev-latest"))
  (<- (asking world body None))
  (<- restarted (reopen-world world))
  (<- after (asking restarted body None))
  (assert (= (! (header after "x-jev-proxy")) "hit"))
  (assert (= (len world.script.calls) 1)))


(defk peeking [world keys token]
  {:pre [(: world World) (: keys list) (: token (| str None))] :post [(: % ProxyReply)]}
  "鍵の束で覚えている時だけの問い(POST /v1/systemone/peek)を撃つため。"
  (<- body (json-bytes {"keys" keys}))
  (<- request (request-of "POST" "/v1/systemone/peek" body token None))
  (world.run request))


(deftest test-peek-many-returns-only-remembered-answers-and-never-calls-jev
  ;; 覚えている時だけの問いの束は、鍵の束のうち覚えている答えだけを返し、本物の Jev を 1 度も呼ばない。重ねた鍵は 1 つに数える。
  ;; 形の違う鍵・空の束・身元の無い呼び手は断る。答えた model の版が変わった後の古い答えは、束でも返さない。
  (val answers (lfor #(p m) [#(0.1 "jev-1") #(0.2 "jev-2")]
                     (UpstreamReply :status 200 :body (.encode (json.dumps {"answers" {"q" {"noul" p}} "model" m}) "utf-8"))))
  (<- world (open-world (fn [_] (.pop answers 0)) 0.0))
  (<- a (question "(defk a [] 1)" "jev-latest"))
  (<- b (question "(defk b [] 2)" "jev-latest"))
  (<- first (asking world a None))
  (<- ka (normalize-request a))
  (<- kb (normalize-request b))
  (<- peeked (peeking world [ka.key kb.key ka.key] WORKER-TOKEN))
  (assert (= peeked.status 200) peeked)
  (val found (get (json.loads peeked.body) "answers"))
  (assert (= (list (.keys found)) [ka.key]) "覚えている鍵の答えだけを返す")
  (assert (= (get found ka.key) (json.loads first.body)) "問うた時と同じ答え")
  (assert (= (len world.script.calls) 1) "束の問いは本物の Jev を呼ばない")
  (<- stats (stats-of world))
  (assert (= #((get (get stats "events") "peek-hit") (get (get stats "events") "peek-miss")) #(1 1)) "重ねた鍵は 1 つに数える")
  (<- malformed (peeking world ["NOT-A-KEY"] WORKER-TOKEN))
  (assert (= malformed.status 400) malformed)
  (<- empty (peeking world [] WORKER-TOKEN))
  (assert (= empty.status 400) empty)
  (<- stranger (peeking world [ka.key] None))
  (assert (= stranger.status 401) stranger)
  (<- (asking world b None))
  (<- retired (peeking world [ka.key kb.key] WORKER-TOKEN))
  (assert (= (list (.keys (get (json.loads retired.body) "answers"))) [kb.key]) "jev-1 で覚えた a は、今の版 jev-2 では束でも返らない")
  (assert (= (len world.script.calls) 2)))


(deftest test-peek-many-only-reads-the-store
  ;; 覚えている時だけの問いの束は置き場を読むだけ — 答えごとの hits・last_hit_at を書かない(agora-redesign #1885: 書くと 1000 鍵の
  ;; 束が 1000 行の UPDATE と commit になり、束が 5 秒を超えて linter の待ちに収まらなかった)。数は計器 peek-hit に残る。
  ;; 反例: 普通の問いが覚えから答えた時は、今までどおり hits を数える。
  (val answers [(UpstreamReply :status 200 :body (.encode (json.dumps {"answers" {"q" {"noul" 0.3}} "model" "jev-1"}) "utf-8"))])
  (<- world (open-world (fn [_] (.pop answers 0)) 0.0))
  (<- a (question "(defk a [] 1)" "jev-latest"))
  (<- (asking world a None))
  (<- ka (normalize-request a))
  (val row-of (fn [] (with [connection (closing (sqlite3.connect world.path))]
                       (.fetchone (.execute connection "SELECT hits, last_hit_at FROM answers WHERE key = ?" #(ka.key))))))
  (val before (row-of))
  (for [_ (range 3)]
    (<- peeked (peeking world [ka.key] WORKER-TOKEN))
    (assert (= (list (.keys (get (json.loads peeked.body) "answers"))) [ka.key]) peeked))
  (assert (= (row-of) before) #("束の問いが置き場に書いた" before (row-of)))
  (<- stats (stats-of world))
  (assert (= (get (get stats "events") "peek-hit") 3) stats)
  (<- (asking world a None))
  (assert (= (get (row-of) 0) (+ (get before 0) 1)) "普通の問いの覚えからの答えは hits を数える")
  (assert (= (len world.script.calls) 1) "束の問いも覚えからの答えも本物の Jev を呼ばない"))


(deftest test-peek-many-is-answered-from-the-memory-copy-not-the-store
  ;; 束を置き場(SQLite)から引くと、読みの速さが node の page cache 次第になる(agora-redesign #1912: memory の足りない k3s-1 では
  ;; page cache が 20 秒ほどで捨てられ、1000 鍵の束が約 12 秒になって linter の待ち — 全部の束で 5 秒 — を越えた)。束は proxy の
  ;; memory の写しから答え、置き場へは届かない。写しは組み直し(Pod の入れ替え)で置き場から読み直し、消した答えは束からも消える。
  ;; 反例: 写しの無い組(直す前の並び)では、束が毎回置き場へ届く。
  (<- a (question "(defk a [] 1)" "jev-latest"))
  (<- b (question "(defk b [] 2)" "jev-latest"))
  (<- ka (normalize-request a))
  (<- kb (normalize-request b))
  (<- spied (spied-world (! (jev-answers 0.4)) True))
  (val world spied.world)
  (<- (asking world a None))
  (<- (asking world b None))
  (for [_ (range 3)]
    (<- peeked (peeking world [ka.key kb.key] WORKER-TOKEN))
    (assert (= (sorted (.keys (get (json.loads peeked.body) "answers"))) (sorted [ka.key kb.key])) peeked))
  (assert (= spied.reads []) #("束の問いが置き場へ届いた" spied.reads))
  (<- restarted (reopen-world world))
  (<- after-restart (peeking restarted [ka.key kb.key] WORKER-TOKEN))
  (assert (= (sorted (.keys (get (json.loads after-restart.body) "answers"))) (sorted [ka.key kb.key]))
          "組み直した写しは置き場から前の答えを読む")
  (val forgot (restarted.run (! (request-of "DELETE" (+ "/v1/answers/" ka.key) b"" OPERATOR-TOKEN None))))
  (assert (= #(forgot.status (get (json.loads forgot.body) "forgotten")) #(200 True)) forgot)
  (<- after-forget (peeking restarted [ka.key kb.key] WORKER-TOKEN))
  (assert (= (list (.keys (get (json.loads after-forget.body) "answers"))) [kb.key]) "消した答えは束からも消える")
  (<- plain (spied-world (! (jev-answers 0.4)) False))
  (<- (asking plain.world a None))
  (for [_ (range 2)]
    (<- (peeking plain.world [ka.key] WORKER-TOKEN)))
  (assert (= plain.reads [#(ka.key) #(ka.key)]) #("写しの無い組では束が毎回置き場へ届く" plain.reads)))


(deftest test-key-contract-sample-is-the-proxy-key
  ;; 呼び手(doeff-linter の src/project/semantic.rs の proxy_key)が本文から鍵を作る決まりの見本 tests/key_contract.json が、
  ;; 代理の鍵と同じであること。linter の検(tests/semantic_proxy.rs)も同じ見本を読む — 片方の決まりだけを変えると、どちらかが赤になる。
  ;; 見本は日本語・絵文字・逃がす文字・制御文字・鍵の並び(符号位置の順)・入れ子・真偽・null・整数を持つ(小数は持たない — 綴りが
  ;; 言語で違う。linter の本文は小数を持たない)。
  (val cases (json.loads (.read-text (/ (. (Path __file__) parent) "key_contract.json") :encoding "utf-8")))
  (assert (>= (len cases) 4))
  (for [case cases]
    (<- normalized (normalize-request (.encode (json.dumps (get case "body")) "utf-8")))
    (assert (= normalized.key (get case "key")) (get case "body"))))
