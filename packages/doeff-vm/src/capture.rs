//! Unified traceback capture and assembly types.

use pyo3::prelude::*;

use crate::ids::DispatchId;

/// Unique identifier for a program frame instance.
pub type FrameId = u64;

/// Handler implementation kind for trace output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HandlerKind {
    Python,
    RustBuiltin,
}

/// Per-handler status marker for active-chain rendering.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandlerStatus {
    Active,
    Pending,
    Delegated,
    Resumed,
    Transferred,
    Returned,
    Threw,
}

/// Snapshot row for a handler in the chain at dispatch start.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandlerSnapshotEntry {
    pub handler_name: String,
    pub handler_kind: HandlerKind,
    pub source_file: Option<String>,
    pub source_line: Option<u32>,
}

/// Handler row emitted in active-chain effect entries with status markers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandlerDispatchEntry {
    pub handler_name: String,
    pub handler_kind: HandlerKind,
    pub source_file: Option<String>,
    pub source_line: Option<u32>,
    pub status: HandlerStatus,
}

/// Spawn site metadata used for spawned-task traceback separators.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpawnSite {
    pub function_name: String,
    pub source_file: String,
    pub source_line: u32,
}

/// Effect yield callsite captured from continuation frame metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectCreationSite {
    pub function_name: String,
    pub source_file: String,
    pub source_line: u32,
}

/// Single frame captured in traceback query results.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TraceFrame {
    pub func_name: String,
    pub source_file: String,
    pub source_line: u32,
}

/// Frames captured for one continuation hop in traceback query results.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TraceHop {
    pub frames: Vec<TraceFrame>,
}

impl From<EffectCreationSite> for SpawnSite {
    fn from(value: EffectCreationSite) -> Self {
        SpawnSite {
            function_name: value.function_name,
            source_file: value.source_file,
            source_line: value.source_line,
        }
    }
}

/// Result status for an effect yield in the active chain.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EffectResult {
    Resumed {
        value_repr: String,
    },
    Threw {
        handler_name: String,
        exception_repr: String,
    },
    Transferred {
        handler_name: String,
        target_repr: String,
    },
    Active,
}

/// Active chain row assembled by Rust for default traceback rendering.
#[derive(Debug, Clone)]
pub enum ActiveChainEntry {
    ProgramYield {
        function_name: String,
        source_file: String,
        source_line: u32,
        sub_program_repr: String,
    },
    EffectYield {
        function_name: String,
        source_file: String,
        source_line: u32,
        effect_repr: String,
        handler_stack: Vec<HandlerDispatchEntry>,
        result: EffectResult,
    },
    ContextEntry {
        data: Py<PyAny>,
    },
    ExceptionSite {
        function_name: String,
        source_file: String,
        source_line: u32,
        exception_type: String,
        message: String,
    },
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
        program_call_repr: Option<String>,
    },
    FrameExited {
        function_name: String,
    },
    DispatchStarted {
        dispatch_id: DispatchId,
        effect_repr: String,
        is_execution_context_effect: bool,
        creation_site: Option<EffectCreationSite>,
        handler_name: String,
        handler_kind: HandlerKind,
        handler_source_file: Option<String>,
        handler_source_line: Option<u32>,
        handler_chain_snapshot: Vec<HandlerSnapshotEntry>,
        effect_frame_id: Option<FrameId>,
        effect_function_name: Option<String>,
        effect_source_file: Option<String>,
        effect_source_line: Option<u32>,
    },
    Delegated {
        dispatch_id: DispatchId,
        from_handler_name: String,
        from_handler_index: usize,
        to_handler_name: String,
        to_handler_index: usize,
        to_handler_kind: HandlerKind,
        to_handler_source_file: Option<String>,
        to_handler_source_line: Option<u32>,
    },
    HandlerCompleted {
        dispatch_id: DispatchId,
        handler_name: String,
        handler_index: usize,
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
