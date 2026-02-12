"""
win_print_debug.py — Honeywell PC42t Windows Print Diagnostics
===============================================================
Run this FIRST on Windows to figure out exactly why labels are blank.
It will test every print method and tell you which one works.

Usage:
    python win_print_debug.py

No extra packages required (pywin32 optional but recommended).
"""

import os, sys, time, socket, subprocess, platform, struct
import ctypes, ctypes.wintypes as wt
from datetime import datetime

# ─── Minimal test ZPL — as simple as possible, just text, no frills ──────────
MINIMAL_ZPL = b"^XA^FO50,50^A0N,40,40^FDHoneywell Test^FS^FO50,110^A0N,30,30^FDPC42t Windows^FS^FO50,160^A0N,25,25^FD" + datetime.now().strftime("%H:%M:%S").encode() + b"^FS^XZ"

# ─── Minimal test IPL ────────────────────────────────────────────────────────
MINIMAL_IPL = (
    b"\x02"            # STX
    b"n\r\n"           # new label
    b"M t\r\n"         # thermal transfer
    b"S l1;c15,3\r\n"  # 4x2 inch @ 203dpi
    b"d PC\r\n"        # PC42 direct
    b'T 50,50,0,3,2,2,"Honeywell Test"\r\n'
    b'T 50,110,0,3,1,1,"PC42t Windows"\r\n'
    b"P 1\r\n"         # print 1 copy
    b"\x03"            # ETX
)

SEP = "─" * 60


def header(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def ok(msg):   print(f"  ✔  {msg}")
def fail(msg): print(f"  ✖  {msg}")
def info(msg): print(f"  ·  {msg}")
def warn(msg): print(f"  ⚠  {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Environment
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 1 — Environment")
info(f"Python  : {sys.version}")
info(f"OS      : {platform.version()}")
info(f"Admin   : {ctypes.windll.shell32.IsUserAnAdmin() != 0}")
info(f"Script  : {os.path.abspath(__file__)}")

if platform.system() != "Windows":
    fail("This script is for Windows only.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Find installed printers
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 2 — Installed printers")

printers = []
default_printer = ""

# Method A: win32print
try:
    import win32print
    printers = [p[2] for p in win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
        None, 4)]
    default_printer = win32print.GetDefaultPrinter()
    ok(f"win32print found {len(printers)} printer(s)")
    for p in printers:
        marker = " ← DEFAULT" if p == default_printer else ""
        info(f"  '{p}'{marker}")
except ImportError:
    warn("pywin32 not installed — using PowerShell fallback")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Printer | Select-Object Name,PortName,DriverName | Format-Table -AutoSize"],
            capture_output=True, text=True, timeout=10)
        print(r.stdout)
        # Extract names
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("Name") and not line.startswith("----"):
                printers.append(line.split()[0])
    except Exception as e:
        fail(f"PowerShell failed: {e}")
except Exception as e:
    fail(f"Printer enumeration error: {e}")

if not printers:
    fail("No printers found! Install Honeywell PC42t Windows driver first.")
    print("\n  Download: https://www.honeywell.com/en-us/software-licensing")
    sys.exit(1)

# Let user pick
honeywell_printers = [p for p in printers
                      if any(k in p.lower() for k in
                             ("honeywell","pc42","pc42t","pc42d","intermec"))]
if honeywell_printers:
    target_printer = honeywell_printers[0]
    ok(f"Auto-selected Honeywell printer: '{target_printer}'")
elif default_printer:
    target_printer = default_printer
    warn(f"No Honeywell printer found — using default: '{target_printer}'")
else:
    target_printer = printers[0]
    warn(f"Using first available printer: '{target_printer}'")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Inspect printer port
# ══════════════════════════════════════════════════════════════════════════════
header(f"STEP 3 — Port for '{target_printer}'")

port_name   = None
driver_name = None

try:
    import win32print
    h = win32print.OpenPrinter(target_printer)
    try:
        info2 = win32print.GetPrinter(h, 2)
        port_name   = info2["pPortName"]
        driver_name = info2["pDriverName"]
        ok(f"Port   : {port_name}")
        ok(f"Driver : {driver_name}")

        # Check if driver is RAW-passthrough capable
        raw_drivers = ["generic", "text only", "raw", "zpl", "ipl", "honeywell", "intermec", "datamax"]
        driver_ok = any(k in driver_name.lower() for k in raw_drivers)
        if driver_ok:
            ok(f"Driver looks RAW-compatible ✔")
        else:
            warn(f"Driver '{driver_name}' may NOT pass raw ZPL/IPL through!")
            warn("Consider installing 'Generic / Text Only' driver for this printer.")
            warn("See STEP 7 at the bottom for instructions.")
    finally:
        win32print.ClosePrinter(h)
except ImportError:
    warn("win32print not available — using PowerShell")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f'Get-Printer -Name "{target_printer}" | Select-Object PortName,DriverName | Format-List'],
            capture_output=True, text=True, timeout=8)
        print(r.stdout)
        for line in r.stdout.splitlines():
            if "PortName" in line:
                port_name = line.split(":")[-1].strip()
            if "DriverName" in line:
                driver_name = line.split(":")[-1].strip()
    except Exception as e:
        fail(f"PowerShell failed: {e}")
