;;; Jev の呼び出しを覚える代理(doeff-jev-proxy)の値 — 届いた要求・返す答え・覚えた答え・本物の Jev の答え・計器の数。
;;; 流れ(service.hy)と答え手(handlers.hy・http_server.hy)の間を流れる素の値で、振る舞いを持たない。
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])


;; 呼び手が見出し Cache-Control で名乗る覚えの使い方(RFC 9111 の語 — 名乗りが無ければ NORMAL)。
;;   NORMAL          覚えていれば答え、無ければ本物の Jev に問うて覚える
;;   ONLY-IF-CACHED  覚えている時だけ答える(無ければ 504・本物の Jev を呼ばない)
;;   NO-CACHE        覚えていても本物の Jev に問い直し、新しい答えで覚え直す(較正の見張りの問い)
(defenum Directive NORMAL ONLY-IF-CACHED NO-CACHE)


;; 計器の出来事(数える物の閉じた集合)。
;;   HIT           覚えた答えで答えた(本物の Jev を呼ばずに済んだ 1 回)
;;   MISS          覚えが無く本物の Jev に問うた
;;   COALESCED     同じ鍵の同時の問いに相乗りした(本物の Jev を呼ばずに済んだ 1 回)
;;   REFRESHED     NO-CACHE で問い直した
;;   PEEK-HIT      覚えている時だけの問いが当たった
;;   PEEK-MISS     覚えている時だけの問いが外れた(Jev は呼ばない)
;;   UPSTREAM-FAILED  本物の Jev が失敗した(届かない・4xx / 5xx — 覚えない)
;;   REFUSED       身元が引けない・形の違う要求を断った
;;   FORGOTTEN     管理者が答えを消した
(defenum Event HIT MISS COALESCED REFRESHED PEEK-HIT PEEK-MISS UPSTREAM-FAILED REFUSED FORGOTTEN)


(defrecord Header
  "HTTP の見出し 1 つ(名は小文字にそろえる)。"
  (#^ str name)
  (#^ str value))


(defrecord ProxyRequest
  "届いた要求 1 つ: method・path(query なし)・見出しの列・本文の byte 列。"
  (#^ str method)
  (#^ str path)
  (#^ tuple headers)
  (#^ bytes body))


(defrecord ProxyReply
  "返す答え 1 つ: status・見出しの列・本文の byte 列。"
  (#^ int status)
  (#^ tuple headers)
  (#^ bytes body))


(defrecord StoredAnswer
  "覚えた答え 1 つ: key = 鍵 / model = 呼び手が名指した model / served-model = 答えた model の版つきの名(Jev が名乗らなければ \"\")/
   body = 本物の Jev の答えの本文(byte 列のまま)/ hits = この答えで答えた回数(覚えている時だけの問いの束は数えない — 束の数は
   計器 peek-hit が持つ・agora-redesign #1885)。"
  (#^ str key)
  (#^ str model)
  (#^ str served-model)
  (#^ bytes body)
  (#^ int hits))


(defrecord Remembered
  "置き場の覚えた答えの全部(起動の時に proxy の memory の写しを作るため・agora-redesign #1912): answers = StoredAnswer の tuple /
   served = model → その model の今の版(答えが名乗った served-model — LookupAnswer の版の決まりが読む表)。"
  (#^ tuple answers)
  (#^ dict served))


(defrecord UpstreamReply
  "本物の Jev が返した答え(status と本文)。"
  (#^ int status)
  (#^ bytes body))


(defrecord UpstreamUnreachable
  "本物の Jev に届かなかった(接続の失敗・時間切れ)。detail = 人の読む理由(キーは載せない)。"
  (#^ str detail))


(defrecord Fetched
  "鍵 1 つを取りに行った結果: reply = 本物の Jev の答え・届かなかった理由・覚えた答え(相乗りの先頭が本物の Jev を呼ぶ前に、
   先に着いた答えを見つけた時)/ remembered = 覚えた答えで済んだか。"
  (#^ (| UpstreamReply UpstreamUnreachable StoredAnswer) reply)
  (#^ bool remembered))


(defrecord Coalesced
  "同じ鍵の同時の問いを 1 回にまとめた結果: value = 先頭が取りに行った結果 / joined = 相乗りした側か。"
  (#^ Fetched value)
  (#^ bool joined))


(defrecord Counters
  "計器の数(process をまたいで置き場に残る): counts = 出来事の名 → 回数 / answers = 覚えている答えの数。"
  (#^ dict counts)
  (#^ int answers))


(defrecord Caller
  "身元の引けた呼び手: name = 名簿の名 / admin = 答えを消せる管理者か。"
  (#^ str name)
  (#^ bool admin))


(defrecord Stranger
  "身元の引けない呼び手。reason = 人の読む理由(token は載せない)。"
  (#^ str reason))
