;;; 宣言の書き(shared/entry/declare の apply-declaration)の答え — HTTP の答え手だけを台本の coordinator の代役に替えて回す。
;;;
;;; apply-declaration は詰めた Program を PUT /programs/<sha> で置いてから、Service の行を GET → 無ければ POST・在れば読んだ版を付けて
;;; PUT で書き、答え = 全部が通ったか(偽なら declare の入口が 1 で終わる)。HTTP は汎用の effect HttpRequest(本番は入口が並べる
;;; http-production-handler が答える)なので、検は代役の handler で答える。
;;; 失敗ケース: Program の置きが 300 以上なら行を 1 つも書かずに偽(代役は Program の置きが落ちた後の行の書きを断る)・行の書きが
;;; 300 以上なら偽。
(require doeff-hy.macros [deftest defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import doeff_cluster.shared.intent.service_model [Declaration])

(val URL "http://coordinator.test")
(val DECLARATION (Declaration :rows [{"name" "writer-a" "revision" "r1" "run" {"program" "sha-a" "versions" {"python" "3.14.0"}}}]
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
