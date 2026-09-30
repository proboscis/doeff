//! DOEFF146: 判定を 1 か所に閉じ込めた語彙(agora-redesign #1106・#1192・#1371 — 元は agora-controllers の一時の検
//! check_screen_slice_single_point.hy)。
//!
//! architecture.hy の `:single-point-vocabulary [(vocabulary-scope "名" :patterns [r"…"] :files [..] :except [..] :instead "…") …]`
//! で群を宣言する。群ごとに `:files` の glob に当たった Hy の file(`:except` に挙げた file を除く)を読み、註(`;` から行末・
//! 文字列の外)を落とした本文に `:patterns` のどれかが当たる行が 1 行でもあれば file ごとに 1 件出す(critical)。判定は `:except`
//! の file(通常は 1 つ = 判定の 1 点)だけが持ってよく、他の file がこの語彙を読むと「第 2 の判定」が生えたことになる。
//! 註を落とすのは DOEFF150(:retired-words の :in lines — 註・docstring を数えない・agora-redesign #1794)と同じ向きの判断 —
//! この規則は「判定の二重化」を見るので、判定でないことを述べる註(例: 「routedTo はここで読まない」)まで赤にしない。
//! 判定は字面の行で読む(この語彙を使わずに済む形かは linter には分からない — repo の宣言が「これは判定の語彙だ」と決める)。
//! ただし欄を 1 対 1 で写すだけの行(`:routed-to d.routed-to`・`"routedTo" request.routed-to` — 判断も分岐も無い)は数えない
//! (agora-redesign #1800・#1762 の決定 Q2)。値をそのまま運ぶ行は判定を持たず、それを数えると判定の 1 点の外で欄を渡すだけの
//! 行まで「第 2 の判定」になる。

use std::path::{Path, PathBuf};

use once_cell::sync::Lazy;
use regex::Regex;

use super::architecture::VocabularyScope;
use super::{glob_matches, relative_path};

/// 判断・分岐の形(註と文字列を落とした行に当てる)— これを含む行は、写すだけの行ではない。
static JUDGMENT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\((?:if|when|unless|cond|match|case|and|or|not|is|is-not|in|not-in|=|!=|<|>|<=|>=)[\s)]|:if\b").unwrap()
});

/// 欄を写す対 1 つ = 鍵(keyword `:名` か文字列の鍵)と、その後ろの属性の読み(`名.欄` — 段は 1 つ以上)。元の行に当てる。
static COPY_PAIR: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?::[A-Za-z][\w-]*|"(?:[^"\\]|\\.)*")\s+[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+"#).unwrap()
});

/// 行の中の byte の位置 → 文字の番号(註と文字列を落とした写しは文字ごとに 1 文字を置くので、元の行と文字の番号がそろう —
/// byte の位置は多 byte の文字の所でずれる)。
fn char_index(line: &str, byte: usize) -> usize {
    line[..byte].chars().count()
}

/// 欄を 1 対 1 で写すだけの行か: 判断・分岐の形を含まず、語彙の当たりがどれも「鍵 + 属性の読み」の対の中にある(#1800)。
/// code = 註と文字列を落とした行(当たりはここで探す — 註と文字列の中は数えない)・source = 同じ行の元の字面(文字列の鍵を読む)。
fn copies_only(code: &str, source: &str, regexes: &[Regex]) -> bool {
    if JUDGMENT.is_match(code) {
        return false;
    }
    let pairs: Vec<(usize, usize)> =
        COPY_PAIR.find_iter(source).map(|m| (char_index(source, m.start()), char_index(source, m.end()))).collect();
    let mut hits = regexes.iter().flat_map(|re| re.find_iter(code)).peekable();
    hits.peek().is_some()
        && hits.all(|m| {
            let (start, end) = (char_index(code, m.start()), char_index(code, m.end()));
            pairs.iter().any(|&(from, to)| from <= start && end <= to)
        })
}

