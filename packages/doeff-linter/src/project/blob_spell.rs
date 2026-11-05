//! A memory of "which names a file spells", for one file — looked up by the git blob id as key, without reading the contents.
//!
//! A single-file run reads all of the repo's Hy (`spelled_callers`) to pick only the files that spell the callee's name literally. What `Spelling::in_source` looks at is
//! only "the last segment of a symbol": for words delimited by whitespace and `( ) [ ] { } " ;`, the part after the last `.`. If the set of those words (with `-` folded into `_`) is remembered per file in a Bloom filter,
//! a file that does not contain the spelling of a name can be answered "does not spell it" without reading its contents (a Bloom filter only wrongly says "present", never wrongly
//! says "absent" — a wrong "present" just re-reads the file, and the answer does not change).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde::{Deserialize, Serialize};

use super::blob_chunks::{ChunkedFacts, Lookup};
use super::smells::has_failure_declaration_spelling;
use super::spelling::Spelling;
use super::{hy_files, paths};

/// Name of the kind of memory store.
const KIND: &str = "blob-spell";

/// The result of looking at one Hy file of the repo.
pub enum Seen {
    /// Settled by the memory (contents not read).
    Spell(BlobSpell),
    /// Contents were read (no memory, a file out of step with the index, or a file the memory matched against `wants_source`).
    Source(String),
    /// Could not be read.
    Unreadable,
}

/// While walking all of the repo's Hy, looks up each file's spelling memory by git blob id. A file that has a memory and
/// for which `wants_source(memory)` is false is not read. A file with no memory, a file out of step with the index, or a file for which `wants_source` is true is read (for a blob with no memory,
/// a memory is made from the contents read and stored). Memories are looked up one file per chunk of the index order (`ChunkedFacts` — so a cold worktree does not open 4600 store files).
/// Emits to the instrument the time of stage `label` and the number of files whose contents were read (`<label> read=N`). The order is the same as `hy_files::collect_reading`.
pub fn scan(root: &Path, label: &str, wants_source: impl Fn(&BlobSpell) -> bool + Sync) -> Vec<(String, PathBuf, Seen)> {
    let memory = ChunkedFacts::<BlobSpell>::open(root, KIND, |rel| hy_files::walks_to(&hy_files::HY_WALK, rel));
    let read = AtomicUsize::new(0);
    let seen = crate::timing::timed(label, || hy_files::collect_reading(root, |path| see(root, memory.as_ref(), &wants_source, path, &read)));
    if let Some(memory) = &memory {
        let (chunks, missed) = memory.reads();
        crate::timing::count(label, "chunks", chunks);
        crate::timing::count(label, "missed", missed);
        crate::timing::timed(&format!("{label}.remember"), || memory.finish());
    }
    crate::timing::count(label, "read", read.load(Ordering::Relaxed));
    seen
}

/// Looks at one file (one item of `scan`).
fn see(root: &Path, memory: Option<&ChunkedFacts<BlobSpell>>, wants_source: &(impl Fn(&BlobSpell) -> bool + Sync), path: &Path, read: &AtomicUsize) -> Seen {
    let known = memory.and_then(|memory| {
        let rel = paths::relative_path(root, path)?;
        let (found, meta) = memory.lookup_path(&rel, path)?;
        Some((memory, found, rel, meta))
    });
    let unknown = match known {
        Some((_, Lookup::Known(spell), _, _)) if !wants_source(&spell) => return Seen::Spell(spell),
        Some((memory, Lookup::Unknown(oid), rel, meta)) => Some((memory, oid, rel, meta)),
        _ => None,
    };
    read.fetch_add(1, Ordering::Relaxed);
    let Ok(source) = std::fs::read_to_string(path) else { return Seen::Unreadable };
    if let Some((memory, oid, rel, meta)) = unknown {
        memory.learn_read(&rel, oid, path, meta.as_ref(), source.as_bytes(), BlobSpell::of(&source));
    }
    Seen::Source(source)
}

/// Symbol-ending characters (the characters of `spelling::SYMBOL_END`).
fn ends_symbol(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\x0B' | '\x0C' | '\r' | '(' | ')' | '[' | ']' | '{' | '}' | '"' | ';')
}

