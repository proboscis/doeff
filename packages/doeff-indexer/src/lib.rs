pub mod deps;
pub mod indexer;

pub use deps::{analyze_dependencies, FunctionDependency};
pub use indexer::{
    build_index, entry_matches, entry_matches_with_markers, find_interceptors, find_interpreters,
    find_kleisli, find_kleisli_with_type, find_transforms, EntryCategory, Index, IndexEntry,
    ItemKind, ParameterKind, ProgramTypeKind, ProgramTypeUsage,
};

#[cfg(test)]
mod test_markers;
#[cfg(test)]
mod test_module_path;
