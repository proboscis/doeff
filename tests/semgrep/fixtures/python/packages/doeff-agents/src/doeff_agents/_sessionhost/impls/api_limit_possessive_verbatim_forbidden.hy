;;; Hit fixture: the possessive api-limit wording family (you've hit/reached
;;; your … limit) is matched by the bounded family regex
;;; API-LIMIT-EXHAUSTED-FAMILY-RE in sessionhost/impls/markers.hy —
;;; ACP ADR 0049 R9 (revised 2026-08-07). A verbatim possessive substring is
;;; the enumeration that broke identically three times (2026-07-20 / 07-26 /
;;; 08-06: 22 limit-death terminals discarded as run_failed/retryable=false).

(deff has-api-limit-marker-bad [output]
  (setv text (tail-lower output 30))
  (bool (in "you've hit your brand new limit" text)))
