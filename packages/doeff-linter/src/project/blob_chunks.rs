//! git の index の blob の id で引く file ごとの事実を、index の並びを区切った塊ごとに 1 つの表として置き・引く(card ki-40c8ca380713)。
//!
//! 1 file の実行が repo の file の全部を見る所(引数に渡された Program の呼び手・失敗の型の宣言・class の frozen)は、変わっていない file の事実を
//! blob の id で引く(card ki-d56ba76422ba — 中身を読まない)。事実を 1 blob 1 file で置くと、冷えた新しい作業木では引くだけで置き場の file を
//! repo の Hy の数だけ(約 4600)開き、1 つごとに disk を待つ(置き場の file も、作業木の file と同じに数分で page cache から追い出される)。
//! そこで index の行(path の byte の順)を塊に分け、塊ごとに(blob の id・事実)の表を 1 file で置く(agora-controllers の Hy で約 70 file)。
//!
//! 塊の切れ目は path だけで決まり(`starts_chunk` — path の hash が `CHUNK_EVERY` で割り切れる行で切る・`CHUNK_MOST` 行で必ず切る)、塊の置き場の名は
//! 塊の頭の path(`slot`)。file を変えても塊の名は変わらず、表に新しい blob の行が足されるだけなので、別の commit から作った作業木どうしも同じ
//! 置き場を引き、変わっていない file の行はそのまま当たる。表は今の版の行と、近い過去の版の行を持つ(`SLOT_KEEPS` — 古い commit の作業木も当たる)。
//!
//! 表に無い blob は、呼び手が中身を読んで作った事実を覚え(`learn_read`)、実行の終わりに(`finish`)置き場の今の表と合わせて置き直す。並ぶ実行が
//! 同じ表を同時に置き直すと後の方が勝ち、先の方が足した行は落ちる(次に引いた実行が読み直して足すだけ)。どの行も blob の中身だけで決まる同じ
//! 事実なので(呼び手が保つ — 事実は中身だけで決め、path も根も読まない)、どの版の表から引いても答えは中身を読んだ時と同じ。

use std::collections::HashMap;
use std::fs::Metadata;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use serde::de::DeserializeOwned;
use serde::Serialize;

use super::facts_cache::BlobFacts;
use super::git_index::{self, GitIndex};

/// 塊の大きさの目安(行の数)— path の hash がこれで割り切れる行で切る。塊の数は file の数 / これ。
const CHUNK_EVERY: u64 = 64;
/// 塊の行の数の上限 — hash の切れ目が続けて来ない時も、ここで切る(読む塊が大きくなりすぎない)。
const CHUNK_MOST: usize = 256;
/// 塊の表が持つ行の数の上限(塊の今の行の数の倍数 — 今の版の行と、それと同じ数の過去の版の行)。下限は `SLOT_KEEPS_AT_LEAST`。
const SLOT_KEEPS: usize = 3;
const SLOT_KEEPS_AT_LEAST: usize = 64;

/// 行 `path` で新しい塊を始めるか(path だけで決まる — FNV-1a・実行と build をまたいで同じ答え)。
fn starts_chunk(path: &str) -> bool {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in path.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash % CHUNK_EVERY == 0
}

/// (path・blob の id)の行の列(index の並び)を塊に分ける。
fn chunks_of<'a>(files: impl Iterator<Item = (&'a str, [u8; 20])>) -> Vec<Vec<(String, [u8; 20])>> {
    let mut chunks: Vec<Vec<(String, [u8; 20])>> = Vec::new();
    for (path, oid) in files {
        let fresh = match chunks.last() {
            None => true,
            Some(last) => starts_chunk(path) || last.len() >= CHUNK_MOST,
        };
        if fresh {
            chunks.push(Vec::new());
        }
        if let Some(last) = chunks.last_mut() {
            last.push((path.to_string(), oid));
        }
    }
    chunks
}

/// 塊の表を置き直す時の行: 今の実行で足す行・今の塊の行・それ以外(過去の版)の順に、`keep` 行まで(同じ blob は 1 行)。
fn merged<T: Clone>(added: &[([u8; 20], T)], stored: Vec<([u8; 20], T)>, members: &[(String, [u8; 20])], keep: usize) -> Vec<([u8; 20], T)> {
    let current: std::collections::HashSet<[u8; 20]> = members.iter().map(|(_, oid)| *oid).collect();
    let (now, past): (Vec<([u8; 20], T)>, Vec<([u8; 20], T)>) = stored.into_iter().partition(|(oid, _)| current.contains(oid));
    let mut seen = std::collections::HashSet::new();
    added.iter().cloned().chain(now).chain(past).filter(|(oid, _)| seen.insert(*oid)).take(keep).collect()
}

