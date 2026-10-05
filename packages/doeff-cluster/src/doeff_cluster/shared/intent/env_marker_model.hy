;;; 実行環境の root の完成の印(env の準備が最後に書く file の名と形の版)と、file の中身の指紋を問う effect FileSha256 —
;;; worker の準備(worker/core/env_prepare)と、送り手の宣言の組み立て・入口の検め(shared/core/runtime_env・runtime_identity・
;;; shared/protocol/checkout_reads・runtime_facts)が同じ約束を読む共有の部品(#2025 の 3 本目で env_prepare から分けた)。
;;;
;;; 印の bytecode の欄(#3607 の H2): 準備の bytecode の処理ステージが焼いた数と処理ごとの秒(BytecodeCounts — 印の JSON の形は defwire の
;;; 宣言 1 つ: 読み手 runtime_identity の marker-bytecode が parse で解き、書き手 env_prepare の env-marker->json は同じ camel の名で綴る)。欄を足しただけで
;;; 形式の版は変えない — 読み手は知らない欄を読まずに落とす。この欄の無い印(足す前に書かれた印・焼く木が無かった準備)は「記録が無い」
;;; (None — 0 で埋めない)。
;;; 数の欄は報告と log のためだけの値で、置き場を名指す読み手(入口の検め runtime_identity の decode-marker・worker の known-roots・引き継ぎ元の
;;; 選び carry-candidates)は読まない — 欄の名を替えても(#3675 で compiled を rebuilt と reused に分けた)、PVC に残る前の形の印の置き場は
;;; 今までどおり名指せる。
;;; 印の hyVersion の欄(#3706): root の venv の Hy の compiler の版(書き手 env_prepare の env-marker->json)。読むのは引き継ぎ元の選び
;;; carry-candidates だけ(worker の known-roots が頼みの JSON へ写す)で、置き場の名指し(decode-marker・known-roots が完成した root に
;;; 数えるか)は読まない。欄の無い前の印の root は「版が分からない」で、引き継ぎ元にしない。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defwire])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; dataclass は defwire の展開が使う


(val ENV-MARKER ".doeff-env-ready.json")
(val ENV-MARKER-FORMAT 1)


(defwire TreeCounts
  "1 回の焼きの木 1 つの数: name = repo の木の名(root の下の dir の名 — path は載せない)・carried = 前の root から hardlink で
   引き継いだ .pyc の数・焼く計画の file のうち rebuilt = 焼いた・reused = 在った .pyc が今の source と macro に合い焼かずに残した・
   failed = 焼けなかった file の数(rebuilt + reused + failed = 焼く計画の数 — #3675)。"
  {:tags {:context "doeff-cluster" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ str name)
  (#^ int carried)
  (#^ int rebuilt)
  (#^ int reused)
  (#^ int failed))


(defwire BytecodeCounts
  "準備の bytecode の処理ステージの 1 回の焼きの数と秒(焼く道具の全体の報告の行の値): carried・rebuilt・reused・failed = 全部の木の合計
   (欄の意味は TreeCounts)・scan-seconds = 木の走査・closure-seconds = 閉包の歩み・carry-seconds = 引き継ぎ・compile-seconds = 焼き・trees = 木ごとの数
   (TreeCounts の列・要求の木の順)。準備が長い時に、どの処理で時間を使ったかを印から読むため。"
  {:tags {:context "doeff-cluster" :role "type" :reads "json"} :names :camel :unknown :ignore}
  (#^ int carried)
  (#^ int rebuilt)
  (#^ int reused)
  (#^ int failed)
  (#^ float scan-seconds)
  (#^ float closure-seconds)
  (#^ float carry-seconds)
  (#^ float compile-seconds)
  (#^ (get tuple #(TreeCounts ...)) trees))


(defeffect FileSha256
  "file の中身の sha256(16 進)。答え = str か None(file が無い)。準備(処理ステージ 4)と送り手の宣言の組み立て(runtime_env)が出す。"
  {:fields [(: path str)]
   :answer (| str None)
   :tags {:context "runtime-env" :role "intent"}})
