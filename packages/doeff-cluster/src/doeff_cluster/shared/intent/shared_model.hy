;;; 共有の保存(cluster のどの worker で走っても同じ意味になる読み書き)の effect。service と task の業務コードが
;;; 状態を読み書きする口はこれだけ(file・lock・socket を直に触らない — 規則 = repo の root の .semgrep.yaml)。
;;; service どうしは戻り値でやり取りせず、この effect を通してだけつながる。本番では業務の系の共有の置き場に当たる物の、実験用の代役。
;;;
;;;   ReadShared prefix              → {鍵: 値}(鍵が prefix で始まる行だけ)
;;;   WriteShared key value expect [ttl-seconds] → bool。ttl-seconds を付けた行は期限を過ぎると coordinator が消す。expect = ANY なら無条件・None なら「行が無い時だけ」・値なら「今の値がそれと等しい時だけ」
;;;                                     (compare-and-set。複数の実行役が同じ行を取り合わないため)
;;; 値と expect の値は OpaqueJson(doeff_hy.json_value — 形を書き手が決める JSON を中を読まずに運ぶ型・#2543)。作り手は
;;; (OpaqueJson.of 値) で包み、盤へ送る要求の形を組む foundation/board_requests.hy だけが JSON の値へ戻す。compare-and-set は
;;; 盤が解いた値で比べる(欄の順は見ない — OpaqueJson の文字列の等しさでは比べない)。handler = shared_handlers.hy の shared-http(coordinator の /board — テストは HTTP の層の fake の盤の上で)。
;;; この module は型と effect だけを持つ(SDK の型の置き場・#2107)。書いてよいかの判断(board-allows・期限の検め)は
;;; doeff_cluster.shared.core.board_rules — coordinator の /board とテストの fake の盤が同じ定義を使う。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_hy.json_value [OpaqueJson])


(defclass AnyExpect []
  "書きの条件(expect)を付けない印 ANY の型 — 書きの handler が expect の型に書けるよう公開の名にする(以前は内部名・#1692)。"
  (defn #^ str __repr__ [self] "ANY"))


(setv ANY (AnyExpect))

(defclass [(dataclass :frozen True)] ReadShared [EffectBase]
  (#^ str prefix))


(defclass [(dataclass :frozen True)] WriteShared [EffectBase]
  (#^ str key)
  (#^ OpaqueJson value)
  ;; ANY = 無条件・None = 行が無い時だけ・OpaqueJson = 今の値がそれと(解いた値で)等しい時だけ。
  (setv #^ (| OpaqueJson AnyExpect None) expect ANY)
  ;; 行の期限(秒)。coordinator は期限を過ぎた行を消す(盤の掃除・2026-09-25)。None = ずっと残す。
  (setv #^ (| int float None) ttl-seconds None))
