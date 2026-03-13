use std::collections::HashMap;

use crate::dispatch::DispatchContext;
use crate::ids::DispatchId;

pub struct DispatchState {
    dispatch_stack: Vec<DispatchContext>,
    dispatch_index: HashMap<DispatchId, usize>,
}

impl DispatchState {
    fn bad_parent_chain_completion(ctx: &DispatchContext) -> bool {
        let mut cursor = Some(ctx.k_current.clone());
        while let Some(current) = cursor {
            if current.parent.is_none() {
                return true;
            }
            cursor = current.parent.as_ref().map(|parent| (**parent).clone());
        }
        false
    }
}