/// Hash of a word (FNV-1a — the same value across builds. `-` is folded into `_`: a name's spelling may write `_` as `-` too).
fn hash_of(token: &str) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in token.bytes() {
        hash ^= u64::from(if byte == b'-' { b'_' } else { byte });
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

/// The number of bit positions, and the number of bits per word (rounded up to a power of 2). For a file that looks for 12 callee names (requests_projection), the previous 3 hashes / 8 bits
/// gave a wrong "present" of about 1% per name, making about 500 of the repo's 4620 Hy files be read. With 8 hashes / 16 bits it is
/// about 0.05% or less per name (the table is about 2x larger).
const HASHES: u64 = 8;
const BITS_PER_WORD: usize = 16;

/// `HASHES` bit positions (double hashing — uses the upper and lower halves of one hash). `bits` is a power of 2.
fn positions(hash: u64, bits: usize) -> impl Iterator<Item = usize> {
    let first = hash & 0xffff_ffff;
    let step = (hash >> 32) | 1;
    (0..HASHES).map(move |i| (first.wrapping_add(i.wrapping_mul(step)) as usize) & (bits - 1))
}

/// The memory of the names one file spells, whether it contains the spelling of `(defk`, whether it contains the spelling of a failure-type declaration (`smells::has_failure_declaration_spelling`), and whether it contains `(defeffect`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlobSpell {
    pub defk: bool,
    pub failure: bool,
    pub defeffect: bool,
    bloom: Vec<u8>,
}

impl BlobSpell {
    pub fn of(source: &str) -> BlobSpell {
        let mut hashes: Vec<u64> = source
            .split(ends_symbol)
            .filter_map(|word| word.rsplit('.').next())
            .filter(|last| !last.is_empty())
            .map(hash_of)
            .collect();
        hashes.sort_unstable();
        hashes.dedup();
        let bits = (hashes.len() * BITS_PER_WORD).next_power_of_two().max(256);
        let mut bloom = vec![0u8; bits / 8];
        for hash in hashes {
            for at in positions(hash, bits) {
                bloom[at / 8] |= 1 << (at % 8);
            }
        }
        BlobSpell { defk: source.contains("(defk"), failure: has_failure_declaration_spelling(source), defeffect: source.contains("(defeffect"), bloom }
    }

    /// Whether it might spell `spelling`. If false it definitely does not spell it (`Spelling::in_source` is false).
    pub fn may_spell(&self, spelling: &Spelling) -> bool {
        let Some(name) = spelling.folded_name() else { return true };
        let bits = self.bloom.len() * 8;
        if bits == 0 || !bits.is_power_of_two() {
            return true;
        }
        positions(hash_of(name), bits).all(|at| self.bloom[at / 8] & (1 << (at % 8)) != 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A name the memory answers "does not spell" is also false for `Spelling::in_source` (never wrongly says "absent"). Checks every pair over files and names with various ways of spelling.
    #[test]
    fn a_no_from_the_memory_is_always_a_no_from_the_source() {
        let sources = [
            "(rows-by-text x)",
            "(app.hof.rows_by_text x)",
            "(import app.core.rows-by-text.system)",
            "(rows-by-text-all x) [rows_by_text] {:f rows-by-text}",
            "\"rows_by_text\" ;rows-by-text\n(a)\n(rows-by-text\n x)",
            "xrows-by-text rows-by-text ",
            "(_private-fn 1) (__dunder__) (a.b.c)",
            "",
            "(defk g [x] (! (hof.field-of x)))",
        ];
        let names = ["rows_by_text", "rows_by_text_all", "_private_fn", "__dunder__", "c", "b", "a", "field_of", "g", "nothing_here", "hyx_checkXquestion_markX", "x"];
        for source in sources {
            let memory = BlobSpell::of(source);
            for name in names {
                let spelling = Spelling::of(name);
                if !memory.may_spell(&spelling) {
                    assert!(!spelling.in_source(source), "{name:?} is spelled by {source:?}, but the memory said it does not spell it");
                }
                if spelling.in_source(source) {
                    assert!(memory.may_spell(&spelling), "the memory missed {source:?}, which spells {name:?}");
                }
            }
        }
    }

    /// The rate of wrongly saying "might spell" for a name that is not spelled is small — a wrong "present" only makes a file be read, but for a file that looks for many names the number read
    /// grows with the number of names (for a file with 1000 words and 10 thousand unspelled names, errors are 0.1% or less).
    #[test]
    fn few_names_the_file_does_not_spell_are_taken_as_maybe_spelled() {
        let source: String = (0..1000).map(|i| format!("(word-{i} x)\n")).collect();
        let memory = BlobSpell::of(&source);
        let wrong = (0..10_000).filter(|j| memory.may_spell(&Spelling::of(&format!("other_{j}")))).count();
        assert!(wrong <= 10, "of 10 thousand unspelled names, {wrong} were wrongly judged as possibly spelled");
        assert!((0..1000).all(|i| memory.may_spell(&Spelling::of(&format!("word_{i}")))), "spelled names are not missed");
    }

    #[test]
    fn a_file_that_does_not_spell_the_name_is_ruled_out() {
        let memory = BlobSpell::of("(defn other [x] (helper x))\n(defk g [x] x)\n");
        assert!(!memory.may_spell(&Spelling::of("rows_by_text")));
        assert!(memory.may_spell(&Spelling::of("helper")));
        assert!(memory.defk);
        assert!(!BlobSpell::of("(defn a [])").defk);
    }
}
