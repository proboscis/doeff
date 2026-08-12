;;; Hit fixture: the non-limit provider-failure families (re-auth demanded /
;;; access revoked / context exhausted / transport error) are matched by the
;;; bounded family regexes PROVIDER-*-FAMILY-RE in
;;; sessionhost/impls/markers.hy — ACP ADR 0049 R9 (third revision,
;;; 2026-08-12). A verbatim substring test re-creates the enumeration that
;;; broke identically three times for the limit family. The drift is already
;;; on record for THIS family: one auth event renders as "Not logged in ·
;;; Run /login", "Not logged in · Please run /login" and "Login expired ·
;;; Please run /login" across three real captures — a verbatim test pinned to
;;; any one of them silently drops the other two, which is how
;;; ledger-integrity-steward lost 13 hours on 2026-08-12.

(deff has-auth-failure-marker-bad [output]
  (setv text (tail-lower output 30))
  (bool (in "not logged in · please run /login" text)))
