//! Unified traceback capture and assembly types.

use crate::ids::DispatchId;

/// Unique identifier for a program frame instance.
pub type FrameId = u64;

/// Handler implementation kind for trace output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HandlerKind {
    Python,
    RustBuiltin,
}

/// Final action produced by a handler for a dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HandlerAction {
    Resumed { value_repr: Option<String> },
    Transferred { value_repr: Option<String> },
    Returned { value_repr: Option<String> },
    Threw { exception_repr: Option<String> },
}

/// Low-level control-flow event appended by the VM.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CaptureEvent {
    FrameEntered {
        frame_id: FrameId,
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
    },
    FrameExited {
        function_name: String,
    },
    DispatchStarted {
        dispatch_id: DispatchId,
        effect_repr: String,
        handler_name: String,
        handler_kind: HandlerKind,
        handler_source_file: Option<String>,
        handler_source_line: Option<u32>,
    },
    Delegated {
        dispatch_id: DispatchId,
        from_handler_name: String,
        to_handler_name: String,
        to_handler_kind: HandlerKind,
        to_handler_source_file: Option<String>,
        to_handler_source_line: Option<u32>,
    },
    HandlerCompleted {
        dispatch_id: DispatchId,
        handler_name: String,
        action: HandlerAction,
    },
    Resumed {
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
    },
    Transferred {
        dispatch_id: DispatchId,
        handler_name: String,
        value_repr: Option<String>,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
    },
}

/// Delegation hop for a dispatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DelegationEntry {
    pub handler_name: String,
    pub handler_kind: HandlerKind,
    pub handler_source_file: Option<String>,
    pub handler_source_line: Option<u32>,
}

/// Dispatch completion status.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DispatchAction {
    Active,
    Resumed,
    Transferred,
    Returned,
    Threw,
}

/// Assembled VM-level trace entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TraceEntry {
    Frame {
        frame_id: FrameId,
        function_name: String,
        source_file: String,
        source_line: u32,
        args_repr: Option<String>,
    },
    Dispatch {
        dispatch_id: DispatchId,
        effect_repr: String,
        handler_name: String,
        handler_kind: HandlerKind,
        handler_source_file: Option<String>,
        handler_source_line: Option<u32>,
        delegation_chain: Vec<DelegationEntry>,
        action: DispatchAction,
        value_repr: Option<String>,
        exception_repr: Option<String>,
    },
    ResumePoint {
        dispatch_id: DispatchId,
        handler_name: String,
        resumed_function_name: String,
        source_file: String,
        source_line: u32,
        value_repr: Option<String>,
    },
}
