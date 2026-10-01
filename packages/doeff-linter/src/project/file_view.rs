//! 1 file の読み — 見出しと束縛(`signatures.rs`)・本体の呼びの表示の置き換え(`call_view.rs`)・定義ごとの本体の文字の行
//! (`body_view.rs`)を 1 つに組む。3 つの読みは下から順に並び(signatures ← call_view ← body_view)、組むのはこの module だけ
//! (agora-redesign #2125 で signatures.rs から分けた — 見出しの読みが置き換えと本体の読みを呼び返して輪になっていた)。

use std::path::Path;

use serde::Serialize;

use super::body_view::{file_bodies, Body};
use super::call_view::{file_rewrites, Rewrite};
use super::signatures::{read_file, Binding, Signature, World};

/// 1 file の見出しと束縛と、本体の読み。
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileSignatures {
    pub signatures: Vec<Signature>,
    pub bindings: Vec<Binding>,
    /// 定義の本体の呼びを `f(a, b)` の形で見せる表示の置き換え(`call_view.rs`)。
    pub rewrites: Vec<Rewrite>,
    /// 定義ごとの本体の文字の行(`body_view.rs` — 読む面が描く)。
    pub bodies: Vec<Body>,
}

/// 1 file の見出しと束縛と本体を読む(表は `World::build` で、この file の同じ中身を overlay にして作った物)。
pub fn file_signatures(world: &World, root: &Path, rel: &str, source: &str) -> FileSignatures {
    read_file(world, root, rel, source, |reader, forms, heads| {
        let rewrites = crate::timing::timed("signatures.rewrites", || file_rewrites(world, reader, forms));
        let bodies = crate::timing::timed("signatures.bodies", || file_bodies(world, reader, forms, &heads.bindings));
        FileSignatures { signatures: heads.signatures, bindings: heads.bindings, rewrites, bodies }
    })
}
