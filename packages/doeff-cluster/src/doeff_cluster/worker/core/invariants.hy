;;; worker の業務の不変条件(packages/doeff-cluster/architecture.hy の defservice worker の :invariants が名指す判断 — 条は本番の code を
;;; 持つ package の architecture.hy に 1 か所で宣言する)。
;;;
;;; 条 W1 handoff-keeps-a-ready-writer: 入れ替え(handoff)を宣言した Service は、入れ替えの間も書き手が居続ける — 最初の世代が Ready に
;;; なってから、どの時点でも Ready を報告した生きた process が 1 つ以上在る(旧は新が Ready になった後にだけ止める — worker_policy の
;;; handoff-actions / retired-actions)。判断は記録(世代ごとの最初の Ready の時刻と終わった時刻)を受けて空白の列を返す純関数 1 つ。
;;; 記録を集めるのは検(tests/test_local.hy の handoff の入れ替えの検)。
;;;
;;; 条 C4b stopped-job-leaves-no-descendant(#2940 の 2 段目): job を止め切った後、job の子孫(job が別の session・process group で起こした
;;; 孫を含む)は 1 つも生きていない。止め切りの時刻 = 止めの合図から停止の猶予 + KILL の猶予の後・worker が消えてから shim の期限の後・
;;; job が自分で終わったのを worker が観測した時。守るのは入れ物 shim(worker/entry/shim)の子孫の引き取りと片づけ。判断は記録(止め切りの
;;; 時刻と、子孫ごとに生きているのを最後に見た時刻)を受けて破りの列を返す純関数 1 つ。記録を集めるのは本物の process の検
;;; (tests/test_shim_descendants.hy)。

;;;
;;; 待ちの子(#3646)の条 3 つ。判断(worker/core/policy の plan)の答えと観測を受けて破りの列を返す純関数。条は守りの関数(warm_rules の
;;; warm-key-of・warm-mark-clean)を呼ばずに性質を言い直す — 守りを壊すと破りが出る(失敗ケース = tests/test_warm_child_policy.hy)。
;;; 条 WC1 warm-fork-uses-its-own-root: 待ちの子から分けて起こす task(StartJob の warm-key が在る)は、その task 自身の env の root の
;;;   待ちの子からだけ分かれる(古い root・別の root の待ちの子へ行かない)。
;;; 条 WC2 warm-child-state-leaves-running-tasks: 待ちの子の段階(起こし中・準備済み・失敗・止め中・無い)だけが違う 2 つの観測で、
;;;   走っている process への止めと回収(SignalJob・ReapJob)は同じ(待ちの子が落ちても、そこから分かれた task は落ちない)。
;;; 条 WC3 warm-fork-only-before-any-vm: task を分けるのは、分かれ元の待ちの子が走っていて、準備完了の印が「thread 1 つ・生きた VM 0」の
;;;   時だけ(VM を起こした process から fork しない)。

(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import doeff_cluster.worker.intent.worker_model [StartJob SignalJob ReapJob WorldView])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])