except Exception as e:
    fail(f"Cannot read printer info: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Check if port is a network IP
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 4 — Port type analysis")

is_network_port = False
printer_ip      = None

if port_name:
    # Check if it looks like an IP address
    parts = port_name.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        is_network_port = True
        printer_ip      = port_name
        ok(f"Port is a network IP: {printer_ip}")
        info("Network printing via TCP port 9100 is available.")
    elif port_name.upper().startswith("USB"):
        ok(f"Port is USB: {port_name}")
    elif port_name.upper().startswith("COM"):
        ok(f"Port is Serial: {port_name}")
    elif port_name.upper().startswith("LPT"):
        ok(f"Port is Parallel: {port_name}")
    else:
        info(f"Port type unknown: {port_name}")
        # Try to resolve as hostname
        try:
            ip = socket.gethostbyname(port_name)
            is_network_port = True
            printer_ip      = ip
            ok(f"Port resolved to IP: {ip}")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Test data bytes
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 5 — Generate label bytes")

info(f"ZPL bytes ({len(MINIMAL_ZPL)})  : {MINIMAL_ZPL[:60]}...")
info(f"IPL bytes ({len(MINIMAL_IPL)})  : {MINIMAL_IPL[:60]}...")

# Verify ZPL structure
assert MINIMAL_ZPL.startswith(b"^XA"), "ZPL must start with ^XA"
assert MINIMAL_ZPL.endswith(b"^XZ"),   "ZPL must end with ^XZ"
assert b"^FD" in MINIMAL_ZPL,          "ZPL must contain ^FD field data"
ok("ZPL structure valid")

# Verify IPL structure
assert MINIMAL_IPL[0:1] == b"\x02", "IPL must start with STX (0x02)"
assert MINIMAL_IPL[-1:] == b"\x03", "IPL must end with ETX (0x03)"
assert b"^T " not in MINIMAL_IPL,   "IPL should not contain ^ commands"
ok("IPL structure valid")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Try every print method
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 6 — Testing all print methods")

results = {}

# ── Method A: Network TCP (if IP known) ──────────────────────────────────────
print("\n  [A] Network TCP → port 9100")
if printer_ip or is_network_port:
    ip = printer_ip
    for fmt_name, data in [("ZPL", MINIMAL_ZPL), ("IPL", MINIMAL_IPL)]:
        try:
            with socket.create_connection((ip, 9100), timeout=5) as s:
                s.sendall(data)
                time.sleep(0.5)
            ok(f"Network {fmt_name} sent to {ip}:9100 — check if label printed")
            results[f"Network-{fmt_name}"] = True
        except Exception as e:
            fail(f"Network {fmt_name}: {e}")
            results[f"Network-{fmt_name}"] = False
else:
    info("Skipped — no network IP for this printer")
    info("If your printer is on WiFi/Ethernet, enter its IP in Settings → Network mode")

# ── Method B: Direct port write (CreateFile/WriteFile) ───────────────────────
print(f"\n  [B] Direct port write → {port_name}")
if port_name:
    GENERIC_WRITE  = 0x40000000
    OPEN_EXISTING  = 3
    INVALID_HANDLE = wt.HANDLE(-1).value

    for fmt_name, data in [("ZPL", MINIMAL_ZPL), ("IPL", MINIMAL_IPL)]:
        port_path = f"\\\\.\\{port_name}"
        handle = ctypes.windll.kernel32.CreateFileW(
            port_path, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE:
            err = ctypes.windll.kernel32.GetLastError()
            fail(f"Direct {fmt_name}: Cannot open {port_path} — error {err} "
                 f"({'Access denied — run as Admin' if err == 5 else 'Port not found' if err == 2 else 'Unknown'})")
            results[f"Direct-{fmt_name}"] = False
        else:
            written = wt.DWORD(0)
            ok_write = ctypes.windll.kernel32.WriteFile(
                handle, data, len(data), ctypes.byref(written), None)
            ctypes.windll.kernel32.CloseHandle(handle)
            if ok_write and written.value == len(data):
                ok(f"Direct {fmt_name}: wrote {written.value} bytes to {port_path} — check label")
                results[f"Direct-{fmt_name}"] = True
            else:
                err = ctypes.windll.kernel32.GetLastError()
                fail(f"Direct {fmt_name}: WriteFile error {err}")
                results[f"Direct-{fmt_name}"] = False
else:
    info("Skipped — port name unknown")

# ── Method C: win32print RAW spool ───────────────────────────────────────────
print(f"\n  [C] win32print RAW spool → '{target_printer}'")
try:
    import win32print
    for fmt_name, data in [("ZPL", MINIMAL_ZPL), ("IPL", MINIMAL_IPL)]:
        try:
            h = win32print.OpenPrinter(target_printer)
            try:
                win32print.StartDocPrinter(h, 1, (f"Test {fmt_name}", None, "RAW"))
                win32print.StartPagePrinter(h)
                win32print.WritePrinter(h, data)
                win32print.EndPagePrinter(h)
                win32print.EndDocPrinter(h)
            finally:
                win32print.ClosePrinter(h)
            ok(f"Spooler {fmt_name}: job submitted — check label")
            results[f"Spooler-{fmt_name}"] = True
        except Exception as e:
            fail(f"Spooler {fmt_name}: {e}")
            results[f"Spooler-{fmt_name}"] = False
except ImportError:
    warn("pywin32 not installed — skipping spooler test")
    info("Install it: pip install pywin32")

# ── Method D: PowerShell cmd copy /b ────────────────────────────────────────
print(f"\n  [D] PowerShell cmd copy /b → '{target_printer}'")
import tempfile
for fmt_name, data in [("ZPL", MINIMAL_ZPL), ("IPL", MINIMAL_IPL)]:
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=f"_{fmt_name}.prn")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(data)
        ps = (
            f'$p = (Get-Printer -Name "{target_printer}" -ErrorAction Stop).PortName;'
            f'cmd /c "copy /b \\"{tmp}\\" $p" 2>&1'
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and ("1 file" in out.lower() or out == ""):
            ok(f"PowerShell {fmt_name}: copy succeeded — check label")
            results[f"PS-{fmt_name}"] = True
        else:
            fail(f"PowerShell {fmt_name}: {out or 'returncode=' + str(r.returncode)}")
            results[f"PS-{fmt_name}"] = False
    except Exception as e:
        fail(f"PowerShell {fmt_name}: {e}")
        results[f"PS-{fmt_name}"] = False
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except Exception: pass

# ── Method E: Write to USB device directly (no driver) ───────────────────────
print("\n  [E] Scan all USB ports — write raw bytes")
usb_ports = []
try:
    import win32print
    for p in win32print.EnumPorts(None, 1):
        n = p["pName"]
        if n.upper().startswith("USB"):
            usb_ports.append(n)
except Exception:
    usb_ports = [f"USB{i:03d}" for i in range(1, 6)]

info(f"USB ports to try: {usb_ports}")
for port in usb_ports:
    for fmt_name, data in [("ZPL", MINIMAL_ZPL)]:
        port_path = f"\\\\.\\{port}"
        handle = ctypes.windll.kernel32.CreateFileW(
            port_path, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if handle != INVALID_HANDLE:
            written = wt.DWORD(0)
            ok_write = ctypes.windll.kernel32.WriteFile(
                handle, data, len(data), ctypes.byref(written), None)
            ctypes.windll.kernel32.CloseHandle(handle)
            if ok_write and written.value > 0:
                ok(f"Port {port}: wrote {written.value} bytes of {fmt_name} — check label!")
                results[f"USB-scan-{port}"] = True
            else:
                info(f"Port {port}: opened but write failed")
        # else: port not found, silent skip

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Summary + recommendations
# ══════════════════════════════════════════════════════════════════════════════
header("STEP 7 — Summary & Recommendations")

worked  = [k for k,v in results.items() if v is True]
failed  = [k for k,v in results.items() if v is False]

if worked:
    ok(f"Methods that successfully sent data: {', '.join(worked)}")
    print()
    print("  ► One or more methods sent data to the printer.")
    print("  ► If a label was still blank, the issue is the DRIVER intercepting")
    print("    the data and converting it to GDI before it reaches the print head.")
    print()
    print("  SOLUTION — Install Generic/Text Only driver:")
    print("    1. Open: Control Panel → Devices and Printers")
    print("    2. Right-click the Honeywell printer → Printer properties")
    print('    3. Click "Advanced" tab → New Driver')
    print('    4. Select: Generic → "Generic / Text Only"')
    print("    5. Re-run this script — then try printing again")
    print()
    print("  OR — Use Network mode in the app (most reliable):")
    print(f"    1. Find printer IP: press Menu on printer → Network → IP Address")
    print("    2. In app Settings: set mode=Network, enter IP, port=9100")
    print("    3. This sends ZPL/IPL bytes directly — zero driver interference")
else:
    fail("No print method succeeded")
    print()
    print("  Possible causes:")
    print("  1. Printer not connected or powered off")
    print("  2. Wrong printer selected")
    print("  3. Driver not installed")
    print()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        warn("You are NOT running as Administrator.")
        warn("Re-run this script as Admin:")
        warn("  Right-click → 'Run as administrator'")

print()
info(f"Printer tested : '{target_printer}'")
info(f"Port           : {port_name}")
info(f"Driver         : {driver_name}")
info(f"Admin rights   : {bool(ctypes.windll.shell32.IsUserAnAdmin())}")
print()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Write diagnostic report to file
# ══════════════════════════════════════════════════════════════════════════════
report_path = os.path.join(os.path.expanduser("~"), "rfid_print_diagnostic.txt")
with open(report_path, "w") as f:
    f.write(f"RFID Printer Diagnostic Report\n")
    f.write(f"Generated: {datetime.now()}\n\n")
    f.write(f"Printer : {target_printer}\n")
    f.write(f"Port    : {port_name}\n")
    f.write(f"Driver  : {driver_name}\n")
    f.write(f"Admin   : {bool(ctypes.windll.shell32.IsUserAnAdmin())}\n\n")
    f.write("Results:\n")
    for k, v in results.items():
        f.write(f"  {'OK' if v else 'FAIL'} — {k}\n")
    f.write(f"\nZPL sent:\n{MINIMAL_ZPL.decode('ascii', errors='replace')}\n")
    f.write(f"\nIPL sent (hex): {MINIMAL_IPL.hex()}\n")

ok(f"Report saved to: {report_path}")
print(f"\n{'═'*60}")
print("  Share rfid_print_diagnostic.txt if you need further help.")
print(f"{'═'*60}\n")

input("Press Enter to exit...")
