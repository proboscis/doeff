;; job の宣言の指紋 spec-hash(doeff_cluster.shared.core.job_rules)の値と、値ごとに 1 度だけ計って値の中に覚える形(JobSpec.fingerprint — #3774)。
;;
;; - 指紋の値は覚える形にする前と同じ(固定の値の指紋が、前の版で計った文字列と一致する)。
;; - 覚えた指紋は古くならない: 比べる欄を 1 つ替えた値は別の指紋になり、同じ欄で新しく作った値の指紋と一致する。
;; - 比べない欄(placement・handoff など)だけを替えた値は同じ指紋になる。
;; - 覚えの寿命は値と同じ: 同じ欄の別の値は、読むまで指紋を持たない(値をまたいで覚えを分けない)。
(require doeff-hy.macros [defk deftest val])
(import dataclasses [replace])
(import hashlib)
(import json)
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.core.job_rules [spec-hash])


(val BASE (JobSpec "svc" "pkg.entry" #("service" "--identity" "abc") "rev1"))

;; 覚える形にする前の版(doeff 0201f7cc7)の spec-hash で計った値。
(val GOLDEN [#((JobSpec "a" "m" #("x" "y") "r1") "94f50811d9bce4fd")
             #((JobSpec "a" "m" #("x" "y") "r1" :once True :runtime-env "{\"k\":1}" :environ #(#("A" "1") #("B" "ü")))
               "dec1fef94656ce60")
             #((JobSpec "t/1" "m" #() "r2" :placement 3 :handoff True :program "sha" :versions #()) "72d20e1c2eced251")])

;; 比べる欄を 1 つずつ替えた値(指紋が変わるべき物)。
(val COMPARED-CHANGES [{"name" "svc2"} {"entry" "pkg.other"} {"args" #("service" "--identity" "abd")} {"revision" "rev2"}
                       {"once" True} {"runtime_env" "{\"root\":\"x\"}"} {"environ" #(#("A" "1"))}])

;; 比べない欄だけを替えた値(指紋が変わらないべき物)。
(val UNCOMPARED-CHANGES [{"placement" 7} {"handoff" True} {"ready_instance" "i-1"} {"handoff_abandoned" True} {"detached" True}
                         {"env_key" "k"} {"program" "sha" "versions" #()} {"keep_when_cut_off" True} {"hold_version" True}
                         {"versions" #(#("doeff" "1"))}])


(defk fresh-hash [spec]
  {:pre [(: spec JobSpec)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "覚えを使わずに指紋を計り直すため(テストの中だけの照合の元 — 実装と同じ材料と綴りで毎回 sha256 を計る)。"
  (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (+ [spec.name spec.entry (list spec.args) spec.revision spec.once]
                                                           (if spec.runtime-env [spec.runtime-env] [])
                                                           (if spec.environ [(lfor #(k v) spec.environ [k v])] []))
                                                        :ensure-ascii False :separators #("," ":"))
                                            "utf-8")))
       0 16))


(deftest test-the-spec-hash-is-the-same-as-before-it-was-remembered
  ;; 覚える形にしても指紋の値は変わらない(worker と coordinator が版をまたいで同じ指紋を比べる)。
  (for [#(spec want) GOLDEN]
    (assert (= (spec-hash spec) want) #(spec (spec-hash spec) want))
    ;; 2 度目(覚えた値)も同じ。
    (assert (= (spec-hash spec) want) #(spec (spec-hash spec) want))))


(deftest test-a-value-with-one-compared-field-changed-gets-its-own-spec-hash
  ;; 先に元の値の指紋を読んで覚えさせてから欄を替える — 覚えが替えた後の値へ漏れれば元の指紋が返って赤になる。
  (val base-hash (spec-hash BASE))
  (for [change COMPARED-CHANGES]
    (val changed (replace BASE #** change))
    (assert (!= (spec-hash changed) base-hash) change)
    (assert (= (spec-hash changed) (! (fresh-hash changed))) change)
    ;; 同じ欄で新しく作った値とも一致する。
    (assert (= (spec-hash changed) (spec-hash (replace BASE #** change))) change))
  (assert (= (spec-hash BASE) base-hash)))


(deftest test-two-specs-that-differ-in-one-field-have-different-spec-hashes
  ;; 欄が 1 つ違う 2 つの値は、どちらを先に読んでも別の指紋(値を作っては捨てる順でも — 消えた値の覚えを別の値が拾わない)。
  (for [change COMPARED-CHANGES]
    (val a (JobSpec "svc" "pkg.entry" #("service" "--identity" "abc") "rev1"))
    (val b (replace a #** change))
    (val hb (spec-hash b))
    (val ha (spec-hash a))
    (assert (!= ha hb) change)
    (assert (= ha (! (fresh-hash a))) change)
    (assert (= hb (! (fresh-hash b))) change))
  (for [i (range 200)]
    (val spec (JobSpec "svc" "pkg.entry" #("n" (str i)) "rev1"))
    (assert (= (spec-hash spec) (! (fresh-hash spec))) i)))


(deftest test-changing-only-uncompared-fields-keeps-the-spec-hash
  ;; 割り当ての世代・入れ替えの形などの比べない欄は指紋に入らない(変わっても process を起こし直さない)。
  (val base-hash (spec-hash BASE))
  (for [change UNCOMPARED-CHANGES]
    (assert (= (spec-hash (replace BASE #** change)) base-hash) change)))


(deftest test-the-remembered-spec-hash-lives-in-the-value
  ;; 覚えは値の中だけ: 読んだ値は持ち、同じ欄で別に作った値は読むまで持たない(module の大域で値をまたいで覚えない)。
  (val one (JobSpec "svc" "pkg.entry" #("service" "--identity" "abc") "rev1"))
  (val two (JobSpec "svc" "pkg.entry" #("service" "--identity" "abc") "rev1"))
  (assert (not-in "fingerprint" (vars one)))
  (val h (spec-hash one))
  (assert (= (get (vars one) "fingerprint") h))
  (assert (not-in "fingerprint" (vars two)))
  (assert (= one two))
  (assert (= (spec-hash two) h)))
