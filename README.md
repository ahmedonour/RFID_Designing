# RFID Asset Manager 🏥
### Hospital-Grade RFID Label Design & Encoding Suite

A fast, modern desktop application for labeling, encoding, and managing RFID
assets at scale — built with Python, CustomTkinter, and RFIDIOt.

---

## ✨ Features

| Feature | Description |
|---|---|
| **One-Click Read & Assign** | Place tag → Click Read → EPC auto-fills |
| **Auto-Generated Asset IDs** | `HOSP-EQP-000231` — auto-increment, zero collisions |
| **Batch Mode** | Import CSV → print 500 labels in one click |
| **ZPL / Zebra Printing** | Direct ZPL output — printer encodes + verifies |
| **Label Preview** | Live label preview with QR code embedded |
| **Instant Verify** | Read-back verify after print — ✔ / ✖ mismatch |
| **Duplicate EPC Guard** | Blocks re-use of EPCs at every entry point |
| **Tag Type Profiles** | Plastic / Metal / Large — auto power settings |
| **Door-Ready Status** | READY / BLOCKED / MAINTENANCE per asset |
| **Door Alarm Simulation** | Simulate tag passing door before install |
| **PDF Export** | Multi-label PDF for any printer |
| **CSV Import / Export** | Bulk import, bulk export with filters |
| **Activity Log** | Full timestamped audit trail |
| **SQLite Database** | Local DB at `~/rfid_assets.db` |

---

## 🚀 Quick Start (macOS)

```bash
# 1. Install dependencies
pip3 install customtkinter Pillow "qrcode[pil]" reportlab

# 2. Install RFIDIOt (for real hardware)
pip3 install RFIDIOt
# or from source: https://github.com/AdamLaurie/RFIDIOt

# 3. Run
python3 rfid_manager.py

# Or use the launcher script:
bash launch.sh
```

> **No RFID reader?** The app runs in **Simulation Mode** automatically —
> all features work with randomized fake EPCs for testing.

---

## 📁 File Structure

```
rfid_app/
├── rfid_manager.py     ← Main application
├── requirements.txt    ← Python dependencies
├── launch.sh           ← macOS one-click launcher
└── README.md

~/rfid_assets.db        ← SQLite database (auto-created)
~/rfid_exports/         ← ZPL / PDF / CSV exports (auto-created)
```

---

## 📋 CSV Import Format

```csv
asset_id,name,epc,type,location,department,status,notes
HOSP-EQP-000001,Wheelchair #12,,Standard (Plastic),ICU,Cardiology,PENDING,
HOSP-EQP-000002,IV Pump,,Metal Surface Tag,Ward A,Neurology,PENDING,
```

Leave `asset_id` blank — the app will auto-generate IDs.
Leave `epc` blank — the printer will encode during printing.

---

## 🖨️ Printer Setup (Zebra)

1. Install your Zebra printer driver on macOS
2. Open **Settings** panel in the app
3. Enter the printer name (from System Preferences → Printers)
4. ZPL files are also saved to `~/rfid_exports/` as fallback

**Supported printers:** ZT410, ZT610, ZD620, ZD421, and any ZPL-capable printer

---

## 🏷️ Tag Type Profiles

| Profile | Power | Use Case |
|---|---|---|
| Standard (Plastic) | Medium | Wheelchairs, beds, carts |
| Metal Surface Tag | High | IV pumps, metal equipment |
| Large Equipment | High + Long Range | MRI, large machinery |
| Small Device | Low | Handheld devices, tablets |
| Wristband | Low + Flexible | Patient wristbands |

---

## 🚪 Door Alarm Logic

| Status | Action |
|---|---|
| `READY` | ✔ Allow exit silently |
| `BLOCKED` | 🚨 Trigger alarm immediately |
| `MAINTENANCE` | ⚠ Alert staff + log event |
| `PENDING` | ⚠ Alert staff |

Use the **Door Simulation** panel to test all scenarios before physical install.

---

## ⚡ Speed Comparison

| Method | Time / 100 labels |
|---|---|
| Manual entry | 45–60 min ❌ |
| Semi-auto | 20–30 min |
| **Batch auto (this app)** | **5–8 min ✅** |

---

## 🧰 Tech Stack

| Component | Tool |
|---|---|
| GUI | CustomTkinter |
| RFID Read/Write | RFIDIOt |
| Database | SQLite (local) |
| Label Format | ZPL for Zebra |
| PDF Export | ReportLab |
| QR Codes | qrcode + Pillow |
| Platform | macOS / Windows / Linux |

---

## 📦 Dependencies

```
customtkinter >= 5.2.0
Pillow        >= 10.0.0
qrcode[pil]   >= 7.4
reportlab     >= 4.0.0
RFIDIOt       (optional — enables real hardware)
```

---

*Built for hospital asset management. Designed for speed.*
