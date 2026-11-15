;;; 送り手の checkout の読み(ReadCheckout・CheckoutRoot・SenderSourceRoot)を、git を起こさずに checkout の file から答える翻訳の handler
;;; (宣言の組み立てが起動のたびに git の子 process を 17〜21 本起こしていた)。git の読みの翻訳 checkout-reads の
;;; 内側に並べ、file から答えられない時だけ同じ effect を外の checkout-reads へ出し直す。I/O を持たない(file の読みは effect)。
;;;
;;;   ReadCheckout      ReadCheckoutFiles で checkout の file の姿(HEAD の commit・index の checksum・設定の file の digest・settled)を読み、
;;;                     settled で、覚えた答えに (remote・head・index の checksum・設定の digest) の汚れ無しの行と (url・head) の remote に在る
;;;                     行の両方が在れば、git を起こさずに答える。ほかは git の読み(外の ReadCheckout)へ倒し、その前と後の file の姿が同じで
;;;                     settled なら汚れ無しを、remote に在れば (url・head) を覚える。
;;;   CheckoutRoot      FindCheckoutRoot(上へ .git を探す)。見つからなければ git の読みへ倒す
;;;   SenderSourceRoot  同じ読みを送り手の source の dir(SENDER-SOURCE-DIR)で
;;;
;;; 覚えた答えを使ってよい訳:
;;;   - 汚れ無し: index の中身(checksum)と HEAD が git の読みの時と同じなら、HEAD と index の差も同じ(stage した変更は index の checksum を
;;;     変える)。作業木と index の差は、settled(全部の項の stat が作業木と同じで racy な項が無い — git status も file を読み直さない形)なら無い。
;;;     index を書き換える git status の読みと重ならないよう、前と後の file の姿が同じ時だけ覚える。
;;;   - URL: 設定の file の中身が同じなら remote.<名>.url も同じ(include した file と、機体の ~/.gitconfig にだけ在る remote の url は鍵に入れない —
;;;     宣言する checkout は remote の url を自分の .git/config に持つ)。
;;;   - remote に在る: 1 度 remote の branch に在った commit は、remote の既定の branch を force push で書き換えない限り在り続ける(この前提に
;;;     依る — 在ると読めた時だけ覚え、無いと読めた時は覚えない。push していない commit は head が変わるので鍵が合わない)。
;;;
;;; 覚えた答えの置き場: 作業木の git の dir の MEMO-NAME(1 行 1 つ・tab で区切る・種類ごとに新しい MEMO-KEPT 行を残す)。書けなくても
;;; 答えは変わらない(次の起動も git の読みへ倒すだけ)。
(require doeff-hy.macros [defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff_core_effects [FileFailed ReadText WriteText])
(import doeff_cluster._shared.intent.checkout_model [CheckoutFiles CheckoutState ReadCheckout CheckoutRoot SenderSourceRoot
                                                     ReadCheckoutFiles FindCheckoutRoot])
(import doeff_cluster._shared.protocol.checkout_reads [SENDER-SOURCE-DIR])

;; 覚えた答えの file の名(作業木の git の dir の中 — git は知らない名の file を読まない)と、種類ごとに残す行の数。
(val MEMO-NAME "doeff-checkout-reads")
(val MEMO-KEPT 16)
;; 行の種類(1 つ目の欄)。
(val CLEAN "clean")
(val ON-REMOTE "on-remote")


(defk memo-lines [files]
  {:pre [(: files CheckoutFiles)] :post [(: % tuple)]}
  "覚えた答えの行を読むため(無い・読めない file は空 — 欄を tab で分けた tuple の tuple)。"
  (<- text (| str FileFailed) (ReadText (.format "{}/{}" files.git-dir MEMO-NAME)))
  (if (isinstance text str)
      (tuple (gfor line (.splitlines text) :if line (tuple (.split line "\t"))))
      #()))


(defk clean-row [files remote url]
  {:pre [(: files CheckoutFiles) (: remote str) (: url str)] :post [(: % tuple)]}
  "汚れ無しの行を作るため(鍵 = remote・head・index の checksum・設定の digest、値 = url)。"
  #(CLEAN remote files.head files.index-digest files.config-digest url))


(defk remembered-state [files remote rows]
  {:pre [(: files CheckoutFiles) (: remote str) (: rows tuple)] :post [(: % (| CheckoutState None))]}
  "覚えた行から、file の姿 files の checkout の読みを答えるため(settled で汚れ無しの行と remote に在る行が両方在る時だけ — 無ければ None)。"
  (<- key tuple (clean-row files remote ""))
  (val url (next (gfor row rows
                       :if (and (= (len row) 6) (= (cut row 0 5) (cut key 0 5)))
                       (get row 5))
                 None))
  (if (and files.settled (is-not url None) (in #(ON-REMOTE url files.head) rows))
      (CheckoutState :head files.head :url url :dirty False :on-remote True)
      None))


(defk kept-rows [rows added]
  {:pre [(: rows tuple) (: added tuple)] :post [(: % tuple)]}
  "覚えた行に added を足し、種類ごとに新しい MEMO-KEPT 行だけを残すため(同じ行は 1 つ — 新しい方を残す)。"
  (val ordered (tuple (gfor row (+ (tuple (gfor row rows :if (not-in row added) row)) added) row)))
  (tuple (gfor kind #(CLEAN ON-REMOTE)
               row (cut (tuple (gfor row ordered :if (= (get row 0) kind) row)) (- MEMO-KEPT) None)
               row)))


(defk same-shape [before after]
  {:pre [(: before CheckoutFiles) (: after (| CheckoutFiles None))] :post [(: % bool)]}
  "git の読みの前と後で checkout の file の姿が同じか(git の読みの間に index・HEAD・設定が変わっていない)を判じるため。"
  (and (is-not after None) after.settled before.settled (= after before)))


(defk read-and-remember [path remote before rows]
  {:pre [(: path str) (: remote str) (: before CheckoutFiles) (: rows tuple)] :post [(: % CheckoutState)]}
  "git の読み(外の ReadCheckout)で答え、覚えてよい事を覚えるため(頭の註)。"
  (<- state CheckoutState (ReadCheckout path remote))
  (<- after (| CheckoutFiles None) (ReadCheckoutFiles path))
  (<- stable bool (same-shape before after))
  (<- clean tuple (clean-row before remote state.url))
  (val added (+ (if (and stable (not state.dirty) (= before.head state.head)) #(clean) #())
                (if state.on-remote #(#(ON-REMOTE state.url state.head)) #())))
  (<- kept tuple (kept-rows rows added))
  (when (!= kept rows)
    (<- _ (| FileFailed None) (WriteText (.format "{}/{}" before.git-dir MEMO-NAME)
                                         (.join "" (gfor row kept (+ (.join "\t" row) "\n")))
                                         :replace True)))
  state)


(defk remembered-checkout [path remote]
  {:pre [(: path str) (: remote str)] :post [(: % CheckoutState)]}
  "checkout の読みを、覚えた答えか git の読みで答えるため(頭の註)。"
  (<- before (| CheckoutFiles None) (ReadCheckoutFiles path))
  (when (is before None)
    (<- plain CheckoutState (ReadCheckout path remote))
    (return plain))
  (<- rows tuple (memo-lines before))
  (<- remembered (| CheckoutState None) (remembered-state before remote rows))
  (when (is-not remembered None)
    (return remembered))
  (<- state CheckoutState (read-and-remember path remote before rows))
  state)


(defk found-root [path fallback]
  {:pre [(: path str) (: fallback (| CheckoutRoot SenderSourceRoot))] :post [(: % (| str None))]}
  "path を含む checkout の根を、上へ .git を探して読むため(見つからなければ git の読み fallback を外へ出し直す)。"
  (<- found (| str None) (FindCheckoutRoot path))
  (when (is found None)
    (<- root (| str None) fallback)
    (return root))
  found)


(defhandler checkout-memo
  ;; 送り手の checkout の読みを、file から答えられれば答え、ほかは外の git の読み(checkout-reads)へ出し直す(頭の註)。
  (ReadCheckout [path remote]
    (<- state CheckoutState (remembered-checkout path remote))
    (resume state))
  (CheckoutRoot [path]
    (<- root (| str None) (found-root path (CheckoutRoot path)))
    (resume root))
  (SenderSourceRoot []
    (<- root (| str None) (found-root SENDER-SOURCE-DIR (SenderSourceRoot)))
    (resume root)))
