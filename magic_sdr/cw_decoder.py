"""Morse Code (CW) decoder — decodes continuous-wave Morse signals in real time.

Approach
--------
Morse code is amplitude keying (ASK): the carrier is on for "dits" and
"dahs" and off between them. So we:

1. Compute the envelope of the incoming audio (rectify + low-pass filter).
2. Threshold the envelope to get a binary on/off signal.
3. Measure the durations of on-periods (marks) and off-periods (spaces).
4. Map mark/space durations to Morse elements using adaptive timing:
     • A dit is the base unit.
     • A dah is ~3 dits.
     • Intra-character space (between dits/dahs in a letter) = 1 dit.
     • Inter-character space (between letters in a word) = 3 dits.
     • Inter-word space = 7 dits.
   We adaptively estimate the dit duration from observed marks.
5. Look up the resulting dot-dash sequence in MORSE_TABLE to get text.

Limitations
-----------
* This works best on clean CW signals (good SNR). Heavy QRN (static) or
  QRM (interference) will produce errors.
* We assume the CW tone is within the audio passband. For SSB/CW modes
  in Gqrx, the CW pitch is typically 600-800 Hz — well within audio.
* The decoder works on the demodulated audio, so it only decodes CW that
  is already tuned in. It's not a "search for CW anywhere" tool.

Output
------
The decoder exposes a `decoded_text` property and emits a `text_updated`
signal whenever new characters are decoded. The UI shows a scrolling
text panel.
"""

from __future__ import annotations

import math
import time
from typing import Optional, List, Deque
from collections import deque

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, QTimer

# International Morse code table
MORSE_TABLE: dict[str, str] = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'",
    "-.-.--": "!", "-..-.": "/", "-.--.": "(", "-.--.-": ")",
    ".-...": "&", "---...": ":", "-.-.-.": ";", "-...-": "=",
    ".-.-.": "+", "-....-": "-", "..--.-": "_", ".-..-.": '"',
    "...-..-": "$", ".--.-.": "@",
}

# Prosigns (special Morse abbreviations) — we render them as text markers
PROSIGNS: dict[str, str] = {
    ".-.-": "<AR>",  # end of message
    "...-.": "<SN>",  # understood
    "-.-.-": "<KA>",  # starting signal
    "-...-": "<BT>",  # break / new paragraph
    "...-.-": "<SK>",  # end of contact
}


