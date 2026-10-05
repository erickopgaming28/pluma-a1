@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar IA local.ps1"
echo.
echo En Pluma A1, abre Preparar con IA local y pulsa Buscar modelos.
pause
