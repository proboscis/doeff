pub mod indexer;

use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum EntryCategory {
    ProgramInterpreter,
    ProgramTransformer,
    KleisliProgram,
    Interceptor,
    DoFunction,
    AcceptsProgramParam,
    AcceptsEffectParam,
    ReturnsProgram,
    ReturnsKleisliProgram,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Parameter {
    pub name: String,
    pub annotation: Option<String>,
    pub is_required: bool,
    pub default: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexEntry {
    pub name: String,
    pub file_path: String,
    pub line: usize,
    pub module_path: String,
    pub categories: HashSet<EntryCategory>,
    pub markers: Vec<String>,
    pub decorators: Vec<String>,
    pub all_parameters: Vec<Parameter>,
    pub return_annotation: Option<String>,
    pub doc_string: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexOutput {
    pub entries: Vec<IndexEntry>,
    pub total_files: usize,
    pub total_functions: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ProgramTypeKind {
    Program,
    KleisliProgram,
    Effect,
    Other,
}

pub fn detect_program_type(type_str: &str) -> Option<ProgramTypeKind> {
    if type_str.contains("Program[") || type_str == "Program" {
        Some(ProgramTypeKind::Program)
    } else if type_str.contains("KleisliProgram[") || type_str == "KleisliProgram" {
        Some(ProgramTypeKind::KleisliProgram)
    } else if type_str.contains("Effect") {
        Some(ProgramTypeKind::Effect)
    } else if type_str.contains("->") && type_str.contains("Program") {
        Some(ProgramTypeKind::KleisliProgram)
    } else {
        None
    }
}

pub use indexer::{
    build_index, find_interceptors, find_interceptors_with_type, find_interpreters,
    find_kleisli, find_kleisli_with_type, find_transforms,
};