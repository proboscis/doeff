;;; Data fetching layer — uses FetchPrice, FetchNews
(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import tests.multimod.effects [FetchPrice FetchNews])

(defk fetch-price-data [ticker start end]
  {:pre [(: ticker str) (: start str) (: end str)] :post [(: % object)]}
  (<- prices (FetchPrice :ticker ticker))
  prices)

(defk fetch-news-for-day [day]
  {:pre [(: day str)] :post [(: % object)]}
  (<- raw (FetchNews :day day))
  raw)

(defk fetch-all-data [ticker day]
  {:pre [(: ticker str) (: day str)] :post [(: % dict)]}
  (<- prices (fetch-price-data ticker "2025-01-01" "2025-12-31"))
  (<- news (fetch-news-for-day day))
  {"prices" prices "news" news})
