from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt

TIMEZONES = {
    "IST (UTC+05:30)": dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST"),
    "UTC (UTC+00:00)": dt.timezone.utc,
    "JST (UTC+09:00)": dt.timezone(dt.timedelta(hours=9), name="JST"),
    "CST (UTC+08:00)": dt.timezone(dt.timedelta(hours=8), name="CST"),
    "CET (UTC+01:00)": dt.timezone(dt.timedelta(hours=1), name="CET"),
    "GMT (UTC+00:00)": dt.timezone(dt.timedelta(hours=0), name="GMT"),
    "EDT/EST (UTC-04:00)": dt.timezone(dt.timedelta(hours=-4), name="EDT"),
    "PDT/PST (UTC-07:00)": dt.timezone(dt.timedelta(hours=-7), name="PDT"),
}

#!/usr/bin/env python3
"""
================================================================================
CDDIS GNSS Upload Monitor
================================================================================
Author      : Muhammed Shahid V.H.
Contact     : mail.shahidvh@gmail.com
Version     : 1.1.0
License     : MIT License
Repository  : NASA CDDIS GNSS Station Monitoring Tool
Description : Desktop tool (Linux) built with PySide6 to verify that GNSS 
              station data uploads reach NASA CDDIS archive using native 
              Year and Julian Day (DOY) indexing in UTC.
================================================================================
"""
import base64
import csv
import html
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
import wave

import requests
from PySide6 import QtCore, QtGui, QtWidgets, QtMultimedia
QtCore.QLoggingCategory.setFilterRules("qt.multimedia.*=false\nqt.multimedia.ffmpeg.*=false")
# Silence Qt multimedia hardware acceleration warnings for pure audio playback


BASE = "https://cddis.nasa.gov/archive/gnss/data"

DATA_TYPES = {
    "Hourly observation": dict(path="hourly/{Y}/{DDD}/{HH}", hourly=True, expected=1),
    "Daily observation (RINEX)": dict(path="daily/{Y}/{DDD}/{yy}d", hourly=False, expected=1),
    "Daily navigation": dict(path="daily/{Y}/{DDD}/{yy}n", hourly=False, expected=1),
    "Daily meteorological": dict(path="daily/{Y}/{DDD}/{yy}m", hourly=False, expected=1),
    "High-rate 1 Hz (15-min files)": dict(path="highrate/{Y}/{DDD}/{yy}d/{HH}", hourly=True, expected=4),
}

STATUS_COLORS = {
    "OK": "#2e7d32",
    "PARTIAL": "#ef6c00",
    "MISSING": "#c62828",
    "PENDING": "#757575",
    "ERROR": "#6a1b9a",
}


