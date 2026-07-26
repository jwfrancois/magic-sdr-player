"""Functional smoke test for Magic SDR Player.

Runs from /home/z/my-project so the package is importable.
Tests instantiation of each major component without a real Gqrx/SDR.
"""
import sys, os, socket, threading, time, json, tempfile, logging

sys.path.insert(0, "/home/z/my-project")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

errors = []

def check(name, fn):
    try:
        fn()
        print(f"  OK  {name}")
    except Exception as e:
        import traceback
        errors.append((name, e, traceback.format_exc()))
        print(f"  FAIL {name}: {type(e).__name__}: {e}")

# ----------------------------- band_presets -----------------------------
def test_band_lookup():
    from magic_sdr.band_presets import band_for_frequency, lookup_known, guess_modulation
    assert band_for_frequency(96_900_000).name == "FM Broadcast"
    assert band_for_frequency(121_500_000).name == "Aviation (Airband)"
    assert band_for_frequency(162_550_000).name == "NOAA Weather Radio"
    assert band_for_frequency(146_520_000).name == "2m Amateur (Ham)"
    assert band_for_frequency(156_800_000).name == "Marine VHF"
    assert band_for_frequency(14_200_000).name == "Shortwave (HF)"
    assert band_for_frequency(500_000_000) is None  # not in any band
    assert lookup_known(121_500_000) == "EMERGENCY Guard 121.5"
    assert lookup_known(162_400_000).startswith("NOAA WX-1")
    assert lookup_known(96_900_000) is None  # local FM station, not in known table
    assert guess_modulation(96_900_000) == "WFM_ST"
    assert guess_modulation(121_500_000) == "AM"
    assert guess_modulation(14_200_000) == "AM"

check("band_presets.lookups", test_band_lookup)

# ----------------------------- config -----------------------------
def test_config():
    from magic_sdr.config import Config
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    # Monkey-patch the module-level CONFIG_FILE
    import magic_sdr
    orig = magic_sdr.CONFIG_FILE
    magic_sdr.CONFIG_FILE = tmp.name
    # Also patch the import inside config module
    import magic_sdr.config as cfg_mod
    cfg_mod.CONFIG_FILE = tmp.name
    c = Config.load()
    assert c.gqrx_port == 7356
    c.last_frequency_hz = 100_000_000
    c.save()
    c2 = Config.load()
    assert c2.last_frequency_hz == 100_000_000
    magic_sdr.CONFIG_FILE = orig
    cfg_mod.CONFIG_FILE = orig
    os.unlink(tmp.name)

check("config.load_save", test_config)

# ----------------------------- GqrxClient (mock server) -----------------------------
def test_gqrx_client():
    """Spin up a tiny mock Gqrx TCP server and exercise the client."""
    from magic_sdr.gqrx_client import GqrxClient

    # Mock server: respond to F, f, M, m, l STRENGTH
    def mock_server(port_holder):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port_holder.append(srv.getsockname()[1])
        # Wake up the main thread
        conn, _ = srv.accept()
        try:
            buf = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode().strip()
                    if cmd == "\\dump_state":
                        # Reply with a minimal state dump
                        conn.sendall(b"Version: 1\nRPRT 0\n")
                    elif cmd.startswith("F "):
                        freq = int(cmd.split()[1])
                        port_holder.append(freq)
                        conn.sendall(b"RPRT 0\n")
                    elif cmd == "f":
                        conn.sendall(b"96900000\nRPRT 0\n")
                    elif cmd.startswith("M "):
                        conn.sendall(b"RPRT 0\n")
                    elif cmd == "m":
                        conn.sendall(b"WFM_ST\nRPRT 0\n")
                    elif cmd == "l STRENGTH":
                        conn.sendall(b"-32.5\nRPRT 0\n")
                    elif cmd == "AOS" or cmd == "LOS":
                        conn.sendall(b"RPRT 0\n")
                    else:
                        conn.sendall(b"RPRT 0\n")
        finally:
            conn.close()
            srv.close()

    port_holder = []
    t = threading.Thread(target=mock_server, args=(port_holder,), daemon=True)
    t.start()
    # Wait for port to be assigned
    for _ in range(50):
        if port_holder:
            break
        time.sleep(0.05)
    port = port_holder[0]

    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])

    client = GqrxClient(host="127.0.0.1", port=port)
    # Disable the poller for this test (it would interfere)
    ok = client.connect()
    assert ok, "Should connect to mock server"
    assert client.set_frequency(96_900_000), "set_frequency should succeed"
    # Wait briefly for the request to be sent
    time.sleep(0.1)
    # Check the server saw the F command
    assert any(isinstance(x, int) and x == 96_900_000 for x in port_holder[1:]), \
        f"Server should have received F 96900000, got {port_holder[1:]}"
    # Test get_frequency
    f = client.get_frequency()
    assert f == 96_900_000, f"get_frequency should return 96900000, got {f}"
    # Test set_modulation
    assert client.set_modulation("WFM_ST"), "set_modulation should succeed"
    # Test get_modulation
    m = client.get_modulation()
    assert m == "WFM_ST", f"get_modulation should return WFM_ST, got {m}"
    # Test start_recording
    assert client.start_recording(), "start_recording should succeed"
    assert client.stop_recording(), "stop_recording should succeed"
    client.disconnect()

