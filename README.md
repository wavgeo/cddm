# 🛰️ CDDM Live — NASA CDDIS GNSS Archive Monitor

[![Python 3](https://img.shields.io/badge/Python-3.8%20--%203.14+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20(Qt6)-41CD52?logo=qt&logoColor=white)](https://wiki.qt.io/Qt_for_Python)
[![Linux](https://img.shields.io/badge/Platform-Linux%20(Ubuntu%2020.04--26.04)-E95420?logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![Windows](https://img.shields.io/badge/Platform-Windows%2010%7C11-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An autonomous desktop verification engine designed for geodesy and seismology pipelines. CDDM monitors real-time telemetry, audits historical gaps, and verifies observation completeness across NASA CDDIS GNSS archives.

---

## ⚡ Key Features

* **Real-Time Live Telemetry Stream:** Tracks daily slot availability from Hour 00 to active UTC with arrival timestamps.
* **Offline Gap Audit Matrix (`📊 Gap Audit`):** Instantly audits station summaries from local disk without querying CDDIS or triggering NASA rate limits.
* **Intelligent Gap Re-Check (`↻ Recheck Gaps`):** Skips verified `24/24 OK` days, focusing background network requests strictly on missing or incomplete dates.
* **Strict 24/24 Verification Standard:** Requires every single hourly slot confirmed before displaying a complete badge, preventing false positives.
* **Automated Daily 24h Fallback:** Verifies daily composite archives (`*.crx.gz`) when real-time hourly telemetry experiences drops.
* **Dual-Platform Desktop Integration:** Native launchers for Ubuntu desktop (`.desktop`) and Windows desktop (`.lnk`).
* **Display-Aware CSV Export:** Generates missing-hours CSV reports filtered to visible records.

---

## 💻 System Requirements

| Component | Linux Specification | Windows Specification |
| :--- | :--- | :--- |
| **OS** | Ubuntu 20.04–26.04, Debian 11/12 | Windows 10 / Windows 11 (64-bit) |
| **Python** | Python 3.8 – 3.14+ | Python 3.10 – 3.14+ (Ensure "Add to PATH" is checked) |
| **UI Engine** | PySide6 (Qt6) | PySide6 (Qt6) |
| **Memory** | Minimum 1 GB RAM (~60 MB active) | Minimum 1 GB RAM (~60 MB active) |
| **Dependencies** | PySide6, Requests, Certifi | PySide6, Requests, Certifi |

---

## 🚀 Quick Start & Installation

### Option A: Linux (Ubuntu, Debian, Fedora)

```bash
# 1. Clone repository
git clone [https://github.com/wavgeo/cddm.git](https://github.com/wavgeo/cddm.git)
cd cddm

# 2. Run automated installer
./install.sh

# 3. Launch CDDM Live
./run_cddm.sh
```

> **Ubuntu App Menu:** Once installed, launch CDDM directly from your applications menu by searching **"CDDM Live"**.

---

### Option B: Windows 10 / 11

1. **Prerequisite:** Install Python 3.10+ from python.org (check **"Add python.exe to PATH"** during installation).
2. **Installation:**
   * Open the `windows/` folder inside the cloned repository or extracted ZIP.
   * Double-click **`install.bat`**.
   * Enter authorization key `123456` when prompted.
3. **Launch:**
   * Double-click the **CDDM Live** desktop shortcut, or execute `windows/run_cddm.bat`.

---

## 🌐 Network & Proxy Configuration

* **Linux:** Run `./set_proxy.sh` to configure institutional HTTP/HTTPS proxies.
* **Linux Diagnostics:** Run `bash diagnose_network.sh` to verify NASA Earthdata and CDDIS endpoints.
* **Windows:** Automatically honors Windows system environment variables (`HTTP_PROXY`, `HTTPS_PROXY`).

---

## 📁 Repository Structure

```text
├── program/
│   ├── cddis_monitor.py      # Core PySide6 application engine
│   ├── cddis_icon.png        # Application desktop icon
│   └── alert.mp3             # Notification sound asset
├── windows/
│   ├── install.bat           # Windows automated installer & desktop shortcut creator
│   ├── run_cddm.bat          # Windows background launcher (pythonw)
│   ├── README.txt            # Windows quick-start guide
│   ├── requirements.txt      # Windows Python dependencies
│   └── program/              # Windows asset mirror
├── install.sh                # Linux auto-installer & .desktop integrator
├── run_cddm.sh               # Linux launcher
├── requirements.txt          # Python package specifications
└── README.md                 # Project documentation
```

---

## 📄 License

Distributed under the MIT License.
