//! Core identifier types for the VM.
//!
//! All IDs are lightweight Copy types using newtype pattern for type safety.

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

/// Unique identifier for prompts/handlers.
///
/// A Marker identifies a handler installation point (prompt) in the continuation stack.
/// Each `with_handler` creates a fresh Marker.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct Marker(pub u64);

/// Unique identifier for segments (arena index).
///
/// Segments are stored in a Vec and referenced by index for efficiency.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct SegmentId(pub u32);

/// Unique identifier for continuations (one-shot tracking).
///
/// Each captured continuation gets a unique ContId to enforce one-shot semantics.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct ContId(pub u64);

/// Unique identifier for dispatches.
///
/// Tracks the lifecycle of an effect dispatch through handler chain.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct DispatchId(pub u64);

/// Unique identifier for runnable continuations.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct RunnableId(pub u64);

/// Unique identifier for callbacks stored in VM's callback table.
///
/// Callbacks are stored separately from Frames to allow Frame to be Clone.
/// The callback is consumed when executed.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct CallbackId(pub u32);

/// Unique identifier for spawned tasks.
///
/// Tasks are managed by the scheduler which maintains its own internal counter.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct TaskId(pub u64);

/// Unique identifier for promises.
///
/// Promises are managed by the scheduler which maintains its own internal counter.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub struct PromiseId(pub u64);

// Global counters for ID generation
static MARKER_COUNTER: AtomicU64 = AtomicU64::new(1);
static CONT_ID_COUNTER: AtomicU64 = AtomicU64::new(1);
static DISPATCH_ID_COUNTER: AtomicU64 = AtomicU64::new(1);
static RUNNABLE_ID_COUNTER: AtomicU64 = AtomicU64::new(1);
static CALLBACK_ID_COUNTER: AtomicU32 = AtomicU32::new(1);

impl Marker {
    /// Create a fresh unique Marker.
    pub fn fresh() -> Self {
        Marker(MARKER_COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    /// Reserved placeholder marker for unstarted continuations.
    pub fn placeholder() -> Self {
        Marker(0)
    }

    /// Create a Marker with a specific value (for testing/deserialization).
    pub fn from_raw(value: u64) -> Self {
        Marker(value)
    }

    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }
}

impl ContId {
    /// Create a fresh unique ContId.
    pub fn fresh() -> Self {
        ContId(CONT_ID_COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }

    pub fn from_raw(value: u64) -> Self {
        ContId(value)
    }
}

impl DispatchId {
    /// Create a fresh unique DispatchId.
    pub fn fresh() -> Self {
        DispatchId(DISPATCH_ID_COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }
}

impl RunnableId {
    /// Create a fresh unique RunnableId.
    pub fn fresh() -> Self {
        RunnableId(RUNNABLE_ID_COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }
}

impl SegmentId {
    pub fn from_index(index: usize) -> Self {
        SegmentId(index as u32)
    }

    pub fn index(&self) -> usize {
        self.0 as usize
    }
}

impl CallbackId {
    pub fn fresh() -> Self {
        CallbackId(CALLBACK_ID_COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    pub fn raw(&self) -> u32 {
        self.0
    }
}

impl TaskId {
    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }

    /// Create a TaskId from a raw value.
    pub fn from_raw(value: u64) -> Self {
        TaskId(value)
    }
}

impl PromiseId {
    /// Get the raw value.
    pub fn raw(&self) -> u64 {
        self.0
    }

    /// Create a PromiseId from a raw value.
    pub fn from_raw(value: u64) -> Self {
        PromiseId(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_marker_fresh_is_unique() {
        let m1 = Marker::fresh();
        let m2 = Marker::fresh();
        assert_ne!(m1, m2);
    }

    #[test]
    fn test_cont_id_fresh_is_unique() {
        let c1 = ContId::fresh();
        let c2 = ContId::fresh();
        assert_ne!(c1, c2);
    }

    #[test]
    fn test_segment_id_index_roundtrip() {
        let id = SegmentId::from_index(42);
        assert_eq!(id.index(), 42);
    }

    #[test]
    fn test_callback_id_fresh_is_unique() {
        let c1 = CallbackId::fresh();
        let c2 = CallbackId::fresh();
        assert_ne!(c1, c2);
    }

    #[test]
    fn test_task_id_equality() {
        let t1 = TaskId::from_raw(42);
        let t2 = TaskId::from_raw(42);
        assert_eq!(t1, t2);
    }

    #[test]
    fn test_promise_id_equality() {
        let p1 = PromiseId::from_raw(42);
        let p2 = PromiseId::from_raw(42);
        assert_eq!(p1, p2);
    }
}
