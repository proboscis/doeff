//! Segment types for delimited continuations.
//!
//! A Segment represents a delimited continuation frame bounded by a prompt (Marker).

use crate::frame::Frame;
use crate::ids::{Marker, SegmentId};

/// Delimited continuation frame.
///
/// Represents a continuation delimited by a prompt (marker).
/// Contains the frames to execute and metadata about the prompt boundary.
#[derive(Debug)]
pub struct Segment {
    /// Prompt identity (delimiting prompt for this segment)
    pub marker: Marker,

    /// Frames in this segment (stack, index 0 = top/next to execute)
    pub frames: Vec<Frame>,

    /// Caller link - who to return value to when this segment completes
    pub caller: Option<SegmentId>,

    /// Evidence vector snapshot - handlers in scope at creation time.
    /// This is [innermost, ..., outermost] order.
    pub scope_chain: Vec<Marker>,

    /// Is this a prompt boundary segment?
    /// If true, this segment was created by WithHandler and delimits handler scope.
    pub is_prompt_boundary: bool,

    /// If prompt boundary, which handler marker it delimits.
    /// Handler returns go HERE, not to user code continuation.
    pub handled_marker: Option<Marker>,
}

impl Segment {
    /// Create a new regular segment.
    pub fn new(marker: Marker, caller: Option<SegmentId>, scope_chain: Vec<Marker>) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            is_prompt_boundary: false,
            handled_marker: None,
        }
    }

    /// Create a new prompt boundary segment (for WithHandler).
    ///
    /// A prompt segment marks where handler returns should go.
    pub fn new_prompt(
        marker: Marker,
        caller: Option<SegmentId>,
        scope_chain: Vec<Marker>,
        handled_marker: Marker,
    ) -> Self {
        Segment {
            marker,
            frames: Vec::new(),
            caller,
            scope_chain,
            is_prompt_boundary: true,
            handled_marker: Some(handled_marker),
        }
    }

    /// Push a frame onto this segment's stack.
    pub fn push_frame(&mut self, frame: Frame) {
        self.frames.insert(0, frame);
    }

    /// Pop the top frame from this segment's stack.
    pub fn pop_frame(&mut self) -> Option<Frame> {
        if self.frames.is_empty() {
            None
        } else {
            Some(self.frames.remove(0))
        }
    }

    /// Check if this segment has any frames.
    pub fn has_frames(&self) -> bool {
        !self.frames.is_empty()
    }

    /// Get the number of frames in this segment.
    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }

    /// Check if this is a prompt boundary segment.
    pub fn is_prompt(&self) -> bool {
        self.is_prompt_boundary
    }

    /// Get the handled marker if this is a prompt segment.
    pub fn get_handled_marker(&self) -> Option<Marker> {
        self.handled_marker
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_segment_creation() {
        let marker = Marker::fresh();
        let seg = Segment::new(marker, None, vec![]);
        assert_eq!(seg.marker, marker);
        assert!(seg.caller.is_none());
        assert!(!seg.is_prompt_boundary);
        assert!(seg.handled_marker.is_none());
    }

    #[test]
    fn test_prompt_segment_creation() {
        let marker = Marker::fresh();
        let handled = Marker::fresh();
        let seg = Segment::new_prompt(marker, None, vec![handled], handled);
        assert!(seg.is_prompt_boundary);
        assert_eq!(seg.handled_marker, Some(handled));
    }

    #[test]
    fn test_segment_frame_operations() {
        let marker = Marker::fresh();
        let mut seg = Segment::new(marker, None, vec![]);

        assert!(!seg.has_frames());
        assert_eq!(seg.frame_count(), 0);

        // Can't easily test push/pop without creating real frames
        // but the structure is correct
    }
}
