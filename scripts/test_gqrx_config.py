"""Test the new gqrx_config.py module + the updated main_window integration.

Tests:
1. setup_gqrx_config writes a known-good config to a temp path
2. setup_gqrx_config preserves existing keys when merging
3. setup_gqrx_config backs up the existing config
4. setup_gqrx_config is idempotent (no changes the second time)
5. inspect_gqrx_config returns a readable summary
6. main_window imports cleanly with the new code paths
"""

import sys
import os
import tempfile
import shutil
from pathlib import Path

# Add project to path
sys.path.insert(0, '/home/z/my-project')

# Set offscreen Qt for headless test
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def test_setup_writes_known_good_config():
    """Test 1: setup_gqrx_config writes a known-good config from scratch."""
    from magic_sdr.gqrx_config import setup_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "default.conf"
        result = setup_gqrx_config(config_path=cfg_path)
        assert result.ok, f"Expected ok=True, got: {result.message}"
        assert cfg_path.exists(), "Config file was not written"
        # Read it back
        import configparser
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(cfg_path)
        assert cp.get("remote_control", "enabled") == "true"
        assert cp.get("remote_control", "port") == "7356"
        assert cp.get("audio_udp", "enabled") == "true"
        assert cp.get("audio_udp", "host") == "127.0.0.1"
        assert cp.get("audio_udp", "port") == "7355"
        assert cp.get("audio_udp", "sample_rate") == "48000"
        assert cp.get("audio_udp", "stereo") == "true"
        # Should have recorded all the changes (none existed before)
        sections_changed = {(s, k) for s, k, _, _ in result.changes}
        assert ("remote_control", "enabled") in sections_changed
        assert ("audio_udp", "enabled") in sections_changed
        # No backup because file didn't exist
        assert result.backup_path is None
        print("✓ Test 1 passed: setup_gqrx_config writes known-good config from scratch")


def test_setup_preserves_existing_keys():
    """Test 2: setup_gqrx_config preserves existing keys (dongle, gain, etc.)."""
    from magic_sdr.gqrx_config import setup_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "default.conf"
        # Write an existing config with user settings we must preserve
        cfg_path.write_text(
            "[input]\n"
            "device = rtl\n"
            "sample_rate = 2400000\n"
            "gain = 40\n"
            "\n"
            "[receiver]\n"
            "demod = WFM_STEREO\n"
            "frequency = 96900000\n"
            "\n"
            "[bookmarks]\n"
            "1\\name = My Station\n"
            "1\\frequency = 101100000\n"
            "\n"
            "[remote_control]\n"
            "enabled = false\n"
            "port = 7356\n"
        )
        result = setup_gqrx_config(config_path=cfg_path)
        assert result.ok, f"Expected ok=True, got: {result.message}"
        # Read it back
        import configparser
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(cfg_path)
        # User's input section must be preserved
        assert cp.get("input", "device") == "rtl"
        assert cp.get("input", "sample_rate") == "2400000"
        assert cp.get("input", "gain") == "40"
        # User's receiver section preserved
        assert cp.get("receiver", "demod") == "WFM_STEREO"
        assert cp.get("receiver", "frequency") == "96900000"
        # User's bookmarks preserved
        assert cp.get("bookmarks", "1\\name") == "My Station"
        # remote_control.enabled was flipped from false to true
        assert cp.get("remote_control", "enabled") == "true"
        # audio_udp section was added (didn't exist before)
        assert cp.get("audio_udp", "enabled") == "true"
        assert cp.get("audio_udp", "port") == "7355"
        # Backup was created
        assert result.backup_path is not None
        assert Path(result.backup_path).exists()
        # Backup should still have enabled = false (the original value)
        cp_bak = configparser.ConfigParser(interpolation=None)
        cp_bak.read(result.backup_path)
        assert cp_bak.get("remote_control", "enabled") == "false"
        print("✓ Test 2 passed: setup_gqrx_config preserves existing keys + creates backup")


