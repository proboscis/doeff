use anyhow::{anyhow, Result};
use tree_sitter::{Parser, Tree};

pub fn parse_module(source: &str) -> Result<Tree> {
    let mut parser = Parser::new();
    parser
        .set_language(tree_sitter_python::language())
        .map_err(|err| anyhow!("failed to load tree-sitter-python: {err}"))?;

    parser
        .parse(source, None)
        .ok_or_else(|| anyhow!("tree-sitter failed to parse module"))
}
