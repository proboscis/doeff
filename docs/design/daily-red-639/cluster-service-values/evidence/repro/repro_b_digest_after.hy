;;; 盲検 B が書いた差分そのもの(controllers/digest の keeper.hy・cluster.hy・envs.hy — 本体は keeper.hy・宣言は cluster.hy)に、
;;; 修正前と修正後の service の本体の検査を当てる。盲検の木は読むだけ(bytecode は PYTHONPYCACHEPREFIX の下へ)。
;;; 使い方: cd <修正後の agora-controllers の root>; hy <この file> <盲検 B の木の root> before <修正前の ADR の file>
;;;                                              hy <この file> <盲検 B の木の root> after
;;; ADR の file は読み込む時に検査の id を登録するので、修正前と修正後は別の process で走らせる。
;;; controllers の package はこの repo の物を使い、controllers.digest だけを盲検の木から引く(package の __path__ に足す)。
(import sys)
(import importlib.util)
(import pathlib [Path])
(import doeff [run])
(import controllers)

(setv tree (Path (get sys.argv 1)) mode (get sys.argv 2))
(.append controllers.__path__ (str (/ tree "controllers")))
(setv digest (/ tree "controllers" "digest"))

(if (= mode "before")
    (do
      (setv spec (importlib.util.spec-from-file-location "m5_before" (get sys.argv 3)))
      (setv before (importlib.util.module-from-spec spec))
      (.exec-module spec.loader before)
      (setv found (run (before.service-body-report tree)))
      (print (.format "修正前(字面の (service \"名\" f) と同じ module の defk・盲検の木の全体): 読めた本体 {}・違反 {}"
                      found.bodies (list found.violations)))
      (setv only-digest [])
      (for [p (sorted (.glob digest "*.hy"))]
        (setv bodies (run (before.service-bodies-in (.read-text p :encoding "utf-8"))))
        (.extend only-digest (lfor #(name _) bodies #(p.name name))))
      (print "修正前: controllers/digest の中で読めた本体 =" only-digest))
    (do
      (import controllers.worker.adr.defadr_worker_business_code_touches_io_only_through_effects :as after)
      (setv report (run (after.service-body-report tree #("controllers/digest"))))
      (print (.format "修正後(宣言の値から本体を引く・controllers/digest): 読めた本体 {}・違反 {}・検められない宣言 {}"
                      report.bodies (lfor v report.violations #(v.path v.name v.word)) (list report.unchecked)))))
