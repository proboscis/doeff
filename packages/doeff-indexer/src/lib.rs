pub mod deps;
pub mod indexer;

pub use deps::{analyze_dependencies, FunctionDependency};
pub use indexer::{
    build_index, entry_matches, EntryCategory, Index, IndexEntry, ItemKind, ParameterKind,
    ProgramTypeKind, ProgramTypeUsage,
};