/// file 1 つの引いた結果。
pub enum Lookup<T> {
    /// 事実を引けた(中身を読まない)。
    Known(T),
    /// 中身は index の blob と同じだが、その blob の事実がまだ無い — 呼び手が中身を読んで作り、`learn_read` で覚えさせる。
    Unknown([u8; 20]),
    /// index と外れた file(書き替えた・index に無い・index と同じ刻に書かれた racy の行)— 呼び手が中身を読んで作る(覚えない)。
    Untrusted,
}

/// 種類 1 つの、塊ごとに置く blob の事実(1 回の実行の間だけ持つ)。
pub struct ChunkedFacts<T> {
    facts: BlobFacts,
    index: Arc<GitIndex>,
    chunks: Vec<Vec<(String, [u8; 20])>>,
    /// path → 塊。
    at: HashMap<String, usize>,
    /// 塊ごとの、置き場から読んだ表(初めて引かれた時に読む — 置き場に無ければ空)。
    loaded: Vec<OnceLock<HashMap<[u8; 20], T>>>,
    /// この実行で中身から知った事実(塊 → その塊の file の(blob の id・事実))— 終わりに表へ足す。
    learned: Mutex<HashMap<usize, Vec<([u8; 20], T)>>>,
    /// 置き場から読んだ塊の数と、表に無かった blob の数(計器と検のため)。
    chunk_reads: AtomicUsize,
    missed: AtomicUsize,
}

impl<T: Clone + Serialize + DeserializeOwned> ChunkedFacts<T> {
    /// `root` の index の通常の file のうち `select`(根からの path)が選ぶ物を塊に分けて、種類 `kind` の置き場を開く。index が読めない・
    /// cache を使わない時は None(呼び手は全部の file の中身を読む)。`select` は、呼び手が歩いて見る file と同じ集まりを選ぶこと(選ばない file は
    /// 塊に入らず、`lookup` が表に無いと答えて呼び手が毎回読むだけ — 答えは変わらない)。
    pub fn open(root: &Path, kind: &str, select: impl Fn(&str) -> bool) -> Option<Self> {
        let facts = BlobFacts::open(root, kind)?;
        let index = GitIndex::of(root)?;
        let chunks = chunks_of(index.files().filter(|(path, _)| select(path)));
        let at = chunks.iter().enumerate().flat_map(|(c, members)| members.iter().map(move |(path, _)| (path.clone(), c))).collect();
        let loaded = chunks.iter().map(|_| OnceLock::new()).collect();
        Some(ChunkedFacts {
            facts,
            index,
            chunks,
            at,
            loaded,
            learned: Mutex::new(HashMap::new()),
            chunk_reads: AtomicUsize::new(0),
            missed: AtomicUsize::new(0),
        })
    }

    /// 塊 `c` の置き場の名(塊の頭の path)。
    fn slot(&self, c: usize) -> &str {
        self.chunks[c].first().map_or("", |(path, _)| path.as_str())
    }

    /// file 1 つ(根からの path `rel`・今の stat `meta`)の事実を引く。
    pub fn lookup(&self, rel: &str, meta: &Metadata) -> Lookup<T> {
        let Some(oid) = self.index.blob_of(rel, meta) else { return Lookup::Untrusted };
        let known = self.at.get(rel).and_then(|&c| {
            let table = self.loaded[c].get_or_init(|| {
                self.chunk_reads.fetch_add(1, Ordering::Relaxed);
                self.facts.get_slot::<T>(self.slot(c)).map(|rows| rows.into_iter().collect()).unwrap_or_default()
            });
            table.get(&oid).cloned()
        });
        match known {
            Some(value) => Lookup::Known(value),
            None => {
                self.missed.fetch_add(1, Ordering::Relaxed);
                Lookup::Unknown(oid)
            }
        }
    }

    /// 置き場から読んだ塊の数と、表に無かった blob の数。
    pub fn reads(&self) -> (usize, usize) {
        (self.chunk_reads.load(Ordering::Relaxed), self.missed.load(Ordering::Relaxed))
    }

