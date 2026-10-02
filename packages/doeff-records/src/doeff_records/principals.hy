;;; 記録の service の呼び手の書き手の名 — `Authorization: Bearer <token>` を名簿(principals.json)で書き手の名へ引く(純粋)。
;;;
;;; 名簿の形は {version: 1, principals: [{name, tokenSha256}]}(token そのものは持たず sha256 だけ — 呼び手の系の既存の名簿をそのまま読める)。
;;; 引いた名がそのまま記録の handler の書き手の名になる(書き手の名は effect の引数にせず、HTTP の口が要求ごとに handler を組む時に渡す)。
;;; 名簿は呼び手を断らない(#2988・利用者 2026-10-02「頼んでいない token の確かめを外す」): 見出しが無い・形が違う・
;;; 名簿に無い token の呼び手は、名の無い書き手 ANONYMOUS として通す。書き手の名は呼び手が X-Records-Writer の見出しで名乗る(writer-of)。
;;; 名簿は、呼び手が token をやめて名乗りに移るまでの間、名乗らない呼び手の書き手の名を引くためだけに読む。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "judgment"})
(import dataclasses [dataclass field])
(import hashlib)
(import hmac)
(import json)
(import doeff_hy.frozen [FrozenMap])

(setv ROSTER-VERSION 1)
(setv ROSTER-DOCUMENT-KEYS (frozenset ["version" "principals"]))
(setv ROSTER-ENTRY-KEYS (frozenset ["name" "tokenSha256"]))
(setv AUTH-SCHEME "Bearer")
(setv HEX-DIGITS (frozenset "0123456789abcdef"))
(setv ANONYMOUS "anonymous")


(defclass [(dataclass :frozen True)] Roster []
  "身元の名簿: digests = 書き手の名 → token の sha256(小文字の 64 hex)の凍らせた写像。"
  (setv #^ (get FrozenMap str) digests (field :default-factory FrozenMap)))


(defclass [(dataclass :frozen True)] Principal []
  "呼び手(name = 書き手の名・名簿で引けなければ ANONYMOUS)。"
  (#^ str name))


(defk token-digest [token]
  {:pre [(: token str)] :post [(: % str)]}
  "token の sha256(名簿に載せる形 — 名簿を作る側もこれで digest を作る)。"
  (.hexdigest (hashlib.sha256 (.encode token "utf-8"))))


(defk decode-roster [text]
  {:pre [(: text str)] :post [(: % Roster)]}
  "principals.json の綴りを名簿にする(service の起動時に 1 回)。厳しく読む: 知らない鍵は断る(token を運ぶ名簿を名簿として
   読まない)・名は空でなく ':' を含まず一意・digest は 64 hex で一意(1 つの token は 1 つの名)。形が違えば ValueError。"
  (setv document (json.loads text))
  (when (not (isinstance document dict))
    (raise (ValueError "名簿は JSON の object {version: 1, principals: [...]}")))
  (setv unknown (sorted (- (set document) ROSTER-DOCUMENT-KEYS)))
  (when unknown (raise (ValueError (.format "名簿に知らない鍵: {}" unknown))))
  (when (!= (.get document "version") ROSTER-VERSION)
    (raise (ValueError (.format "名簿の version は {}: {!r}" ROSTER-VERSION (.get document "version")))))
  (setv entries (.get document "principals"))
  (when (not (isinstance entries list)) (raise (ValueError "名簿の principals は {name, tokenSha256} の配列")))
  (setv digests {} owners {})
  (for [#(position entry) (enumerate entries)]
    (when (not (isinstance entry dict)) (raise (ValueError (.format "名簿の {} 番目が object でない" position))))
    (setv unknown-entry (sorted (- (set entry) ROSTER-ENTRY-KEYS)))
    (when unknown-entry (raise (ValueError (.format "名簿の {} 番目に知らない鍵: {}" position unknown-entry))))
    (setv name (.get entry "name") digest (.get entry "tokenSha256"))
    (when (not (and (isinstance name str) name (not-in ":" name)))
      (raise (ValueError (.format "名簿の {} 番目の name は ':' を含まない空でない文字列: {!r}" position name))))
    (when (in name digests) (raise (ValueError (.format "名簿の name {!r} が重なる" name))))
    (when (not (and (isinstance digest str) (= (len digest) 64) (<= (set (.lower digest)) HEX-DIGITS)))
      (raise (ValueError (.format "名簿の {!r} の tokenSha256 が 64 hex でない" name))))
    (setv lowered (.lower digest))
    (when (in lowered owners)
      (raise (ValueError (.format "名簿の {!r} と {!r} が同じ tokenSha256" (get owners lowered) name))))
    (setv (get owners lowered) name (get digests name) lowered))
  (Roster (FrozenMap digests)))


(defk identify [roster header]
  {:pre [(: roster Roster) (: header (| str None))] :post [(: % Principal)]}
  "Authorization の見出し → 書き手の名。引けなければ(見出しが無い・形が違う・名簿に無い token)ANONYMOUS — 断らない。"
  (when (is header None) (return (Principal ANONYMOUS)))
  (setv parts (.split (.strip header) None 1))
  (when (or (!= (len parts) 2) (!= (.lower (get parts 0)) (.lower AUTH-SCHEME)) (= (.strip (get parts 1)) ""))
    (return (Principal ANONYMOUS)))
  (<- presented (token-digest (.strip (get parts 1))))
  (setv found None)
  (for [#(name digest) (sorted (.items roster.digests))]
    (when (hmac.compare-digest presented digest) (setv found name)))
  (Principal (if (is found None) ANONYMOUS found)))


(defk writer-of [roster authorization declared]
  {:pre [(: roster Roster) (: authorization (| str None)) (: declared (| str None))] :post [(: % Principal)]}
  "要求の書き手の名を決めるため: 呼び手が X-Records-Writer で名乗った名(空でなければ確かめずに使う)→ 無ければ移行の間だけ
   Authorization の token を名簿で引く(identify)。token をやめた呼び手は名乗りだけで書き手の名を運ぶ(#2988)。"
  (val named (if (is declared None) "" (.strip declared)))
  (when named
    (return (Principal named)))
  (<- caller Principal (identify roster authorization))
  caller)
