;;; 宿の読み(Ask)の契約テスト — 宿の契約(host_contract.hy)の Ask に答える本物(本番の子の土台の os-environ-reader と host-reader)と
;;; fake(sim の宿の子の host-answers と値の表の environ-table-reader)が、同じ deftest を通る。解釈器の組み立ては host_reads_contract_handlers.hy。
;;;
;;;   * 宣言の :environ の名の値は字面どおりの文字列(JSON を parse しない・{…} を解かない・空白を削らない)・空の値は空の文字列
;;;   * 置き場に無い名・文字列でない鍵の Ask は外側へ渡す(外側が答えればその答え・外側も答えなければ外側の断り KeyError — None ではない)
;;;   * 宿の契約の鍵: run-context は worker が渡した文脈と同じ RunContext・program は Program の path
;;;   * Ask でない effect は外側へ渡す
;;; 本物だけの性質(置き場 = process の os.environ なので宣言の外の名 — PATH など — にも答える・子の process での読み)は
;;; test_environ_reader.hy と test_job_context.hy。
(require doeff-hy.macros [deftest <-])
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import os)
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import tests.host_reads_contract_handlers [Outside CONTEXT PROGRAM-PATH JSON-NAME JSON-VALUE PLAIN-NAME PLAIN-VALUE EMPTY-NAME
                                            MISSING OUTER-NAME OUTSIDE-ANSWER])


(deftest test-a-declared-name-answers-its-value-literally
  {:interpreters ["host-process" "sim-host"]}
  (<- json-like str (Ask JSON-NAME))
  (<- plain str (Ask PLAIN-NAME))
  (<- empty str (Ask EMPTY-NAME))
  (assert (= json-like JSON-VALUE) json-like)
  (assert (= plain PLAIN-VALUE) (repr plain))
  (assert (= empty "") (repr empty)))


(deftest test-an-undeclared-name-and-a-non-string-key-go-outward
  {:interpreters ["host-process" "sim-host"]}
  (<- outer str (Ask OUTER-NAME))
  (<- typed str (Ask int))
  (assert (= outer "外側") outer)
  (assert (= typed "型の鍵") typed))


(deftest test-a-name-nobody-answers-is-refused-by-the-outside-not-answered-none
  {:interpreters ["host-process" "sim-host"]}
  (try
    (<- got (Ask MISSING))
    (assert False (.format "無い名 {} に答えた: {!r}" MISSING got))
    (except [refused KeyError]
      (assert (in MISSING (str refused)) (str refused)))))


(deftest test-the-host-keys-answer-the-run-context-and-the-program-path
  {:interpreters ["host-process" "sim-host"]}
  (<- context RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- path str (Ask HOST-CONTRACT.program-key))
  (assert (= context CONTEXT) context)
  (assert (= path PROGRAM-PATH) path))


(deftest test-the-versions-key-answers-this-process-versions
  ;; 失敗ケース(#2345): 版の識別は宿の契約の鍵 versions-key で答える(本物の宿は process-versions に os.environ を渡した物・sim の宿は
  ;; 送り手の口の versions — この検の sim の宿は同じ物を口に載せる)。
  ;; protocol の層(記録係・送り手の client)は process の版を自分で読まず、この答えを受け取る。
  {:interpreters ["host-process" "sim-host"]}
  (<- versions dict (Ask HOST-CONTRACT.versions-key))
  (assert (= versions (! (process-versions os.environ))) versions))


(deftest test-an-effect-other-than-ask-goes-outward
  {:interpreters ["host-process" "sim-host"]}
  (<- answer str (Outside))
  (assert (= answer OUTSIDE-ANSWER) answer))
