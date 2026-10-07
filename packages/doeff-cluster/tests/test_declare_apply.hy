;;; 宣言の書き(shared/entry/declare の apply-declaration)の答え — HTTP の答え手だけを台本の coordinator の代役に替えて回す。
;;;
;;; apply-declaration は詰めた Program を PUT /programs/<sha> で置いてから、Service の行を GET → 無ければ POST・在れば読んだ版を付けて
;;; PUT で書き、答え = 全部が通ったか(偽なら declare の入口が 1 で終わる)。HTTP は汎用の effect HttpRequest(本番は入口が並べる
;;; http-production-handler が答える)なので、検は代役の handler で答える。
;;; 失敗ケース: Program の置きが 300 以上なら行を 1 つも書かずに偽(代役は Program の置きが落ちた後の行の書きを断る)・行の書きが
;;; 300 以上なら偽。
(require doeff-hy.macros [deftest defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import doeff_cluster.shared.intent.service_model [Declaration])

(val URL "http://coordinator.test")
(val DECLARATION (Declaration :rows [{"name" "writer-a" "revision" "r1" "replicas" 1 "run" {"program" "sha-a" "versions" {"python" "3.14.0"}}}]
                              :programs {"sha-a" "blob-a"}))


(defhandler stand-in-coordinator [#^ int program-status #^ (| int None) service-status]
  ;; coordinator の資源の口の代役: Program の置きは program-status で答え、Service はいつも無い(GET は 404)ので POST の書きを
  ;; service-status で答える。service-status が None なら行の書きが来ること自体を断る(Program の置きが落ちた後に行を書かない検)。
  ;; 引数に残す理由: 答えの番号は検ごとに決まる代役の作りで、業務の Program が読む設定ではない。
  (HttpRequest [method url]
    (val status (cond
                  (.startswith url (+ URL "/programs/")) program-status
                  (= method "GET") 404
                  (is service-status None) (raise (AssertionError (.format "Program の置きが落ちた後に行を書いた: {} {}" method url)))
                  True service-status))
    (resume (HttpResponse status {} b"" "" url 0.0))))


(deftest test-apply-declaration-places-programs-then-creates-the-missing-service
  (<- applied bool (with-handlers [slog-discard-handler (stand-in-coordinator 200 201)]
                     (apply-declaration URL DECLARATION "c-test")))
  (assert applied))


(deftest test-apply-declaration-stops-before-the-rows-when-a-program-is-refused
  ;; 代役は行の書きが来れば断るので、行を書いたなら赤(AssertionError)・書かずに偽なら緑。
  (<- applied bool (with-handlers [slog-discard-handler (stand-in-coordinator 500 None)]
                     (apply-declaration URL DECLARATION "c-test")))
  (assert (not applied)))


(deftest test-apply-declaration-answers-false-when-a-service-write-is-refused
  (<- applied bool (with-handlers [slog-discard-handler (stand-in-coordinator 200 409)]
                     (apply-declaration URL DECLARATION "c-test")))
  (assert (not applied)))


;; --- 読んでから書くまでに版が進んだ時(#3850 の 20:00 の日次の赤 — 落ち続ける job の宣言し直しが 409 で落ちた)----------
;; coordinator は Service の状態の欄(落ちた回数・Ready)が変わっても版を進める。PUT が書くのは spec だけなので、状態だけの変化は書きが
;; 消す他人の変更ではない。宣言は 409 を受けたら読み直し(coordinator の 409 の文「読み直してから書く」)、spec が最初に読んだ時と同じなら
;; 新しい版で書き直す。spec が変わっていれば(他の書き手が書いた)今までどおり止まる — 他の作業係の変更を消さない。

(val SPEC-READ {"readiness" None "revision" "r0" "replicas" 1 "run" {"program" "sha-a" "versions" {"python" "3.14.0"}} "owner" "o-1"})
(val SPEC-OTHER (| SPEC-READ {"revision" "r-other"}))


(defhandler versioned-coordinator [#^ dict service #^ str turn]
  ;; 版を持つ coordinator の代役: service = 今の資源 {"version" 版・"spec" spec・"puts" 受けた PUT の版の組}。最初の GET の直後に turn の
  ;; 変化を起こす — "status" = 状態だけが変わって版だけ進む・"spec" = 他の書き手が spec を書いて版が進む。PUT は版が今と同じ時だけ書く
  ;; (違えば 409 — coordinator の check-version と同じ)。引数に残す理由: 代役の外の世界の状態は検ごとに作る値で、業務の設定ではない。
  (HttpRequest [method url body]
    (cond
      (.startswith url (+ URL "/programs/"))
        (resume (HttpResponse 200 {} b"" "" url 0.0))
      (= method "GET")
        (do (val text (json.dumps {"spec" (get service "spec") "resourceVersion" (get service "version")}))
            (when (= (get service "reads") 0)
              (setv (get service "version") (+ (get service "version") 1))
              (when (= turn "spec")
                (setv (get service "spec") SPEC-OTHER)))
            (setv (get service "reads") (+ (get service "reads") 1))
            (resume (HttpResponse 200 {} (.encode text) text url 0.0)))
      (= method "PUT")
        (do (setv (get service "puts") (+ (get service "puts") #((get body "resourceVersion"))))
            (if (= (get body "resourceVersion") (get service "version"))
                (do (setv (get service "spec") (get body "spec") (get service "version") (+ (get service "version") 1))
                    (resume (HttpResponse 200 {} b"{}" "{}" url 0.0)))
                (resume (HttpResponse 409 {} b"{}" "{}" url 0.0))))
      True (raise (AssertionError (.format "代役が知らない要求: {} {}" method url))))))


(deftest test-apply-declaration-rewrites-when-only-the-status-moved-between-the-read-and-the-write
  ;; 失敗ケース: 直す前は 409 で止まり偽(PUT は版 15 の 1 回だけ)。
  (val service {"version" 15 "spec" SPEC-READ "reads" 0 "puts" #()})
  (<- applied bool (with-handlers [slog-discard-handler (versioned-coordinator service "status")]
                     (apply-declaration URL DECLARATION "c-test")))
  (assert applied service)
  (assert (= (get service "puts") #(15 16)) service)
  (assert (= (get (get service "spec") "revision") "r1") service))


(deftest test-apply-declaration-still-stops-when-another-writer-moved-the-spec
  ;; 反例: 読んでから書くまでに他の書き手が spec を書いたら、読み直しても書き直さずに偽 — 他の書き手の spec が残る。
  (val service {"version" 15 "spec" SPEC-READ "reads" 0 "puts" #()})
  (<- applied bool (with-handlers [slog-discard-handler (versioned-coordinator service "spec")]
                     (apply-declaration URL DECLARATION "c-test")))
  (assert (not applied) service)
  (assert (= (get service "puts") #(15)) service)
  (assert (= (get service "spec") SPEC-OTHER) service))
