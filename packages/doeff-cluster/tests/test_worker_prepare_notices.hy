;; worker は準備の file を間隔で読み直さず、書き手が file を置き換えた知らせ(foundation/file_notices — Linux = inotify)で読み直し、
;; 読んだ姿が前に送った姿と違えば送る間隔を待たずに heartbeat を送る(coordinator_link)。以前は枠の file と同じ 10 秒の間で読み直したので、
;; 準備が済んでから能力が置き方に効くまで、読みの間と heartbeat の間の遅れが残った(2026-11-12)。
;;   (1) 読み直すのは、まだ読んでいない時・知らせの印が進んだ時・遅い読みの間(SYNC-READ-EVERY-MS)を過ぎた時だけ。見張りの掛からない
;;       間は枠の file と同じ間で読む
;;   (2) 知らせの箱は file の名の置き換えで鈴を満たし、同じ dir の別の名では満たさない。dir が無い間は見張りを掛けず、dir が現れたら
;;       掛けて鳴らす(見張りの無い間の変わりを取りこぼさない)
;;   (3) 置き換えの知らせは周の間の待ちを起こす鈴(WorkerWakes)に掛かり、起きた周の heartbeat が、送る間隔の前でも準備で足す能力を名乗る
;; 失敗ケース = 置き換えても次の間まで読まない・置き換えを読んでも送る間隔まで送らない・別の file の書きで毎回読み直す・dir が後から
;; 出来た時に見張りを掛けないまま・周ごとに鈴を掛けて箱に溜める。
(require doeff-hy.macros [defk deff deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import platform)
(import threading)
(import time)
(import httpx)
(import doeff [run])
(import pathlib [Path])
(import doeff_time [GetMonotonic RaceOutcome RaceWithin])
(import tests.link_rig [LinkRig])
(import doeff_cluster [no-wakes])
(import doeff_cluster._shared.core.clock [now-epoch-ms monotonic-ms-of])
(import doeff_cluster._shared.intent.due_model [DueNow])
(import doeff_cluster._worker.intent.worker_model [WakeSet WorkerWakes])
(import doeff_cluster._worker.core.prepare_rules [SYNC-READ-EVERY-MS UNWATCHED-READ-EVERY-MS prepare-read-due])
(import doeff_cluster._foundation.file_notices [FileNoticeBoard changed-names file-notices])

(val ON-LINUX "OS の見張りの検は inotify の在る Linux で走らせる")


(defclass Bell []
  "検の鈴(箱が満たす外の Promise の代わり — complete を受けた事を Event で見せる)。"
  (defn #^ None __init__ [self]
    (setv self.rung (threading.Event)))
  (defn #^ None complete [self #^ object value]
    (.set self.rung)))


(deff replaced [#^ str path #^ dict row]  ; cannot be a defk: 検が Program の外で書き手の役をする道具
  {:pre [(: path str) (: row dict)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "準備の書き手と同じく、別の名で書いてから rename で置き換えるため。"
  (setv staged (+ path ".tmp"))
  (with [f (open staged "w" :encoding "utf-8")]
    (.write f (json.dumps row)))
  (os.replace staged path)
  None)


(deftest test-the-prepare-file-is-read-on-a-notice-and-at-the-slow-sync-only
  ;; まだ読んでいない・時計が戻った時は読む。
  (<- first bool (prepare-read-due None 1000 True None 0))
  (<- back bool (prepare-read-due 5000 1000 True 3 3))
  (assert (and first back))
  ;; 見張りの掛かっている間: 印が進めば読み、進まなければ遅い読みの間まで読まない。
  (<- noticed bool (prepare-read-due 1000 1001 True 3 4))
  (<- quiet bool (prepare-read-due 1000 (+ 1000 UNWATCHED-READ-EVERY-MS) True 3 3))
  (<- synced bool (prepare-read-due 1000 (+ 1000 SYNC-READ-EVERY-MS) True 3 3))
  (assert noticed)
  (assert (not quiet))
  (assert synced)
  ;; 見張りの掛からない間は枠の file と同じ間で読む(印は読まない)。
  (<- early bool (prepare-read-due 1000 (+ 1000 (- UNWATCHED-READ-EVERY-MS 1)) False 3 4))
  (<- late bool (prepare-read-due 1000 (+ 1000 UNWATCHED-READ-EVERY-MS) False None None))
  (assert (not early))
  (assert late))


(deftest test-the-inotify-reading-names-each-change
  ;; inotify の知らせの頭(wd・mask・cookie・len)と NUL で詰めた名を解く。
  (val one (+ (.to-bytes 1 4 "little" :signed True) (.to-bytes 0x80 4 "little") (.to-bytes 0 4 "little") (.to-bytes 16 4 "little")
              (.ljust b"prepared" 16 b"\x00")))
  (val self-gone (+ (.to-bytes 1 4 "little" :signed True) (.to-bytes 0x8000 4 "little") (.to-bytes 0 4 "little") (.to-bytes 0 4 "little")))
  (assert (= (changed-names (+ one self-gone)) #(#(0x80 "prepared") #(0x8000 "")))))


(deftest test-the-board-rings-on-a-replace-of-the-file-and-not-on-other-names [#^ Path tmp-path]
  {:marks ["realtime"] :skip-if (!= (platform.system) "Linux") :skip-reason ON-LINUX}
  (val directory (/ tmp-path "gen"))
  (val path (str (/ directory "prepared")))
  (val board (run-board path))
  (try
    ;; dir が無い間は見張りを掛けない(訳を名乗る)。
    (assert (.wait board.told 3.0))
    (assert (not (.watching board)))
    (assert (in "見張りを掛けられない" (.fault board)) (.fault board))
    ;; dir が現れたら掛け、掛かった時に鳴らす(見張りの無い間に置かれた file を取りこぼさない)。
    (val early (Bell))
    (val seen (.mark board))
    (assert (not (.arm board seen early)))
    (.mkdir directory)
    (assert (.wait early.rung 3.0) (.fault board))
    (assert (.watching board) (.fault board))
    ;; 同じ dir の別の名の書きでは鳴らない。
    (val quiet (Bell))
    (val now-seen (.mark board))
    (assert (not (.arm board now-seen quiet)))
    (replaced (str (/ directory "other")) {"x" 1})
    (time.sleep 0.3)
    (assert (not (.is-set quiet.rung)))
    (assert (= (.mark board) now-seen))
    ;; file の名の置き換え(rename)で鳴り、印が進む。印の進んだ後の arm は掛けずに真。
    (replaced path {"state" "done"})
    (assert (.wait quiet.rung 3.0))
    (assert (> (.mark board) now-seen))
    (assert (.arm board now-seen (Bell)))
    (finally
      (.stop board))))


(deff run-board [#^ str path]  ; cannot be a defk: 検が Program の外で箱を作る道具
  {:pre [(: path str)] :post [(: % FileNoticeBoard)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "検の箱を作るため(dir が無い間の掛け直しを短くする)。"
  (run (file-notices path :retry-seconds 0.05)))


(defk collected-wakes []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "周の間の待ちを起こす物を集めるため(待たない)。答え = #(集めた組)。"
  (<- began int (now-epoch-ms))
  (<- mono float (GetMonotonic))
  (<- wakes WakeSet (WorkerWakes :began began :began-mono-ms (monotonic-ms-of mono)))
  #(wakes))


(defk woken-by-the-prepare-bell []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "周の間の待ちを起こす物を集め(WorkerWakes)、その鈴が 5 秒の内に満ちるまで待つため。答え = #(集めた組 待ちの答え)。"
  (<- began int (now-epoch-ms))
  (<- mono float (GetMonotonic))
  (<- wakes WakeSet (WorkerWakes :began began :began-mono-ms (monotonic-ms-of mono)))
  (<- outcome RaceOutcome (RaceWithin wakes.bells 5.0))
  #(wakes outcome))


(deftest test-a-replace-of-the-prepare-file-wakes-the-tick-and-sends-the-heartbeat-at-once [#^ Path tmp-path]
  {:marks ["realtime"] :skip-if (!= (platform.system) "Linux") :skip-reason ON-LINUX}
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; cannot be a defk: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val path (str (/ tmp-path "prepared")))
  (val board (run-board path))
  (try
    ;; 見張りが掛かった時に鳴る(dir は在る)— 印 0 の後に鳴っていれば掛けずに真。
    (val hooked (Bell))
    (assert (or (.arm board 0 hooked) (.wait hooked.rung 3.0)) (.fault board))
    (assert (.watching board) (.fault board))
    (val link (LinkRig "http://coord" "node-h" #("host-node-h") 60000 :task-dir (str (/ tmp-path "tasks")) :watch True
                       :transport (httpx.MockTransport handle) :answerers #(no-wakes)))
    (setv link.state.prepare-file path link.state.prepare-notices board)
    ;; 待ちの口を使えている worker にする(宣言の変化か状態の違いか送る間隔でしか送らない)。送る間隔は検の長さより長くする。
    (deff watching []  ; cannot be a defk: 検が Program の外で口の入れ物を書く道具
      {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
      "口を、待ちの口を使えていて送る間隔の長い worker にするため(拍の送りの後に毎回置き直す — 返事が送る間隔を書き直す)。"
      (setv link.state.watch.running True link.state.watch.confirmed True link.state.beat-interval-ms 600000)
      None)
    ;; 起動の拍: file がまだ無い — running を名乗る。
    (.poll link)
    (watching)
    (assert (= (len sent) 1) sent)
    (assert (= (get sent -1 "prepare" "state") "running") (get sent -1))
    ;; 何も変わらない拍は送らない(送る間隔の前)。
    (.poll link)
    (assert (= (len sent) 1) sent)
    ;; 周の間の待ちに置き換えの鈴が掛かり、書き手の置き換えで満ちる(期限を待たない)。
    (val writer (threading.Timer 0.2 replaced :args #(path {"state" "done" "provides" ["agent"] "sinceMs" 4000})))
    (.start writer)
    (val woken (.on-link link (woken-by-the-prepare-bell)))
    (val wakes (get woken 0))
    (val outcome (get woken 1))
    (.join writer)
    (assert (= (len wakes.bells) 1) wakes)
    (assert (not (isinstance wakes.due DueNow)) wakes)
    (assert (not outcome.timed-out) outcome)
    ;; 起きた周の拍は、送る間隔の前でも file を読み直し、準備で足す能力を名乗る heartbeat を送る。
    (val read-before link.state.prepare-read-ms)
    (.poll link)
    (assert (= (len sent) 2) sent)
    (assert (= (get sent -1 "provides") ["host-node-h" "agent"]) (get sent -1))
    (assert (= (get sent -1 "prepare" "state") "done") (get sent -1))
    (assert (< (- link.state.prepare-read-ms read-before) SYNC-READ-EVERY-MS))
    ;; 読んだ後の周は同じ鈴を周をまたいで渡す(周ごとに掛けない)— 鳴った鈴の後は新しく 1 つ。
    (watching)
    (val rung link.state.prepare-bell)
    (val again (get (.on-link link (collected-wakes)) 0))
    (val held link.state.prepare-bell)
    (val later (get (.on-link link (collected-wakes)) 0))
    (assert (is-not held rung))
    (assert (is link.state.prepare-bell held))
    (assert (= #((len again.bells) (len later.bells)) #(1 1)) #(again later))
    (assert (= (len board.bells) 1) board.bells)
    ;; 同じ姿の拍は送らない。
    (.poll link)
    (assert (= (len sent) 2) sent)
    (finally
      (.stop board))))
