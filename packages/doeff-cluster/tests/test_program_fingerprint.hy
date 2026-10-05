;; 詰めた Program の指紋(置き場の鍵 program-sha = 詰めた bytes の sha256 — 宣言の行に載り、宣言し直しが cluster の行と照らす)は、
;; 同じ版の同じ Program なら手元の状態に依らず同じ(#3660)。揺れの元 2 つの失敗ケース:
;;   - 物の共有: Hy の module を今 compile した process(.pyc の置き場が空)では値ごと詰める関数の code の co_filename と globals の
;;     __file__ が同じ object、.pyc から読んだ process では別の object で、pickle の memo の形(中身を書くか前の物を指すか)が変わっていた。
;;   - 宣言を組んだ手元の path: co_filename と __file__ に checkout の絶対 path が入っていた。
;; 見本の module は tmp_path に書き、宣言の道具と同じく別の process で import して詰める(.pyc の置き場は PYTHONPYCACHEPREFIX で分ける)。
;; 受け側(worker の子)は詰めた Program を別の checkout の根から解いて走らせ、例外の traceback に置き場に依らない source の名と行が出る。
(require doeff-hy.macros [deftest defk val])
(import base64)
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff [Pure run])
(import doeff_cluster.shared.protocol.program_codec [encode-program decode-program])
(import doeff_cluster.shared.core.remote_rules [program-sha])


