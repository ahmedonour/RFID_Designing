"""
RFID Asset Manager — Hospital Grade Label & Encoding Suite
Requires: customtkinter, pillow, qrcode, reportlab
Install:  pip install customtkinter pillow qrcode reportlab
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import sqlite3
import csv
import os
import sys
import time
import threading
import json
import qrcode
import io
import subprocess
import platform
from datetime import datetime
from PIL import Image, ImageTk, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors as rl_colors

# ─── Try importing RFIDIOt (gracefully degrade if not installed) ─────────────
try:
    import RFIDIOt as rfid
    RFID_AVAILABLE = True
except ImportError:
    RFID_AVAILABLE = False

# ─── App-wide constants ───────────────────────────────────────────────────────
APP_TITLE    = "RFID Asset Manager"
APP_VERSION  = "v1.0"
DB_PATH      = os.path.join(os.path.expanduser("~"), "rfid_assets.db")
EXPORT_DIR   = os.path.join(os.path.expanduser("~"), "rfid_exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# Color palette – dark industrial / hospital-grade aesthetic
PALETTE = {
    "bg":          "#0D1117",
    "bg2":         "#161B22",
    "bg3":         "#21262D",
    "border":      "#30363D",
    "accent":      "#00D4AA",
    "accent2":     "#0EA5E9",
    "accent3":     "#F59E0B",
    "danger":      "#EF4444",
    "success":     "#22C55E",
    "warning":     "#F59E0B",
    "text":        "#E6EDF3",
    "text2":       "#8B949E",
    "text3":       "#6E7681",
}

TAG_TYPES = {
    "Standard (Plastic)":   {"power": "medium", "antenna": "standard"},
    "Metal Surface Tag":    {"power": "high",   "antenna": "metal"},
    "Large Equipment":      {"power": "high",   "antenna": "long-range"},
    "Small Device":         {"power": "low",    "antenna": "compact"},
    "Wristband":            {"power": "low",    "antenna": "flexible"},
}

STATUS_COLORS = {
    "READY":       "#22C55E",
    "BLOCKED":     "#EF4444",
    "MAINTENANCE": "#F59E0B",
    "PENDING":     "#8B949E",
}

# ─── Database Layer ────────────────────────────────────────────────────────────
class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id    TEXT UNIQUE NOT NULL,
                epc         TEXT UNIQUE,
                name        TEXT,
                type        TEXT DEFAULT 'Standard (Plastic)',
                location    TEXT,
                department  TEXT,
                status      TEXT DEFAULT 'PENDING',
                notes       TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                printed_at  TEXT,
                verified_at TEXT,
                verified    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scan_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id   TEXT,
                epc        TEXT,
                action     TEXT,
                result     TEXT,
                ts         TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()
        # Seed counter
        self.conn.execute(
            "INSERT OR IGNORE INTO settings VALUES ('id_counter','1')")
        self.conn.execute(
            "INSERT OR IGNORE INTO settings VALUES ('id_prefix','HOSP-EQP')")
        self.conn.commit()

    # ── ID generation ────────────────────────────────────────────────────────
    def next_asset_id(self):
        prefix  = self.get_setting("id_prefix") or "HOSP-EQP"
        counter = int(self.get_setting("id_counter") or 1)
        aid = f"{prefix}-{counter:06d}"
        self.set_setting("id_counter", str(counter + 1))
        return aid

    # ── CRUD ─────────────────────────────────────────────────────────────────
    def insert_asset(self, asset_id, epc=None, name="", atype="Standard (Plastic)",
                     location="", department="", status="PENDING", notes=""):
        try:
            self.conn.execute(
                "INSERT INTO assets (asset_id,epc,name,type,location,department,status,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (asset_id, epc, name, atype, location, department, status, notes))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError as e:
            return str(e)

    def update_asset(self, asset_id, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        sets  = ", ".join(f"{k}=?" for k in kwargs)
        vals  = list(kwargs.values()) + [asset_id]
        self.conn.execute(f"UPDATE assets SET {sets} WHERE asset_id=?", vals)
        self.conn.commit()

    def delete_asset(self, asset_id):
        self.conn.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,))
        self.conn.commit()

    def get_asset(self, asset_id):
        return self.conn.execute(
            "SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()

    def get_asset_by_epc(self, epc):
        return self.conn.execute(
            "SELECT * FROM assets WHERE epc=?", (epc,)).fetchone()

    def epc_exists(self, epc):
        r = self.conn.execute(
            "SELECT 1 FROM assets WHERE epc=?", (epc,)).fetchone()
        return r is not None

    def all_assets(self, search="", status_filter="ALL"):
        q  = "SELECT * FROM assets WHERE 1=1"
        ps = []
        if search:
            q += " AND (asset_id LIKE ? OR name LIKE ? OR epc LIKE ? OR location LIKE ?)"
            ps.extend([f"%{search}%"] * 4)
        if status_filter != "ALL":
            q += " AND status=?"
            ps.append(status_filter)
        q += " ORDER BY id DESC"
        return self.conn.execute(q, ps).fetchall()

    def total_assets(self):
        return self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    def assets_by_status(self):
        r = self.conn.execute(
            "SELECT status, COUNT(*) FROM assets GROUP BY status").fetchall()
        return {row[0]: row[1] for row in r}

    # ── Settings ─────────────────────────────────────────────────────────────
    def get_setting(self, key):
        r = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        self.conn.commit()

    # ── Scan log ─────────────────────────────────────────────────────────────
    def log_scan(self, asset_id, epc, action, result):
        self.conn.execute(
            "INSERT INTO scan_log (asset_id,epc,action,result) VALUES (?,?,?,?)",
            (asset_id, epc, action, result))
        self.conn.commit()

    def recent_scans(self, limit=50):
        return self.conn.execute(
            "SELECT * FROM scan_log ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()


# ─── RFID Hardware Layer ───────────────────────────────────────────────────────
class RFIDReader:
    """Wraps RFIDIOt with simulation fallback."""

    def __init__(self):
        self.connected = False
        self.reader    = None
        self._connect()

    def _connect(self):
        if not RFID_AVAILABLE:
            return
        try:
            self.reader    = rfid.RFIDReader()
            self.connected = True
        except Exception:
            self.connected = False

    def read_epc(self):
        """Returns (epc_hex_str, rssi) or raises RuntimeError."""
        if not self.connected or not RFID_AVAILABLE:
            # Demo simulation
            import random, string
            fake = "E2" + "".join(
                random.choices("0123456789ABCDEF", k=22))
            return fake, -65 + random.randint(-10, 10)
        try:
            tags = self.reader.scan()
            if not tags:
                raise RuntimeError("No tag detected")
            tag  = tags[0]
            return tag.epc, getattr(tag, "rssi", 0)
        except Exception as e:
            raise RuntimeError(str(e))

    def write_epc(self, epc):
        if not self.connected or not RFID_AVAILABLE:
            time.sleep(0.3)   # simulate write delay
            return True
        try:
            self.reader.write_epc(epc)
            return True
        except Exception as e:
            raise RuntimeError(str(e))

    def verify_epc(self, expected_epc):
        epc, rssi = self.read_epc()
        return epc.upper() == expected_epc.upper(), epc, rssi


# ─── Honeywell PC42t/PC42d Printer Driver ─────────────────────────────────────
class HoneywellPrinter:
    """
    Unified print driver for Honeywell PC42t / PC42d.
    Supports:
      • Network (TCP/IP socket) — WiFi or Ethernet
      • USB (macOS lpr / Windows raw / Linux lp)
    Auto-detects ZPL vs IPL by probing the printer's status response.
    """

    PORT_ZPL = 9100   # Honeywell default raw ZPL/IPL port
    TIMEOUT  = 5      # seconds

    # ── Connection modes ──────────────────────────────────────────────────────
    MODE_NETWORK = "network"
    MODE_USB     = "usb"

    # ── IPL label template for PC42t (4×2 inch, 203 dpi → 812×406 dots) ─────
    @staticmethod
    def ipl(asset_id, epc, name="", location="", tag_type="Standard"):
        """Generate IPL (Intermec Printer Language) for Honeywell PC42t/d."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # IPL uses STX/ETX framing; <STX>=\x02, <ETX>=\x03
        label = (
            "\x02"                          # STX — start of label
            "n\r\n"                         # new label
            "M t\r\n"                       # media type: thermal transfer
            "S l1;c15,3\r\n"               # label size 4in x 2in @ 203dpi
            "d PC\r\n"                      # PC42 direct
            # ── Header bar (inverted box) ────────────────────────────────────
            "B 20,10,0,1,2,2,50,B,\"" + (name or asset_id)[:28] + "\"\r\n"
            # ── Body fields ─────────────────────────────────────────────────
            "T 20,70,0,3,1,1,\"Asset ID: " + asset_id + "\"\r\n"
            "T 20,100,0,3,1,1,\"EPC:      " + (epc or "UNASSIGNED") + "\"\r\n"
            "T 20,128,0,3,1,1,\"Type:     " + tag_type + "\"\r\n"
            "T 20,150,0,3,1,1,\"Loc:      " + (location or "—") + "\"\r\n"
            # ── Code 128 barcode ────────────────────────────────────────────
            "B 20,175,0,1A,3,1,60,\"" + asset_id + "\"\r\n"
            # ── Timestamp ───────────────────────────────────────────────────
            "T 20,260,0,3,1,1,\"" + ts + "\"\r\n"
            # ── RFID encode (EPC Gen2) ───────────────────────────────────────
            + ("R 1,E200," + (epc or "0000000000000000000000") + "\r\n"
               if epc else "")
            + "P 1\r\n"                     # print 1 copy
            "\x03"                          # ETX — end of label
        )
        return label

    # ── ZPL label (same layout, ZPL syntax) ──────────────────────────────────
    @staticmethod
    def zpl(asset_id, epc, name="", location="", tag_type="Standard"):
        """ZPL for Honeywell PC42t in ZPL-emulation mode."""
        ts      = datetime.now().strftime("%Y-%m-%d %H:%M")
        qr_data = json.dumps({"id": asset_id, "epc": epc or ""})
        return (
            "^XA\n"
            "^CI28\n"
            "^PW812\n"
            "^LL406\n"
            # Header
            "^FO0,0^GB812,52,52^FS\n"
            f"^FO18,10^A0N,30,30^FR^FD{(name or asset_id)[:30]}^FS\n"
            # Body
            f"^FO18,62^A0N,20,20^FDAsset: {asset_id}^FS\n"
            f"^FO18,90^A0N,18,18^FDEPC: {epc or 'UNASSIGNED'}^FS\n"
            f"^FO18,114^A0N,16,16^FDType: {tag_type}^FS\n"
            f"^FO18,136^A0N,16,16^FDLoc:  {location or '—'}^FS\n"
            # Barcode
            "^FO18,162^BY2,3,55\n"
            "^BCN,55,Y,N,N\n"
            f"^FD{asset_id}^FS\n"
            # QR
            "^FO620,60\n"
            "^BQN,2,4\n"
            f"^FDMA,{qr_data}^FS\n"
            # Timestamp
            f"^FO18,252^A0N,14,14^FD{ts}^FS\n"
            # RFID encode
            + (f"^RFWM,{epc}\n" if epc else "")
            + "^XZ\n"
        )

    # ── Language auto-detection ───────────────────────────────────────────────
    @staticmethod
    def detect_language(host, port=9100, timeout=4):
        """
        Probe printer over TCP to detect ZPL vs IPL.
        Sends a ZPL host-status query (^XA^HH^XZ).
        If we get back a response starting with STX it's IPL, else ZPL.
        Returns 'zpl', 'ipl', or 'unknown'.
        """
        try:
            import socket
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.sendall(b"^XA^HH^XZ\r\n")
                time.sleep(0.6)
                s.settimeout(1.5)
                try:
                    resp = s.recv(256)
                except Exception:
                    resp = b""
            if resp:
                if resp[0:1] == b"\x02":
                    return "ipl"
                if b"^" in resp or b"ZPL" in resp.upper():
                    return "zpl"
                # Honeywell may echo ASCII status
                if resp[0:1] in (b"\x02", b"\x06", b"\x15"):
                    return "ipl"
                return "zpl"          # default assumption
            return "zpl"             # no response → try ZPL
        except Exception:
            return "unknown"

    # ── Network print (TCP socket → port 9100) ────────────────────────────────
    @classmethod
    def print_network(cls, host, data, port=9100, timeout=8):
        """
        Send raw label data to printer via TCP socket.
        Works for both ZPL and IPL.
        Returns (success: bool, message: str).
        """
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                if isinstance(data, str):
                    data = data.encode("latin-1", errors="replace")
                s.sendall(data)
                s.shutdown(socket.SHUT_WR)
                # Brief read to catch any error response
                s.settimeout(1.0)
                try:
                    resp = s.recv(64)
                    # IPL NACK = 0x15
                    if resp and resp[0:1] == b"\x15":
                        return False, "Printer returned NACK (check label format)"
                except Exception:
                    pass
            return True, "OK"
        except ConnectionRefusedError:
            return False, f"Connection refused — is printer online at {host}:{port}?"
        except OSError as e:
            if "timed out" in str(e).lower():
                return False, f"Timeout — printer not responding at {host}:{port}"
            return False, str(e)
        except Exception as e:
            return False, str(e)

    # ── USB print ─────────────────────────────────────────────────────────────
    @classmethod
    def print_usb(cls, data, printer_name=None):
        """
        Send raw data to USB-connected Honeywell printer.
        macOS / Linux: lpr/lp with -oraw flag.
        Windows: win32print RAW spool.
        Returns (success: bool, message: str).
        """
        system = platform.system()
        if isinstance(data, str):
            data = data.encode("latin-1", errors="replace")
        try:
            if system == "Darwin":
                cmd = ["lpr", "-l"]          # -l = pass through raw
                if printer_name:
                    cmd += ["-P", printer_name]
                proc = subprocess.run(cmd, input=data, capture_output=True)
                if proc.returncode != 0:
                    err = proc.stderr.decode().strip()
                    return False, err or "lpr failed (check printer name in Settings)"
                return True, "OK"

            elif system == "Windows":
                try:
                    import win32print
                    pname = printer_name or win32print.GetDefaultPrinter()
                    h = win32print.OpenPrinter(pname)
                    try:
                        job = win32print.StartDocPrinter(h, 1, ("RAW Label", None, "RAW"))
                        win32print.StartPagePrinter(h)
                        win32print.WritePrinter(h, data)
                        win32print.EndPagePrinter(h)
                        win32print.EndDocPrinter(h)
                    finally:
                        win32print.ClosePrinter(h)
                    return True, "OK"
                except ImportError:
                    return False, "win32print not installed — run: pip install pywin32"

            else:  # Linux
                cmd = ["lp", "-o", "raw"]
                if printer_name:
                    cmd += ["-d", printer_name]
                proc = subprocess.run(cmd, input=data, capture_output=True)
                if proc.returncode != 0:
                    return False, proc.stderr.decode().strip()
                return True, "OK"

        except FileNotFoundError:
            return False, "lpr/lp not found — ensure CUPS is installed"
        except Exception as e:
            return False, str(e)

    # ── High-level unified print ──────────────────────────────────────────────
    @classmethod
    def print_label(cls, asset_id, epc, name="", location="", tag_type="Standard",
                    mode=MODE_NETWORK, host=None, port=9100,
                    printer_name=None, language="auto"):
        """
        Top-level method called by the GUI.
        Builds the correct label format, picks the connection method, sends it.
        Returns (success: bool, message: str, language_used: str).
        """
        # ── Detect / choose language ──────────────────────────────────────
        if language == "auto":
            if mode == cls.MODE_NETWORK and host:
                detected = cls.detect_language(host, port)
                lang = "ipl" if detected == "ipl" else "zpl"
            else:
                lang = "zpl"   # USB — default to ZPL; user can override
        else:
            lang = language.lower()

        # ── Build label data ──────────────────────────────────────────────
        if lang == "ipl":
            data = cls.ipl(asset_id, epc, name, location, tag_type)
        else:
            data = cls.zpl(asset_id, epc, name, location, tag_type)

        # ── Send ──────────────────────────────────────────────────────────
        if mode == cls.MODE_NETWORK:
            if not host:
                return False, "No printer IP address configured", lang
            ok, msg = cls.print_network(host, data, port)
        else:
            ok, msg = cls.print_usb(data, printer_name)

        return ok, msg, lang