check("gqrx_client.basic", test_gqrx_client)

# ----------------------------- BookmarkManager -----------------------------
def test_bookmarks():
    from magic_sdr.bookmark_manager import BookmarkManager
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    tmp.write("[]")
    tmp.close()
    bm = BookmarkManager(path=tmp.name)
    # Should have seeded defaults
    seeded = bm.list_all()
    assert len(seeded) > 10, f"Should seed defaults, got {len(seeded)}"
    # Add a custom bookmark
    bm.add(freq_hz=96_900_000, label="My FM Station", tags=["music"])
    b = bm.get(96_900_000)
    assert b is not None
    assert b.label == "My FM Station"
    assert "music" in b.tags
    # Search
    results = bm.search("music")
    assert any(b.freq_hz == 96_900_000 for b in results)
    # Remove
    assert bm.remove(96_900_000)
    assert bm.get(96_900_000) is None
    os.unlink(tmp.name)

check("bookmark_manager.crud", test_bookmarks)

# ----------------------------- AudioReceiver (loopback) -----------------------------
def test_audio_receiver():
    """Send a fake audio packet to the receiver and check it emits."""
    from magic_sdr.audio_receiver import AudioReceiver
    from PyQt5.QtCore import QCoreApplication, QEventLoop
    app = QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()

    receiver = AudioReceiver(port=0)  # let OS assign
    # Bind manually to a random port
    import socket as s
    sock = s.socket(s.AF_INET, s.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    receiver.port = port

    received = []
    def on_chunk(chunk, sr, ch):
        received.append((chunk, sr, ch))
        loop.quit()

    receiver.chunk_ready.connect(on_chunk)
    assert receiver.start(), "AudioReceiver should start"
    # Send a fake packet
    import numpy as np
    data = np.zeros(512, dtype=np.int16).tobytes()
    sender = s.socket(s.AF_INET, s.SOCK_DGRAM)
    sender.sendto(data, ("127.0.0.1", port))
    sender.close()
    # Wait briefly
    timer = receiver.startTimer(500)
    # Use a Python-side timeout instead of Qt event loop
    import time as t
    deadline = t.time() + 1.0
    while not received and t.time() < deadline:
        app.processEvents()
        t.sleep(0.05)
    receiver.stop()
    assert received, "AudioReceiver should have received the packet"
    chunk, sr, ch = received[0]
    assert sr == 48000
    assert ch == 2

check("audio_receiver.packet", test_audio_receiver)

# ----------------------------- RecordingManager -----------------------------
def test_recording():
    from magic_sdr.recording_manager import RecordingManager
    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    rm = RecordingManager()
    import tempfile, os
    tmpdir = tempfile.mkdtemp()
    # Patch RECORDINGS_DIR
    import magic_sdr
    orig_dir = magic_sdr.RECORDINGS_DIR
    magic_sdr.RECORDINGS_DIR = tmpdir
    import magic_sdr.recording_manager as rm_mod
    rm_mod.RECORDINGS_DIR = tmpdir
    os.makedirs(tmpdir, exist_ok=True)
    import numpy as np
    ok = rm.start_recording(96_900_000, "WFM_ST", label="Test")
    assert ok, "start_recording should succeed"
    # Write some chunks
    for _ in range(10):
        chunk = (np.random.randn(1024, 2) * 1000).astype(np.int16)
        rm.write_chunk(chunk, 48000, 2, signal_level_db=-30.0)
    path = rm.stop_recording()
    assert path is not None, "stop_recording should return a path"
    assert os.path.exists(path), f"WAV file should exist at {path}"
    # Check metadata JSON
    meta = path.replace(".wav", ".json")
    assert os.path.exists(meta)
    with open(meta) as f:
        m = json.load(f)
    assert m["freq_hz"] == 96_900_000
    assert m["duration_s"] > 0
    assert m["peak_level_db"] >= -120
    magic_sdr.RECORDINGS_DIR = orig_dir
    rm_mod.RECORDINGS_DIR = orig_dir

check("recording_manager.basic", test_recording)

# ----------------------------- AITagger (graceful failure) -----------------------------
def test_ai_tagger():
    """AI tagger should gracefully return None when the helper is unavailable."""
    from magic_sdr.ai_tagger import AITagger, compute_audio_features
    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    tagger = AITagger(helper_path="/nonexistent/path")
    assert not tagger.is_available(), "Should report unavailable with bad path"
    result = tagger.classify_sync(96_900_000)
    assert result is None, "Should return None when unavailable"
    # Test feature computation
    import numpy as np
    chunk = (np.random.randn(2048) * 1000).astype(np.int16)
    feats = compute_audio_features(chunk, 48000)
    assert "spectral_centroid_hz" in feats
    assert "rms" in feats

check("ai_tagger.graceful_failure", test_ai_tagger)

# ----------------------------- WaterfallWidget (instantiation) -----------------------------
def test_waterfall_widget():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.spectrum import WaterfallWidget
    import numpy as np
    w = WaterfallWidget()
    w.set_band_context(96_900_000, 2_000_000)
    # Push a spectrum update
    data = (np.random.rand(512) * 50 - 80).astype(np.float32)
    w.update_spectrum(data, 96_900_000, 2_000_000)
    w.set_tune_marker(96_900_000)

check("waterfall_widget.instantiate", test_waterfall_widget)

# ----------------------------- MainWindow (instantiation) -----------------------------
def test_main_window():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from magic_sdr.config import Config
    from magic_sdr.main_window import MainWindow
    # Use a temp config file so we don't pollute the real one
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    import magic_sdr
    orig = magic_sdr.CONFIG_FILE
    magic_sdr.CONFIG_FILE = tmp.name
    import magic_sdr.config as cfg_mod
    cfg_mod.CONFIG_FILE = tmp.name
    # Don't auto-start web server for the test
    c = Config.load()
    c.remote_access_enabled = False
    win = MainWindow(c)
    win.show()
    app.processEvents()
    win.close()
    magic_sdr.CONFIG_FILE = orig
    cfg_mod.CONFIG_FILE = orig
    os.unlink(tmp.name)

check("main_window.instantiate", test_main_window)

# ----------------------------- WebServer (creation only, not started) -----------------------------
def test_web_server_factory():
    from PyQt5.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    from magic_sdr.web_server import create_app
    from magic_sdr.gqrx_client import GqrxClient
    from magic_sdr.bookmark_manager import BookmarkManager
    from magic_sdr.recording_manager import RecordingManager
    from magic_sdr.band_scanner import BandScanner
    from magic_sdr.ai_tagger import AITagger
    from magic_sdr.audio_receiver import AudioReceiver
    from magic_sdr.spectrum import SpectrumReceiver

    gqrx = GqrxClient()
    audio = AudioReceiver()
    spec = SpectrumReceiver()
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    tmp.write("[]"); tmp.close()
    bm = BookmarkManager(path=tmp.name)
    rec = RecordingManager()
    scan = BandScanner(gqrx)
    ai = AITagger()
    fapp = create_app(
        gqrx=gqrx, bookmarks=bm, recordings=rec, scanner=scan, ai_tagger=ai,
        audio_receiver=audio, spectrum_receiver=spec,
        get_state_fn=lambda: {"freq_hz": 96900000, "modulation": "WFM_ST"},
    )
    assert fapp is not None
    os.unlink(tmp.name)

check("web_server.factory", test_web_server_factory)

print()
if errors:
    print(f"FAILED: {len(errors)} tests failed")
    for name, e, tb in errors:
        print(f"\n--- {name} ---")
        print(tb)
    sys.exit(1)
print("All functional tests passed.")