def create_satellite_icon(bg_color_hex="#1565c0"):
    size = 64
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)

    painter.setBrush(QtGui.QBrush(QtGui.QColor(bg_color_hex)))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)

    painter.translate(size / 2, size / 2)
    painter.rotate(-35)

    panel_pen = QtGui.QPen(QtGui.QColor("#212121"), 1)
    panel_brush = QtGui.QBrush(QtGui.QColor("#e0e0e0"))
    painter.setPen(panel_pen)
    painter.setBrush(panel_brush)

    painter.drawRect(-26, -5, 12, 10)
    painter.drawLine(-20, -5, -20, 5)

    painter.drawRect(14, -5, 12, 10)
    painter.drawLine(20, -5, 20, 5)

    boom_pen = QtGui.QPen(QtGui.QColor("#ffffff"), 1.5)
    painter.setPen(boom_pen)
    painter.drawLine(-14, 0, -8, 0)
    painter.drawLine(8, 0, 14, 0)

    body_brush = QtGui.QBrush(QtGui.QColor("#ffffff"))
    painter.setPen(QtGui.QPen(QtGui.QColor("#212121"), 1.2))
    painter.setBrush(body_brush)
    painter.drawRect(-8, -8, 16, 16)

    foil_brush = QtGui.QBrush(QtGui.QColor("#ffd54f"))
    painter.setBrush(foil_brush)
    painter.drawRect(-4, -4, 8, 8)

    dish_pen = QtGui.QPen(QtGui.QColor("#ffffff"), 2)
    painter.setPen(dish_pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawArc(-6, 9, 12, 8, 0 * 16, 180 * 16)
    painter.drawLine(0, 8, 0, 12)

    painter.end()
    return QtGui.QIcon(pixmap)


_SYSTEM_SOUNDS = [
    "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
]
_TONE_PATH = os.path.join(tempfile.gettempdir(), "cddis_monitor_alert.wav")


def _make_tone(path):
    rate = 22050
    frames = bytearray()
    for _ in range(3):
        for i in range(int(rate * 0.18)):
            v = int(20000 * math.sin(2 * math.pi * 880 * i / rate))
            frames += struct.pack("<h", v)
        frames += b"\x00\x00" * int(rate * 0.10)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def play_alert_sound():
    if os.name == "nt":
        try:
            import winsound
            if not os.path.exists(_TONE_PATH):
                _make_tone(_TONE_PATH)
            winsound.PlaySound(_TONE_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        except Exception:
            QtWidgets.QApplication.beep()
            return

    players = [p for p in ("paplay", "pw-play", "aplay", "play") if shutil.which(p)]
    for snd in _SYSTEM_SOUNDS:
        if os.path.exists(snd) and players:
            if players[0] != "aplay":
                try:
                    subprocess.Popen([players[0], snd], stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass
    if players:
        try:
            if not os.path.exists(_TONE_PATH):
                _make_tone(_TONE_PATH)
            cmd = [players[0], _TONE_PATH]
            if players[0] == "play":
                cmd.insert(1, "-q")
            subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    QtWidgets.QApplication.beep()


def install_linux_autostart(enable=True):
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = os.path.join(autostart_dir, "cddis_monitor.desktop")
    if not enable:
        if os.path.exists(desktop_file):
            try:
                os.remove(desktop_file)
            except Exception:
                pass
        return

    os.makedirs(autostart_dir, exist_ok=True)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    launcher_sh = os.path.join(root_dir, "run_cddm.sh")
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cddis_icon.png")

    # If run_cddm.sh exists, launch via it to preserve virtual environment
    if os.path.exists(launcher_sh):
        exec_cmd = f"/bin/bash -c \"sleep 25 && {launcher_sh} --autostart\""
    else:
        exec_cmd = f"/bin/bash -c \"sleep 25 && {sys.executable} {os.path.abspath(__file__)} --autostart\""

    content = f"""[Desktop Entry]
Type=Application
Exec={exec_cmd}
Path={root_dir}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=25
Name=CDDM Live v1.1
Comment=Automated CDDIS GNSS Archive Monitor
Icon={icon_path if os.path.exists(icon_path) else "utilities-system-monitor"}
Terminal=false
"""
    try:
        with open(desktop_file, "w", encoding="utf-8") as f:
            f.write(content)
        import stat
        os.chmod(desktop_file, os.stat(desktop_file).st_mode | stat.S_IEXEC)
        print("✓ Autostart desktop entry updated.")
    except Exception as e:
        print(f"Failed to write autostart desktop entry: {e}", file=sys.stderr)


def get_station_doy_filepath(base_dir, station, year, doy):
    st_folder = os.path.join(base_dir, station.upper())
    return os.path.join(st_folder, f"{doy:03d}.txt")



def load_existing_station_doy_records(base_dir, station, year, doy):
    """
    Reads <Root>/<STATION>/<DOY>.txt and parses existing verified rows.
    Returns dict: {(station, hour_str): row_dict}
    """
    filepath = get_station_doy_filepath(base_dir, station, year, doy)
    if not os.path.exists(filepath):
        return {}

    records = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        table_started = False
        for line in lines:
            if not table_started:
                if line.strip().startswith("Station") and "Hour (UTC)" in line:
                    table_started = True
                continue

            if line.startswith("-") or not line.strip():
                continue

            parts = line.split()
            if len(parts) >= 8:
                st = parts[0].upper()
                hr = parts[1]
                status = parts[2]
                files_str = parts[3]
                
                # We only preserve slots confirmed as OK
                if status == "OK" and hr.isdigit():
                    checked_str = parts[6] + " " + parts[7] if len(parts) >= 8 else ""
                    try:
                        checked_dt = dt.datetime.strptime(checked_str, "%H:%M:%S UTC").replace(
                            year=year, tzinfo=dt.timezone.utc
                        )
                    except Exception:
                        checked_dt = dt.datetime.now(dt.timezone.utc)

                    count = 1
                    expected = 1
                    if "/" in files_str:
                        c_p, e_p = files_str.split("/", 1)
                        count = int(c_p) if c_p.isdigit() else 1
                        expected = int(e_p) if e_p.isdigit() else 1

                    records[(st, hr)] = {
                        "station": st,
                        "slot": None,
                        "year": year,
                        "doy": doy,
                        "hour_str": hr,
                        "status": "OK",
                        "files": count,
                        "expected": expected,
                        "filenames": "Cached verified file",
                        "size": 0,
                        "mod": "-",
                        "checked": checked_dt,
                        "url": ""
                    }
    except Exception:
        pass
    return records

def parse_single_summary_file(filepath):
    if not os.path.exists(filepath):
        return "unchecked", "Not yet audited"
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        missing_m = re.search(r"-\s*MISSING\s*:\s*(\d+)", text)
        partial_m = re.search(r"-\s*PARTIAL\s*:\s*(\d+)", text)
        ok_m = re.search(r"-\s*OK\s*:\s*(\d+)", text)

        m_cnt = int(missing_m.group(1)) if missing_m else 0
        p_cnt = int(partial_m.group(1)) if partial_m else 0
        ok_cnt = int(ok_m.group(1)) if ok_m else 0

        if m_cnt > 0 or p_cnt > 0:
            return "missing", f"OK={ok_cnt}/24 (Missing={m_cnt}, Partial={p_cnt})"
        if ok_cnt == 24:
            return "ok", "Verified OK (24/24 slots)"
        if ok_cnt > 0:
            return "missing", f"Incomplete: only {ok_cnt}/24 slots logged"
        if "MISSING" in text or "PARTIAL" in text:
            return "missing", "Contains missing/partial slots"
        return "ok", "Verified OK"
    except Exception as e:
        return "unchecked", f"Read error: {e}"


def check_all_stations_doy_status(base_dir, stations, year, doy):
    if not stations:
        return "unchecked", "No stations configured"

    any_unchecked = False
    any_missing = False
    summaries = []

    for st in stations:
        fp = get_station_doy_filepath(base_dir, st, year, doy)
        st_state, st_det = parse_single_summary_file(fp)
        summaries.append(f"{st}: {st_det}")
        if st_state == "unchecked":
            any_unchecked = True
        elif st_state == "missing":
            any_missing = True

    tooltip_details = "\n".join(summaries)
    if any_unchecked:
        return "unchecked", tooltip_details
    if any_missing:
        return "missing", tooltip_details
    return "ok", tooltip_details


import http.cookiejar as cookiejar

class EarthdataSession(requests.Session):
    def __init__(self, username=None, password=None, proxy_url=None):
        super().__init__()
        import os, netrc
        from requests.auth import HTTPBasicAuth

        self.cookie_file = os.path.expanduser("~/.cddis_cookies.txt")
        self.cookies = cookiejar.MozillaCookieJar(self.cookie_file)
        if os.path.exists(self.cookie_file):
            try:
                self.cookies.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass

        u, p = username, password
        if not u or not p:
            try:
                auth_data = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
                if auth_data:
                    u, _, p = auth_data
            except Exception:
                pass

        self.auth_creds = (u, p) if (u and p) else None
        if self.auth_creds:
            self.auth = HTTPBasicAuth(*self.auth_creds)

        self.trust_env = True
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=2)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

        # Institutional Proxy configuration
        cfg_proxy = None
        cfg_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proxy.conf")
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as pf:
                    for raw_ln in pf:
                        ln = raw_ln.strip()
                        if not ln or ln.startswith("#"):
                            continue
                        if "=" in ln:
                            k, v = ln.split("=", 1)
                            if k.strip().upper() in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "PROXY"):
                                ln = v.strip().strip('"').strip("'")
                        if ln.upper() == "DIRECT":
                            cfg_proxy = "DIRECT"
                            break
                        if ln:
                            cfg_proxy = ln
                            break
            except Exception:
                pass

        env_proxy = os.getenv("https_proxy") or os.getenv("http_proxy") or os.getenv("ALL_PROXY")
        if cfg_proxy == "DIRECT":
            active_proxy = None
        else:
            active_proxy = proxy_url or (cfg_proxy if cfg_proxy != "DIRECT" else None) or env_proxy or None
        if active_proxy:
            if not active_proxy.startswith("http"):
                active_proxy = f"http://{active_proxy}"
            self.proxies = {"http": active_proxy, "https": active_proxy}

    def request(self, *args, **kwargs):
        resp = super().request(*args, **kwargs)
        try:
            self.cookies.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
        return resp

    def rebuild_auth(self, prepared_request, response):
        from requests.auth import HTTPBasicAuth
        headers = prepared_request.headers
        # Allow basic auth header on CDDIS domain and NASA URS endpoints cleanly
        hostname = getattr(prepared_request, "url", "")
        if self.auth_creds and ("cddis.nasa.gov" in hostname or "urs.earthdata.nasa.gov" in hostname):
            prepared_request.prepare_auth(HTTPBasicAuth(*self.auth_creds))
            return
        super().rebuild_auth(prepared_request, response)


def list_dir(session, url):
    """Fetches and parses a CDDIS HTML directory listing, extracting filenames, file sizes, and mod times."""
    import re
    try:
        r = session.get(url, timeout=(6, 12))
        if r.status_code == 404:
            return "NOT_FOUND", {}
        if r.status_code in (401, 403):
            return "UNAUTHORIZED", {}
        if r.status_code != 200:
            return f"HTTP_{r.status_code}", {}

        file_map = {}
        html = r.text
        valid_exts = (".z", ".gz", ".crx.gz", ".rnx.gz", ".zip")

        def _parse_size_kb(sz_str):
            if not sz_str:
                return 0.0
            s = sz_str.strip().upper()
            mult = 1.0
            if s.endswith("KB") or s.endswith("K"):
                mult = 1.0
            elif s.endswith("MB") or s.endswith("M"):
                mult = 1024.0
            elif s.endswith("GB") or s.endswith("G"):
                mult = 1024.0 * 1024.0
            elif s.endswith("B"):
                mult = 1.0 / 1024.0
            else:
                mult = 1.0 / 1024.0

            digits = [ch for ch in s if ch.isdigit() or ch == '.']
            if not digits:
                return 0.0
            try:
                return round(float("".join(digits)) * mult, 2)
            except Exception:
                return 0.0

        item_pat = re.compile(
            r'<a\b[^>]*\bhref=["\']([^"\'?#]+)["\'][^>]*>.*?</a>\s*<span\b[^>]*class=["\']fileInfo["\'][^>]*>\s*([0-9:/\-\s]+?)\s+([0-9.]+\s*[KMGT]?B?)\s*</span>',
            re.IGNORECASE | re.DOTALL
        )
        for m in item_pat.finditer(html):
            link = m.group(1).strip()
            mod_time = m.group(2).strip().replace(":", "-", 2)
            size_raw = m.group(3).strip()
            fname = link.split("/")[-1].strip()
            fname_l = fname.lower()
            if fname and any(fname_l.endswith(ext) for ext in valid_exts):
                file_map[fname] = {
                    "filename": fname,
                    "url": link if link.startswith("http") else url.rstrip("/") + "/" + fname,
                    "size": _parse_size_kb(size_raw),
                    "mod": mod_time
                }

        if not file_map:
            link_pat = re.compile(r'href=["\']([^"\'?#]+)["\']', re.IGNORECASE)
            for m in link_pat.finditer(html):
                link = m.group(1).strip()
                fname = link.split("/")[-1].strip()
                fname_l = fname.lower()
                if fname and any(fname_l.endswith(ext) for ext in valid_exts):
                    file_map[fname] = {
                        "filename": fname,
                        "url": link if link.startswith("http") else url.rstrip("/") + "/" + fname,
                        "size": 0.0,
                        "mod": ""
                    }
        return "ok", file_map
    except Exception:
        return "error", {}



class LoginWorker(QtCore.QThread):
    finished_auth = QtCore.Signal(bool, str, str)

    def __init__(self, username, password, parent=None):
        super().__init__(parent)
        self.username = username
        self.password = password
        self.result_session = None

    def __del__(self):
        try:
            self.quit()
            self.wait(1000)
        except Exception:
            pass

    def run(self):
        try:
            sess = EarthdataSession(self.username, self.password)
            test_url = f"{BASE}/daily/"
            # Use session-level proxies automatically configured by EarthdataSession
            r = sess.get(test_url, timeout=30, allow_redirects=True)

            if "urs.earthdata.nasa.gov" in r.url and "/oauth/authorize" in r.url:
                self.finished_auth.emit(
                    False, "auth",
                    "Authentication error: Authorize 'CDDIS' in your Earthdata profile."
                )
                return

            if r.status_code in (401, 403):
                self.finished_auth.emit(
                    False, "auth",
                    "Incorrect username or password."
                )
                return

            if r.status_code != 200:
                self.finished_auth.emit(
                    False, "network",
                    f"Server returned unexpected HTTP {r.status_code}."
                )
                return

            self.result_session = sess
            self.finished_auth.emit(True, "ok", "Connected successfully.")
        except requests.exceptions.Timeout:
            self.finished_auth.emit(
                False, "network",
                "Connection timed out. CDDIS server took too long to respond."
            )
        except requests.exceptions.ConnectionError:
            self.finished_auth.emit(
                False, "network",
                "Network connection error. Waiting for internet..."
            )
        except Exception as e:
            self.finished_auth.emit(False, "network", f"Err: {str(e)} | {type(e).__name__}")

class LoginDialog(QtWidgets.QDialog):
    MAX_BOOT_RETRIES = 12

    def __init__(self, parent=None, is_autostart=False):
        super().__init__(parent)
        self.setWindowTitle("CDDIS Login")
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cddis_icon.png")))
        self.setMinimumWidth(450)
        self.session = None
        self.worker = None
        self.transition_steps = 0
        self.is_autostart = is_autostart
        self.retry_count = 0
        self.retry_seconds_left = 0

        self.root_layout = QtWidgets.QVBoxLayout(self)
        self.root_layout.setSpacing(12)
        self.root_layout.setContentsMargins(24, 24, 24, 24)

        self.form_widget = QtWidgets.QWidget()
        f_layout = QtWidgets.QVBoxLayout(self.form_widget)
        f_layout.setContentsMargins(0, 0, 0, 0)
        f_layout.setSpacing(10)

        header = QtWidgets.QLabel("<b>Connect to NASA CDDIS Archive</b>")
        header.setStyleSheet("font-size: 15px; color: #1565c0;")
        f_layout.addWidget(header)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)

        self.user = QtWidgets.QLineEdit()
        self.user.setPlaceholderText("Earthdata Username")
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.pw.setPlaceholderText("Password")
        self.remember_cb = QtWidgets.QCheckBox("Save credentials on this computer")

        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        form.addRow("", self.remember_cb)
        f_layout.addLayout(form)

        self.status_box = QtWidgets.QFrame()
        self.status_box.setStyleSheet("background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px;")
        s_layout = QtWidgets.QHBoxLayout(self.status_box)
        s_layout.setContentsMargins(10, 8, 10, 8)

        self.status_spinner = QtWidgets.QProgressBar(self)
        self.status_spinner.setFixedSize(60, 10)
        self.status_spinner.setRange(0, 0)
        self.status_spinner.hide()

        self.msg = QtWidgets.QLabel("Enter your NASA Earthdata credentials.")
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color: #495057; font-size: 12px;")

        s_layout.addWidget(self.status_spinner)
        s_layout.addWidget(self.msg, 1)
        f_layout.addWidget(self.status_box)

        self.btn = QtWidgets.QPushButton("Connect to CDDIS")
        self.btn.setFixedHeight(36)
        self.btn.setDefault(True)
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: #1976d2;
                color: white;
                font-weight: bold;
                font-size: 13px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1565c0;
            }
            QPushButton:disabled {
                background-color: #90caf9;
                color: #e3f2fd;
            }
        """)
        self.btn.clicked.connect(self.manual_start_login)
        f_layout.addWidget(self.btn)

        self.root_layout.addWidget(self.form_widget)

        self.success_widget = QtWidgets.QWidget()
        self.success_widget.hide()
        succ_layout = QtWidgets.QVBoxLayout(self.success_widget)
        succ_layout.setAlignment(QtCore.Qt.AlignCenter)
        succ_layout.setSpacing(12)
        succ_layout.setContentsMargins(16, 16, 16, 16)

        tick_label = QtWidgets.QLabel("✔")
        tick_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: #2e7d32;
                font-size: 34px;
                font-weight: bold;
                border-radius: 30px;
                min-width: 60px;
                max-width: 60px;
                min-height: 60px;
                max-height: 60px;
            }
        """)
        tick_label.setAlignment(QtCore.Qt.AlignCenter)

        succ_title = QtWidgets.QLabel("Connected to NASA Earthdata!")
        succ_title.setStyleSheet("color: #2e7d32; font-size: 16px; font-weight: bold;")
        succ_title.setAlignment(QtCore.Qt.AlignCenter)

        self.succ_sub = QtWidgets.QLabel("Launching GNSS Data Checker in 3.0s…")
        self.succ_sub.setStyleSheet("color: #495057; font-size: 13px;")
        self.succ_sub.setAlignment(QtCore.Qt.AlignCenter)

        self.launch_bar = QtWidgets.QProgressBar()
        self.launch_bar.setFixedHeight(8)
        self.launch_bar.setRange(0, 30)
        self.launch_bar.setValue(0)
        self.launch_bar.setTextVisible(False)
        self.launch_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #e0e0e0;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: #2e7d32;
                border-radius: 4px;
            }
        """)

        succ_layout.addWidget(tick_label, 0, QtCore.Qt.AlignCenter)
        succ_layout.addWidget(succ_title)
        succ_layout.addWidget(self.succ_sub)
        succ_layout.addWidget(self.launch_bar)
        self.root_layout.addWidget(self.success_widget)

        self.transition_timer = QtCore.QTimer(self)
        self.transition_timer.timeout.connect(self._tick_transition)

        self.retry_timer = QtCore.QTimer(self)
        self.retry_timer.timeout.connect(self._tick_retry_countdown)

        s = QtCore.QSettings("gnss-tools", "cddis-monitor")
        self.user.setText(s.value("username", ""))
        saved_pw = s.value("password", "")
        if saved_pw:
            try:
                self.pw.setText(base64.b64decode(saved_pw.encode()).decode())
                self.remember_cb.setChecked(True)
            except Exception:
                pass
        (self.pw if self.user.text() else self.user).setFocus()

        if self.is_autostart and self.user.text() and self.pw.text():
            self.msg.setText("<span style='color:#0d47a1;'>Autostart: Connecting to NASA Earthdata…</span>")
            QtCore.QTimer.singleShot(1500, self.start_login)

    def manual_start_login(self):
        self.retry_timer.stop()
        self.retry_count = 0
        self.start_login()

    def start_login(self):
        self.retry_timer.stop()
        u = self.user.text().strip()
        p = self.pw.text()
        if not u or not p:
            self.msg.setText("<b style='color:#c62828;'>Please enter both username and password.</b>")
            return

        self.btn.setEnabled(False)
        self.btn.setText("Connecting...")
        self.user.setEnabled(False)
        self.pw.setEnabled(False)
        self.remember_cb.setEnabled(False)

        
        self.msg.setText("<span style='color:#0d47a1;'>Authenticating with NASA Earthdata… Please wait.</span>")

        self.worker = LoginWorker(u, p, self)
        self.worker.finished_auth.connect(self.on_login_finished)
        self.worker.start()

    def on_login_finished(self, success, err_type, message):
        if success:
            session = getattr(self.worker, "result_session", None)
            self.retry_timer.stop()
            s = QtCore.QSettings("gnss-tools", "cddis-monitor")
            s.setValue("username", self.user.text().strip())
            s.setValue("auto_login", True)
            if self.remember_cb.isChecked():
                encoded_pw = base64.b64encode(self.pw.text().encode()).decode()
                s.setValue("password", encoded_pw)
            else:
                s.remove("password")
            s.sync()
            self.session = session

            self.form_widget.hide()
            self.success_widget.show()
            self.adjustSize()

            self.transition_steps = 0
            self.transition_timer.start(100)
            return

        if self.is_autostart and err_type == "network" and self.retry_count < self.MAX_BOOT_RETRIES:
            self.retry_count += 1
            self.retry_seconds_left = 10
            self.btn.setEnabled(True)
            self.btn.setText("Retry Now")
            self.user.setEnabled(True)
            self.pw.setEnabled(True)
            self.remember_cb.setEnabled(True)
            
            self._update_retry_label()
            self.retry_timer.start(1000)
            return

        self.btn.setEnabled(True)
        self.btn.setText("Connect to CDDIS")
        self.user.setEnabled(True)
        self.pw.setEnabled(True)
        self.remember_cb.setEnabled(True)
        self.status_spinner.hide()
        self.msg.setText(f"<b style='color:#c62828;'>{html.escape(message)}</b>")

        if self.is_autostart:
            QtWidgets.QMessageBox.critical(
                self,
                "CDDIS Autostart Connection Error",
                f"CDDIS Monitor failed to autostart connection:\n\n{message}\n\n"
                "Please verify your internet connection and Earthdata login."
            )

    def _update_retry_label(self):
        self.msg.setText(
            f"<b style='color:#ef6c00;'>Waiting for network after boot… "
            f"Auto-retrying in {self.retry_seconds_left}s "
            f"(Attempt {self.retry_count}/{self.MAX_BOOT_RETRIES})</b>"
        )

    def _tick_retry_countdown(self):
        self.retry_seconds_left -= 1
        if self.retry_seconds_left <= 0:
            self.retry_timer.stop()
            self.start_login()
        else:
            self._update_retry_label()

    def _tick_transition(self):
        self.transition_steps += 1
        self.launch_bar.setValue(self.transition_steps)
        remaining = max(0.0, (30 - self.transition_steps) / 10.0)
        self.succ_sub.setText(f"Launching GNSS Data Checker in {remaining:.1f}s…")
        if self.transition_steps >= 30:
            self.transition_timer.stop()
            self.accept()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings - CDDM Live v1.1")
        self.setFixedSize(460, 320)
        self.setModal(True)
        self.settings = QtCore.QSettings("gnss-tools", "cddis-monitor")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QtWidgets.QLabel("Application Preferences")
        title.setStyleSheet("font-weight: bold; font-size: 14px; color: #1d1d1f;")
        layout.addWidget(title)

        # Audio Alerts Section
        sound_group = QtWidgets.QGroupBox("Alert Sounds")
        sound_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 12px; margin-top: 6px; }")
        sg_layout = QtWidgets.QVBoxLayout(sound_group)
        sg_layout.setSpacing(10)

        self.sound_alerts_cb = QtWidgets.QCheckBox("Play sound alert for missing station files & failures")
        self.sound_alerts_cb.setStyleSheet("font-size: 12px; font-weight: normal; color: #212529;")
        self.sound_alerts_cb.setChecked(self.settings.value("sound_alerts_enabled", True, type=bool))
        sg_layout.addWidget(self.sound_alerts_cb)

        # Volume Row
        vol_row = QtWidgets.QHBoxLayout()
        vol_label = QtWidgets.QLabel("Volume:")
        vol_label.setStyleSheet("font-size: 11px; font-weight: normal; color: #495057;")
        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        saved_vol = self.settings.value("sound_volume", 50, type=int)
        self.vol_slider.setValue(saved_vol)

        self.vol_val_lbl = QtWidgets.QLabel(f"{saved_vol}%")
        self.vol_val_lbl.setFixedWidth(40)
        self.vol_val_lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #212529;")

        self.test_sound_btn = QtWidgets.QPushButton("▶ Test Sound")
        self.test_sound_btn.setStyleSheet("padding: 3px 10px; font-size: 11px;")

        vol_row.addWidget(vol_label)
        vol_row.addWidget(self.vol_slider)
        vol_row.addWidget(self.vol_val_lbl)
        vol_row.addWidget(self.test_sound_btn)
        sg_layout.addLayout(vol_row)

        layout.addWidget(sound_group)

        # Wire volume events and test audio
        self.vol_slider.valueChanged.connect(self._on_vol_changed)
        self.test_sound_btn.clicked.connect(self._toggle_test_sound)

        # Autostart Option
        self.autostart_cb = QtWidgets.QCheckBox("Start automatically on system login (Linux)")
        self.autostart_cb.setStyleSheet("font-size: 12px; color: #212529;")
        # Check ~/.config/autostart existence as ground truth, or fallback to QSettings
        autostart_file = os.path.expanduser("~/.config/autostart/cddis_monitor.desktop")
        is_enabled = os.path.exists(autostart_file) or self.settings.value("autostart", True, type=bool)
        self.autostart_cb.setChecked(is_enabled)
        layout.addWidget(self.autostart_cb)

        desc = QtWidgets.QLabel("When enabled, CDDM launches in background 30 seconds after system login.")
        desc.setStyleSheet("color: #6c757d; font-size: 11px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch()

        # Buttons
        btn_box = QtWidgets.QHBoxLayout()
        btn_box.addStretch()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QtWidgets.QPushButton("Save Changes")
        save_btn.setStyleSheet("background-color: #0071e3; color: white; font-weight: bold; padding: 5px 15px; border-radius: 4px;")
        save_btn.clicked.connect(self.save_and_close)

        btn_box.addWidget(cancel_btn)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _on_vol_changed(self, val):
        self.vol_val_lbl.setText(f"{val}%")
        parent = self.parent()
        if parent and hasattr(parent, "audio_output"):
            parent.audio_output.setVolume(val / 100.0)

    def _toggle_test_sound(self):
        parent = self.parent()
        if parent and hasattr(parent, "play_alert_audio"):
            parent.play_alert_audio(max_duration_sec=5)

    def save_and_close(self):
        enabled = self.autostart_cb.isChecked()
        self.settings.setValue("autostart", enabled)
        install_linux_autostart(enabled)

        # Save Audio Settings
        self.settings.setValue("sound_alerts_enabled", self.sound_alerts_cb.isChecked())
        self.settings.setValue("sound_volume", self.vol_slider.value())
        parent = self.parent()
        if parent and hasattr(parent, "audio_output"):
            parent.audio_output.setVolume(self.vol_slider.value() / 100.0)

        self.accept()


class ArchiveGapAnalyzerDialog(QtWidgets.QDialog):
    """Offline analytical tool for auditing missing hours and data gaps across local DOY reports."""
    def __init__(self, parent, base_dir, stations, current_year):
        super().__init__(parent)
        self.base_dir = base_dir
        self.stations = stations
        self.current_year = current_year
        self.setWindowTitle("Offline Archive Gap & Missing-Hours Analyzer")
        self.resize(880, 560)
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; color: #1e293b; }
            QLabel { color: #334155; font-size: 12px; font-weight: 500; }
            QComboBox, QSpinBox {
                background-color: #f8fafc; color: #0f172a;
                border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 8px;
            }
            QPushButton {
                background-color: #0071e3; color: #ffffff;
                border: none; border-radius: 4px; padding: 6px 14px; font-weight: 600;
            }
            QPushButton:hover { background-color: #0077ed; }
            QTableWidget {
                background-color: #ffffff; color: #1e293b;
                gridline-color: #e2e8f0; border: 1px solid #cbd5e1;
                font-family: monospace; font-size: 11px;
            }
            QHeaderView::section {
                background-color: #f1f5f9; color: #334155;
                padding: 5px; border: 1px solid #e2e8f0; font-weight: 600;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header controls
        ctrl_box = QtWidgets.QHBoxLayout()
        ctrl_box.addWidget(QtWidgets.QLabel("Station:"))
        self.st_combo = QtWidgets.QComboBox()
        self.st_combo.addItem("ALL STATIONS")
        for s in self.stations:
            self.st_combo.addItem(s.upper())
        ctrl_box.addWidget(self.st_combo)

        ctrl_box.addWidget(QtWidgets.QLabel("Year:"))
        self.year_spin = QtWidgets.QSpinBox()
        self.year_spin.setRange(1990, 2099)
        self.year_spin.setValue(self.current_year)
        ctrl_box.addWidget(self.year_spin)

        ctrl_box.addWidget(QtWidgets.QLabel("From DOY:"))
        self.start_doy = QtWidgets.QSpinBox()
        self.start_doy.setRange(1, 366)
        self.start_doy.setValue(max(1, dt.datetime.now().timetuple().tm_yday - 30))
        ctrl_box.addWidget(self.start_doy)

        ctrl_box.addWidget(QtWidgets.QLabel("To DOY:"))
        self.end_doy = QtWidgets.QSpinBox()
        self.end_doy.setRange(1, 366)
        self.end_doy.setValue(dt.datetime.now().timetuple().tm_yday)
        ctrl_box.addWidget(self.end_doy)

        self.scan_btn = QtWidgets.QPushButton("Run Offline Audit")
        self.scan_btn.clicked.connect(self.run_audit)
        ctrl_box.addWidget(self.scan_btn)

        self.export_btn = QtWidgets.QPushButton("Export CSV")
        self.export_btn.clicked.connect(self.export_csv)
        ctrl_box.addWidget(self.export_btn)

        layout.addLayout(ctrl_box)

        # Filter bar for Missing and Complete files
        filter_box = QtWidgets.QHBoxLayout()
        filter_box.setSpacing(12)
        filter_lbl = QtWidgets.QLabel("Filter View:")
        filter_lbl.setStyleSheet("font-weight: 600; color: #475569; font-size: 11px;")
        filter_box.addWidget(filter_lbl)

        self.chk_complete = QtWidgets.QCheckBox("✔ Complete (24/24)")
        self.chk_complete.setChecked(True)
        self.chk_complete.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_complete.setStyleSheet("color: #166534; font-weight: 600; font-size: 11px;")
        self.chk_complete.toggled.connect(self.apply_filter)
        filter_box.addWidget(self.chk_complete)

        self.chk_missing = QtWidgets.QCheckBox("✖ Missing / Incomplete / Unscanned")
        self.chk_missing.setChecked(True)
        self.chk_missing.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_missing.setStyleSheet("color: #991b1b; font-weight: 600; font-size: 11px;")
        self.chk_missing.toggled.connect(self.apply_filter)
        filter_box.addWidget(self.chk_missing)

        filter_box.addStretch()
        layout.addLayout(filter_box)

        # Results table
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "DOY", "Date (UTC)", "Station", "Status", "Available", "Missing / Incomplete Hours (UTC)"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        self.status_lbl = QtWidgets.QLabel("Ready to scan offline reports.")
        layout.addWidget(self.status_lbl)


    def apply_filter(self):
        show_comp = self.chk_complete.isChecked()
        show_miss = self.chk_missing.isChecked()
        visible_cnt = 0
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 3)
            is_comp = item and "COMPLETE" in item.text()
            should_show = (is_comp and show_comp) or (not is_comp and show_miss)
            self.table.setRowHidden(r, not should_show)
            if should_show:
                visible_cnt += 1
        base_msg = getattr(self, "_base_status_text", "")
        if base_msg:
            self.status_lbl.setText(f"{base_msg} | Displaying {visible_cnt} filtered row(s).")

    def run_audit(self):
        sel_st = self.st_combo.currentText()
        target_sts = self.stations if sel_st == "ALL STATIONS" else [sel_st]
        year = self.year_spin.value()
        s_doy = self.start_doy.value()
        e_doy = self.end_doy.value()

        if s_doy > e_doy:
            s_doy, e_doy = e_doy, s_doy

        self.table.setRowCount(0)
        total_days = 0
        gap_days = 0
        unscanned_days = 0

        for d in range(s_doy, e_doy + 1):
            day_date = dt.datetime(year, 1, 1) + dt.timedelta(days=d - 1)
            date_str = day_date.strftime("%Y-%m-%d")

            for st in target_sts:
                total_days += 1
                fp = get_station_doy_filepath(self.base_dir, st, year, d)
                
                row_idx = self.table.rowCount()
                self.table.insertRow(row_idx)
                self.table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(f"{d:03d}"))
                self.table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(date_str))
                self.table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(st.upper()))

                if not os.path.exists(fp):
                    unscanned_days += 1
                    stat_item = QtWidgets.QTableWidgetItem("NOT SCANNED / ARCHIVED")
                    stat_item.setForeground(QtGui.QColor("#9e9e9e"))
                    self.table.setItem(row_idx, 3, stat_item)
                    self.table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem("0/24"))
                    self.table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem("No summary report on disk"))
                    continue

                # Parse existing report
                ok_count = 0
                missing_hours = []
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) >= 4 and parts[1] == "Hr":
                                hr_val = parts[2]
                                status = parts[3]
                                if status == "OK":
                                    ok_count += 1
                                elif status in ("MISSING", "PARTIAL"):
                                    missing_hours.append(hr_val)

                    if ok_count == 24:
                        stat_item = QtWidgets.QTableWidgetItem("COMPLETE (24/24)")
                        stat_item.setForeground(QtGui.QColor("#4caf50"))
                        self.table.setItem(row_idx, 3, stat_item)
                        self.table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem("24/24"))
                        self.table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem("None (All hours verified)"))
                    else:
                        gap_days += 1
                        stat_item = QtWidgets.QTableWidgetItem(f"GAPS FOUND ({len(missing_hours)} hrs)")
                        stat_item.setForeground(QtGui.QColor("#ff5252"))
                        self.table.setItem(row_idx, 3, stat_item)
                        self.table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem(f"{ok_count}/24"))
                        miss_str = ", ".join(f"Hr {h}" for h in sorted(missing_hours)) if missing_hours else "Incomplete"
                        self.table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem(miss_str))
                except Exception as ex:
                    self.table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem("READ ERROR"))
                    self.table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem(str(ex)))

        self.table.resizeColumnsToContents()
        self._base_status_text = (
            f"Audit Complete: {total_days} station-day(s) analyzed. "
            f"{gap_days} day(s) have missing hours | {unscanned_days} not yet scanned."
        )
        self.apply_filter()

    def export_csv(self):
        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "Export", "No data to export. Run audit first.")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Archive Gap Report", "cddis_archive_gaps.csv", "CSV Files (*.csv)")
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("DOY,Date_UTC,Station,Status,Available_Hours,Missing_Hours_UTC\n")
                exported_count = 0
                for r in range(self.table.rowCount()):
                    if self.table.isRowHidden(r):
                        continue
                    vals = [self.table.item(r, c).text().replace(",", ";") if self.table.item(r, c) else "" for c in range(6)]
                    f.write(",".join(vals) + "\n")
                    exported_count += 1
            QtWidgets.QMessageBox.information(self, "Export Successful", f"Saved {exported_count} displayed record(s) to:\n{path}")
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Export Failed", str(ex))



class CheckWorker(QtCore.QThread):
    results = QtCore.Signal(list)
    slot_checked = QtCore.Signal(dict)
    failed = QtCore.Signal(str)
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, str)

    def __init__(self, session, dtype, slots_utc, stations, grace_min, cached_records=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.dtype = dtype
        self.slots_utc = slots_utc
        self.stations = [s.upper() for s in stations]
        self.grace_min = grace_min
        self.cached_records = cached_records or {}

    def run(self):
        try:
            cfg = DATA_TYPES[self.dtype]
            sess = self.session or EarthdataSession()
            now_utc = dt.datetime.now(dt.timezone.utc)
            out_rows = []
            total = len(self.slots_utc) * len(self.stations)
            done = 0

            def _fetch_slot(slot_dt):
                y = slot_dt.year
                doy = slot_dt.timetuple().tm_yday
                hh_str = f"{slot_dt.hour:02d}" if cfg["hourly"] else "00"
                url = f"{BASE}/" + cfg["path"].format(Y=y, DDD=f"{doy:03d}", yy=f"{y % 100:02d}", HH=hh_str)
                # Only hit network if at least one station isn't already cached OK
                needs_net = any((st, y, doy, hh_str) not in self.cached_records for st in self.stations)
                if not needs_net:
                    return slot_dt, url, "ok", {}
                st_code, fmap = list_dir(sess, url)
                return slot_dt, url, st_code, fmap

            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_fetch_slot, sl): sl for sl in self.slots_utc}
                for fut in as_completed(futures):
                    if self.isInterruptionRequested():
                        break
                    slot_dt, url, st_code, fmap = fut.result()
                    y = slot_dt.year
                    doy = slot_dt.timetuple().tm_yday
                    hh_str = f"{slot_dt.hour:02d}" if cfg["hourly"] else "00"
                    slot_end = slot_dt + (dt.timedelta(hours=1) if cfg["hourly"] else dt.timedelta(days=1))
                    grace_deadline = slot_end + dt.timedelta(minutes=self.grace_min)

                    for st in self.stations:
                        cached = self.cached_records.get((st, y, doy, hh_str))
                        if cached and cached.get("status") == "OK":
                            row = dict(cached)
                            out_rows.append(row)
                            done += 1
                            self.slot_checked.emit(row)
                            self.progress.emit(done, total, f"Cached {st} Hr {hh_str}")
                            continue

                        matches = sorted([fn for fn in fmap if fn.lower().startswith(st.lower())])
                        f_cnt = len(matches)
                        exp_cnt = cfg["expected"]
                        if f_cnt >= exp_cnt:
                            status = "OK"
                        elif f_cnt > 0:
                            status = "PARTIAL"
                        elif now_utc < grace_deadline:
                            status = "PENDING"
                        else:
                            status = "MISSING"

                        tot_sz = sum(fmap[fn].get("size", 0.0) for fn in matches) * 1024.0
                        mod_str = fmap[matches[-1]].get("mod", "-") if matches else "-"
                        row = {
                            "station": st,
                            "slot": slot_dt,
                            "year": y,
                            "doy": doy,
                            "hour_str": hh_str,
                            "status": status,
                            "files": f_cnt,
                            "expected": exp_cnt,
                            "filenames": ", ".join(matches) if matches else "-",
                            "size": tot_sz,
                            "mod": mod_str,
                            "checked": dt.datetime.now(dt.timezone.utc),
                            "url": url,
                        }
                        out_rows.append(row)
                        done += 1
                        self.slot_checked.emit(row)
                        self.progress.emit(done, total, f"Checked {st} Hr {hh_str}: {status}")

            out_rows.sort(key=lambda r: (r["slot"], r["station"]))
            self.results.emit(out_rows)
        except Exception as e:
            self.failed.emit(str(e))


class CustomScanWorker(QtCore.QThread):
    log = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, str)
    row_scanning = QtCore.Signal(int, int, str, str)
    row_ready = QtCore.Signal(dict)
    finished_all = QtCore.Signal(str, list)

    def __init__(self, session, dtype, stations, date_list, out_dir, auto_audit=True, parent=None):
        super().__init__(parent)
        self.session = session
        self.dtype = dtype
        self.stations = [s.upper() for s in stations]
        self.date_list = date_list
        self.out_dir = out_dir
        self.auto_audit = auto_audit

    def _custom_doy_filepath(self, year, doy, st):
        years_in_scan = {y for (y, _) in self.date_list}
        if len(years_in_scan) > 1:
            return os.path.join(self.out_dir, str(year), st.upper(), f"{doy:03d}.txt")
        return get_station_doy_filepath(self.out_dir, st, year, doy)

    def _build_rows_from_dir_maps(self, cfg, year, doy, st, dir_maps):
        base_day = dt.datetime(year, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(days=doy - 1)
        hours = range(24) if cfg["hourly"] else [0]
        rows = []
        for h in hours:
            hh_str = f"{h:02d}"
            fmap = dir_maps.get(hh_str, {})
            matches = sorted([fn for fn in fmap if fn.lower().startswith(st.lower())])
            f_cnt = len(matches)
            exp_cnt = cfg["expected"]
            status = "OK" if f_cnt >= exp_cnt else ("PARTIAL" if f_cnt > 0 else "MISSING")
            tot_sz = sum(fmap[fn].get("size", 0.0) for fn in matches) * 1024.0
            mod_str = fmap[matches[-1]].get("mod", "-") if matches else "-"
            rows.append({
                "station": st,
                "slot": base_day + dt.timedelta(hours=h),
                "year": year,
                "doy": doy,
                "hour_str": hh_str,
                "status": status,
                "files": f_cnt,
                "expected": exp_cnt,
                "filenames": ", ".join(matches) if matches else "-",
                "size": tot_sz,
                "mod": mod_str,
            })
        return rows

    def _write_station_doy_report(self, year, doy, st, st_rows):
        filepath = self._custom_doy_filepath(year, doy, st)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        total = len(st_rows)
        counts = {}
        for r in st_rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("=" * 110 + "\n")
            f.write("NASA CDDIS GNSS ARCHIVE - CUSTOM SCAN DOY SUMMARY REPORT\n")
            f.write(f"Station       : {st}\n")
            f.write(f"Year / DOY    : {year} / DOY {doy:03d}\n")
            f.write(f"Data Type     : {self.dtype}\n")
            f.write(f"Generated on  : {dt.datetime.now():%Y-%m-%d %H:%M:%S} Local Time\n")
            f.write("=" * 96 + "\n\n")
            f.write("STATUS SUMMARY:\n")
            for st_type in ("OK", "PARTIAL", "MISSING"):
                c = counts.get(st_type, 0)
                pct = (c / total * 100) if total else 0
                f.write(f"  - {st_type:<10}: {c:>3} ({pct:.1f}%)\n")
            f.write("\n" + "-" * 96 + "\n")
            f.write(f"{'Station':<8} {'Hour (UTC)':<11} {'Status':<9} {'Files':<6} {'Size (KB)':<11} {'Last Modified (UTC)':<20} {'Uploaded File(s)'}\n")
            f.write("-" * 110 + "\n")
            for r in sorted(st_rows, key=lambda x: x["slot"]):
                f.write(
                    f"{r['station']:<8} Hr {r['hour_str']:<7} {r['status']:<9} "
                    f"{r['files']}/{r['expected']:<4} {r.get('size', 0)/1024:<11.1f} "
                    f"{str(r.get('mod', '-')):<20} {r.get('filenames', '')}\n"
                )
            f.write("=" * 110 + "\n")

    def _write_total_summary(self, summary_path, day_records, audit_notes=None, start_dt=None, end_dt=None):
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        merged_list = sorted(day_records, key=lambda r: (r["year"], r["doy"], r["station"]))
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("=" * 115 + "\n")
            f.write("NASA CDDIS GNSS ARCHIVE - CONSOLIDATED CUSTOM SCAN SUMMARY\n")
            f.write(f"Data Type     : {self.dtype}\n")
            f.write(f"Stations      : {', '.join(self.stations)}\n")
            if merged_list:
                f.write(f"Range         : {merged_list[0]['year']}-DOY{merged_list[0]['doy']:03d} to {merged_list[-1]['year']}-DOY{merged_list[-1]['doy']:03d}\n")
            if start_dt and end_dt:
                elapsed_s = max(0.0, (end_dt - start_dt).total_seconds())
                hrs, rem = divmod(int(elapsed_s), 3600)
                mins, secs = divmod(rem, 60)
                dur_str = f"{hrs:02d}h {mins:02d}m {secs:02d}s ({elapsed_s:.1f}s)" if hrs else f"{mins:02d}m {secs:02d}s ({elapsed_s:.1f}s)"
                f.write(f"Scan Duration : Start: {start_dt:%Y-%m-%d %H:%M:%S} | End: {end_dt:%Y-%m-%d %H:%M:%S} | Total Duration: {dur_str}\n")
            f.write(f"Updated on    : {dt.datetime.now():%Y-%m-%d %H:%M:%S} Local Time\n")
            f.write("=" * 115 + "\n\n")
            f.write(f"{'Year':<6} {'DOY':<5} {'Date (UTC)':<12} {'Station':<9} {'Status':<10} {'OK/Tot':<8} {'Missing Slots'}\n")
            f.write("-" * 115 + "\n")
            for rec in merged_list:
                f.write(
                    f"{rec['year']:<6} {rec['doy']:03d}   {rec['date_str']:<12} {rec['station']:<9} "
                    f"{rec['status']:<10} {rec['ok_cnt']}/{rec['total_cnt']:<6} {rec['missing_str']}\n"
                )
            f.write("=" * 115 + "\n")
            if audit_notes:
                f.write("\nPOST-SCAN GAP AUDIT & RETRY RECOVERY LOG:\n")
                for note in audit_notes:
                    f.write(f"  - {note}\n")

    def _fetch_all_days_pipelined(self, sess, cfg, day_to_stns, phase_label="Scanning", on_day_complete=None):
        import threading
        all_maps = {}
        hours = list(range(24)) if cfg["hourly"] else [0]
        ordered_days = sorted(day_to_stns.keys())
        if not ordered_days:
            return all_maps

        # Thread-local keep-alive sessions sharing cookies/auth/proxies safely
        tls = threading.local()
        def _get_thread_sess():
            if not hasattr(tls, "s"):
                creds = getattr(sess, "auth_creds", None) or (None, None)
                s = requests.Session()
                s.trust_env = True
                adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=1)
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                if getattr(sess, "auth", None):
                    s.auth = sess.auth
                elif creds[0] and creds[1]:
                    from requests.auth import HTTPBasicAuth
                    s.auth = HTTPBasicAuth(creds[0], creds[1])
                if getattr(sess, "proxies", None):
                    s.proxies.update(sess.proxies)
                if hasattr(sess, "cookies"):
                    for ck in sess.cookies:
                        s.cookies.set_cookie(ck)
                tls.s = s
            return tls.s

        day_buffers = {yd: {} for yd in ordered_days}
        day_counts = {yd: 0 for yd in ordered_days}
        started_days = set()
        buf_lock = threading.Lock()

        tasks = [(y, d, h) for (y, d) in ordered_days for h in hours]
        total_slots = len(tasks)
        done_slots = 0
        self.progress.emit(0, total_slots, f"{phase_label}: 0/{total_slots} directory slot(s)...")

        def _worker(task):
            if self.isInterruptionRequested():
                return task, {}
            y, d, h = task
            with buf_lock:
                if (y, d) not in started_days:
                    started_days.add((y, d))
                    for st in day_to_stns[(y, d)]:
                        self.row_scanning.emit(y, d, st, f"{phase_label}…")
            hh_str = f"{h:02d}"
            url = f"{BASE}/" + cfg["path"].format(Y=y, DDD=f"{d:03d}", yy=f"{y % 100:02d}", HH=hh_str)
            st_code, fmap = list_dir(_get_thread_sess(), url)
            if st_code not in ("ok", "NOT_FOUND"):
                self.log.emit(f"[THROTTLE GUARD] {y}-DOY{d:03d} Hr {hh_str}: {st_code}")
            return (y, d, hh_str), fmap

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_worker, t) for t in tasks]
            for fut in as_completed(futures):
                if self.isInterruptionRequested():
                    for pending_f in futures:
                        pending_f.cancel()
                    break
                res_key, fmap = fut.result()
                if len(res_key) != 3:
                    continue
                y, d, hh_str = res_key
                completed_d_maps = None
                with buf_lock:
                    done_slots += 1
                    cur_slot_done = done_slots
                    day_buffers[(y, d)][hh_str] = fmap
                    day_counts[(y, d)] += 1
                    if day_counts[(y, d)] == len(hours):
                        completed_d_maps = day_buffers.pop((y, d))
                        all_maps[(y, d)] = completed_d_maps
                self.progress.emit(cur_slot_done, total_slots, f"{phase_label} {y}-DOY{d:03d} Hr {hh_str} ({cur_slot_done}/{total_slots})")

                if completed_d_maps is not None and on_day_complete:
                    on_day_complete(y, d, day_to_stns[(y, d)], completed_d_maps)

        if hasattr(sess, "save_cookies_now"):
            sess.save_cookies_now()
        return all_maps

    def run(self):
        import threading
        scan_start_dt = dt.datetime.now()
        cfg = DATA_TYPES[self.dtype]
        sess = self.session or EarthdataSession()
        total_tasks = len(self.date_list) * len(self.stations)
        completed_tasks = 0
        day_records = []
        rows_by_key = {}
        day_to_uncached = {}
        records_lock = threading.Lock()

        os.makedirs(self.out_dir, exist_ok=True)
        summary_path = os.path.join(self.out_dir, "CUSTOM_SCAN_TOTAL_SUMMARY.txt")
        years_in_scan = sorted({y for (y, _) in self.date_list})

        def _flush_summaries(audit_notes=None):
            with records_lock:
                snap = list(day_records)
            if not snap:
                return
            now_dt = dt.datetime.now()
            self._write_total_summary(
                summary_path, snap, audit_notes=audit_notes, start_dt=scan_start_dt, end_dt=now_dt
            )
            if len(years_in_scan) > 1:
                for yr in years_in_scan:
                    yr_recs = [r for r in snap if r["year"] == yr]
                    if yr_recs:
                        yr_sum_path = os.path.join(self.out_dir, str(yr), f"CUSTOM_SCAN_{yr}_SUMMARY.txt")
                        self._write_total_summary(
                            yr_sum_path, yr_recs, audit_notes=audit_notes, start_dt=scan_start_dt, end_dt=now_dt
                        )

        try:
            for (y, d) in self.date_list:
                d_str = (dt.date(y, 1, 1) + dt.timedelta(days=d - 1)).strftime("%Y-%m-%d")
                expected_slots = 24 if cfg["hourly"] else 1
                for st in self.stations:
                    existing_fp = self._custom_doy_filepath(y, d, st)
                    cached_state, _ = parse_single_summary_file(existing_fp)
                    if cached_state == "ok":
                        completed_tasks += 1
                        self.progress.emit(completed_tasks, total_tasks, f"Cached OK · {st} {y} DOY {d:03d} ({completed_tasks}/{total_tasks})")
                        summary_item = dict(
                            year=y, doy=d, date_str=d_str, station=st,
                            status="COMPLETE", ok_cnt=expected_slots, total_cnt=expected_slots, missing_str="None"
                        )
                        with records_lock:
                            day_records.append(summary_item)
                        self.row_ready.emit(summary_item)
                    else:
                        day_to_uncached.setdefault((y, d), []).append(st)

            _flush_summaries()

            if day_to_uncached and not self.isInterruptionRequested():
                def _commit_completed_day(y, d, st_list, dir_maps):
                    nonlocal completed_tasks
                    if self.isInterruptionRequested():
                        return
                    d_str = (dt.date(y, 1, 1) + dt.timedelta(days=d - 1)).strftime("%Y-%m-%d")
                    for st in st_list:
                        st_rows = self._build_rows_from_dir_maps(cfg, y, d, st, dir_maps)
                        self._write_station_doy_report(y, d, st, st_rows)
                        ok_cnt = sum(1 for r in st_rows if r["status"] == "OK")
                        tot_cnt = len(st_rows)
                        miss_hrs = [r["hour_str"] for r in st_rows if r["status"] != "OK"]
                        day_status = "COMPLETE" if ok_cnt == tot_cnt else ("PARTIAL" if ok_cnt > 0 else "MISSING")
                        summary_item = dict(
                            year=y, doy=d, date_str=d_str, station=st,
                            status=day_status, ok_cnt=ok_cnt, total_cnt=tot_cnt,
                            missing_str=", ".join(miss_hrs) if miss_hrs else "None"
                        )
                        with records_lock:
                            rows_by_key[(y, d, st)] = st_rows
                            day_records.append(summary_item)
                            completed_tasks += 1
                            cur_done = completed_tasks
                        self.progress.emit(cur_done, total_tasks, f"Completed {st} · {y} DOY {d:03d} ({cur_done}/{total_tasks})")
                        self.row_ready.emit(summary_item)
                    _flush_summaries()

                self._fetch_all_days_pipelined(
                    sess, cfg, day_to_uncached, phase_label="Scanning", on_day_complete=_commit_completed_day
                )

            _flush_summaries()

            audit_notes = []
            if self.auto_audit and not self.isInterruptionRequested() and day_records:
                audit_day_to_stns = {}
                with records_lock:
                    for rec in day_records:
                        if rec["status"] in ("PARTIAL", "MISSING") or rec["ok_cnt"] < rec["total_cnt"]:
                            audit_day_to_stns.setdefault((rec["year"], rec["doy"]), []).append(rec["station"])

                if not audit_day_to_stns:
                    audit_notes.append("Audit verified 0 gaps across all scanned station-days.")
                else:
                    self.progress.emit(0, len(audit_day_to_stns), f"Audit Retry running across {len(audit_day_to_stns)} day(s)…")
                    retry_maps = self._fetch_all_days_pipelined(sess, cfg, audit_day_to_stns, phase_label="Audit Retry")
                    for (gy, gd), gst_list in sorted(audit_day_to_stns.items()):
                        d_maps = retry_maps.get((gy, gd), {})
                        for gst in gst_list:
                            with records_lock:
                                old_rows = rows_by_key.get((gy, gd, gst), [])
                            old_ok = sum(1 for r in old_rows if r["status"] == "OK")
                            refreshed_rows = self._build_rows_from_dir_maps(cfg, gy, gd, gst, d_maps)
                            new_ok = sum(1 for r in refreshed_rows if r["status"] == "OK")
                            if new_ok > old_ok:
                                self._write_station_doy_report(gy, gd, gst, refreshed_rows)
                                miss_hrs = [r["hour_str"] for r in refreshed_rows if r["status"] != "OK"]
                                new_status = "COMPLETE" if new_ok == len(refreshed_rows) else "PARTIAL"
                                with records_lock:
                                    rows_by_key[(gy, gd, gst)] = refreshed_rows
                                    for rec in day_records:
                                        if rec["year"] == gy and rec["doy"] == gd and rec["station"] == gst:
                                            rec["ok_cnt"] = new_ok
                                            rec["status"] = new_status
                                            rec["missing_str"] = ", ".join(miss_hrs) if miss_hrs else "None"
                                            self.row_ready.emit(dict(rec))
                                note = f"[RECOVERED] {gst} {gy}-DOY{gd:03d}: {old_ok}/{len(refreshed_rows)} -> {new_ok}/{len(refreshed_rows)}"
                            else:
                                note = f"[UNCHANGED] {gst} {gy}-DOY{gd:03d}: {new_ok}/{len(refreshed_rows)} available on CDDIS."
                            audit_notes.append(note)
                            self.log.emit(note)
                _flush_summaries(audit_notes=audit_notes)
        except Exception as exc:
            import traceback
            err_txt = f"Custom Scan error: {exc}"
            self.log.emit(f"[THROTTLE GUARD] {err_txt}")
            print(traceback.format_exc())
            _flush_summaries(audit_notes=[err_txt])

        self.progress.emit(total_tasks, total_tasks, "Completed")
        self.finished_all.emit(summary_path, day_records)


class ArchiveGapAnalyzerDialog(QtWidgets.QDialog):
    def __init__(self, parent, root_dir, stations, year):
        super().__init__(parent)
        self.setWindowTitle("Offline Archive Gap Audit")
        self.resize(780, 520)
        lay = QtWidgets.QVBoxLayout(self)
        tbl = QtWidgets.QTableWidget(0, 4)
        tbl.setHorizontalHeaderLabels(["Station", "DOY", "Status", "Details"])
        tbl.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        now_doy = dt.datetime.now(dt.timezone.utc).timetuple().tm_yday
        for st in stations:
            for d in range(max(1, now_doy - 30), now_doy):
                fp = get_station_doy_filepath(root_dir, st, year, d)
                state, det = parse_single_summary_file(fp)
                r = tbl.rowCount()
                tbl.insertRow(r)
                for c, val in enumerate([st, f"{d:03d}", state.upper(), det]):
                    tbl.setItem(r, c, QtWidgets.QTableWidgetItem(val))
        lay.addWidget(tbl)
        btn = QtWidgets.QPushButton("Close")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("CDDM Preferences")
        self.resize(400, 180)
        s = QtCore.QSettings("gnss-tools", "cddis-monitor")
        lay = QtWidgets.QVBoxLayout(self)
        chk_auto = QtWidgets.QCheckBox("Launch automatically on Linux desktop login")
        chk_auto.setChecked(s.value("autostart", True, type=bool))
        lay.addWidget(chk_auto)
        btn_save = QtWidgets.QPushButton("Save & Close")
        def _save():
            s.setValue("autostart", chk_auto.isChecked())
            s.sync()
            install_linux_autostart(chk_auto.isChecked())
            self.accept()
        btn_save.clicked.connect(_save)
        lay.addWidget(btn_save)


class StationProbeWorker(QtCore.QThread):
    finished_probe = QtCore.Signal(str)

    def __init__(self, session, stations, dtype, parent=None):
        super().__init__(parent)
        self.session = session
        self.stations = stations
        self.dtype = dtype

    def run(self):
        ref_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        probe_y = ref_dt.year
        probe_d = ref_dt.timetuple().tm_yday
        ddd_str = f"{probe_d:03d}"
        yy_str = f"{probe_y % 100:02d}"

        cfg = DATA_TYPES[self.dtype]
        probe_url = f"{BASE}/" + cfg["path"].format(Y=probe_y, DDD=ddd_str, yy=yy_str, HH="00")

        sess = self.session or EarthdataSession()
        state, fmap = list_dir(sess, probe_url)

        daily_fmap = {}
        if cfg["hourly"]:
            daily_url = f"{BASE}/daily/{probe_y}/{ddd_str}/{yy_str}d"
            _, daily_fmap = list_dir(sess, daily_url)

        if state != "ok" and not fmap and not daily_fmap:
            self.finished_probe.emit(
                f"<span style='color:#d92d20; font-weight:600;'>Archive unreachable ({state}) on baseline {probe_y}-DOY{ddd_str}</span>"
            )
            return

        findings = []
        for st in self.stations:
            matches = [fn for fn in fmap if fn.lower().startswith(st.lower())]
            if matches:
                findings.append(f"<span style='color:#15803d; font-weight:600;'>● {st}: Available ({matches[0]})</span>")
            elif cfg["hourly"] and any(fn.lower().startswith(st.lower()) for fn in daily_fmap):
                findings.append(f"<span style='color:#c2410c; font-weight:600;'>▲ {st}: Daily-only station (no Hourly stream)</span>")
            else:
                findings.append(f"<span style='color:#d92d20; font-weight:600;'>✕ {st}: Not found on CDDIS ({self.dtype})</span>")
        self.finished_probe.emit("   ·   ".join(findings))


class CustomScanDialog(QtWidgets.QDialog):
    def __init__(self, parent, session, default_stations="", default_dtype="Hourly observation", default_out_dir=""):
        super().__init__(parent)
        self.session = session
        self.worker = None
        self._active_row_idx = None
        self._pulse_phase = 0
        self.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
            | QtCore.Qt.WindowCloseButtonHint
        )
        self.setWindowTitle("Custom Archive Scan & Post-Audit")
        self.resize(960, 680)

        # Apple HIG Clean Light Theme
        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f7;
                color: #1d1d1f;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QGroupBox {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 10px;
                margin-top: 10px;
                padding: 14px 12px 10px 12px;
                font-weight: 600;
                font-size: 12px;
                color: #1d1d1f;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                background-color: #f5f5f7;
                border-radius: 4px;
                color: #424245;
            }
            QLabel {
                color: #1d1d1f;
                font-size: 12px;
            }
            QRadioButton, QCheckBox {
                color: #1d1d1f;
                font-size: 12px;
                spacing: 6px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                padding: 5px 9px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1.5px solid #0071e3;
            }
            QLineEdit:disabled, QSpinBox:disabled {
                background-color: #f5f5f7;
                color: #86868b;
            }
            QTableWidget {
                background-color: #ffffff;
                color: #1d1d1f;
                gridline-color: #ececef;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                font-size: 12px;
                selection-background-color: #e8f2ff;
                selection-color: #1d1d1f;
            }
            QHeaderView::section {
                background-color: #fbfbfd;
                color: #424245;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid #d2d2d7;
                border-right: 1px solid #ececef;
                font-weight: 600;
                font-size: 11px;
            }
            QPlainTextEdit {
                background-color: #ffffff;
                color: #333336;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                padding: 6px;
                font-family: monospace;
                font-size: 11px;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Card 1: Station & File Type Selection
        st_group = QtWidgets.QGroupBox("1. Station && File Type Selection")
        st_lay = QtWidgets.QVBoxLayout(st_group)
        st_lay.setSpacing(8)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QtWidgets.QLabel("Station ID(s):"))
        self.st_input = QtWidgets.QLineEdit(default_stations)
        self.st_input.setPlaceholderText("e.g. IISC, HYDE, LCK4")
        row1.addWidget(self.st_input, 1)

        row1.addWidget(QtWidgets.QLabel("Data Type:"))
        self.dtype_combo = QtWidgets.QComboBox()
        self.dtype_combo.addItems(DATA_TYPES.keys())
        idx = self.dtype_combo.findText(default_dtype)
        if idx >= 0:
            self.dtype_combo.setCurrentIndex(idx)
        row1.addWidget(self.dtype_combo)

        self.btn_verify_st = QtWidgets.QPushButton("Check Station Availability")
        self.btn_verify_st.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_verify_st.setStyleSheet("""
            QPushButton {
                background-color: #e8f2ff;
                color: #0071e3;
                border: 1px solid #b6d8ff;
                border-radius: 6px;
                padding: 5px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #d8eaff; }
            QPushButton:disabled { background-color: #f5f5f7; color: #86868b; border-color: #d2d2d7; }
        """)
        self.btn_verify_st.clicked.connect(self.check_station_availability)
        row1.addWidget(self.btn_verify_st)
        st_lay.addLayout(row1)

        self.avail_lbl = QtWidgets.QLabel("Probe a recent baseline DOY to confirm whether each station publishes hourly or daily-only files on CDDIS.")
        self.avail_lbl.setStyleSheet("color: #6e6e73; font-size: 11px;")
        self.avail_lbl.setWordWrap(True)
        st_lay.addWidget(self.avail_lbl)
        layout.addWidget(st_group)

        # Card 2: Custom DOY / Multi-Year Date Range
        rng_group = QtWidgets.QGroupBox("2. Custom DOY / Multi-Year Date Range")
        rng_lay = QtWidgets.QVBoxLayout(rng_group)
        rng_lay.setSpacing(8)

        mode_row = QtWidgets.QHBoxLayout()
        self.rb_single = QtWidgets.QRadioButton("Particular DOY(s) (comma-separated in a Year)")
        self.rb_range = QtWidgets.QRadioButton("From Year / DOY  →  To Year / DOY")
        self.rb_range.setChecked(True)
        mode_row.addWidget(self.rb_single)
        mode_row.addWidget(self.rb_range)
        mode_row.addStretch()
        rng_lay.addLayout(mode_row)

        now_utc = dt.datetime.now(dt.timezone.utc)
        cur_y = now_utc.year
        cur_doy = max(1, now_utc.timetuple().tm_yday - 1)

        single_row = QtWidgets.QHBoxLayout()
        single_row.setSpacing(8)
        single_row.addWidget(QtWidgets.QLabel("Year:"))
        self.single_year = QtWidgets.QSpinBox()
        self.single_year.setRange(1990, 2099)
        self.single_year.setValue(cur_y)
        single_row.addWidget(self.single_year)

        single_row.addWidget(QtWidgets.QLabel("DOY(s):"))
        self.single_doys = QtWidgets.QLineEdit(f"{cur_doy:03d}")
        self.single_doys.setPlaceholderText("e.g. 120, 125, 130-135")
        single_row.addWidget(self.single_doys, 1)
        rng_lay.addLayout(single_row)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setSpacing(8)
        range_row.addWidget(QtWidgets.QLabel("From Year:"))
        self.from_year = QtWidgets.QSpinBox()
        self.from_year.setRange(1990, 2099)
        self.from_year.setValue(cur_y)
        range_row.addWidget(self.from_year)

        range_row.addWidget(QtWidgets.QLabel("From DOY:"))
        self.from_doy = QtWidgets.QSpinBox()
        self.from_doy.setRange(1, 366)
        self.from_doy.setValue(max(1, cur_doy - 2))
        range_row.addWidget(self.from_doy)

        range_row.addWidget(QtWidgets.QLabel("   To Year:"))
        self.to_year = QtWidgets.QSpinBox()
        self.to_year.setRange(1990, 2099)
        self.to_year.setValue(cur_y)
        range_row.addWidget(self.to_year)

        range_row.addWidget(QtWidgets.QLabel("To DOY:"))
        self.to_doy = QtWidgets.QSpinBox()
        self.to_doy.setRange(1, 366)
        self.to_doy.setValue(cur_doy)
        range_row.addWidget(self.to_doy)
        range_row.addStretch()
        rng_lay.addLayout(range_row)

        def _toggle_inputs():
            is_s = self.rb_single.isChecked()
            self.single_year.setEnabled(is_s)
            self.single_doys.setEnabled(is_s)
            self.from_year.setEnabled(not is_s)
            self.from_doy.setEnabled(not is_s)
            self.to_year.setEnabled(not is_s)
            self.to_doy.setEnabled(not is_s)

        self.rb_single.toggled.connect(_toggle_inputs)
        self.rb_range.toggled.connect(_toggle_inputs)
        _toggle_inputs()
        layout.addWidget(rng_group)

        # Card 3: Output Folder & Post-Scan Audit
        out_group = QtWidgets.QGroupBox("3. Output Folder && Post-Scan Gap Audit")
        out_lay = QtWidgets.QHBoxLayout(out_group)
        out_lay.setSpacing(8)

        out_lay.addWidget(QtWidgets.QLabel("Output Folder:"))
        self.out_input = QtWidgets.QLineEdit(default_out_dir or os.path.expanduser("~/Downloads/CDDM_Custom"))
        out_lay.addWidget(self.out_input, 1)

        btn_browse = QtWidgets.QPushButton("Browse…")
        btn_browse.setCursor(QtCore.Qt.PointingHandCursor)
        btn_browse.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #e8e8ed; }
        """)
        btn_browse.clicked.connect(self._browse_out)
        out_lay.addWidget(btn_browse)

        self.chk_audit = QtWidgets.QCheckBox("Auto Gap-Audit && Retry Missing")
        self.chk_audit.setChecked(True)
        out_lay.addWidget(self.chk_audit)
        layout.addWidget(out_group)

        # Unified Action Pill Bar: Single Start/Stop Toggle + Status Label + Sleek 4px Progress Line
        ctrl_card = QtWidgets.QFrame()
        ctrl_card.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 10px;
            }
        """)
        ctrl_v = QtWidgets.QVBoxLayout(ctrl_card)
        ctrl_v.setContentsMargins(12, 8, 12, 8)
        ctrl_v.setSpacing(6)

        act_row = QtWidgets.QHBoxLayout()
        act_row.setSpacing(12)

        self.btn_toggle_scan = QtWidgets.QPushButton("▶  Start Custom Scan")
        self.btn_toggle_scan.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_toggle_scan.setFixedHeight(32)
        self.btn_toggle_scan.setMinimumWidth(185)
        self.btn_toggle_scan.clicked.connect(self.toggle_scan)
        self._apply_toggle_btn_style(running=False)
        act_row.addWidget(self.btn_toggle_scan)

        self.status_pill_lbl = QtWidgets.QLabel("Ready to scan")
        self.status_pill_lbl.setStyleSheet("color: #6e6e73; font-size: 12px; font-weight: 500; border: none;")
        act_row.addWidget(self.status_pill_lbl, 1)

        self.pct_lbl = QtWidgets.QLabel("0%")
        self.pct_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.pct_lbl.setStyleSheet("""
            QLabel {
                color: #1d1d1f;
                background-color: #f5f5f7;
                border: 1px solid #e5e5ea;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 12px;
                font-weight: 700;
                font-variant-numeric: tabular-nums;
            }
        """)
        act_row.addWidget(self.pct_lbl)

        ctrl_v.addLayout(act_row)

        self.prog_bar = QtWidgets.QProgressBar()
        self.prog_bar.setFixedHeight(4)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 2px;
                background-color: #e5e5ea;
            }
            QProgressBar::chunk {
                background-color: #0071e3;
                border-radius: 2px;
            }
        """)
        self.prog_bar.setValue(0)
        ctrl_v.addWidget(self.prog_bar)
        layout.addWidget(ctrl_card)

        # Live Table with Animated Active-Row Highlight
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Year", "DOY", "Date (UTC)", "Station", "Status", "OK / Total", "Missing Slots"
        ])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, 1)



        # Gentle pulse timer for active scanning row background shimmer
        self._row_anim_timer = QtCore.QTimer(self)
        self._row_anim_timer.setInterval(120)
        self._row_anim_timer.timeout.connect(self._animate_active_row)

    def _apply_toggle_btn_style(self, running=False):
        if running:
            self.btn_toggle_scan.setText("⏹  Stop Scan")
            self.btn_toggle_scan.setStyleSheet("""
                QPushButton {
                    background-color: #fff1f0;
                    color: #d92d20;
                    border: 1px solid #fecdca;
                    border-radius: 7px;
                    padding: 5px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #fee4e2;
                    border-color: #fda29b;
                }
                QPushButton:disabled {
                    background-color: #f5f5f7;
                    color: #86868b;
                    border-color: #d2d2d7;
                }
            """)
        else:
            self.btn_toggle_scan.setText("▶  Start Custom Scan")
            self.btn_toggle_scan.setStyleSheet("""
                QPushButton {
                    background-color: #0071e3;
                    color: #ffffff;
                    border: none;
                    border-radius: 7px;
                    padding: 5px 16px;
                    font-weight: 600;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #0077ed;
                }
                QPushButton:disabled {
                    background-color: #a1c6ea;
                    color: #ffffff;
                }
            """)

    def _animate_active_row(self):
        if self._active_row_idx is None or self._active_row_idx >= self.table.rowCount():
            return
        self._pulse_phase = (self._pulse_phase + 1) % 12
        # Soft pastel sky-blue shimmer (#f2f8ff -> #dfeeff -> #f2f8ff)
        wave = abs(6 - self._pulse_phase) / 6.0
        r = int(242 - 19 * wave)
        g = int(248 - 10 * wave)
        b = 255
        bg = QtGui.QBrush(QtGui.QColor(r, g, b))
        for c in range(self.table.columnCount()):
            it = self.table.item(self._active_row_idx, c)
            if it:
                it.setBackground(bg)

    def _browse_out(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Folder for Custom Scan", self.out_input.text())
        if folder:
            self.out_input.setText(folder)

    def _parse_stations(self):
        raw = re.split(r"[,\s;]+", self.st_input.text().strip())
        out = []
        for s in raw:
            s = s.strip().upper()
            if len(s) in (4, 9) and s not in out:
                out.append(s)
        return out

    def _build_date_list(self):
        dates = []
        if self.rb_single.isChecked():
            y = self.single_year.value()
            max_d = 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365
            for token in re.split(r"[,\s;]+", self.single_doys.text().strip()):
                if not token:
                    continue
                if "-" in token:
                    a, b = token.split("-", 1)
                    if a.isdigit() and b.isdigit():
                        for d in range(max(1, int(a)), min(max_d, int(b)) + 1):
                            if (y, d) not in dates:
                                dates.append((y, d))
                elif token.isdigit():
                    d = int(token)
                    if 1 <= d <= max_d and (y, d) not in dates:
                        dates.append((y, d))
        else:
            d1 = dt.date(self.from_year.value(), 1, 1) + dt.timedelta(days=self.from_doy.value() - 1)
            d2 = dt.date(self.to_year.value(), 1, 1) + dt.timedelta(days=self.to_doy.value() - 1)
            if d1 > d2:
                d1, d2 = d2, d1
            cur = d1
            while cur <= d2:
                dates.append((cur.year, cur.timetuple().tm_yday))
                cur += dt.timedelta(days=1)
        return dates

    def check_station_availability(self):
        stations = self._parse_stations()
        if not stations:
            QtWidgets.QMessageBox.warning(self, "Station Required", "Enter at least one 4-char or 9-char station ID.")
            return
        if getattr(self, "_probe_worker", None) and self._probe_worker.isRunning():
            return

        self.btn_verify_st.setEnabled(False)
        self.btn_verify_st.setText("Checking…")
        self.avail_lbl.setText(f"<span style='color:#0071e3;'>Checking CDDIS baseline for {', '.join(stations)}…</span>")

        self._probe_worker = StationProbeWorker(
            self.session, stations, self.dtype_combo.currentText(), parent=self
        )
        def _on_probe_done(html_res):
            self.btn_verify_st.setEnabled(True)
            self.btn_verify_st.setText("Check Station Availability")
            self.avail_lbl.setText(html_res)

        self._probe_worker.finished_probe.connect(_on_probe_done)
        self._probe_worker.start()

    def toggle_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.btn_toggle_scan.setEnabled(False)
            self.status_pill_lbl.setText("Stopping scan after current slot…")
            return
        self.start_scan()

    def _prefill_queue_rows(self, stations, dates):
        self.table.setRowCount(0)
        for (y, d) in dates:
            d_str = (dt.date(y, 1, 1) + dt.timedelta(days=d - 1)).strftime("%Y-%m-%d")
            for st in stations:
                r = self.table.rowCount()
                self.table.insertRow(r)
                vals = [str(y), f"{d:03d}", d_str, st, "QUEUED", "–", "–"]
                for c, v in enumerate(vals):
                    it = QtWidgets.QTableWidgetItem(v)
                    if c == 4:
                        it.setForeground(QtGui.QBrush(QtGui.QColor("#86868b")))
                    self.table.setItem(r, c, it)

    def start_scan(self):
        stations = self._parse_stations()
        if not stations:
            QtWidgets.QMessageBox.warning(self, "Station Required", "Enter at least one station ID.")
            return
        dates = self._build_date_list()
        if not dates:
            QtWidgets.QMessageBox.warning(self, "Date Range Invalid", "Specify at least one valid Year and DOY.")
            return
        out_dir = self.out_input.text().strip()
        if not out_dir:
            QtWidgets.QMessageBox.warning(self, "Output Folder Required", "Select an output folder.")
            return

        self._prefill_queue_rows(stations, dates)
        self._apply_toggle_btn_style(running=True)
        self.btn_toggle_scan.setEnabled(True)
        self.status_pill_lbl.setText("Initializing scan…")
        self.pct_lbl.setText("0%")
        self.pct_lbl.setStyleSheet("""
            QLabel {
                color: #0071e3;
                background-color: #eef6ff;
                border: 1px solid #cce3ff;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 12px;
                font-weight: 700;
            }
        """)
        self._row_anim_timer.start()

        self.worker = CustomScanWorker(
            self.session,
            self.dtype_combo.currentText(),
            stations,
            dates,
            out_dir,
            auto_audit=self.chk_audit.isChecked(),
            parent=self
        )
        self.worker.log.connect(self._on_worker_notice)
        self.worker.progress.connect(self._on_progress)
        self.worker.row_scanning.connect(self._on_row_scanning)
        self.worker.row_ready.connect(self._on_row_ready)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.start()

    def _find_row(self, year, doy, station):
        y_str = str(year)
        d_str = f"{doy:03d}"
        st_u = station.upper()
        for r in range(self.table.rowCount()):
            if (
                self.table.item(r, 0).text() == y_str
                and self.table.item(r, 1).text() == d_str
                and self.table.item(r, 3).text() == st_u
            ):
                return r
        return None

    def _on_row_scanning(self, year, doy, station, phase_text):
        r = self._find_row(year, doy, station)
        if r is None:
            return
        self._active_row_idx = r
        self.table.scrollToItem(self.table.item(r, 0), QtWidgets.QAbstractItemView.EnsureVisible)
        st_item = self.table.item(r, 4)
        if st_item:
            st_item.setText(phase_text)
            st_item.setForeground(QtGui.QBrush(QtGui.QColor("#0071e3")))
            f = st_item.font()
            f.setBold(True)
            st_item.setFont(f)

    def _on_worker_notice(self, msg):
        if "[THROTTLE GUARD]" in msg or "Audit" in msg or "[RECOVERED]" in msg:
            self.status_pill_lbl.setText(msg.replace("[THROTTLE GUARD] ", ""))

    def _on_progress(self, cur, tot, msg):
        safe_tot = max(1, tot)
        pct = int(round((cur / safe_tot) * 100))
        self.prog_bar.setMaximum(safe_tot)
        self.prog_bar.setValue(cur)
        self.status_pill_lbl.setText(msg)
        self.pct_lbl.setText(f"{pct}%")

    def _on_row_ready(self, rec):
        target_row = self._find_row(rec["year"], rec["doy"], rec["station"])
        if target_row is None:
            target_row = self.table.rowCount()
            self.table.insertRow(target_row)

        if self._active_row_idx == target_row:
            self._active_row_idx = None

        # Settle row background to a clean, subtle Apple pastel tint
        if rec["status"] == "COMPLETE":
            row_bg = QtGui.QBrush(QtGui.QColor("#f6fef9"))
            fg_color = "#15803d"
        elif rec["status"] == "PARTIAL":
            row_bg = QtGui.QBrush(QtGui.QColor("#fffcf5"))
            fg_color = "#c2410c"
        else:
            row_bg = QtGui.QBrush(QtGui.QColor("#fffafa"))
            fg_color = "#d92d20"

        vals = [
            str(rec["year"]),
            f"{rec['doy']:03d}",
            rec["date_str"],
            rec["station"],
            rec["status"],
            f"{rec['ok_cnt']}/{rec['total_cnt']}",
            rec["missing_str"]
        ]
        for c, v in enumerate(vals):
            it = QtWidgets.QTableWidgetItem(v)
            it.setBackground(row_bg)
            if c == 4:
                it.setForeground(QtGui.QBrush(QtGui.QColor(fg_color)))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.table.setItem(target_row, c, it)

    def _on_finished(self, summary_path, records):
        self._row_anim_timer.stop()
        self._active_row_idx = None
        self._apply_toggle_btn_style(running=False)
        self.btn_toggle_scan.setEnabled(True)
        self.status_pill_lbl.setText(f"Completed · {len(records)} station-day(s) audited")
        self.pct_lbl.setText("100%")
        self.pct_lbl.setStyleSheet("""
            QLabel {
                color: #15803d;
                background-color: #f0fdf4;
                border: 1px solid #bbf7d0;
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 12px;
                font-weight: 700;
            }
        """)
        QtWidgets.QMessageBox.information(
            self,
            "Custom Scan & Audit Complete",
            f"All DOY reports and consolidated summary saved to:\n{summary_path}"
        )

class ConnectionCheckWorker(QtCore.QThread):
    # emits (status_code, pill_text, detail_report)
    finished_check = QtCore.Signal(str, str, str)

    def __init__(self, session, repair=False, parent=None):
        super().__init__(parent)
        self.session = session
        self.repair = repair

    def run(self):
        lines = []
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
        )
        if not proxy_url and self.session is not None and getattr(self.session, "proxies", None):
            proxy_url = self.session.proxies.get("https") or self.session.proxies.get("http")

        try:
            proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else None
            r_head = requests.head(
                "https://cddis.nasa.gov/archive/",
                proxies=proxies,
                timeout=10,
                allow_redirects=False,
            )
            via = f"via proxy ({proxy_url})" if proxy_url else "via direct HTTPS"
            lines.append(f"✔ [1/3] Network: Reachable {via} [HTTP {r_head.status_code}].")
        except Exception as e:
            lines.append(f"✖ [1/3] Network: Cannot reach cddis.nasa.gov ({e}).")
            self.finished_check.emit("offline", "Disconnected", "\n".join(lines))
            return

        sess = self.session or EarthdataSession()
        if self.repair:
            try:
                creds = getattr(sess, "auth_creds", None) or (None, None)
                sess = EarthdataSession(username=creds[0], password=creds[1])
                lines.append("↻ [2/3] Session: Re-initialized EarthdataSession & cookie jar.")
            except Exception as e:
                lines.append(f"✖ [2/3] Session Repair failed: {e}")

        try:
            r = sess.get(f"{BASE}/daily/", timeout=20, allow_redirects=True)
            if r.status_code == 200 and "urs.earthdata.nasa.gov" not in r.url:
                lines.append("✔ [3/3] Earthdata Auth: Authenticated (HTTP 200 OK on CDDIS archive).")
                self.finished_check.emit("connected", "CDDIS Connected", "\n".join(lines))
            elif r.status_code in (401, 403) or "urs.earthdata.nasa.gov" in r.url:
                lines.append(f"▲ [3/3] Earthdata Auth: Session expired or unauthorized (HTTP {r.status_code}).")
                self.finished_check.emit("expired", "Session Expired", "\n".join(lines))
            else:
                lines.append(f"✖ [3/3] CDDIS Archive returned HTTP {r.status_code}.")
                self.finished_check.emit("offline", f"HTTP {r.status_code}", "\n".join(lines))
        except Exception as e:
            lines.append(f"✖ [3/3] Auth Probe Error: {e}")
            self.finished_check.emit("offline", "Connection Error", "\n".join(lines))