/// 文字列と註(`;` から行末)を空白に置き換える(括弧の対応と行・列の位置は保つ)。
fn strip_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_string = false;
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if in_string {
            out.push(if ch == '\n' { '\n' } else { ' ' });
            if ch == '\\' {
                if let Some(&next) = chars.peek() {
                    out.push(if next == '\n' { '\n' } else { ' ' });
                    chars.next();
                }
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => {
                in_string = true;
                out.push(' ');
            }
            ';' => {
                for c in chars.by_ref() {
                    if c == '\n' {
                        out.push('\n');
                        break;
                    }
                    out.push(' ');
                }
            }
            _ => out.push(ch),
        }
    }
    out
}

/// 見つけた当たり 1 つ(file × 群)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VocabularyHit {
    pub rel: String,
    /// 群の名(登録簿の鍵の細目)。
    pub group: String,
    /// 最初に当たった行(0 始まり)。
    pub line: u32,
    /// 当たった行の数。
    pub count: usize,
    pub instead: String,
}

/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// 歩く Hy の file(根からの path・path の順)— focus(命令の行の名指し)が在ればその下だけを歩き、無ければ根の全体を歩く。
/// 隠し dir と生成物の下の file は、どちらの歩き方でも数えない(名指しがその中の file でも、全体の実行と同じ答えにするため)。
fn walked_hy_files(root: &Path, focus: Option<&[PathBuf]>) -> Vec<String> {
    let starts: Vec<PathBuf> = match focus {
        Some(paths) => paths.to_vec(),
        None => vec![root.to_path_buf()],
    };
    let mut rels: Vec<String> = starts
        .iter()
        .flat_map(|start| {
            walkdir::WalkDir::new(start).follow_links(false).into_iter().filter_entry(|entry| {
                let name = entry.file_name().to_string_lossy();
                entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
            })
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| entry.path().extension().is_some_and(|e| e == "hy"))
        .filter_map(|entry| relative_path(root, entry.path()))
        .filter(|rel| {
            let dirs: Vec<&str> = rel.split('/').collect();
            !dirs[..dirs.len().saturating_sub(1)].iter().any(|d| d.starts_with('.') || SKIPPED_DIRS.contains(d))
        })
        .collect();
    rels.sort();
    rels.dedup();
    rels
}

/// `:single-point-vocabulary` の群ごとに `:except` の外の当たりを探す(群の宣言の順・path の順)。repo は 1 度だけ歩き(群ごとに
/// 歩き直さない)、focus が在ればその下の file だけを読む — 当たりは file ごとにその file に付くので、答えは全体を読んで名指しで
/// 絞った時と同じ(1 file の commit の hook で repo の全部の Hy を読んでいた・agora-redesign #1418)。
pub fn find(root: &Path, scopes: &[VocabularyScope], focus: Option<&[PathBuf]>) -> Vec<VocabularyHit> {
    let mut out = Vec::new();
    let walked = walked_hy_files(root, focus);
    for scope in scopes {
        let regexes: Vec<Regex> = scope.patterns.iter().filter_map(|p| Regex::new(p).ok()).collect();
        if regexes.is_empty() {
            continue;
        }
        let rels = walked
            .iter()
            .filter(|rel| scope.files.iter().any(|p| glob_matches(p, rel)))
            .filter(|rel| !scope.except.iter().any(|p| glob_matches(p, rel)))
            .cloned();
        for rel in rels {
            let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
            let code = strip_comments(&source);
            let mut first: Option<u32> = None;
            let mut count = 0usize;
            for (idx, (line, written)) in code.lines().zip(source.lines()).enumerate() {
                if regexes.iter().any(|re| re.is_match(line)) && !copies_only(line, written, &regexes) {
                    count += 1;
                    if first.is_none() {
                        first = Some(idx as u32);
                    }
                }
            }
            if let Some(line) = first {
                out.push(VocabularyHit { rel: rel.clone(), group: scope.name.clone(), line, count, instead: scope.instead.clone() });
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pattern_hit_is_counted_and_positioned_at_its_first_line() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/queue.hy"), "(setv a 1)\n(when (= phase JOB-PHASE-RUNNING) 1)\n(when (= phase JOB-PHASE-ENDED) 2)\n").unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        let hits = find(dir.path(), &scopes, None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "glue/queue.hy");
        assert_eq!(hits[0].group, "job-phase");
        assert_eq!(hits[0].line, 1);
        assert_eq!(hits[0].count, 2);
    }

    #[test]
    fn a_mention_inside_a_comment_or_string_is_not_counted() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/queue.hy"),
            "; JOB-PHASE-RUNNING はここで読まない\n(setv label \"JOB-PHASE-RUNNING\")\n(when (= phase JOB-PHASE-RUNNING) 1)\n",
        )
        .unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        let hits = find(dir.path(), &scopes, None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].line, 2, "註と文字列の中の当たりは数えない: {:?}", hits);
        assert_eq!(hits[0].count, 1);
    }

    fn routed_to_scope() -> Vec<VocabularyScope> {
        vec![VocabularyScope {
            name: "routed-to".to_string(),
            patterns: vec![r"\broutedTo\b".to_string(), r"\brouted-to\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }]
    }

    #[test]
    fn a_line_that_only_copies_the_field_is_not_counted() {
        // agora-redesign #1800: 欄を 1 対 1 で写すだけの行(keyword の鍵・文字列の鍵・日本語の註が前に在る行)は数えない。
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/rows.hy"),
            concat!(
                ";; 宛先の会話を写す(判定は slice.hy)\n",
                "(.append requests (HoldingRequest :id ask-id :routed-to mail.routed-to :routed-at mail.routed-at))\n",
                "{\"routedTo\" request.routed-to \"class\" request.request-class}\n",
                "(Row :note \"宛先\" :routed-to d.routed-to)\n",
            ),
        )
        .unwrap();
        assert!(find(dir.path(), &routed_to_scope(), None).is_empty());
    }

    #[test]
    fn a_line_that_judges_the_field_is_still_counted() {
        // 反例(#1800): 同じ欄でも、判断・分岐を持つ行・鍵の無い読み・欄を鍵にして別の値を置く行は写すだけではない — 1 行ずつ数える。
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/joins.hy"),
            concat!(
                ":routed-to (if (is d None) None d.routed-to)\n",
                "(when (= d.routed-to chat) (hold d))\n",
                "(lfor d rows :if (= d.routed-to chat) d)\n",
                "(setv target d.routed-to)\n",
                ":routed-to (pick-carrier d)\n",
            ),
        )
        .unwrap();
        let hits = find(dir.path(), &routed_to_scope(), None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!((hits[0].line, hits[0].count), (0, 5), "{:?}", hits);
    }

    #[test]
    fn non_hy_files_are_not_walked() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/queue.py"), "JOB_PHASE_RUNNING = 1\n# JOB-PHASE-RUNNING\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec![],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        assert!(find(dir.path(), &scopes, None).is_empty());
    }

    #[test]
    fn the_except_file_itself_is_never_a_hit() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        assert!(find(dir.path(), &scopes, None).is_empty());
    }

    #[test]
    fn named_paths_read_only_the_named_files_with_the_same_answer() {
        // agora-redesign #1418: 名指しの実行の当たりは、全体の実行の当たりを名指しで絞った物と同じ(名指しの外の b.hy は出ない)。
        // 隠し dir の下の file は名指しても数えない(全体の実行と同じ)。読めない file は全体でも黙って飛ばす(今までどおり)。
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::create_dir_all(dir.path().join("glue/.hidden")).unwrap();
        std::fs::write(dir.path().join("glue/a.hy"), "(when (= phase JOB-PHASE-RUNNING) 1)\n").unwrap();
        std::fs::write(dir.path().join("glue/b.hy"), "(when (= phase JOB-PHASE-ENDED) 2)\n").unwrap();
        std::fs::write(dir.path().join("glue/.hidden/c.hy"), "(when (= phase JOB-PHASE-ENDED) 3)\n").unwrap();
        std::fs::write(dir.path().join("glue/broken.hy"), [0xff_u8, 0xfe]).unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec![],
            instead: "答えを読む".to_string(),
        }];
        let whole = find(dir.path(), &scopes, None);
        assert_eq!(whole.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["glue/a.hy", "glue/b.hy"]);
        let named = [dir.path().join("glue/a.hy"), dir.path().join("glue/.hidden/c.hy")];
        let hits = find(dir.path(), &scopes, Some(&named));
        assert_eq!(hits, whole.into_iter().filter(|h| h.rel == "glue/a.hy").collect::<Vec<_>>());
    }
}
