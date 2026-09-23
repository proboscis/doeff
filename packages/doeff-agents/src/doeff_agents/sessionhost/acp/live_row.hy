(require doeff-hy.macros [defk deftest <-])

;;; 名前が指す**生きている行**を解く 3 値の純関数(段 12・agora-redesign #317 規則 1「名前は生きている行の中で一意」/ #320・
;;; operator の決定 2026-09-16)。Haskell の Acp.App.Scheduling.LiveRow(配置の腕の表)と同じ規則で、Hy の読み手(据え直しの
;;; 取り下げ = dotfiles・agentd の claim = doeff)がこちらを読む。両方が docs/contracts/scheduling.json の liveRow.cases を通す
;;; (golden の突合 = clients/hy/acp_client/tests/test_live_row_golden.hy)。
;;;
;;; 同じ名前の行が並ぶのは、機体が agentd の再起動ごとに同じ spec.name で再参加し、退役した行が GC に畳まれるまで残るから
;;; (2026-09-16 19:09 の一覧: CA-20038667 gone・-10 joined・-2 … -9 gone — #295)。名前だけで索引した読み手は退役の行に当たる:
;;; 配置の監督は結んだ直後の生きている機体を gone と読み(#295)、据え直しの withdraw-node は 2 日前に終端した行を指して 409(#242)。
;;;
;;; 規則は 1 点: 終端の行は候補にしない・生きている行は lease の新しい順(lease の無い行 = まだ一度も鼓動していない行は最後)・
;;; その本数が答え。many は規則 1 の違反で、受付が一意を強制するまでの間、動き続けねばならない呼び手は先頭(preferred-live-row)を
;;; 採り、推測してはならない呼び手は断る。一覧の順は入力ではない(同じ lease の 2 行の順だけが一覧の順)。
;;;
;;; 入力の形は「項(entry)」= {"name": str, "alive": bool, "lease": int | None, "row": dict} — 行の欄の綴りを知るのは kind ごとの
;;; 読み(node の行 = node-row-entry)で、判断(resolve-live-row)は綴りを知らない。答え = {"verdict": "none" | "one" | "many",
;;; "live": [row …](lease の新しい順), "retired": [row …](同)}。

;; 契約 scheduling.json liveRow.verdicts の語。
(setv VERDICT-NONE "none")
(setv VERDICT-ONE "one")
(setv VERDICT-MANY "many")
(setv VERDICTS [VERDICT-NONE VERDICT-ONE VERDICT-MANY])

;; node の行の綴り(契約 scheduling.json liveRow.nodeRow)。
(setv NODE-STATE-JOINED "joined")

;; lease の新しい順の鍵(無名 fn の値 — 判断の補助で、run 境界ではない): lease の無い項は最後・同じ lease は sorted の安定性で
;; 一覧の順のまま。
(setv _FRESHEST-FIRST (fn [entry] #((if (is (.get entry "lease") None) 1 0) (- (int (or (.get entry "lease") 0))))))


(defk resolve-live-row [entries]
  {:pre [(: entries list)]
   :post [(: % dict)]}
  ;; 1 つの名前の項の列を判じる。終端(alive が偽)の行は候補にしない・生きている行の本数が答え。
  (setv live (sorted (lfor entry entries :if (get entry "alive") entry) :key _FRESHEST-FIRST))
  (setv retired (sorted (lfor entry entries :if (not (get entry "alive")) entry) :key _FRESHEST-FIRST))
  (setv verdict (cond
                  (= (len live) 0) VERDICT-NONE
                  (= (len live) 1) VERDICT-ONE
                  True VERDICT-MANY))
  {"verdict" verdict
   "live" (lfor entry live (get entry "row"))
   "retired" (lfor entry retired (get entry "row"))})


(defk preferred-live-row [resolved]
  {:pre [(: resolved dict)]
   :post [(: % (| dict None))]}
  ;; 動き続けねばならない呼び手が採る行: one ならその行・many なら先頭(lease の最も新しい行)・none なら None。
  (setv live (get resolved "live"))
  (if (= (len live) 0) None (get live 0)))


(defk live-rows-by-name [entries]
  {:pre [(: entries list)]
   :post [(: % dict)]}
  ;; 一覧の全部の名前を一度に判じる: 名前ごとに束ね(名前の中の順は一覧の順)、束ごとに resolve-live-row。
  (setv grouped {})
  (for [entry entries]
    (.append (.setdefault grouped (str (get entry "name")) []) entry))
  (setv judged {})
  (for [name (sorted (.keys grouped))]
    (<- resolved dict (resolve-live-row (get grouped name)))
    (setv (get judged name) resolved))
  judged)


;; --- node の行の読み(契約 liveRow.nodeRow の綴り — 判断はこの綴りを知らない)-------------------------

(defk node-row-entry [row]
  {:pre [(: row dict)]
   :post [(: % dict)]}
  ;; name = resourceSpecJson.name・alive = resourceStatusJson.state == "joined"・lease = resourceStatusJson.lease.expiresAt(epoch ms)。
  (setv spec (.get row "resourceSpecJson"))
  (setv status (.get row "resourceStatusJson"))
  (setv name (if (isinstance spec dict) (str (.get spec "name" "")) ""))
  (setv state (if (isinstance status dict) (.get status "state") None))
  (setv lease (if (isinstance status dict) (.get status "lease") None))
  (setv expires (if (isinstance lease dict) (.get lease "expiresAt") None))
  {"name" name
   "alive" (= state NODE-STATE-JOINED)
   "lease" (if (isinstance expires int) expires None)
   "row" row})


(defk resolve-live-node-row [rows name]
  {:pre [(: rows list) (: name str)]
   :post [(: % dict)]}
  ;; node の行の一覧から、name が指す生きている行を解く(他の名前の行は入力に数えない)。
  (setv entries [])
  (for [row rows]
    (<- entry dict (node-row-entry row))
    (when (= (get entry "name") name)
      (.append entries entry)))
  (<- resolved dict (resolve-live-row entries))
  resolved)


(defk live-node-rows-by-name [rows]
  {:pre [(: rows list)]
   :post [(: % dict)]}
  ;; node の行の一覧の全部の名前を一度に判じる。
  (setv entries [])
  (for [row rows]
    (<- entry dict (node-row-entry row))
    (.append entries entry))
  (<- judged dict (live-rows-by-name entries))
  judged)


;; --- deftests(純粋・契約の cases は tests/test_live_row_golden.hy が撃つ)----------------------------

(defk _entry [name alive lease tag]
  {:pre [(: name str) (: alive bool) (: lease (| int None)) (: tag str)]
   :post [(: % dict)]}
  {"name" name "alive" alive "lease" lease "row" {"tag" tag}})


(deftest test-resolve-live-row-is-three-valued-and-orders-by-the-freshest-lease
  (<- none dict (resolve-live-row []))
  (assert (= none {"verdict" "none" "live" [] "retired" []}))
  (<- a dict (_entry "m" True 100 "a"))
  (<- b dict (_entry "m" True 300 "b"))
  (<- c dict (_entry "m" True None "c"))
  (<- g1 dict (_entry "m" False 200 "g1"))
  (<- g2 dict (_entry "m" False 50 "g2"))
  ;; 1 本: 退役の行が前に並んでいても答えは one で、退役の行は lease の新しい順に記録として残る。
  (<- one dict (resolve-live-row [g2 g1 a]))
  (assert (= (get one "verdict") "one"))
  (assert (= (get one "live") [{"tag" "a"}]))
  (assert (= (get one "retired") [{"tag" "g1"} {"tag" "g2"}]))
  ;; 2 本以上: many・lease の新しい順・lease の無い行は最後(一覧の順は入力ではない)。
  (<- many dict (resolve-live-row [c a b]))
  (assert (= (get many "verdict") "many"))
  (assert (= (get many "live") [{"tag" "b"} {"tag" "a"} {"tag" "c"}]))
  (<- preferred (| dict None) (preferred-live-row many))
  (assert (= preferred {"tag" "b"}))
  ;; 退役だけ: none で退役の行が記録。
  (<- retired-only dict (resolve-live-row [g1 g2]))
  (assert (= (get retired-only "verdict") "none"))
  (assert (= (get retired-only "retired") [{"tag" "g1"} {"tag" "g2"}]))
  (<- nothing (| dict None) (preferred-live-row retired-only))
  (assert (is nothing None))
  (for [judged [none one many retired-only]]
    (assert (in (get judged "verdict") VERDICTS))))


(deftest test-node-row-entry-reads-the-contract-spellings-and-other-names-are-not-input
  (setv joined {"resourceKey" "default:node:mac-1-2" "resourceSpecJson" {"name" "mac-1"}
                "resourceStatusJson" {"state" "joined" "lease" {"owner" "mac-1" "heartbeatAt" 1 "expiresAt" 900}}})
  (setv gone {"resourceKey" "default:node:mac-1" "resourceSpecJson" {"name" "mac-1"} "resourceStatusJson" {"state" "gone"}})
  (setv other {"resourceKey" "default:node:mac-2" "resourceSpecJson" {"name" "mac-2"}
               "resourceStatusJson" {"state" "joined" "lease" {"expiresAt" 950}}})
  (<- entry dict (node-row-entry joined))
  (assert (= (get entry "name") "mac-1"))
  (assert (= (get entry "alive") True))
  (assert (= (get entry "lease") 900))
  (<- resolved dict (resolve-live-node-row [gone joined other] "mac-1"))
  (assert (= (get resolved "verdict") "one"))
  (assert (= (get resolved "live") [joined]))
  (assert (= (get resolved "retired") [gone]))
  (<- by-name dict (live-node-rows-by-name [gone joined other]))
  (assert (= (sorted (.keys by-name)) ["mac-1" "mac-2"]))
  (assert (= (get (get by-name "mac-2") "verdict") "one"))
  (<- unknown dict (resolve-live-node-row [gone joined other] "mac-9"))
  (assert (= unknown {"verdict" "none" "live" [] "retired" []})))
