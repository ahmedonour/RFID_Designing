"""
install_rfidiot.py — Cross-Platform RFIDIOt Installer
======================================================
Installs RFIDIOt from GitHub (AdamLaurie/RFIDIOt) on:
  • Windows 10/11
  • macOS (Intel + Apple Silicon)
  • Linux (Ubuntu, Debian, Fedora, Arch)

Usage:
    python install_rfidiot.py

No arguments needed. Run with admin/sudo rights for best results.
"""

import os
import sys
import platform
import subprocess
import shutil
import textwrap
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────
REPO_URL    = "https://github.com/AdamLaurie/RFIDIOt.git"
REPO_NAME   = "RFIDIOt"
INSTALL_DIR = Path.home() / "rfidiot_src"

# Python deps needed by RFIDIOt
PIP_DEPS = [
    "pyserial",       # serial port communication (ACG/Frosch readers)
    "pyscard",        # PC/SC smart card interface
    "pycryptodome",   # crypto operations (replaces pycrypto)
    "Pillow",         # imaging support
]

# ─── Terminal colours ──────────────────────────────────────────────────────────
IS_WIN   = platform.system() == "Windows"
IS_MAC   = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

def _c(code, text):
    if IS_WIN:
        return text          # Windows CMD doesn't support ANSI by default
    return f"\033[{code}m{text}\033[0m"

def ok(msg):    print(_c("32", f"  ✔  {msg}"))
def fail(msg):  print(_c("31", f"  ✖  {msg}"))
def info(msg):  print(_c("36", f"  ·  {msg}"))
def warn(msg):  print(_c("33", f"  ⚠  {msg}"))
def hdr(title):
    bar = "─" * 58
    print(f"\n{_c('1;34', bar)}")
    print(_c("1;34", f"  {title}"))
    print(_c("1;34", bar))

def run(cmd, check=True, shell=False, capture=False):
    """Run a shell command. Returns (returncode, stdout, stderr)."""
    kw = dict(shell=shell)
    if capture:
        kw["capture_output"] = True
        kw["text"] = True
    result = subprocess.run(cmd, **kw)
    if check and result.returncode != 0:
        stderr = getattr(result, "stderr", "")
        raise RuntimeError(f"Command failed: {' '.join(cmd) if isinstance(cmd, list) else cmd}\n{stderr}")
    return result

def pip(*packages, flags=None):
    """Install pip packages. Uses --break-system-packages on modern Linux."""
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + list(packages)
    if flags:
        cmd += flags
    # On Linux/macOS with system Python, may need --break-system-packages
    try:
        run(cmd, capture=True)
    except RuntimeError:
        try:
            run(cmd + ["--break-system-packages"], capture=True)
        except RuntimeError:
            run(cmd + ["--user"], capture=True)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Python version check
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 1 — Python environment")
ver = sys.version_info
info(f"Python {ver.major}.{ver.minor}.{ver.micro} — {sys.executable}")
info(f"Platform: {platform.system()} {platform.machine()}")

if ver.major < 3 or (ver.major == 3 and ver.minor < 8):
    fail("Python 3.8+ required. Download from https://python.org")
    sys.exit(1)
ok("Python version OK")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Check/install Git
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 2 — Git")

git_path = shutil.which("git")
if git_path:
    r = run(["git", "--version"], capture=True, check=False)
    ok(f"Git found: {r.stdout.strip()}")