# ─── ZPL / Label Generator ─────────────────────────────────────────────────────
class LabelGenerator:
    """Produces ZPL strings and PDF previews for Zebra printers."""

    @staticmethod
    def zpl(asset_id, epc, name="", location="", tag_type="Standard",
            dpi=203):
        """Generate ZPL for a 4×2-inch label."""
        barcode_data = asset_id
        qr_data      = json.dumps({"id": asset_id, "epc": epc, "type": tag_type})
        zpl = f"""^XA
^CI28
^PW812
^LL406
^LH0,0

^FO30,20^A0N,28,28^FD{name or asset_id}^FS
^FO30,60^A0N,18,18^FDAsset: {asset_id}^FS
^FO30,90^A0N,18,18^FDEPC: {epc or 'UNASSIGNED'}^FS
^FO30,118^A0N,16,16^FDType: {tag_type}^FS
^FO30,140^A0N,16,16^FDLoc: {location or '—'}^FS

^FO30,175^BY2,3,60
^BCN,60,Y,N,N
^FD{barcode_data}^FS

^FO600,20
^BQN,2,4
^FDMA,{qr_data}^FS

^FO30,260^A0N,14,14^FD{datetime.now().strftime('%Y-%m-%d %H:%M')}^FS
^FO600,260^A0N,14,14^FDverified^FS

^RFWM,{epc or '0000000000000000000000'}
^XZ"""
        return zpl


    @staticmethod
    def preview_image(asset_id, epc, name="", location="",
                      tag_type="Standard", width=600, height=300):
        """Render a label preview as a PIL Image."""
        img  = Image.new("RGB", (width, height), "#FFFFFF")
        draw = ImageDraw.Draw(img)

        # Background
        draw.rectangle([0, 0, width, height], fill="#F8F9FA")
        # Blue header strip
        draw.rectangle([0, 0, width, 50], fill="#0D1117")

        # Header text (use default PIL font for portability)
        draw.text((15, 12), name or asset_id, fill="#00D4AA")
        draw.text((15, 32), f"Asset: {asset_id}", fill="#8B949E")

        # Separator
        draw.line([0, 50, width, 50], fill="#30363D", width=2)

        # Body
        draw.text((15, 65),  f"EPC:      {epc or 'UNASSIGNED'}",   fill="#1E293B")
        draw.text((15, 90),  f"Type:     {tag_type}",               fill="#1E293B")
        draw.text((15, 115), f"Location: {location or '—'}",        fill="#1E293B")
        draw.text((15, 145), f"Printed:  {datetime.now().strftime('%Y-%m-%d %H:%M')}", fill="#64748B")

        # QR code on the right
        try:
            qr_data = json.dumps({"id": asset_id, "epc": epc})
            qr_img  = qrcode.make(qr_data).resize((160, 160))
            img.paste(qr_img, (width - 180, 60))
        except Exception:
            pass

        # Border
        draw.rectangle([2, 2, width-3, height-3], outline="#CBD5E1", width=2)

        return img

    @staticmethod
    def export_pdf(assets, output_path):
        """Export a multi-label PDF."""
        c      = rl_canvas.Canvas(output_path, pagesize=A4)
        pw, ph = A4
        lw, lh = 100*mm, 50*mm
        margin = 10*mm
        cols   = int((pw - margin) / (lw + margin))
        rows   = int((ph - margin) / (lh + margin))
        per    = cols * rows
        idx    = 0
        for asset in assets:
            col = idx % cols
            row = (idx // cols) % rows
            if idx > 0 and idx % per == 0:
                c.showPage()
            x = margin + col * (lw + margin)
            y = ph - margin - (row + 1) * (lh + margin) + margin

            # Label border
            c.setStrokeColor(rl_colors.HexColor("#CBD5E1"))
            c.rect(x, y, lw, lh)

            # Header
            c.setFillColor(rl_colors.HexColor("#0D1117"))
            c.rect(x, y + lh - 14*mm, lw, 14*mm, fill=1, stroke=0)
            c.setFillColor(rl_colors.HexColor("#00D4AA"))
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x + 3*mm, y + lh - 9*mm,
                         (asset["name"] or asset["asset_id"])[:30])

            c.setFillColor(rl_colors.black)
            c.setFont("Helvetica", 7)
            c.drawString(x+3*mm, y+lh-14*mm+1*mm, f"ID: {asset['asset_id']}")

            # Body
            c.setFont("Helvetica", 7)
            c.setFillColor(rl_colors.HexColor("#1E293B"))
            lines = [
                f"EPC: {asset['epc'] or 'UNASSIGNED'}",
                f"Type: {asset['type']}",
                f"Loc:  {asset['location'] or '—'}",
                f"Dept: {asset['department'] or '—'}",
            ]
            for i, line in enumerate(lines):
                c.drawString(x + 3*mm, y + lh - 18*mm - i*6*mm, line)

            # Status badge
            sc = STATUS_COLORS.get(asset["status"], "#8B949E")
            c.setFillColor(rl_colors.HexColor(sc))
            c.roundRect(x + lw - 22*mm, y + 3*mm, 20*mm, 7*mm, 2*mm, fill=1, stroke=0)
            c.setFillColor(rl_colors.white)
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(x + lw - 12*mm, y + 5.5*mm, asset["status"])

            # QR
            try:
                qr_data = json.dumps({"id": asset["asset_id"], "epc": asset["epc"]})
                qr_img  = qrcode.make(qr_data).resize((80, 80))
                buf = io.BytesIO()
                qr_img.save(buf, "PNG")
                buf.seek(0)
                from reportlab.lib.utils import ImageReader
                c.drawImage(ImageReader(buf), x + lw - 25*mm, y + 12*mm,
                            22*mm, 22*mm)
            except Exception:
                pass

            idx += 1

        c.save()