class ConnectionTroubleshooterDialog(QtWidgets.QDialog):
    status_updated = QtCore.Signal(str, str)

    def __init__(self, parent, session):
        super().__init__(parent)
        self.session = session
        self.worker = None
        self.setWindowTitle("CDDIS Connection Troubleshooter & Session Repair")
        self.resize(620, 380)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        hdr = QtWidgets.QLabel("<b>NASA CDDIS & Earthdata Connectivity Diagnostics</b>")
        hdr.setStyleSheet("font-size: 14px; color: #1d1d1f;")
        lay.addWidget(hdr)

        self.report_box = QtWidgets.QPlainTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setStyleSheet("font-family: monospace; font-size: 12px; background: #f8fafc; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px;")
        self.report_box.setPlainText("Click 'Run Diagnostic' or 'Repair Session' to test network, proxy, and Earthdata authentication.")
        lay.addWidget(self.report_box, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_diag = QtWidgets.QPushButton("Run Diagnostic")
        self.btn_repair = QtWidgets.QPushButton("Repair Session")
        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_diag.clicked.connect(lambda: self._run_probe(False))
        self.btn_repair.clicked.connect(lambda: self._run_probe(True))
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_diag)
        btn_row.addWidget(self.btn_repair)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

        QtCore.QTimer.singleShot(150, lambda: self._run_probe(False))

    def _run_probe(self, repair):
        self.btn_diag.setEnabled(False)
        self.btn_repair.setEnabled(False)
        self.report_box.setPlainText("Probing institutional proxy, cddis.nasa.gov, and Earthdata OAuth session…")
        self.worker = ConnectionCheckWorker(self.session, repair=repair, parent=self)
        def _done(st, pill_txt, rep):
            self.btn_diag.setEnabled(True)
            self.btn_repair.setEnabled(True)
            self.report_box.setPlainText(rep)
            self.status_updated.emit(st, pill_txt)
        self.worker.finished_check.connect(_done)
        self.worker.start()


