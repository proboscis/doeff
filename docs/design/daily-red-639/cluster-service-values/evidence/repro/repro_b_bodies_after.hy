;;; 盲検 B の反例の再現(修正後): repro_b_bodies.hy と同じ 4 つの宣言の形の木に、宣言の値から本体を引く検査を当てる。
;;; 使い方: cd <agora-controllers の root>; hy <この file> <作業 dir(/tmp の下)>
;;; 修正後の検査は宣言を作りうる module を import するので、木は一意な package の名で置き、sys.path に足す。最後に本物の repo で、
;;; 読めた本体の数・検められない宣言・違反を印字する。
(require doeff-hy.macros [<-])
(import sys shutil)
(import pathlib [Path])
(import doeff [run])
(import controllers.worker.adr.defadr_worker_business_code_touches_io_only_through_effects :as adr)

(setv work (Path (get sys.argv 1)))
(when (.exists work) (shutil.rmtree work))

(for [#(label body) [#("直の I/O の本体" adr.BAD-BODY) #("effect だけの本体" adr.GOOD-BODY)]]
  (print "==" label)
  (for [#(shape files) (.items (adr.service-shapes body))]
    (setv package (+ "repro_b_" (.replace shape "-" "_") (if (is body adr.BAD-BODY) "_bad" "_good")))
    (setv root (/ work shape (if (is body adr.BAD-BODY) "bad" "good")))
    (for [#(name text) (.items files)]
      (setv p (/ root package name))
      (.mkdir p.parent :parents True :exist-ok True)
      (.write-text p (.replace text "PACKAGE" package) :encoding "utf-8"))
    (.insert sys.path 0 (str root))
    (setv report (run (adr.service-body-report root #(package))))
    (print (.format "  {:24} 読めた本体 {}・違反 {}・検められない宣言 {}" shape report.bodies
                    (lfor v report.violations v.word) (lfor u report.unchecked u.reason)))))

(setv here (Path.cwd))
(setv report (run (adr.service-body-report here #("controllers" "services"))))
(print (.format "本物の repo: 読めた本体 {}・違反 {}・検められない宣言 {}" report.bodies (list report.violations) (list report.unchecked)))
