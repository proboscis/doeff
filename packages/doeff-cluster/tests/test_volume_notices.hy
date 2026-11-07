;;; foundation/volume_notices の変わりの知らせの箱(VolumeNoticeBoard)と、その作り手(volume-notices)の検。
;;;
;;;   印と鈴      読む前の印の後に変わっていなければ鈴を掛け、名が変わった時に満たす・印の後に変わっていれば掛けずに真・外した鈴は満たさない・
;;;               list し直し(None)は全部の鈴を満たし、前の印を古くする・ほかの名の変わりでは満たさない
;;;   file の鈴   無い file に掛けた鈴は、file が現れた時に箱の thread が満たす・在る file には掛けずに真
;;;   見張り      Pod の外(token が無く偽の API も無い)では見張りを立てない・偽の API では Volume と VolumeAttachment を 1 本ずつ名の見張りで
;;;               受け、変わった名の鈴を満たす・見張りの止まりの訳は替わった時だけ 1 行
;;;   綴り        箱の Longhorn の API の頭は答え手(protocol/workspace_volumes)と同じ
;;; 鈴は満たした値を覚えるだけの相手役(Bell — 箱が呼ぶのは complete だけ)。見張りの thread の答えは上限つきで待つ。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import threading)
(import httpx)
(import doeff_cluster._foundation.volume_notices [LONGHORN-API VolumeNoticeBoard volume-notices])
(import doeff_cluster._shared.protocol.workspace_volumes [LONGHORN-API :as PROTOCOL-LONGHORN-API VolumeNotices])

;; 箱の thread の答えを待つ上限の秒。
(val WAIT-SECONDS 10.0)
(val NAME "ws-c-exampleownerid000000000001")
(val OTHER "ws-c-exampleotherid00000000002")


