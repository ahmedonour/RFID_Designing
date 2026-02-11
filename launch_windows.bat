@echo off
title RFID Asset Manager — Startup
echo ==========================================
echo   RFID Asset Manager — Windows Launcher
echo ==========================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found.
    echo Download from https://python.org ^(check "Add to PATH"^)
    pause & exit /b 1
)

echo [OK] Python found
echo.
echo Checking dependencies...

python -c "import customtkinter" 2>nul || (
    echo Installing customtkinter...
    pip install customtkinter
)
python -c "import PIL" 2>nul || (
    echo Installing Pillow...
    pip install Pillow
)
python -c "import qrcode" 2>nul || (
    echo Installing qrcode...
    pip install qrcode[pil]
)
python -c "import reportlab" 2>nul || (
    echo Installing reportlab...
    pip install reportlab
)
python -c "import win32print" 2>nul || (
    echo Installing pywin32 ^(needed for USB printing^)...
    pip install pywin32
)

echo.
echo [OK] All dependencies ready
echo Launching RFID Asset Manager...
echo.

python rfid_manager.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] App crashed. See error above.
    pause
)
