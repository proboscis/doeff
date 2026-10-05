;; 組みの完成を待つ効果 AwaitWarm(#3668 (b))の本番の答え手(detached.hy の warm-cluster の warm-awaited)の待ち方を、coordinator への HTTP
;; だけを台本の答え手に替えて確かめる(本番の coordinator が組みの進みで版を進めることは、本物の coordinator の上の検 test_await_warm.hy が持つ):
;;   * 待ちは GET /watch(版の変化の long-poll)で起き、版が進めば読み直して WarmReady で返る — 間隔で起きて確かめない
;;   * 版が進まない coordinator では、GET /watch の上限(WATCH-MAX-SECONDS)ごとに 1 回だけ読み直し、期限で WarmWaitExpired(最後の読みつき)
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import json)
(import urllib.parse [urlsplit])
(import doeff [with_handlers])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [WATCH-MAX-SECONDS])
(import doeff_cluster.shared.intent.warm_model [AwaitWarm WarmState WarmReady WarmWaitExpired])
(import doeff_cluster.shared.core.warm_rules [warm-state->json])
(import doeff_cluster.shared.protocol.detached [warm-cluster])
(import tests.coordinator_contract_handlers [CONTRACT-ROUTE contract-route])

(val KEY "0123456789abcdef01234567")
;; 台本の coordinator が組みを終える(版を 1 から 2 へ進める)までの仮想の秒と、待つ上限。
(val READY-AFTER-SECONDS 5.0)
(val LIMIT-SECONDS 60.0)


(defclass [dataclass] Script []
  "台本の coordinator の真実(答え手が書き換え、検が後で読む — そのため frozen でない): moves = 組みの完成で版が進むか(偽 = 版の進まない
   coordinator)・calls = 受けた要求の #(path after) の tuple(受けた順)。"
  (#^ bool moves)
  (#^ tuple calls))


(defrecord Awaited
  "筋書きの結果: answer = AwaitWarm の答え・waited = 仮想の秒。"
  (#^ object answer)
  (#^ float waited))


(defk row-at [revision]
  {:pre [(: revision int)] :post [(: % WarmState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の行の姿: 版 2 で準備済み・それまで準備中。"
  (if (>= revision 2)
      (WarmState :key KEY :ready #("w1") :preparing #() :failed #() :until-ms 0)
      (WarmState :key KEY :ready #() :preparing #("w1") :failed #() :until-ms 0)))


(defk json-response [url body]
  {:pre [(: url str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の答えを本物の答え手と同じ形の HttpResponse にするため。"
  (val text (json.dumps body))
  (HttpResponse 200 {"content-type" "application/json"} (.encode text "utf-8") text url 0.0))


(defhandler scripted-coordinator [#^ Script script]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 台本の真実(検が後で読む)を組み立てが 1 つ作って渡す。
  ;; coordinator への要求に台本で答える: GET /warm/<key> = 今の版の行の姿・GET /watch = 版が進む台本なら READY-AFTER-SECONDS 待って版 2 を
  ;; 「変わった」で、進まない台本(か版 2 を知った後)なら問いの上限まで待って今の版を「変わらない」で返す(本物の long-poll と同じ形)。
  (HttpRequest [method url headers params body]
    (val path (. (urlsplit url) path))
    (val after (int (.get (or params {}) "after" "-1")))
    (setv script.calls (+ script.calls #(#(path after))))
    (cond
      (.startswith path "/warm/")
        (do (val revision (if (and script.moves (any (gfor c script.calls (= (get c 0) "/watch")))) 2 1))
            (<- row WarmState (row-at revision))
            (<- answer HttpResponse (json-response url (! (warm-state->json row))))
            (resume answer))
      (= path "/watch")
        (do (val seconds (float (get params "timeoutSeconds")))
            (if (and script.moves (< after 2))
                (do (<- (Delay READY-AFTER-SECONDS))
                    (<- moved HttpResponse (json-response url {"revision" 2 "changed" True}))
                    (resume moved))
                (do (<- (Delay seconds))
                    (<- still HttpResponse (json-response url {"revision" (if script.moves 2 1) "changed" False}))
                    (resume still))))
      True (raise (AssertionError (.format "台本に無い要求: {} {}" method path))))))


(defk await-under [script]
  {:pre [(: script Script)] :post [(: % Awaited)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の warm-cluster を台本の coordinator の上に置き、AwaitWarm で LIMIT-SECONDS を上限に待つため。"
  (<- asked int (now-epoch-ms))
  (<- answer (AwaitWarm KEY LIMIT-SECONDS))
  (<- now int (now-epoch-ms))
  (Awaited :answer answer :waited (/ (- now asked) 1000.0)))


(defk run-scripted [script]
  {:pre [(: script Script)] :post [(: % Awaited)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の世界の組み立て: 仮想の時計・台本の coordinator・本番の warm-cluster(外側が先)。"
  (<- seen Awaited (with_handlers [(sim-time-handler :clock (SimClock)) (scripted-coordinator script)
                                   (warm-cluster (contract-route) CONTRACT-ROUTE)]
                                  (await-under script)))
  seen)


(deftest test-the-production-wait-wakes-on-the-revision-and-reads-again
  ;; 版が進めば、その時に読み直して WarmReady で返る: 読み → 版の待ち(after = 0)→ 読み、の 3 つの要求だけ(間隔で問い直さない)。
  (val script (Script :moves True :calls #()))
  (<- seen Awaited (run-scripted script))
  (assert (isinstance seen.answer WarmReady) seen)
  (assert (= seen.waited READY-AFTER-SECONDS) seen)
  (assert (= script.calls #(#((+ "/warm/" KEY) -1) #("/watch" 0) #((+ "/warm/" KEY) -1))) script.calls))


(deftest test-the-production-wait-on-a-still-coordinator-reads-once-per-watch-and-expires
  ;; 版の進まない coordinator: GET /watch の上限ごとに 1 回だけ読み直し(期限 60 秒 / 上限 10 秒 = 6 回の待ち)、期限で準備中の最後の
  ;; 読みつきの WarmWaitExpired で返る。
  (val script (Script :moves False :calls #()))
  (<- seen Awaited (run-scripted script))
  (assert (isinstance seen.answer WarmWaitExpired) seen)
  (assert (= seen.answer.last.preparing #("w1")) seen)
  (assert (= seen.waited LIMIT-SECONDS) seen)
  (val watches (lfor c script.calls :if (= (get c 0) "/watch") c))
  (assert (= (len watches) (int (/ LIMIT-SECONDS WATCH-MAX-SECONDS))) script.calls))
