@echo off
setlocal
title Pluma A1 - Terminal
cd /d "%~dp0"
echo Pluma A1 - http://127.0.0.1:8765
echo Deja esta terminal abierta mientras usas la aplicacion.
echo Aqui se mostraran las solicitudes y los errores del servidor.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Preparando por primera vez...
  py -3.11 -m venv .venv || python -m venv .venv
  if errorlevel 1 goto fallo_preparacion
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fallo_preparacion
)
".venv\Scripts\python.exe" -u app.py
set "pluma_exit=%errorlevel%"
echo.
if not "%pluma_exit%"=="0" echo El inicio termino con un error. Revisa los mensajes de arriba.
echo Si ya habia una copia abierta, sus registros estan en la terminal que la inicio.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b %pluma_exit%

:fallo_preparacion
echo.
echo No se pudo preparar la aplicacion. Revisa el error de arriba.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b 1