;; 見本の module(package fp_sample の job)。defk の関数は値ごと詰まる(module の名で引くと defk の包みに当たり、中の関数そのものではない)。
(val SAMPLE-SOURCE
  "(require doeff-hy.macros [defk])

(defk sample-job [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context \"doeff-cluster-test\" :role \"entry\"}}
  \"見本の job: n に 1 を足す。\"
  (+ n 1))

(defk sample-boom []
  {:pre [] :post [(: % int)] :tags {:context \"doeff-cluster-test\" :role \"entry\"}}
  \"見本の失敗: 受け側の traceback に source の名と行が出るかを見る。\"
  (raise (ValueError \"fingerprint-sample-boom\")))
")

;; 送り手の子: 見本の根を sys.path に足して import し、名指した Program を詰める。詰める前に見本の .pyc が在ったか(温まっていたか)も返す。
(val SENDER-SCRIPT
  "import importlib.util, json, os, sys
root, name = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
import hy
warm = os.path.exists(importlib.util.cache_from_source(os.path.join(root, 'fp_sample', 'job.hy')))
import fp_sample.job as job
from doeff_cluster.shared.protocol.program_codec import encode_program
program = job.sample_job(1) if name == 'sample-job' else job.sample_boom()
print(json.dumps({'blob': encode_program(program), 'warm': warm}))
")

;; 受け側の子(worker の子と同じく、自分の checkout の根を sys.path に持つ): 詰めた Program を解いて走らせ、答えか traceback を返す。
(val RECEIVER-SCRIPT
  "import json, sys, traceback
root, blob = sys.argv[1], sys.stdin.read()
sys.path.insert(0, root)
import hy
from doeff import run
from doeff_cluster.shared.protocol.program_codec import decode_program
try:
    print(json.dumps({'value': run(decode_program(blob))}))
except ValueError:
    print(json.dumps({'traceback': traceback.format_exc()}))
")


(defk checkout-at [root]
  {:pre [(: root Path)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root に見本の module を置いた checkout を作り、root を返す。"
  (.mkdir (/ root "fp_sample") :parents True)
  (.write-text (/ root "fp_sample" "__init__.hy") "" :encoding "utf-8")
  (.write-text (/ root "fp_sample" "job.hy") SAMPLE-SOURCE :encoding "utf-8")
  root)


(defk child [script argv pycache cwd stdin]
  {:pre [(: script str) (: argv list) (: pycache Path) (: cwd Path) (: stdin str)] :post [(: % dict)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "この venv の python で script を別の process として走らせ、最後の行の JSON を返す。.pyc の置き場は pycache(書く・読む)。"
  (val env (| (dfor #(k v) (.items os.environ) :if (!= k "PYTHONDONTWRITEBYTECODE") k v) {"PYTHONPYCACHEPREFIX" (str pycache)}))
  (val done (subprocess.run [sys.executable "-c" script #* argv] :cwd (str cwd) :env env :input stdin
                            :capture-output True :text True :timeout 50))
  (assert (= done.returncode 0) done.stderr)
  (json.loads (get (.splitlines done.stdout) -1)))


(defk send [root name pycache]
  {:pre [(: root Path) (: name str) (: pycache Path)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "checkout root の見本の Program name を、宣言の道具と同じく別の process で詰める。答え = {blob warm sha}。"
  (val sent (! (child SENDER-SCRIPT [(str root) name] pycache root "")))
  (| sent {"sha" (program-sha (get sent "blob"))}))


(deftest test-the-fingerprint-is-the-same-whether-the-module-was-just-compiled-or-read-from-pyc [tmp-path]
  ;; 失敗ケース: 空の .pyc の置き場で 1 回(Hy が今 compile する)・温まった置き場でもう 1 回(.pyc から読む)詰めて、指紋が同じ。
  (val root (! (checkout-at (/ tmp-path "checkout"))))
  (val pycache (/ tmp-path "pyc"))
  (val cold (! (send root "sample-job" pycache)))
  (val warm (! (send root "sample-job" pycache)))
  (assert (not (get cold "warm")) "1 回目の前に見本の .pyc が在る(compile した時の詰め方を測れていない)")
  (assert (get warm "warm") "2 回目の前に見本の .pyc が無い(.pyc から読んだ時の詰め方を測れていない)")
  (assert (= (get cold "sha") (get warm "sha")) #((get cold "sha") (get warm "sha"))))


(deftest test-the-fingerprint-is-the-same-wherever-the-checkout-is [tmp-path]
  ;; 失敗ケース: 同じ中身の checkout を深さの違う 2 か所に置いて詰め、指紋が同じ。詰めた bytes に checkout の path が入らない。
  (val here (! (send (! (checkout-at (/ tmp-path "here"))) "sample-job" (/ tmp-path "pyc-here"))))
  (val there (! (send (! (checkout-at (/ tmp-path "far" "away" "there"))) "sample-job" (/ tmp-path "pyc-there"))))
  (assert (= (get here "sha") (get there "sha")) #((get here "sha") (get there "sha")))
  (assert (not-in (.encode (str tmp-path) "utf-8") (base64.b64decode (get here "blob"))) "詰めた bytes に checkout の path が残っている"))


(deftest test-the-fingerprint-is-the-same-whether-equal-strings-are-one-object-or-two
  ;; 失敗ケース(速い形): 物の共有だけが違う 2 つの値 — 同じ値の文字列が 1 つの object か、別の 2 つの object か — の指紋が同じ。
  (val text (.join "" ["fingerprint-" "shared-value-" "shared-value"]))
  (val other (.join "" (list text)))
  (assert (and (= text other) (is-not text other)) "見本の 2 つの文字列が同じ値の別の object になっていない")
  (val one (encode-program (Pure #(text text))))
  (val two (encode-program (Pure #(text other))))
  (assert (= (program-sha one) (program-sha two)) #((program-sha one) (program-sha two)))
  (assert (= (run (decode-program two)) #(text text))))


(deftest test-a-program-sent-from-one-checkout-runs-from-another-and-its-traceback-names-the-portable-source [tmp-path]
  ;; 受け側の道が壊れない: 送り手の checkout で詰めた Program を、別の checkout の根(と別の cwd)から解いて走らせる。答えが返り、
  ;; 例外の traceback には置き場に依らない source の名(fp_sample/job.hy)と、受け側の checkout から引いた source の行が出る。
  (val sender (! (checkout-at (/ tmp-path "sender"))))
  (val receiver (! (checkout-at (/ tmp-path "receiver"))))
  (val cwd (/ tmp-path "cwd"))
  (.mkdir cwd)
  (val ok (! (child RECEIVER-SCRIPT [(str receiver)] (/ tmp-path "pyc-r")
                    cwd (get (! (send sender "sample-job" (/ tmp-path "pyc-s"))) "blob"))))
  (assert (= (get ok "value") 2) ok)
  (val boom (get (! (child RECEIVER-SCRIPT [(str receiver)] (/ tmp-path "pyc-r")
                           cwd (get (! (send sender "sample-boom" (/ tmp-path "pyc-s"))) "blob")))
                 "traceback"))
  (assert (in "File \"fp_sample/job.hy\"" boom) boom)
  (assert (in "(raise (ValueError \"fingerprint-sample-boom\"))" boom) boom)
  (assert (not-in (str sender) boom) boom))
