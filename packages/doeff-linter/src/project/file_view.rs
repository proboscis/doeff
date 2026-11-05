//! Reading of one file: combines headings and bindings (`signatures.rs`), the replacement display of calls in bodies (`call_view.rs`), and per-definition lines of body text
//! (`body_view.rs`) into one. The three readings are stacked from the bottom up (signatures <- call_view <- body_view), and only this module combines them
//! (split out of signatures.rs, where the heading reader called back into the replacement and body readers, forming a cycle).

use std::path::Path;

use serde::Serialize;

use super::body_view::{file_bodies, Body};
use super::call_view::{file_rewrites, Rewrite};
use super::signatures::{read_file, Binding, Signature, World};

/// The headings, bindings, and body reading of one file.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileSignatures {
    pub signatures: Vec<Signature>,
    pub bindings: Vec<Binding>,
    /// Replacement display that shows the calls in a definition's body in the form `f(a, b)` (`call_view.rs`).
    pub rewrites: Vec<Rewrite>,
    /// Per-definition lines of body text (`body_view.rs`; drawn by the reading surface).
    pub bodies: Vec<Body>,
}

/// Reads the headings, bindings, and bodies of one file (the table is built by `World::build`, using this file's same contents as an overlay).
pub fn file_signatures(world: &World, root: &Path, rel: &str, source: &str) -> FileSignatures {
    read_file(world, root, rel, source, |reader, forms, heads| {
        let rewrites = crate::timing::timed("signatures.rewrites", || file_rewrites(world, reader, forms));
        let bodies = crate::timing::timed("signatures.bodies", || file_bodies(world, reader, forms, &heads.bindings));
        FileSignatures { signatures: heads.signatures, bindings: heads.bindings, rewrites, bodies }
    })
}