class CWDecoder(QObject):
    """Real-time Morse code decoder.

    Usage:
        dec = CWDecoder()
        dec.text_updated.connect(my_label.setText)
        # On each audio chunk:
        dec.process_audio(chunk_int16, sample_rate=48000)
    """

    text_updated = pyqtSignal(str)  # full decoded text so far
    element_updated = pyqtSignal(str)  # current element being built (e.g. ".-.")
    wpm_updated = pyqtSignal(float)  # words per minute estimate

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True

        # Adaptive timing state
        self._dit_duration_s: float = 0.10  # initial guess: 100 ms (12 WPM)
        # History of recent mark durations — used to refine dit estimate
        self._mark_history: Deque[float] = deque(maxlen=20)

        # Envelope filter state — single-pole IIR low-pass
        self._env_state: float = 0.0
        self._env_alpha: float = 0.05  # smoothing factor (lower = smoother, slower)

        # Threshold for on/off detection — adaptive, based on recent envelope.
        # Initial value of 0.05 corresponds to ~-26 dBFS, low enough to detect
        # any reasonable CW tone but high enough to ignore noise floor. Once we
        # have ≥10 samples of history, this gets updated adaptively.
        self._env_history: Deque[float] = deque(maxlen=4800)  # ~1 s at 48 kHz chunk rate
        self._threshold: float = 0.05

        # Current state
        self._current_state: bool = False  # False = off (space), True = on (mark)
        # Use sample-count-based timing instead of wall-clock. This is robust
        # against chunks arriving faster or slower than real-time (e.g. when
        # the audio buffer is being drained or filled, or in unit tests).
        self._total_samples_processed: int = 0
        self._state_start_sample: int = 0  # sample index when current state began
        self._current_morse: str = ""  # building current letter (e.g. ".-.")

        # Output text
        self._decoded_text: str = ""
        self._last_decode_time: float = time.time()

        # Idle timeout — if no state transitions for 2 s, force a word break
        # and clear the current letter (in case we missed a transition).
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(500)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start()

    # ----------------------------- public API -----------------------------
    def reset(self) -> None:
        """Clear all state (call when changing stations)."""
        self._env_state = 0.0
        self._threshold = 0.05  # back to default — adaptive computation kicks in later
        self._env_history.clear()
        self._mark_history.clear()
        self._current_state = False
        self._total_samples_processed = 0
        self._state_start_sample = 0
        self._current_morse = ""
        self._decoded_text = ""
        self._dit_duration_s = 0.10
        self.text_updated.emit("")
        self.element_updated.emit("")

    def clear_text(self) -> None:
        """Clear just the decoded text (keep timing state)."""
        self._decoded_text = ""
        self.text_updated.emit("")

    @property
    def decoded_text(self) -> str:
        return self._decoded_text

    @property
    def wpm(self) -> float:
        """Words-per-minute estimate based on current dit duration."""
        # Standard: WPM = 1200 / (dit_duration_ms)
        if self._dit_duration_s <= 0:
            return 0.0
        return 1200.0 / (self._dit_duration_s * 1000.0)

    # ----------------------------- audio processing -----------------------------
    def process_audio(self, chunk: np.ndarray, sample_rate: int) -> None:
        if not self.enabled:
            return
        try:
            # Convert to mono float32 in [-1, 1]
            if chunk.dtype == np.int16:
                audio = chunk.astype(np.float32) / 32768.0
            elif chunk.dtype == np.int32:
                audio = chunk.astype(np.float32) / 2147483648.0
            elif chunk.dtype == np.float32:
                audio = chunk
            else:
                audio = chunk.astype(np.float32)
            if audio.ndim == 2:
                audio = audio.mean(axis=1)

            if len(audio) == 0:
                return

            # Rectify + low-pass to get envelope
            rect = np.abs(audio)
            win = max(8, sample_rate // 4000)  # ~2 ms window at 48 kHz = 96 samples
            kernel = np.ones(win, dtype=np.float32) / win
            envelope = np.convolve(rect, kernel, mode='same')
            self._env_state = float(envelope[-1]) if len(envelope) > 0 else 0.0

            # IMPORTANT: compute threshold from PREVIOUS env_history BEFORE
            # processing this chunk's slices. This prevents the case where a
            # pure-tone chunk sets the threshold so high that its own slices
            # can't detect the tone.
            if len(self._env_history) >= 100:
                arr = np.fromiter(self._env_history, dtype=np.float32)
                lo = float(np.percentile(arr, 20))
                hi = float(np.percentile(arr, 80))
                self._threshold = lo + 0.5 * (hi - lo)
            elif len(self._env_history) >= 10:
                arr = np.fromiter(self._env_history, dtype=np.float32)
                lo = float(np.min(arr))
                hi = float(np.max(arr))
                self._threshold = lo + 0.5 * (hi - lo)

            # Detect on/off transitions using the now-stable threshold.
            # Use SAMPLE-COUNT-based timing — slice_time is the absolute
            # sample index, and durations are computed as sample differences
            # divided by sample_rate. This is robust to chunks arriving
            # faster or slower than real-time.
            now = time.time()
            chunk_samples = len(audio)
            chunk_start_sample = self._total_samples_processed
            # Sample the envelope at ~100 Hz (10 ms slices)
            chunk_duration_s = chunk_samples / sample_rate
            n_slices = max(1, int(chunk_duration_s / 0.01))
            slice_size = chunk_samples // n_slices
            for i in range(n_slices):
                seg = envelope[i * slice_size:(i + 1) * slice_size]
                if len(seg) == 0:
                    continue
                v = float(np.max(seg))  # use max to avoid missing short marks
                is_on = v > self._threshold and self._threshold > 0
                # slice_sample is the sample index at the middle of this slice
                slice_sample = chunk_start_sample + (i + 0.5) * slice_size

                if is_on != self._current_state:
                    # State transition — record the duration of the previous state
                    duration_s = (slice_sample - self._state_start_sample) / sample_rate
                    self._handle_transition(self._current_state, duration_s)
                    self._current_state = is_on
                    self._state_start_sample = slice_sample
                    self._last_decode_time = now
                elif not is_on and self._current_morse:
                    # Currently in silence, with a partial letter being built.
                    # Check if the silence has been long enough to flush the letter
                    # as an inter-character or inter-word space.
                    silence_duration_s = (slice_sample - self._state_start_sample) / sample_rate
                    if silence_duration_s > self._dit_duration_s * 7.0:
                        # Inter-word space — flush letter + add space
                        self._flush_letter()
                        if not self._decoded_text.endswith(" "):
                            self._decoded_text += " "
                            self.text_updated.emit(self._decoded_text)
                        # Reset state start so we don't keep flushing
                        self._state_start_sample = slice_sample
                    elif silence_duration_s > self._dit_duration_s * 3.0:
                        # Inter-character space — flush letter
                        self._flush_letter()
                        # Reset state start so we don't flush again prematurely
                        self._state_start_sample = slice_sample

            # Update total samples processed (for next chunk's slice_sample math)
            self._total_samples_processed += chunk_samples

            # AFTER processing slices, add the current chunk's envelope to
            # env_history so it informs the NEXT chunk's threshold computation.
            for v in envelope[::64]:
                self._env_history.append(float(v))
        except Exception:
            pass

    # ----------------------------- element classification -----------------------------
    def _handle_transition(self, was_on: bool, duration: float) -> None:
        """Called when the envelope transitions from on→off or off→on.

        `was_on` is the state we were in BEFORE the transition.
        `duration` is how long we were in that state.
        """
        if was_on:
            # Just finished a mark — classify as dit or dah
            self._mark_history.append(duration)
            # Refine dit duration: the dit is the smaller of the two clusters
            # in mark_history. We use the median of the bottom 50% as dit estimate.
            if len(self._mark_history) >= 4:
                arr = sorted(self._mark_history)
                n_dit = len(arr) // 2
                dits = arr[:n_dit]
                dahs = arr[n_dit:]
                if dits:
                    median_dit = float(np.median(dits))
                    # Sanity check: dahs should be ~3x dits
                    if dahs:
                        median_dah = float(np.median(dahs))
                        if 1.5 < median_dah / median_dit < 6:
                            self._dit_duration_s = median_dit
                            self.wpm_updated.emit(self.wpm)
            # Classify this mark
            if duration < self._dit_duration_s * 2.0:
                self._current_morse += "."
            else:
                self._current_morse += "-"
            self.element_updated.emit(self._current_morse)
        else:
            # Just finished a space — classify as intra-char, inter-char, or inter-word
            if duration > self._dit_duration_s * 5.0:
                # Inter-word space — flush current letter, add a space
                self._flush_letter()
                if not self._decoded_text.endswith(" "):
                    self._decoded_text += " "
                    self.text_updated.emit(self._decoded_text)
            elif duration > self._dit_duration_s * 2.0:
                # Inter-character space — flush current letter
                self._flush_letter()

    def _flush_letter(self) -> None:
        """Convert the accumulated morse sequence to a character and append."""
        if not self._current_morse:
            return
        # Try prosigns first
        if self._current_morse in PROSIGNS:
            ch = PROSIGNS[self._current_morse]
        elif self._current_morse in MORSE_TABLE:
            ch = MORSE_TABLE[self._current_morse]
        else:
            ch = f"[?{self._current_morse}]"
        self._decoded_text += ch
        # Cap buffer to 2000 chars
        if len(self._decoded_text) > 2000:
            self._decoded_text = self._decoded_text[-2000:]
        self._current_morse = ""
        self.element_updated.emit("")
        self.text_updated.emit(self._decoded_text)

    # ----------------------------- idle handling -----------------------------
    def _check_idle(self) -> None:
        """If no transitions for 2 s, flush any pending letter + add space."""
        now = time.time()
        if now - self._last_decode_time > 2.0 and self._current_morse:
            self._flush_letter()
