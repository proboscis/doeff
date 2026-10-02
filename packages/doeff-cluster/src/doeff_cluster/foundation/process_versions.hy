;;; この process の版の識別を読む(io の層)— Python・cloudpickle・doeff・doeff-vm の版、doeff.do の source の hash、
;;; env の中で動く子 process なら env のキー(DOEFF_RUNTIME_ENV_KEY)。
;;;
;;; 送り手は blob に添え、受け側は remote_model.version-diffs で突き合わせる。読むのは process の外の事実(入っている dist の版・
;;; source の file・環境変数)なので、送る形と判断を置く remote_model.hy(domain)から分けた(#1630 — 純粋な層の
;;; module が remote_model 経由で os・pathlib を読んでいた)。呼ぶのは送り手と受け側の入口と io の handler だけで、
;;; 宣言の組み立て(service_build.system-declaration)には呼び手がこの値を渡す。
(require doeff-hy.macros [deff defk val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import collections.abc [Mapping])
(import functools [cache])
(import hashlib)
(import importlib.metadata)
(import os)
(import pathlib [Path])
(import sys)
(import types [MappingProxyType ModuleType])
(import doeff [run])
(import doeff.do)


(defclass SourceUnidentified [RuntimeError]
  "版の識別に要る module の source の file が無い(frozen の module など)。版を作れないので送りも受けもしない — 呼び手の process を止めるために
   投げる。前は intent の RemoteJobFailed を投げていたが、層 foundation は intent を読めないので foundation の例外にした(#2566)。")


(defn #^ str _source-fingerprint [#^ ModuleType module]
  ;; 同じ dist の版でも source が違えば cloudpickle が値として運ぶ内部の関数(@do の定義の関数等)は食い違う。
  ;; 版の名だけでは足りないので、その file の hash も添える。module は静的に import した物を渡す(名から引き直さない)。
  (setv file module.__file__)
  (when (is file None)
    (raise (SourceUnidentified (.format "{} の source の file が無い(版の識別を作れない)" module.__name__))))
  (setv path (Path file))
  (cut (.hexdigest (hashlib.sha256 (.read-bytes path))) 0 12))


(defn [cache] #^ MappingProxyType _installed-versions []
  "この process に入っている版(Python・cloudpickle・doeff・doeff-vm の dist の版と doeff.do の source の hash)を 1 度だけ読むため —
   どれも process の生きている間は変わらないのに、送り手は blob ごとに呼ぶ(sim-cluster の検 1 本で 367 回・dist の metadata の
   読み 1,101 回 — #1749)。"
  (MappingProxyType
    {"python" (.format "{}.{}.{}{}" sys.version-info.major sys.version-info.minor sys.version-info.micro
                       (if (getattr sys "_is_gil_enabled" None) (if (sys._is-gil-enabled) "" "t") ""))
     "cloudpickle" (importlib.metadata.version "cloudpickle")
     "doeff" (importlib.metadata.version "doeff")
     "doeff-vm" (importlib.metadata.version "doeff-vm")
     ;; doeff.do の名は package の doeff が出す関数 do に解けるので、上の静的な import で読み込んだ module を sys.modules から取る
     "doeff-do" (_source-fingerprint (get sys.modules "doeff.do"))}))


;; env の root の中の子 process が自分の env のキーを受け取る環境変数の名(worker が子へ渡す — job_context の context-from-env と同じ名)。
(val RUNTIME-ENV-KEY-VAR "DOEFF_RUNTIME_ENV_KEY")


(defk process-versions [environ]
  {:pre [(: environ Mapping)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "この process の版の識別を綴るため — 送り手が blob と heartbeat に添え、受け側が突き合わせる(remote_model.version-diffs)。入っている版
   (_installed-versions)に、environ の RUNTIME-ENV-KEY-VAR を名乗り envKey として添える。environ は呼び手が渡す: process の入口と宿の
   handler は os.environ(環境変数は process の中で変わり得るので読むたびに渡す)・env の root の外として名乗る呼び手(sim の送り手)は空。
   Program の中の読み手は自分で読まず、宿の契約の鍵 versions-key を Ask で読む(答えるのは宿の handler — host_contract.hy)。"
  ;; env の root の中の子 process は、その env のキーを名乗る。送り手が env の中で動いていれば送り手も名乗る。両方が名乗る時だけ
  ;; 比べる(remote_model.version-diffs)。
  (val key (.get environ RUNTIME-ENV-KEY-VAR ""))
  {#** (_installed-versions) #** (if key {"envKey" key} {})})


(deff current-versions []  ; defk にできない: 検と使い手の repo の呼び手(後半 #2766 で移して消す)が Program の外で素で呼ぶ
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "この process の版の識別(process-versions に os.environ を渡した物)。本番の呼び手は process-versions と宿の契約の鍵 versions-key へ
   移した(#2765)— 残る呼び手は検と使い手の repo だけで、後半(#2766)で移して消す。"
  (run (process-versions os.environ)))
