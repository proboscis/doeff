;; 待ちの子の socket の path(warm_rules の warm-place・warm-socket-of)を、状態の dir の長さに依らず unix socket の上限の内に導く事を縛る。
;; 2026-11-07、Mac の worker(世代ごとの状態の dir)が socket を <state-dir>/warm/env-<24 桁>/sock に置き、path が 118 byte になって
;; macOS の上限(sun_path 104 byte・NUL 込み)を越え、待ちの子が bind で断り続けて task が env-incompatible で終わった。
;;   * Mac の世代の dir でも Linux の Pod の世代の dir でも、socket の path は上限(SOCKET-PATH-LIMIT)の内で、状態の dir の下に無い。
;;   * 同じ置き場からは同じ socket を導き(起こす宿と分ける宿が同じ関数で導く)、世代の違う置き場からは違う socket を導く。
;;   * 上限を越える組み立て(長すぎる根)は、導く関数が WarmSocketTooLong で名指して断る。
(require doeff-hy.macros [deftest <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster._worker.core.places [worker-places])
(import doeff_cluster._worker.core.warm_rules [SOCKET-PATH-LIMIT WarmSocketTooLong warm-place warm-socket-of])

;; Mac の worker の世代の状態の dir(keeper の世代の名 = local.<boot の ms 13 桁>)と、Linux の Pod の世代の状態の dir
;; (世代の名 = Pod の uid 36 字 + . + 13 桁)。
(val MAC-STATE "/Users/agent/.local/state/doeff-agent-worker/node/gen/local.1794013200000/state")
(val POD-STATE "/work/gen/3f6c1d2e-9a8b-4c7d-8e6f-5a4b3c2d1e0f.1794013200000/state")
(val SHARED "/work/state")
(val BOOT "0b6f5e2a9c1d4f3e8a7b6c5d4e3f2a1b")
(val KEY "env-0123456789abcdef01234567")


(deftest test-the-socket-fits-the-limit-on-the-mac-generation-dir
  (<- places (worker-places MAC-STATE SHARED BOOT))
  (<- place (warm-place places.warm KEY))
  ;; 状態の dir の下に置いた前の綴りは、上限を越えていた(失敗の形の記録)。
  (assert (= (len (.encode (+ place.dir "/sock") "utf-8")) 118) place.dir)
  (assert (<= (len (.encode place.socket "utf-8")) SOCKET-PATH-LIMIT) place.socket)
  (assert (not (.startswith place.socket MAC-STATE)) place.socket)
  (assert (= place.socket (+ place.socket-dir "/sock")) #(place.socket-dir place.socket))
  ;; 準備完了の印は今までどおり状態の dir の下。
  (assert (= place.ready (+ MAC-STATE "/warm/" KEY "/ready.json")) place.ready))


(deftest test-the-socket-fits-the-limit-on-the-pod-generation-dir
  (<- places (worker-places POD-STATE SHARED BOOT))
  (<- place (warm-place places.warm KEY))
  (assert (<= (len (.encode place.socket "utf-8")) SOCKET-PATH-LIMIT) place.socket)
  (assert (not (.startswith place.socket POD-STATE)) place.socket))


(deftest test-one-place-gives-one-socket-and-two-generations-give-two
  (<- mac (worker-places MAC-STATE SHARED BOOT))
  (<- pod (worker-places POD-STATE SHARED BOOT))
  (<- first (warm-place mac.warm KEY))
  (<- again (warm-place mac.warm KEY))
  (<- other-key (warm-place mac.warm "env-fedcba9876543210fedcba98"))
  (<- other-generation (warm-place pod.warm KEY))
  (assert (= first again) #(first again))
  (assert (= (len (set #(first.socket other-key.socket other-generation.socket))) 3)
          #(first.socket other-key.socket other-generation.socket)))


(deftest test-a-root-too-long-for-the-socket-is-refused-by-name
  (val root (+ "/" (* "r" 100)))
  (var refused None)
  (try
    (<- (warm-socket-of root (+ MAC-STATE "/warm/" KEY)))
    (except [error WarmSocketTooLong]
      (:= refused (str error))))
  (assert (is-not refused None) "長すぎる根の socket の path を断らなかった")
  (assert (in root refused) refused)
  (assert (in (str SOCKET-PATH-LIMIT) refused) refused))
