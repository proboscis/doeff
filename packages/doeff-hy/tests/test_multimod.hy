;;; Multi-module S-expr analysis + transform test
(import doeff_hy.sexpr [body-of collect-effects effect-tree
                        print-effect-tree assert-handlers])

;; ========================================================================
;; Part 1: Multi-module effect analysis
;; ========================================================================

(print "=" (* "=" 60))
(print "Part 1: Multi-module effect collection")
(print (* "=" 60))

(import tests.multimod.data_fetch [fetch-price-data fetch-news-for-day fetch-all-data])
(import tests.multimod.signal [generate-signal generate-and-notify])

(print)
(print "--- fetch-price-data effects ---")
(print (collect-effects fetch-price-data))

(print)
(print "--- fetch-all-data effects (should include FetchPrice + FetchNews) ---")
(print (collect-effects fetch-all-data))

(print)
(print "--- generate-signal effects (should include FetchPrice + FetchNews + LLMRank) ---")
(print (collect-effects generate-signal))

(print)
(print "--- generate-and-notify effects (all 4) ---")
(setv all-effects (collect-effects generate-and-notify))
(print all-effects)
(assert (= all-effects #{"FetchPrice" "FetchNews" "LLMRank" "SendSlack"})
        (+ "Expected 4 effects, got: " (str all-effects)))
(print "PASS: all 4 effects found across 3 modules")

(print)
(print "--- effect tree (generate-and-notify) ---")
(print-effect-tree (effect-tree generate-and-notify))

(print)
(print "--- assert-handlers: all covered ---")
(assert-handlers generate-and-notify #{"FetchPrice" "FetchNews" "LLMRank" "SendSlack"})
(print "PASS")

(print)
(print "--- assert-handlers: missing SendSlack ---")
(try
  (assert-handlers generate-and-notify #{"FetchPrice" "FetchNews" "LLMRank"})
  (print "FAIL — should have raised")
  (except [e AssertionError]
    (print (+ "PASS — caught: " (str e)))))

;; ========================================================================
;; Part 2: S-expr transform — insert print around every <-
;; ========================================================================

(print)
(print (* "=" 60))
(print "Part 2: S-expr bind transformation")
(print (* "=" 60))

(import hy.models [Expression Symbol String Keyword])

(defn _is-bind [form]
  (and (isinstance form Expression)
       (> (len form) 0)
       (= (str (get form 0)) "<-")))

(defn _bind-var [form]
  "Get var name from (<- var expr). None for (<- expr)."
  (if (>= (len form) 3) (get form 1) None))

(defn _bind-expr [form]
  (cond
    (= (len form) 2) (get form 1)
    (= (len form) 3) (get form 2)
    (= (len form) 4) (get form 3)
    True None))

(defn insert-print-around-binds [body]
  "Transform: wrap every <- with print before and after.
   (<- x (SomeEffect ...))
   →
   (print 'BEFORE: x <- SomeEffect')
   (<- x (SomeEffect ...))
   (print 'AFTER: x = ...')"
  (setv result [])
  (for [form body]
    (if (_is-bind form)
        (let [var (_bind-var form)
              expr (_bind-expr form)
              label (if var (str var) "_")]
          ;; Before
          (.append result
            (Expression [(Symbol "print")
                         (String (+ "BEFORE: " label " <- " (str (get expr 0))))]))
          ;; The bind itself
          (.append result form)
          ;; After
          (when var
            (.append result
              (Expression [(Symbol "print")
                           (String (+ "AFTER: " label " ="))
                           var]))))
        (.append result form)))
  result)

(print)
(print "--- Original body (fetch-all-data) ---")
(for [form (body-of fetch-all-data)]
  (print (str form)))

(print)
(print "--- Transformed body ---")
(setv transformed (insert-print-around-binds (body-of fetch-all-data)))
(for [form transformed]
  (print (str form)))

;; ========================================================================
;; Part 3: Compile and run the transformed program
;; ========================================================================

(print)
(print (* "=" 60))
(print "Part 3: Compile and run transformed program")
(print (* "=" 60))

(import doeff [run WithHandler Pure Resume Pass])
(import doeff [do :as _doeff-do])
(import doeff_core_effects.scheduler [scheduled])
(require doeff-hy.macros [defk <-])
(import tests.multimod.effects [FetchPrice FetchNews])

;; Mock handlers — return dummy data
(defk mock-price-handler [effect k]
  {:pre [(: effect object) (: k object)] :post [(: % object)]}
  (if (isinstance effect FetchPrice)
    (yield (Resume k [100 101 102]))
    (yield (Pass effect k))))

(defk mock-news-handler [effect k]
  {:pre [(: effect object) (: k object)] :post [(: % object)]}
  (if (isinstance effect FetchNews)
    (yield (Resume k ["news-article-1" "news-article-2"]))
    (yield (Pass effect k))))

;; Compile transformed body into executable
(import hy)

(setv compile-body
  `(do
     (require doeff-hy.macros [defk <-])
     (import doeff [do :as _doeff-do])
     (import tests.multimod.effects [FetchPrice FetchNews])
     (defk _compiled [ticker day]
       {:pre [(: ticker str) (: day str)] :post [(: % object)]}
       ~@transformed)
     _compiled))

(setv compiled-fn (hy.eval compile-body))

;; Run it with mock handlers
(setv program
  (WithHandler mock-price-handler
    (WithHandler mock-news-handler
      (compiled-fn "7203.T" "2025-11-10"))))

(print)
(print "--- Running transformed program ---")
(setv result (run (scheduled program)))
(print)
(print "Result:" result)
(assert (= (get result "prices") [100 101 102]))
(assert (= (get result "news") ["news-article-1" "news-article-2"]))
(print "PASS: transformed program executed correctly")
