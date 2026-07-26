"""Gqrx configuration helper.

Writes a known-good ``~/.config/gqrx/default.conf`` so that Gqrx launches
already configured to talk to Magic SDR:

  * Remote control TCP enabled on 127.0.0.1:7356
  * Audio UDP stream enabled to 127.0.0.1:7355 (48 kHz, stereo, int16 PCM)

Gqrx does NOT support a UDP spectrum stream in stock builds, so we don't
try to enable one. Magic SDR falls back to an audio-FFT waterfall.

Why this exists
---------------
Gqrx's GUI menus for enabling Audio UDP and Remote Control vary across
versions (and sometimes the Audio UDP menu item is missing entirely).
Rather than send the user on a UI hunt, we can write the config file
directly. The user just needs to **quit Gqrx, click "Setup Gqrx config"
in Magic SDR's Settings tab, then re-launch Gqrx**.

Safety
------
* We never destroy the existing config — we back it up to
  ``default.conf.bak-<timestamp>`` first.
* We merge keys into the existing file instead of overwriting it, so
  the user's dongle, bookmarks, gain, demodulator settings etc. are
  preserved.
* We use :mod:`configparser` which is compatible with Gqrx's
  QSettings-INI format (no Qt lockfile is held because Gqrx is quit
  before the write).
"""

from __future__ import annotations

