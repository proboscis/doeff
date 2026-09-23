;;; Handler composition macros.

(defmacro with-handlers [handlers body]
  "Compose multiple handlers around a body program.

   (with-handlers [(state) writer slog-handler resolve-handler]
     (my-program))

   Expands to:
   ((state) (writer (slog-handler (resolve-handler (my-program)))))

   Handlers are applied inner-first: last in list is innermost."
  (setv result body)
  (for [h (reversed handlers)]
    (setv result `(~h ~result)))
  result)
