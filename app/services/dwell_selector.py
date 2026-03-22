"""
app/services/dwell_selector.py
────────────────────────────────
Dwell-time selection engine with stability gate and hysteresis.

Improvements over original
──────────────────────────
1. Stability gate — dwell ring only starts after the gaze has been
   stable on the SAME key for `stable_frames` consecutive frames.
   Prevents dwell from firing from a brief glance or transit.

2. Hysteresis — once a key is focused, the gaze must move to a
   DIFFERENT key for `hyst_frames` consecutive frames before the
   focus actually switches.  A single-frame jitter to an adjacent
   key is ignored completely.

3. Post-selection cooldown — unchanged from original.

Data flow
─────────
  update(key_label) called every tick (30 fps)
       ↓
  hysteresis filter  →  candidate_key  (stable candidate)
       ↓
  stability gate     →  focused_key    (focus confirmed after N frames)
       ↓
  dwell accumulator  →  (selected_key | None, progress 0-1)
"""
from __future__ import annotations

import time
from typing import Optional, Tuple


class DwellSelector:

    def __init__(
        self,
        dwell_time_ms: int = 800,
        cooldown_ms:   int = 400,
        stable_frames: int = 3,
        hyst_frames:   int = 4,
    ) -> None:
        """
        Parameters
        ----------
        dwell_time_ms : ms the gaze must stay on a key to select it.
        cooldown_ms   : ms blocked after a selection fires.
        stable_frames : frames gaze must be on a key before dwell starts.
                        3 frames @ 30 fps = 100 ms settle time.
        hyst_frames   : frames gaze must be on a NEW key before focus switches.
                        4 frames @ 30 fps = ~133 ms — kills single-frame jitter.
        """
        self._dwell_time_ms = dwell_time_ms
        self._cooldown_ms   = cooldown_ms
        self._stable_frames = stable_frames
        self._hyst_frames   = hyst_frames

        # hysteresis state
        self._focused_key:     Optional[str] = None
        self._candidate_key:   Optional[str] = None
        self._candidate_count: int           = 0

        # stability gate state
        self._stable_count: int  = 0
        self._stable_ready: bool = False

        # dwell accumulator state
        self._enter_time:     float = 0.0
        self._cooldown_until: float = 0.0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def dwell_time_ms(self) -> int:
        return self._dwell_time_ms

    @dwell_time_ms.setter
    def dwell_time_ms(self, value: int) -> None:
        self._dwell_time_ms = max(200, min(3000, value))

    @property
    def focused_key(self) -> Optional[str]:
        """The currently confirmed (hysteresis-filtered) focused key."""
        return self._focused_key

    @property
    def current_key(self) -> Optional[str]:
        """Alias for focused_key — backward compatibility."""
        return self._focused_key

    # ── Core ──────────────────────────────────────────────────────────────────

    def update(self, key_label: Optional[str]) -> Tuple[Optional[str], float]:
        """
        Call once per frame with the raw key under gaze.

        Returns
        -------
        (selected_key_or_None, dwell_progress_0_to_1)

        selected_key is non-None only on the exact frame dwell completes.
        dwell_progress is 0.0 until the stability gate passes, then rises
        smoothly from 0 to 1 as the user dwells.
        """
        now = time.time()

        # ── cooldown gate ─────────────────────────────────────────────────────
        if now < self._cooldown_until:
            return None, 0.0

        # ── step 1: hysteresis filter ─────────────────────────────────────────
        # Only commit to a new key after hyst_frames consecutive frames on it.
        if key_label == self._focused_key:
            self._candidate_key   = None
            self._candidate_count = 0
        else:
            if key_label == self._candidate_key:
                self._candidate_count += 1
            else:
                self._candidate_key   = key_label
                self._candidate_count = 1

            if self._candidate_count >= self._hyst_frames:
                # commit focus switch
                self._focused_key     = self._candidate_key
                self._candidate_key   = None
                self._candidate_count = 0
                self._stable_count    = 0
                self._stable_ready    = False
                self._enter_time      = 0.0

        # ── step 2: stability gate ────────────────────────────────────────────
        # Dwell ring only starts after stable_frames on the focused key.
        if self._focused_key is None:
            self._stable_count = 0
            self._stable_ready = False
            return None, 0.0

        if not self._stable_ready:
            self._stable_count += 1
            if self._stable_count >= self._stable_frames:
                self._stable_ready = True
                self._enter_time   = now
            return None, 0.0

        # ── step 3: dwell accumulator ─────────────────────────────────────────
        if self._enter_time == 0.0:
            self._enter_time = now

        elapsed_ms = (now - self._enter_time) * 1000.0
        progress   = min(1.0, elapsed_ms / self._dwell_time_ms)

        if progress >= 1.0:
            selected = self._focused_key
            self._cooldown_until  = now + self._cooldown_ms / 1000.0
            self._focused_key     = None
            self._candidate_key   = None
            self._candidate_count = 0
            self._stable_count    = 0
            self._stable_ready    = False
            self._enter_time      = 0.0
            return selected, 1.0

        return None, progress

    def reset(self) -> None:
        """Full state reset — call after re-calibration or focus loss."""
        self._focused_key     = None
        self._candidate_key   = None
        self._candidate_count = 0
        self._stable_count    = 0
        self._stable_ready    = False
        self._enter_time      = 0.0
        self._cooldown_until  = 0.0