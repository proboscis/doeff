;;; Data fetching layer — uses FetchPrice, FetchNews
(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import tests.multimod.effects [FetchPrice FetchNews])

(defk fetch-price-data [ticker start end]
  {:pre [] :post []}
  (<- prices (FetchPrice :ticker ticker))
  prices)

(defk fetch-news-for-day [day]
  {:pre [] :post []}
  (<- raw (FetchNews :day day))
  raw)

(defk fetch-all-data [ticker day]
  {:pre [] :post []}
  (<- prices (fetch-price-data ticker "2025-01-01" "2025-12-31"))
  (<- news (fetch-news-for-day day))
  {"prices" prices "news" news})
