;;; 耐久の差分と版の差分の近道(#2716)— 欄が前後で同じ object なら、その欄の鍵を作らずに飛ばす。
;;;
;;; 固定すること:
;;;   - durable-delta は、欄の組(durable_kv の SOURCE-GROUPS)のうち欄が前後で全部同じ object の組の鍵の部品を作らない(数える形で —
;;;     組ごとに鍵を作る関数を Mock で包み、呼ばれた回数を見る)。1 欄だけ書いた歩では、その欄の組だけを前後 1 回ずつ作る。
;;;   - 全部の欄が同じ object の歩は、差分が空で、どの組の鍵も作らない。
;;;   - 失敗ケース: 欄が別の object で中身が違えば、差分に入る(丸ごとの直列化の差分と 1 字も違わない)— 同じ object の判定が中身の変化を
;;;     見逃さない。別の object で中身が同じなら、差分に入らない(直列化して比べる側へ回る)。
;;;   - (#2767 の失敗ケース「durable-delta を素で呼ぶと差分でなく Program が返り、差分の読みで名指して落ちる」は、#2821 の案 A-1 から
;;;     linter の DOEFF126(素の defk 呼び・critical)が検の dir でも静的に名指すので、実行時の検から外した — 呼び手は下のとおり `!` で
;;;     答えを受ける。linter の側の失敗ケースは packages/doeff-linter/tests/out_of_scope.rs。)
;;;   - resource_policy.moved-names は、写像が同じ object なら空・別の写像は値が同じ物でない鍵だけ。
;;; 前提: 状態は replace で作り直し、欄の写像をその場で書き換えない(durable_kv・resource_policy の頭の註と同じ前提 — 以前の鍵ごとの
;;;   同一性の比べも同じ前提に立つ)。
(require doeff-hy.macros [deftest val])
(import dataclasses [replace])
(import unittest.mock [Mock])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.durable_kv :as dk)
(import doeff_cluster.coordinator.core.resource_policy [moved-names])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val WORKERS #("workers" "seen_marks"))


(deftest test-a-step-that-writes-one-field-builds-only-that-group [monkeypatch]
  ;; 1 欄(workers)だけ書いた歩: 組ごとに鍵を作る関数の呼ばれた回数を数え、workers の組だけが前後 1 回ずつ。
  (val s (get (responded (ClusterState) (http-request "POST" "/heartbeat" {} {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []}
                                                       :actor "c-test") 1000 T) 0))
  (val served (get (responded s (http-request "POST" "/resources/Service" {} {"name" "a" "spec" {"revision" "r" "needs" ["net"] "run" SAMPLE-RUN}}
                                              :actor "c-test") 1500 T) 0))
  (val changed (replace served :workers (dfor #(k w) (.items served.workers) k (replace w :capacity (+ w.capacity 1)))))
  (val spies (tuple (gfor g dk.SOURCE-GROUPS #(g.fields (Mock :wraps g.build)))))
  (.setattr monkeypatch dk "SOURCE_GROUPS" (tuple (gfor #(fields spy) spies (dk.SourceGroup :fields fields :build spy))))
  (val got (! (dk.durable-delta served changed)))
  (val built (dfor #(fields spy) spies fields spy.call-count))
  (assert (= (get built WORKERS) 2) built)
  (assert (= (sum (gfor #(fields n) (.items built) :if (!= fields WORKERS) n)) 0) built)
  (assert (and got (all (gfor k got (.startswith k "worker/")))) got))


(deftest test-a-step-where-every-field-is-the-same-object-builds-nothing [monkeypatch]
  ;; 全部の欄が同じ object の歩(replace だけ)は、差分が空で、どの組の鍵も作らない。
  (val s (get (responded (ClusterState) (http-request "POST" "/heartbeat" {} {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []}
                                                       :actor "c-test") 1000 T) 0))
  (val spies (tuple (gfor g dk.SOURCE-GROUPS #(g.fields (Mock :wraps g.build)))))
  (.setattr monkeypatch dk "SOURCE_GROUPS" (tuple (gfor #(fields spy) spies (dk.SourceGroup :fields fields :build spy))))
  (assert (= (! (dk.durable-delta s (replace s))) {}))
  (assert (= (sum (gfor #(_ spy) spies spy.call-count)) 0) spies))


(deftest test-counterexample-a-new-field-object-with-changed-content-is-not-skipped
  ;; 失敗ケース: 欄が別の object で中身が違う(capacity を 1 足した worker)— 差分に入り、丸ごとの直列化の差分と同じ。同じ object の
  ;; 判定が中身の変化を見逃さない。別の object で中身が同じ(作り直しただけ)なら差分に入らない。
  (val s (get (responded (ClusterState) (http-request "POST" "/heartbeat" {} {"name" "atlas" "provides" ["net"] "capacity" 2 "statuses" []}
                                                       :actor "c-test") 1000 T) 0))
  (val changed (replace s :workers (dfor #(k w) (.items s.workers) k (replace w :capacity (+ w.capacity 1)))))
  (val old (dk.durable-kv s))
  (val new (dk.durable-kv changed))
  (val by-full (| (dfor #(k v) (.items new) :if (!= (.get old k) v) k v) (dfor k old :if (not-in k new) k None)))
  (val got (! (dk.durable-delta s changed)))
  (assert (= got by-full) #(got by-full))
  (assert (= (sorted got) ["worker/atlas"]) got)
  (val rebuilt (replace s :workers (dict s.workers)))
  (assert (is-not rebuilt.workers s.workers))
  (assert (= (! (dk.durable-delta s rebuilt)) {})))


(deftest test-moved-names-skips-the-same-mapping-and-names-replaced-values
  (val one (object))
  (val two (object))
  (val before {"a" one "b" two})
  (assert (= (moved-names before before) (frozenset)))
  (assert (= (moved-names before (dict before)) (frozenset)))
  (assert (= (moved-names before {"a" one "b" (object)}) (frozenset ["b"])))
  (assert (= (moved-names before {"a" one}) (frozenset ["b"]))))