import configparser
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Where Gqrx stores its config on Linux. (On macOS it's a different path
# under ~/Library/Preferences; we don't support that here — Gqrx on macOS
# uses QSettings via plist, not INI.)
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "gqrx" / "default.conf"


@dataclass
class GqrxConfigResult:
    """Outcome of a setup_gqrx_config() call."""
    ok: bool
    config_path: str
    backup_path: Optional[str]
    message: str
    # What we changed: list of (section, key, old_value, new_value)
    changes: list[tuple[str, str, str, str]]


def find_config_path() -> Path:
    """Return the Gqrx config path. Uses $XDG_CONFIG_HOME if set."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "gqrx" / "default.conf"
    return DEFAULT_CONFIG_PATH


def setup_gqrx_config(
    config_path: Optional[Path] = None,
    *,
    remote_port: int = 7356,
    audio_port: int = 7355,
    audio_host: str = "127.0.0.1",
) -> GqrxConfigResult:
    """Write a known-good Gqrx config, merging into the existing file.

    Steps:
      1. Resolve the path (default ~/.config/gqrx/default.conf).
      2. If it exists, back it up to ``default.conf.bak-<unix-time>``.
      3. Read existing keys (preserves user's dongle, gain, bookmarks…).
      4. Force-set:
           [remote_control] enabled = true, port = 7356, host = ""
           [audio_udp]      enabled = true, port = 7355, host = "127.0.0.1"
      5. Write back.

    The caller MUST ensure Gqrx is NOT running when this is called —
    otherwise Gqrx will overwrite our changes on its next save.

    Returns a :class:`GqrxConfigResult` with the path, backup path, and a
    human-readable message.
    """
    path = config_path or find_config_path()
    changes: list[tuple[str, str, str, str]] = []

    # Gqrx writes its config with Qt's QSettings which uses INI format,
    # but QSettings' INI writer is picky about quotes/escapes for some
    # types. configparser reads it back fine. For the keys we set
    # (enabled, port, host), the values are simple strings/ints, so
    # there are no escaping issues.
    cp = configparser.ConfigParser(interpolation=None)
    # Preserve case of keys (Gqrx uses lowercase, but be safe).
    cp.optionxform = str  # type: ignore[assignment]

    if path.exists():
        try:
            cp.read(path, encoding="utf-8")
        except configparser.Error as e:
            return GqrxConfigResult(
                ok=False,
                config_path=str(path),
                backup_path=None,
                message=f"Could not parse existing Gqrx config at {path}: {e}",
                changes=[],
            )
        # Back it up.
        backup = path.with_name(f"{path.name}.bak-{int(time.time())}")
        try:
            shutil.copy2(path, backup)
        except OSError as e:
            return GqrxConfigResult(
                ok=False,
                config_path=str(path),
                backup_path=None,
                message=f"Could not back up existing config: {e}",
                changes=[],
            )
        backup_path = str(backup)
    else:
        backup_path = None

    # Ensure sections exist.
    if not cp.has_section("remote_control"):
        cp.add_section("remote_control")
    if not cp.has_section("audio_udp"):
        cp.add_section("audio_udp")

    # Helper that records a change only if the value differs.
    def _set(section: str, key: str, new_val: str) -> None:
        old_val = cp.get(section, key, fallback="")
        if old_val != new_val:
            changes.append((section, key, old_val, new_val))
        cp.set(section, key, new_val)

    # [remote_control] — Gqrx's keys (verified against gqrx 2.15 / 2.16):
    #   enabled  = true|false
    #   port     = int
    #   host     = ""  (empty = listen on all interfaces; we keep whatever
    #                   the user had, only set if missing)
    _set("remote_control", "enabled", "true")
    _set("remote_control", "port", str(remote_port))
    # Gqrx's [remote_control] host key: empty string = listen on all
    # interfaces. We only set it if the key is ABSENT — once it exists
    # (even with empty value), we leave it alone so we don't repeatedly
    # record a "change" on every run.
    if not cp.has_option("remote_control", "host"):
        cp.set("remote_control", "host", "")
        changes.append(("remote_control", "host", "(missing)", '""'))

    # [audio_udp] — Gqrx's keys:
    #   enabled     = true|false
    #   host        = destination host (127.0.0.1 = this machine)
    #   port        = int
    #   sample_rate = 48000  (Gqrx uses the receiver's audio rate; setting
    #                          it here is optional, Gqrx will use 48k by
    #                          default for FM demod.)
    #   stereo      = true|false
    _set("audio_udp", "enabled", "true")
    _set("audio_udp", "host", audio_host)
    _set("audio_udp", "port", str(audio_port))
    _set("audio_udp", "sample_rate", "48000")
    _set("audio_udp", "stereo", "true")

    # Make sure the directory exists (covers fresh installs).
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with path.open("w", encoding="utf-8") as f:
            cp.write(f, space_around_delimiters=False)
    except OSError as e:
        return GqrxConfigResult(
            ok=False,
            config_path=str(path),
            backup_path=backup_path,
            message=f"Could not write Gqrx config at {path}: {e}",
            changes=changes,
        )

    # Build a user-readable summary.
    if changes:
        summary_lines = [f"Wrote {path}"]
        if backup_path:
            summary_lines.append(f"Backup of old config: {backup_path}")
        summary_lines.append("")
        summary_lines.append("Changes:")
        for sec, key, old, new in changes:
            if old == "":
                summary_lines.append(f"  [{sec}] {key} = {new}  (added)")
            else:
                summary_lines.append(f"  [{sec}] {key}: {old!r} → {new!r}")
        summary_lines.append("")
        summary_lines.append("Next steps:")
        summary_lines.append("  1. If Gqrx is running, quit it completely (File → Quit).")
        summary_lines.append("  2. Re-launch Gqrx:  gqrx &")
        summary_lines.append("  3. In Gqrx, press the green ▶ Play button.")
        summary_lines.append("  4. Back in Magic SDR, click Connect.")
        msg = "\n".join(summary_lines)
    else:
        msg = (
            f"Gqrx config at {path} was already correct — no changes needed.\n\n"
            "If Magic SDR still can't connect:\n"
            "  • Make sure Gqrx is actually running (gqrx &)\n"
            "  • In Gqrx, press the green ▶ Play button to start the receiver\n"
            "  • In Gqrx, Tools → Remote control settings → ensure 'Enable remote control' is checked\n"
            "  • In Gqrx, Tools → Audio UDP → ensure it's enabled (host 127.0.0.1, port 7355)\n"
        )

    return GqrxConfigResult(
        ok=True,
        config_path=str(path),
        backup_path=backup_path,
        message=msg,
        changes=changes,
    )


def inspect_gqrx_config(config_path: Optional[Path] = None) -> str:
    """Read the existing Gqrx config and return a human-readable summary.

    Used by the Diagnose dialog to show what Gqrx's config currently says
    about remote control + audio UDP.
    """
    path = config_path or find_config_path()
    if not path.exists():
        return (
            f"Gqrx config file not found at {path}.\n"
            "  → Gqrx has never been run on this user account. Launch Gqrx once\n"
            "    to create it, or click 'Setup Gqrx config' in Magic SDR's Settings\n"
            "    tab to write a known-good config now."
        )

    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as e:
        return f"Could not parse {path}: {e}"

    lines = [f"Gqrx config: {path}", ""]

    rc_enabled = cp.get("remote_control", "enabled", fallback="(not set)")
    rc_port = cp.get("remote_control", "port", fallback="(not set)")
    rc_host = cp.get("remote_control", "host", fallback="(not set)")
    lines.append("[remote_control]")
    lines.append(f"  enabled = {rc_enabled}  {'✓' if rc_enabled == 'true' else '✗'}")
    lines.append(f"  port    = {rc_port}  {'✓' if str(rc_port) == '7356' else '✗ (Magic SDR expects 7356)'}")
    lines.append(f"  host    = {rc_host!r}  (empty = listen on all interfaces — fine)")
    lines.append("")

    au_enabled = cp.get("audio_udp", "enabled", fallback="(not set)")
    au_host = cp.get("audio_udp", "host", fallback="(not set)")
    au_port = cp.get("audio_udp", "port", fallback="(not set)")
    lines.append("[audio_udp]")
    lines.append(f"  enabled = {au_enabled}  {'✓' if au_enabled == 'true' else '✗'}")
    lines.append(f"  host    = {au_host}  {'✓' if au_host == '127.0.0.1' else '✗ (Magic SDR expects 127.0.0.1)'}")
    lines.append(f"  port    = {au_port}  {'✓' if str(au_port) == '7355' else '✗ (Magic SDR expects 7355)'}")
    lines.append("")

    # Note: Gqrx's config also has [input] (dongle), [receiver] (demod),
    # [bookmarks], etc. We only show the two sections relevant to Magic SDR.

    return "\n".join(lines)
