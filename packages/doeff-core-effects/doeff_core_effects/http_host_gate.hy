;;; 外への HTTP の要求(HttpRequest)を、通す宛先の host の集合で絞る答え手(agora-redesign #3412 — 手元の構成が本物の資格で上流を撃たない
;;; ため。cisco-c8 の決め 2026-10-05 05:53: 手元の預かり所の job の HTTP は 127.0.0.1 だけに答える)。
;;;
;;;   http-host-gate [hosts]  宛先の host(URL の hostname — port は見ない)が hosts に無い要求を止める。要求が届かない失敗を値で受ける形
;;;                           (failures-as-values)なら HttpFailed(kind = CONNECT-FAILED — 繋がらなかった)で答え、そうでなければ名指しの例外
;;;                           HostNotAllowed で落とす(transport の例外が上がるのと同じ向き — 黙って値を返さない)。hosts に在る要求は答えず、
;;;                           外側の答え手(本物の HTTP)へそのまま流れる(:when)。
;;;
;;; 何の答えも作らない — 止めた要求を外へ出さないだけ。宛先の決めは呼び手が引数で渡す(この module は名を知らない)。
(require doeff-hy.macros [defhandler val])
(import urllib.parse [urlsplit])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest])

(val MODULE-TAGS {:context "http" :role "foundation"})


(defclass HostNotAllowed [ConnectionError]
  "宛先の host が通す集合に無い要求を、失敗を値で受けない呼び手へ止めた(接続しなかった)— url と通す集合を名指す。"
  (defn #^ None __init__ [self #^ str url #^ (get frozenset str) hosts]  ; defk にできない: 例外の型を作る時に Python が呼ぶ口(__init__)
    (.__init__ (super) (.format "宛先 {} の host は通す集合 {} に無い — 要求を出していない" url (sorted hosts)))
    (setv self.url url
          self.hosts hosts)))


(defhandler http-host-gate [#^ (get frozenset str) hosts]
  "宛先の host が hosts に無い HttpRequest を止める(失敗を値で受ける要求には HttpFailed・それ以外には HostNotAllowed)。hosts に在る要求は外側へ流す。"
  {:tags {:context "http" :role "foundation"}}
  ;; 引数に残す理由: 通す宛先は組み立てごとに違う外の世界の形(手元の構成 = この機体の中だけ)で、Ask の設定ではない。
  (HttpRequest [url failures-as-values]
    :when (not (in (. (urlsplit url) hostname) hosts))
    (if failures-as-values
        (resume (HttpFailed :url url :detail (.format "宛先の host は通す集合 {} に無い — 要求を出していない" (sorted hosts))
                            :kind HttpFailureKind.CONNECT-FAILED))
        (raise (HostNotAllowed url hosts)))))
