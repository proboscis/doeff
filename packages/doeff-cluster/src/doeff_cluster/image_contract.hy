;;; 土台だけの image の約束(設計 worker-runtime-env.md 節 3.5・E13)を Dockerfile の字面で検める。
;;;
;;;   hy -m doeff_cluster.image_contract <Dockerfile> …   違反があれば 1 行ずつ出して終了コード 1
;;;
;;; 約束: image を作り直す理由は (1) 土台の道具の版を変える時 (2) 業務の Python の package が新しい OS の library を要する時 の
;;; 2 種類だけ。だから Dockerfile は
;;;   - 頭の註に「作り直す理由」と 2 種類を書く
;;;   - OS の package は頭の註の表(`#   <名>  (1|2)  <何に使うか>`)に理由の種類つきで載せた物だけを入れる(python の package は載せられない)
;;;   - Python の package・venv・interpreter を入れない(pip・uv の sync / pip / tool / venv / python・venv や site-packages の COPY・
;;;     PYTHONPATH / VIRTUAL_ENV の ENV)
;;;   - npm の道具は版を固定して入れる(`名@x.y.z`)
;;; 業務の code と依存は宣言(RuntimeEnv)から worker が root に用意するので、image には載らない。
(import sys)
(import pathlib [Path])
(import doeff [run])
(import doeff_cluster.shared.core.image_rules [image-contract-violations])


(defn #^ None main []
  "image を build する前に Dockerfile を検め、約束を破る image を作らせないための入口(違反が在れば終了コード 1)。"
  (setv paths (cut sys.argv 1 None))
  (when (not paths)
    (print "使い方: hy -m doeff_cluster.image_contract <Dockerfile> …" :file sys.stderr)
    (sys.exit 2))
  (setv failed False)
  (for [p paths]
    (setv found (run (image-contract-violations (.read-text (Path p) :encoding "utf-8"))))
    (for [v found]
      (print (.format "{}:{}: {} — {}" p v.line v.rule v.text))
      (setv failed True)))
  (sys.exit (if failed 1 0)))


(when (= __name__ "__main__")
  (main))
