;;; The only place that splits JSON Lines (text with one JSON value per line, separated by newlines -- CLI conversation records, stream output, etc.) into lines.
;;;
;;; It splits only on newlines (LINE-END). str.splitlines also splits on U+2028 and U+2029 written raw inside a JSON string (JSON does not require escaping them --
;;; they appear in web text) and on \x1c to \x1e, \x85, \v, and \f, turning a valid JSON line into two lines that cannot be read (measured: with four U+2028 in a production Claude Code
;;; subagent transcript, the reader rejected the line as not JSON and dropped all the call records). Packages that read JSON Lines do not
;;; split on their own but split with jsonl-lines of this module.
(require doeff-hy.macros [defk val])

;; The end of a JSON Lines line (this one character is the only thing to split on).
(val LINE-END "\n")

(defk jsonl-lines [#^ str text]
  {:pre [(: text str)] :post [(: % tuple)] :tags {:context "json-lines" :role "foundation"}}
  "To turn JSON Lines text into a list of lines of JSON values (see the note at the top -- split only on LINE-END). Lines of only whitespace are dropped. The fragment after the last newline
   is also returned as a line (the reader checks whether it was completely written -- whole-lines-end)."
  (tuple (gfor line (.split text LINE-END) :if (.strip line) line)))

(defk whole-lines-end [#^ bytes data]
  {:pre [(: data bytes)] :post [(: % int)] :tags {:context "json-lines" :role "foundation"}}
  "To find, among the bytes of JSON Lines being appended to, the end of the lines closed by a newline (the position of the byte after the last LINE-END -- 0 if none).
   The reader treats everything up to here as read, and reads the trailing fragment (a line being written) from its start on the next read. A UTF-8 multi-byte character does not
   contain 0x0A, so searching by byte does not cut in the middle of a character."
  (+ (.rfind data (.encode LINE-END "utf-8")) 1))
