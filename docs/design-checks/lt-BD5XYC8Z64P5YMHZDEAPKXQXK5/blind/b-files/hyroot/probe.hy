(require doeff-hy.macros [defk val <- for/do])
(import doeff [do :as _doeff-do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.handlers [sequential])

(defk pair-sums [xs ys]
  {:pre [(: xs (get tuple #(int ...))) (: ys (get tuple #(int ...)))] :post [(: % (get tuple #(int ...)))]}
  (<- got
      (for/do
        (<- x (From xs :label "outer"))
        (<- y (From ys :label "inner"))
        (+ x y)))
  (tuple got.valid-values))

(defk post-counts [users posts]
  {:pre [(: users (get tuple #(int ...))) (: posts (get tuple #(int ...)))] :post [(: % (get tuple #(int ...)))]}
  (<- got
      (for/do
        (<- u (From users :label "users"))
        (<- sub (for/do (<- p (From posts :label "posts")) (+ u p)))
        (len (list sub))))
  (tuple got.valid-values))

(defn #^ None report [#^ (| str None) label #^ int done #^ int total]
  (print label done total))

(val progress-handler (sequential :on-progress report))