# ─── Custom Widgets ────────────────────────────────────────────────────────────
class StatusBadge(ctk.CTkLabel):
    def __init__(self, master, status="PENDING", **kw):
        color = STATUS_COLORS.get(status, "#8B949E")
        super().__init__(master,
            text=f"  {status}  ",
            fg_color=color,
            text_color="white",
            corner_radius=4,
            font=ctk.CTkFont("Courier New", 11, "bold"),
            **kw)

class SectionHeader(ctk.CTkFrame):
    def __init__(self, master, title, subtitle="", icon="", **kw):
        super().__init__(master, fg_color="transparent", **kw)
        ctk.CTkLabel(self, text=f"{icon}  {title}",
            font=ctk.CTkFont("Courier New", 16, "bold"),
            text_color=PALETTE["accent"]).pack(side="left")
        if subtitle:
            ctk.CTkLabel(self, text=subtitle,
                font=ctk.CTkFont("Courier New", 11),
                text_color=PALETTE["text2"]).pack(side="left", padx=12)

class LogBox(ctk.CTkTextbox):
    """Auto-scrolling log textbox."""
    def __init__(self, master, **kw):
        super().__init__(master,
            font=ctk.CTkFont("Courier New", 11),
            fg_color=PALETTE["bg"],
            text_color=PALETTE["text2"],
            wrap="word", **kw)
        self.configure(state="disabled")

    def log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {"info": PALETTE["text2"], "ok": PALETTE["success"],
                  "warn": PALETTE["warning"], "error": PALETTE["danger"]}
        prefix = {"info": "●", "ok": "✔", "warn": "⚠", "error": "✖"}
        line   = f"[{ts}] {prefix.get(level,'●')} {msg}\n"
        self.configure(state="normal")
        self.insert("end", line)
        self.see("end")
        self.configure(state="disabled")


