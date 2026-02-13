@echo off
title RFIDIOt Installer
color 0A
echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║        RFIDIOt Installer — Windows                   ║
echo  ║        github.com/AdamLaurie/RFIDIOt                 ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found.
    echo  Download from: https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo  [OK] Python found: 
python --version
echo.

:: Run installer
echo  Starting RFIDIOt installer...
echo.
python install_rfidiot.py

echo.
if %errorlevel% equ 0 (
    echo  [SUCCESS] Installation complete!
    echo  You can now run: python rfid_manager.py
) else (
    echo  [WARNING] Installer finished with some issues.
    echo  Check the output above for details.
)
echo.
pause
