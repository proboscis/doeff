;;; Executable ADR: doeff's GitHub tracker (issues, PRs, comments) is written in English, and design or work notes that assume
;;; the maintainer's internal infrastructure are not written to this tracker; they go to the maintainer's internal tracker.
;;;
;;; The handling of the open Japanese issues was decided as option a (the content of the option is the :problem fact).
;;;
;;; The advice at the moment of writing lives outside doeff: agent-commons' defjevrule doeff-tracker-is-written-in-english (fires when a command
;;; writes an issue, PR, or comment on proboscis/doeff; agent-commons 17d5d6b3). On the doeff side we keep the English paragraph in the entry file
;;; (AGENTS.md) and a deftest that checks the paragraph stays (R2, R3). Handling of the existing Japanese issues is outside this ADR (tracked separately
;;; in the maintainer's internal tracker).
;;;
;;; Rollback: revert the commit that added this file and the AGENTS.md paragraph, and rebuild the ledger with `make enforcement-ledger` (no code or wire changed).
;;; Retire it only on the maintainer's word. Retire the agent-commons advice at the same time.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk val])
(import doeff [run])
(import pathlib [Path])
(import re)


(val AGENTS-MD (/ (. (Path __file__) parent parent parent) "AGENTS.md"))
(val SECTION-HEADING "## Ownership & Write Authority (CRITICAL)")
(val PARAGRAPH-LEAD "- **The tracker is written in English.**")
(val REQUIRED-PHRASES #("Issues, pull requests, and comments" "written in English" "kept out of this tracker"))
;; Internal names that must not appear in the entry file's paragraph (names of the maintainer's internal infrastructure and the id prefixes of its records).
(val INTERNAL-NAME (re.compile r"(?i)\bagora\b|\bkanban\b|\bcard\b|\b(?:ki|lt|ci)-[0-9a-z]{6,}|\bc-[0-9a-z]{20,}"))
;; Non-English characters (kana, kanji, full-width forms).
(val NON-ENGLISH (re.compile "[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]"))


(defk paragraph-problems [agents-md]
  {:pre [(: agents-md str)]
   :post [(: % list)]
   :tags {:context "doeff-tracker-adr" :role "judgment"}}
  "From the AGENTS.md text, the list of messages naming what is missing from the write-in-English paragraph (empty = the paragraph is intact).
  The paragraph sits inside the section Ownership & Write Authority, holds the required phrases, is written only in English, and holds no internal names (R2, R3)."
  (when (not-in SECTION-HEADING agents-md)
    (return [(+ "section " SECTION-HEADING " is missing")]))
  (val section (get (.split (get (.split agents-md SECTION-HEADING 1) 1) "\n## " 1) 0))
  (val found (re.search (+ (re.escape PARAGRAPH-LEAD) r"[^\n]*(?:\n  [^\n]+)*") section))
  (when (is found None)
    (return [(+ "section " SECTION-HEADING " has no paragraph " PARAGRAPH-LEAD)]))
  (val paragraph (.join " " (.split (.group found 0))))
  (+ (lfor phrase REQUIRED-PHRASES :if (not-in phrase paragraph) (+ "paragraph lacks phrase " (repr phrase)))
     (if (is (NON-ENGLISH.search paragraph) None) []
         [(+ "paragraph has a non-English character: " (repr (.group (NON-ENGLISH.search paragraph) 0)))])
     (if (is (INTERNAL-NAME.search paragraph) None) []
         [(+ "paragraph has internal names: " (str (INTERNAL-NAME.findall paragraph)))])))


(defadr ADR-DOE-TRACKER-001
  :title "doeff's GitHub tracker (titles and bodies of issues, PRs, and comments) is written in English. Design or work notes that assume the maintainer's internal infrastructure are not written to this tracker; they go to the maintainer's internal tracker. The entry file AGENTS.md holds this rule as an English paragraph, and a deftest checks the paragraph stays"
  :status "accepted"
  :scope ["AGENTS.md"
          "docs/adr/defadr_doeff_tracker_001_written_in_english.hy"]
  :problem
    [(fact
       "The handling of the Japanese issues was decided as option a. Option a moves the internal notes to the maintainer's internal tracker and closes them, keeps what is useful to outsiders with English added, and adds a rule to write in English from now on. It separates the place for public issues from the place for internal notes and removes the cause of Japanese notes mixing in.")
     (fact
       "In the 2026-10-18 classification, 39 of the 90 open issues on proboscis/doeff were in Japanese. Of these, 7 issues and 1 draft PR were notes that assumed the maintainer's internal infrastructure."
       :evidence "Classification of open issues and PRs on 2026-10-18 (report in the internal tracker)")
     (fact
       "AGENTS.md on main as of 2026-10-18 told downstream agents only to \"file an issue\"; it did not say which language to write on the tracker, nor that internal notes must not be placed there."
       :evidence "AGENTS.md, section Ownership & Write Authority, the Downstream-task agents item (373ae2638)")]
  :context
    [(interpretation
       "The cause is that bugs and designs found by the maintainer's internal agents were written to public issues as-is, in Japanese. As a matter of rule, the public tracker and the place for internal notes were not separated.")
     (interpretation
       "The tracker is read by people outside the repo, who do not know the internal infrastructure. Text that assumes internal names (infrastructure names, record ids) is unreadable to outsiders even in English. So writing in English and keeping internal notes out are made one rule.")]
  :decision
    [(rule R1 "Titles and bodies of issues, PRs, and comments on proboscis/doeff are written in English, independent of the language of the conversation itself (the Communication section of AGENTS.md).")
     (rule R2 "Design or work notes that assume the maintainer's internal infrastructure are not written to this tracker; they go to the maintainer's internal tracker. Tracker text does not contain internal names (infrastructure names, record ids).")
     (rule R3 "The section Ownership & Write Authority of the entry file AGENTS.md holds R1 and R2 in one English paragraph. The paragraph is written only in English and contains no internal names.")
     (rule R4 "The advice at the moment of writing lives outside doeff (agent-commons' defjevrule doeff-tracker-is-written-in-english). The doeff side holds only the R3 paragraph and its deftest.")]
  :laws
    [(law tracker-is-written-in-english
       :statement "AGENTS.md section Ownership & Write Authority has the paragraph PARAGRAPH-LEAD ∧ paragraph ⊇ REQUIRED-PHRASES ∧ the paragraph has only English characters ∧ the paragraph holds no internal names"
       :counterexamples
         [(counterexample "A change tidying AGENTS.md drops the paragraph, and the tracker-language rule silently disappears from the entry file — downstream agents write Japanese issues again")
          (counterexample "The paragraph is rewritten in Japanese, or an internal infrastructure name is written into it — the rule becomes unreadable to outsiders in the entry file")]
       :enforced-by ["docs/adr/defadr_doeff_tracker_001_written_in_english.hy::test-adr-doe-tracker-001-agents-md-keeps-the-english-paragraph"
                     "docs/adr/defadr_doeff_tracker_001_written_in_english.hy::test-adr-doe-tracker-001-broken-paragraphs-are-named"]
       :wiring "Wired (2026-10-18) — the tests in this file. The advice at the moment of writing is agent-commons' defjevrule (R4)")]
  :enforcement
    [(deftest test-adr-doe-tracker-001-agents-md-keeps-the-english-paragraph
       ;; The current AGENTS.md keeps the write-in-English paragraph intact (R3).
       (val problems (run (paragraph-problems (.read-text AGENTS-MD :encoding "utf-8"))))
       (assert (= problems [])
               (+ "The write-in-English paragraph of AGENTS.md is broken (ADR-DOE-TRACKER-001 R3 — keep the paragraph in English in section Ownership & Write Authority): "
                  (str problems))))
     (deftest test-adr-doe-tracker-001-broken-paragraphs-are-named
       ;; The paragraph judgment names and rejects the counterexamples (no section, no paragraph, missing phrase, Japanese paragraph, paragraph with internal names).
       (val good (+ SECTION-HEADING "\n\n" PARAGRAPH-LEAD " Issues, pull requests, and comments on\n  this repository are written in English."
                    " Internal notes are kept out of this tracker.\n\n## Next\n"))
       (assert (= (run (paragraph-problems good)) []))
       (assert (= (run (paragraph-problems "# Repository Guidelines\n")) [(+ "section " SECTION-HEADING " is missing")]))
       (assert (= (run (paragraph-problems (+ SECTION-HEADING "\n\n- other\n\n## Next\n" PARAGRAPH-LEAD " written in English\n")))
                  [(+ "section " SECTION-HEADING " has no paragraph " PARAGRAPH-LEAD)]))
       (assert (= (run (paragraph-problems (.replace good " Internal notes are kept out of this tracker." "")))
                  ["paragraph lacks phrase 'kept out of this tracker'"]))
       (assert (= (len (run (paragraph-problems (.replace good "written in English" "written in English(\u82f1\u8a9e)")))) 1))
       (assert (= (run (paragraph-problems (.replace good "Internal notes" "Agora card notes")))
                  ["paragraph has internal names: ['Agora', 'card']"])))])
