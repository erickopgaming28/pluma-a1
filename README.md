<div align="center">

<img src="docs/assets/cover.svg" alt="Pluma A1 — Del diseño al papel" width="100%">

**Tu Bambu Lab A1 también puede escribir.**

Texto manuscrito, dibujos de una línea y composición sobre una hoja, desde una app local en español.

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-2743b8?style=flat-square)](#empezar-en-windows)
[![Interfaz](https://img.shields.io/badge/Interfaz-Espa%C3%B1ol-2743b8?style=flat-square)](#crear-tu-dise%C3%B1o)
[![Pruebas](https://github.com/erickopgaming28/pluma-a1/actions/workflows/tests.yml/badge.svg)](https://github.com/erickopgaming28/pluma-a1/actions/workflows/tests.yml)

[Empezar](#empezar-en-windows) · [Funciones](#crear-tu-dise%C3%B1o) · [Guía de la A1](GUIA-A1.md) · [Reportar un problema](https://github.com/erickopgaming28/pluma-a1/issues)

</div>

## Del diseño al papel

Pluma A1 convierte texto e imágenes en recorridos de pluma para una Bambu Lab A1 con soporte de bolígrafo. Coloca los elementos, revisa los trazos y envía comandos de movimiento desde tu PC por la red local.

![Editor de Pluma A1 con los elementos, la hoja y los controles de preparación y envío](docs/assets/editor.png)

El editor separa la creación del diseño, la preparación de la A1 y el envío. Puedes mover los elementos libremente, incluso fuera de la hoja; el envío se bloquea mientras haya trazos fuera del alcance de la pluma. Puedes plegar **Posición, tamaño y rotación** para concentrarte en el texto o la imagen. La vista previa muestra las dimensiones de tu hoja y la interfaz se adapta a computadora, celular y al tema claro u oscuro del sistema.

> **Estado:** proyecto experimental. Hay 109 pruebas del motor, pruebas de historial/importación/ajuste y revisión del editor en escritorio y móvil. Incluyen una imagen de 4000 × 4000 píxeles convertida, rotada, exportada y transmitida íntegramente a un receptor de prueba. El usuario observó dibujos con tinta desigual entre zonas y pausas durante la escritura; la uniformidad de contacto, fluidez y precisión todavía requieren comprobación física. Las capturas usan diseños de prueba, sin conexión a una impresora.

## Crear tu diseño

| Puedes… | Así funciona |
| :--- | :--- |
| **Escribir a mano** | Fuentes de trazos, tamaño, interlineado, inclinación y variación del pulso. |
| **Dibujar con una línea** | Sigue el centro de las franjas oscuras, sin contorno doble ni sombreado. |
| **Elegir el acabado** | Una línea, sólo bordes, boceto o sombreado por rayado. |
| **Convertir fotografías** | Foto a líneas conserva cambios de tono sin sombreado, con limpieza de textura ajustable. |
| **Dibujar retratos con sombras** | Tonos suaves mediante trazos cortos o rayado con doce niveles de densidad; conserva volumen y zonas oscuras. |
| **Comprobar el apoyo** | Prepara nueve cruces repartidas entre las zonas trasera, central y frontal. |
| **Recortar imágenes** | Marco arrastrable, esquinas, porcentajes y encuadres rápidos. |
| **Componer la hoja** | Mueve, duplica, redimensiona y rota cada texto o imagen. |
| **Deshacer y rehacer** | Recupera cambios del diseño, incluidos borrados y apertura de otro archivo. |
| **Guardar diseños** | Descarga un archivo editable con las imágenes incluidas y ábrelo en otra sesión. |
| **Centrar y hacer caber** | Acomoda el elemento seleccionado dentro de los márgenes alcanzables, conservando su giro. |
| **Ajustar con precisión** | Controles deslizantes y campos numéricos con explicaciones junto a cada opción. |
| **Elegir apariencia** | Interfaz de color claro, oscura o automática, con colores por función. |
| **Girar directamente** | Arrastra el círculo sobre el elemento o usa sus parámetros de ángulo. |
| **Ampliar los ajustes** | Detalle, contraste, limpieza y sombreado hasta 300 %; los valores superiores a 100 % aparecen en rojo con «extra». |
| **Organizar los márgenes** | Arrastra cada borde por separado o escribe su distancia en milímetros. |
| **Mover la vista** | Desplaza la hoja, acerca o aleja entre 50 % y 400 %, y vuelve a centrarla. |
| **Restablecer una imagen** | Recupera sus ajustes, recorte completo, color, posición, tamaño y giro iniciales guardados. |
| **Cambiar de color** | Capas por pluma y pausas para el cambio manual. |
| **Revisar y conservar** | Simulación de recorrido y exportación SVG, G-code y paquete 3MF. |

### Recorta antes de dibujar

El recorte cambia los trazos reales que recibe la pluma. Puedes ajustar el encuadre y volver al original. Las nuevas imágenes cargadas se conservan en la caché privada local `.image-cache`, excluida de Git, para recuperarlas al reiniciar el servidor. Las imágenes de versiones anteriores a esta caché deben cargarse una vez más. La caché guarda la imagen de trabajo, con un lado máximo de 1600 píxeles; el archivo de origen no se modifica.

### Guarda, abre y recupera tu diseño

La barra superior permite poner un nombre y usar **Guardar diseño**, **Abrir diseño**, **Nuevo**, **Deshacer** y **Rehacer**. El archivo `.pluma.json` incluye los elementos, ajustes y las imágenes de trabajo que usa la app. No incluye la IP, el código de acceso ni la calibración de la impresora. Admite hasta 100 elementos y 64 MB por archivo; los archivos personales de diseño están excluidos de Git.

Al abrirlo, se conserva tu hoja, calibración y conjunto de plumas actual. La app avisa si el tamaño de hoja o los colores difieren; revisa la composición antes de enviar. Si una imagen original falta, vuelve a cargarla antes de guardar. El guardado en el navegador permite continuar en la misma sesión de origen, pero descargar el diseño es la forma de conservar una copia portátil.

El historial conserva hasta 60 estados del diseño durante la sesión y agrupa cambios seguidos de un control. Permite recuperar elementos borrados, un diseño reemplazado o una hoja vaciada con **Nuevo**. No modifica márgenes, configuración ni un trabajo ya enviado. **Ctrl+Z** deshace y **Ctrl+Mayús+Z** o **Ctrl+Y** rehace fuera de campos de escritura; los campos conservan su deshacer habitual. **Ctrl+S** guarda el diseño y **Ctrl+D** duplica el elemento seleccionado. Con la hoja enfocada, las flechas mueven 1 mm; con Mayús, 5 mm.

**Centrar elemento** mueve sin cambiar el tamaño. **Hacer que quepa** reduce y centra en la zona alcanzable dentro de los márgenes, conservando el ángulo. En texto también reduce la letra hasta 4 mm; si no hay espacio suficiente, indica que debes acortar el texto o cambiar la hoja. Los elementos pueden seguir moviéndose fuera de la zona mientras editas, pero el envío mantiene su validación física.

**Cómo empezar** abre la guía del orden de preparación. **Apariencia** permite elegir color claro, oscuro o automático. Cada estilo de dibujo tiene una descripción, y los ajustes admiten valores numéricos exactos además de sus controles deslizantes. Las pantallas bajas permiten desplazar la página para conservar un área de dibujo útil.

![Recorte de una imagen con marco y controles de porcentaje](docs/assets/recorte.png)

**Una línea** funciona mejor con dibujos y letras oscuros sobre fondo claro. Formas separadas o ramificadas pueden necesitar varios recorridos y levantadas. Si el original ya es una figura hueca, sus dos lados pueden seguir siendo trazos distintos.

### Ajustes, márgenes y vista

Los porcentajes de imagen admiten un rango ampliado: detalle, limpieza y sombreado de 0 a 300 %, contraste de −99 a 300 % y brillo de −300 a 300 %. Al superar el 100 % en magnitud, se muestran en rojo con «extra». Un ajuste elevado puede recuperar líneas tenues o aumentar ruido, trazos y tiempo de preparación. La oscuridad de **Una línea** usa un umbral de gris de 1 a 254, no un porcentaje. El rayado admite separación de 0.3 a 10 mm y ángulo de −360 a 360°.

### Retratos y tonos de lápiz

**Sólo bordes** y **Foto a líneas** dejan zonas claras vacías: no representan el volumen de las mejillas, la nariz o el cuello mediante tonos. Para eso usa **Retrato con sombras**. El botón prepara brillo y contraste en cero, detalle 70 %, limpieza 85 %, sombreado 100 % y separación 0.35 mm; selecciona la pluma más oscura y conserva el recorte y la colocación.

- **Tonos suaves** conserva el tono local mediante difusión de error y pequeños trazos orientados. Los tramos oscuros contiguos se unen. Da mayor parecido tonal, con muchas más levantadas.
- **Rayado** intercala doce niveles de líneas y añade una segunda dirección en tonos oscuros si activas el rayado cruzado. Tiene un acabado de líneas más visible y suele necesitar menos levantadas.

Ambos generan recorridos reales exportables, con progreso y cancelación. La difusión no muestrea más fino que los píxeles de trabajo, para limitar memoria incluso en recortes muy alargados. La vista previa del retrato supone una punta de 0.3 mm; no cambia el grosor ni la presión física del instrumento. La A1 mantiene una altura de apoyo fija y aproxima el gris mediante densidad de líneas, así que no reproduce exactamente el sombreado manual del grafito. Revisa el tiempo estimado: un retrato detallado puede requerir horas por las subidas y bajadas de la punta.

### Puntillismo

**Puntillismo** representa luces y sombras mediante puntos independientes, con más puntos en las zonas oscuras. Cada punto genera un contacto real: desplazamiento con la punta levantada, bajada y subida, sin unir puntos con líneas. El botón prepara densidad 100 %, separación 0.7 mm, brillo y contraste neutros y la pluma más oscura; conserva el recorte, tamaño y colocación.

Puedes ajustar **Separación de los puntos** entre 0.3 y 10 mm y **Densidad de los puntos** hasta 300 %, además de limpieza, brillo y contraste. Menor separación aumenta detalle, número de contactos y tiempo. La vista y el SVG suponen puntos de 0.3 mm; la marca física depende de la punta y del apoyo. El envío conserva las levantadas necesarias para cada punto, aunque los comandos se transmitan de forma continua. Admite recorte, rotación, colores, progreso y cancelación; el G-code de un dibujo compuesto sólo de puntos también informa avance por contactos.

**Restablecer ajustes del dibujo** conserva el encuadre y la colocación. **Restablecer imagen original** también elimina el recorte y recupera la colocación, tamaño, giro y color iniciales de ese elemento. En diseños guardados antes de esta función, la colocación existente al cargarlos se toma como punto inicial; no hay un historial anterior de posiciones.

Activa **Editar márgenes** y arrastra los tiradores del centro de los cuatro bordes; también puedes escribir cada margen. Se guardan para los siguientes dibujos, conservando las alturas y la calibración. **Restablecer márgenes** recupera el margen uniforme definido en Ajustes. Estas guías no amplían el alcance físico: puedes colocar elementos fuera de ellas, pero el servidor rechaza cualquier trazo fuera de la zona alcanzable.

**Mover vista** arrastra la hoja sin cambiar las coordenadas del diseño ni la ubicación física del papel. Los botones de acercar y alejar sólo cambian la vista, y **Centrar hoja** restablece el encuadre al 100 %. El círculo superior gira el elemento alrededor de su centro, también cuando la vista está desplazada o ampliada.

## Empezar en Windows

Necesitas **Python 3.11 o 3.12**, una **Bambu Lab A1**, un soporte de pluma y conexión a la misma red local. El modo LAN y la autorización de comandos dependen del firmware; revisa sus opciones en la pantalla de la impresora.

1. Descarga el repositorio con **Code → Download ZIP** y descomprímelo, o clónalo:

   ```powershell
   git clone https://github.com/erickopgaming28/pluma-a1.git
   cd pluma-a1
   ```

2. Abre **Iniciar Pluma A1.bat**. En el primer inicio prepara el entorno e instala las dependencias. Deja la terminal abierta durante el uso.
3. Entra en **http://127.0.0.1:8765**.
4. En **Ajustes**, introduce la IP, el número de serie y el código de acceso LAN de tu A1.
5. Lee [la guía de ajuste y primera prueba](GUIA-A1.md) antes de iniciar movimientos.

### Inicio manual

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

El servidor también escucha en la red local: desde un celular puedes abrir la dirección que aparece en Ajustes. La PC debe permanecer encendida cuando transmite el dibujo.

## Cómo mueve la pluma

La ruta inicial envía **comandos G-code directos por MQTT**. Alimenta la cola de movimientos sin insertar una espera de finalización entre cada grupo normal. Conserva confirmaciones físicas para pausar, cambiar de pluma, cancelar y terminar. La fluidez observada depende de la red y del firmware.

Las imágenes grandes se preparan en un trabajador con mensajes de avance. Cambiar un ajuste descarta la conversión anterior. La ordenación usa un índice espacial a partir de 4096 trazos y la vista previa reutiliza los recorridos al moverlos o girarlos. El envío mantiene una ventana nominal de 12 segundos, con reserva para seguir alimentando la cola; un paquete individual largo puede superar esa ventana. Los grupos normales se envían tras sus ACK, sin esperar telemetría intermedia. El margen de aceleración y procesamiento amplía los tiempos de confirmación, separado del ritmo de alimentación. La ventana no mide la posición ni limita con certeza la cola física; la A1 puede aplazar o rechazar comandos, y el fin sigue exigiendo una confirmación nueva.

La exportación G-code/3MF se conserva como alternativa; que un archivo se suba o una orden sea aceptada no confirma que el firmware lo haya ejecutado. No se envía un modelo STL para dibujar ni se requiere extrusión.

El aviso final confirma que se recibió la señal situada detrás de la barrera del recorrido; **no certifica tinta ni contacto de la punta**. Los marcadores finales 204/205 se separan de los marcadores 200/201 de preparación y pausas para que una señal retrasada no cierre el trabajo. La compensación de cama se activa después del sondeo y también al reutilizar la nivelación con «pluma ya ajustada». **Ajustar pluma** sondea toda la cama; la medición normal incluye el área de la boquilla y la punta desplazada.

### Ajustar la velocidad

Pulsa **Velocidad** junto a las dimensiones de la hoja. Puedes elegir **Suave** (20 mm/s), **Normal** (40 mm/s), **Rápido** (60 mm/s) o **Mega rápido** (80 mm/s), o escribir las velocidades de dibujo y viaje. **Mega rápido** usa los máximos actuales de la app: 80 mm/s de dibujo, 200 mm/s de viaje, 30 mm/s de elevación y 3000 mm/s² de aceleración. En **Elevación y aceleración** ajustas esos movimientos sin cambiar las alturas de apoyo. El perfil se guarda para el próximo dibujo; no modifica el que esté en curso. En puntillismo acelera viajes y elevaciones, pero conserva el contacto separado de cada punto. Los perfiles son puntos de partida, sin certificación para cada soporte o instrumento.

Los cambios se guardan para el **próximo dibujo**, conservando la calibración. No modifican un trabajo ya enviado ni ordenan movimientos. Si actualizas mientras está dibujando, espera a que termine antes de reiniciar la terminal y recargar la página. Elevar la velocidad no elimina las levantadas necesarias entre trazos separados ni garantiza contacto o calidad.

## Antes de la primera marca

- Retira o levanta la punta durante el homing y la nivelación con la boquilla. Colócala al llegar a la pausa de ajuste.
- La pluma debe tocar la hoja a la altura de escritura configurada. Su soporte y desplazamiento respecto a la boquilla determinan el alcance.
- Carta y A4 pueden tener zonas fuera de alcance; la vista previa las indica y el envío rechaza los trazos que no caben.
- Mantén la app abierta hasta finalizar. El porcentaje de transmisión no equivale a progreso físico confirmado.
- Guarda tu código de acceso sólo en Ajustes. `config.json` se genera localmente y queda excluido de Git. Usa la app en una red de confianza, sin exponerla a Internet.

## Desarrollo y pruebas

El motor usa Python, Flask, OpenCV y NumPy; el editor usa HTML, CSS y JavaScript sin compilación.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Las pruebas no conectan a la impresora. Comprueban geometría, recorte, rotación, exportación, límites, transmisión, pausas y cancelación. GitHub Actions las ejecuta con Python 3.11 y 3.12.

Para revisar el editor con Chromium y datos de prueba:

```powershell
npm install
npx playwright install chromium
npm run test:ui
```

Todas las solicitudes del navegador se resuelven con archivos y datos locales de prueba: no inicia un servidor ni manda órdenes a la A1. Las capturas se escriben en `tests/artifacts/`.

| Carpeta | Contenido |
| :--- | :--- |
| `plotter/` | Conversión, geometría, G-code y conexión a la impresora. |
| `static/` | Editor y vista previa. |
| `fonts/` | Fuentes SVG y su licencia original. |
| `tests/` | Pruebas del motor y del editor. |
| `android/` | Proyecto Android experimental; el APK antiguo no contiene estas últimas mejoras. |
| `tools/` | Diagnósticos manuales que requieren una impresora configurada. |

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para preparar cambios. Los créditos de componentes externos están en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Las fuentes conservan sus licencias EMS/Hershey; no se ha asignado una licencia general al código propio del proyecto.

---

Hecho por [erickopgaming28](https://github.com/erickopgaming28). Proyecto independiente, sin afiliación con Bambu Lab.
