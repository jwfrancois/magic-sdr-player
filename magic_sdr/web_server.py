"""Embedded web server for remote access from phones/tablets/laptops.

FastAPI app exposing:
  GET  /                       Web UI (HTML/CSS/JS, served from web/templates)
  GET  /api/state              Current state (freq, mod, level, is_recording, etc.)
  POST /api/tune               { freq_hz, modulation? } — tune Gqrx
  POST /api/modulation         { modulation }
  POST /api/volume             { volume: 0..1 }
  POST /api/mute               { muted: bool }
  POST /api/record             { action: "start"|"stop" }
  GET  /api/bookmarks          List all bookmarks
  POST /api/bookmarks          Add a bookmark
  DELETE /api/bookmarks/{freq} Remove a bookmark
  GET  /api/scan               { band: name } — start a band scan
  POST /api/scan/stop
  GET  /api/recordings         List recent recordings
  GET  /api/recordings/{path}  Download a recording ( WAV file )
  GET  /api/ai/tag/{freq}      Trigger AI classification for a frequency
  WS   /ws/audio               Stream live audio (raw PCM int16) to the browser
  WS   /ws/spectrum            Stream live spectrum data (JSON) to the browser
  WS   /ws/events              Stream state-change events (tune, level, recording...)

The web server runs inside the same Python process as the GUI so it can share
in-memory state. It listens on 0.0.0.0:8000 by default (configurable).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import base64
from typing import Optional, List, Dict, Any

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import WEB_HOST, WEB_PORT, RECORDINGS_DIR
from .gqrx_client import GqrxClient
from .bookmark_manager import BookmarkManager
from .recording_manager import RecordingManager
from .band_scanner import BandScanner
from .ai_tagger import AITagger
from .audio_receiver import AudioReceiver
from .spectrum import SpectrumReceiver
from .band_presets import BANDS, BANDS_BY_NAME, band_for_frequency, lookup_known, guess_modulation

log = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
TEMPLATES_DIR = os.path.join(WEB_DIR, "templates")
STATIC_DIR = os.path.join(WEB_DIR, "static")


# ----------------------------- request models -----------------------------
class TuneReq(BaseModel):
    freq_hz: int
    modulation: Optional[str] = None


class ModReq(BaseModel):
    modulation: str


class VolumeReq(BaseModel):
    volume: float


class MuteReq(BaseModel):
    muted: bool


class RecordReq(BaseModel):
    action: str  # "start" or "stop"


class BookmarkReq(BaseModel):
    freq_hz: int
    label: Optional[str] = None
    modulation: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None


class ScanReq(BaseModel):
    band: str


class AITagReq(BaseModel):
    duration_s: float = 5.0


# ----------------------------- app factory -----------------------------
def create_app(gqrx: GqrxClient,
               bookmarks: BookmarkManager,
               recordings: RecordingManager,
               scanner: BandScanner,
               ai_tagger: AITagger,
               audio_receiver: AudioReceiver,
               spectrum_receiver: SpectrumReceiver,
               get_state_fn) -> FastAPI:
    """Build the FastAPI app, wired to the shared in-process components.

    `get_state_fn` is a zero-arg callable that returns the current app state
    as a dict (frequency, modulation, level, recording, etc.).
    """
    app = FastAPI(title="Magic SDR Player API")

    os.makedirs(STATIC_DIR, exist_ok=True)
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Live subscribers for the WebSocket audio stream
    audio_subs: List[WebSocket] = []
    spectrum_subs: List[WebSocket] = []
    event_subs: List[WebSocket] = []

    # ----------------------------- routes -----------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {
            "request": request,
            "bands": [b.name for b in BANDS],
        })

    @app.get("/api/state")
    async def get_state():
        return JSONResponse(get_state_fn())

    @app.post("/api/tune")
    async def tune(req: TuneReq):
        if not gqrx.is_connected():
            raise HTTPException(503, "Gqrx not connected")
        mod = req.modulation or guess_modulation(req.freq_hz)
        if not gqrx.set_modulation(mod):
            raise HTTPException(500, "Failed to set modulation")
        if not gqrx.set_frequency(req.freq_hz):
            raise HTTPException(500, "Failed to set frequency")
        return {"ok": True, "freq_hz": req.freq_hz, "modulation": mod}

    @app.post("/api/modulation")
    async def set_mod(req: ModReq):
        if not gqrx.set_modulation(req.modulation):
            raise HTTPException(500, "Failed to set modulation")
        return {"ok": True, "modulation": req.modulation}

    @app.post("/api/volume")
    async def set_volume(req: VolumeReq):
        # Volume is handled by the AudioPlayer, exposed via state callback
        # We dispatch through the event bus
        for ws in event_subs:
            try:
                await ws.send_json({"type": "volume", "value": req.volume})
            except Exception:
                pass
        return {"ok": True, "volume": req.volume}

    @app.post("/api/mute")
    async def set_mute(req: MuteReq):
        for ws in event_subs:
            try:
                await ws.send_json({"type": "mute", "value": req.muted})
            except Exception:
                pass
        return {"ok": True, "muted": req.muted}

    @app.post("/api/record")
    async def record(req: RecordReq):
        if req.action == "start":
            state = get_state_fn()
            if not recordings.start_recording(state["freq_hz"], state["modulation"],
                                              label=state.get("label")):
                raise HTTPException(500, "Could not start recording")
            return {"ok": True, "recording": True}
        elif req.action == "stop":
            path = recordings.stop_recording()
            return {"ok": True, "recording": False, "path": path}
        raise HTTPException(400, "action must be 'start' or 'stop'")

    @app.get("/api/bookmarks")
    async def list_bookmarks():
        return [b.to_dict() for b in bookmarks.list_all()]

    @app.get("/api/bookmarks/{band}")
    async def list_bookmarks_by_band(band: str):
        return [b.to_dict() for b in bookmarks.list_by_band(band)]

    @app.post("/api/bookmarks")
    async def add_bookmark(req: BookmarkReq):
        b = bookmarks.add(freq_hz=req.freq_hz, label=req.label,
                          modulation=req.modulation, tags=req.tags,
                          notes=req.notes or "")
        return b.to_dict()

    @app.delete("/api/bookmarks/{freq}")
    async def remove_bookmark(freq: int):
        ok = bookmarks.remove(freq)
        return {"ok": ok}

    @app.post("/api/scan")
    async def scan(req: ScanReq):
        if req.band.lower() == "all":
            ok = scanner.scan_all_bands()
        else:
            ok = scanner.scan_band_by_name(req.band)
        if not ok:
            raise HTTPException(409, "Scanner already running or invalid band")
        return {"ok": True, "band": req.band}

    @app.post("/api/scan/stop")
    async def scan_stop():
        scanner.stop()
        return {"ok": True}

    @app.get("/api/recordings")
    async def list_recordings():
        return recordings.list_recordings()

    @app.get("/api/recordings/file")
    async def download_recording(path: str):
        if not os.path.isfile(path) or not path.startswith(RECORDINGS_DIR):
            raise HTTPException(404, "Recording not found")
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/ai/tag/{freq}")
    async def ai_tag(freq: int, req: AITagReq = AITagReq()):
        if not ai_tagger.is_available():
            raise HTTPException(503, "AI tagger unavailable (node helper not found)")
        b = band_for_frequency(freq)
        # Run sync classification — for short clips this is acceptable
        tag = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ai_tagger.classify_sync(freq, band=b, duration_s=req.duration_s)
        )
        if tag is None:
            raise HTTPException(500, "AI classification failed")
        # Persist tag to bookmark if exists
        if bookmarks.get(freq):
            bookmarks.add(freq_hz=freq, ai_tag=tag.get("summary") or tag.get("signal_type"))
        return tag

    @app.get("/api/bands")
    async def list_bands():
        return [{
            "name": b.name,
            "start_mhz": b.start_mhz,
            "end_mhz": b.end_mhz,
            "modulation": b.modulation,
            "step_khz": b.step_khz,
            "description": b.description,
            "known_count": len(b.known),
        } for b in BANDS]

    # ----------------------------- WebSocket endpoints -----------------------------
    @app.websocket("/ws/audio")
    async def ws_audio(ws: WebSocket):
        """Stream live PCM audio to the browser as base64-encoded int16 frames.

        The browser decodes and feeds a Web Audio API AudioBufferSourceNode.
        """
        await ws.accept()
        audio_subs.append(ws)
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        def on_chunk(chunk, sr, ch):
            try:
                # Encode as int16 little-endian base64
                payload = {
                    "sample_rate": sr,
                    "channels": ch,
                    "data": base64.b64encode(chunk.tobytes()).decode("ascii"),
                }
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception:
                pass

        loop = asyncio.get_event_loop()
        audio_receiver.chunk_ready.connect(on_chunk)
        try:
            while True:
                payload = await queue.get()
                await ws.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("ws_audio error: %s", e)
        finally:
            try:
                audio_receiver.chunk_ready.disconnect(on_chunk)
            except Exception:
                pass
            if ws in audio_subs:
                audio_subs.remove(ws)

    @app.websocket("/ws/spectrum")
    async def ws_spectrum(ws: WebSocket):
        """Stream live spectrum data (JSON: {center_hz, span_hz, bins: [...]})"""
        await ws.accept()
        spectrum_subs.append(ws)
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)

        def on_spec(data, center_hz, span_hz):
            try:
                # Downsample to max 256 bins for the web UI
                if len(data) > 256:
                    idx = np.linspace(0, len(data) - 1, 256).astype(int)
                    data = data[idx]
                payload = {
                    "center_hz": int(center_hz),
                    "span_hz": int(span_hz),
                    "bins": data.tolist(),
                }
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception:
                pass

        loop = asyncio.get_event_loop()
        spectrum_receiver.spectrum_ready.connect(on_spec)
        try:
            while True:
                payload = await queue.get()
                await ws.send_json(payload)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("ws_spectrum error: %s", e)
        finally:
            try:
                spectrum_receiver.spectrum_ready.disconnect(on_spec)
            except Exception:
                pass
            if ws in spectrum_subs:
                spectrum_subs.remove(ws)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        """Stream state-change events."""
        await ws.accept()
        event_subs.append(ws)
        last_emit = 0.0
        try:
            while True:
                # Poll state every 0.5s
                await asyncio.sleep(0.5)
                state = get_state_fn()
                await ws.send_json({"type": "state", "state": state})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("ws_events error: %s", e)
        finally:
            if ws in event_subs:
                event_subs.remove(ws)

    return app


class WebServer:
    """Wraps FastAPI + uvicorn so it can be started/stopped from the GUI."""

    def __init__(self, app_factory_args, host: str = WEB_HOST, port: int = WEB_PORT):
        self.app = create_app(**app_factory_args)
        self.host = host
        self.port = port
        self._server = None
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = None

    def start(self) -> bool:
        import uvicorn
        try:
            config = uvicorn.Config(self.app, host=self.host, port=self.port,
                                    log_level="warning", lifespan="on")
            self._server = uvicorn.Server(config)
            def run():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._server.serve())
            import threading
            self._thread = threading.Thread(target=run, daemon=True, name="WebServer")
            self._thread.start()
            log.info("Web server started on http://%s:%d", self.host, self.port)
            return True
        except Exception as e:
            log.error("Failed to start web server: %s", e)
            return False

    def stop(self) -> None:
        if self._server:
            try:
                self._server.should_exit = True
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
