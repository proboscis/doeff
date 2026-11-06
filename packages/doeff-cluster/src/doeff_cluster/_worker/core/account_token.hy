;;; worker の ServiceAccount の token の古さの判断— I/O は呼び手(worker/protocol/coordinator_link)が行う。
;;;
;;; kubelet は投影の token を、年齢が 24 時間を越えるか残りが期限の 20% を切った時に置き直す(KUBELET-MAX-AGE-S・KUBELET-LEFT-PERCENT —
;;; 1 時間の token なら発行から 48 分・36 時間の token なら 24 時間)。2026-10-24 の zeus では kubelet が置き直しを止め、
;;; 通信が戻った後も再開せず、JWT の exp の 81 秒後から Pod の中で API server を呼ぶ物が全部 401 になった(Pod の中からは
;;; kubelet の訳を読めない)。worker は token の file の発行の刻(JWT の iat)と期限(exp)を読み、置き直しの遅れを heartbeat で名乗る —
;;; 401 が黙って続かない様に、読み手(GET /resources/Worker)が期限の前に知る。stale の線は token 自身の期限から導く(配備の宣言の
;;; expirationSeconds を機体ごとに変えても、線が一緒に動く — 11-06 に zeus・helios・atlas の期限を 36 時間へ延ばした時、55 分の定数の線は
;;; 置き直しの正しい worker に stale を名乗らせた)。
;;;   ok          kubelet が置き直す刻 + REFRESH-SLACK-MS(token-stale-at-ms)の内で、期限の前
;;;   stale       token-stale-at-ms を越えた(kubelet が置き直しを取り逃している — 期限の延びない token なら間も無く 401)
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

;; kubelet の置き直しの規則(kubelet の token_manager の requiresRefresh): 年齢が KUBELET-MAX-AGE-S を越えるか、残りが期限の
;; KUBELET-LEFT-PERCENT % を切ると置き直す(どちらも最大 10 秒の揺らぎで早める)。期限は kubelet が API server に頼んだ長さ
;; (spec.expirationSeconds — 延びた token では warnafter − iat)。配備の宣言の検(tests/test_k8s_declarations.py)もここを読む — 規則を
;; 2 か所に書かない。
(val KUBELET-MAX-AGE-S (* 24 60 60))
(val KUBELET-LEFT-PERCENT 20)
;; kubelet が置き直す刻を過ぎてから stale と読むまでの余り(7 分 — 1 時間の token では 48 分 + 7 分 = 発行から 55 分、期限の 5 分前。
;; kubelet が投影の volume を読み直す周期〔約 1〜2 分〕で置き直しが遅れても名乗らない)。
(val REFRESH-SLACK-MS (* 7 60 1000))
;; token の file を読み直す間(拍ごとには読まない — 置き直しの遅れは分の単位で十分に分かる)。
(val READ-EVERY-MS (* 60 1000))

(defenum TokenState OK STALE EXPIRED UNREADABLE)


(defrecord TokenClaims
  "token(JWT)の期限の欄: issued-at = 発行の刻(iat)・expires-at = 期限(exp)・warn-after = 頼んだ期限(kubernetes.io.warnafter — API server が
   期限を延ばした token〔3607 秒の要求〕だけに在る。None = 延びていない)— どれも epoch 秒。"
  (#^ int issued-at)
  (#^ int expires-at)
  (setv #^ (| int None) warn-after None))


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
  (val scope (if (isinstance claims dict) (.get claims "kubernetes.io") None))
  (val warn (if (isinstance scope dict) (.get scope "warnafter") None))
  (val stamp? (fn [v] (and (isinstance v int) (not (isinstance v bool)))))
  (if (all (gfor v stamps (stamp? v)))
      (TokenClaims :issued-at (get stamps 0) :expires-at (get stamps 1) :warn-after (if (stamp? warn) warn None))
      None))


(defk kubelet-refresh-after-s [lifetime-s]
  {:pre [(: lifetime-s int)] :post [(: % int)]}
  "期限 lifetime-s 秒の token を kubelet が置き直す、発行からの秒(年齢 KUBELET-MAX-AGE-S か、残りが KUBELET-LEFT-PERCENT % を切る時の
   早い方)を知るため。配備の宣言の検が、Terminating の Pod に残る token の残りを読むのもこれ。"
  (min KUBELET-MAX-AGE-S (- lifetime-s (// (* lifetime-s KUBELET-LEFT-PERCENT) 100))))


(defk token-stale-at-ms [claims]
  {:pre [(: claims TokenClaims)] :post [(: % int)]}
  "token を stale と読み始める刻(epoch ms — これを越えたら stale)を、token 自身の期限から導くため: kubelet が置き直す刻 +
   REFRESH-SLACK-MS。期限の延びた token(warn-after が在る)は、kubelet が頼んだ期限(warnafter)で置き直すので、exp でなく warnafter を使う
   (exp の 1 年で数えると、置き直しの止まりを 24 時間まで見落とす)。"
  (val asked-until (if (is claims.warn-after None) claims.expires-at claims.warn-after))
  (<- refresh-s int (kubelet-refresh-after-s (- asked-until claims.issued-at)))
  (+ (* (+ claims.issued-at refresh-s) 1000) REFRESH-SLACK-MS))


(defk token-state [claims now-ms]
  {:pre [(: claims (| TokenClaims None)) (: now-ms int)] :post [(: % TokenState)]}
  "token の発行の刻と期限(token-claims の答え — None = 読めない)と今の刻(epoch ms)から、heartbeat で名乗る token の状態を決めるため。
   期限を過ぎた事を古さより先に読む。"
  (if (is claims None)
      TokenState.UNREADABLE
      (do
        (<- stale-at int (token-stale-at-ms claims))
        (cond
          (>= now-ms (* claims.expires-at 1000)) TokenState.EXPIRED
          (> now-ms stale-at) TokenState.STALE
          True TokenState.OK))))


(defk token-read-due [read-ms now-ms]
  {:pre [(: read-ms (| int None)) (: now-ms int)] :post [(: % bool)]}
  "この拍で token の file を読み直すかを決めるため(read-ms = 前に読んだ刻・None = まだ読んでいない)。壁の時計が戻った時も読み直す。"
  (or (is read-ms None) (< now-ms read-ms) (>= (- now-ms read-ms) READ-EVERY-MS)))