def test_setup_idempotent():
    """Test 3: Calling setup_gqrx_config twice = no changes the second time."""
    from magic_sdr.gqrx_config import setup_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "default.conf"
        # First call: writes everything
        r1 = setup_gqrx_config(config_path=cfg_path)
        assert r1.ok
        assert len(r1.changes) > 0, "First call should have changes"
        first_backup = r1.backup_path
        # Second call: should detect no changes needed
        r2 = setup_gqrx_config(config_path=cfg_path)
        assert r2.ok
        assert len(r2.changes) == 0, f"Second call should be no-op, but got changes: {r2.changes}"
        # Backup was still created (since file existed) — that's fine, it's a safety backup
        assert r2.backup_path is not None
        assert r2.backup_path != first_backup, "Each call should create its own backup"
        assert "no changes needed" in r2.message.lower() or "already correct" in r2.message.lower()
        print("✓ Test 3 passed: setup_gqrx_config is idempotent (2nd call = no changes)")


def test_inspect_config_returns_readable_summary():
    """Test 4: inspect_gqrx_config returns a readable summary."""
    from magic_sdr.gqrx_config import inspect_gqrx_config, setup_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "default.conf"
        # Write a known-good config first
        setup_gqrx_config(config_path=cfg_path)
        # Inspect it
        report = inspect_gqrx_config(config_path=cfg_path)
        assert "[remote_control]" in report
        assert "[audio_udp]" in report
        assert "enabled = true" in report
        # The inspect output uses aligned columns, so look for the key + value
        # separately rather than "port = 7356".
        assert "port" in report
        assert "7356" in report
        assert "7355" in report
        # Should show ✓ marks for matching values
        assert "✓" in report
        print("✓ Test 4 passed: inspect_gqrx_config returns readable summary")


def test_inspect_missing_config():
    """Test 5: inspect_gqrx_config gracefully handles a missing config file."""
    from magic_sdr.gqrx_config import inspect_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "does-not-exist.conf"
        report = inspect_gqrx_config(config_path=cfg_path)
        assert "not found" in report.lower()
        assert "Launch Gqrx once" in report or "Setup Gqrx config" in report
        print("✓ Test 5 passed: inspect_gqrx_config gracefully handles missing file")


def test_setup_with_broken_config():
    """Test 6: setup_gqrx_config reports an error when existing config is unparseable."""
    from magic_sdr.gqrx_config import setup_gqrx_config
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "default.conf"
        # Write a malformed INI file
        cfg_path.write_text("this is not valid INI = [unterminated\n[bad section\n")
        result = setup_gqrx_config(config_path=cfg_path)
        assert not result.ok, "Expected ok=False for malformed INI"
        assert "could not parse" in result.message.lower() or "parse" in result.message.lower()
        print("✓ Test 6 passed: setup_gqrx_config reports error on malformed config")


def test_main_window_imports_cleanly():
    """Test 7: main_window imports cleanly with the new gqrx_config integration."""
    import importlib
    # Just importing main_window exercises the new code paths
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from magic_sdr import main_window
        importlib.reload(main_window)
        print("✓ Test 7 passed: main_window imports cleanly with gqrx_config integration")
    except Exception as e:
        print(f"✗ Test 7 FAILED: {e}")
        raise


def test_main_window_instantiates_with_settings_tab():
    """Test 8: MainWindow instantiates with the new 'Setup Gqrx config' button."""
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from magic_sdr.main_window import MainWindow
    from magic_sdr.config import Config
    # MainWindow requires a Config instance
    cfg = Config()
    mw = MainWindow(config=cfg)
    # The Setup Gqrx config button must exist
    assert hasattr(mw, 'gqrx_cfg_btn'), "Missing gqrx_cfg_btn attribute"
    assert hasattr(mw, 'gqrx_inspect_btn'), "Missing gqrx_inspect_btn attribute"
    # The _setup_gqrx_config method must exist
    assert hasattr(mw, '_setup_gqrx_config'), "Missing _setup_gqrx_config method"
    assert hasattr(mw, '_inspect_gqrx_config'), "Missing _inspect_gqrx_config method"
    # The button text must contain "Setup"
    assert "Setup" in mw.gqrx_cfg_btn.text(), f"Button text wrong: {mw.gqrx_cfg_btn.text()}"
    print("✓ Test 8 passed: MainWindow has Setup Gqrx config button + handlers")


def main():
    print("Running gqrx_config + main_window integration tests...\n")
    tests = [
        test_setup_writes_known_good_config,
        test_setup_preserves_existing_keys,
        test_setup_idempotent,
        test_inspect_config_returns_readable_summary,
        test_inspect_missing_config,
        test_setup_with_broken_config,
        test_main_window_imports_cleanly,
        test_main_window_instantiates_with_settings_tab,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*60}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