(defclass Bell []
  "鈴の相手役: 箱が complete で満たした値を覚え、満たされたかを上限つきで待てる。"
  (defn #^ None __init__ [self]
    (setv self.values #() self.rung (threading.Event)))

  (defn #^ None complete [self value]
    (setv self.values (+ self.values #(value)))
    (.set self.rung)))


(deftest test-a-bell-hung-after-the-mark-is-rung-when-the-name-changes
  (val board (VolumeNoticeBoard))
  (assert (isinstance board VolumeNotices))
  (val seen (.mark board NAME))
  (val bell (Bell))
  (assert (not (.arm board NAME seen bell)))
  ;; ほかの名の変わりでは満たさない。
  (.changed board OTHER)
  (assert (= bell.values #()) bell.values)
  (.changed board NAME)
  (assert (= bell.values #(True)) bell.values)
  ;; 満たした鈴は外れている(次の変わりで 2 度満たさない)。
  (.changed board NAME)
  (assert (= bell.values #(True)) bell.values))


(deftest test-a-change-between-the-mark-and-the-arm-is-not-missed
  ;; 読む前に印を取り、読んだ後・鈴を掛ける前に変わった — 掛けずに真(待ち手は待たずに読み直す)。
  (val board (VolumeNoticeBoard))
  (val seen (.mark board NAME))
  (.changed board NAME)
  (val bell (Bell))
  (assert (.arm board NAME seen bell))
  (assert (= bell.values #()) bell.values)
  (assert (not (.arm board NAME (.mark board NAME) bell))))


(deftest test-a-disarmed-bell-is-not-rung
  (val board (VolumeNoticeBoard))
  (val bell (Bell))
  (val kept (Bell))
  (assert (not (.arm board NAME (.mark board NAME) bell)))
  (assert (not (.arm board NAME (.mark board NAME) kept)))
  (.disarm board NAME bell)
  (.changed board NAME)
  (assert (= bell.values #()) bell.values)
  (assert (= kept.values #(True)) kept.values))


(deftest test-a-new-list-rings-every-bell-and-ages-every-mark
  ;; list し直した(見張りの外れた間に何が変わったか分からない)— 全部の鈴を満たし、前に取った印は古い。
  (val board (VolumeNoticeBoard))
  (val seen (.mark board OTHER))
  (val one (Bell))
  (val two (Bell))
  (assert (not (.arm board NAME (.mark board NAME) one)))
  (assert (not (.arm board OTHER seen two)))
  (.changed board None)
  (assert (= one.values #(True)) one.values)
  (assert (= two.values #(True)) two.values)
  (assert (.arm board OTHER seen (Bell))))


(deftest test-a-file-bell-is-rung-when-the-file-appears [tmp-path]
  (val board (VolumeNoticeBoard))
  (val path (/ tmp-path "mount-ws-1.out"))
  (val bell (Bell))
  (assert (not (.arm-file board (str path) bell)))
  (.write-text path "0\n" :encoding "utf-8")
  (assert (.wait bell.rung WAIT-SECONDS))
  (assert (= bell.values #(True)) bell.values)
  ;; 在る file には掛けない。
  (val again (Bell))
  (assert (.arm-file board (str path) again))
  (assert (= again.values #()) again.values))


(deftest test-outside-a-pod-the-board-follows-nothing [tmp-path]
  (<- board VolumeNoticeBoard (volume-notices "longhorn-system" :sa-dir (str tmp-path)))
  (assert (= board.stops #()) board.stops))


(deftest test-the-board-names-the-same-longhorn-api-as-the-handler
  (assert (= LONGHORN-API PROTOCOL-LONGHORN-API) #(LONGHORN-API PROTOCOL-LONGHORN-API)))


(defclass Longhorn []
  "模擬の k8s の API(Longhorn の 2 つの種類の一覧): list には空の一覧を版 1 で、watch には種類ごとに用意した出来事を渡し終えて終わる
   (server が timeoutSeconds で閉じた形 — 見張りは覚えた版から受け直す)。2 度目からの watch は 503 で断る(見張りは retry-seconds の後に
   list し直す — 空の stream で受け直しを回し続けない)。受けた要求の path を記録する。"
  (defn #^ None __init__ [self #^ dict events]
    (setv self.events events self.paths #() self.watched #() self.lock (threading.Lock)))

  (defn #^ httpx.Response handle [self #^ httpx.Request request]
    (with [self.lock]
      (setv kind (get (.split request.url.path "/") -1)
            first (and (in "watch" request.url.params) (not-in kind self.watched))
            self.paths (+ self.paths #(request.url.path)))
      (when (in "watch" request.url.params)
        (setv self.watched (+ self.watched #(kind)))))
    (cond
      (not-in "watch" request.url.params)
        (httpx.Response 200 :json {"kind" "List" "metadata" {"resourceVersion" "1"} "items" []})
      first
        (httpx.Response 200 :content (.encode (.join "" (gfor event (.get self.events kind #()) (+ (json.dumps event) "\n"))) "utf-8"))
      True
        (httpx.Response 503 :json {"kind" "Status" "code" 503}))))


(deftest test-the-board-follows-volumes-and-attachments-and-rings-the-changed-name [tmp-path]
  (.write-text (/ tmp-path "token") "test-token" :encoding "utf-8")
  (val event (fn [name version] {"type" "MODIFIED" "object" {"metadata" {"name" name "resourceVersion" version}}}))
  (val peer (Longhorn {"volumeattachments" #((event NAME "7"))}))
  ;; 鈴を掛けてから見張りを始める(list し直しの全部の鈴の満たしを、掛けた後の 1 回目の出来事と分けて読む)。
  (<- board VolumeNoticeBoard (volume-notices "longhorn-system" :sa-dir (str tmp-path) :transport (httpx.MockTransport peer.handle)
                                              :retry-seconds 0.2 :watch-seconds 1))
  (try
    (val bell (Bell))
    ;; list の後の全部の満たしか、VolumeAttachment の変わりのどちらかで鳴る。
    (assert (not (.arm board NAME (.mark board NAME) bell)))
    (assert (.wait bell.rung WAIT-SECONDS))
    (assert (= (len board.stops) 2) board.stops)
    (val paths (set peer.paths))
    (assert (= paths #{"/apis/longhorn.io/v1beta2/namespaces/longhorn-system/volumes"
                       "/apis/longhorn.io/v1beta2/namespaces/longhorn-system/volumeattachments"})
            paths)
    (finally
      (.stop board))))
