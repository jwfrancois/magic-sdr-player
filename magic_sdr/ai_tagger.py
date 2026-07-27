"""AI tagger — classifies signals using a small LLM via the z-ai SDK.

The actual LLM call happens in a Node helper script (scripts/ai_helper.js)
because z-ai-web-dev-sdk is a Node package. Python invokes it via subprocess
and feeds it audio features + frequency context.

Audio features are computed cheaply from raw PCM chunks captured from Gqrx's
audio stream — no FFT library needed beyond numpy.

The tagger is fully optional. If the Node SDK is unavailable, or the helper
fails, the tagger returns None and the rest of the app keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Optional, Dict, Any, List

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from .band_presets import Band, lookup_known, band_for_frequency

log = logging.getLogger(__name__)

HELPER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "ai_helper.js")


def compute_audio_features(chunk: np.ndarray, sample_rate: int) -> Dict[str, float]:
    """Compute a small set of features from a PCM audio chunk.

    Returns:
      spectral_centroid_hz, zero_crossing_rate, rms, dominant_freq_hz, bandwidth_hz
    """
    try:
        if chunk.ndim == 2:
            mono = chunk.mean(axis=1)
        else:
            mono = chunk
        mono = mono.astype(np.float32) / 32768.0
        if len(mono) < 256:
            return {}
        # RMS amplitude
        rms = float(np.sqrt(np.mean(mono ** 2)))
        if rms < 1e-5:
            # Effectively silent — don't bother with FFT
            return {"rms": 0.0, "spectral_centroid_hz": 0.0,
                    "zero_crossing_rate": 0.0, "dominant_freq_hz": 0.0,
                    "bandwidth_hz": 0.0}
        # Zero-crossing rate
        zcr = float(np.mean(np.abs(np.diff(np.sign(mono))) > 0))
        # FFT
        n_fft = 1 << int(np.log2(min(len(mono), 8192)))
        win = mono[:n_fft] * np.hanning(n_fft)
        spec = np.abs(np.fft.rfft(win))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
        # Spectral centroid
        total = np.sum(spec) + 1e-12
        centroid = float(np.sum(freqs * spec) / total)
        # Dominant frequency (peak)
        peak_idx = int(np.argmax(spec))
        dominant = float(freqs[peak_idx])
        # Bandwidth (where spectrum drops 20 dB from peak)
        peak_mag = spec[peak_idx]
        threshold = peak_mag * 0.1  # -20 dB
        above = np.where(spec >= threshold)[0]
        if len(above) > 1:
            bandwidth = float(freqs[above[-1]] - freqs[above[0]])
        else:
            bandwidth = 0.0
        return {
            "spectral_centroid_hz": round(centroid, 1),
            "zero_crossing_rate": round(zcr, 4),
            "rms": round(rms, 4),
            "dominant_freq_hz": round(dominant, 1),
            "bandwidth_hz": round(bandwidth, 1),
        }
    except Exception as e:
        log.debug("feature computation failed: %s", e)
        return {}


class AITagger(QObject):
    """Classifies signals via a Node helper that calls the z-ai LLM SDK.

    Async-friendly: `classify_async` runs in a background thread and emits
    `tag_ready` when done. `classify_sync` blocks the caller.
    """

    tag_ready = pyqtSignal(int, object)  # freq_hz, tag dict or None
    tag_failed = pyqtSignal(int, str)    # freq_hz, error

    def __init__(self, helper_path: str = HELPER_PATH, parent=None):
        super().__init__(parent)
        self.helper_path = helper_path
        self.enabled = True
        self._cache: Dict[int, Dict[str, Any]] = {}  # freq_hz -> last tag
        self._cache_ttl = 300  # 5 minutes

    def is_available(self) -> bool:
        """Quick check that the Node helper file exists and node is on PATH."""
        return os.path.exists(self.helper_path)

    def _call_helper(self, payload: Dict[str, Any], timeout: float = 20.0) -> Optional[Dict[str, Any]]:
        try:
            proc = subprocess.run(
                ["node", self.helper_path],
                input=json.dumps(payload),
                capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode != 0:
                log.warning("AI helper failed (exit %d): %s",
                            proc.returncode, proc.stderr[:300])
                return None
            out = proc.stdout.strip()
            if not out:
                return None
            return json.loads(out)
        except subprocess.TimeoutExpired:
            log.warning("AI helper timed out after %.1fs", timeout)
            return None
        except Exception as e:
            log.warning("AI helper invocation failed: %s", e)
            return None

    def classify_sync(self, freq_hz: int, band: Optional[Band] = None,
                      modulation: Optional[str] = None,
                      signal_level_db: Optional[float] = None,
                      audio_chunk: Optional[np.ndarray] = None,
                      sample_rate: int = 48000,
                      duration_s: float = 5.0) -> Optional[Dict[str, Any]]:
        """Classify a signal. Returns a dict {signal_type, language, summary} or None."""
        if not self.enabled or not self.is_available():
            return None
        # Cache check
        now = time.time()
        cached = self._cache.get(int(freq_hz))
        if cached and (now - cached.get("_ts", 0)) < self._cache_ttl:
            return cached.get("tag")

        # Compute features if audio provided
        feats = {}
        if audio_chunk is not None and len(audio_chunk) > 0:
            feats = compute_audio_features(audio_chunk, sample_rate)

        b = band or band_for_frequency(int(freq_hz))
        payload = {
            "frequency_hz": int(freq_hz),
            "modulation": modulation or (b.modulation if b else "FM"),
            "band": b.name if b else "Custom",
            "signal_level_db": signal_level_db or -60.0,
            "duration_s": duration_s,
            "known_label": lookup_known(int(freq_hz)) or "",
            "audio_features": feats,
        }
        tag = self._call_helper(payload)
        if tag is not None:
            tag["_ts"] = now
            self._cache[int(freq_hz)] = {"tag": tag, "_ts": now}
        return tag

    def classify_async(self, freq_hz: int, **kwargs) -> None:
        """Run classification in a background thread; emits tag_ready when done."""
        def worker():
            tag = self.classify_sync(freq_hz, **kwargs)
            if tag is not None:
                self.tag_ready.emit(int(freq_hz), tag)
            else:
                self.tag_failed.emit(int(freq_hz), "No response from AI helper")
        threading.Thread(target=worker, daemon=True, name=f"AITag:{freq_hz}").start()
