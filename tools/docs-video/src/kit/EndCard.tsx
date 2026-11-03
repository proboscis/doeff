// The closing frame of a page's GIF preview: the GIF stops at a scene boundary and this card says the video goes on, and for how long.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {ACCENT, BG, MUTED, SANS, TEXT} from './theme';

export const EndCard: React.FC<{label: string}> = ({label}) => (
  <AbsoluteFill style={{backgroundColor: BG, alignItems: 'center', justifyContent: 'center', fontFamily: SANS}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 36}}>
      <div style={{width: 0, height: 0, borderTop: '48px solid transparent', borderBottom: '48px solid transparent', borderLeft: `80px solid ${ACCENT}`}} />
      <div style={{fontSize: 64, fontWeight: 700, color: TEXT}}>{label}</div>
    </div>
    <div style={{marginTop: 28, fontSize: 30, color: MUTED}}>Click the preview to play it with narration</div>
  </AbsoluteFill>
);
