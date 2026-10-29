;;; handle(一度きりの handler をその場で program に当てる)が、名を付けた defhandler を with-handler で当てた program と同じ物で
;;; ある事の検(card ki-fb1c0fb41ff5)。
;;;
;;; 確かめること: 同じ節を handle で書いた program と defhandler + with-handler で書いた program が同じ答えを返す(resume・transfer・
;;; finish・:when の守りと外への reperform・<- で外へ頼む節)・resume を使えない書き方(Raise の節の resume・resume の抜けた枝)が
;;; defhandler と同じ SyntaxError になる・handle の直下の session val は defhandler を案内する SyntaxError で断る。
;;; 公開は test_handle_inline.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defeffect defhandler handle with-handler defk <- val])
(import hy)
(import pytest)
(import types)
(import doeff [Pure])

(defeffect Greeting
  "言語 lang の挨拶を問う。"
  {:fields [(: lang str)]
   :answer str
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Count
  "数 n を数え直した値を問う(守りつきの節と外への reperform を見る)。"
  {:fields [(: n int)]
   :answer int
   :tags {:context "doeff-hy" :role "intent"}})

(defk greet-twice [lang]
  {:pre [(: lang str)] :post [(: % str)] :tags {:context "doeff-hy" :role "judgment"}}
  "lang と英語の挨拶を問い、繋いで返す。"
  (<- first (Greeting lang))
  (<- second (Greeting "en"))
  (+ first " / " second))

(defk count-both [small large]
  {:pre [(: small int) (: large int)] :post [(: % int)] :tags {:context "doeff-hy" :role "judgment"}}
  "小さい数と大きい数を数え直し、和を返す。"
  (<- a (Count small))
  (<- b (Count large))
  (+ a b))

(defhandler greeting-by-name
  (Greeting [lang] (resume (+ "hello " lang))))

(defhandler greeting-transfer
  (Greeting [lang] (transfer (+ "hi " lang))))

(defhandler greeting-finish
  (Greeting [lang] :finish-reason "最初の問いで答えを決める" (finish (+ "stopped at " lang))))

(defhandler small-count
  (Count [n] :when (< n 10) (resume (* n 2))))

(defhandler outer-count
  (Count [n] (resume (+ n 1000))))

(defhandler count-via-outer
  (Count [n]
    (<- outer (Count (+ n 1)))
    (resume (- outer 1))))


(deftest test-handle-answers-like-defhandler-with-handler
  (<- by-handle (handle (greet-twice "ja") (Greeting [lang] (resume (+ "hello " lang)))))
  (<- by-name (with-handler [greeting-by-name] (greet-twice "ja")))
  (assert (= by-handle by-name "hello ja / hello en") #(by-handle by-name))
  (<- by-handle-transfer (handle (greet-twice "ja") (Greeting [lang] (transfer (+ "hi " lang)))))
  (<- by-name-transfer (with-handler [greeting-transfer] (greet-twice "ja")))
  (assert (= by-handle-transfer by-name-transfer) #(by-handle-transfer by-name-transfer))
  (<- by-handle-finish (handle (greet-twice "ja")
                         (Greeting [lang] :finish-reason "最初の問いで答えを決める" (finish (+ "stopped at " lang)))))
  (<- by-name-finish (with-handler [greeting-finish] (greet-twice "ja")))
  (assert (= by-handle-finish by-name-finish "stopped at ja") #(by-handle-finish by-name-finish)))


(deftest test-handle-guard-and-outer-delegation-answer-like-defhandler
  ;; :when が偽の effect は外の handler へ渡る(10 以上は outer-count の +1000)。
  (<- by-handle (with-handler [outer-count]
                  (handle (count-both 3 20) (Count [n] :when (< n 10) (resume (* n 2))))))
  (<- by-name (with-handler [outer-count small-count] (count-both 3 20)))
  (assert (= by-handle by-name (+ 6 1020)) #(by-handle by-name))
  ;; <- で外の handler へ頼み、答えを受けて節を続ける。
  (<- by-handle-delegate (with-handler [outer-count]
                           (handle (count-both 1 2)
                             (Count [n]
                               (<- outer (Count (+ n 1)))
                               (resume (- outer 1))))))
  (<- by-name-delegate (with-handler [outer-count count-via-outer] (count-both 1 2)))
  (assert (= by-handle-delegate by-name-delegate (+ 1001 1002)) #(by-handle-delegate by-name-delegate)))


(defn expansion-error [form]
  "form を doeff-hy の macro を require した名前空間で展開し、macro が出した SyntaxError の文を返す(出なければ None)。"
  (setv module (types.ModuleType "handle_inline_expansion"))
  (hy.eval '(require doeff-hy.macros [defhandler handle with-handler]) :module module)
  (hy.eval '(import doeff-core-effects.effects [Raise]) :module module)
  (setv (. module __dict__ ["Greeting"]) Greeting)
  (try
    (hy.eval form :module module)
    None
    (except [error hy.errors.HyMacroExpansionError]
      ;; Hy は macro の誤りを「expanding macro 名\n  SyntaxError: 文」の 1 つの文に包む(元の誤りは __cause__ に残さない)。
      (setv #(_head sep reason) (.partition (str error) "SyntaxError: "))
      (assert sep (str error))
      (.strip reason))))

(defn after-clause [message]
  "SyntaxError の文から、節の場所の書き出し(`handle clause …:` / `defhandler 名 clause …:`)を外した理由の部分。"
  (get (.split message ": " 1) 1))


(deftest test-handle-refuses-resume-like-defhandler
  (<- _ (Pure None))
  ;; Raise の節は resume できない — 理由の文が同じ。
  (val by-handle (expansion-error '(handle (Pure 1) (Raise [reason] (resume 1)))))
  (val by-name (expansion-error '(defhandler resumes-raise (Raise [reason] (resume 1)))))
  (assert (is-not by-handle None))
  (assert (in "Raise の節は resume できない" by-handle) by-handle)
  (assert (= (after-clause by-handle) (after-clause by-name)) #(by-handle by-name))
  ;; resume の抜けた枝が在る節 — 文がそのまま同じ。
  (val missing-handle (expansion-error '(handle (Pure 1) (Greeting [lang] (if lang (resume 1) None)))))
  (val missing-name (expansion-error '(defhandler misses-resume (Greeting [lang] (if lang (resume 1) None)))))
  (assert (is-not missing-handle None))
  (assert (in "missing resume/transfer/finish/reperform" missing-handle) missing-handle)
  (assert (= missing-handle missing-name) #(missing-handle missing-name)))


(deftest test-handle-refuses-session-and-names-defhandler
  (<- _ (Pure None))
  (val message (expansion-error '(handle (Pure 1) (session val greeting "hello") (Greeting [lang] (resume greeting)))))
  (assert (is-not message None))
  (assert (in "handle は session val / session var を持たない" message) message)
  (assert (in "defhandler" message) message)
  ;; 同じ宣言を defhandler の直下に置くのは受ける。
  (assert (is (expansion-error '(defhandler with-session (session val greeting "hello") (Greeting [lang] (resume greeting))))
              None)))
