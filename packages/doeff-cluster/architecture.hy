;;; doeff-cluster の service と業務の不変条件の宣言(doeff-linter が実行せずに読む — doeff は monorepo
;;; なので、本番の code を持つ package の根に置き、linter の走査の根をこの package にする)。
;;;
;;; 読むのは この dir で linter を走らせた時だけ(設定は同じ dir の pyproject.toml の [tool.doeff-linter] — linter は今の dir から上へ設定を
;;; 探すので、doeff の根で走る hook と make lint-doeff は根の pyproject.toml を読み、この宣言を読まない)。
;;; 層の置き場(#1988 の決め・移し方 = #2021 / #1976): service(coordinator・worker・record-store)の dir の下に層
;;; core / intent / protocol / entry、共有の部品は shared/<層>/、本物の I/O は foundation/。移しは子ごとに進め(#2022 で coordinator の core と
;;; entry から)、まだ src/doeff_cluster/ に平たく在る module は pyproject.toml で DOEFF114・115 の対象外のまま(#2095 で外す)。
;;;
;;; 条と確かめる検:
;;;   C1 acknowledged-writes-survive(doeff_cluster.coordinator.core.coordinator_invariants:acknowledged-writes-survive)— 返事を返した盤の行は、coordinator が
;;;   止まり置き場から作り直された後も残る。確かめるのは tests/test_local.hy の
;;;   test-a-stopped-coordinator-is-recreated-from-its-store-after-the-downtime(止める前の行と作り直した後の行を判断に渡す)。
;;;   壊した置き場の反例を deftest で結ぶ形(DOEFF167)は別に足す。
;;;   C2 one-place-per-job(doeff_cluster.coordinator.core.coordinator_invariants:one-place-per-job — #2804)— 入れ替えを宣言しない job は、
;;;   担い手が途絶しても(処理の止まり・網の途絶)、その間に能力の合う worker が加わっても・宣言の needs が変わっても・置ける worker が
;;;   退いても(drain・Worker の削除)、同時に 2 つ走らない(違う worker の上でも、同じ名の worker の新しい世代の上でも)。他へ移せる job は時間の柵(worker の fence が coordinator の
;;;   移し替えより先)・他へ移せない job は「移さない」(途絶しても動かし続けてよい印を渡した担い手から、担い手が印を持たないと知らせるか
;;;   Worker が消されるまで移さない)が守る。同じ名の worker の新しい世代(k8s が届かない node の Pod を追い出して作り直した物)とは、印の
;;;   在る job も長い方の柵 ClusterTiming.keep-fence-ms(240 秒)で止まることで重ならない — 前提: 本番の worker の Deployment は
;;;   strategy = Recreate で not-ready / unreachable の tolerations を manifest に足していない → admission の既定 tolerationSeconds 300 秒 +
;;;   k8s v1.32 の node-monitor-grace-period の既定 50 秒 → 届かない node の Pod の新しい世代が来るのは早くても約 350 秒後(2026-10-02 14:24 の
;;;   実測 約 6.6 分)。keep-fence-ms 240 秒 + 停止の猶予 15 秒 = 255 秒 < 350 秒。tolerations を短くする manifest の変更はこの前提を崩す。
;;;   確かめるのは tests/test_keep_when_cut_off.hy の途絶の筋書き(job の process ごとの生きていた区間を判断に渡す)。失敗ケースは同じ file の
;;;   test-a-counterexample-worker-that-ignores-the-fence-breaks-c2(sim の宿の fence の判断を使わない壊れた worker — SimWorker の
;;;   ignores-fence — で、移せる先の在る job が移し替えの後に 2 か所で走り、C2 が重なりを名指す)。2026-10-02 の本番の途絶 3 つ(処理の止まり
;;;   47 秒・網の途絶 115 秒とその最中の coordinator の止まり 20.2 秒・途絶の 30 秒目の 2 台目)を実際の秒と順序で再現した筋書きは
;;;   tests/test_outage_timelines.hy(#2805)。
;;;   C3 stopped-generation-gets-no-new-task(doeff_cluster.coordinator.core.coordinator_invariants:stopped-generation-gets-no-new-task)—
;;;   止まり始めた worker の世代(drain の頼みを通らない止め — sigterm・機体の終了・手の kill)へ、止まり始めの後に新しい task を置かない
;;;   (その世代は task を始めずに抜け、切り離した task は新しい世代へ渡らず lease まで止まる — #2819)。確かめるのは
;;;   tests/test_detached_runners.hy の test-a-runner-stopped-without-a-drain-gets-no-new-task-until-its-next-generation(止めた世代と、
;;;   止めた後・戻す前に読めた task の置き先を判断に渡す)。失敗ケースは同じ file の
;;;   test-a-counterexample-worker-that-does-not-announce-its-stop-breaks-c3(止まり始めを heartbeat で名乗らない worker — sim の SimWorker の
;;;   silent-stop — で、同じ筋書きに C3 の破りが出る)。
;;;   C4 timing-outlasts-the-self-stop(doeff_cluster.shared.core.timing_rules:timing-outlasts-the-self-stop — #2806)— coordinator が
;;;   連絡の途絶えた worker の印の無い job を他へ移す時刻(ClusterTiming.reassign-after-ms)は、その worker が自分で job を止め切る最悪の
;;;   時刻(fence + heartbeat の返事の上限 + 接続の上限 + 子の停止の猶予 — 送った heartbeat が上限まで答えない間は worker の周期が止まって
;;;   いて判じられない)より後(C2 の時間の柵の値の側)。値の定義は 3 か所(ClusterTiming・foundation/coordinator_http の REPLY-SECONDS と
;;;   CONNECT-SECONDS・WorkerPolicy の停止の猶予)に分かれているので、判断は値を受け取る純関数で、値を集めるのは呼び手。確かめるのは
;;;   tests/test_cluster_timing.hy の test-the-production-timing-outlasts-the-self-stop(本番の定数から内訳を作って判断に渡す — 数を検に
;;;   写さない)。失敗ケースは同じ file の、定数を 1 つずつ動かすと破りを名指す検(移し替えを 45 秒に戻す・返事の上限を延ばす・停止の猶予を
;;;   延ばす)。worker の入口(worker/entry/main.hy の timing-checked)は同じ判断で、破る起動を job を走らせる前に名指しで断る。
;;;   W1 handoff-keeps-a-ready-writer(doeff_cluster.worker.core.invariants:handoff-keeps-a-ready-writer)— 入れ替え(handoff)を宣言した Service
;;;   は、入れ替えの間も Ready の書き手が途切れない(旧は新が Ready になった後にだけ止める)。確かめるのは tests/test_local.hy の
;;;   test-redeclaring-a-handoff-service-stops-the-old-process-only-after-the-new-one-is-ready(世代ごとの最初の Ready と終わりを判断に渡す)。
;;;   失敗ケースは同じ file の test-a-counterexample-worker-that-stops-the-old-process-on-retire-breaks-w1(sim の宿の RetireJob の handler を
;;;   「外すと同時に旧を止める」形に壊した worker — SimWorker の retire-stops — で、同じ筋書きに W1 の空白が出る)。
;;;   R1 prune-keeps-runs-whole(doeff_cluster.record_store.core.invariants:prune-keeps-runs-whole)— record-store の保持は run を丸ごと消すか
;;;   丸ごと残し、run の途中だけを残さない(再生は run の始まりから走らせる)。確かめるのは tests/test_record_files_contract.hy の
;;;   test-prune-removes-or-keeps-each-run-whole(区切りを複数持つ run の消す前と後の text を判断に渡す — 本物と memory の file system の両方)。
;;;   失敗ケースは同じ file の test-a-counterexample-remove-that-leaves-part-of-a-run-breaks-r1(保持が使う file system の RemoveTree の答え手を
;;;   「頭の区切りだけを消す」形に壊すと、R1 が 2 つの run を名指す)。

(defarchitecture doeff-cluster
  :root "doeff_cluster"
  ;; 層の説明・役・import の向きは、使い手の repo の architecture.hy の層の表と同じ(#2021 の決め)。
  :layers [(layer core
             :summary "業務の判断と Program"
             :roles [type judgment program]
             :imports [core intent])
           (layer intent
             :summary "core が外へ求める事の型"
             :roles [intent type]
             :imports [intent]
             :types-only True)
           (layer protocol
             :summary "intent を相手の話し方へ訳す handler"
             :roles [protocol]
             :imports [protocol intent core])
           (layer foundation
             :summary "汎用の I/O(環境で差し替えるのはここだけ)— まだ src/doeff_cluster/ に平たく在る coordinator・worker・土台の handler は移しの子で分ける"
             :roles [foundation]
             :imports [foundation])
           (layer entry
             :summary "入口 — 系の宣言・handler の並び・薄い main"
             :roles [system process main]
             :imports [core intent protocol foundation entry])]
  :shared "shared"
  :foundation foundation
  ;; 本物の coordinator と worker を 1 process・仮想の時計で走らせる模擬の環境(sim-cluster の local・環境の世界 env_world・git の台本
  ;; checkout_git_script)。全 service の core と entry を読むので、どの service にも属さない(DOEFF114・115 の外・ほかの規則は当たる)。
  ;; 本番の code はこの dir を import しない。
  ;; JSON の値を手で綴ってよい foundation の module — 盤の送受信と、effect の記録の綴り(record_codec)と記録の形(record_log)(#2580)。
  :wire-modules ["doeff_cluster.shared.protocol.board_requests" "doeff_cluster.foundation.record_codec" "doeff_cluster.foundation.record_log"]
  :verification-environment "sim")

(defservice coordinator "worker へ job を割り当てる coordinator(資源と盤の置き場・調停のループ)"
  {:system {:exempt "cluster そのものの process — cluster に置く job ではなく、自分の image の k8s Deployment として動く(operator 2026-10-01 の補足「doeff-cluster の coordinator と worker の image は残る」)。defsystem にすると cluster が自分を job として置く循環になる"}
   :layers [core intent protocol entry]
   :entry-modules ["doeff_cluster.coordinator.entry.main"]
   :invariants ["doeff_cluster.coordinator.core.coordinator_invariants:acknowledged-writes-survive"
                "doeff_cluster.coordinator.core.coordinator_invariants:one-place-per-job"
                "doeff_cluster.coordinator.core.coordinator_invariants:stopped-generation-gets-no-new-task"
                "doeff_cluster.shared.core.timing_rules:timing-outlasts-the-self-stop"]})

;; worker の条は W1(入れ替えの間も書き手が居続ける)。消す順などの条は後から足す。:entry-modules は worker の入口
;; (doeff_cluster.worker.entry.main — #2029 で移した。boot.sh が起こす旧い名 doeff_cluster.main は渡すだけの入口)。層に分けた後は :entry-modules を外し、entry 層の dir の定義で「code を持つ service」を数える形に移る。
;; 層は移しの進みに合わせて足す: core(調整ループ・判断 — worker/core)・intent(観測・記録・effect の型 — worker/intent)・
;; protocol(heartbeat の本文の形と止めの印 — worker/protocol・#2026)・entry(worker の入口 main・drain の入口 drain_main(#2029)・子 process の入口
;; job_entry・見張り shim・実行環境の準備の入口 env_tool — worker/entry・#2028。bytecode の準備の道具 code_prepare(worker が file の path で起動する)も
;; worker/entry(#2027)。worker が送る名は旧い path の入口のまま — 切り替えは #2112・旧い入口を消すのは #2113)。
;; #2025・#2026。
(defservice worker "coordinator から job と task を受けて子 process として走らせる worker"
  {:system {:exempt "cluster そのものの process — cluster に置く job ではなく、自分の image の k8s Deployment として動く(operator 2026-10-01 の補足「doeff-cluster の coordinator と worker の image は残る」)。defsystem にすると cluster が自分を job として置く循環になる"}
   :layers [core intent protocol entry]
   :entry-modules ["doeff_cluster.worker.entry.main"]
   :invariants ["doeff_cluster.worker.core.invariants:handoff-keeps-a-ready-writer"]})

;; record-store の条は R1(保持は run を丸ごと)。「追記して fsync してから返事」は file system の性質で、memory の置き場では確かめられない
;; ので条にしていない。層の dir(#2030): intent = effect の型・core = 置き場の Program と条・protocol = file の I/O の
;; 言い換え(record-files)・entry = 入口。HTTP の受付(RecordInbox)は汎用の I/O なので foundation/record_inbox。
(defservice record-store "effect の記録の置き場(run ごと・区切りごとの file に追記し、読み・一覧・圧縮・保持を答える)"
  {:system {:exempt "cluster そのものの process — cluster に置く job ではなく、自分の image の k8s Deployment として動く(operator 2026-10-01 の補足「doeff-cluster の coordinator と worker の image は残る」)。defsystem にすると cluster が自分を job として置く循環になる"}
   :layers [core intent protocol entry]
   :entry-modules ["doeff_cluster.record_store.entry.main"]
   :invariants ["doeff_cluster.record_store.core.invariants:prune-keeps-runs-whole"]})