else:
    warn("Git not found — attempting to install...")
    if IS_WIN:
        # Try winget first (Windows 10+), then choco, then manual
        installed = False
        for mgr_cmd in [
            ["winget", "install", "--id", "Git.Git", "-e", "--source", "winget"],
            ["choco",  "install", "git", "-y"],
        ]:
            if shutil.which(mgr_cmd[0]):
                try:
                    run(mgr_cmd)
                    installed = True
                    break
                except RuntimeError:
                    pass
        if not installed:
            fail("Could not auto-install Git on Windows.")
            print(textwrap.dedent("""
                Please install Git manually:
                  1. Go to: https://git-scm.com/download/win
                  2. Download and run the installer
                  3. Re-run this script
            """))
            sys.exit(1)
    elif IS_MAC:
        # xcode-select installs git on macOS
        try:
            run(["xcode-select", "--install"], check=False)
            info("Waiting for Xcode Command Line Tools install…")
            import time; time.sleep(3)
        except Exception:
            pass
        if not shutil.which("git"):
            # Try Homebrew
            if shutil.which("brew"):
                run(["brew", "install", "git"])
            else:
                fail("Could not install Git. Install Homebrew first: https://brew.sh")
                sys.exit(1)
    elif IS_LINUX:
        # Detect distro
        distro_id = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        distro_id = line.split("=")[1].strip().strip('"').lower()
        except Exception:
            pass

        pkg_cmds = {
            "ubuntu": ["sudo", "apt-get", "install", "-y", "git"],
            "debian": ["sudo", "apt-get", "install", "-y", "git"],
            "fedora": ["sudo", "dnf",     "install", "-y", "git"],
            "centos": ["sudo", "yum",     "install", "-y", "git"],
            "arch":   ["sudo", "pacman",  "-Sy", "--noconfirm", "git"],
            "manjaro":["sudo", "pacman",  "-Sy", "--noconfirm", "git"],
        }
        cmd = pkg_cmds.get(distro_id,
              ["sudo", "apt-get", "install", "-y", "git"])  # default: apt
        try:
            run(cmd)
        except RuntimeError:
            fail("Could not install Git. Please install it manually.")
            sys.exit(1)

    git_path = shutil.which("git")
    if git_path:
        ok("Git installed successfully")
    else:
        fail("Git still not found after install attempt.")
        sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — System-level dependencies (non-Python)
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 3 — System dependencies")

if IS_LINUX:
    info("Installing pcscd, libpcsclite-dev, swig (needed for pyscard)…")
    distro_id = ""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    distro_id = line.split("=")[1].strip().strip('"').lower()
    except Exception:
        pass

    sys_pkg_cmds = {
        "ubuntu": ["sudo", "apt-get", "install", "-y",
                   "pcscd", "libpcsclite-dev", "swig",
                   "python3-dev", "build-essential", "libusb-1.0-0-dev"],
        "debian": ["sudo", "apt-get", "install", "-y",
                   "pcscd", "libpcsclite-dev", "swig",
                   "python3-dev", "build-essential", "libusb-1.0-0-dev"],
        "fedora": ["sudo", "dnf", "install", "-y",
                   "pcsc-lite", "pcsc-lite-devel", "swig",
                   "python3-devel", "gcc", "libusb1-devel"],
        "centos": ["sudo", "yum", "install", "-y",
                   "pcsc-lite", "pcsc-lite-devel", "swig",
                   "python3-devel", "gcc"],
        "arch":   ["sudo", "pacman", "-Sy", "--noconfirm",
                   "pcsclite", "swig", "python", "base-devel", "libusb"],
        "manjaro":["sudo", "pacman", "-Sy", "--noconfirm",
                   "pcsclite", "swig", "python", "base-devel", "libusb"],
    }
    cmd = sys_pkg_cmds.get(distro_id, sys_pkg_cmds["ubuntu"])
    try:
        run(cmd)
        ok("System packages installed")
    except RuntimeError as e:
        warn(f"System package install had issues: {e}")
        warn("Continuing — some features may not work without pcscd")

elif IS_MAC:
    info("Checking for Homebrew pcscd/pcsc-lite…")
    if shutil.which("brew"):
        try:
            run(["brew", "install", "pcsc-lite", "swig", "libusb"],
                capture=True, check=False)
            ok("Homebrew packages installed")
        except Exception as e:
            warn(f"brew install had issues: {e}")
    else:
        warn("Homebrew not found — skipping system deps.")
        warn("Install Homebrew for full support: https://brew.sh")

