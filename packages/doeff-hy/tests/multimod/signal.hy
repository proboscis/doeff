;;; Signal generation — uses LLMRank + calls data_fetch
(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import tests.multimod.effects [LLMRank SendSlack])
(import tests.multimod.data_fetch [fetch-all-data])

(defk generate-signal [ticker day]
  {:pre [] :post []}
  (<- data (fetch-all-data ticker day))
  (<- ranking (LLMRank :prompt "rank stocks"))
  {"data" data "ranking" ranking})

(defk generate-and-notify [ticker day]
  {:pre [] :post []}
  (<- signal (generate-signal ticker day))
  (<- (SendSlack :message "Signal generated"))
  signal)
