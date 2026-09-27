;;; 鍵の作り方と、見出しの読み — 純粋(I/O なし)。
;;;
;;; 鍵 = sha256(KEY-VERSION + "\n" + 正規化した本文)の 64 hex。正規化 = 本文を JSON として読み、object の鍵を並べ、区切りの空白を
;;; 除き、文字は UTF-8 のまま(\uXXXX に逃がさない)綴り直す。文字列の中身(空白・改行・大文字小文字)と数(1 と 1.0 は別)は
;;; 変えない — 意味の変わる直しをしない。鍵に入るのは綴り直した本文の全体(state・questions・model とその他の欄)で、本文に
;;; model が無ければ既定の model の名を足してから綴る(省いた呼び手と既定の名を名指した呼び手が同じ鍵になる)。
;;; 正規化を変えたら KEY-VERSION を上げる(古い答えを新しい綴りの問いに当てない)。
;;; 認証の見出し・Cache-Control は鍵に入れない(同じ問いは誰が問うても同じ答え)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import re)
(import doeff_jev.target [DIRECT-MODEL])
(import doeff_jev_proxy.values [Directive Header])

(val KEY-VERSION "jev-proxy-key-1")
;; 呼び手が本文に model を書かなかった時の model の名(TypeSafe 直の既定と同じ)。
(val DEFAULT-MODEL DIRECT-MODEL)


(defrecord NormalizedRequest
  "正規化した問い 1 つ: key = 鍵 / model = 名指した model(省けば既定)/ canonical = 綴り直した本文(覚えた答えと一緒に残す)。"
  (#^ str key)
  (#^ str model)
  (#^ bytes canonical))


(defrecord BadRequest
  "問いとして読めない本文。reason = 人の読む理由。"
  (#^ str reason))


(defk canonical-json [value]
  {:pre [(: value dict)] :post [(: % str)]}
  "JSON の値を 1 つの綴りにするため(鍵を並べ・区切りの空白なし・UTF-8 のまま・NaN は断る)。"
  (json.dumps value :sort-keys True :separators #("," ":") :ensure-ascii False :allow-nan False))


(defk normalize-request [body]
  {:pre [(: body bytes)] :post [(: % (| NormalizedRequest BadRequest))]}
  "本文を正規化して鍵を作るため。JSON の object で questions が空でない object、model は在れば空でない文字列 — でなければ BadRequest。"
  (val parsed (try
                (json.loads (.decode body "utf-8"))
                (except [error ValueError]
                  (return (BadRequest :reason (.format "本文が UTF-8 の JSON でない: {}" error))))))
  (when (not (isinstance parsed dict))
    (return (BadRequest :reason "本文は JSON の object {state, questions, model}")))
  (val questions (.get parsed "questions"))
  (when (not (and (isinstance questions dict) questions))
    (return (BadRequest :reason "questions は空でない object")))
  (val named (.get parsed "model"))
  (when (and (is-not named None) (not (and (isinstance named str) named)))
    (return (BadRequest :reason "model は空でない文字列")))
  (val model (if (is named None) DEFAULT-MODEL named))
  (<- text (canonical-json (| parsed {"model" model})))
  (val digest (.hexdigest (hashlib.sha256 (.encode (+ KEY-VERSION "\n" text) "utf-8"))))
  (NormalizedRequest :key digest :model model :canonical (.encode text "utf-8")))


(defk header-value [headers name]
  {:pre [(: headers tuple) (: name str)] :post [(: % (| str None))]}
  "見出しの列から名の値を読むため(名は小文字で比べる・同じ名が複数なら ', ' で繋ぐ)。"
  (val found (lfor h headers :if (= h.name (.lower name)) h.value))
  (if found (.join ", " found) None))


(defk directive-of [headers]
  {:pre [(: headers tuple)] :post [(: % Directive)]}
  "Cache-Control の見出しから覚えの使い方を読むため。only-if-cached と no-cache が両方あれば only-if-cached(Jev を呼ばない側)。"
  (<- value (header-value headers "cache-control"))
  (val tokens (if (is value None)
                  #()
                  (tuple (gfor part (.split value ",") (.lower (.strip part))))))
  (cond
    (in "only-if-cached" tokens) Directive.ONLY-IF-CACHED
    (in "no-cache" tokens) Directive.NO-CACHE
    True Directive.NORMAL))


(defrecord NotAnAnswer
  "本物の Jev が 200 で返したが答えとして読めない本文(覚えない)。reason = 人の読む理由。"
  (#^ str reason))


(defk answer-model [body]
  {:pre [(: body bytes)] :post [(: % (| str NotAnAnswer))]}
  "本物の Jev の答えの本文から答えた model の版つきの名を読むため。JSON の object で answers が object でなければ NotAnAnswer
   (覚えない)。model を名乗らない答えは \"\"(版を比べない答え)。"
  (val parsed (try
                (json.loads (.decode body "utf-8"))
                (except [error ValueError]
                  (return (NotAnAnswer :reason (.format "答えが UTF-8 の JSON でない: {}" error))))))
  (when (not (and (isinstance parsed dict) (isinstance (.get parsed "answers") dict)))
    (return (NotAnAnswer :reason "答えに answers の object が無い")))
  (val served (.get parsed "model"))
  (if (isinstance served str) served ""))


(defk headers-of [pairs]
  {:pre [(: pairs list)] :post [(: % tuple)]}
  "(名 値) の組の列を見出しの列にするため(名は小文字)。"
  (tuple (gfor #(name value) pairs (Header :name (.lower name) :value value))))


;; 覚えている時だけの問いの束 1 つで受ける鍵の数の上限(本文の上限 4 MiB の内 — 鍵 1 つは 67 byte 前後)。
(val PEEK-MAX-KEYS 20000)


(defrecord PeekKeys
  "覚えている時だけの問いの束の鍵の列: keys = 重ねを除いた鍵(届いた順)。"
  (#^ tuple keys))


(defk peek-keys-of [body]
  {:pre [(: body bytes)] :post [(: % (| PeekKeys BadRequest))]}
  "覚えている時だけの問いの束の本文 {\"keys\": [鍵 …]} を読むため。鍵は 64 桁の小文字の 16 進(/v1/systemone の答えの見出し
   x-jev-proxy-key と同じ鍵 — 呼び手は本文から同じ決まりで作る)。空・上限を超える・形の違う鍵は BadRequest。"
  (val parsed (try
                (json.loads (.decode body "utf-8"))
                (except [error ValueError]
                  (return (BadRequest :reason (.format "本文が UTF-8 の JSON でない: {}" error))))))
  (when (not (isinstance parsed dict))
    (return (BadRequest :reason "本文は JSON の object {keys}")))
  (val keys (.get parsed "keys"))
  (when (not (and (isinstance keys list) keys))
    (return (BadRequest :reason "keys は空でない list")))
  (when (> (len keys) PEEK-MAX-KEYS)
    (return (BadRequest :reason (.format "keys は {} 個まで({} 個)" PEEK-MAX-KEYS (len keys)))))
  (val malformed (lfor k keys :if (not (and (isinstance k str) (re.fullmatch "[0-9a-f]{64}" k))) k))
  (when malformed
    (return (BadRequest :reason (.format "鍵は 64 桁の小文字の 16 進: {!r}" (get malformed 0)))))
  (PeekKeys :keys (tuple (dict.fromkeys keys))))
