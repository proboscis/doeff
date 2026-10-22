//! file 1 つの「どの名を綴っているか」の覚え — git の blob の id を鍵に、中身を読まずに引く(card ki-d56ba76422ba)。
//!
//! 1 file の実行は、呼び先の名を字面に綴る file だけを選ぶために、repo の Hy を全部読む(`spelled_callers`)。`Spelling::in_source` が見るのは
//! 「記号の最後の段」だけ: 空白と `( ) [ ] { } " ;` で区切った語の、最後の `.` より後ろ。その語(`-` を `_` に畳む)の集合を、file ごとに Bloom filter
//! で覚えておけば、名の綴りを含まない file は、中身を読まずに「綴らない」と答えられる(Bloom filter は「在る」を誤って言うだけで、「無い」を誤って
//! 言わない — 誤った「在る」は file を読み直すだけで答えは変わらない)。

use serde::{Deserialize, Serialize};

use super::spelling::Spelling;

/// 記号の終わり(`spelling::SYMBOL_END` の字)。
fn ends_symbol(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\x0B' | '\x0C' | '\r' | '(' | ')' | '[' | ']' | '{' | '}' | '"' | ';')
}

/// 語の hash(FNV-1a — build をまたいで同じ値。`-` は `_` に畳む: 名の綴りは `_` を `-` とも綴れる)。
fn hash_of(token: &str) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in token.bytes() {
        hash ^= u64::from(if byte == b'-' { b'_' } else { byte });
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

const HASHES: u64 = 3;

/// bit の位置 `HASHES` 個(二重の hash — 1 つの hash の上下を使う)。`bits` は 2 の冪。
fn positions(hash: u64, bits: usize) -> impl Iterator<Item = usize> {
    let first = hash & 0xffff_ffff;
    let step = (hash >> 32) | 1;
    (0..HASHES).map(move |i| (first.wrapping_add(i.wrapping_mul(step)) as usize) & (bits - 1))
}

/// file 1 つが綴る名の覚えと、`(defk` の綴りを含むか。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlobSpell {
    pub defk: bool,
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
        let bits = (hashes.len() * 8).next_power_of_two().max(256);
        let mut bloom = vec![0u8; bits / 8];
        for hash in hashes {
            for at in positions(hash, bits) {
                bloom[at / 8] |= 1 << (at % 8);
            }
        }
        BlobSpell { defk: source.contains("(defk"), bloom }
    }

    /// `spelling` を綴るかもしれないか。false なら必ず綴らない(`Spelling::in_source` が false)。
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

    /// 覚えが「綴らない」と答える名は、`Spelling::in_source` も false(誤って「無い」と言わない)。いろいろな綴り方の file と名で全部の組を見る。
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
                    assert!(!spelling.in_source(source), "{name:?} を {source:?} が綴るのに、覚えは綴らないと言った");
                }
                if spelling.in_source(source) {
                    assert!(memory.may_spell(&spelling), "{name:?} を綴る {source:?} を、覚えが取りこぼした");
                }
            }
        }
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
