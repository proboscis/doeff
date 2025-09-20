pub mod deps;
pub mod indexer;

pub use deps::{analyze_dependencies, FunctionDependency};
pub use indexer::{
    build_index, entry_matches, entry_matches_with_markers, EntryCategory, Index, IndexEntry, ItemKind, ParameterKind,
    ProgramTypeKind, ProgramTypeUsage,
};
