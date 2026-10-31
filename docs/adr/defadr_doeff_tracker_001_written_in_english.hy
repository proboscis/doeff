;;; Executable ADR: doeff の GitHub の tracker(issue・PR・comment)は英語で書き、持ち主の内部の仕組みを前提にした設計や作業の記録は
;;; この tracker に書かず、持ち主の内部の追跡(作業ボード)に置く。
;;;
;;; 開いた issue の日本語の記録の扱いは、案 a に決まった(案の中身は :problem の fact)。
;;;
;;; 書くその場面の助言は doeff の外が持つ: agent-commons の defjevrule doeff-tracker-is-written-in-english(命令が proboscis/doeff の
;;; issue・PR・comment を書く時に出る・agent-commons 17d5d6b3)。doeff の側には、入口の file(AGENTS.md)の英語の段と、その段が残る事を
;;; 確かめる deftest を置く(R2・R3)。今ある日本語の issue の扱いはこの ADR の外(持ち主の内部の追跡で別に進める)。
;;;
;;; 戻し方: この file と AGENTS.md の段を足した commit を revert し、`make enforcement-ledger` で台帳を作り直す(code と wire は変えていない)。
;;; 外すのは持ち主の言葉が在る時だけ。agent-commons の助言も同じ時に退ける。

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
;; 入口の file の段に書かない内部の名(持ち主の内部の仕組みの名と、その記録の id の頭)。
(val INTERNAL-NAME (re.compile r"(?i)\bagora\b|\bkanban\b|\bcard\b|\b(?:ki|lt|ci)-[0-9a-z]{6,}|\bc-[0-9a-z]{20,}"))
;; 英語でない字(かな・漢字・全角)。
(val NON-ENGLISH (re.compile "[぀-ヿ㐀-鿿＀-￯]"))


(defk paragraph-problems [agents-md]
  {:pre [(: agents-md str)]
   :post [(: % list)]
   :tags {:context "doeff-tracker-adr" :role "judgment"}}
  "AGENTS.md の本文から、英語で書く決まりの段の欠けの文の列(空 = 段が残っている)— 段は節 Ownership & Write Authority の中に在り、
  要る句を持ち、英語だけで書かれ、内部の名を持たない(R2・R3)。"
  (when (not-in SECTION-HEADING agents-md)
    (return [(+ "節 " SECTION-HEADING " が無い")]))
  (val section (get (.split (get (.split agents-md SECTION-HEADING 1) 1) "\n## " 1) 0))
  (val found (re.search (+ (re.escape PARAGRAPH-LEAD) r"[^\n]*(?:\n  [^\n]+)*") section))
  (when (is found None)
    (return [(+ "節 " SECTION-HEADING " に段 " PARAGRAPH-LEAD " が無い")]))
  (val paragraph (.join " " (.split (.group found 0))))
  (+ (lfor phrase REQUIRED-PHRASES :if (not-in phrase paragraph) (+ "段に句 " (repr phrase) " が無い"))
     (if (is (NON-ENGLISH.search paragraph) None) []
         [(+ "段に英語でない字が在る: " (repr (.group (NON-ENGLISH.search paragraph) 0)))])
     (if (is (INTERNAL-NAME.search paragraph) None) []
         [(+ "段に内部の名が在る: " (str (INTERNAL-NAME.findall paragraph)))])))


