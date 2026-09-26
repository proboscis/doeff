;;; 宣言の値が名指す本体ごとに、引数の一覧・:pre の契約の外で一度も使われない引数を数える(読むだけ)。
(import sys re gc importlib inspect)
(import pathlib [Path])
(import hy)
(import hy.models [Expression Symbol])
(import doeff [run])
(import doeff_cluster.service_model [ServiceDef])
(import controllers.worker.adr.defadr_worker_business_code_touches_io_only_through_effects :as adr)
(setv root (Path.cwd))
(for [name (run (adr.declaring-modules root #("controllers" "services")))]
  (try (importlib.import-module name) (except [e Exception] (print "import できない" name e))))
(setv seen {})
(for [v (gc.get-objects)] (when (isinstance v ServiceDef) (setv (get seen v.factory) v)))
(setv total 0)
(for [#(factory s) (sorted (.items seen))]
  (setv fn (inspect.unwrap s.program-factory))
  (setv src (Path fn.__code__.co_filename))
  (when (not (.is-relative-to (.resolve src) (.resolve root))) (continue))
  (setv form (next (gfor f (hy.read-many (.read-text src :encoding "utf-8"))
                         :if (and (isinstance f Expression) (> (len f) 1) (= (hy.mangle (str (get f 1))) fn.__name__)) f)))
  ;; 本体 = 引数の list と契約の map を除いた残りの form
  (setv rest (cut form 3 None))
  (when (and rest (isinstance (get rest 0) hy.models.Dict)) (setv rest (cut rest 1 None)))
  (setv text (.join " " (gfor f rest (hy.repr f))))
  (setv unused (lfor p (get form 2) :if (not (re.search (+ r"(?<![\w-])" (re.escape (str p)) r"(?![\w-])") text)) (str p)))
  (when unused
    (+= total 1)
    (print (str (.relative-to (.resolve src) (.resolve root))) fn.__name__ "使わない引数:" unused)))
(print "本体の数" (len seen) "使わない引数を持つ本体" total)
