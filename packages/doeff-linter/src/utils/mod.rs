//! Utility functions for AST analysis

use rustpython_ast::{Expr, StmtClassDef};

/// Check if a class has the @dataclass decorator
pub fn has_dataclass_decorator(class_def: &StmtClassDef) -> bool {
    for decorator in &class_def.decorator_list {
        match decorator {
            Expr::Name(name) if name.id.as_str() == "dataclass" => return true,
            Expr::Call(call) => {
                if let Expr::Name(name) = &*call.func {
                    if name.id.as_str() == "dataclass" {
                        return true;
                    }
                }
            }
            Expr::Attribute(attr) => {
                if attr.attr.as_str() == "dataclass" {
                    if let Expr::Name(name) = &*attr.value {
                        if name.id.as_str() == "dataclasses" {
                            return true;
                        }
                    }
                }
            }
            _ => {}
        }
    }
    false
}

/// Check if a class name looks like it could be a dataclass (heuristic)
pub fn looks_like_dataclass_name(name: &str) -> bool {
    name.ends_with("State")
        || name.ends_with("Data")
        || name.ends_with("Config")
        || name.ends_with("Model")
        || name.ends_with("Params")
        || name.ends_with("Settings")
        || name.ends_with("Info")
        || name.ends_with("Record")
        || name.ends_with("Entry")
        || name.ends_with("Item")
        || name.ends_with("Details")
        || name.ends_with("Metadata")
        || name.contains("Dataclass")
        || name.contains("DataClass")
}

/// Python built-in names that should not be shadowed
pub const PYTHON_BUILTINS: &[&str] = &[
    // Types
    "dict", "list", "set", "tuple", "str", "int", "float", "bool", "bytes", "bytearray",
    "object", "type", "super", "property", "classmethod", "staticmethod", "frozenset",
    "complex", "slice", "range", "memoryview",
    // Functions
    "len", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "sum", "min", "max", "abs", "round", "pow", "divmod",
    "all", "any", "next", "iter", "callable", "isinstance", "issubclass",
    "getattr", "setattr", "delattr", "hasattr",
    "repr", "ascii", "bin", "hex", "oct", "chr", "ord",
    "format", "hash", "id", "vars", "dir", "help", "locals", "globals",
    // I/O
    "open", "print", "input",
    // Execution
    "compile", "exec", "eval", "__import__",
    // Exceptions
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "GeneratorExit",
    "FileNotFoundError", "PermissionError", "OSError", "IOError",
    // Constants
    "True", "False", "None", "Ellipsis", "NotImplemented",
    // Other
    "breakpoint", "copyright", "credits", "license", "quit", "exit",
];