elif IS_WIN:
    info("Windows: PC/SC is built into Windows (winscard.dll) — no extra install needed")
    info("USB HID support: install Zadig driver if needed → https://zadig.akeo.ie")
    ok("Windows system deps: OK")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Upgrade pip + install Python dependencies
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 4 — Python dependencies")

info("Upgrading pip…")
try:
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        capture=True)
    ok("pip upgraded")
except Exception as e:
    warn(f"pip upgrade issue: {e}")

for pkg in PIP_DEPS:
    info(f"Installing {pkg}…")
    try:
        pip(pkg)
        ok(f"{pkg} installed")
    except Exception as e:
        warn(f"{pkg} install issue: {e} — continuing")

# Windows: also install pywin32 for printer support
if IS_WIN:
    info("Installing pywin32 (Windows printer support)…")
    try:
        pip("pywin32")
        ok("pywin32 installed")
    except Exception as e:
        warn(f"pywin32 install issue: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Clone RFIDIOt from GitHub
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 5 — Clone RFIDIOt from GitHub")

info(f"Target directory: {INSTALL_DIR}")

if INSTALL_DIR.exists():
    warn(f"Directory already exists: {INSTALL_DIR}")
    choice = input("  Update existing clone? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        info("Pulling latest changes…")
        try:
            run(["git", "-C", str(INSTALL_DIR), "pull", "--rebase"], capture=True)
            ok("RFIDIOt updated to latest")
        except RuntimeError as e:
            warn(f"git pull failed: {e}")
            warn("Trying fresh clone…")
            shutil.rmtree(INSTALL_DIR, ignore_errors=True)
            run(["git", "clone", REPO_URL, str(INSTALL_DIR)])
            ok("Fresh clone complete")
    else:
        info("Skipping clone — using existing directory")
else:
    info(f"Cloning {REPO_URL}…")
    try:
        run(["git", "clone", REPO_URL, str(INSTALL_DIR)])
        ok(f"Cloned into {INSTALL_DIR}")
    except RuntimeError as e:
        fail(f"git clone failed: {e}")
        print(textwrap.dedent(f"""
            Possible causes:
              1. No internet connection
              2. GitHub is blocked on your network
              3. git is not on PATH

            Manual alternative:
              1. Download ZIP from: https://github.com/AdamLaurie/RFIDIOt/archive/refs/heads/master.zip
              2. Extract to: {INSTALL_DIR}
              3. Re-run this script (it will skip the clone step)
        """))
        sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Install RFIDIOt into Python path
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 6 — Install RFIDIOt into Python")

setup_py = INSTALL_DIR / "setup.py"
pyproject = INSTALL_DIR / "pyproject.toml"

installed_via_setup = False

if pyproject.exists() or setup_py.exists():
    info("Installing via pip (editable mode)…")
    try:
        run([sys.executable, "-m", "pip", "install", "-e",
             str(INSTALL_DIR), "--no-build-isolation"],
            capture=True)
        ok("Installed in editable mode (pip -e)")
        installed_via_setup = True
    except RuntimeError:
        try:
            run([sys.executable, "-m", "pip", "install", "-e",
                 str(INSTALL_DIR)], capture=True)
            ok("Installed in editable mode")
            installed_via_setup = True
        except RuntimeError as e:
            warn(f"pip install -e failed: {e}")

if not installed_via_setup:
    # Fallback: add the rfidiot sub-directory to sys.path via .pth file
    info("Falling back to .pth path injection…")
    try:
        import site
        site_dir = Path(site.getsitepackages()[0])
        pth_file  = site_dir / "rfidiot.pth"
        # The library lives in RFIDIOt/rfidiot/
        lib_path  = str(INSTALL_DIR)
        with open(pth_file, "w") as f:
            f.write(lib_path + "\n")
            f.write(str(INSTALL_DIR / "rfidiot") + "\n")
        ok(f"Created {pth_file}")
    except Exception as e:
        warn(f".pth injection failed: {e}")
        # Last resort: PYTHONPATH hint
        warn("Adding to PYTHONPATH manually:")
        warn(f"  export PYTHONPATH={INSTALL_DIR}:$PYTHONPATH")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Verify import works
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 7 — Verify installation")

# Add install dir to path for this process
sys.path.insert(0, str(INSTALL_DIR))
sys.path.insert(0, str(INSTALL_DIR / "rfidiot"))

import importlib
errors = []

for mod in ["serial", "Crypto", "PIL", "smartcard"]:
    try:
        importlib.import_module(mod)
        ok(f"import {mod}")
    except ImportError as e:
        warn(f"import {mod} — not available ({e})")
        errors.append(mod)

# Try importing rfidiot itself
try:
    import rfidiot
    ok("import rfidiot  ✔  RFIDIOt is installed and importable!")
except ImportError as e:
    warn(f"import rfidiot — {e}")
    warn("Trying alternate import path…")
    try:
        sys.path.insert(0, str(INSTALL_DIR / "rfidiot"))
        import RFIDIOt as rfidiot
        ok("import RFIDIOt  ✔  (uppercase module name)")
    except ImportError as e2:
        fail(f"Could not import rfidiot: {e2}")
        errors.append("rfidiot")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Write env setup file for the main app
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 8 — Configure RFID Asset Manager")

app_dir   = Path(__file__).parent
env_file  = app_dir / "rfidiot_path.txt"
with open(env_file, "w") as f:
    f.write(str(INSTALL_DIR) + "\n")
    f.write(str(INSTALL_DIR / "rfidiot") + "\n")

ok(f"Path config saved: {env_file}")
info("The RFID Asset Manager will automatically read this file on startup.")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Platform-specific post-install notes
# ══════════════════════════════════════════════════════════════════════════════
hdr("STEP 9 — Post-install notes")

if IS_LINUX:
    info("Linux: add your user to the 'dialout' and 'plugdev' groups")
    info("for USB serial access without sudo:")
    print(f"\n    sudo usermod -aG dialout,plugdev {os.getenv('USER', '$USER')}")
    print("    (log out and back in for changes to take effect)\n")
    info("Start PC/SC daemon:")
    print("    sudo systemctl enable pcscd && sudo systemctl start pcscd\n")

elif IS_MAC:
    info("macOS: USB serial readers should work immediately.")
    info("For PC/SC readers, ensure pcsc-lite is running:")
    print("    brew services start pcsc-lite\n")

elif IS_WIN:
    info("Windows: for USB HID RFID readers you may need to")
    info("replace the driver with libusb-win32 using Zadig:")
    print("    https://zadig.akeo.ie\n")
    info("PC/SC readers use the built-in Windows Smart Card service.")
    info("Start it: Services → 'Smart Card' → Start\n")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
hdr("Installation Complete")

if not errors:
    ok("All dependencies installed successfully!")
    ok("RFIDIOt is ready to use.")
    print()
    info(f"Source location : {INSTALL_DIR}")
    info(f"Launch app      : python rfid_manager.py")
else:
    warn(f"Installed with some warnings: {', '.join(errors)}")
    warn("The app will run in simulation mode until these are resolved.")
    print()
    info("Common fixes:")
    if "smartcard" in errors:
        if IS_WIN:
            info("  pyscard on Windows: pip install pyscard")
        elif IS_LINUX:
            info("  pyscard on Linux: sudo apt install libpcsclite-dev swig && pip install pyscard")
        elif IS_MAC:
            info("  pyscard on macOS: brew install pcsc-lite swig && pip install pyscard")
    if "rfidiot" in errors:
        info(f"  Manual import: add this to rfid_manager.py startup:")
        info(f"    sys.path.insert(0, '{INSTALL_DIR}')")

print()
input("Press Enter to exit...")
