//! 定義の範囲の道具。規則の module(assembly_shape.rs など)が組み立ての project/mod.rs を読み戻さずに使えるよう、自分の module に
//! 置く(読み戻すと依存の輪になる — agora-redesign #2121)。

use doeff_indexer::hy_index::Definition;

use crate::position::Range;

/// 位置 spot を含む、いちばん内側の定義の添字(無ければ None)— 参照や呼び出しを、それを書いた定義へ帰すため。
pub fn innermost_definition(definitions: &[Definition], spot: &Range) -> Option<usize> {
    definitions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.full_range.start <= spot.start && spot.end <= d.full_range.end)
        .max_by_key(|(_, d)| d.full_range.start)
        .map(|(index, _)| index)
}
