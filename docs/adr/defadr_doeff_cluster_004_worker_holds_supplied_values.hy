;;; Executable ADR: API server が止まっている間に置き直された job も、Secret・ConfigMap の参照の値を得て起動できるよう、worker が自分に
;;; 置かれうる job の参照の値の写しを持ち、job の読みは API server に届かない時だけその写しで答える。
;;;
;;; 出自 = card ki-bda3364cb633(原因の実測は card ki-c1ea3acb8e09)。2026-10-29 23:30 JST に API server の在る機体 eos が止まり、eos で
;;; 動いていた job が pool の atlas へ置き直されたが、起動で参照の値を API server に問い、届かずに 16 分で 17 回落ちた。それまで
;;; supplied-values の、API server が止まった時の振る舞いを決めた ADR は無かった(docs/adr を「supplied」で読んで確かめた — 2026-10-30)。
;;;
;;; 戻し方: この ADR を足した commit を revert する。写しの file(<作業の根>/supplied-held)は残るが、誰も読まない(消すなら手で消す)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest <-])
(import doeff_cluster.shared.core.supplied_spelling :as spelling)
(import doeff_cluster.shared.core.supplied_spelling [supplied-spellings])
(import doeff_cluster.shared.protocol.supplied_values :as reader)


(defadr ADR-DOE-CLUSTER-004
  :title "worker は、自分に置かれうる job の宣言の :environ の ConfigMap・Secret の参照(coordinator が heartbeat の返事の supplied で渡す綴り)の値を、API server が答えている間に作業の根の下の写し(file 0600・dir 0700)として持つ。job の読みは API server に届かない時(HttpFailed)だけ写しで答え、届いて断られた時は写しを読まない"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/shared/core/supplied_spelling.hy"
          "packages/doeff-cluster/src/doeff_cluster/shared/protocol/supplied_values.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/replies.hy"
          "packages/doeff-cluster/src/doeff_cluster/worker/protocol/coordinator_link.hy"
          "packages/doeff-cluster/src/doeff_cluster/worker/protocol/supplied_hold.hy"
          "packages/doeff-cluster/src/doeff_cluster/worker/entry/main.hy"
          "packages/doeff-cluster/src/doeff_cluster/_sim/k8s_supplied.hy"
          "packages/doeff-cluster/tests/test_supplied_held.hy"
          "docs/adr/defadr_doeff_cluster_004_worker_holds_supplied_values.hy"]
  :problem
    [(fact
       "job は宣言の :environ の参照の値を、起動の時に supplied-setting・supplied-directory で自区画の API server への GET 1 本で読む。届かないと SuppliedValueUnavailable で終わり、値を得る道は API server の他に無かった。"
       :evidence "shared/protocol/supplied_values.hy の held-entries と entries-of-answer(この ADR の前の形)")
     (fact
       "区画の API server は 1 台の機体の上に在る。その機体が止まると、どの機体の worker も値を得られない。job を 2 台に置けるようにしても、移る訳がその機体の停止の時は、移り先も起動に同じ API server が要り、必ず起動できない(移り先が、移る原因と同じ障害の範囲に入っている)。"
       :evidence "card ki-c1ea3acb8e09 の実測(10-29 23:31〜23:47 JST・atlas の job が ConnectTimeout で 17 回落ちた)")]
  :context
    [(interpretation
       "control plane を複数台にする事・起こし直しの間隔を変える事はこの ADR の外。値の得方そのものを、API server の停止に耐える形にする。")
     (interpretation
       "写しを job が読んだ時だけ書く形(読めた値を覚える)は採らない — 止まった機体から移って来る job は、移り先でまだ 1 度も読んでいない。置かれうる job の参照を前もって持つには、置き先の判断を持つ coordinator が worker ごとに綴りを並べる。")
     (interpretation
       "値を coordinator や記録の店に載せて配る形は採らない — Secret の中身を coordinator の task の行や記録の店に載せない(supplied_values の頭の註の決まり)。coordinator が運ぶのは綴りだけで、値は各 worker が自分の身元で API server から読む。")]
  :decision
    [(rule R1 "coordinator は heartbeat の返事に supplied を載せる: その worker に置かれうる job(置く対象の宣言のうち pin と効く needs が合う物 — 生死・drain・空きは見ない)の宣言の :environ の、参照の綴り(shared/core/supplied_spelling の綴りの値)を重ねずに並べた列。名乗っていない worker・退いた世代への返事には欄を書かない(空の列は「何も持たない」の意味で、写しを全部消すので、知らない時に渡さない)。")
     (rule R2 "Pod の worker(区画の CA で API server を検める口を組む worker)は値の写しの係を背景の task として回す。係は返事の綴りが変わった時・前に全部を読めてから 5 分を過ぎた時・前の回に届かない物が在った時に、綴りを物ごとに束ねて API server に 1 本ずつ問い、写しを <作業の根>/supplied-held/<worker の名>/<種類>/<namespace>/<名>.json に置き換えで書く(file 0600・作業の根から下の dir の全部の段 0700)。値 1 つの参照は宣言が名指したキーだけ、物 1 つ丸ごとの参照は全部のキーを持つ。届かない・token が読めない・401・5xx は前の写しを残し、403・404 は写しを消し、綴りから外れた写しは消す。")
     (rule R3 "job の読み(held-entries)は、API server に届かない(HttpFailed)時だけ、job を回す worker の写し(機体の事実 WORK_ROOT と WORKER_NAME で引く)で答え、参照の綴りと写しを読んだ刻を warning の 1 行に出す。届いて断られた時(403・404 ほか)は写しを読まず今と同じく断る。写しも無ければ、届かない理由に写しの無い訳を添えて SuppliedValueUnavailable で断る。")
     (rule R4 "値は log・coordinator の返事と task の行・記録の店に出さない。出すのは参照の綴り・数え・刻だけ。本番と模擬で同じ関数(hold-supplied・held-entries)が動き、模擬は doeff_cluster._sim.k8s_supplied の down の世界で試す。")]
  :laws
    [(law a-job-placed-while-the-api-server-is-down-starts-from-the-copy
       :statement "for_all 参照 r ∈ supplied(w) で、API server が答えていた間に w の係が r の写しを持った worker w: API server に届かない間に w に置かれた job の r の読みは、写しの値で答える"
       :counterexamples
         [(counterexample "写しを持たない — API server の機体が止まると、移り先の job も起動で落ち続ける(10-29 23:31〜)")
          (counterexample "写しを job が読んだ時だけ書く — 止まった機体から移って来た job は移り先で 1 度も読んでいない")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-a-job-placed-while-the-api-server-is-down-starts-from-the-worker-copy"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-a-directory-written-from-the-copy-has-every-held-key"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-the-coordinator-names-only-the-spellings-of-jobs-the-worker-could-take"]
       :wiring "配線済み(2026-10-30・card ki-bda3364cb633)")
     (law a-refused-value-is-not-answered-from-the-copy
       :statement "for_all 参照 r: API server が届いて r を断った(403・404)時、r の読みは写しで答えず断る ∧ 係はその写しを消す。写しの無い r・別の worker の写し・宣言が名指さないキーは、届かない時も写しで答えない"
       :counterexamples
         [(counterexample "届いた断りでも写しで答える — 消された・権限を外された値を使い続ける")
          (counterexample "写しを worker の名で分けない — 同じ作業の根を分ける 2 つの worker が、互いの写しを「綴りから外れた」と消し合う")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-a-refused-or-unheld-reference-is-not-answered-from-a-copy"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-the-copy-follows-the-declarations-and-the-api-server-answers"]
       :wiring "配線済み(2026-10-30・card ki-bda3364cb633)")
     (law the-copy-is-private-and-its-values-stay-out-of-the-log
       :statement "for_all 写しの file f と、その上の dir d(作業の根より下): mode(f) = 0600 ∧ mode(d) = 0700 ∧ 係と読みの log の行・coordinator の返事に値は字のままでも base64 でも載らない"
       :counterexamples
         [(counterexample "MakeDirectory の mode に頼って途中の段を umask の mode で作る — 途中の段が他人の読める dir に成る")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-the-copy-is-private-and-its-values-stay-out-of-the-log"]
       :wiring "配線済み(2026-10-30・card ki-bda3364cb633)")
     (law the-coordinator-and-the-reader-share-one-spelling
       :statement "coordinator が並べる綴りと、値を読む部品が受ける綴りは、shared/core/supplied_spelling の同じ正規表現"
       :counterexamples
         [(counterexample "2 か所に綴りを書き、片方だけを変える — coordinator が並べない参照を job が読み、写しが無い")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_004_worker_holds_supplied_values.hy::test-adr-doe-cluster-004-one-spelling"]
       :wiring "配線済み(2026-10-30・card ki-bda3364cb633)")]
  :enforcement
    [(deftest test-adr-doe-cluster-004-one-spelling
       ;; 読む部品の綴りは shared/core の綴りそのもの(R1・R3 — 写しを持つ物と読む物が同じ字で決まる)。
       (assert (is reader.REF-SPELLING spelling.REF-SPELLING))
       (assert (is reader.OBJECT-SPELLING spelling.OBJECT-SPELLING))
       ;; 参照でない値(URL・path)は並べず、値 1 つと物 1 つ丸ごとの参照は並べる。
       (<- found tuple (supplied-spellings #(#("A" "secret:ns/name/key") #("B" "configmap:ns/obj") #("C" "https://example.invalid/x")
                                             #("D" "/etc/x") #("E" "secret:ns/name/key"))))
       (assert (= found #("configmap:ns/obj" "secret:ns/name/key")) found))]
  :plans ["card ki-bda3364cb633"])
