@echo off
setlocal EnableExtensions
title Pluma A1 - Terminal
cd /d "%~dp0"
if errorlevel 1 goto fallo_carpeta
echo Pluma A1 - http://127.0.0.1:8765
echo Deja esta terminal abierta mientras usas la aplicacion.
echo Aqui se mostraran las solicitudes y los errores del servidor.
echo.
set "pluma_python="

rem Una ruta existente no basta: su Python de origen pudo ser desinstalado.
if exist ".venv\Scripts\python.exe" (
  call :probar_python "%~dp0.venv\Scripts\python.exe"
  if not errorlevel 1 set "pluma_python=%~dp0.venv\Scripts\python.exe"
)
if defined pluma_python goto comprobar_dependencias

rem El entorno anterior se conserva intacto cuando su interprete no inicia.
if exist ".venv-runtime\Scripts\python.exe" (
  call :probar_python "%~dp0.venv-runtime\Scripts\python.exe"
  if not errorlevel 1 set "pluma_python=%~dp0.venv-runtime\Scripts\python.exe"
)
if defined pluma_python goto comprobar_dependencias

echo Preparando un entorno local para Pluma A1...
py -c "import sys" >nul 2>&1
if errorlevel 1 goto probar_python_sistema
py -m venv ".venv-runtime"
if errorlevel 1 goto probar_python_sistema
goto usar_runtime

:probar_python_sistema
python -c "import sys" >nul 2>&1
if errorlevel 1 goto falta_python
python -m venv ".venv-runtime"
if errorlevel 1 goto fallo_preparacion

:usar_runtime
call :probar_python "%~dp0.venv-runtime\Scripts\python.exe"
if errorlevel 1 goto fallo_preparacion
set "pluma_python=%~dp0.venv-runtime\Scripts\python.exe"

:comprobar_dependencias
call :probar_dependencias
if not errorlevel 1 goto iniciar
echo Instalando o completando las herramientas de la aplicacion...
"%pluma_python%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto fallo_preparacion
call :probar_dependencias
if errorlevel 1 goto fallo_dependencias

:iniciar
if exist "%~dp0Iniciar IA local.ps1" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar IA local.ps1"
"%pluma_python%" -u "%~dp0app.py"
set "pluma_exit=%errorlevel%"
echo.
if not "%pluma_exit%"=="0" echo El inicio termino con un error. Revisa los mensajes de arriba.
echo Si ya habia una copia abierta, sus registros estan en la terminal que la inicio.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b %pluma_exit%

:probar_python
"%~1" -c "import sys" >nul 2>&1
exit /b %errorlevel%

:probar_dependencias
"%pluma_python%" -c "import flask, numpy, PIL, cv2, paho.mqtt.client, pypdfium2; from cv2 import ximgproc" >nul 2>&1
exit /b %errorlevel%

:falta_python
echo.
echo No se encontro un Python que pueda iniciar.
echo Instala Python y activa la opcion Add python.exe to PATH.
echo Despues vuelve a abrir Iniciar Pluma A1.bat.
echo El entorno anterior .venv y tus archivos se conservan.
goto cerrar_error

:fallo_carpeta
echo No se pudo abrir la carpeta de Pluma A1. Ejecuta este archivo desde su carpeta original.
goto cerrar_error

:fallo_dependencias
echo.
echo Faltan herramientas de Pluma A1 incluso despues de instalarlas.
echo Revisa los errores de instalacion y vuelve a abrir este archivo.
goto cerrar_error

:fallo_preparacion
echo.
echo No se pudo preparar la aplicacion. Revisa el error de arriba.
echo Comprueba tu conexion a Internet y vuelve a abrir este archivo.
:cerrar_error
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b 1
