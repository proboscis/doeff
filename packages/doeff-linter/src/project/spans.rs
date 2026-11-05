//! Helpers for definition ranges. Kept in their own module so rule modules (assembly_shape.rs etc.) can use them without reading back
//! from the assembling project/mod.rs (reading back would create a dependency cycle).

use doeff_indexer::hy_index::Definition;

use crate::position::Range;

/// Index of the innermost definition containing position spot (None if none) — to attribute a reference or call to the definition that wrote it.
pub fn innermost_definition(definitions: &[Definition], spot: &Range) -> Option<usize> {
    definitions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.full_range.start <= spot.start && spot.end <= d.full_range.end)
        .max_by_key(|(_, d)| d.full_range.start)
        .map(|(index, _)| index)
}
