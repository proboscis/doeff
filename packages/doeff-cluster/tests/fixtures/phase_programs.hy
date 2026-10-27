;;; 世界の区切りの印(card ki-a27b195a3f92 — 歩数の上限は世界が立ち上がってから止め始めるまでの走りの歩数だけを数える)の検の
;;; 見本の系: 起動の時だけ仕事をする service(本番の部品の起動の追いつきの代わり)と、周ごとに起きて少し仕事をする service(空回りの
;;; 代わり)。部品を足した系と、空回りを 2 倍にした系を並べる。
(require doeff-hy.macros [defk defsystem <- var])
(import collections.abc [Callable])
(import doeff_core_effects.effects [Put])
(import doeff_time [Delay])


(defk one-unit [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "仕事 1 つ分の代わり(土台の session の値の置き場へ 1 つ書く effect 1 つ — VM を何歩か回す)で数を 1 つ進めるため。"
  (<- (Put "phase-work" n))
  (+ n 1))


(defk warm-up-then-rest [work]
  {:pre [(: work int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "起き上がった刻に work 回の仕事をし(本番の部品の記録の追いつきの代わり)、その後は周の仕事を持たずに休むため。"
  (var done 0)
  (for [_ (range work)]
    (<- next-done int (one-unit done))
    (:= done next-done))
  (<- (Delay 3600.0))
  done)


(defk idle-ticks [every]
  {:pre [(: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "every 秒ごとに起きて仕事を 20 回する(何も起きていない世界の周の費用 — 空回りの代わり)。止められるまで続く。"
  (var n 0)
  (while True
    (for [_ (range 20)]
      (<- next-n int (one-unit n))
      (:= n next-n))
    (<- (Delay every)))
  n)


(defk warm-program [foundation work]
  {:pre [(: foundation Callable) (: work int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "起動の時だけ仕事をする service。"
  (<- done int (foundation (warm-up-then-rest work)))
  done)


(defk ticker-program [foundation every]
  {:pre [(: foundation Callable) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "周ごとに起きる service。"
  (<- n int (foundation (idle-ticks every)))
  n)


(defsystem phase-world [#^ Callable foundation]
  "土台の世界: 0.5 秒ごとに起きる service 1 つと、起動の時だけ仕事をする service 1 つ"
  (ticker (ticker-program foundation 0.5) :replicas 1 :needs #{"cluster-net"})
  (warm-a (warm-program foundation 2000) :replicas 1 :needs #{"cluster-net"}))


(defsystem phase-world-plus-part [#^ Callable foundation]
  "土台の世界に、起動の時だけ仕事をする service を 1 つ足した世界(部品を足した)"
  (ticker (ticker-program foundation 0.5) :replicas 1 :needs #{"cluster-net"})
  (warm-a (warm-program foundation 2000) :replicas 1 :needs #{"cluster-net"})
  (warm-b (warm-program foundation 2000) :replicas 1 :needs #{"cluster-net"}))


(defsystem phase-world-double-idle [#^ Callable foundation]
  "土台の世界の周の service を 0.25 秒ごとに起こした世界(空回りを 2 倍にした)"
  (ticker (ticker-program foundation 0.25) :replicas 1 :needs #{"cluster-net"})
  (warm-a (warm-program foundation 2000) :replicas 1 :needs #{"cluster-net"}))
