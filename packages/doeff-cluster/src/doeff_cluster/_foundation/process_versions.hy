;;; この process の版の識別を読む(io の層)— Python・cloudpickle・doeff・doeff-vm の版、doeff.do の source の hash、
;;; env の中で動く子 process なら env のキー(DOEFF_RUNTIME_ENV_KEY)。
;;;
;;; 送り手は blob に添え、受け側は remote_model.version-diffs で突き合わせる。読むのは process の外の事実(入っている dist の版・
;;; source の file・環境変数)なので、送る形と判断を置く remote_model.hy(domain)から分けた(#1630 — 純粋な層の
;;; module が remote_model 経由で os・pathlib を読んでいた)。呼ぶのは送り手と受け側の入口と io の handler だけで、
;;; 宣言の組み立て(service_build.system-declaration)には呼び手がこの値を渡す。環境変数の置き場も呼び手が渡す(process の入口と宿の
;;; handler は os.environ・sim の送り手は空 — 旧い current-versions は #2766 で消した)。
(require doeff-hy.macros [defk val <-])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import collections.abc [Mapping])
(import os)
(import functools [cache])
(import hashlib)
(import importlib.metadata)
(import pathlib [Path])
(import sys)
(import types [MappingProxyType ModuleType])
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


;; env の root の中の子 process が自分の env のキーを受け取る環境変数の名(worker が子へ渡す — shared/entry/run_context_env の context-from-env と同じ名)。
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


(defk this-process-versions []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "この process の版の識別を、この process の環境変数から綴るため — 宿を持たない呼び手(手元の道具の送り手・宣言の道具・子の入口の
   突き合わせ)が使う。os.environ の読みを foundation の層に閉じる(入口と protocol の層は os.environ に触らない — DOEFF106・#3014)。"
  (<- versions dict (process-versions os.environ))
  versions)


(defk this-process-environ []
  {:pre [] :post [(: % Mapping)] :tags {:context "doeff-cluster" :role "foundation"}}
  "この process の環境変数の写像を返すため — 入口が Program の外で読む実行先の文脈(shared/entry/run_context_env の context-from-env)の
   材料。os.environ の読みを foundation の層に閉じ、写像は組まずにそのまま渡す(読みの規則は core の context-of-environ の 1 つ — #3014)。"
  os.environ)


(defk clock-ticks []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster" :role "foundation"}}
  "この機体の 1 秒の clock tick の数(/proc/<pid>/stat の starttime の単位 — worker の起動の刻を読む process_clock の handler へ入口が渡す・
   #3676)を返すため。os への問いを foundation の層に閉じる。"
  (os.sysconf "SC_CLK_TCK"))