    /// `lookup` が `Unknown(oid)` と答えた file(根からの path `rel`・`path`・引いた時の stat `meta`)の中身 `source` から作った事実を、読む間に file が
    /// 変わっていない事と、中身が index の blob と同じ事(clean の filter や改行の変換が在る作業木では違う)を確かめてから覚える。確かめられなければ
    /// 覚えない(答えは変わらない)。
    pub fn learn_read(&self, rel: &str, oid: [u8; 20], path: &Path, meta: &Metadata, source: &[u8], value: T) {
        let Some(&c) = self.at.get(rel) else { return };
        let unchanged = std::fs::metadata(path).is_ok_and(|now| now.len() == meta.len() && now.modified().ok() == meta.modified().ok());
        if unchanged && git_index::blob_id(source) == oid {
            self.learned.lock().unwrap_or_else(PoisonError::into_inner).entry(c).or_default().push((oid, value));
        }
    }

    /// この実行で覚えた事実を、塊ごとに置き場の今の表(並ぶ実行が足した行を含む — 読み直す)と合わせて置き直す。
    pub fn finish(&self) {
        let learned = std::mem::take(&mut *self.learned.lock().unwrap_or_else(PoisonError::into_inner));
        for (c, added) in learned {
            let members = &self.chunks[c];
            let stored = self.facts.get_slot::<T>(self.slot(c)).unwrap_or_default();
            let keep = (members.len() * SLOT_KEEPS).max(SLOT_KEEPS_AT_LEAST);
            self.facts.put_slot(self.slot(c), &merged(&added, stored, members, keep));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn oid(n: u8) -> [u8; 20] {
        [n; 20]
    }

    /// 塊の切れ目は path だけで決まる: 行を 1 つ足しても消しても、その行の塊の外の塊は、中身(path と id)がそのまま残る — 別の commit の作業木と分け合える。
    #[test]
    fn adding_or_removing_a_file_changes_only_its_own_chunk() {
        let paths: Vec<String> = (0..2000).map(|n| format!("app/m{n:04}.hy")).collect();
        let base = chunks_of(paths.iter().map(|p| (p.as_str(), oid(1))));
        assert!(base.len() >= 10, "検の材料が足りない(塊 {})", base.len());
        assert!(base.iter().all(|members| members.len() <= CHUNK_MOST));
        let added: Vec<String> = paths.iter().cloned().chain(["app/m1000a.hy".to_string()]).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let with = chunks_of(added.iter().map(|p| (p.as_str(), oid(1))));
        let unchanged = with.iter().filter(|members| base.contains(members)).count();
        assert!(unchanged + 1 >= base.len(), "1 行足して {} の塊のうち {} しか残らない", base.len(), unchanged);
        let removed: Vec<&String> = paths.iter().filter(|p| p.as_str() != "app/m0500.hy").collect();
        let without = chunks_of(removed.iter().map(|p| (p.as_str(), oid(1))));
        assert!(without.iter().filter(|members| base.contains(members)).count() + 1 >= base.len());
    }

    /// 表を置き直す時は、足す行・今の塊の行・過去の版の行の順に残し、上限で古い版から落とす(同じ blob は 1 行)。
    #[test]
    fn a_rewritten_table_keeps_the_added_and_current_rows_before_past_versions() {
        let members = vec![("a.hy".to_string(), oid(1)), ("b.hy".to_string(), oid(2))];
        let stored = vec![(oid(9), "past-9"), (oid(1), "now-1"), (oid(8), "past-8"), (oid(2), "now-2")];
        let added = [(oid(3), "added-3"), (oid(1), "now-1")];
        assert_eq!(merged(&added, stored.clone(), &members, 10), vec![(oid(3), "added-3"), (oid(1), "now-1"), (oid(2), "now-2"), (oid(9), "past-9"), (oid(8), "past-8")]);
        assert_eq!(merged(&added, stored, &members, 4), vec![(oid(3), "added-3"), (oid(1), "now-1"), (oid(2), "now-2"), (oid(9), "past-9")]);
    }

    /// 同じ行の列は同じ塊に分かれる(順も同じ)— 全部の行がどれか 1 つの塊に 1 度だけ入る。
    #[test]
    fn every_file_is_in_exactly_one_chunk_in_index_order() {
        let paths: Vec<String> = (0..700).map(|n| format!("d{}/f{n}.hy", n % 13)).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let chunks = chunks_of(paths.iter().map(|p| (p.as_str(), oid(2))));
        let flat: Vec<&str> = chunks.iter().flatten().map(|(p, _)| p.as_str()).collect();
        assert_eq!(flat, paths.iter().map(String::as_str).collect::<Vec<_>>());
        assert_eq!(chunks, chunks_of(paths.iter().map(|p| (p.as_str(), oid(2)))));
        assert!(chunks_of(std::iter::empty()).is_empty());
    }
}
