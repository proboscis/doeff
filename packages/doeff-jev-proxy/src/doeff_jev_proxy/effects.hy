;;; Jev の呼び出しを覚える代理の effect — 流れ(service.hy)が出し、答え手(handlers.hy)が答える。
;;;
;;;   PrepareStore      置き場の表を用意する(起動の時に 1 度 — 無ければ作る・在れば触らない)
;;;   LookupAnswer      鍵の覚えた答えを読む(今の版の model の答えだけ — 答えた model の版が変わった後の古い答えは無いと読む)
;;;   LookupAnswers     鍵の束の覚えた答えを一度に読む(覚えている時だけの問いの束 — LookupAnswer と同じ版の決まり)
;;;   LoadRemembered    置き場の覚えた答えを全部読む(起動の時に 1 度 — proxy の memory の写しを作るため)
;;;   RememberAnswer    本物の Jev の答えを覚える(同じ鍵は置き換える)
;;;   ForgetAnswer      覚えた答えを消す(答え = 消したか)
;;;   ReadAnswer        覚えた答えを版を問わず読む(管理者が中身を見るため)
;;;   AskJev            本物の Jev に本文をそのまま問う
;;;   Coalesce          同じ鍵の同時の Program を 1 回だけ走らせ、答えを全員に配る
;;;   Count             計器の出来事を 1 つ数える
;;;   CountTimes        計器の出来事を times 回数える(束の問いの当たりと外れ)
;;;   ReadCounters      計器の数を読む
;;;   IdentifyCaller    Authorization の見出しから呼び手の身元を引く
(require doeff-hy.macros [defeffect])
(import dataclasses [dataclass])
(import doeff [EffectBase Program])
(import doeff_jev_proxy.values [StoredAnswer Remembered UpstreamReply UpstreamUnreachable Coalesced Counters Caller Stranger Event])


(defeffect PrepareStore
  "置き場の表を用意する(起動の時に 1 度 — 無ければ作る・在れば触らない)。"
  {:fields []
   :answer None
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect LookupAnswer
  "鍵の覚えた答えを読む。答えた model の版が今の版と違う答えは無いと読む(model の版が変われば鍵が変わる)。"
  {:fields [(: key str)]
   :answer (| StoredAnswer None)
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect LookupAnswers
  "鍵の束の覚えた答えを一度に読む(読むだけ — 答えごとの hits を書かない・agora-redesign #1885)。LookupAnswer と同じく、答えた model の
   版が今の版と違う答えは無いと読む。答え = 覚えていた答えの
   tuple(覚えていない鍵は載らない・並びは決めない)。"
  {:fields [(: keys tuple)]
   :answer tuple
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect LoadRemembered
  "置き場の覚えた答えを版を問わず全部読み、model ごとの今の版と一緒に返す(起動の時に 1 度 — proxy の memory の写しを作るため・
   agora-redesign #1912)。"
  {:fields []
   :answer Remembered
   :tags {:context "jev-proxy" :role "intent"}})

(defeffect RememberAnswer
  "本物の Jev の答えを覚える。同じ鍵の答えは置き換え、その model の今の版を served-model にする。"
  {:fields [(: key str) (: model str) (: served-model str) (: request bytes) (: body bytes)]
   :answer None
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect ForgetAnswer
  "覚えた答えを消す。答え = 消したか(無ければ False)。"
  {:fields [(: key str)]
   :answer bool
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect ReadAnswer
  "覚えた答えを版を問わず読む(管理者が中身を見るため)。"
  {:fields [(: key str)]
   :answer (| StoredAnswer None)
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect AskJev
  "本物の Jev に本文をそのまま問う。答え = 本物の答え(status と本文)か届かなかった理由。"
  {:fields [(: body bytes)]
   :answer (| UpstreamReply UpstreamUnreachable)
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect Coalesce
  "同じ鍵の同時の Program を 1 回だけ走らせる。先頭が program を走らせ、走っている間に来た同じ鍵は先頭の答えを受け取る。"
  {:fields [(: key str) (: program Program)]
   :answer Coalesced
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect Count
  "計器の出来事を 1 つ数える。"
  {:fields [(: event Event)]
   :answer None
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect CountTimes
  "計器の出来事を times 回数える(times が 0 なら何もしない)。"
  {:fields [(: event Event) (: times int)]
   :answer None
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect ReadCounters
  "計器の数を読む。"
  {:fields []
   :answer Counters
   :tags {:context "jev-proxy" :role "intent"}})


(defeffect IdentifyCaller
  "Authorization の見出し(無ければ None)から呼び手の身元を引く。"
  {:fields [(: authorization (| str None))]
   :answer (| Caller Stranger)
   :tags {:context "jev-proxy" :role "intent"}})
