;;; Pod の止まりの印(AL-D2p — card ki-bfa05ac34c77): worker の Pod の preStop(drain の入口 drain_main の drain)が、止まりが始まった事を
;;; worker の本体へ伝える Pod の中の file。preStop は別の process が coordinator へ drain を頼むだけで、worker の本体は coordinator の返事の
;;; 印(supersededBy)でしか退けなかった — 印の欠けた拍に、止まる Pod の古い世代が handoff の job の新しい試行を起こした(10-11 の zeus の
;;; 画面の process の群 — card ki-3a57786eba88)。
;;;   書く口 = leaving-mark-written(preStop の drain の頭 — coordinator へ頼む前に、この Pod の worker の世代を書く)
;;;   読む口 = leaving-mark-read(印の世代がこの process の世代と同じか — worker の観測の答え手 worker/protocol/leaving_observer が呼ぶ)
;;; 置き場は世代の file(DOEFF_WORKER_BOOT_FILE・boot.sh の既定 /tmp/doeff-worker-boot)の隣 — 両方の入口が既に知る path から決める。印は
;;; 世代つきなので、/tmp が worker の process より長く残る機体でも、前の process の印を新しい process の止まりと読まない。file の I/O は
;;; file の効果(答え手 = 入口が並べる os-file-handler)。
;;; 書き手と path と形はこの module だけが持つ。依存してよいのは preStop の入口(drain_main)と観測の答え手(leaving_observer)だけで、
;;; worker の入口(main)は答え手の module だけを読む(module の契約 .agents/code-quality.d/doeff-cluster-worker-leaving-mark.json — 設計検証
;;; lt-C071SZK8EQTX19F3YV1MSWT53X の盲検 B: 入口が印を新しい世代へ引き継ぐ 2 人目の書き手になると、前の process の印を新しい process の
;;; 止まりと読む)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_core_effects [FileFailed ReadText WriteText])

;; 世代の file の名に足して止まりの印の名にする。
(val LEAVING-SUFFIX ".leaving")


(defk leaving-path [boot-file]
  {:pre [(: boot-file str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "世代の file の path から止まりの印の path を決めるため(書く口と読む口が同じ綴りを読む)。"
  (+ boot-file LEAVING-SUFFIX))


(defk leaving-mark-written [boot-file boot]
  {:pre [(: boot-file str) (: boot str)] :post [(: % (| FileFailed None))] :tags {:context "worker" :role "protocol"}}
  "preStop の drain の頭で、この Pod の止まりが始まった事を世代 boot の印として書くため(置き換えで書く — 読み手が書きかけを見ない)。
   答え = 書けなかった訳(FileFailed)か None。書けなくても preStop の drain は続ける(呼び手が 1 行にする)— 印が無い worker は今どおり
   coordinator の印で退く。"
  (<- path str (leaving-path boot-file))
  (<- answer (WriteText path (+ boot "\n") :replace True))
  answer)


(defk leaving-mark-read [boot-file boot]
  {:pre [(: boot-file (| str None)) (: boot str)] :post [(: % bool)] :tags {:context "worker" :role "protocol"}}
  "この process(世代 boot)の Pod の止まりが始まったかを読むため: 世代の file の隣の印が在り、その世代が boot と同じ時だけ真。世代の
   file を持たない worker(boot-file が None — Pod の外)・印が無い・読めない・別の世代の印は偽。"
  (when (is boot-file None) (return False))
  (<- path str (leaving-path boot-file))
  (<- text (ReadText path))
  (and (isinstance text str) (= (.strip text) boot)))
