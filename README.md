# 🛰️ CDDM Live — NASA CDDIS GNSS Archive Monitor

[![Python 3](https://img.shields.io/badge/Python-3.8%20--%203.14+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6%20(Qt6)-41CD52?logo=qt&logoColor=white)](https://wiki.qt.io/Qt_for_Python)
[![Platform](https://img.shields.io/badge/Platform-Linux%20(Ubuntu%2020.04--26.04)-E95420?logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

An autonomous desktop verification engine designed for geodesy and seismology pipelines. CDDM monitors real-time telemetry, audits historical gaps, and verifies observation completeness across NASA's Crustal Dynamics Data Information System (CDDIS) GNSS archives.

---

## ⚡ Key Features

* **Real-Time Live Telemetry Stream:** Tracks daily slot availability from Hour 00 to active UTC with arrival timestamps.
* **Offline Gap Audit Matrix (`📊 Gap Audit`):** Instantly audits station summaries from local disk without querying CDDIS or triggering NASA rate limits.
* **Intelligent Gap Re-Check (`↻ Recheck Gaps`):** Skips verified `24/24 OK` days, focusing background network requests strictly on missing or incomplete dates.
* **Strict 24/24 Verification Standard:** Requires every single hourly slot confirmed before displaying a complete badge, preventing false positives.
* **Automated Daily 24h Fallback:** Verifies daily composite archives (`*.crx.gz`) when real-time hourly telemetry experiences drops.
* **Self-Healing Environment:** Resilient launcher automatically repairs Python virtual environment symlinks after major Linux distribution upgrades.
* **Display-Aware CSV Export:** Generates missing-hours CSV reports filtered to visible records.

---

## 💻 System Requirements

| Component | Tested Specification |
| :--- | :--- |
| **Operating System** | Ubuntu 20.04, 22.04, 24.04, 26.04 LTS • Debian 11/12 • Fedora • Arch Linux |
| **Python** | Python 3.8 – 3.14+ (CPython x86_64) |
| **Display Server** | X11 and Wayland (GNOME, KDE, XFCE) |
| **Memory** | Minimum 1 GB RAM (~60 MB active memory during scans) |
| **Dependencies** | PySide6 (Qt6), Requests, Certifi |

---

## 🚀 Quick Start & Installation

Clone the repository and run the automated installer:

```bash
# 1. Clone repository
git clone https://github.com/wavgeo/cddm.git
cd cddm

# 2. Run automated installer (creates virtual environment & app menu entry)
./install.sh

# 3. Launch CDDM Live
./run_cddm.sh
```

> **Ubuntu App Menu:** Once installed, launch CDDM directly from your desktop application launcher by searching **"CDDM Live"**.

---

## 🌐 Network & Proxy Configuration

CDDM supports direct internet connections as well as university or institutional proxies:

```bash
# Configure Direct Internet or Institutional Proxy
./set_proxy.sh
```

If you encounter authentication or SSL certificate issues on a new network:

```bash
# Run the built-in diagnostic and auto-repair utility
bash diagnose_network.sh
```

---

## 📁 Repository Structure

```text
├── program/
│   ├── cddis_monitor.py      # Core PySide6 application engine
│   ├── cddis_icon.png        # Application desktop icon
│   └── alert.mp3             # Notification sound asset
├── install.sh                # Automated installer & desktop integrator
├── run_cddm.sh               # Self-healing desktop launcher
├── diagnose_network.sh       # Network & SSL diagnostic tool
├── set_proxy.sh              # Interactive proxy configurator
├── uninstall.sh              # Clean removal utility
├── requirements.txt          # Python package specifications
└── README.html               # Interactive documentation with constellation canvas
```

---

## 📄 License

Distributed under the MIT License. See `README.html` for full terms.
