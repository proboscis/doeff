;;; この process の版の識別を読む(io の層)— Python・cloudpickle・doeff・doeff-vm の版、doeff.do の source の hash、
;;; env の中で動く子 process なら env のキー(DOEFF_RUNTIME_ENV_KEY)。
;;;
;;; 送り手は blob に添え、受け側は remote_model.version-diffs で突き合わせる。読むのは process の外の事実(入っている dist の版・
;;; source の file・環境変数)なので、送る形と判断を置く remote_model.hy(domain)から分けた(#1630 — 純粋な層の
;;; module が remote_model 経由で os・pathlib を読んでいた)。呼ぶのは送り手と受け側の入口と io の handler だけで、
;;; 宣言の組み立て(service_model.system-declaration)には呼び手がこの値を渡す。
(import hashlib)
(import importlib.metadata)
(import os)
(import pathlib [Path])
(import sys)
(import types [ModuleType])
(import doeff.do)
(import doeff_cluster.remote_model [RemoteJobFailed])


(defn #^ str _source-fingerprint [#^ ModuleType module]
  ;; 同じ dist の版でも source が違えば cloudpickle が値として運ぶ内部の関数(@do の定義の関数等)は食い違う。
  ;; 版の名だけでは足りないので、その file の hash も添える。module は静的に import した物を渡す(名から引き直さない)。
  (setv file module.__file__)
  (when (is file None)
    (raise (RemoteJobFailed (.format "{} の source の file が無い(版の識別を作れない)" module.__name__))))
  (setv path (Path file))
  (cut (.hexdigest (hashlib.sha256 (.read-bytes path))) 0 12))


(defn #^ dict current-versions []
  "この process の版の識別。送り手が blob に添え、受け側が突き合わせる。"
  {"python" (.format "{}.{}.{}{}" sys.version-info.major sys.version-info.minor sys.version-info.micro
                     (if (getattr sys "_is_gil_enabled" None) (if (sys._is-gil-enabled) "" "t") ""))
   "cloudpickle" (importlib.metadata.version "cloudpickle")
   "doeff" (importlib.metadata.version "doeff")
   "doeff-vm" (importlib.metadata.version "doeff-vm")
   ;; doeff.do の名は package の doeff が出す関数 do に解けるので、上の静的な import で読み込んだ module を sys.modules から取る
   "doeff-do" (_source-fingerprint (get sys.modules "doeff.do"))
   ;; env の root の中の子 process は、その env のキーを名乗る(worker が DOEFF_RUNTIME_ENV_KEY で渡す)。送り手が env の中で動いていれば
   ;; 送り手も名乗る。両方が名乗る時だけ比べる(remote_model.version-diffs)。
   #** (let [key (os.environ.get "DOEFF_RUNTIME_ENV_KEY" "")] (if key {"envKey" key} {}))})