(defk handoff-keeps-a-ready-writer [lifetimes]
  {:pre [(: lifetimes tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "条 W1: 世代ごとの #(最初の Ready の時刻 終わった時刻) の列(Ready を一度も報告しなかった世代は最初が None・まだ動く世代は終わりが
   None)から、最初の Ready から最後の終わりまでの間で Ready の生きた process が 1 つも無い区間 #(始め 終わり) の列を返す(空なら緑)。
   入れ替えの worker が新の準備の間に旧を止めて書き手の空白を作らないことを、筋書きの記録から判じるため。"
  (val spans (sorted (gfor #(ready ended) lifetimes :if (is-not ready None) #(ready (if (is ended None) (float "inf") ended)))))
  (tuple (gfor i (range 1 (len spans))
               :setv covered (max (gfor #(_ end) (cut spans 0 i) end))
               :setv start (get spans i 0)
               :if (< covered start)
               #(covered start))))


(defrecord DescendantLife
  "条 C4b の記録 1 つ = job の子孫 1 つの見え方: pid・label = 筋書きが付けた名(破りの名指しに使う)・last-alive-ms = 生きているのを最後に
   見た時刻(epoch ms — 見張りの間に一度も生きているのを見なければ None。回収を待つだけの zombie は生きていない)。"
  {:tags {:context "worker" :role "type"}}
  (#^ int pid)
  (#^ str label)
  (#^ (| int None) last-alive-ms))


(defrecord DescendantOutlivedTheStop
  "条 C4b の破り 1 つ: 止め切りの時刻 stopped-ms の後に生きているのを見た子孫 life。"
  {:tags {:context "worker" :role "type"}}
  (#^ int stopped-ms)
  (#^ DescendantLife life))


(defk stopped-job-leaves-no-descendant [stopped-ms lives]
  {:pre [(: stopped-ms int) (: lives (get tuple #(DescendantLife ...)))] :post [(: % (get tuple #(DescendantOutlivedTheStop ...)))]
   :tags {:context "worker" :role "judgment"}}
  "条 C4b: 止め切りの時刻(epoch ms)と子孫ごとの見え方の列から、止め切りの後に生きているのを見た子孫を破りの列にして返す(空なら緑)—
   job を止め切った後に子孫が孤児として残らないことを、筋書きの記録から判じるため。止め切りの時刻ちょうどに見たのは破りではない。"
  (tuple (gfor life lives
               :if (and (is-not life.last-alive-ms None) (> life.last-alive-ms stopped-ms))
               (DescendantOutlivedTheStop :stopped-ms stopped-ms :life life))))


(defk warm-fork-uses-its-own-root [actions]
  {:pre [(: actions tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "条 WC1: 判断の答え actions のうち、待ちの子から分ける StartJob で、分かれ元のキーが task 自身の env の root のキー(\"env-\" + 宣言の
   env-key)でない物を破りの列にして返す(空なら緑)— 別の root(古い版の root を含む)の venv で task が走らないため。"
  (tuple (gfor action actions
               :if (and (isinstance action StartJob) (is-not action.warm-key None)
                        (!= action.warm-key (+ ENV-KEY-PREFIX (or action.spec.env-key ""))))
               action)))


(defk warm-child-state-leaves-running-tasks [baseline variant]
  {:pre [(: baseline tuple) (: variant tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "条 WC2: 待ちの子の段階だけが違う 2 つの観測で判断した答え(baseline・variant)の、走っている process への止めと回収(SignalJob・ReapJob)
   の食い違いを破りの列にして返す(空なら緑)— 待ちの子の失敗や止めが、そこから分かれて走っている task を巻き込まないため。"
  (val stops (fn [actions] (frozenset (gfor a actions :if (isinstance a #(SignalJob ReapJob)) a))))
  (tuple (sorted (^ (stops baseline) (stops variant)) :key repr)))


(defk warm-fork-only-before-any-vm [actions world]
  {:pre [(: actions tuple) (: world WorldView)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "条 WC3: 判断の答え actions のうち、待ちの子から分ける StartJob で、分かれ元の待ちの子が観測に無い・終わった・止め始めた・印が無い・
   印の thread が 1 つでない・生きた VM が在る物を破りの列にして返す(空なら緑)— VM や thread を持った process から fork すると、
   分かれた task の中で錠や VM の状態が壊れるため。"
  (val clean-keys (frozenset (gfor view world.warm-children
                                   :if (and (is view.exit-code None) (is view.stop None) (is-not view.mark None)
                                            (= view.mark.threads 1) (not (any view.mark.vm-live)))
                                   view.key)))
  (tuple (gfor action actions
               :if (and (isinstance action StartJob) (is-not action.warm-key None) (not-in action.warm-key clean-keys))
               action)))
