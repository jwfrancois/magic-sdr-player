"""Recording manager — captures audio + metadata to disk.

Files are written to /home/z/my-project/recordings/.

Two recording modes:
  1. Quick recording — start/stop on demand, saves a single WAV file.
  2. Scheduled recording — cron-like schedule that triggers on a given
     frequency + modulation at a given time, for a given duration.

Each recording is saved as:
  recordings/2026-07-27/20260727_153045_96.900MHz_WFM_ST.wav
  recordings/2026-07-27/20260727_153045_96.900MHz_WFM_ST.json   (metadata)

The metadata JSON contains:
  - frequency, modulation, band, label (if known)
  - start time, end time, duration
  - sample rate, channels
  - ai_tag (if classified)
  - signal_level_avg (mean level during recording)
  - peak_level_db, peak_level_at_offset_s
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import wave
from datetime import datetime
from typing import Optional, List, Dict, Any

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from . import RECORDINGS_DIR
from .band_presets import band_for_frequency, lookup_known, guess_modulation

log = logging.getLogger(__name__)


class Recording:
    def __init__(self, freq_hz: int, modulation: str, label: Optional[str] = None,
                 ai_tag: Optional[str] = None):
        self.freq_hz = int(freq_hz)
        self.modulation = modulation
        self.label = label or lookup_known(self.freq_hz) or "Unknown"
        self.ai_tag = ai_tag
        self.start_ts = time.time()
        self.end_ts: Optional[float] = None
        self.sample_rate = 48000
        self.channels = 2
        self._wav_path: Optional[str] = None
        self._meta_path: Optional[str] = None
        self._wav: Optional[wave.Wave_write] = None
        self._lock = threading.Lock()
        self._frames_written = 0
        self._level_samples: List[float] = []
        self._peak_level: float = -120.0
        self._peak_at_offset: float = 0.0

    @property
    def wav_path(self) -> Optional[str]:
        return self._wav_path

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def duration_s(self) -> float:
        if self._frames_written == 0 or self.sample_rate == 0:
            return 0.0
        return self._frames_written / self.sample_rate

    def start(self) -> bool:
        # Make a date-stamped subfolder
        date_dir = os.path.join(RECORDINGS_DIR, datetime.now().strftime("%Y-%m-%d"))
        os.makedirs(date_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mhz = self.freq_hz / 1e6
        # Sanitize modulation for filename
        mod_safe = self.modulation.replace(" ", "_")
        name = f"{ts}_{mhz:.4f}MHz_{mod_safe}"
        self._wav_path = os.path.join(date_dir, name + ".wav")
        self._meta_path = os.path.join(date_dir, name + ".json")
        try:
            self._wav = wave.open(self._wav_path, "wb")
            self._wav.setnchannels(self.channels)
            self._wav.setsampwidth(2)  # int16
            self._wav.setframerate(self.sample_rate)
            log.info("Recording started: %s", self._wav_path)
            return True
        except Exception as e:
            log.error("Failed to start recording: %s", e)
            self._wav = None
            return False

    def write_chunk(self, chunk: np.ndarray, sample_rate: int, channels: int,
                    signal_level_db: Optional[float] = None) -> None:
        if self._wav is None:
            return
        with self._lock:
            try:
                self.sample_rate = sample_rate
                self.channels = channels
                # Make sure chunk is int16, 2-D for stereo
                if chunk.dtype != np.int16:
                    chunk = chunk.astype(np.int16)
                if channels == 2 and chunk.ndim == 1:
                    chunk = np.column_stack([chunk, chunk])
                # Reshape to (N, channels) and write interleaved bytes
                if chunk.ndim == 2:
                    interleaved = chunk.flatten()
                else:
                    interleaved = chunk
                self._wav.writeframes(interleaved.tobytes())
                self._frames_written += len(interleaved) // channels
                if signal_level_db is not None:
                    self._level_samples.append(signal_level_db)
                    if signal_level_db > self._peak_level:
                        self._peak_level = signal_level_db
                        self._peak_at_offset = self.duration_s
            except Exception as e:
                log.warning("Failed to write recording chunk: %s", e)

    def stop(self) -> Optional[str]:
        with self._lock:
            if self._wav is None:
                return None
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
            self.end_ts = time.time()
            # Write metadata JSON
            meta: Dict[str, Any] = {
                "freq_hz": self.freq_hz,
                "freq_mhz": self.freq_hz / 1e6,
                "modulation": self.modulation,
                "band": (band_for_frequency(self.freq_hz).name
                         if band_for_frequency(self.freq_hz) else "Custom"),
                "label": self.label,
                "ai_tag": self.ai_tag,
                "start_ts": self.start_ts,
                "end_ts": self.end_ts,
                "duration_s": self.end_ts - self.start_ts if self.end_ts else 0.0,
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "wav_file": os.path.basename(self._wav_path) if self._wav_path else None,
                "peak_level_db": self._peak_level,
                "peak_at_offset_s": self._peak_at_offset,
                "avg_level_db": (float(np.mean(self._level_samples))
                                 if self._level_samples else None),
            }
            try:
                with open(self._meta_path, "w") as f:
                    json.dump(meta, f, indent=2)
            except Exception as e:
                log.warning("Failed to write recording metadata: %s", e)
            log.info("Recording stopped: %s (%.1fs)", self._wav_path, meta["duration_s"])
            return self._wav_path


class RecordingManager(QObject):
    """Manages live recordings and the scheduled recording queue."""

    recording_started = pyqtSignal(object)   # Recording
    recording_stopped = pyqtSignal(object, str)  # Recording, wav_path
    chunk_recorded = pyqtSignal(int)         # frames_written

    def __init__(self, parent=None):
        super().__init__(parent)
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        self._current: Optional[Recording] = None
        self._lock = threading.Lock()
        self._scheduled: List[Dict[str, Any]] = []

    @property
    def is_recording(self) -> bool:
        return self._current is not None

    @property
    def current(self) -> Optional[Recording]:
        return self._current

    def start_recording(self, freq_hz: int, modulation: str,
                        label: Optional[str] = None,
                        ai_tag: Optional[str] = None) -> bool:
        with self._lock:
            if self._current is not None:
                log.warning("Already recording — stop first")
                return False
            r = Recording(freq_hz=freq_hz, modulation=modulation,
                          label=label, ai_tag=ai_tag)
            if not r.start():
                return False
            self._current = r
        self.recording_started.emit(r)
        return True

    def write_chunk(self, chunk: np.ndarray, sample_rate: int, channels: int,
                    signal_level_db: Optional[float] = None) -> None:
        with self._lock:
            if self._current is None:
                return
            self._current.write_chunk(chunk, sample_rate, channels, signal_level_db)
        self.chunk_recorded.emit(self._current.frames_written)

    def stop_recording(self) -> Optional[str]:
        with self._lock:
            if self._current is None:
                return None
            r = self._current
            self._current = None
        path = r.stop()
        self.recording_stopped.emit(r, path or "")
        return path

    def list_recordings(self) -> List[Dict[str, Any]]:
        """List all recording metadata in the recordings dir, newest first."""
        out: List[Dict[str, Any]] = []
        for root, dirs, files in os.walk(RECORDINGS_DIR):
            for f in files:
                if f.endswith(".json"):
                    try:
                        with open(os.path.join(root, f)) as fp:
                            d = json.load(fp)
                            d["path"] = os.path.join(root, d.get("wav_file", ""))
                            out.append(d)
                    except Exception:
                        continue
        out.sort(key=lambda x: x.get("start_ts", 0), reverse=True)
        return out

    # ----------------------------- scheduling -----------------------------
    def schedule_recording(self, freq_hz: int, modulation: str,
                            start_at: float, duration_s: float,
                            label: Optional[str] = None) -> int:
        """Schedule a future recording. Returns a schedule ID."""
        sched_id = int(time.time() * 1000) % (1 << 31)
        entry = {
            "id": sched_id,
            "freq_hz": int(freq_hz),
            "modulation": modulation,
            "label": label,
            "start_at": start_at,
            "duration_s": duration_s,
        }
        self._scheduled.append(entry)
        threading.Thread(target=self._schedule_runner, args=(entry,), daemon=True).start()
        return sched_id

    def _schedule_runner(self, entry: Dict[str, Any]) -> None:
        # Sleep until start_at
        delay = entry["start_at"] - time.time()
        if delay > 0:
            time.sleep(delay)
        # Tune Gqrx — but we don't have a reference here; rely on caller to set up
        # the right frequency before scheduling. We just record.
        self.start_recording(entry["freq_hz"], entry["modulation"],
                             label=entry.get("label"))
        time.sleep(entry["duration_s"])
        self.stop_recording()
        # Remove from list
        try:
            self._scheduled.remove(entry)
        except ValueError:
            pass
