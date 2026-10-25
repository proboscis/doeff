;;; ReachJob(届くかの検査)の契約テストの解釈器(composition root)— 同じ契約の Program を、handler だけ替えて走らせる。
;;;
;;;   job-reach-handover  模擬: job-reach-handover が要求を試しの API notes の答えの 1 点(CallJob の契約と同じ notes-answer)へ直に渡す
;;;   job-reach-sender    本物: job-reach-sender が道ごとの宛先の環境変数(名の道 NOTES-ENVIRON・番地の道 NOTES-ADDRESS-ENVIRON — 試しでは
;;;                       どちらも同じ口の URL)へ HttpRequest を 1 本出し、その HttpRequest には CallJob の契約と同じ served-notes が HTTP の
;;;                       待ち受けと同じく要求の行を読み直して同じ答えの 1 点へ渡して答える(道が選ぶ環境変数は本物だけの性質 — 契約の外)
;;;
;;; 両方とも、表に無い API の ReachJob は外側の答え手 elsewhere-reach が答える(外側へ回す性質を同じ Program で見る)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [Program with_handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.shared.intent.job_reach_model [JobNoWay ReachJob])
(import doeff_cluster.shared.protocol.job_call_handlers [Counterpart])
(import doeff_cluster.shared.protocol.job_reach_handlers [ReachDestination job-reach-handover job-reach-sender])
(import tests.job_call_contract_handlers [NOTES-API NOTES-ENVIRON NOTES-ROOT NOTES-URL notes-answer served-notes])

;; 番地の道の宛先の環境変数の名(試しの値は名の道と同じ口の URL)。
(val NOTES-ADDRESS-ENVIRON "NOTES_ADDRESS_URL")
;; 表に無い API の ReachJob に外側が返す答え(外側へ回ったかを読む印)。
(val ELSEWHERE-REACH (JobNoWay :reason "外側の答え手が答えた"))


(defhandler reach-environ
  ;; 本物の側の道ごとの宛先の環境変数の読みに答えるため(名 2 つだけ・ほかは外側へ)。
  (Ask [key]
    (cond (= key NOTES-ENVIRON) (resume NOTES-URL)
          (= key NOTES-ADDRESS-ENVIRON) (resume NOTES-URL)
          True (reperform effect))))


(defhandler elsewhere-reach
  ;; 表に無い API の ReachJob に、両方の解釈器で同じ印の答えを返すため(頭の註)。
  (ReachJob [api]
    (resume ELSEWHERE-REACH)))


(defk under-reach-handover [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "模擬の組: 相手の表に notes の答えの 1 点を置いた job-reach-handover の下で program を走らせるため。"
  (<- answer (with_handlers [elsewhere-reach (job-reach-handover #((Counterpart :api NOTES-API :answer notes-answer)))] program))
  answer)


(defk under-reach-sender [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の組: 道ごとの宛先の表に notes を置いた job-reach-sender の下で program を走らせるため(HttpRequest と宛先の読みは served-notes・
   reach-environ)。"
  (<- answer (with_handlers [elsewhere-reach served-notes reach-environ
                             (job-reach-sender #((ReachDestination :api NOTES-API :root NOTES-ROOT :environ NOTES-ENVIRON
                                                                   :address-environ NOTES-ADDRESS-ENVIRON)))]
               program))
  answer)


(val INTERPRETERS {"job-reach-handover" under-reach-handover
                   "job-reach-sender" under-reach-sender})
