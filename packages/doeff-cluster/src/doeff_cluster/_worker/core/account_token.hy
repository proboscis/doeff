;;; worker の ServiceAccount の token の古さの判断— I/O は呼び手(worker/protocol/coordinator_link)が行う。
;;;
;;; kubelet は投影の token を期限の 8 割(1 時間の token なら約 48 分)で置き直す。2026-10-24 の zeus では kubelet が置き直しを止め、
;;; 通信が戻った後も再開せず、JWT の exp の 81 秒後から Pod の中で API server を呼ぶ物が全部 401 になった(Pod の中からは
;;; kubelet の訳を読めない)。worker は token の file の発行の刻(JWT の iat)と期限(exp)を読み、置き直しの遅れを heartbeat で名乗る —
;;; 401 が黙って続かない様に、読み手(GET /resources/Worker)が期限の前に知る。
;;;   ok          発行から STALE-AFTER-MS の内で、期限の前
;;;   stale       発行から STALE-AFTER-MS を越えた(kubelet が置き直しを取り逃している — 期限の延びない token なら間も無く 401)
;;;   expired     期限(exp)を過ぎた(API server は断る)
;;;   unreadable  file は在るが JWT の形に読めない
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import base64)
(import binascii)
(import json)

;; 置き直しが遅れたと読む、発行からの長さ(55 分 — kubelet の置き直しの刻 約 48 分と、1 時間の token の期限の間)。
(val STALE-AFTER-MS (* 55 60 1000))
;; token の file を読み直す間(拍ごとには読まない — 置き直しの遅れは分の単位で十分に分かる)。
(val READ-EVERY-MS (* 60 1000))

(defenum TokenState OK STALE EXPIRED UNREADABLE)


(defrecord TokenClaims
  "token(JWT)の期限の欄: issued-at = 発行の刻(iat)・expires-at = 期限(exp)— どちらも epoch 秒。"
  (#^ int issued-at)
  (#^ int expires-at))


(defk token-claims [text]
  {:pre [(: text str)] :post [(: % (| TokenClaims None))] :tags {:context "worker" :role "judgment" :reads "json"}}
  "token の file の中身(JWT)から発行の刻と期限(iat・exp — epoch 秒)を読むため。JWT の形でない・欄が整数でない中身は None。
   署名は検めない(期限を読むだけ — 検めるのは API server)。"
  (val parts (.split (.strip text) "."))
  (val payload (if (= (len parts) 3) (get parts 1) None))
  (val claims
    (if (is payload None)
        None
        (try (json.loads (base64.urlsafe-b64decode (+ payload (* "=" (% (- 4 (% (len payload) 4)) 4)))))
             (except [#(ValueError binascii.Error UnicodeDecodeError)] None))))
  (val stamps (if (isinstance claims dict) #((.get claims "iat") (.get claims "exp")) #(None None)))
  (if (all (gfor v stamps (and (isinstance v int) (not (isinstance v bool)))))
      (TokenClaims :issued-at (get stamps 0) :expires-at (get stamps 1))
      None))


(defk token-state [claims now-ms]
  {:pre [(: claims (| TokenClaims None)) (: now-ms int)] :post [(: % TokenState)]}
  "token の発行の刻と期限(token-claims の答え — None = 読めない)と今の刻(epoch ms)から、heartbeat で名乗る token の状態を決めるため。
   期限を過ぎた事を古さより先に読む。"
  (cond
    (is claims None) TokenState.UNREADABLE
    (>= now-ms (* claims.expires-at 1000)) TokenState.EXPIRED
    (> (- now-ms (* claims.issued-at 1000)) STALE-AFTER-MS) TokenState.STALE
    True TokenState.OK))


(defk token-read-due [read-ms now-ms]
  {:pre [(: read-ms (| int None)) (: now-ms int)] :post [(: % bool)]}
  "この拍で token の file を読み直すかを決めるため(read-ms = 前に読んだ刻・None = まだ読んでいない)。壁の時計が戻った時も読み直す。"
  (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) READ-EVERY-MS)))