class MainWindow(QtWidgets.QMainWindow):
    COLS = [
        "Station",
        "Hour / Slot (UTC)",
        "Status",
        "Uploaded File(s)",
        "Files",
        "Size",
        "Last-Modified",
        "Checked (UTC)", "IST"
    ]

    def __init__(self, session, is_autostart=False):
        super().__init__()
        self.TIMEZONES = TIMEZONES
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cddis_icon.png")))
        QtCore.QCoreApplication.setApplicationName("CDDM")
        self.session = session
        self.is_autostart = is_autostart
        self.worker = None
        self.bg_doy_worker = None
        self.pending_doy_queue = []
        self.doy_badges = {}

        self.alerted = set()
        self.latest_rows = []
        self.is_monitoring = False
        self.settings = QtCore.QSettings("gnss-tools", "cddis-monitor")
        self.last_daily_report_date = None

        self.setWindowTitle("CDDM Live v1.1")
        self.resize(1340, 890)

        # 1. Build UI widgets first
        self._build_ui()
        # Audio alert engine
        self._setup_audio_engine()


        # 2. Safely load settings via singleShot timer AFTER event loop and C++ bindings are active
        QtCore.QTimer.singleShot(0, self._delayed_init)

    def _delayed_init(self):
        saved_tz = self.settings.value('display_tz', 'IST (UTC+05:30)')
        self._update_all_tz_columns(saved_tz)
        self._load_settings()
        self.stations.textChanged.connect(self._on_stations_changed)
        autostart_on = self.settings.value('autostart', True, type=bool)
        install_linux_autostart(autostart_on)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.run_check)

        self.daily_report_timer = QtCore.QTimer(self)
        self.daily_report_timer.timeout.connect(self._check_daily_report_trigger)
        self.daily_report_timer.start(30000)

        initial_icon = QtGui.QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cddis_icon.png"))
        self.setWindowIcon(initial_icon)
        self.tray = None
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            try:
                self.tray = QtWidgets.QSystemTrayIcon(initial_icon, self)
                self.tray.show()
                self.tray.messageClicked.connect(self.stop_alert_audio)
            except Exception:
                self.tray = None

        self._update_doy_date_preview()
        self._on_mode_changed()
        self.update_satellite_logo("#6c757d")

        self.refresh_doy_strip()
        QtCore.QTimer.singleShot(400, self._startup_live_then_catchup)

    def update_satellite_logo(self, color_hex):
        try:
            icon = create_satellite_icon(color_hex)
            self.setWindowIcon(icon)
            if hasattr(self, "tray") and self.tray and self.tray.isVisible():
                self.tray.setIcon(icon)
        except Exception as e:
            pass

    def _apply_icon_file(self, path):
        if os.path.exists(path):
            icon = QtGui.QIcon(path)
            self.setWindowIcon(icon)
            if self.tray and self.tray.isVisible():
                self.tray.setIcon(icon)

    
    def _show_how_to_use_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Documentation & User Guide — CDDM")
        dlg.resize(740, 640)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)

        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 10px;
                padding: 16px;
                color: #1d1d1f;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
        """)

        guide_html = """
        <style>
            body { font-size: 13px; line-height: 1.6; color: #1d1d1f; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
            h2 { color: #0071e3; font-size: 20px; margin: 0 0 4px 0; font-weight: 700; }
            .subtitle { color: #86868b; font-size: 12px; margin-bottom: 16px; }
            .section-card { background: #fbfbfd; border: 1px solid #e5e5ea; border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
            .section-title { font-size: 14px; font-weight: 700; color: #1d1d1f; margin-top: 0; margin-bottom: 8px; }
            .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
            .badge-blue { background: #e8f2ff; color: #0071e3; }
            .badge-amber { background: #fff3dc; color: #b25e00; }
            .badge-green { background: #eafaf1; color: #1b873f; }
            .badge-red { background: #fee2e2; color: #dc2626; }
            .code-box { background: #f5f5f7; border: 1px solid #d2d2d7; border-radius: 6px; padding: 4px 8px; font-family: monospace; font-size: 11px; color: #0f172a; word-break: break-all; }
            .faq-q { font-weight: 600; color: #0071e3; margin-top: 10px; margin-bottom: 2px; }
            .faq-a { color: #424245; margin-bottom: 10px; }
            ul { margin: 6px 0 6px 18px; padding: 0; }
            li { margin-bottom: 4px; }
        </style>

        <h2>CDDM Operational Guide & Reference</h2>
        <div class="subtitle">Crustal Dynamics Data Monitor — Geodesy & Seismology GNSS Ingestion Utility</div>

        <div class="section-card">
            <div class="section-title">1. Real-Time Hourly Monitoring</div>
            <p>CDDM tracks NASA CDDIS archive directories in real time to verify that hourly GNSS observation files arrive on schedule without data loss.</p>
            <ul>
                <li><b>Configuration:</b> Set <b>Data type</b> to <code>Hourly observation</code> and enter four-character IGS station identifiers separated by commas (e.g., <code>IISC, IITK, POL2</code>).</li>
                <li><b>Live Streaming:</b> When monitoring starts, all slots from <code>Hour 00</code> up to the active hour appear immediately as <span class="badge badge-amber">PENDING</span>. As each hour is verified on CDDIS, the row dynamically updates to <span class="badge badge-green">OK</span> or <span class="badge badge-red">MISSING</span>.</li>
                <li><b>Smooth Progress Animation:</b> A non-blocking, continuous sliding shimmer indicator provides real-time feedback during multi-threaded network polling.</li>
                <li><b>Persistent State:</b> Every verified slot is automatically saved to disk in:
                    <div style="margin-top: 6px;"><span class="code-box">&lt;Station Folders Root&gt;/&lt;STATION&gt;/&lt;DDD&gt;.txt</span></div>
                    Past verified hours load instantly from local summaries, preventing redundant network sweeps.</li>
            </ul>
        </div>

        <div class="section-card">
            <div class="section-title">2. Offline Archive Gap Audit (Matrix Tool)</div>
            <p>The <b>📊 Gap Audit</b> utility parses your local historical summaries completely offline without querying NASA servers or experiencing network latency.</p>
            <ul>
                <li><b>Station & DOY Range:</b> Audit specific stations or <code>ALL STATIONS</code> across any chosen Julian Day window.</li>
                <li><b>Interactive Filter View:</b> Toggle between <span class="badge badge-green">✔ Complete (24/24)</span> and <span class="badge badge-red">✖ Missing / Incomplete / Unscanned</span> days with real-time row filtering.</li>
                <li><b>Filter-Aware CSV Export:</b> The <b>Export CSV</b> button exports strictly the visible, filtered subset of rows to disk.</li>
            </ul>
        </div>

        <div class="section-card">
            <div class="section-title">3. Intelligent Gap Re-Check (↻ Recheck Gaps)</div>
            <p>Clicking <b>↻ Recheck Gaps</b> selectively identifies incomplete past days without rescanning verified data:</p>
            <ul>
                <li><b>Selective Re-Queuing:</b> Automatically skips days with <code>24/24 OK</code> slots, focusing network requests solely on incomplete or missing days.</li>
                <li><b>Summary Synchronization:</b> When missing files are retrieved from CDDIS, local summaries (<code>&lt;DDD&gt;.txt</code>) update immediately, shifting badges to <span class="badge badge-green">✔ Complete</span>.</li>
            </ul>
        </div>

        <div class="section-card">
            <div class="section-title">4. Strict 24/24 Verification Standard</div>
            <p>To eliminate false positives, past Julian Days must account for all 24 slots (<code>24/24 OK</code>) before badge ribbons display green. Days interrupted mid-scan (e.g., <code>10/24 slots</code>) are clearly marked incomplete.</p>
        </div>

        <div class="section-card">
            <div class="section-title">5. Dynamic Station Hot-Sync & Multi-Timezone Header</div>
            <ul>
                <li><b>Hot-Sync:</b> Add or remove stations during active live scans without restarting. Click the blue <b>⟳ Sync</b> button to add station tabs dynamically.</li>
                <li><b>Interactive Timezones:</b> Switch between <b>IST</b> (UTC+05:30), <b>UTC</b>, <b>JST</b>, <b>CET</b>, <b>EST</b>, and <b>PST</b>. Scan timestamps and work logs re-project instantly.</li>
            </ul>
        </div>

        <div class="section-card">
            <div class="section-title">6. Frequently Asked Questions (Troubleshooting)</div>
            <div class="faq-q">Q: Why does HYDE show MISSING for all hourly slots?</div>
            <div class="faq-a">Station <code>HYDE</code> (Hyderabad) uploads only 24-hour daily composite files to CDDIS, not hourly streams. Switch <b>Data type</b> to <code>Daily observation</code> to audit <code>HYDE</code>.</div>

            <div class="faq-q">Q: What happens if hourly telemetry drops but daily data is intact?</div>
            <div class="faq-a">If a station experiences an hourly upload outage (e.g., DOY 257), switch to <code>Daily observation (RINEX)</code> to verify the full 24-hour daily archive file (<code>*.crx.gz</code>).</div>

            <div class="faq-q">Q: What do the badge colors in Archive History represent?</div>
            <div class="faq-a">• <span class="badge badge-green">Green (✔)</span>: Fully verified 24/24 hours archived.<br>
            • <span class="badge badge-red">Red (✖)</span>: Incomplete hours or missing data detected.<br>
            • <span class="badge" style="background:#adb5bd;color:#212529;">Grey (○)</span>: Unaudited past Julian Day.</div>
        </div>
        """

        browser.setHtml(guide_html)
        lay.addWidget(browser)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btn_box.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
            }
        """)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        dlg.exec()

    def _show_about_software_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("About")
        dlg.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)

        browser = QtWidgets.QTextBrowser()
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                padding: 16px;
                color: #1d1d1f;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        about_html = """
        <style>
            h2 { color: #0071e3; font-size: 18px; margin: 0 0 2px 0; font-weight: 700; }
            .version { color: #6e6e73; font-size: 12px; margin-bottom: 12px; font-weight: 500; }
            .section { margin-bottom: 14px; }
            .section-title { font-size: 13px; font-weight: 700; color: #1d1d1f; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
            p { margin: 0 0 8px 0; color: #333336; }
            ul { margin: 4px 0 8px 18px; padding: 0; color: #333336; }
            li { margin-bottom: 4px; }
            code { background: #f5f5f7; border: 1px solid #d2d2d7; border-radius: 4px; padding: 1px 5px; font-family: monospace; font-size: 11px; }
            hr { border: none; border-top: 1px solid #e5e5ea; margin: 12px 0; }
        </style>

        <h2>CDDM — CDDIS Data Monitor</h2>
        <div class="version">GNSS Telemetry & Archive Audit Utility · Version 2.4 (Build 2026.10)</div>
        <hr>

        <div class="section">
            <div class="section-title">Purpose</div>
            <p>CDDM was developed to provide continuous verification of GNSS observation data ingestion on NASA's Crustal Dynamics Data Information System (CDDIS) archives.</p>
            <p>In geodetic networks and crustal deformation monitoring, research pipelines depend on continuous, uninterrupted observation streams. CDDM continuously monitors remote server directories to immediately detect missing hourly RINEX uploads, latency, or telemetry drops before downstream processing cycles run.</p>
        </div>

        <div class="section">
            <div class="section-title">Architecture & Implementation</div>
            <ul>
                <li><b>Framework:</b> Developed in Python 3 utilizing PySide6 (Qt) for modular event-driven desktop execution.</li>
                <li><b>Network Engine:</b> Session-managed HTTP/HTTPS data retrieval with support for Earthdata authentication, connection pooling, and authenticated institutional proxy servers.</li>
                <li><b>Threading & Concurrency:</b> Decoupled background workers (<code>QThread</code>) prevent user interface latency during live server directory sweeps and multi-day historical audits.</li>
                <li><b>State Management:</b> Thread-safe atomic JSON caching stores verified hourly file arrivals, minimizing redundant server requests and preserving true verification timestamps across sessions.</li>
                <li><b>Alerting:</b> Automated threshold verification with sound frequency alerts generated via standard system audio interfaces.</li>
            </ul>
        </div>

        <div class="section">
            <div class="section-title">Target Data Products</div>
            <p>Tracks standard International GNSS Service (IGS) RINEX 2 and RINEX 3 observation formats archived at CDDIS:</p>
            <ul>
                <li>Hourly GNSS Observation files (<code>/archive/gnss/data/hourly/</code>)</li>
                <li>Daily 30-second Observation archives (<code>/archive/gnss/data/daily/</code>)</li>
                <li>High-rate and broadcast ephemeris streams</li>
            </ul>
        </div>
        """

        browser.setHtml(about_html)
        lay.addWidget(browser)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btn_box.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
            }
        """)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        dlg.exec()

    def _show_author_info_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Author")
        dlg.resize(420, 240)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(12)

        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                padding: 16px;
                color: #1d1d1f;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        author_html = """
        <style>
            h2 { color: #0071e3; font-size: 18px; margin: 0 0 8px 0; font-weight: 700; }
            p { margin: 6px 0; color: #1d1d1f; }
            b { color: #1d1d1f; }
            a { color: #0071e3; text-decoration: none; }
            a:hover { text-decoration: underline; }
            hr { border: none; border-top: 1px solid #e5e5ea; margin: 12px 0; }
        </style>

        <h2>Author Information</h2>
        <hr>
        <p><b>Software:</b> CDDM (CDDIS Data Monitor)</p>
        <p><b>Developer:</b> Muhammed Shahid</p>
        <p><b>Contact:</b> <a href="mailto:mail.shahidvh@gmail.com">mail.shahidvh@gmail.com</a></p>
        """

        browser.setHtml(author_html)
        lay.addWidget(browser)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btn_box.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f7;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e5e5ea;
            }
        """)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        dlg.exec()

    def _show_license_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("License & Terms")
        dlg.resize(540, 380)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(18, 18, 18, 18)

        browser = QtWidgets.QTextBrowser()
        browser.setStyleSheet("""
            background-color: #ffffff;
            border: 1px solid #d2d2d7;
            border-radius: 8px;
            padding: 12px;
            font-family: monospace;
            font-size: 11px;
            color: #1d1d1f;
        """)
        browser.setPlainText("""MIT License

Copyright (c) 2026 Muhammed Shahid

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Data Attribution Notice:
GNSS data tracked through this monitor is provided by the Crustal Dynamics Data
Information System (CDDIS), NASA Goddard Space Flight Center, and the
International GNSS Service (IGS).
""")
        lay.addWidget(browser)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)
        dlg.exec()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        # Menu Bar Construction
        menubar = self.menuBar()
        custom_act = menubar.addAction("Custom Scan")
        custom_act.triggered.connect(self.open_custom_scan_dialog)
        help_menu = menubar.addMenu("&Help && Info")

        # Right-aligned live CDDIS connection status pill in top header bar
        self.conn_pill_btn = QtWidgets.QPushButton("● Checking CDDIS…")
        self.conn_pill_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.conn_pill_btn.setToolTip("Click to open Connection Troubleshooter & Session Repair")
        self.conn_pill_btn.clicked.connect(self.open_connection_troubleshooter)
        menubar.setCornerWidget(self.conn_pill_btn, QtCore.Qt.TopRightCorner)
        self._set_conn_pill_state("connected", "CDDIS Connected")

        # Background heartbeat every 10 minutes + initial check
        self._conn_heartbeat_timer = QtCore.QTimer(self)
        self._conn_heartbeat_timer.setInterval(10 * 60 * 1000)
        self._conn_heartbeat_timer.timeout.connect(self._probe_header_connection)
        self._conn_heartbeat_timer.start()
        QtCore.QTimer.singleShot(1200, self._probe_header_connection)

        act_how_to = help_menu.addAction("📖 How to Use / Guide")
        act_how_to.triggered.connect(self._show_how_to_use_dialog)

        act_about = help_menu.addAction("ℹ️ About CDDM")
        act_about.triggered.connect(self._show_software_dialog if hasattr(self, "_show_software_dialog") else self._show_about_software_dialog)

        act_author = help_menu.addAction("👤 Author Information")
        act_author.triggered.connect(self._show_author_info_dialog)

        help_menu.addSeparator()

        act_license = help_menu.addAction("📄 License & Terms")
        act_license.triggered.connect(self._show_license_dialog)
        root_v = QtWidgets.QVBoxLayout(central)
        top_split = QtWidgets.QHBoxLayout()

        panel = QtWidgets.QGroupBox("Monitor Configuration")
        panel.setFixedWidth(390)
        form = QtWidgets.QFormLayout(panel)

        self.type_box = QtWidgets.QComboBox()
        self.type_box.addItems(DATA_TYPES.keys())
        self.type_box.currentIndexChanged.connect(self._on_type_changed)

        st_container = QtWidgets.QVBoxLayout()
        st_container.setSpacing(0)

        stn_h_layout = QtWidgets.QHBoxLayout()
        stn_h_layout.setContentsMargins(0, 0, 0, 0)
        stn_h_layout.setSpacing(6)

        self.stations = QtWidgets.QLineEdit()
        self.stations.setFixedHeight(28)
        self.stations.setPlaceholderText("Enter 4-char IDs (e.g. IISC, IITK)")
        self.stations.setText(self.settings.value("stations", "IISC"))
        self._last_synced_stns = self.stations.text().strip()

        self.btn_sync_stns = QtWidgets.QPushButton("⟳ Sync")
        self.btn_sync_stns.setToolTip("Sync and audit newly added stations immediately")
        self.btn_sync_stns.setFixedWidth(64)
        self.btn_sync_stns.setFixedHeight(28)
        self.btn_sync_stns.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.btn_sync_stns.setStyleSheet("""
            QPushButton {
                background-color: #0071e3;
                color: #ffffff;
                font-size: 11px;
                font-weight: 700;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #0077ed;
            }
        """)
        self.btn_sync_stns.hide()
        self.btn_sync_stns.clicked.connect(self._hot_sync_stations)

        stn_h_layout.addWidget(self.stations, 1)
        stn_h_layout.addWidget(self.btn_sync_stns)

        def _on_txt_change(text):
            if text.strip() != getattr(self, "_last_synced_stns", "") and text.strip():
                self.btn_sync_stns.show()
            else:
                self.btn_sync_stns.hide()

        self.stations.textEdited.connect(_on_txt_change)
        st_container.addLayout(stn_h_layout)

        self.mode_live = QtWidgets.QRadioButton("Live Today (00..Now UTC)")
        self.mode_doy = QtWidgets.QRadioButton("Specific Julian Day")
        self.mode_live.setChecked(True)
        mode_box = QtWidgets.QHBoxLayout()
        mode_box.addWidget(self.mode_live)
        mode_box.addWidget(self.mode_doy)
        self.mode_live.toggled.connect(self._on_mode_changed)
        self.mode_doy.toggled.connect(self._on_mode_changed)

        now_utc = dt.datetime.now(dt.timezone.utc)
        current_doy = now_utc.timetuple().tm_yday

        self.year_spin = QtWidgets.QSpinBox()
        self.year_spin.setRange(1980, 2100)
        self.year_spin.setValue(now_utc.year)
        self.year_spin.valueChanged.connect(self._on_year_doy_changed)

        self.doy_spin = QtWidgets.QSpinBox()
        self.doy_spin.setRange(1, 366)
        self.doy_spin.setValue(current_doy)
        self.doy_spin.setToolTip("Julian Day of Year (1 to 366)")
        self.doy_spin.valueChanged.connect(self._on_year_doy_changed)

        self.today_btn = QtWidgets.QPushButton("Today")
        self.today_btn.setFixedWidth(65)
        self.today_btn.setToolTip("Set Year and Julian Day to today UTC")
        self.today_btn.clicked.connect(self._set_today_doy)

        doy_row = QtWidgets.QHBoxLayout()
        doy_row.addWidget(self.doy_spin, 1)
        doy_row.addWidget(self.today_btn)

        self.doy_preview_lbl = QtWidgets.QLabel("")
        self.doy_preview_lbl.setStyleSheet("color: #1565c0; font-weight: bold; font-size: 12px;")

        self.track_start_doy = QtWidgets.QSpinBox()
        self.track_start_doy.setRange(1, 366)
        self.track_start_doy.setValue(max(1, current_doy - 5))
        self.track_start_doy.setPrefix("DOY ")
        self.track_start_doy.setToolTip(
            "Start Julian Day for bottom DOY history boxes and automatic weekend catch-up audits"
        )
        self.track_start_doy.valueChanged.connect(self._on_track_start_changed)

        self.recheck_past_btn = QtWidgets.QPushButton("Audit Unchecked")
        self.recheck_past_btn.setToolTip("Scan any grey (unchecked) past DOYs in the background now")
        self.recheck_past_btn.clicked.connect(self._start_background_doy_catchup)

        track_box = QtWidgets.QHBoxLayout()
        track_box.addWidget(self.track_start_doy, 1)
        track_box.addWidget(self.recheck_past_btn)

        self.interval = QtWidgets.QSpinBox()
        self.interval.setRange(1, 1440)
        self.interval.setValue(15)
        self.interval.setSuffix(" min")
        self.interval.valueChanged.connect(self._save_settings)

        self.grace = QtWidgets.QSpinBox()
        self.grace.setRange(0, 1440)
        self.grace.setValue(30)
        self.grace.setSuffix(" min")
        self.grace.valueChanged.connect(self._save_settings)

        self.sound_chk = QtWidgets.QCheckBox("Play sound alert on missing")
        self.sound_chk.setChecked(True)
        self.sound_chk.toggled.connect(self._save_settings)

        self.report_path = QtWidgets.QLineEdit()
        self.report_path.setText(os.path.expanduser("~/Downloads/CDDM"))
        self.report_path.textChanged.connect(self._on_report_path_changed)
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_report_dir)
        path_box = QtWidgets.QHBoxLayout()
        path_box.addWidget(self.report_path, 1)
        path_box.addWidget(browse_btn)

        form.addRow("Data type", self.type_box)
        form.addRow("Stations", st_container)
        form.addRow("Scan Mode", mode_box)
        form.addRow("Year (UTC)", self.year_spin)
        form.addRow("Julian Day (DOY)", doy_row)
        form.addRow("Calendar Date", self.doy_preview_lbl)
        form.addRow("Audit History From", track_box)
        form.addRow("Live Interval", self.interval)
        form.addRow("Grace Period", self.grace)
        form.addRow("Audio Alert", self.sound_chk)
        form.addRow("Station Folders Root", path_box)

        self.start_stop_btn = QtWidgets.QPushButton("▶ Start Monitoring")
        self.start_stop_btn.setFixedHeight(34)
        self.start_stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
        """)
        self.start_stop_btn.clicked.connect(self.toggle_monitoring)

        self.check_btn = QtWidgets.QPushButton("Scan Julian Day Now (Single Run)")
        self.check_btn.clicked.connect(self.run_check)

        self.export_btn = QtWidgets.QPushButton("Export Results to CSV")
        self.export_btn.clicked.connect(self.export_csv)

        self.clear_btn = QtWidgets.QPushButton("Clear Alerts")
        self.clear_btn.clicked.connect(self._handle_clear_alerts)

        self.about_btn = QtWidgets.QPushButton("Help & Documentation")
        self.about_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.about_btn.setStyleSheet("""
            QPushButton {
                background-color: #f8f9fa;
                color: #343a40;
                font-weight: 500;
                font-size: 11px;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 6px 14px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #e9ecef;
                color: #212529;
                border-color: #adb5bd;
            }
            QPushButton:pressed {
                background-color: #dee2e6;
            }
            QPushButton::menu-indicator {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                right: 10px;
                width: 10px;
            }
        """)
        about_menu = QtWidgets.QMenu(self)
        about_menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                color: #212529;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 4px;
                font-size: 11px;
            }
            QMenu::item {
                padding: 6px 18px 6px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #0071e3;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #e9ecef;
                margin: 4px 6px;
            }
        """)

        doc_act = about_menu.addAction("Documentation & User Guide")
        doc_act.triggered.connect(self._show_how_to_use_dialog)

        about_act = about_menu.addAction("About CDDM")
        about_act.triggered.connect(getattr(self, "_show_software_dialog", getattr(self, "_show_about_software_dialog", getattr(self, "show_about_dialog", None))))

        author_act = about_menu.addAction("Author Information")
        author_act.triggered.connect(self._show_author_info_dialog)

        about_menu.addSeparator()

        license_act = about_menu.addAction("License & Terms")
        license_act.triggered.connect(self._show_license_dialog)

        self.about_btn.setMenu(about_menu)
        self.about_btn.clicked.connect(self.show_about_dialog)

        form.addRow(self.start_stop_btn)
# form.addRow(self.check_btn) -- removed duplicate
        form.addRow(self.export_btn)
        form.addRow(self.clear_btn)
        form.addRow(self.about_btn)

        right = QtWidgets.QVBoxLayout()

        status_banner = QtWidgets.QFrame()
        status_banner.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        status_layout = QtWidgets.QHBoxLayout(status_banner)
        status_layout.setContentsMargins(10, 6, 10, 6)

        self.indicator_light = QtWidgets.QLabel(self)
        self.indicator_light.setFixedSize(16, 16)
        self.indicator_label = QtWidgets.QLabel("IDLE")
        self.indicator_label.setStyleSheet("color: #495057; font-weight: bold; font-size: 13px; border: none;")

        # scan_progress moved below heartbeat

        self.summary = QtWidgets.QLabel("Initializing…")
        self.summary.setStyleSheet("color: #212529; font-size: 13px; border: none;")

        # Pulse Heartbeat Bar
        self.heartbeat_box = QtWidgets.QWidget(self)
        hb_layout = QtWidgets.QHBoxLayout(self.heartbeat_box)
        hb_layout.setContentsMargins(6, 2, 8, 2)
        hb_layout.setSpacing(6)
        self.heartbeat_box.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.heartbeat_box.setStyleSheet("""
            background-color: #f2f5f9;
            border: 1px solid #d0d7de;
            border-radius: 14px;
        """)

        self.hb_led = QtWidgets.QLabel("●")
        self.hb_led.setStyleSheet("color: #0071e3; font-size: 10px; border: none; background: transparent; padding-left: 2px;")
        
        self.hb_label = QtWidgets.QLabel("Live  |  Updated: Initializing...")
        self.hb_label.setStyleSheet("color: #1d1d1f; font-size: 11px; font-weight: 500; border: none; background: transparent; padding-right: 4px;")

        hb_layout.addWidget(self.hb_led)
        hb_layout.addWidget(self.hb_label)
        hb_layout.addStretch()

        # Top-right Timezone Dropdown Selector
        tz_lbl = QtWidgets.QLabel("Timezone:")
        tz_lbl.setStyleSheet("font-weight: 600; color: #495057; font-size: 11px; border: none; background: transparent;")
        hb_layout.addWidget(tz_lbl)

        self.tz_combo = QtWidgets.QComboBox(self)
        self.tz_combo.setStyleSheet("""
            QComboBox {
                padding: 2px 8px;
                border: 1px solid #ced4da;
                border-radius: 4px;
                background: #ffffff;
                color: #212529;
                font-weight: 500;
                font-size: 11px;
                min-width: 140px;
            }
        """)

        tz_list = [
            "IST (UTC+05:30)",
            "UTC",
            "JST (UTC+09:00)",
            "CET (UTC+01:00)",
            "EST (UTC-05:00)",
            "PST (UTC-08:00)"
        ]
        if hasattr(self, "TIMEZONES") and self.TIMEZONES:
            tz_list = list(self.TIMEZONES.keys())

        self.tz_combo.addItems(tz_list)
        saved_tz = self.settings.value("display_tz", "IST (UTC+05:30)")
        idx = self.tz_combo.findText(saved_tz)
        if idx >= 0:
            self.tz_combo.setCurrentIndex(idx)

        self.tz_combo.currentIndexChanged.connect(self._on_tz_combo_changed)
        hb_layout.addWidget(self.tz_combo)

        # User Profile Pill with Logout Dropdown
        current_user = self.settings.value("username", "Earthdata User")
        self.profile_btn = QtWidgets.QPushButton(f"👤  {current_user}  ▾", self)
        self.profile_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.profile_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #1d1d1f;
                font-weight: 600;
                font-size: 11px;
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 2px 10px;
                margin-left: 6px;
            }
            QPushButton:hover {
                background-color: #f1f3f5;
                border-color: #0d47a1;
            }
            QPushButton::menu-indicator { image: none; }
        """)

        profile_menu = QtWidgets.QMenu(self.profile_btn)
        profile_menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
                color: #212529;
            }
            QMenu::item:selected {
                background-color: #e9ecef;
                color: #c92a2a;
            }
        """)

        user_info_action = QtGui.QAction(f"Signed in as {current_user}", self)
        user_info_action.setEnabled(False)
        profile_menu.addAction(user_info_action)
        profile_menu.addSeparator()

        settings_action = QtGui.QAction("⚙  Settings", self)
        settings_action.triggered.connect(self.open_settings)
        profile_menu.addAction(settings_action)
        profile_menu.addSeparator()

        logout_action = QtGui.QAction("⎋  Logout", self)
        logout_action.triggered.connect(self.handle_logout)
        profile_menu.addAction(logout_action)

        self.profile_btn.setMenu(profile_menu)
        hb_layout.addWidget(self.profile_btn)


        # Hide redundant static indicator widgets
        self.indicator_light.hide()
        self.indicator_label.hide()
        self.summary.hide()

        # Ultra-thin 2px Apple-style progress line (hidden by default when idle)
        self.scan_progress = QtWidgets.QProgressBar(self)
        self.scan_progress.setFixedHeight(6)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setFixedHeight(4)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 2px;
                background-color: #f1f5f9;
                max-height: 4px;
                min-height: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0071e3, stop:0.4 #38bdf8, stop:0.7 #0284c7, stop:1 #0071e3);
                border-radius: 2px;
            }
        """)
        QtCore.QTimer.singleShot(600, self.scan_progress.hide)

        # Dynamic Unified Apple Pill (Left-aligned or expanding)
        self.heartbeat_box.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.heartbeat_box.setStyleSheet("""
            QWidget {
                background-color: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 16px;
                padding: 3px 8px;
            }
        """)
        hb_layout.setContentsMargins(10, 4, 12, 4)
        hb_layout.setSpacing(8)

        self.hb_led.setStyleSheet("color: #0071e3; font-size: 13px; border: none; background: transparent;")
        self.hb_label.setStyleSheet("color: #1d1d1f; font-size: 12px; font-weight: 600; border: none; background: transparent;")

        status_layout.addWidget(self.heartbeat_box)
        status_layout.addStretch(1)

        # Container for the thin line right below the pill
        

        # Heartbeat pulse animation timer
        self._hb_state = True
        self.hb_timer = QtCore.QTimer(self)
        self.hb_timer.setInterval(1000)
        self.hb_timer.timeout.connect(self._pulse_heartbeat)
        self.hb_timer.start()

        self.station_tabs = QtWidgets.QTabWidget()
        
        # Corner Action Button: Top-right refresh button for live monitoring
        self.btn_live_refresh = QtWidgets.QPushButton("🔄 Refresh Live")
        self.btn_live_refresh.setToolTip("Force an immediate live check on CDDIS servers for today's hours (bypassing cache)")
        self.btn_live_refresh.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.btn_live_refresh.setStyleSheet("""
            QPushButton {
                background-color: #f1f3f5;
                color: #1c7ed6;
                font-weight: 600;
                font-size: 11px;
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 4px 10px;
                margin-right: 6px;
                margin-top: 2px;
                margin-bottom: 2px;
            }
            QPushButton:hover {
                background-color: #e7f5ff;
                border-color: #339af0;
                color: #1864ab;
            }
            QPushButton:pressed {
                background-color: #d0ebff;
            }
            QPushButton:disabled {
                color: #adb5bd;
                background-color: #f8f9fa;
                border-color: #e9ecef;
            }
        """)
        self.btn_live_refresh.clicked.connect(self.refresh_live_now)
        self.station_tabs.setCornerWidget(self.btn_live_refresh, QtCore.Qt.TopRightCorner)

        self.station_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                background: #ffffff;
            }
            QTabBar::tab {
                background: #e5e5ea;
                color: #1d1d1f;
                padding: 7px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 12px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0071e3;
                font-weight: 700;
                border-bottom: 2px solid #0071e3;
            }
        """)

        self.table = self._make_table()
        self.station_tables = {}
        self.station_tabs.addTab(self.table, "All Stations")

        right.addWidget(status_banner)

        # Scanning indicator line aligned beneath heartbeat pill
        # scan_progress already initialized above
        self.scan_progress.setFixedHeight(5)
        self.scan_progress.setMinimum(0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 2px;
                background-color: #e5e7eb;
                max-height: 5px;
                min-height: 5px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2563eb, stop:0.5 #06b6d4, stop:1 #3b82f6);
                border-radius: 2px;
            }
        """)
        self.scan_progress.hide()
        right.addWidget(self.scan_progress)
        self.calendar_strip = QtWidgets.QLabel("")
        self.calendar_strip.setStyleSheet("color: #6e6e73; font-size: 11px; font-weight: 500; padding: 0px 2px; margin: 0px;")
        right.addWidget(self.calendar_strip)
        right.addWidget(self.station_tabs, 1)

        # Properly assemble layout hierarchy into top_split and root_v
        top_split.addWidget(panel)
        top_split.addLayout(right, 1)
        root_v.addLayout(top_split, 1)

        doy_strip_group = QtWidgets.QGroupBox("Archive History")
        doy_strip_v = QtWidgets.QVBoxLayout(doy_strip_group)
        doy_strip_v.setContentsMargins(6, 4, 6, 4)
        doy_strip_v.setSpacing(4)

        # Compact utility bar with minimal action triggers
        doy_tools_row = QtWidgets.QHBoxLayout()
        doy_tools_row.setContentsMargins(0, 0, 0, 2)
        doy_tools_row.setSpacing(6)

        legend_lbl = QtWidgets.QLabel("<span style=\x27color:#2e7d32;\x27>●</span> Complete  <span style=\x27color:#c62828;\x27>●</span> Incomplete  <span style=\x27color:#757575;\x27>●</span> Unchecked")
        legend_lbl.setStyleSheet("color: #718096; font-size: 11px; font-weight: 500;")
        doy_tools_row.addWidget(legend_lbl)
        doy_tools_row.addStretch()

        self.btn_gap_analyzer = QtWidgets.QPushButton("📊 Gap Audit")
        self.btn_gap_analyzer.setFixedHeight(26)
        self.btn_gap_analyzer.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_gap_analyzer.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #0f172a;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 12px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
            }
            QPushButton:hover {
                background-color: #e2e8f0;
                border-color: #94a3b8;
                color: #0284c7;
            }
            QPushButton:pressed {
                background-color: #cbd5e1;
            }
        """)
        self.btn_gap_analyzer.clicked.connect(self.open_gap_analyzer)
        doy_tools_row.addWidget(self.btn_gap_analyzer)

        self.btn_recheck_missing = QtWidgets.QPushButton("↻ Recheck Gaps")
        self.btn_recheck_missing.setFixedHeight(26)
        self.btn_recheck_missing.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_recheck_missing.setStyleSheet("""
            QPushButton {
                background-color: #fef2f2;
                color: #b91c1c;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 12px;
                border-radius: 4px;
                border: 1px solid #fecaca;
            }
            QPushButton:hover {
                background-color: #fee2e2;
                border-color: #f87171;
                color: #991b1b;
            }
            QPushButton:pressed {
                background-color: #fca5a5;
            }
        """)
        self.btn_recheck_missing.clicked.connect(self.recheck_incomplete_doys)
        doy_tools_row.addWidget(self.btn_recheck_missing)

        doy_strip_v.addLayout(doy_tools_row)

        self.doy_scroll = QtWidgets.QScrollArea()
        self.doy_scroll.setWidgetResizable(True)
        self.doy_scroll.setFixedHeight(62)
        self.doy_scroll.setStyleSheet("""
            QScrollArea {
                background-color: #f8f9fa;
                border: 1px solid #ced4da;
                border-radius: 4px;
            }
        """)

        self.doy_strip_container = QtWidgets.QWidget()
        self.doy_strip_container.setStyleSheet("background-color: #f8f9fa;")
        self.doy_strip_layout = QtWidgets.QHBoxLayout(self.doy_strip_container)
        self.doy_strip_layout.setContentsMargins(6, 4, 6, 4)
        self.doy_strip_layout.setSpacing(6)
        self.doy_strip_layout.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        self.doy_scroll.setWidget(self.doy_strip_container)
        doy_strip_v.addWidget(self.doy_scroll)
        root_v.addWidget(doy_strip_group)

        self.log_group = bottom_box = QtWidgets.QGroupBox("Activity Work Log (UTC)")
        bottom_box = self.log_group
        bottom_layout = QtWidgets.QVBoxLayout(bottom_box)
        bottom_layout.setContentsMargins(6, 6, 6, 6)

        self.logbox = QtWidgets.QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(3000)
        self.logbox.setStyleSheet("""
            QPlainTextEdit {
                background-color: #ffffff;
                color: #212529;
                font-family: monospace;
                font-size: 12px;
                border: 1px solid #ced4da;
                border-radius: 4px;
            }
        """)
        bottom_layout.addWidget(self.logbox)
        root_v.addWidget(bottom_box, 1)

        self.statusBar().showMessage("Ready.")

    def _quick_add_station(self):
        new_raw = self.quick_station_input.text().strip().upper()
        if not new_raw:
            return
        tokens = [t.strip() for t in re.split(r"[,\s;]+", new_raw) if len(t.strip()) in (4, 9)]
        if not tokens:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Station ID",
                "Station ID must be 4 characters (e.g. HYDE, IISC) or 9 characters (e.g. HYDE00IND)."
            )
            return

        current_list = self.parse_stations()
        added = []
        for t in tokens:
            if t not in current_list:
                current_list.append(t)
                added.append(t)

        if added:
            self.stations.setText(", ".join(current_list))
            self.log(f"Added station(s) to monitor list: {', '.join(added)}")
            self.refresh_doy_strip()
            if self.is_monitoring:
                self.run_check(trigger_catchup_on_finish=True)
        self.quick_station_input.clear()

    def _on_stations_changed(self):
        self._save_settings()
        self.refresh_doy_strip()

    def _style_doy_badge(self, btn, state, tooltip=""):
        try:
            palette = {
                "ok": ("#2e7d32", "#ffffff", "✔"),
                "missing": ("#c62828", "#ffffff", "✖"),
                "scanning": ("#0288d1", "#ffffff", "↻"),
                "unchecked": ("#adb5bd", "#212529", "○"),
            }
            bg, fg, sym = palette.get(state, ("#adb5bd", "#212529", "○"))
            doy = btn.property("doy")
            year = btn.property("year")
            cal_short = btn.property("cal_short")
            btn.setText(f"{sym} DOY {doy:03d}\n{cal_short}")
            if tooltip:
                btn.setToolTip(
                    f"Year {year} DOY {doy:03d} ({cal_short})\n"
                    f"Per-Station Status:\n{tooltip}\n\n"
                    f"Click to view station reports or re-audit."
                )
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    color: {fg};
                    font-weight: bold;
                    font-size: 11px;
                    border-radius: 5px;
                    padding: 4px 8px;
                    min-width: 78px;
                    max-height: 44px;
                    border: 1px solid rgba(0,0,0,0.15);
                }}
                QPushButton:hover {{
                    border: 2px solid #212529;
                }}
            """)

        except RuntimeError:
            pass
    def refresh_doy_strip(self):
        while self.doy_strip_layout.count():
            item = self.doy_strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.doy_badges.clear()

        now_utc = dt.datetime.now(dt.timezone.utc)
        curr_year = now_utc.year
        today_doy = now_utc.timetuple().tm_yday
        yesterday_doy = today_doy - 1

        start_doy = self.track_start_doy.value()
        if start_doy > yesterday_doy:
            start_doy = max(1, yesterday_doy)

        target_dir = self.report_path.text().strip()
        stations = self.parse_stations()

        for d in range(start_doy, yesterday_doy + 1):
            cal_date = dt.date(curr_year, 1, 1) + dt.timedelta(days=d - 1)
            cal_short = cal_date.strftime("%d %b")

            state, details = check_all_stations_doy_status(target_dir, stations, curr_year, d)

            btn = QtWidgets.QPushButton()
            btn.setProperty("year", curr_year)
            btn.setProperty("doy", d)
            btn.setProperty("cal_short", cal_short)
            btn.setProperty("state", state)
            self._style_doy_badge(btn, state, details)
            btn.clicked.connect(lambda checked=False, y=curr_year, doy=d: self._on_doy_badge_clicked(y, doy))

            self.doy_strip_layout.addWidget(btn)
            self.doy_badges[(curr_year, d)] = btn

        QtCore.QTimer.singleShot(
            100,
            lambda: self.doy_scroll.horizontalScrollBar().setValue(
                self.doy_scroll.horizontalScrollBar().maximum()
            )
        )

    def _on_doy_badge_clicked(self, year, doy):
        target_dir = self.report_path.text().strip()
        stations = self.parse_stations()
        if not stations:
            return

        existing_files = [
            (st, get_station_doy_filepath(target_dir, st, year, doy))
            for st in stations
            if os.path.exists(get_station_doy_filepath(target_dir, st, year, doy))
        ]

        if existing_files:
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle(f"Station DOY Summary Reports – Year {year} DOY {doy:03d}")
            dlg.resize(860, 580)
            lay = QtWidgets.QVBoxLayout(dlg)

            tabs = QtWidgets.QTabWidget()
            for st, fp in existing_files:
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    content = f"Error reading {fp}: {e}"
                viewer = QtWidgets.QPlainTextEdit()
                viewer.setReadOnly(True)
                viewer.setPlainText(content)
                viewer.setStyleSheet("font-family: monospace; font-size: 12px; background: #ffffff; color: #212529;")
                tabs.addTab(viewer, f"Station: {st}")

            lay.addWidget(tabs)

            btn_row = QtWidgets.QHBoxLayout()
            reaudit_btn = QtWidgets.QPushButton(f"↻ Re-Audit DOY {doy:03d} for All Stations")
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.clicked.connect(dlg.accept)

            def trigger_reaudit():
                dlg.accept()
                for st in stations:
                    fp = get_station_doy_filepath(target_dir, st, year, doy)
                    if os.path.exists(fp):
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
                self.refresh_doy_strip()
                self._start_background_doy_catchup()

            reaudit_btn.clicked.connect(trigger_reaudit)
            btn_row.addWidget(reaudit_btn)
            btn_row.addStretch()
            btn_row.addWidget(close_btn)
            lay.addLayout(btn_row)
            dlg.exec()
        else:
            self.log(f"DOY {doy:03d} clicked (Unchecked). Queuing background 24-hour audit...")
            self._start_background_doy_catchup()

    
    def _set_conn_pill_state(self, state, label_text):
        if not hasattr(self, "conn_pill_btn"):
            return
        if state == "connected":
            self.conn_pill_btn.setText(f"● {label_text}")
            self.conn_pill_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f0fdf4; color: #15803d;
                    border: 1px solid #bbf7d0; border-radius: 6px;
                    padding: 4px 12px; margin: 3px 6px;
                    font-size: 11px; font-weight: 600;
                }
                QPushButton:hover { background-color: #dcfce7; }
            """)
        elif state == "expired":
            self.conn_pill_btn.setText(f"▲ {label_text}")
            self.conn_pill_btn.setStyleSheet("""
                QPushButton {
                    background-color: #fffbeb; color: #b45309;
                    border: 1px solid #fde68a; border-radius: 6px;
                    padding: 4px 12px; margin: 3px 6px;
                    font-size: 11px; font-weight: 600;
                }
                QPushButton:hover { background-color: #fef3c7; }
            """)
        elif state == "offline":
            self.conn_pill_btn.setText(f"✕ {label_text}")
            self.conn_pill_btn.setStyleSheet("""
                QPushButton {
                    background-color: #fff1f0; color: #d92d20;
                    border: 1px solid #fecdca; border-radius: 6px;
                    padding: 4px 12px; margin: 3px 6px;
                    font-size: 11px; font-weight: 600;
                }
                QPushButton:hover { background-color: #fee4e2; }
            """)
        else:
            self.conn_pill_btn.setText(f"● {label_text}")
            self.conn_pill_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff; color: #6e6e73;
                    border: 1px solid #d2d2d7; border-radius: 6px;
                    padding: 4px 12px; margin: 3px 6px;
                    font-size: 11px; font-weight: 600;
                }
                QPushButton:hover { background-color: #f5f5f7; }
            """)

    def _probe_header_connection(self):
        if getattr(self, "_hdr_conn_worker", None) and self._hdr_conn_worker.isRunning():
            return
        self._hdr_conn_worker = ConnectionCheckWorker(self.session, repair=False, parent=self)
        self._hdr_conn_worker.finished_check.connect(lambda st, txt, _rep: self._set_conn_pill_state(st, txt))
        self._hdr_conn_worker.start()

    def open_connection_troubleshooter(self):
        dlg = ConnectionTroubleshooterDialog(self, self.session)
        dlg.status_updated.connect(self._set_conn_pill_state)
        dlg.exec()

    def open_custom_scan_dialog(self):
        existing = getattr(self, "_custom_scan_dlg", None)
        if existing is not None and existing.isVisible():
            existing.showNormal()
            existing.raise_()
            existing.activateWindow()
            return
        self._custom_scan_dlg = CustomScanDialog(
            None,
            self.session,
            default_stations=self.stations.text().strip(),
            default_dtype=self.type_box.currentText(),
            default_out_dir=self.report_path.text().strip()
        )
        self._custom_scan_dlg.setWindowIcon(self.windowIcon())
        self._custom_scan_dlg.show()
        self._custom_scan_dlg.raise_()
        self._custom_scan_dlg.activateWindow()

    def open_gap_analyzer(self):
        target_dir = self.report_path.text().strip() or os.path.expanduser("~/cddis_reports")
        stations = self.parse_stations()
        curr_year = dt.datetime.now(dt.timezone.utc).year
        dlg = ArchiveGapAnalyzerDialog(self, target_dir, stations, curr_year)
        dlg.exec()

    def recheck_incomplete_doys(self):
        """Scans tracked badges and re-queues only missing/partial/unscanned DOYs, skipping complete OK days."""
        target_dir = self.report_path.text().strip() or os.path.expanduser("~/Downloads/CDDM")
        stations = self.parse_stations()
        if not stations:
            self.log("Cannot re-check: No stations configured.")
            return

        requeued = 0
        now_utc = dt.datetime.now(dt.timezone.utc)
        curr_year = now_utc.year
        start_doy = self.track_start_doy.value()
        yesterday_doy = now_utc.timetuple().tm_yday - 1

        for d in range(start_doy, yesterday_doy + 1):
            day_needs_rescan = False
            for st in stations:
                fp = get_station_doy_filepath(target_dir, st, curr_year, d)
                st_state, _ = parse_single_summary_file(fp)
                if st_state in ("missing", "partial", "unchecked"):
                    if os.path.exists(fp):
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
                    day_needs_rescan = True
            if day_needs_rescan:
                requeued += 1

        if requeued > 0:
            self.refresh_doy_strip()
            self.log(f"Queuing {requeued} incomplete/missing DOY(s) for CDDIS re-audit...")
            self._start_background_doy_catchup()
        else:
            self.log("All tracked DOYs are already fully verified (OK). Nothing to re-check.")

    def _startup_live_then_catchup(self):
        stations = self.parse_stations()
        if not stations:
            self.log("Startup paused: Add station IDs in the configuration panel.")
            self.set_indicator("idle", "IDLE (Add Stations)")
            return

        self._activate_live_monitoring(schedule_catchup_after=True)

    def _start_background_doy_catchup(self):
        if self.bg_doy_worker and self.bg_doy_worker.isRunning():
            return

        stations = self.parse_stations()
        if not stations:
            return

        now_utc = dt.datetime.now(dt.timezone.utc)
        curr_year = now_utc.year
        yesterday_doy = now_utc.timetuple().tm_yday - 1
        start_doy = self.track_start_doy.value()
        target_dir = self.report_path.text().strip()

        self.pending_doy_queue = []
        for d in range(start_doy, yesterday_doy + 1):
            missing_stations = [
                st for st in stations
                if not os.path.exists(get_station_doy_filepath(target_dir, st, curr_year, d))
            ]
            if missing_stations:
                self.pending_doy_queue.append((curr_year, d, missing_stations))

        if not self.pending_doy_queue:
            self.log("DOY History Audit: All station folders and past DOY summaries are complete.")
            return

        doy_list_str = ", ".join(f"DOY {d:03d}" for _, d, _ in self.pending_doy_queue)
        self.log(f"Background Catch-Up Queue: Auditing {len(self.pending_doy_queue)} past day(s): {doy_list_str}")
        self._process_next_queued_doy()

    def _process_next_queued_doy(self):
        if not self.pending_doy_queue:
            self.log("Background Catch-Up Complete: All station DOY folders and summaries are up to date!")
            self.statusBar().showMessage("All station DOY summaries verified and saved.", 6000)
            return

        year, doy, missing_stations = self.pending_doy_queue.pop(0)
        btn = self.doy_badges.get((year, doy))
        if btn:
            self._style_doy_badge(btn, "scanning", f"Auditing {', '.join(missing_stations)}...")

        self.log(
            f"Background DOY Audit: Scanning 24h (00..23 UTC) for Year {year} DOY {doy:03d} "
            f"[Stations: {', '.join(missing_stations)}]..."
        )
        base_day = dt.datetime(year, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(days=doy - 1)
        hourly = self._hourly()
        slots = [base_day + dt.timedelta(hours=h) for h in range(24)] if hourly else [base_day]

        if hasattr(self, "bg_doy_worker") and self.bg_doy_worker is not None:
            if self.bg_doy_worker.isRunning():
                self.bg_doy_worker.wait(1500)
        self.bg_doy_worker = CheckWorker(
            self.session, self.type_box.currentText(),
            slots, missing_stations, self.grace.value(), cached_records=None, parent=self
        )

        def on_bg_doy_done(rows):
            target_dir = self.report_path.text().strip()
            self._save_per_station_doy_files(target_dir, year, doy, rows)
            all_stations = self.parse_stations()
            state, details = check_all_stations_doy_status(target_dir, all_stations, year, doy)
            if btn:
                self._style_doy_badge(btn, state, details)
            self.log(
                f"Saved station summary file(s) for DOY {doy:03d} in "
                f"{target_dir}/<STATION>/{year}_DOY{doy:03d}.txt -> Status: {state.upper()}"
            )
            QtCore.QTimer.singleShot(300, self._process_next_queued_doy)

        def on_bg_doy_failed(err_msg):
            self.log(f"Background DOY {doy:03d} audit failed: {err_msg}")
            if btn:
                self._style_doy_badge(btn, "unchecked", f"Audit failed: {err_msg}")
            QtCore.QTimer.singleShot(500, self._process_next_queued_doy)

        self.bg_doy_worker.results.connect(on_bg_doy_done)
        self.bg_doy_worker.failed.connect(on_bg_doy_failed)
        self.bg_doy_worker.start()

    def _save_per_station_doy_files(self, base_dir, year, doy, rows):
        by_station = {}
        for r in rows:
            by_station.setdefault(r["station"], []).append(r)

        for st, st_rows in by_station.items():
            filepath = get_station_doy_filepath(base_dir, st, year, doy)
            try:
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                total = len(st_rows)
                counts = {}
                for r in st_rows:
                    counts[r["status"]] = counts.get(r["status"], 0) + 1

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write("=" * 110 + "\n")
                    f.write(f"NASA CDDIS GNSS ARCHIVE - STATION 24-HOUR DOY SUMMARY REPORT\n")
                    f.write(f"Station       : {st}\n")
                    f.write(f"Year / DOY    : {year} / DOY {doy:03d} (00:00 to 23:59 UTC)\n")
                    f.write(f"Data Type     : {self.type_box.currentText()}\n")
                    f.write(f"File Location : {filepath}\n")
                    f.write(f"Generated on  : {dt.datetime.now():%Y-%m-%d %H:%M:%S} Local Time\n")
                    f.write("=" * 96 + "\n\n")

                    f.write("STATUS SUMMARY:\n")
                    for st_type in ("OK", "PARTIAL", "MISSING", "PENDING"):
                        c = counts.get(st_type, 0)
                        pct = (c / total * 100) if total else 0
                        f.write(f"  - {st_type:<10}: {c:>3} ({pct:.1f}%)\n")
                    f.write("\n" + "-" * 96 + "\n")
                    f.write(
                        f"{'Station':<8} {'Hour (UTC)':<11} {'Status':<9} {'Files':<6} "
                        f"{'Size (KB)':<11} {'Last Modified (UTC)':<20} {'Uploaded File(s)'}\n"
                    )
                    f.write("-" * 110 + "\n")
                    for r in sorted(st_rows, key=lambda x: x["slot"]):
                        mod_time = str(r.get('mod', '-'))
                        if not mod_time or mod_time.strip() == "":
                            mod_time = "-"
                        f.write(
                            f"{r['station']:<8} "
                            f"Hr {r['hour_str']:<7} "
                            f"{r['status']:<9} "
                            f"{r['files']}/{r['expected']:<4} "
                            f"{r.get('size', 0)/1024:<11.1f} "
                            f"{mod_time:<20} "
                            f"{r.get('filenames', '')}\n"
                        )
                    f.write("=" * 110 + "\n")
            except Exception as e:
                self.log(f"Failed writing summary file for {st} ({filepath}): {e}")

    def _on_track_start_changed(self):
        self._save_settings()
        self.refresh_doy_strip()

    def _on_report_path_changed(self):
        self._save_settings()
        self.refresh_doy_strip()

    def set_indicator(self, state, msg=""):
        colors = {
            "idle": ("#6c757d", "STOPPED"),
            "scanning": ("#0288d1", "SCANNING"),
            "ok": ("#2e7d32", "ALL OK"),
            "missing": ("#c62828", "MISSING DATA"),
            "partial": ("#ef6c00", "PARTIAL DATA"),
            "error": ("#6a1b9a", "ERROR"),
        }
        color, default_text = colors.get(state, ("#6c757d", "UNKNOWN"))
        text = msg if msg else default_text

        self.indicator_light.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 8px;
                border: 1px solid #adb5bd;
            }}
        """)
        self.indicator_label.setText(text)
        self.indicator_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 12px; border: none;")
        self.update_satellite_logo(color)

    def show_about_dialog(self):
        QtWidgets.QMessageBox.information(
            self,
            "About CDDM",
            "<b>CDDM Live v1.1</b><br>"
            "Version: 3.7.9<br><br>"
            "<b>Author:</b> Muhammed Shahid V.H.<br>"
            "<b>Contact:</b> shahidvh@users.noreply.github.com<br>"
            "<b>License:</b> MIT License<br><br>"
            "Autonomous multi-station tool for GNSS upload verification on NASA CDDIS archives."
        )

    def _set_today_doy(self):
        now_utc = dt.datetime.now(dt.timezone.utc)
        self.year_spin.setValue(now_utc.year)
        self.doy_spin.setValue(now_utc.timetuple().tm_yday)
        self._update_doy_date_preview()
        self._save_settings()

    def _on_year_doy_changed(self):
        self._update_doy_date_preview()
        self._save_settings()
        if not self.mode_live.isChecked():
            self.reset_display_for_current_mode()
    def _update_doy_date_preview(self):
        y = self.year_spin.value()
        doy = self.doy_spin.value()
        try:
            target_date = dt.date(y, 1, 1) + dt.timedelta(days=doy - 1)
            if target_date.year != y:
                self.doy_preview_lbl.setText(f"{y} has 365 days (Invalid DOY 366)")
            else:
                self.doy_preview_lbl.setText(
                    f"{target_date.strftime('%Y-%m-%d (%a, %d %b %Y)')} | DOY {doy:03d}"
                )
        except Exception:
            self.doy_preview_lbl.setText("Invalid Date")

    def _activate_live_monitoring(self, schedule_catchup_after=False):
        self.mode_live.setChecked(True)
        self.is_monitoring = True
        self.start_stop_btn.setText("⏹ Stop Monitoring")
        self.start_stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #c62828;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        self._set_today_doy()
        self.timer.start(self.interval.value() * 60 * 1000)
        self.log(f"Live Today Monitoring active (Interval: {self.interval.value()} min). Scanning today's live slots first...")
        self.run_check(trigger_catchup_on_finish=schedule_catchup_after)

    def toggle_monitoring(self):
        if self.mode_doy.isChecked():
            # In Specific Day mode: perform a single direct scan
            self.run_check()
            return
        if not self.is_monitoring:
            self._activate_live_monitoring(schedule_catchup_after=True)
        else:
            self.is_monitoring = False
            self.timer.stop()
            self.start_stop_btn.setText("▶ Start Monitoring")
            self.start_stop_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2e7d32;
                    color: white;
                    font-weight: bold;
                    padding: 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #388e3c;
                }
            """)
            self.log("Monitoring stopped by user.")
    def _browse_report_dir(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Root Folder for Station Summary Folders", self.report_path.text()
        )
        if directory:
            self.report_path.setText(directory)
            self._save_settings()
            self.refresh_doy_strip()
            self.log(f"Station folders root path set to: {directory}")

    def _load_settings(self):
        s = self.settings
        saved_stations = s.value("stations", "")
        if saved_stations:
            self.stations.setText(saved_stations)

        idx = self.type_box.findText(s.value("dtype", "Hourly observation"))
        if idx >= 0:
            self.type_box.setCurrentIndex(idx)

        now_utc = dt.datetime.now(dt.timezone.utc)
        current_doy = now_utc.timetuple().tm_yday
        self.year_spin.setValue(int(s.value("year", now_utc.year)))
        self.doy_spin.setValue(int(s.value("doy", current_doy)))

        default_track_start = max(1, current_doy - 5)
        self.track_start_doy.setValue(int(s.value("track_start_doy", default_track_start)))

        is_live = str(s.value("mode_live", "true")).lower() == "true"
        self.mode_live.setChecked(is_live)
        self.mode_doy.setChecked(not is_live)

        self.interval.setValue(int(s.value("interval", 15)))
        self.grace.setValue(int(s.value("grace", 30)))
        self.sound_chk.setChecked(str(s.value("sound", "true")).lower() == "true")
        saved_path = s.value("report_path", "")
        if saved_path and os.path.isdir(saved_path):
            self.report_path.setText(saved_path)

    def _save_settings(self):
        s = self.settings
        s.setValue("stations", self.stations.text().strip())
        s.setValue("dtype", self.type_box.currentText())
        s.setValue("year", self.year_spin.value())
        s.setValue("doy", self.doy_spin.value())
        s.setValue("track_start_doy", self.track_start_doy.value())
        s.setValue("mode_live", self.mode_live.isChecked())
        s.setValue("interval", self.interval.value())
        s.setValue("grace", self.grace.value())
        s.setValue("sound", self.sound_chk.isChecked())
        s.setValue("report_path", self.report_path.text().strip())
        s.sync()



    def _get_live_cache_path(self, station, year, doy):
        root = self.report_path.text().strip() if hasattr(self, 'folder_input') else os.getcwd()
        station_dir = os.path.join(root, station.upper())
        os.makedirs(station_dir, exist_ok=True)
        return os.path.join(station_dir, f"{year}_{int(doy):03d}_live_summary.json")

    def _save_station_live_cache(self, station, year, doy):
        try:
            import json
            cache_file = self._get_live_cache_path(station, year, doy)
            data = {}
            if hasattr(self, 'station_data') and station in self.station_data:
                data = self.station_data[station]
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_station_live_cache(self, station, year, doy):
        try:
            import json
            cache_file = self._get_live_cache_path(station, year, doy)
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_slots = json.load(f)
                    if hasattr(self, 'station_data'):
                        if station not in self.station_data:
                            self.station_data[station] = {}
                        for slot, info in cached_slots.items():
                            if info.get("status") == "OK":
                                self.station_data[station][slot] = info
                return True
        except Exception:
            pass
        return False


    def _get_live_summary_file_path(self, station):
        """Returns <Station_Root>/<STATION>/live_summary.txt"""
        root = self.report_path.text().strip() if hasattr(self, 'folder_input') else os.getcwd()
        station_dir = os.path.join(root, station.upper())
        os.makedirs(station_dir, exist_ok=True)
        return os.path.join(station_dir, "live_summary.txt")

    def _sync_live_summary_to_disk(self, station, year, doy):
        """Writes/overwrites today live summary to station path as formatted text."""
        try:
            summary_path = self._get_live_summary_file_path(station)
            station_info = self.station_data.get(station, {})
            
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(f"# CDDM Live v1.1 Daily Scan Summary\n")
                f.write(f"# Station: {station.upper()} | Year: {year} | DOY: {doy:03d}\n")
                f.write(f"# Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# {'Slot':<6} | {'Status':<8} | {'Checked_UTC':<12} | {'Files':<6} | {'Uploaded_File'}\n")
                f.write("# " + "-" * 75 + "\n")

                for hour in range(24):
                    slot_key = f"{hour:02d}"
                    slot_data = station_info.get(slot_key, {})
                    status = slot_data.get("status", "PENDING")
                    checked = slot_data.get("checked_utc", "-")
                    files_cnt = slot_data.get("files_count", "0/1")
                    uploaded = slot_data.get("uploaded_files", "-")
                    if isinstance(uploaded, list):
                        uploaded = ", ".join(uploaded) if uploaded else "-"
                    
                    f.write(f"{slot_key:<8} | {status:<8} | {checked:<12} | {files_cnt:<6} | {uploaded}\n")
        except Exception as e:
            self._log_activity(f"Notice: Could not write live_summary for {station}: {e}")

    def _load_live_summary_from_disk(self, station, current_year, current_doy):
        """Reads live_summary.txt. If from today, loads OK hours into station_data instantly."""
        summary_path = self._get_live_summary_file_path(station)
        if not os.path.exists(summary_path):
            return False

        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Check header line for date rollover
            target_marker = f"Year: {current_year} | DOY: {current_doy:03d}"
            header_found = False
            for line in lines[:5]:
                if target_marker in line:
                    header_found = True
                    break
            
            # If summary is from yesterday or older, do not load old state (roll over for new day)
            if not header_found:
                return False

            if station not in self.station_data:
                self.station_data[station] = {}

            loaded_count = 0
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5:
                    slot_key = parts[0]
                    status = parts[1]
                    checked_utc = parts[2]
                    files_cnt = parts[3]
                    uploaded_files = parts[4]

                    # Restore cached OK records
                    if status == "OK":
                        self.station_data[station][slot_key] = {
                            "status": "OK",
                            "checked_utc": checked_utc,
                            "files_count": files_cnt,
                            "uploaded_files": uploaded_files,
                            "last_modified": "-"
                        }
                        loaded_count += 1

            if loaded_count > 0:
                self._log_activity(f"Loaded {loaded_count} verified hour(s) from {station}/live_summary.txt (Skipping network scans for these).")
                return True
        except Exception as e:
            pass
        return False

    def closeEvent(self, event):
        self.is_monitoring = False
        # Cleanly dismantle the system tray icon so GNOME drops the panel slot
        if hasattr(self, "tray") and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None
        # Terminate any running background workers
        for worker_name in ("worker", "bg_doy_worker", "audit_worker", "_hdr_conn_worker"):
            w = getattr(self, worker_name, None)
            if w is not None and w.isRunning():
                w.quit()
                w.wait(1000)
        self._save_settings()
        self.log("Application closed.")
        super().closeEvent(event)
        QtWidgets.QApplication.exit(0)
    def _hourly(self):
        return DATA_TYPES[self.type_box.currentText()]["hourly"]

    def _on_type_changed(self):
        self._save_settings()
        self._on_mode_changed()

    def reset_display_for_current_mode(self):
        """Immediately clears the table and populates rows for the active mode & DOY."""
        stations = self.parse_stations()
        if not stations:
            return
        slots_utc = self.build_slots_utc()
        self._prefill_slots(stations, slots_utc, self._hourly())
        
        # Update top calendar banner immediately
        if slots_utc and hasattr(self, "calendar_strip"):
            y = slots_utc[0].year
            doy = slots_utc[0].timetuple().tm_yday
            cal_str = slots_utc[0].strftime("%Y-%m-%d (%a, %d %b %Y)")
            self.calendar_strip.setText(f"📅 Year {y}  ·  DOY {doy:03d}  ·  {cal_str} (UTC)")

    def _on_mode_changed(self):
        is_live = self.mode_live.isChecked()
        self.year_spin.setEnabled(not is_live)
        self.doy_spin.setEnabled(not is_live)
        self.today_btn.setEnabled(not is_live)

        if is_live:
            if hasattr(self, "start_stop_btn"):
                self.start_stop_btn.setText("⏹ Stop Monitoring" if self.is_monitoring else "▶ Start Monitoring")
                self.start_stop_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #c62828 if self.is_monitoring else #2e7d32;
                        color: white;
                        font-weight: bold;
                        padding: 8px;
                        border-radius: 4px;
                    }
                """)
            self._set_today_doy()
            self.log("Switched to Live Today Monitoring Mode.")
        else:
            if self.is_monitoring:
                self.is_monitoring = False
                self.timer.stop()
            if hasattr(self, "start_stop_btn"):
                self.start_stop_btn.setText("Scan Julian Day")
                self.start_stop_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #1976D2;
                        color: white;
                        font-weight: bold;
                        padding: 8px;
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        background-color: #1565C0;
                    }
                """)
            y = self.year_spin.value()
            d = self.doy_spin.value()
            self.log(f"Switched to Specific Julian Day Mode ({y} DOY {d:03d}). Background auto-scan paused.")

        self._save_settings()
        
        self.reset_display_for_current_mode()
    def log(self, text):
        now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self.logbox.appendPlainText(f"[{now_str}] {text}")

    def parse_stations(self):
        raw = re.split(r"[,\s;]+", self.stations.text().strip())
        seen, out = set(), []
        for x in raw:
            x = x.strip().upper()
            if len(x) in (4, 9) and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def build_slots_utc(self):
        hourly = self._hourly()
        now_utc = dt.datetime.now(dt.timezone.utc)

        if self.mode_live.isChecked():
            self.year_spin.blockSignals(True)
            self.doy_spin.blockSignals(True)
            self.year_spin.setValue(now_utc.year)
            self.doy_spin.setValue(now_utc.timetuple().tm_yday)
            self.year_spin.blockSignals(False)
            self.doy_spin.blockSignals(False)
            self._update_doy_date_preview()

            base_day = dt.datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0, tzinfo=dt.timezone.utc)
            if hourly:
                return [base_day + dt.timedelta(hours=h) for h in range(now_utc.hour + 1)]
            else:
                return [base_day]

        y = self.year_spin.value()
        doy = self.doy_spin.value()
        base_day = dt.datetime(y, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(days=doy - 1)

        if not hourly:
            return [base_day]

        return [base_day + dt.timedelta(hours=h) for h in range(24)]

    def refresh_live_now(self):
        """Forces an immediate, fresh live scan of today's hours from CDDIS (bypassing cache)."""
        if self.worker and self.worker.isRunning():
            self.statusBar().showMessage("A scan is already in progress...", 3000)
            return
        self.log("Manual Live Refresh triggered: Querying all today's hours fresh from CDDIS...")
        self.run_check(force_fresh=True)

    def on_check_btn_clicked(self):
        if self.mode_live.isChecked():
            self.refresh_live_now()
        else:
            self.run_check(force_fresh=True)

    def run_check(self, trigger_catchup_on_finish=False, force_fresh=False):
        self._save_settings()
        if self.worker and self.worker.isRunning():
            return

        stations = self.parse_stations()
        if not stations:
            self.statusBar().showMessage("Specify at least one station ID", 5000)
            self.log("Check stopped: No station IDs configured.")
            return

        self.refresh_doy_strip()

        slots_utc = self.build_slots_utc()
        if hasattr(self, "check_btn"): self.check_btn.setEnabled(False)
        if hasattr(self, "btn_live_refresh"): self.btn_live_refresh.setEnabled(False)
        self.set_indicator("scanning", "SCANNING")
        self.scan_progress.show()
        self.scan_progress.setRange(0, 0)  # Activates continuous smooth sliding animation immediately

        y = slots_utc[0].year
        doy = slots_utc[0].timetuple().tm_yday
        self.statusBar().showMessage(f"Scanning Year {y} DOY {doy:03d} ({len(slots_utc)} slots)...")
        self.log(f"Started scan for Year {y} Julian Day {doy:03d} across {len(slots_utc)} slot(s) for {', '.join(stations)}.")

        if hasattr(self, "worker") and self.worker is not None:
            if self.worker.isRunning():
                self.worker.wait(1500)

        # Collect existing OK records from memory to prevent false alerts on previous hours
        cached_recs = {}
        if not force_fresh and hasattr(self, "latest_rows") and self.latest_rows:
            for r in self.latest_rows:
                if r.get("status") == "OK":
                    k = (r["station"].upper(), r["year"], r["doy"], r["hour_str"])
                    cached_recs[k] = r

        self.worker = CheckWorker(
            self.session, self.type_box.currentText(),
            slots_utc, stations, self.grace.value(), cached_records=cached_recs, parent=self
        )
        self.worker.results.connect(self.on_results)
        self.worker.slot_checked.connect(self.on_slot_checked)
        self._prefill_slots(stations, slots_utc, self._hourly())
        
        self.worker.failed.connect(self.on_failed)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.on_progress)

        def on_worker_done():
            if hasattr(self, "check_btn"): self.check_btn.setEnabled(True)
            if hasattr(self, "btn_live_refresh"): self.btn_live_refresh.setEnabled(True)
            now_utc = dt.datetime.now(dt.timezone.utc)
            if not self.mode_live.isChecked() and (y < now_utc.year or doy < now_utc.timetuple().tm_yday):
                if self.latest_rows:
                    target_dir = self.report_path.text().strip()
                    self._save_per_station_doy_files(target_dir, y, doy, self.latest_rows)
                    self.refresh_doy_strip()
            if trigger_catchup_on_finish or self.is_monitoring:
                QtCore.QTimer.singleShot(500, self._start_background_doy_catchup)

        self.worker.finished.connect(on_worker_done)
        self.worker.start()

    def on_progress(self, current, total, text):
        if not self.scan_progress.isVisible():
            self.scan_progress.show()
        if total > 0 and self.scan_progress.maximum() == 0:
            self.scan_progress.setRange(0, total)
        if total > 0:
            self.scan_progress.setValue(current)
        self.statusBar().showMessage(f"[{current}/{total}] {text}")

    def on_failed(self, msg):
        self.set_indicator("error", "ERROR")
        self.log(f"ERROR: {msg}")
        self.statusBar().showMessage(f"Check failed: {msg}")
        self.summary.setText(f"<b style='color:{STATUS_COLORS['ERROR']}'>Scan Failed:</b> {html.escape(msg)}")
        if self.is_monitoring and "connection" not in self.alerted:
            self.alerted.add("connection")
            self.notify("CDDIS Connection Failed", msg)

    def _on_header_clicked(self, logical_index):
        if logical_index == 8:
            tz_keys = list(getattr(self, "TIMEZONES", {}).keys())
            if not tz_keys:
                return
            current_tz = self.settings.value("display_tz", "IST (UTC+05:30)")
            try:
                curr_idx = tz_keys.index(current_tz)
                next_idx = (curr_idx + 1) % len(tz_keys)
            except ValueError:
                next_idx = 0
            new_tz = tz_keys[next_idx]
            self.settings.setValue("display_tz", new_tz)
            if hasattr(self, "tz_combo"):
                self.tz_combo.setCurrentText(new_tz)
            self._update_all_tz_columns(new_tz)

    def _set_timezone(self, tz_name):
        self.settings.setValue("display_tz", tz_name)
        self._update_all_tz_columns(tz_name)
        self.log(f"Timezone switched to: {tz_name}")

    def _on_tz_combo_changed(self, index):
        tz_name = self.tz_combo.currentText()
        self.settings.setValue("display_tz", tz_name)
        self._update_all_tz_columns(tz_name)
        self.log(f"Display timezone updated to: {tz_name}")

    def _update_all_tz_columns(self, tz_name):
        tz_tag = tz_name.split()[0]
        header_text = tz_tag

        if hasattr(self, "table"):
            self.table.setHorizontalHeaderItem(8, QtWidgets.QTableWidgetItem(header_text))
        if hasattr(self, "station_tables"):
            for tbl in self.station_tables.values():
                tbl.setHorizontalHeaderItem(8, QtWidgets.QTableWidgetItem(header_text))

        if hasattr(self, "log_group") and self.log_group:
            self.log_group.setTitle(f"Activity Work Log ({header_text})")

        active_tz = getattr(self, "TIMEZONES", {}).get(tz_name, dt.timezone.utc)

        def _recalc_table(tbl):
            for row in range(tbl.rowCount()):
                utc_item = tbl.item(row, 7)
                if utc_item and utc_item.text() and "-" not in utc_item.text():
                    try:
                        raw_utc = utc_item.text().replace(" UTC", "").strip()
                        parts = raw_utc.split(":")
                        if len(parts) == 3:
                            now_u = dt.datetime.now(dt.timezone.utc).replace(
                                hour=int(parts[0]), minute=int(parts[1]), second=int(parts[2])
                            )
                            loc_str = now_u.astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")
                            tbl.setItem(row, 8, QtWidgets.QTableWidgetItem(loc_str))
                    except Exception:
                        pass

        if hasattr(self, "table"):
            _recalc_table(self.table)
        if hasattr(self, "station_tables"):
            for tbl in self.station_tables.values():
                _recalc_table(tbl)

    def log(self, msg):
        try:
            tz_name = self.settings.value("display_tz", "IST (UTC+05:30)")
            tz_tag = tz_name.split()[0]
            target_tz = getattr(self, "TIMEZONES", {}).get(tz_name, dt.timezone.utc)
            now_dt = dt.datetime.now(dt.timezone.utc).astimezone(target_tz)
            ts = now_dt.strftime(f"[%Y-%m-%d %H:%M:%S {tz_tag}]")
        except Exception:
            ts = dt.datetime.now(dt.timezone.utc).strftime("[%Y-%m-%d %H:%M:%S UTC]")

        if not hasattr(self, "logbox") or self.logbox is None:
            return

        if msg.startswith("PROGRESS:"):
            clean_text = f"{ts} {msg[9:]}"
            if getattr(self, "_progress_active", False) and self.logbox.document().blockCount() > 0:
                cursor = QtGui.QTextCursor(self.logbox.document().lastBlock())
                cursor.select(QtGui.QTextCursor.BlockUnderCursor)
                cursor.insertText(clean_text)
            else:
                self.logbox.appendPlainText(clean_text)
                self._progress_active = True
            return

        self._progress_active = False
        line = f"{ts} {msg}"
        self.logbox.appendPlainText(line)

    def _make_table(self):
        t = QtWidgets.QTableWidget(0, len(self.COLS))
        t.setHorizontalHeaderLabels(self.COLS)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(8, QtWidgets.QHeaderView.ResizeToContents)
        hh.sectionClicked.connect(self._on_header_clicked)
        return t

    def _pulse_heartbeat(self):
        self._hb_state = not self._hb_state
        self.hb_led.setStyleSheet(f"color: {"#0071e3" if self._hb_state else "#a1a1a6"}; font-size: 10px; border: none; background: transparent; padding-left: 2px;")

    def _on_tz_changed(self, text):
        self.settings.setValue("display_tz", text)
        tz_tag = text.split()[0]
        new_header_title = "IST"
        self.COLS[-1] = new_header_title
        
        # Update header title across all active tables
        if hasattr(self, "table"):
            self.table.setHorizontalHeaderItem(8, QtWidgets.QTableWidgetItem(new_header_title))
        if hasattr(self, "station_tables"):
            for stn_tbl in self.station_tables.values():
                stn_tbl.setHorizontalHeaderItem(8, QtWidgets.QTableWidgetItem(new_header_title))

        # Re-convert visible rows if rows exist
        if hasattr(self, "_last_rows") and self._last_rows:
            self.on_results(self._last_rows)

    def _update_heartbeat_timestamp(self):
        now_str = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S UTC")
        self.hb_label.setText(f"Live | Updated: {now_str}")

    def _on_stn_text_changed(self, text):
        current = text.strip()
        last = getattr(self, "_last_synced_stns", "")
        if current != last:
            self.btn_sync_stns.show()
        else:
            self.btn_sync_stns.hide()

    def _hot_sync_stations(self):
        new_text = getattr(self, "stations", getattr(self, "stn_input", None)).text().strip()
        self._last_synced_stns = new_text
        self.btn_sync_stns.hide()
        self.settings.setValue("stations", new_text)

        raw_stations = [s.strip().upper() for s in new_text.split(",") if s.strip()]
        # Strictly keep 4-character alphanumeric station names (ignore "FEED", "NO FEED", etc.)
        stations = [s for s in raw_stations if len(s) == 4 and s.isalnum() and s != "FEED"]
        if not stations:
            return

        self.log(f"Hot-synced stations: {', '.join(stations)}")

        # Remove invalid tabs like "Feed" if present
        for i in reversed(range(self.station_tabs.count())):
            text = self.station_tabs.tabText(i)
            # Remove tabs that are not "All Stations" and not in current valid station list
            clean_tab_id = re.sub(r"[^A-Za-z0-9]", "", text).upper()
            if text != "All Stations" and (clean_tab_id not in stations or "FEED" in text.upper()):
                widget = self.station_tabs.widget(i)
                self.station_tabs.removeTab(i)
                for k in list(self.station_tables.keys()):
                    if self.station_tables[k] is widget or k.upper() == "FEED":
                        del self.station_tables[k]

        # Synchronize tabs and tables strictly with current stations list
        self._sync_station_tabs(stations)

        if getattr(self, "is_monitoring", False):
            self.run_check(trigger_catchup_on_finish=False)

    def _get_cached_slot_timestamp(self, station, hour_idx):
        try:
            doy = self._current_doy()
            year = self._current_year()
            root = self.root_input.text().strip()
            live_path = os.path.join(root, station, f"{year}_DOY{doy:03d}_live.json")
            if os.path.exists(live_path):
                with open(live_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                slot_info = data.get("slots", {}).get(f"{hour_idx:02d}", {})
                if slot_info.get("status") in ("OK", "VERIFIED") and slot_info.get("checked_utc"):
                    return slot_info.get("checked_utc")
        except Exception:
            pass
        return None

    def _save_slot_to_live_cache(self, station, hour_idx, status, uploaded_files, checked_utc):
        try:
            doy = self._current_doy()
            year = self._current_year()
            root = self.root_input.text().strip()
            stn_dir = os.path.join(root, station)
            os.makedirs(stn_dir, exist_ok=True)
            live_path = os.path.join(stn_dir, f"{year}_DOY{doy:03d}_live.json")

            data = {"year": year, "doy": doy, "station": station, "slots": {}}
            if os.path.exists(live_path):
                try:
                    with open(live_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass

            # Only lock arrival timestamp if not already locked
            existing_slot = data.setdefault("slots", {}).get(f"{hour_idx:02d}", {})
            if existing_slot.get("status") == "OK" and existing_slot.get("checked_utc"):
                locked_ts = existing_slot.get("checked_utc")
            else:
                locked_ts = checked_utc

            data["slots"][f"{hour_idx:02d}"] = {
                "status": status,
                "files": uploaded_files,
                "checked_utc": locked_ts
            }

            with open(live_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return locked_ts
        except Exception as e:
            return checked_utc

    def _sync_station_tabs(self, target_stations):
        """Adds missing tabs and removes obsolete tabs so UI exactly matches the Stations config."""
        import re
        target_upper = set(s.upper() for s in target_stations if s.strip())

        # 1. Remove tabs that are no longer in target_stations
        idx = 1  # Index 0 is always 'All Stations'
        while idx < self.station_tabs.count():
            tab_text = self.station_tabs.tabText(idx)
            cleaned = re.sub(r'^[⚪🟢🔴⚠️\s]+', '', tab_text)
            cleaned = re.sub(r'\(.*?\)', '', cleaned).strip()
            raw_stn = cleaned.split()[0].upper() if cleaned else ""

            if raw_stn and raw_stn not in target_upper:
                self.station_tabs.removeTab(idx)
                if raw_stn in self.station_tables:
                    del self.station_tables[raw_stn]
            else:
                idx += 1

        # 2. Add tabs for newly configured stations
        for stn in target_stations:
            stn_u = stn.upper()
            if stn_u not in self.station_tables:
                stn_tbl = self._make_table()
                self.station_tables[stn_u] = stn_tbl
                self.station_tabs.addTab(stn_tbl, f"⚪ {stn_u}")

    def _prefill_slots(self, stations, slots, is_hourly):
        self.table.setRowCount(0)
        self._sync_station_tabs(stations)
        for stn in stations:
            stn_u = stn.upper()
            if stn_u in self.station_tables:
                self.station_tables[stn_u].setRowCount(0)

        current_tz_name = self.tz_combo.currentText() if hasattr(self, "tz_combo") else "IST (UTC+05:30)"
        active_tz = getattr(self, "TIMEZONES", TIMEZONES).get(current_tz_name, dt.timezone.utc)
        tz_tag = current_tz_name.split()[0]
        now_dt = dt.datetime.now(dt.timezone.utc)
        now_local = now_dt.astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")

        for slot in slots:
            if is_hourly:
                hh = slot.hour
                slot_disp = f"Hour {hh:02d}  ({hh:02d}:00 - {(hh + 1) % 24:02d}:00 UTC)"
            else:
                slot_disp = f"DOY {slot.timetuple().tm_yday:03d} (Full Day UTC)"

            for stn in stations:
                row_idx = self.table.rowCount()
                self.table.insertRow(row_idx)
                stn_tbl = self.station_tables.get(stn)
                stn_row_idx = stn_tbl.rowCount() if stn_tbl else None
                if stn_tbl:
                    stn_tbl.insertRow(stn_row_idx)

                vals = [stn, slot_disp, "PENDING", "-", "0/1", "–", "–", now_dt.strftime("%H:%M:%S UTC"), now_local]
                for j, v in enumerate(vals):
                    it = QtWidgets.QTableWidgetItem(v)
                    if j == 2:
                        it.setForeground(QtGui.QBrush(QtGui.QColor("white")))
                        it.setBackground(QtGui.QBrush(QtGui.QColor(STATUS_COLORS["PENDING"])))
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
                    self.table.setItem(row_idx, j, it)
                    if stn_tbl:
                        it_s = QtWidgets.QTableWidgetItem(v)
                        if j == 2:
                            it_s.setForeground(QtGui.QBrush(QtGui.QColor("white")))
                            it_s.setBackground(QtGui.QBrush(QtGui.QColor(STATUS_COLORS["PENDING"])))
                            f_s = it_s.font()
                            f_s.setBold(True)
                            it_s.setFont(f_s)
                        stn_tbl.setItem(stn_row_idx, j, it_s)

    def _save_slot_to_live_state(self, r):
        try:
            import json
            root = self.report_path.text().strip() or os.path.expanduser("~/Downloads/CDDM")
            stn = r["station"].upper()
            year = int(r["year"])
            doy = int(r["doy"])
            hh_str = str(r.get("hour_str", "00"))
            stn_dir = os.path.join(root, stn)
            os.makedirs(stn_dir, exist_ok=True)
            live_path = os.path.join(stn_dir, f"{year}_DOY{doy:03d}_live.json")

            data = {"year": year, "doy": doy, "station": stn, "slots": {}}
            if os.path.exists(live_path):
                try:
                    with open(live_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass

            checked_str = r["checked"].strftime("%H:%M:%S UTC") if hasattr(r.get("checked"), "strftime") else str(r.get("checked", "-"))
            existing_slot = data.setdefault("slots", {}).get(hh_str, {})
            if existing_slot.get("status") == "OK" and existing_slot.get("checked_utc"):
                locked_ts = existing_slot["checked_utc"]
            else:
                locked_ts = checked_str

            data["slots"][hh_str] = {
                "status": r.get("status", "PENDING"),
                "files": r.get("filenames", "-"),
                "checked_utc": locked_ts,
            }
            with open(live_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def on_slot_checked(self, r):
        _st = str(r.get("status", "")).upper()
        if _st in ("OK", "PENDING", "MISSING"):
            self._set_conn_pill_state("connected", "CDDIS Connected")
        elif "AUTH" in _st or "EXPIRED" in _st:
            self._set_conn_pill_state("expired", "Session Expired")
        elif "ERR" in _st or "OFFLINE" in _st or "TIMEOUT" in _st:
            self._set_conn_pill_state("offline", "Connection Error")
        hh = r["slot"].hour if hasattr(r["slot"], "hour") else 0
        self.hb_led.setStyleSheet("color: #ff9500; font-size: 13px; border: none; background: transparent;")
        self.hb_label.setText(f"Scanning Hr {hh:02d} ({r.get('station', '')})...")
        
        is_hr = self._hourly()
        target_slot_match = f"Hour {hh:02d}" if is_hr else f"DOY {r['doy']:03d}"
        if is_hr:
            slot_disp = f"Hour {hh:02d}  ({hh:02d}:00 - {(hh + 1) % 24:02d}:00 UTC)"
        else:
            slot_disp = f"DOY {r['doy']:03d} (Full Day UTC)"

        current_tz_name = self.tz_combo.currentText() if hasattr(self, "tz_combo") else "IST (UTC+05:30)"
        active_tz = getattr(self, "TIMEZONES", TIMEZONES).get(current_tz_name, dt.timezone.utc)
        tz_tag = current_tz_name.split()[0]
        local_time = r["checked"].astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")

        vals = [
            r["station"], slot_disp, r["status"], r["filenames"],
            f"{r['files']}/{r['expected']}",
            f"{r['size'] / 1024:.1f} KB" if r["size"] else "–",
            r["mod"] or "–",
            r["checked"].strftime("%H:%M:%S UTC"),
            local_time
        ]

        def _update_tbl(tbl):
            for row in range(tbl.rowCount()):
                stn_i = tbl.item(row, 0)
                slot_i = tbl.item(row, 1)
                if stn_i and slot_i and stn_i.text().strip().upper() == r["station"].strip().upper():
                    if target_slot_match in slot_i.text():
                        for col_idx, val in enumerate(vals):
                            it = QtWidgets.QTableWidgetItem(val)
                            if col_idx == 2:
                                it.setForeground(QtGui.QBrush(QtGui.QColor("white")))
                                it.setBackground(QtGui.QBrush(QtGui.QColor(STATUS_COLORS.get(r["status"], "#757575"))))
                                f = it.font()
                                f.setBold(True)
                                it.setFont(f)
                            tbl.setItem(row, col_idx, it)
                        break

        _update_tbl(self.table)
        self._save_slot_to_live_state(r)
        stn_tbl = self.station_tables.get(r["station"])
        if stn_tbl:
            _update_tbl(stn_tbl)

    def on_results(self, rows):
        self.scan_progress.hide()
        current_tz_name = self.tz_combo.currentText() if hasattr(self, "tz_combo") else "IST (UTC+05:30)"
        active_tz = getattr(self, "TIMEZONES", {}).get(current_tz_name, dt.timezone.utc)
        tz_tag = current_tz_name.split()[0]
        now_str = dt.datetime.now(dt.timezone.utc).astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")
        self.hb_led.setStyleSheet("color: #0071e3; font-size: 13px; border: none; background: transparent;")
        self.hb_label.setText(f"Live  |  Updated: {now_str}")
        self._last_rows = rows
        if not hasattr(self, "_verified_history"):
            self._verified_history = {}

        # Preserve original checked timestamps for established OK slots
        for r in rows:
            key = (r["station"], r["year"], r["doy"], r["slot"])
            if key in self._verified_history and self._verified_history[key]["status"] == "OK":
                r["checked"] = self._verified_history[key]["checked"]
            elif r["status"] == "OK":
                self._verified_history[key] = {
                    "status": "OK",
                    "checked": r["checked"]
                }
        self.alerted.discard("connection")
        self.latest_rows = rows
        rows.sort(key=lambda r: (r["slot"], r["station"]))

        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        self.table.setRowCount(0)
        
        configured_stns = self.parse_stations()
        self._sync_station_tabs(configured_stns)
        for stn in configured_stns:
            stn_u = stn.upper()
            if stn_u in self.station_tables:
                self.station_tables[stn_u].setRowCount(0)

        active_stns = sorted(list(set(r["station"] for r in rows)))
        stn_has_missing = {stn: False for stn in active_stns}

        current_header_str = None
        row_idx = 0

        for r in rows:
            stn = r["station"]
            if r["status"] == "MISSING":
                stn_has_missing[stn] = True
            doy_num = r["doy"]
            cal_str = r["slot"].strftime("%Y-%m-%d (%a, %d %b %Y)")
            header_str = f"📅 Year {r["year"]}  ·  DOY {doy_num:03d}  ·  {cal_str} (UTC)"
            if hasattr(self, "calendar_strip") and header_str != current_header_str:
                current_header_str = header_str
                self.calendar_strip.setText(header_str)

            if self._hourly():
                hh = r["slot"].hour
                slot_display = f"Hour {hh:02d}  ({hh:02d}:00 - {(hh + 1) % 24:02d}:00 UTC)"
            else:
                slot_display = f"DOY {r['doy']:03d} (Full Day UTC)"

            current_tz_name = self.tz_combo.currentText() if hasattr(self, "tz_combo") else "IST (UTC+05:30)"
            active_tz = getattr(self, "TIMEZONES", TIMEZONES).get(current_tz_name, dt.timezone.utc)
            tz_tag = current_tz_name.split()[0]
            local_time_str = r["checked"].astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")

            vals = [
                r["station"],
                slot_display,
                r["status"],
                r["filenames"],
                f"{r["files"]}/{r["expected"]}",
                f"{r["size"] / 1024:.1f} KB" if r["size"] else "–",
                r["mod"] or "–",
                r["checked"].strftime("%H:%M:%S UTC"),
                local_time_str
            ]

            self.table.insertRow(row_idx)
            stn_tbl = self.station_tables.get(stn)
            stn_row_idx = stn_tbl.rowCount() if stn_tbl else None
            if stn_tbl is not None:
                stn_tbl.insertRow(stn_row_idx)

            for j, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                if j == 2:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("white")))
                    it.setBackground(QtGui.QBrush(QtGui.QColor(STATUS_COLORS[r["status"]])))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(row_idx, j, it)

                if stn_tbl is not None:
                    it_stn = QtWidgets.QTableWidgetItem(v)
                    if j == 2:
                        it_stn.setForeground(QtGui.QBrush(QtGui.QColor("white")))
                        it_stn.setBackground(QtGui.QBrush(QtGui.QColor(STATUS_COLORS[r["status"]])))
                        f_stn = it_stn.font()
                        f_stn.setBold(True)
                        it_stn.setFont(f_stn)
                    stn_tbl.setItem(stn_row_idx, j, it_stn)

            row_idx += 1

        for idx in range(1, self.station_tabs.count()):
            tab_text = self.station_tabs.tabText(idx)
            # Strip emojis, symbols, and parenthetical notes to keep the exact station code
            cleaned = re.sub(r'^[⚪🟢🔴⚠️\s]+', '', tab_text)
            cleaned = re.sub(r'\(.*?\)', '', cleaned).strip()
            raw_stn = cleaned.split()[0] if cleaned else tab_text.strip()
            
            # Find all scanned rows for this station
            stn_rows = [r for r in rows if r["station"].upper() == raw_stn.upper()]
            missing_cnt = sum(1 for r in stn_rows if r["status"] == "MISSING")
            ok_cnt = sum(1 for r in stn_rows if r["status"] == "OK")
            pending_cnt = sum(1 for r in stn_rows if r["status"] == "PENDING")

            # If every single completed slot is missing (0 OK), mark as No Feed
            if len(stn_rows) > 0 and ok_cnt == 0 and missing_cnt > 0:
                self.station_tabs.setTabText(idx, f"⚠️ {raw_stn} (No Feed)")
            elif missing_cnt > 0:
                self.station_tabs.setTabText(idx, f"🔴 {raw_stn}")
            else:
                self.station_tabs.setTabText(idx, f"🟢 {raw_stn}")

        self.scan_progress.hide()
        current_tz_name = self.tz_combo.currentText() if hasattr(self, "tz_combo") else "IST (UTC+05:30)"
        active_tz = getattr(self, "TIMEZONES", TIMEZONES).get(current_tz_name, dt.timezone.utc)
        tz_tag = current_tz_name.split()[0]
        now_local = dt.datetime.now(dt.timezone.utc).astimezone(active_tz).strftime(f"%H:%M:%S {tz_tag}")
        self.hb_label.setText(f"Live  |  Updated: {now_local}")
        self.hb_led.setStyleSheet("color: #0071e3; font-size: 10px; border: none; background: transparent;")

        if self.is_monitoring:
            self._batch_alert_logic(rows)

        if counts.get("MISSING", 0) > 0:
            self.set_indicator("missing", "MISSING")
        elif counts.get("PARTIAL", 0) > 0:
            self.set_indicator("partial", "PARTIAL")
        else:
            self.set_indicator("ok", "ALL OK")

        parts = [
            f"<span style='color:{STATUS_COLORS[k]}'><b>{counts[k]}</b> {k}</span>"
            for k in ("OK", "PARTIAL", "MISSING", "PENDING") if k in counts
        ]
        self.summary.setText(" | ".join(parts) or "No data found")
        now_str = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S UTC")
        nxt = f" – next in {self.interval.value()}m" if (self.is_monitoring and self.mode_live.isChecked()) else ""
        self.statusBar().showMessage(f"Updated at {now_str}{nxt}")
        self.log(f"Scan complete. Summary: {', '.join(f'{k}={v}' for k, v in counts.items())}")
        try:
            target_dir = self.report_path.text().strip() or os.getcwd()
            cur_year = int(self.year_spin.value())
            cur_doy = int(self.doy_spin.value())
            self._save_per_station_doy_files(target_dir, cur_year, cur_doy, rows)
            self.log(f"Live summary synchronized to <STATION>/{cur_doy:03d}.txt")
        except Exception as e:
            self.log(f"Notice: DOY summary auto-save: {e}")

    def _batch_alert_logic(self, rows):
        missing_items = []
        partial_items = []
        recovered_items = []

        for r in rows:
            key = (r["station"], r["slot"], self.type_box.currentText())
            time_label = f"DOY {r['doy']:03d} Hr {r['hour_str']} UTC"

            if r["status"] == "MISSING":
                if key not in self.alerted:
                    self.alerted.add(key)
                    missing_items.append(f"• {r['station']} [{time_label}]: 0/{r['expected']} files")
            elif r["status"] == "PARTIAL":
                if key not in self.alerted:
                    self.alerted.add(key)
                    partial_items.append(f"• {r['station']} [{time_label}]: {r['files']}/{r['expected']} files")
            elif r["status"] == "OK" and key in self.alerted:
                self.alerted.discard(key)
                recovered_items.append(f"• {r['station']} [{time_label}] ({r['filenames']})")

        total_alerts = len(missing_items) + len(partial_items) + len(recovered_items)
        if total_alerts == 0:
            return

        body_lines = []
        if missing_items:
            body_lines.append(f"MISSING ({len(missing_items)}):")
            body_lines.extend(missing_items[:5])
            if len(missing_items) > 5:
                body_lines.append(f"  ...and {len(missing_items) - 5} more")

        if partial_items:
            if body_lines:
                body_lines.append("")
            body_lines.append(f"PARTIAL ({len(partial_items)}):")
            body_lines.extend(partial_items[:3])

        if recovered_items:
            if body_lines:
                body_lines.append("")
            body_lines.append(f"RECOVERED ({len(recovered_items)}):")
            body_lines.extend(recovered_items[:3])

        title = f"CDDIS Alert: {len(missing_items)} Missing, {len(partial_items)} Partial"
        full_text = "\n".join(body_lines)
        self.notify(title, full_text)

    def tray_notify(self, title, text):
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(title, text, QtWidgets.QSystemTrayIcon.Information, 8000)

    def notify(self, title, text):
        self.log(f"ALERT NOTIFICATION:\n{text}")
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(title, text, QtWidgets.QSystemTrayIcon.Warning, 12000)
        if shutil.which("notify-send"):
            try:
                subprocess.Popen(["notify-send", "-u", "critical", title, text])
            except Exception:
                pass
        # Trigger configured MP3 alert sound with 25s auto-stop
        self.play_alert_audio(max_duration_sec=25)

    def _check_daily_report_trigger(self):
        now_local = dt.datetime.now()
        if now_local.weekday() > 4:
            return

        today_date = now_local.date()
        if self.last_daily_report_date == today_date:
            return

        if (now_local.hour == 16 and now_local.minute >= 30) or now_local.hour > 16:
            self.last_daily_report_date = today_date
            self._generate_daily_report(now_local)

    def _generate_daily_report(self, trigger_time):
        target_dir = self.report_path.text().strip()
        stations = self.parse_stations()
        if not stations:
            return

        self.log("Triggered automated 16:30 Daily Report scan (Past 24 Hours UTC) per station...")
        now_utc = dt.datetime.now(dt.timezone.utc)
        hourly = self._hourly()
        if hourly:
            cur = now_utc.replace(minute=0, second=0, microsecond=0)
            slots_24h = [cur - dt.timedelta(hours=i) for i in range(24)]
        else:
            today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            slots_24h = [today - dt.timedelta(days=i) for i in range(1, 3)]

        if hasattr(self, "audit_worker") and self.audit_worker is not None:
            if self.audit_worker.isRunning():
                self.audit_worker.wait(1500)
        self.audit_worker = CheckWorker(
            self.session, self.type_box.currentText(),
            slots_24h, stations, self.grace.value(), self
        )

        def write_report_files(rows):
            by_station = {}
            for r in rows:
                by_station.setdefault(r["station"], []).append(r)

            for st, st_rows in by_station.items():
                try:
                    st_dir = os.path.join(target_dir, st)
                    os.makedirs(st_dir, exist_ok=True)
                    file_path = os.path.join(
                        st_dir, f"daily_1630_{trigger_time.strftime('%Y%m%d')}_UTC.txt"
                    )
                    total = len(st_rows)
                    counts = {}
                    for r in st_rows:
                        counts[r["status"]] = counts.get(r["status"], 0) + 1

                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("=" * 110 + "\n")
                        f.write("NASA CDDIS GNSS ARCHIVE - DAILY 16:30 WORKING SUMMARY REPORT (UTC)\n")
                        f.write(f"Station:      {st}\n")
                        f.write(f"Generated at: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')} Local Time\n")
                        f.write(f"Data Type:    {self.type_box.currentText()}\n")
                        f.write("=" * 96 + "\n\n")

                        f.write("SUMMARY BREAKDOWN:\n")
                        for st_type in ("OK", "PARTIAL", "MISSING", "PENDING"):
                            c = counts.get(st_type, 0)
                            pct = (c / total * 100) if total else 0
                            f.write(f"  - {st_type:<10}: {c:>3} ({pct:.1f}%)\n")
                        f.write("\n" + "-" * 96 + "\n")
                        f.write(
                            f"{'Station':<10} {'Year/DOY/Hr':<18} {'Status':<10} {'Files':<7} "
                            f"{'Size (KB)':<10} {'Uploaded File(s)'}\n"
                        )
                        f.write("-" * 96 + "\n")

                        for r in sorted(st_rows, key=lambda x: x["slot"]):
                            slot_str = f"{r['year']}/{r['doy']:03d}/{r['hour_str']}"
                            f.write(
                                f"{r['station']:<10} "
                                f"{slot_str:<18} "
                                f"{r['status']:<10} "
                                f"{r['files']}/{r['expected']:<5} "
                                f"{r['size']/1024:<10.1f} "
                                f"{r['filenames']}\n"
                            )
                        f.write("=" * 110 + "\n")
                    self.log(f"Daily 16:30 report saved for {st}: {file_path}")
                except Exception as ex:
                    self.log(f"Error saving daily 16:30 report for {st}: {ex}")

            self.tray_notify(
                "Daily 16:30 Summaries Saved",
                f"Per-station reports saved in {target_dir}/<STATION>/"
            )

        self.audit_worker.results.connect(write_report_files)
        self.audit_worker.start()

    def export_csv(self):
        if not self.latest_rows:
            self.statusBar().showMessage("No scan results available to export.", 4000)
            return
        default_name = os.path.join(
            self.report_path.text(),
            f"cddis_doy{self.doy_spin.value():03d}_{dt.datetime.now():%Y%m%d_%H%M}_UTC.csv"
        )
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Results", default_name, "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Station", "Year", "Julian Day (DOY)", "Hour (UTC)",
                    "Status", "Uploaded File(s)", "Files Count", "Size (KB)",
                    "Last-Modified", "Checked (UTC)", "IST", "Archive URL"
                ])
                for r in self.latest_rows:
                    writer.writerow([
                        r["station"],
                        r["year"],
                        f"{r['doy']:03d}",
                        r["hour_str"],
                        r["status"],
                        r["filenames"],
                        f"{r['files']}/{r['expected']}",
                        f"{r['size'] / 1024:.1f}" if r["size"] else "0",
                        r["mod"],
                        r["checked"].strftime("%Y-%m-%d %H:%M:%S UTC"),
                        r["url"]
                    ])
            self.log(f"Exported current table results to CSV: {path}")
            self.statusBar().showMessage(f"Exported to {path}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Export Failed", str(e))


    def _setup_audio_engine(self):
        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio_output = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        mp3_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert.mp3")
        if os.path.exists(mp3_path):
            self.player.setSource(QtCore.QUrl.fromLocalFile(mp3_path))

        saved_vol = self.settings.value("sound_volume", 50, type=int)
        self.audio_output.setVolume(saved_vol / 100.0)

        # 25-second cutoff timer
        self.audio_stop_timer = QtCore.QTimer(self)
        self.audio_stop_timer.setSingleShot(True)
        self.audio_stop_timer.timeout.connect(self.stop_alert_audio)

    def play_alert_audio(self, max_duration_sec=25):
        if not self.settings.value("sound_alerts_enabled", True, type=bool):
            return
        if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState:
            self.player.stop()

        self.player.setPosition(0)
        self.player.play()
        self.audio_stop_timer.start(max_duration_sec * 1000)

    def stop_alert_audio(self):
        if hasattr(self, "player") and self.player.playbackState() == QtMultimedia.QMediaPlayer.PlayingState:
            self.player.stop()

    def _handle_clear_alerts(self):
        self.alerted.clear()
        self.stop_alert_audio()

    def launch_network_diagnostics(self):
        import subprocess, shutil
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        diag_script = os.path.join(root_dir, "diagnose_network.sh")
        
        # Check available terminal emulators
        terms = ["gnome-terminal", "x-terminal-emulator", "konsole", "xfce4-terminal", "xterm"]
        found_term = next((t for t in terms if shutil.which(t)), None)
        
        if found_term:
            if found_term == "gnome-terminal" or found_term == "x-terminal-emulator":
                subprocess.Popen([found_term, "--", "bash", diag_script])
            else:
                subprocess.Popen([found_term, "-e", f"bash {diag_script}"])
        else:
            subprocess.Popen(["bash", diag_script])

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def handle_logout(self):
        # 1. Stop all active timers
        for timer_name in ("timer", "daily_report_timer", "hb_timer", "clock_timer"):
            t = getattr(self, timer_name, None)
            if t is not None:
                t.stop()

        # 2. Safely wait for running background threads
        for worker_name in ("worker", "bg_doy_worker", "audit_worker", "_hdr_conn_worker"):
            w = getattr(self, worker_name, None)
            if w is not None and w.isRunning():
                w.quit()
                w.wait(1500)

        # 3. Wipe saved credentials & auto-login flags from QSettings
        s = QtCore.QSettings("gnss-tools", "cddis-monitor")
        s.setValue("auto_login", False)
        s.remove("password")
        s.setValue("remember", False)

        # 4. Remove Earthdata entries from ~/.netrc if present
        try:
            import os
            netrc_path = os.path.expanduser("~/.netrc")
            if os.path.exists(netrc_path):
                with open(netrc_path, "r") as nf:
                    lines = nf.readlines()
                filtered = []
                skip = False
                for line in lines:
                    if "machine urs.earthdata.nasa.gov" in line:
                        skip = True
                        continue
                    if skip and line.strip().startswith("machine"):
                        skip = False
                    if not skip:
                        filtered.append(line)
                with open(netrc_path, "w") as nf:
                    nf.writelines(filtered)
        except Exception:
            pass

        # 4b. Hide and tear down system tray icon
        if hasattr(self, "tray") and self.tray:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

        # 5. Clear active session cookies
        if hasattr(self, "session") and self.session and hasattr(self.session, "cookies"):
            self.session.cookies.clear()

        # 6. Signal application controller to return to LoginDialog
        self.logout_requested = True
        self.close()




def main():
    from PySide6 import QtNetwork
    is_autostart = "--autostart" in sys.argv

    sys.argv[0] = "cddm"
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Single-instance guard: raise existing window instead of opening a duplicate process
    server_name = "cddm_live_v1_single_instance"
    probe_sock = QtNetwork.QLocalSocket()
    probe_sock.connectToServer(server_name)
    if probe_sock.waitForConnected(400):
        probe_sock.write(b"RAISE")
        probe_sock.flush()
        probe_sock.waitForBytesWritten(400)
        probe_sock.disconnectFromServer()
        sys.exit(0)

    QtNetwork.QLocalServer.removeServer(server_name)
    instance_server = QtNetwork.QLocalServer(app)
    instance_server.listen(server_name)
    active_win_ref = {"win": None}

    def _on_second_launch():
        conn = instance_server.nextPendingConnection()
        if conn:
            conn.waitForReadyRead(200)
            conn.close()
        w = active_win_ref.get("win")
        if w is not None:
            w.showNormal()
            w.raise_()
            w.activateWindow()

    instance_server.newConnection.connect(_on_second_launch)
    app.setDesktopFileName("cddm")
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cddis_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))
    app.setApplicationName("CDDM Live v1.1")

    settings = QtCore.QSettings("gnss-tools", "cddis-monitor")

    while True:
        session = None
        auto_login = settings.value("auto_login", False, type=bool)
        saved_user = settings.value("username", "")
        saved_pass = settings.value("password", "")

        # Try automatic silent authentication ONLY if auto_login is active AND a saved password exists
        if auto_login and saved_user and saved_pass:
            try:
                decoded_pw = base64.b64decode(saved_pass.encode()).decode()
                test_sess = EarthdataSession(saved_user, decoded_pw)
                r = test_sess.get(f"{BASE}/daily/", timeout=25, allow_redirects=True)
                if r.status_code == 200 and "urs.earthdata.nasa.gov" not in r.url:
                    session = test_sess
            except Exception:
                session = None

        # Show login dialog if not auto-logged in
        if not session:
            dlg = LoginDialog(is_autostart=is_autostart)
            res = dlg.exec()
            if res != QtWidgets.QDialog.Accepted or not dlg.session:
                break
            session = dlg.session

        win = MainWindow(session, is_autostart=is_autostart)
        win.logout_requested = False
        active_win_ref["win"] = win
        win.show()
        app.exec()
        active_win_ref["win"] = None

        # If user explicitly logged out, reopen the login dialog; otherwise exit
        if not getattr(win, "logout_requested", False):
            break

    sys.exit(0)

if __name__ == "__main__":
    main()


