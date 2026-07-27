"""Test the BandScanner with poller pause/resume and robust level sampling."""
import sys
import time
import threading
import socket
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Headless CI / sandbox — use offscreen Qt platform
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

# Need a QApplication for Qt cross-thread signal delivery to work
from PyQt5.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from magic_sdr.gqrx_client import GqrxClient
from magic_sdr.band_scanner import BandScanner
from magic_sdr.band_presets import BANDS_BY_NAME


class MockGqrx:
    """Minimal Gqrx TCP server that responds to F/M/f/m/l STRENGTH."""

    def __init__(self, port):
        self.port = port
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(('127.0.0.1', port))
        self.srv.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.running = True
        self.thread.start()
        self.commands_received = []
        self.current_freq = 96_900_000
        self.current_mod = 'WFM_ST'

    def _serve(self):
        try:
            conn, _ = self.srv.accept()
            conn.settimeout(0.5)
            buf = b''
            while self.running:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        cmd = line.decode().strip()
                        self.commands_received.append(cmd)
                        resp = self._respond(cmd)
                        if resp is not None:
                            conn.sendall(resp)
                except socket.timeout:
                    continue
                except OSError:
                    break
        except Exception as e:
            print(f'MockGqrx error: {e}')

    def _respond(self, cmd):
        if cmd == 'f':
            return f'{self.current_freq}\n'.encode()
        if cmd == 'm':
            return f'{self.current_mod}\n'.encode()
        if cmd == 'l STRENGTH':
            # Simulate signal at certain frequencies
            if 96_000_000 <= self.current_freq <= 97_000_000:
                return b'-42.5\n'
            if 98_000_000 <= self.current_freq <= 99_000_000:
                return b'-55.0\n'
            return b'-95.0\n'
        if cmd.startswith('F '):
            try:
                self.current_freq = int(cmd.split()[1])
            except Exception:
                pass
            return b'RPRT 0\n'
        if cmd.startswith('M '):
            try:
                self.current_mod = cmd.split()[1]
            except Exception:
                pass
            return b'RPRT 0\n'
        # All other commands (dump_state, L SQL/L RF, etc.)
        return b'RPRT 0\n'

    def stop(self):
        self.running = False
        try:
            self.srv.close()
        except Exception:
            pass


def main():
    # Find a free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()

    mock = MockGqrx(port)
    time.sleep(0.1)

    gc = GqrxClient(host='127.0.0.1', port=port)
    ok = gc.connect(timeout=2.0)
    print(f'Connect: {ok}')
    assert ok, 'failed to connect to mock gqrx'

    # Wait for poller to start
    time.sleep(0.6)
    poller_alive = gc._poller is not None and gc._poller.is_alive()
    print(f'Poller alive after connect: {poller_alive}')
    assert poller_alive, 'poller should be running after connect'

    # Pause poller
    gc.pause_poller()
    time.sleep(0.2)
    poller_alive = gc._poller is not None and gc._poller.is_alive()
    print(f'Poller alive after pause:   {poller_alive}')
    assert not poller_alive, 'poller should be stopped after pause_poller()'

    # Test signal level parsing — should NOT return None for our mock responses
    lvl = gc.get_signal_level()
    print(f'Signal level at 96.9 MHz: {lvl}')
    assert lvl == -42.5, f'expected -42.5, got {lvl}'

    # Test robust sampling (max of 3 samples)
    lvl_robust = gc.get_signal_level_robust(n_samples=3, interval_s=0.02)
    print(f'Robust signal level: {lvl_robust}')
    assert lvl_robust == -42.5

    # Run a small scan
    scanner = BandScanner(gc)
    scanner.threshold_db = -90  # very permissive
    scanner.auto_threshold = False
    scanner.dwell_s = 0.05

    results = []
    done_event = threading.Event()

    def on_found(st):
        results.append(st)

    def on_finished(name, found):
        done_event.set()

    scanner.station_found.connect(on_found)
    scanner.scan_finished.connect(on_finished)

    # Fake band: 96.0 - 99.0 MHz, 200 kHz step
    class FakeBand:
        name = 'FM Broadcast'
        start_mhz = 96.0
        end_mhz = 99.0
        step_khz = 200
        modulation = 'WFM_ST'

    scanner.scan_band(FakeBand())
    # Run a Qt event loop while waiting so cross-thread signals get delivered
    start = time.time()
    while not done_event.is_set() and time.time() - start < 5.0:
        app.processEvents()
        time.sleep(0.01)

    print(f'\nStations found: {len(results)}')
    for st in results:
        print(f'  {st.freq_hz/1e6:.4f} MHz @ {st.level_db:.1f} dBFS')

    # We expect stations at 96.0, 96.2, 96.4, ..., 96.8, 96.9~97.0 (96.9 is between),
    # and 98.0, 98.2, ..., 98.8 — basically all freqs in 96-97 and 98-99 ranges
    assert len(results) >= 5, f'expected >=5 stations, got {len(results)}'

    found_freqs = sorted(r.freq_hz for r in results)
    print(f'Found freqs: {[f/1e6 for f in found_freqs]}')

    # At least one in 96-97 MHz range
    assert any(96_000_000 <= f <= 97_000_000 for f in found_freqs)
    # At least one in 98-99 MHz range
    assert any(98_000_000 <= f <= 99_000_000 for f in found_freqs)
    # None in 97-98 MHz range (no signal there)
    assert not any(97_500_000 <= f <= 97_800_000 for f in found_freqs)

    # Resume poller
    gc.resume_poller()
    time.sleep(0.6)
    poller_alive = gc._poller is not None and gc._poller.is_alive()
    print(f'\nPoller alive after resume:  {poller_alive}')
    assert poller_alive, 'poller should be running after resume_poller()'

    print('\n✓ All scanner health tests passed!')

    gc.disconnect()
    mock.stop()


if __name__ == '__main__':
    main()