# ─── Panels ────────────────────────────────────────────────────────────────────
class DashboardPanel(ctk.CTkFrame):
    def __init__(self, master, db, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db = db
        self._build()

    def _stat_card(self, parent, label, value, color, col):
        card = ctk.CTkFrame(parent, fg_color=PALETTE["bg2"],
                            border_color=PALETTE["border"], border_width=1,
                            corner_radius=8)
        card.grid(row=0, column=col, padx=8, pady=8, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)
        ctk.CTkLabel(card, text=str(value),
                     font=ctk.CTkFont("Courier New", 32, "bold"),
                     text_color=color).pack(pady=(16, 4))
        ctk.CTkLabel(card, text=label,
                     font=ctk.CTkFont("Courier New", 11),
                     text_color=PALETTE["text2"]).pack(pady=(0, 16))

    def _build(self):
        SectionHeader(self, "Dashboard", "Asset overview", "◈").pack(
            fill="x", padx=20, pady=(20, 10))

        stats_row = ctk.CTkFrame(self, fg_color="transparent")
        stats_row.pack(fill="x", padx=20)

        by_status = self.db.assets_by_status()
        total     = self.db.total_assets()
        self._stat_card(stats_row, "Total Assets",   total,
                        PALETTE["accent"],  0)
        self._stat_card(stats_row, "Ready",           by_status.get("READY", 0),
                        PALETTE["success"], 1)
        self._stat_card(stats_row, "Pending",         by_status.get("PENDING", 0),
                        PALETTE["text2"],   2)
        self._stat_card(stats_row, "Blocked",         by_status.get("BLOCKED", 0),
                        PALETTE["danger"],  3)
        self._stat_card(stats_row, "Maintenance",     by_status.get("MAINTENANCE", 0),
                        PALETTE["warning"], 4)

        # Recent assets table
        SectionHeader(self, "Recent Assets", "Last 10 added", "⊞").pack(
            fill="x", padx=20, pady=(20, 6))

        cols = ("asset_id", "name", "epc", "type", "location", "status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=10)
        for col in cols:
            self.tree.heading(col, text=col.replace("_", " ").title())
            self.tree.column(col, width=130)
        self.tree.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
            background=PALETTE["bg2"], foreground=PALETTE["text"],
            fieldbackground=PALETTE["bg2"], rowheight=26,
            font=("Courier New", 10))
        style.configure("Treeview.Heading",
            background=PALETTE["bg3"], foreground=PALETTE["accent"],
            font=("Courier New", 10, "bold"))
        style.map("Treeview", background=[("selected", PALETTE["accent"])])

        self._refresh()

    def _refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for a in self.db.all_assets()[:10]:
            self.tree.insert("", "end",
                values=(a["asset_id"], a["name"] or "—", a["epc"] or "—",
                        a["type"], a["location"] or "—", a["status"]))


class ReadEncodePanel(ctk.CTkFrame):
    """One-Click Read & Assign panel."""
    def __init__(self, master, db, reader, log_box, refresh_cb, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db         = db
        self.reader     = reader
        self.log        = log_box
        self.refresh_cb = refresh_cb
        self._build()

    def _build(self):
        SectionHeader(self, "Read & Encode",
                      "One-click RFID assignment", "⊕").pack(
            fill="x", padx=20, pady=(20, 10))

        body = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                            border_color=PALETTE["border"], border_width=1,
                            corner_radius=10)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        body.grid_columnconfigure((0, 1), weight=1)

        # ── Left column: form ─────────────────────────────────────────────
        lf = ctk.CTkFrame(body, fg_color="transparent")
        lf.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        def lbl(text):
            return ctk.CTkLabel(lf, text=text,
                font=ctk.CTkFont("Courier New", 11),
                text_color=PALETTE["text2"], anchor="w")

        def entry(var, placeholder=""):
            return ctk.CTkEntry(lf, textvariable=var,
                placeholder_text=placeholder,
                font=ctk.CTkFont("Courier New", 12),
                fg_color=PALETTE["bg3"],
                border_color=PALETTE["border"],
                text_color=PALETTE["text"],
                height=36)

        self.v_asset_id  = tk.StringVar()
        self.v_epc       = tk.StringVar()
        self.v_name      = tk.StringVar()
        self.v_location  = tk.StringVar()
        self.v_dept      = tk.StringVar()
        self.v_status    = tk.StringVar(value="READY")
        self.v_notes     = tk.StringVar()
        self.v_tag_type  = tk.StringVar(value="Standard (Plastic)")

        # Auto-generate ID
        new_id = self.db.next_asset_id()
        self.db.set_setting("id_counter",
            str(int(self.db.get_setting("id_counter") or 2) - 1))  # peek only
        self.v_asset_id.set(new_id)

        row = 0
        for ltext, var, ph in [
            ("Asset ID",   self.v_asset_id,  "Auto-generated"),
            ("EPC (Hex)",  self.v_epc,       "Place tag and click Read"),
            ("Name/Label", self.v_name,      "e.g. Wheelchair #12"),
            ("Location",   self.v_location,  "e.g. ICU Ward 3"),
            ("Department", self.v_dept,      "e.g. Cardiology"),
            ("Notes",      self.v_notes,     "Optional notes"),
        ]:
            lbl(ltext).grid(row=row, column=0, sticky="w", pady=(6, 2))
            e = entry(var, ph)
            e.grid(row=row+1, column=0, sticky="ew", pady=(0, 4))
            lf.grid_columnconfigure(0, weight=1)
            row += 2

        lbl("Tag Type").grid(row=row, column=0, sticky="w", pady=(6, 2))
        ctk.CTkOptionMenu(lf, values=list(TAG_TYPES.keys()),
            variable=self.v_tag_type,
            fg_color=PALETTE["bg3"],
            button_color=PALETTE["accent"],
            dropdown_fg_color=PALETTE["bg2"],
            font=ctk.CTkFont("Courier New", 12)).grid(
            row=row+1, column=0, sticky="ew")
        row += 2

        lbl("Status").grid(row=row, column=0, sticky="w", pady=(6, 2))
        ctk.CTkOptionMenu(lf, values=["READY", "BLOCKED", "MAINTENANCE", "PENDING"],
            variable=self.v_status,
            fg_color=PALETTE["bg3"],
            button_color=PALETTE["accent2"],
            dropdown_fg_color=PALETTE["bg2"],
            font=ctk.CTkFont("Courier New", 12)).grid(
            row=row+1, column=0, sticky="ew")

        # ── Right column: controls + preview ─────────────────────────────
        rf = ctk.CTkFrame(body, fg_color="transparent")
        rf.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        rf.grid_columnconfigure(0, weight=1)

        btn_cfg = dict(
            font=ctk.CTkFont("Courier New", 13, "bold"),
            height=44, corner_radius=6)

        ctk.CTkButton(rf, text="⊙  Read RFID Tag",
            fg_color=PALETTE["accent2"], hover_color="#0284C7",
            command=self._read_tag, **btn_cfg).grid(
            row=0, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkButton(rf, text="⊕  Generate Asset ID",
            fg_color=PALETTE["bg3"], hover_color=PALETTE["border"],
            command=self._gen_id, **btn_cfg).grid(
            row=1, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkButton(rf, text="▷  Save Asset",
            fg_color=PALETTE["accent"], hover_color="#00B891",
            text_color="#0D1117", command=self._save, **btn_cfg).grid(
            row=2, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkButton(rf, text="⊠  Print Label",
            fg_color="#7C3AED", hover_color="#6D28D9",
            command=self._print_label, **btn_cfg).grid(
            row=3, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkButton(rf, text="✔  Verify Tag",
            fg_color=PALETTE["success"], hover_color="#16A34A",
            text_color="#0D1117", command=self._verify, **btn_cfg).grid(
            row=4, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkButton(rf, text="⟲  Clear Form",
            fg_color=PALETTE["bg3"], hover_color=PALETTE["border"],
            command=self._clear, **btn_cfg).grid(
            row=5, column=0, sticky="ew", pady=(0, 24))

        # Label preview
        ctk.CTkLabel(rf, text="Label Preview",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"]).grid(row=6, column=0, sticky="w")
        self.preview_label = ctk.CTkLabel(rf, text="", image=None)
        self.preview_label.grid(row=7, column=0, pady=(4, 0))
        self._update_preview()

        # Live-update preview on changes
        for v in [self.v_asset_id, self.v_epc, self.v_name,
                  self.v_location, self.v_tag_type]:
            v.trace_add("write", lambda *_: self._update_preview())

        # Status indicator
        hw = "🟢 Reader Connected" if RFID_AVAILABLE else "🟡 Simulation Mode"
        ctk.CTkLabel(rf, text=hw,
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text3"]).grid(row=8, column=0, pady=(8, 0))

    # ── Actions ───────────────────────────────────────────────────────────────
    def _read_tag(self):
        def _worker():
            try:
                self.log.log("Scanning for RFID tag…", "info")
                epc, rssi = self.reader.read_epc()
                if self.db.epc_exists(epc):
                    self.log.log(f"⚠ Duplicate EPC detected: {epc}", "warn")
                    self.v_epc.set(epc)
                    return
                self.v_epc.set(epc)
                self.log.log(f"Tag read OK — EPC: {epc}  RSSI: {rssi}dBm", "ok")
            except RuntimeError as e:
                self.log.log(f"Read failed: {e}", "error")
        threading.Thread(target=_worker, daemon=True).start()

    def _gen_id(self):
        new_id = self.db.next_asset_id()
        self.v_asset_id.set(new_id)
        self.log.log(f"Generated ID: {new_id}", "info")

    def _save(self):
        aid = self.v_asset_id.get().strip()
        epc = self.v_epc.get().strip()
        if not aid:
            self.log.log("Asset ID is required", "error"); return
        if epc and self.db.epc_exists(epc):
            existing = self.db.get_asset_by_epc(epc)
            if existing and existing["asset_id"] != aid:
                self.log.log(f"Duplicate EPC blocked: {epc}", "error"); return

        result = self.db.insert_asset(
            asset_id   = aid,
            epc        = epc or None,
            name       = self.v_name.get().strip(),
            atype      = self.v_tag_type.get(),
            location   = self.v_location.get().strip(),
            department = self.v_dept.get().strip(),
            status     = self.v_status.get(),
            notes      = self.v_notes.get().strip(),
        )
        if result is True:
            self.log.log(f"Asset saved: {aid}", "ok")
            self.db.log_scan(aid, epc, "SAVE", "OK")
            self.refresh_cb()
            self._gen_id()  # auto-advance ID
        else:
            self.log.log(f"Save failed: {result}", "error")

    def _print_label(self):
        aid = self.v_asset_id.get().strip()
        epc = self.v_epc.get().strip()
        if not aid:
            self.log.log("Enter Asset ID first", "error"); return

        mode      = self.db.get_setting("print_mode") or "network"
        host      = self.db.get_setting("printer_ip") or ""
        port      = int(self.db.get_setting("printer_port") or 9100)
        usbname   = self.db.get_setting("printer_usb_name") or None
        lang      = self.db.get_setting("printer_lang") or "auto"

        def _worker():
            self.log.log(f"Sending label to Honeywell PC42t ({mode})…", "info")
            ok, msg, lang_used = HoneywellPrinter.print_label(
                asset_id     = aid,
                epc          = epc or None,
                name         = self.v_name.get(),
                location     = self.v_location.get(),
                tag_type     = self.v_tag_type.get().split("(")[0].strip(),
                mode         = mode,
                host         = host,
                port         = port,
                printer_name = usbname,
                language     = lang,
            )
            if ok:
                self.log.log(
                    f"✔ Label printed OK — {aid}  [{lang_used.upper()}]", "ok")
                self.db.update_asset(aid, printed_at=datetime.now().isoformat())
            else:
                self.log.log(f"✖ Printer error: {msg}", "error")

            # Always save ZPL/IPL to file as backup
            export_dir = self.db.get_setting("export_dir") or EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)
            ext      = "ipl" if (lang == "ipl") else "zpl"
            lbl_path = os.path.join(export_dir, f"{aid}.{ext}")
            label_data = (HoneywellPrinter.ipl if ext == "ipl"
                          else HoneywellPrinter.zpl)(
                aid, epc, self.v_name.get(),
                self.v_location.get(),
                self.v_tag_type.get().split("(")[0].strip())
            with open(lbl_path, "w") as f:
                f.write(label_data)
            self.log.log(f"Label file saved: {lbl_path}", "info")

        threading.Thread(target=_worker, daemon=True).start()

    def _verify(self):
        epc = self.v_epc.get().strip()
        aid = self.v_asset_id.get().strip()
        if not epc:
            self.log.log("No EPC to verify — read tag first", "warn"); return

        def _worker():
            try:
                match, read_epc, rssi = self.reader.verify_epc(epc)
                if match:
                    self.log.log(
                        f"✔ Verify OK — {aid}  EPC matches  RSSI:{rssi}dBm", "ok")
                    self.db.update_asset(
                        aid, verified=1,
                        verified_at=datetime.now().isoformat(), status="READY")
                    self.db.log_scan(aid, epc, "VERIFY", "MATCH")
                    self.refresh_cb()
                else:
                    self.log.log(
                        f"✖ Mismatch — expected {epc}, read {read_epc}", "error")
                    self.db.log_scan(aid, epc, "VERIFY", f"MISMATCH:{read_epc}")
            except RuntimeError as e:
                self.log.log(f"Verify error: {e}", "error")
        threading.Thread(target=_worker, daemon=True).start()

    def _update_preview(self):
        try:
            img = LabelGenerator.preview_image(
                self.v_asset_id.get() or "HOSP-EQP-000001",
                self.v_epc.get()     or "E200...",
                self.v_name.get(),
                self.v_location.get(),
                self.v_tag_type.get().split("(")[0].strip(),
                width=340, height=170)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                   size=(340, 170))
            self.preview_label.configure(image=ctk_img)
            self.preview_label.image = ctk_img
        except Exception:
            pass

    def _clear(self):
        for v in [self.v_epc, self.v_name, self.v_location,
                  self.v_dept, self.v_notes]:
            v.set("")
        self.v_status.set("READY")
        self.v_tag_type.set("Standard (Plastic)")
        self._gen_id()
        self.log.log("Form cleared", "info")


class BatchPanel(ctk.CTkFrame):
    """Batch import, print, and encode."""
    def __init__(self, master, db, reader, log_box, refresh_cb, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db         = db
        self.reader     = reader
        self.log        = log_box
        self.refresh_cb = refresh_cb
        self._rows      = []
        self._build()

    def _build(self):
        SectionHeader(self, "Batch Operations",
                      "Import → Print → Encode 50–500 labels", "⊞").pack(
            fill="x", padx=20, pady=(20, 10))

        # Top controls
        ctrl = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                            border_color=PALETTE["border"], border_width=1,
                            corner_radius=8)
        ctrl.pack(fill="x", padx=20, pady=(0, 10))
        ctrl.grid_columnconfigure((0,1,2,3,4,5), weight=1)

        btn = dict(height=36, corner_radius=6,
                   font=ctk.CTkFont("Courier New", 12, "bold"))

        ctk.CTkButton(ctrl, text="📂 Import CSV",
            fg_color=PALETTE["accent2"], command=self._import_csv, **btn
        ).grid(row=0, column=0, padx=8, pady=10, sticky="ew")

        ctk.CTkButton(ctrl, text="📋 Template CSV",
            fg_color=PALETTE["bg3"], command=self._save_template, **btn
        ).grid(row=0, column=1, padx=8, pady=10, sticky="ew")

        ctk.CTkButton(ctrl, text="⚡ Auto-Generate IDs",
            fg_color=PALETTE["bg3"], command=self._auto_ids, **btn
        ).grid(row=0, column=2, padx=8, pady=10, sticky="ew")

        ctk.CTkButton(ctrl, text="🖨 Batch Print",
            fg_color="#7C3AED", command=self._batch_print, **btn
        ).grid(row=0, column=3, padx=8, pady=10, sticky="ew")

        ctk.CTkButton(ctrl, text="📄 Export PDF",
            fg_color=PALETTE["accent3"], text_color="#0D1117",
            command=self._export_pdf, **btn
        ).grid(row=0, column=4, padx=8, pady=10, sticky="ew")

        ctk.CTkButton(ctrl, text="💾 Save to DB",
            fg_color=PALETTE["success"], text_color="#0D1117",
            command=self._save_all, **btn
        ).grid(row=0, column=5, padx=8, pady=10, sticky="ew")

        # Progress
        self.progress_var = tk.DoubleVar()
        self.progress_lbl = ctk.CTkLabel(ctrl, text="Ready",
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text2"])
        self.progress_lbl.grid(row=1, column=0, columnspan=3, sticky="w", padx=8)
        self.progress_bar = ctk.CTkProgressBar(ctrl,
            variable=self.progress_var,
            fg_color=PALETTE["bg3"],
            progress_color=PALETTE["accent"])
        self.progress_bar.grid(row=1, column=0, columnspan=6,
                                padx=8, pady=(0, 10), sticky="ew")

        # Table
        cols = ("asset_id","name","epc","type","location","department","status")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for col in cols:
            w = 160 if col in ("asset_id","epc","name") else 110
            self.tree.heading(col, text=col.replace("_"," ").title())
            self.tree.column(col, width=w, anchor="w")

        sb = ttk.Scrollbar(self, orient="vertical",
                           command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True,
                       padx=(20, 0), pady=(0, 20))
        sb.pack(side="left", fill="y", pady=(0, 20))

        style = ttk.Style()
        style.configure("Treeview",
            background=PALETTE["bg2"], foreground=PALETTE["text"],
            fieldbackground=PALETTE["bg2"], rowheight=24,
            font=("Courier New", 10))
        style.configure("Treeview.Heading",
            background=PALETTE["bg3"], foreground=PALETTE["accent"],
            font=("Courier New", 10, "bold"))

    # ── Actions ──────────────────────────────────────────────────────────────
    def _import_csv(self):
        path = filedialog.askopenfilename(
            title="Import CSV", filetypes=[("CSV files","*.csv"),("All","*.*")])
        if not path:
            return
        self._rows.clear()
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self._rows.append(dict(row))
        self._refresh_table()
        self.log.log(f"Imported {len(self._rows)} rows from {os.path.basename(path)}", "ok")

    def _save_template(self):
        path = filedialog.asksaveasfilename(
            title="Save CSV Template", defaultextension=".csv",
            filetypes=[("CSV","*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "asset_id","name","epc","type","location","department","status","notes"])
            w.writeheader()
            for i in range(1, 4):
                w.writerow({
                    "asset_id": f"HOSP-EQP-{i:06d}",
                    "name": f"Sample Device {i}",
                    "epc": "",
                    "type": "Standard (Plastic)",
                    "location": "Ward A",
                    "department": "ICU",
                    "status": "PENDING",
                    "notes": "",
                })
        self.log.log(f"Template saved: {path}", "ok")

    def _auto_ids(self):
        for row in self._rows:
            if not row.get("asset_id"):
                row["asset_id"] = self.db.next_asset_id()
        self._refresh_table()
        self.log.log("Auto-generated missing Asset IDs", "ok")

    def _batch_print(self):
        if not self._rows:
            self.log.log("No rows loaded — import CSV first", "warn"); return

        mode     = self.db.get_setting("print_mode") or "network"
        host     = self.db.get_setting("printer_ip") or ""
        port_str = self.db.get_setting("printer_port") or "9100"
        usbname  = self.db.get_setting("printer_usb_name") or None
        lang     = self.db.get_setting("printer_lang") or "auto"
        try:
            port = int(port_str)
        except ValueError:
            port = 9100

        def _worker():
            total   = len(self._rows)
            success = skipped = errors = 0

            # Detect language once for network mode to avoid per-label probing
            effective_lang = lang
            if lang == "auto" and mode == "network" and host:
                self.log.log("Probing printer language…", "info")
                detected = HoneywellPrinter.detect_language(host, port)
                effective_lang = "ipl" if detected == "ipl" else "zpl"
                self.log.log(
                    f"Printer language: {effective_lang.upper()}", "ok")

            export_dir = self.db.get_setting("export_dir") or EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)

            for i, row in enumerate(self._rows):
                aid  = row.get("asset_id", "").strip()
                epc  = row.get("epc", "").strip() or None
                name = row.get("name", "")
                loc  = row.get("location", "")
                typ  = row.get("type", "Standard (Plastic)")

                if not aid:
                    skipped += 1
                    continue

                ok, msg, lang_used = HoneywellPrinter.print_label(
                    asset_id     = aid,
                    epc          = epc,
                    name         = name,
                    location     = loc,
                    tag_type     = typ.split("(")[0].strip(),
                    mode         = mode,
                    host         = host,
                    port         = port,
                    printer_name = usbname,
                    language     = effective_lang,
                )

                if ok:
                    success += 1
                    row["_printed"] = True
                    # Save label file
                    ext  = "ipl" if lang_used == "ipl" else "zpl"
                    path = os.path.join(export_dir, f"{aid}.{ext}")
                    data = (HoneywellPrinter.ipl if ext == "ipl"
                            else HoneywellPrinter.zpl)(
                        aid, epc, name, loc,
                        typ.split("(")[0].strip())
                    with open(path, "w") as fh:
                        fh.write(data)
                else:
                    errors += 1
                    row["_error"] = True
                    self.log.log(f"Print failed [{aid}]: {msg}", "warn")

                pct = (i + 1) / total
                self.progress_var.set(pct)
                self.progress_lbl.configure(
                    text=f"Printing {i+1}/{total} — {aid}")

            self.progress_lbl.configure(
                text=(f"Done: {success} printed"
                      + (f", {errors} failed" if errors else "")
                      + (f", {skipped} skipped" if skipped else "")))
            self.log.log(
                f"Batch print complete — {success} OK, "
                f"{errors} failed, {skipped} skipped", "ok")
            self._refresh_table()

        threading.Thread(target=_worker, daemon=True).start()

    def _export_pdf(self):
        if not self._rows:
            self.log.log("No rows to export", "warn"); return
        path = filedialog.asksaveasfilename(
            title="Export PDF", defaultextension=".pdf",
            initialdir=EXPORT_DIR,
            filetypes=[("PDF","*.pdf")])
        if not path:
            return
        # Convert rows to dict-like objects
        class RowProxy:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d.get(k,"")
        proxies = [RowProxy(r) for r in self._rows]
        try:
            LabelGenerator.export_pdf(proxies, path)
            self.log.log(f"PDF exported: {path}", "ok")
            if platform.system() == "Darwin":
                subprocess.run(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)
        except Exception as e:
            self.log.log(f"PDF export failed: {e}", "error")

    def _save_all(self):
        saved = skipped = 0
        for row in self._rows:
            aid = row.get("asset_id","").strip()
            epc = row.get("epc","").strip() or None
            if not aid:
                skipped += 1; continue
            if epc and self.db.epc_exists(epc):
                self.log.log(f"Duplicate EPC skipped: {epc}", "warn")
                skipped += 1; continue
            result = self.db.insert_asset(
                asset_id   = aid,
                epc        = epc,
                name       = row.get("name",""),
                atype      = row.get("type","Standard (Plastic)"),
                location   = row.get("location",""),
                department = row.get("department",""),
                status     = row.get("status","PENDING"),
                notes      = row.get("notes",""),
            )
            if result is True:
                saved += 1
            else:
                skipped += 1
        self.log.log(f"Batch save: {saved} saved, {skipped} skipped", "ok")
        self.refresh_cb()

    def _refresh_table(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        for row in self._rows:
            tag = "error" if row.get("_error") else \
                  "ok"    if row.get("_printed") else ""
            self.tree.insert("", "end", tags=(tag,),
                values=(
                    row.get("asset_id",""),
                    row.get("name",""),
                    row.get("epc",""),
                    row.get("type",""),
                    row.get("location",""),
                    row.get("department",""),
                    row.get("status",""),
                ))
        self.tree.tag_configure("ok",    background="#14532D")
        self.tree.tag_configure("error", background="#450A0A")


class AssetsPanel(ctk.CTkFrame):
    """Full asset list with search, filter, edit, delete."""
    def __init__(self, master, db, log_box, refresh_cb, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db         = db
        self.log        = log_box
        self.refresh_cb = refresh_cb
        self._build()

    def _build(self):
        SectionHeader(self, "Asset Database",
                      "Search, filter and manage all assets", "⊟").pack(
            fill="x", padx=20, pady=(20, 10))

        # Controls bar
        bar = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                           border_color=PALETTE["border"], border_width=1,
                           corner_radius=8)
        bar.pack(fill="x", padx=20, pady=(0, 10))
        bar.grid_columnconfigure(1, weight=1)

        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *_: self._refresh())
        ctk.CTkEntry(bar, textvariable=self.v_search,
            placeholder_text="🔍 Search asset ID, name, EPC, location…",
            font=ctk.CTkFont("Courier New", 12),
            fg_color=PALETTE["bg3"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            height=36).grid(row=0, column=1, padx=10, pady=8, sticky="ew")

        self.v_filter = tk.StringVar(value="ALL")
        ctk.CTkOptionMenu(bar,
            values=["ALL","READY","BLOCKED","MAINTENANCE","PENDING"],
            variable=self.v_filter,
            fg_color=PALETTE["bg3"],
            button_color=PALETTE["accent"],
            dropdown_fg_color=PALETTE["bg2"],
            font=ctk.CTkFont("Courier New", 12),
            command=lambda _: self._refresh(),
            width=150).grid(row=0, column=2, padx=6, pady=8)

        ctk.CTkButton(bar, text="🗑 Delete Selected",
            fg_color=PALETTE["danger"], hover_color="#B91C1C",
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=36, command=self._delete_selected).grid(
            row=0, column=3, padx=6, pady=8)

        ctk.CTkButton(bar, text="📥 Export CSV",
            fg_color=PALETTE["bg3"],
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=36, command=self._export_csv).grid(
            row=0, column=4, padx=6, pady=8)

        # Table
        cols = ("asset_id","name","epc","type","location","department",
                "status","verified","created_at")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        widths = dict(asset_id=150,name=150,epc=200,type=140,location=100,
                      department=110,status=90,verified=70,created_at=140)
        for col in cols:
            self.tree.heading(col, text=col.replace("_"," ").title(),
                command=lambda c=col: self._sort(c))
            self.tree.column(col, width=widths.get(col,100), anchor="w")

        xsb = ttk.Scrollbar(self, orient="horizontal",
                             command=self.tree.xview)
        ysb = ttk.Scrollbar(self, orient="vertical",
                             command=self.tree.yview)
        self.tree.configure(xscrollcommand=xsb.set,
                            yscrollcommand=ysb.set)

        self.tree.pack(side="left", fill="both", expand=True,
                       padx=(20, 0), pady=(0, 2))
        ysb.pack(side="left", fill="y", pady=(0, 2))
        xsb.pack(fill="x", padx=20)

        self._sort_col = "id"; self._sort_rev = True
        self._refresh()

    def _refresh(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        for a in self.db.all_assets(self.v_search.get(),
                                     self.v_filter.get()):
            self.tree.insert("", "end",
                tags=(a["status"],),
                values=(
                    a["asset_id"], a["name"] or "—",
                    a["epc"] or "—", a["type"],
                    a["location"] or "—", a["department"] or "—",
                    a["status"],
                    "✔" if a["verified"] else "✖",
                    a["created_at"][:16] if a["created_at"] else "—",
                ))
        for s, c in STATUS_COLORS.items():
            self.tree.tag_configure(s, foreground=c)

    def _sort(self, col):
        self._sort_rev = not self._sort_rev if self._sort_col == col else False
        self._sort_col = col
        data = [(self.tree.set(r, col), r) for r in self.tree.get_children()]
        data.sort(reverse=self._sort_rev)
        for i, (_, r) in enumerate(data):
            self.tree.move(r, "", i)

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("Confirm",
                f"Delete {len(sel)} asset(s)?"):
            return
        for item in sel:
            aid = self.tree.item(item)["values"][0]
            self.db.delete_asset(aid)
            self.log.log(f"Deleted: {aid}", "warn")
        self._refresh()
        self.refresh_cb()

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            title="Export CSV", defaultextension=".csv",
            initialdir=EXPORT_DIR,
            filetypes=[("CSV","*.csv")])
        if not path:
            return
        assets = self.db.all_assets(self.v_search.get(),
                                     self.v_filter.get())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["asset_id","name","epc","type","location",
                             "department","status","verified","created_at"])
            for a in assets:
                writer.writerow([a["asset_id"], a["name"], a["epc"],
                                  a["type"], a["location"], a["department"],
                                  a["status"], a["verified"], a["created_at"]])
        self.log.log(f"CSV exported: {path}", "ok")


class SimulatePanel(ctk.CTkFrame):
    """Door-exit alarm simulation."""
    def __init__(self, master, db, reader, log_box, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db     = db
        self.reader = reader
        self.log    = log_box
        self._sim_running = False
        self._build()

    def _build(self):
        SectionHeader(self, "Door Alarm Simulation",
                      "Test RFID exit detection before installation", "⊘").pack(
            fill="x", padx=20, pady=(20, 10))

        body = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                            border_color=PALETTE["border"], border_width=1,
                            corner_radius=10)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        body.grid_columnconfigure((0,1), weight=1)

        # ── Left: door panel ─────────────────────────────────────────────
        lf = ctk.CTkFrame(body, fg_color="transparent")
        lf.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        ctk.CTkLabel(lf, text="Door Zone",
            font=ctk.CTkFont("Courier New", 13, "bold"),
            text_color=PALETTE["text2"]).pack(pady=(0, 8))

        self.door_frame = ctk.CTkFrame(lf, fg_color=PALETTE["bg3"],
                                        corner_radius=12,
                                        border_color=PALETTE["border"],
                                        border_width=2,
                                        width=200, height=280)
        self.door_frame.pack(pady=8)

        self.door_icon = ctk.CTkLabel(self.door_frame, text="🚪",
            font=ctk.CTkFont(size=64))
        self.door_icon.place(relx=0.5, rely=0.3, anchor="center")

        self.door_status = ctk.CTkLabel(self.door_frame, text="CLEAR",
            font=ctk.CTkFont("Courier New", 16, "bold"),
            text_color=PALETTE["success"])
        self.door_status.place(relx=0.5, rely=0.65, anchor="center")

        self.door_detail = ctk.CTkLabel(self.door_frame, text="No tag detected",
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text3"])
        self.door_detail.place(relx=0.5, rely=0.8, anchor="center")

        ctk.CTkButton(lf, text="▷  Simulate Tag Pass",
            fg_color=PALETTE["accent2"],
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=40, command=self._simulate_pass).pack(fill="x", pady=8)

        ctk.CTkButton(lf, text="⟳  Start Continuous Scan",
            fg_color=PALETTE["bg3"],
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=40, command=self._toggle_continuous).pack(fill="x")

        self.v_continuous = ctk.CTkLabel(lf, text="● Idle",
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text3"])
        self.v_continuous.pack(pady=6)

        # ── Right: alarm log + rules ──────────────────────────────────────
        rf = ctk.CTkFrame(body, fg_color="transparent")
        rf.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

        ctk.CTkLabel(rf, text="Alarm Rules",
            font=ctk.CTkFont("Courier New", 13, "bold"),
            text_color=PALETTE["text2"]).pack(anchor="w", pady=(0, 8))

        rules = [
            ("READY",       PALETTE["success"], "✔ Allow exit"),
            ("BLOCKED",     PALETTE["danger"],  "🚨 TRIGGER ALARM"),
            ("MAINTENANCE", PALETTE["warning"], "⚠ Alert + Log"),
            ("PENDING",     PALETTE["text3"],   "⚠ Alert staff"),
        ]
        for status, color, action in rules:
            row_frame = ctk.CTkFrame(rf, fg_color=PALETTE["bg3"],
                                     corner_radius=6)
            row_frame.pack(fill="x", pady=3)
            ctk.CTkLabel(row_frame, text=f"  {status}",
                font=ctk.CTkFont("Courier New", 11, "bold"),
                text_color=color, width=110, anchor="w").pack(side="left")
            ctk.CTkLabel(row_frame, text=action,
                font=ctk.CTkFont("Courier New", 11),
                text_color=PALETTE["text2"]).pack(side="left", padx=8)

        ctk.CTkLabel(rf, text="Alarm Event Log",
            font=ctk.CTkFont("Courier New", 13, "bold"),
            text_color=PALETTE["text2"]).pack(anchor="w", pady=(20, 8))

        self.alarm_log = LogBox(rf, height=220)
        self.alarm_log.pack(fill="both", expand=True)

    def _simulate_pass(self):
        def _worker():
            try:
                self.alarm_log.log("Tag entering door zone…", "info")
                epc, rssi = self.reader.read_epc()
                asset = self.db.get_asset_by_epc(epc)
                if not asset:
                    self._alarm("UNKNOWN", epc, rssi,
                                f"Unknown tag: {epc}")
                else:
                    status = asset["status"]
                    name   = asset["name"] or asset["asset_id"]
                    if status == "READY":
                        self._clear_door(name, epc)
                        self.alarm_log.log(
                            f"✔ {name} — READY — exit allowed", "ok")
                    elif status == "BLOCKED":
                        self._alarm("BLOCKED", epc, rssi,
                                    f"🚨 ALARM: {name} is BLOCKED!")
                    elif status == "MAINTENANCE":
                        self._warn_door(name, epc)
                        self.alarm_log.log(
                            f"⚠ {name} — MAINTENANCE — staff alerted", "warn")
                    else:
                        self.alarm_log.log(
                            f"⚠ {name} — PENDING — alert staff", "warn")
                    self.db.log_scan(asset["asset_id"], epc,
                                     "DOOR_SIM", status)
            except RuntimeError as e:
                self.alarm_log.log(f"Scan error: {e}", "error")
        threading.Thread(target=_worker, daemon=True).start()

    def _alarm(self, reason, epc, rssi, msg):
        self.door_frame.configure(border_color=PALETTE["danger"])
        self.door_icon.configure(text="🚨")
        self.door_status.configure(text="ALARM!", text_color=PALETTE["danger"])
        self.door_detail.configure(text=f"EPC: {epc[:12]}…")
        self.alarm_log.log(msg, "error")
        # Flash effect
        def reset():
            time.sleep(3)
            self.door_frame.configure(border_color=PALETTE["border"])
            self.door_icon.configure(text="🚪")
            self.door_status.configure(text="CLEAR",
                                       text_color=PALETTE["success"])
            self.door_detail.configure(text="No tag detected")
        threading.Thread(target=reset, daemon=True).start()

    def _clear_door(self, name, epc):
        self.door_frame.configure(border_color=PALETTE["success"])
        self.door_icon.configure(text="✅")
        self.door_status.configure(text="CLEAR", text_color=PALETTE["success"])
        self.door_detail.configure(text=name)
        def reset():
            time.sleep(2)
            self.door_frame.configure(border_color=PALETTE["border"])
            self.door_icon.configure(text="🚪")
            self.door_status.configure(text="CLEAR",
                                       text_color=PALETTE["success"])
            self.door_detail.configure(text="No tag detected")
        threading.Thread(target=reset, daemon=True).start()

    def _warn_door(self, name, epc):
        self.door_frame.configure(border_color=PALETTE["warning"])
        self.door_icon.configure(text="⚠️")
        self.door_status.configure(text="WARNING",
                                   text_color=PALETTE["warning"])
        self.door_detail.configure(text=name)
        def reset():
            time.sleep(2)
            self.door_frame.configure(border_color=PALETTE["border"])
            self.door_icon.configure(text="🚪")
            self.door_status.configure(text="CLEAR",
                                       text_color=PALETTE["success"])
            self.door_detail.configure(text="No tag detected")
        threading.Thread(target=reset, daemon=True).start()

    def _toggle_continuous(self):
        self._sim_running = not self._sim_running
        if self._sim_running:
            self.v_continuous.configure(text="● Scanning…",
                                        text_color=PALETTE["success"])
            threading.Thread(target=self._continuous_worker,
                             daemon=True).start()
        else:
            self.v_continuous.configure(text="● Idle",
                                        text_color=PALETTE["text3"])

    def _continuous_worker(self):
        while self._sim_running:
            self._simulate_pass()
            time.sleep(3)


class SettingsPanel(ctk.CTkFrame):
    """Honeywell PC42t/PC42d printer configuration + app preferences."""
    def __init__(self, master, db, log_box, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db  = db
        self.log = log_box
        self._build()

    def _build(self):
        SectionHeader(self, "Settings",
                      "Honeywell PC42t/PC42d + app preferences", "⚙").pack(
            fill="x", padx=20, pady=(20, 10))

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        scroll.grid_columnconfigure(0, weight=1)

        # ── Section helper ────────────────────────────────────────────────────
        def section(parent, title):
            f = ctk.CTkFrame(parent, fg_color=PALETTE["bg2"],
                             border_color=PALETTE["border"], border_width=1,
                             corner_radius=8)
            f.pack(fill="x", pady=(0, 12))
            ctk.CTkLabel(f, text=f"  {title}",
                font=ctk.CTkFont("Courier New", 12, "bold"),
                text_color=PALETTE["accent"],
                anchor="w").pack(fill="x", padx=4, pady=(10, 4))
            ctk.CTkFrame(f, fg_color=PALETTE["border"], height=1).pack(
                fill="x", padx=8, pady=(0, 8))
            return f

        def row(parent, label, var, placeholder="", row_n=0):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=3)
            r.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text=label,
                font=ctk.CTkFont("Courier New", 11),
                text_color=PALETTE["text2"],
                width=170, anchor="e").grid(row=0, column=0, padx=(0,10))
            ctk.CTkEntry(r, textvariable=var,
                placeholder_text=placeholder,
                font=ctk.CTkFont("Courier New", 12),
                fg_color=PALETTE["bg3"],
                border_color=PALETTE["border"],
                text_color=PALETTE["text"],
                height=34).grid(row=0, column=1, sticky="ew")

        # ══════════════════════════════════════════════════════════════════════
        # 1. HONEYWELL PRINTER
        # ══════════════════════════════════════════════════════════════════════
        ps = section(scroll, "🖨  Honeywell PC42t / PC42d Printer")

        # Connection mode toggle
        mf = ctk.CTkFrame(ps, fg_color="transparent")
        mf.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(mf, text="Connection Mode",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"],
            width=170, anchor="e").pack(side="left")
        self.v_print_mode = tk.StringVar(
            value=self.db.get_setting("print_mode") or "network")
        ctk.CTkSegmentedButton(mf,
            values=["network", "usb"],
            variable=self.v_print_mode,
            fg_color=PALETTE["bg3"],
            selected_color=PALETTE["accent"],
            selected_hover_color="#00B891",
            unselected_color=PALETTE["bg3"],
            font=ctk.CTkFont("Courier New", 12),
            command=self._toggle_mode).pack(side="left", padx=12)

        # Network fields
        self.net_frame = ctk.CTkFrame(ps, fg_color="transparent")
        self.net_frame.pack(fill="x")
        self.net_frame.grid_columnconfigure(1, weight=1)

        self.v_printer_ip   = tk.StringVar(
            value=self.db.get_setting("printer_ip") or "")
        self.v_printer_port = tk.StringVar(
            value=self.db.get_setting("printer_port") or "9100")
        row(self.net_frame, "Printer IP Address", self.v_printer_ip,
            "e.g. 192.168.1.100")
        row(self.net_frame, "Port (default 9100)", self.v_printer_port,
            "9100")

        # USB fields
        self.usb_frame = ctk.CTkFrame(ps, fg_color="transparent")
        self.usb_frame.grid_columnconfigure(1, weight=1)
        self.v_usb_name = tk.StringVar(
            value=self.db.get_setting("printer_usb_name") or "")
        row(self.usb_frame, "macOS Printer Name", self.v_usb_name,
            "Name from System Settings → Printers")

        # Language
        lf = ctk.CTkFrame(ps, fg_color="transparent")
        lf.pack(fill="x", padx=12, pady=(4, 0))
        ctk.CTkLabel(lf, text="Label Language",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"],
            width=170, anchor="e").pack(side="left")
        self.v_lang = tk.StringVar(
            value=self.db.get_setting("printer_lang") or "auto")
        ctk.CTkSegmentedButton(lf,
            values=["auto", "zpl", "ipl"],
            variable=self.v_lang,
            fg_color=PALETTE["bg3"],
            selected_color=PALETTE["accent2"],
            selected_hover_color="#0284C7",
            unselected_color=PALETTE["bg3"],
            font=ctk.CTkFont("Courier New", 12)).pack(side="left", padx=12)
        ctk.CTkLabel(lf, text="auto = probe printer on first print",
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text3"]).pack(side="left")

        # Copies
        cf = ctk.CTkFrame(ps, fg_color="transparent")
        cf.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(cf, text="Copies per Label",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"],
            width=170, anchor="e").pack(side="left")
        self.v_copies = tk.StringVar(
            value=self.db.get_setting("printer_copies") or "1")
        ctk.CTkEntry(cf, textvariable=self.v_copies, width=60,
            font=ctk.CTkFont("Courier New", 12),
            fg_color=PALETTE["bg3"],
            border_color=PALETTE["border"],
            text_color=PALETTE["text"],
            height=34).pack(side="left", padx=12)

        # Test + Detect buttons
        bf = ctk.CTkFrame(ps, fg_color="transparent")
        bf.pack(fill="x", padx=12, pady=(10, 12))
        ctk.CTkButton(bf, text="⟳  Detect Language",
            fg_color=PALETTE["bg3"], hover_color=PALETTE["border"],
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=36, command=self._detect_lang).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bf, text="▷  Test Print",
            fg_color=PALETTE["accent2"], hover_color="#0284C7",
            font=ctk.CTkFont("Courier New", 12, "bold"),
            height=36, command=self._test_print).pack(side="left", padx=(0, 8))
        self.printer_status = ctk.CTkLabel(bf, text="",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"])
        self.printer_status.pack(side="left", padx=8)

        # ══════════════════════════════════════════════════════════════════════
        # 2. ASSET ID
        # ══════════════════════════════════════════════════════════════════════
        ids = section(scroll, "🏷  Asset ID Generation")
        self.v_prefix  = tk.StringVar(
            value=self.db.get_setting("id_prefix") or "HOSP-EQP")
        self.v_counter = tk.StringVar(
            value=self.db.get_setting("id_counter") or "1")
        row(ids, "ID Prefix",      self.v_prefix,  "e.g. HOSP-EQP")
        row(ids, "Counter Start",  self.v_counter, "e.g. 1")
        ctk.CTkLabel(ids,
            text=f'  Preview: {self.db.get_setting("id_prefix") or "HOSP-EQP"}'
                 f'-{int(self.db.get_setting("id_counter") or 1):06d}',
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["accent"]).pack(anchor="w", padx=16, pady=(0, 10))

        # ══════════════════════════════════════════════════════════════════════
        # 3. FILES
        # ══════════════════════════════════════════════════════════════════════
        fs = section(scroll, "📂  Files & Export")
        self.v_export = tk.StringVar(
            value=self.db.get_setting("export_dir") or EXPORT_DIR)
        row(fs, "Export Directory", self.v_export, EXPORT_DIR)
        ctk.CTkLabel(fs,
            text=f"  Database: {DB_PATH}",
            font=ctk.CTkFont("Courier New", 10),
            text_color=PALETTE["text3"]).pack(anchor="w", padx=16, pady=(0, 10))

        # ══════════════════════════════════════════════════════════════════════
        # 4. APPEARANCE
        # ══════════════════════════════════════════════════════════════════════
        ap = section(scroll, "🎨  Appearance")
        af = ctk.CTkFrame(ap, fg_color="transparent")
        af.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(af, text="Theme",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text2"],
            width=170, anchor="e").pack(side="left")
        ctk.CTkSegmentedButton(af,
            values=["Dark", "Light", "System"],
            command=lambda v: ctk.set_appearance_mode(v.lower()),
            fg_color=PALETTE["bg3"],
            selected_color=PALETTE["accent"],
            selected_hover_color="#00B891",
            unselected_color=PALETTE["bg3"],
            font=ctk.CTkFont("Courier New", 12)).pack(side="left", padx=12)

        # ── Save button ───────────────────────────────────────────────────────
        ctk.CTkButton(scroll, text="💾  Save All Settings",
            fg_color=PALETTE["accent"],
            text_color="#0D1117",
            font=ctk.CTkFont("Courier New", 13, "bold"),
            height=44, corner_radius=6,
            command=self._save).pack(fill="x", pady=(4, 16))

        # Apply initial visibility
        self._toggle_mode(self.v_print_mode.get())

    # ── Mode toggle ───────────────────────────────────────────────────────────
    def _toggle_mode(self, mode):
        if mode == "network":
            self.net_frame.pack(fill="x")
            self.usb_frame.pack_forget()
        else:
            self.net_frame.pack_forget()
            self.usb_frame.pack(fill="x")

    # ── Detect language over network ──────────────────────────────────────────
    def _detect_lang(self):
        host = self.v_printer_ip.get().strip()
        if not host:
            self.printer_status.configure(
                text="⚠ Enter IP first", text_color=PALETTE["warning"])
            return
        self.printer_status.configure(
            text="Probing…", text_color=PALETTE["text2"])
        def _worker():
            try:
                port = int(self.v_printer_port.get().strip() or 9100)
            except ValueError:
                port = 9100
            lang = HoneywellPrinter.detect_language(host, port)
            if lang == "ipl":
                self.v_lang.set("ipl")
                self.printer_status.configure(
                    text="✔ Detected: IPL (Honeywell native)",
                    text_color=PALETTE["success"])
                self.log.log(f"Printer at {host} speaking IPL", "ok")
            elif lang == "zpl":
                self.v_lang.set("zpl")
                self.printer_status.configure(
                    text="✔ Detected: ZPL (Zebra-compatible)",
                    text_color=PALETTE["success"])
                self.log.log(f"Printer at {host} speaking ZPL", "ok")
            else:
                self.printer_status.configure(
                    text="⚠ Could not detect — defaulting to ZPL",
                    text_color=PALETTE["warning"])
                self.log.log(
                    f"Language detection inconclusive for {host}", "warn")
        threading.Thread(target=_worker, daemon=True).start()

    # ── Test print ────────────────────────────────────────────────────────────
    def _test_print(self):
        self._save()   # save settings first
        mode     = self.v_print_mode.get()
        host     = self.v_printer_ip.get().strip()
        port_str = self.v_printer_port.get().strip()
        usbname  = self.v_usb_name.get().strip()
        lang     = self.v_lang.get()

        try:
            port = int(port_str or 9100)
        except ValueError:
            port = 9100

        self.printer_status.configure(
            text="Sending test label…", text_color=PALETTE["text2"])

        def _worker():
            ok, msg, lang_used = HoneywellPrinter.print_label(
                asset_id   = "TEST-000001",
                epc        = "E200000000000000TEST0001",
                name       = "Honeywell Test Label",
                location   = "Printer Test",
                tag_type   = "Standard (Plastic)",
                mode       = mode,
                host       = host,
                port       = port,
                printer_name = usbname or None,
                language   = lang,
            )
            if ok:
                self.printer_status.configure(
                    text=f"✔ Test printed OK  ({lang_used.upper()})",
                    text_color=PALETTE["success"])
                self.log.log(
                    f"Test print OK — {mode} — language: {lang_used.upper()}", "ok")
            else:
                self.printer_status.configure(
                    text=f"✖ {msg[:50]}",
                    text_color=PALETTE["danger"])
                self.log.log(f"Test print failed: {msg}", "error")
        threading.Thread(target=_worker, daemon=True).start()

    # ── Save ──────────────────────────────────────────────────────────────────
    def _save(self):
        self.db.set_setting("print_mode",      self.v_print_mode.get())
        self.db.set_setting("printer_ip",      self.v_printer_ip.get().strip())
        self.db.set_setting("printer_port",    self.v_printer_port.get().strip() or "9100")
        self.db.set_setting("printer_usb_name",self.v_usb_name.get().strip())
        self.db.set_setting("printer_lang",    self.v_lang.get())
        self.db.set_setting("printer_copies",  self.v_copies.get().strip() or "1")
        self.db.set_setting("id_prefix",       self.v_prefix.get().strip())
        self.db.set_setting("id_counter",      self.v_counter.get().strip())
        self.db.set_setting("export_dir",      self.v_export.get().strip())
        self.log.log("Settings saved", "ok")


# ─── Main Application Window ───────────────────────────────────────────────────
class RFIDApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title(f"{APP_TITLE}  {APP_VERSION}")
        self.geometry("1400x860")
        self.minsize(1100, 700)
        self.configure(fg_color=PALETTE["bg"])

        self.db     = Database()
        self.reader = RFIDReader()

        self._build_ui()

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        topbar = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                              corner_radius=0, height=56)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        ctk.CTkLabel(topbar,
            text="  ◈  RFID ASSET MANAGER",
            font=ctk.CTkFont("Courier New", 15, "bold"),
            text_color=PALETTE["accent"]).pack(side="left", padx=16)

        # hw status pill
        hw_text  = "RFID READER ONLINE" if RFID_AVAILABLE else "SIMULATION MODE"
        hw_color = PALETTE["success"] if RFID_AVAILABLE else PALETTE["warning"]
        ctk.CTkLabel(topbar, text=f"  ●  {hw_text}  ",
            font=ctk.CTkFont("Courier New", 10, "bold"),
            fg_color=PALETTE["bg3"],
            corner_radius=4,
            text_color=hw_color).pack(side="left", padx=8)

        self.clock_label = ctk.CTkLabel(topbar, text="",
            font=ctk.CTkFont("Courier New", 11),
            text_color=PALETTE["text3"])
        self.clock_label.pack(side="right", padx=16)
        self._tick()

        # ── Left sidebar nav ─────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, fg_color=PALETTE["bg2"],
                               border_color=PALETTE["border"], border_width=0,
                               corner_radius=0, width=200)
        sidebar.pack(fill="y", side="left")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(sidebar, text="",
                     height=10).pack()  # spacer

        self._panels     = {}
        self._nav_btns   = {}
        self._active_nav = None

        nav_items = [
            ("dashboard",  "◈  Dashboard"),
            ("read",       "⊕  Read & Encode"),
            ("batch",      "⊞  Batch Mode"),
            ("assets",     "⊟  Asset Database"),
            ("simulate",   "⊘  Door Simulation"),
            ("settings",   "⚙  Settings"),
        ]

        for key, label in nav_items:
            btn = ctk.CTkButton(sidebar, text=label,
                font=ctk.CTkFont("Courier New", 12),
                fg_color="transparent",
                hover_color=PALETTE["bg3"],
                text_color=PALETTE["text2"],
                anchor="w", corner_radius=0, height=44,
                command=lambda k=key: self._nav(k))
            btn.pack(fill="x")
            self._nav_btns[key] = btn

        # bottom sidebar: export dir shortcut
        ctk.CTkButton(sidebar, text="📂  Open Exports",
            font=ctk.CTkFont("Courier New", 10),
            fg_color="transparent",
            hover_color=PALETTE["bg3"],
            text_color=PALETTE["text3"],
            anchor="w", corner_radius=0, height=36,
            command=self._open_exports).pack(side="bottom", fill="x")

        # ── Main content area ────────────────────────────────────────────────
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, side="left")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Shared log box (bottom strip)
        log_frame = ctk.CTkFrame(content, fg_color=PALETTE["bg2"],
                                 border_color=PALETTE["border"], border_width=1,
                                 corner_radius=0, height=100)
        log_frame.pack(fill="x", side="bottom")
        log_frame.pack_propagate(False)
        ctk.CTkLabel(log_frame, text=" ACTIVITY LOG",
            font=ctk.CTkFont("Courier New", 9, "bold"),
            text_color=PALETTE["text3"]).pack(side="left", padx=8)
        self.log_box = LogBox(log_frame, height=80)
        self.log_box.pack(fill="both", expand=True, padx=4, pady=4)

        panel_host = ctk.CTkFrame(content, fg_color="transparent")
        panel_host.pack(fill="both", expand=True)

        # Build all panels (hidden until activated)
        self._panels["dashboard"] = DashboardPanel(
            panel_host, self.db)
        self._panels["read"] = ReadEncodePanel(
            panel_host, self.db, self.reader, self.log_box,
            self._refresh_all)
        self._panels["batch"] = BatchPanel(
            panel_host, self.db, self.reader, self.log_box,
            self._refresh_all)
        self._panels["assets"] = AssetsPanel(
            panel_host, self.db, self.log_box, self._refresh_all)
        self._panels["simulate"] = SimulatePanel(
            panel_host, self.db, self.reader, self.log_box)
        self._panels["settings"] = SettingsPanel(
            panel_host, self.db, self.log_box)

        self._nav("dashboard")

        self.log_box.log(
            f"{APP_TITLE} started — DB: {DB_PATH}", "ok")
        if not RFID_AVAILABLE:
            self.log_box.log(
                "RFIDIOt not installed — running in simulation mode. "
                "pip install RFIDIOt to enable hardware.", "warn")

    def _nav(self, key):
        if self._active_nav == key:
            return
        for k, p in self._panels.items():
            p.pack_forget()
        self._panels[key].pack(fill="both", expand=True)
        if self._active_nav:
            self._nav_btns[self._active_nav].configure(
                fg_color="transparent",
                text_color=PALETTE["text2"])
        self._nav_btns[key].configure(
            fg_color=PALETTE["bg3"],
            text_color=PALETTE["accent"])
        self._active_nav = key

    def _refresh_all(self):
        """Refresh dashboard stats after changes."""
        if "dashboard" in self._panels:
            self._panels["dashboard"]._refresh()
        if "assets" in self._panels:
            self._panels["assets"]._refresh()

    def _tick(self):
        self.clock_label.configure(
            text=datetime.now().strftime("  %a %d %b  %H:%M:%S  "))
        self.after(1000, self._tick)

    def _open_exports(self):
        if platform.system() == "Darwin":
            subprocess.run(["open", EXPORT_DIR])
        elif platform.system() == "Windows":
            os.startfile(EXPORT_DIR)
        else:
            subprocess.run(["xdg-open", EXPORT_DIR])


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = RFIDApp()
    app.mainloop()