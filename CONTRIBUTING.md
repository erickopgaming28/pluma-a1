# Contribuir a Pluma A1

Abre un issue con el problema y el resultado esperado, o propone un cambio mediante un pull request.

## Preparar el entorno

Sigue el inicio manual del README. Python 3.11 y 3.12 son las versiones verificadas. Para pruebas de interfaz instala las dependencias de `package.json` y Chromium mediante Playwright.

## Antes de enviar un cambio

1. Ejecuta `python -m unittest discover -s tests`.
2. Si cambia el editor, ejecuta `npm run test:ui` después de instalar Chromium.
3. Explica qué cambió, cómo lo verificaste y si hubo una prueba física. Distingue simulación, ACK de aceptación y ejecución confirmada.
4. Nunca incluyas `config.json`, credenciales LAN, certificados, diagnósticos privados o archivos de máquina provenientes de una impresión personal.

Las pruebas automatizadas deben funcionar sin impresora. No añadas movimientos físicos al importar módulos ni como parte del test suite. Los cambios de geometría y de transmisión necesitan comprobar límites y estados de pausa/cancelación.

## Reportar problemas

Incluye sistema operativo, versión de Python, versión de la app y pasos para reproducir. Para problemas de máquina añade modelo y firmware. Oculta IP, número de serie y código de acceso en registros y capturas. No pegues el archivo de configuración.

El repositorio incluye código Android experimental. Una compilación correcta no equivale a funcionamiento comprobado en un teléfono o una impresora.
