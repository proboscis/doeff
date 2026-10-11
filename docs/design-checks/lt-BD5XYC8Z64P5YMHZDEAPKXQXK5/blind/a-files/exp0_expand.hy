(require doeff-hy.macros [for/do <-])
(print (hy.repr (hy.macroexpand '(for/do (<- a (From [1 2])) (<- b (From [10 20])) (+ a b)))))
