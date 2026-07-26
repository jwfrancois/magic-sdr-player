"""Bookmark manager — persists a library of favorite stations.

Stores bookmarks in /home/z/my-project/bookmarks.json. Each bookmark has:
  - freq_hz
  - label (user-editable name)
  - band (auto-detected from frequency)
  - modulation
  - tags (free-form, e.g. ["music", "rock"])
  - ai_tag (auto-populated by the AI tagger)
  - notes (free text)
  - last_heard (epoch seconds, updated when the freq is tuned)

On first run, seeds the library with the known channels from all 6 bands
(FM Broadcast, Airband, NOAA, 2m Ham, Marine VHF, Shortwave).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import List, Optional, Dict, Any

from PyQt5.QtCore import QObject, pyqtSignal

from . import BOOKMARKS_FILE
from .band_presets import BANDS, band_for_frequency, lookup_known, guess_modulation

log = logging.getLogger(__name__)


class Bookmark:
    def __init__(self, freq_hz: int, label: str, modulation: Optional[str] = None,
                 band: Optional[str] = None, tags: Optional[List[str]] = None,
                 ai_tag: Optional[str] = None, notes: str = "",
                 last_heard: float = 0.0):
        self.freq_hz = int(freq_hz)
        self.label = label
        self.modulation = modulation or guess_modulation(self.freq_hz)
        b = band_for_frequency(self.freq_hz)
        self.band = band or (b.name if b else "Custom")
        self.tags = tags or []
        self.ai_tag = ai_tag
        self.notes = notes
        self.last_heard = last_heard

    def to_dict(self) -> Dict[str, Any]:
        return {
            "freq_hz": self.freq_hz,
            "freq_mhz": self.freq_hz / 1e6,
            "label": self.label,
            "modulation": self.modulation,
            "band": self.band,
            "tags": self.tags,
            "ai_tag": self.ai_tag,
            "notes": self.notes,
            "last_heard": self.last_heard,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Bookmark":
        return cls(
            freq_hz=int(d["freq_hz"]),
            label=d.get("label", ""),
            modulation=d.get("modulation"),
            band=d.get("band"),
            tags=d.get("tags", []),
            ai_tag=d.get("ai_tag"),
            notes=d.get("notes", ""),
            last_heard=d.get("last_heard", 0.0),
        )


class BookmarkManager(QObject):
    """Manages a JSON-backed bookmark library."""

    bookmarks_changed = pyqtSignal()      # emit on any change
    bookmark_added = pyqtSignal(object)   # emit Bookmark
    bookmark_removed = pyqtSignal(int)    # emit freq_hz

    def __init__(self, path: str = BOOKMARKS_FILE, parent=None):
        super().__init__(parent)
        self.path = path
        self._lock = threading.RLock()
        self._bookmarks: Dict[int, Bookmark] = {}  # freq_hz -> Bookmark
        self.load()

    def load(self) -> None:
        with self._lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path) as f:
                        data = json.load(f)
                    self._bookmarks = {
                        int(b["freq_hz"]): Bookmark.from_dict(b) for b in data
                    }
                    log.info("Loaded %d bookmarks from %s", len(self._bookmarks), self.path)
                except Exception as e:
                    log.warning("Failed to load bookmarks from %s: %s", self.path, e)
                    self._bookmarks = {}
            if not self._bookmarks:
                # Seed with known channels
                self._seed_defaults()

    def _seed_defaults(self) -> None:
        """Populate the library with all known channels from every band."""
        for band in BANDS:
            for freq_hz, name in band.known.items():
                if freq_hz not in self._bookmarks:
                    self._bookmarks[freq_hz] = Bookmark(
                        freq_hz=freq_hz, label=name, modulation=band.modulation,
                        band=band.name,
                    )
        log.info("Seeded %d default bookmarks", len(self._bookmarks))
        self.save()

    def save(self) -> None:
        with self._lock:
            try:
                with open(self.path, "w") as f:
                    json.dump([b.to_dict() for b in self._bookmarks.values()],
                              f, indent=2)
                log.debug("Saved %d bookmarks to %s", len(self._bookmarks), self.path)
            except Exception as e:
                log.warning("Failed to save bookmarks to %s: %s", self.path, e)

    def list_all(self) -> List[Bookmark]:
        with self._lock:
            return sorted(self._bookmarks.values(), key=lambda b: b.freq_hz)

    def list_by_band(self, band_name: str) -> List[Bookmark]:
        with self._lock:
            return sorted([b for b in self._bookmarks.values() if b.band == band_name],
                          key=lambda b: b.freq_hz)

    def get(self, freq_hz: int) -> Optional[Bookmark]:
        with self._lock:
            return self._bookmarks.get(int(freq_hz))

    def add(self, freq_hz: int, label: Optional[str] = None,
            modulation: Optional[str] = None, tags: Optional[List[str]] = None,
            notes: str = "", ai_tag: Optional[str] = None) -> Bookmark:
        """Add or update a bookmark. If freq_hz already exists, update fields."""
        with self._lock:
            existing = self._bookmarks.get(int(freq_hz))
            if existing:
                if label: existing.label = label
                if modulation: existing.modulation = modulation
                if tags: existing.tags = tags
                if notes: existing.notes = notes
                if ai_tag: existing.ai_tag = ai_tag
                bookmark = existing
            else:
                bookmark = Bookmark(
                    freq_hz=int(freq_hz),
                    label=label or lookup_known(int(freq_hz)) or
                          f"{int(freq_hz)/1e6:.4f} MHz",
                    modulation=modulation,
                    tags=tags or [],
                    notes=notes,
                    ai_tag=ai_tag,
                )
                self._bookmarks[int(freq_hz)] = bookmark
            self.save()
        self.bookmark_added.emit(bookmark)
        self.bookmarks_changed.emit()
        return bookmark

    def remove(self, freq_hz: int) -> bool:
        with self._lock:
            if int(freq_hz) in self._bookmarks:
                del self._bookmarks[int(freq_hz)]
                self.save()
                self.bookmark_removed.emit(int(freq_hz))
                self.bookmarks_changed.emit()
                return True
            return False

    def update_last_heard(self, freq_hz: int) -> None:
        """Mark a bookmark as recently tuned (updates last_heard, no save burst)."""
        with self._lock:
            b = self._bookmarks.get(int(freq_hz))
            if b:
                b.last_heard = time.time()

    def search(self, query: str) -> List[Bookmark]:
        q = query.lower()
        with self._lock:
            return [b for b in self._bookmarks.values()
                    if q in b.label.lower() or q in b.band.lower() or
                       any(q in t.lower() for t in b.tags) or
                       (b.ai_tag and q in b.ai_tag.lower()) or
                       (b.notes and q in b.notes.lower())]