(defadr ADR-DOE-TRACKER-001
  :title "doeff の GitHub の tracker(issue・PR・comment の題と本文)は英語で書く。持ち主の内部の仕組みを前提にした設計や作業の記録はこの tracker に書かず、持ち主の内部の追跡に置く。入口の file AGENTS.md がこの決まりを英語の段で持ち、段が残る事を deftest が確かめる"
  :status "accepted"
  :scope ["AGENTS.md"
          "docs/adr/defadr_doeff_tracker_001_written_in_english.hy"]
  :problem
    [(fact
       "日本語の issue の扱いは案 a に決まった。案 a は、内部の記録を作業ボードへ移して閉じ、外の人に役立つ物は英語を足して残し、これからは英語で書く決まりを足す。公開の issue と内部の記録の置き場を分け、日本語の記録が混ざる原因を取り除く。")
     (fact
       "2026-10-18 の分類で、proboscis/doeff の開いた issue 90 件のうち 39 件が日本語だった。うち 7 件と下書きの PR 1 件は、持ち主の内部の仕組みを前提にした記録だった。"
       :evidence "2026-10-18 の開いた issue と PR の分類(内部の追跡の報告)")
     (fact
       "2026-10-18 の main の AGENTS.md は、下流の agent に「issue を出す」とだけ書き、tracker に書く言葉と、内部の記録を置かない事を書いていなかった。"
       :evidence "AGENTS.md の節 Ownership & Write Authority の Downstream-task agents の項(373ae2638)")]
  :context
    [(interpretation
       "原因は、持ち主の内部の agent が見つけた不具合や設計を、日本語のまま公開の issue に書いてきた事。公開の tracker と内部の記録の置き場が、決まりの上で分かれていなかった。")
     (interpretation
       "tracker を読むのは repo の外の人で、内部の仕組みを知らない。内部の名(仕組みの名・記録の id)を前提にした文は、英語にしても外の人に読めない。だから英語で書く事と、内部の記録を置かない事を 1 つの決まりにする。")]
  :decision
    [(rule R1 "proboscis/doeff の issue・PR・comment の題と本文は英語で書く。会話そのものの言葉(AGENTS.md の Communication の節)には依らない。")
     (rule R2 "持ち主の内部の仕組みを前提にした設計や作業の記録は、この tracker に書かず、持ち主の内部の追跡に置く。tracker の文には内部の名(仕組みの名・記録の id)を書かない。")
     (rule R3 "入口の file AGENTS.md の節 Ownership & Write Authority が、R1 と R2 を英語の段 1 つで持つ。段は英語だけで書き、内部の名を書かない。")
     (rule R4 "書くその場面の助言は doeff の外(agent-commons の defjevrule doeff-tracker-is-written-in-english)が持つ。doeff の側には R3 の段とその deftest だけを置く。")]
  :laws
    [(law tracker-is-written-in-english
       :statement "AGENTS.md の節 Ownership & Write Authority に段 PARAGRAPH-LEAD が在る ∧ 段 ⊇ REQUIRED-PHRASES ∧ 段は英語の字だけ ∧ 段は内部の名を持たない"
       :counterexamples
         [(counterexample "AGENTS.md を整える変更で段が消え、tracker の言葉の決まりが入口の file から黙って無くなる — 下流の agent はまた日本語の issue を書く")
          (counterexample "段を日本語に書き換える、または内部の仕組みの名を段に書く — 外の人が入口の file で読めない決まりになる")]
       :enforced-by ["docs/adr/defadr_doeff_tracker_001_written_in_english.hy::test-adr-doe-tracker-001-agents-md-keeps-the-english-paragraph"
                     "docs/adr/defadr_doeff_tracker_001_written_in_english.hy::test-adr-doe-tracker-001-broken-paragraphs-are-named"]
       :wiring "配線済み(2026-10-18)— この file のテスト。書く場面の助言は agent-commons の defjevrule(R4)")]
  :enforcement
    [(deftest test-adr-doe-tracker-001-agents-md-keeps-the-english-paragraph
       ;; 今の AGENTS.md に、英語で書く決まりの段が欠けなく残る(R3)。
       (val problems (run (paragraph-problems (.read-text AGENTS-MD :encoding "utf-8"))))
       (assert (= problems [])
               (+ "AGENTS.md の英語で書く決まりの段が欠けた(ADR-DOE-TRACKER-001 R3 — 段は節 Ownership & Write Authority に英語で残す): "
                  (str problems))))
     (deftest test-adr-doe-tracker-001-broken-paragraphs-are-named
       ;; 反例(節が無い・段が無い・句が欠けた・日本語の段・内部の名を持つ段)を、段の欠けの判じが名指して断る。
       (val good (+ SECTION-HEADING "\n\n" PARAGRAPH-LEAD " Issues, pull requests, and comments on\n  this repository are written in English."
                    " Internal notes are kept out of this tracker.\n\n## Next\n"))
       (assert (= (run (paragraph-problems good)) []))
       (assert (= (run (paragraph-problems "# Repository Guidelines\n")) [(+ "節 " SECTION-HEADING " が無い")]))
       (assert (= (run (paragraph-problems (+ SECTION-HEADING "\n\n- other\n\n## Next\n" PARAGRAPH-LEAD " written in English\n")))
                  [(+ "節 " SECTION-HEADING " に段 " PARAGRAPH-LEAD " が無い")]))
       (assert (= (run (paragraph-problems (.replace good " Internal notes are kept out of this tracker." "")))
                  ["段に句 'kept out of this tracker' が無い"]))
       (assert (= (len (run (paragraph-problems (.replace good "written in English" "written in English(英語)")))) 1))
       (assert (= (run (paragraph-problems (.replace good "Internal notes" "Agora card notes")))
                  ["段に内部の名が在る: ['Agora', 'card']"])))])
