# Pluma A1 · puesta en marcha

## Actualización: alimentación continua y velocidad (3 de octubre de 2026)

La versión **2026.10.03.14** elimina la espera de telemetría entre grupos normales. Los reportes pueden llegar después de que se consume el recorrido enviado; esperar cada uno puede dejar la punta detenida. El envío sigue esperando el ACK de cada paquete y nunca repite movimientos inciertos. Preparación, pausa, cancelación y final conservan sus barreras. Esta separación coincide con las [mediciones de Bambu Cuts sobre una A1](https://github.com/unrelatedlabs/bambu-cuts#G-code-progress-over-MQTT), sin copiar su código.

Se separa la duración nominal de alimentación del margen para confirmar movimientos. Antes se sumaban un factor de 1.5 y al menos 25 ms por movimiento al presupuesto de alimentación: en trazos cortos podía enviar menos recorrido del que consumía la A1. Ahora esos márgenes sólo amplían los tiempos de confirmación. La ventana nominal no mide la cola física y la fluidez debe comprobarse en esta máquina; el estado registra paquetes y latencias de ACK para ayudar a distinguir problemas de red.

El botón **Velocidad** ofrece Suave, Normal y Rápido y valores manuales de dibujo, viaje, elevación y aceleración. Guardar cambia el próximo programa sin mover la A1, modificar el actual ni invalidar las alturas de calibración. Para lápiz o un soporte flexible empieza con el perfil Suave y comprueba apoyo y detalle antes de aumentar.

Si hay un dibujo en curso durante la actualización, espera a que termine, cierra su terminal, abre **Iniciar Pluma A1.bat** y recarga la página. Debe mostrar **2026.10.03.14**. Las pausas breves para levantar la punta entre trazos separados siguen siendo necesarias.

Verificación: 88 pruebas sin impresora, incluida telemetría intermedia silenciosa, alimentación nominal, conservación de calibración, rechazos de velocidad inválida, pausa/cancelación y transmisión íntegra de una imagen grande. QA del diálogo de velocidad, guardado y límites en escritorio y móvil. Falta comprobar la fluidez física de esta versión.

## Actualización: apoyo de la punta y fotografías (3 de octubre de 2026)

La versión **2026.10.03.13** responde a un dibujo real que dejó zonas muy tenues: el usuario observó que la parte trasera no escribía como la frontal. Ese síntoma es compatible con pérdida de contacto, pero la foto no permite certificar su causa. El programa anterior no activaba explícitamente la compensación al reutilizar «pluma ya ajustada»; ahora envía `G29.2 S1` en ese modo si la nivelación está habilitada. También se activa **después** de `G29` y su barrera, siguiendo el uso del [perfil oficial de la A1](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/machine/Bambu%20Lab%20A1%200.4%20nozzle%20template%20machine_start_gcode.json). Ajustar pluma sondea toda la cama; un dibujo que sondea su propia zona incluye las posiciones de boquilla y punta.

Para comprobarlo: retira o levanta la punta para **Ajustar pluma**, colócala tocando la hoja en la pausa y termina el ajuste. Pulsa **Probar apoyo** para preparar nueve cruces; el botón no mueve la A1. Envíalas desde el diálogo habitual con la pluma ya ajustada. Compara las filas trasera, central y frontal: deben dejar tinta similar. **Volver al diseño** recupera la composición conservada. Si siguen faltando cruces, revisa apoyo de la hoja, altura, rigidez del soporte, recorrido del resorte y estado de la punta. No se aumenta presión automáticamente ni se afirma que activar la malla resuelva todo: su referencia es la boquilla y la punta está desplazada.

**Foto a líneas** conserva límites entre tonos de fotografías y retratos sin relleno ni rayado. Usa un filtro que conserva bordes, umbrales ajustados al contraste real y permite trazos cortos para rasgos pequeños. **Detalle** recupera más líneas; **Limpieza de textura** reduce ruido. Recorta fondos que distraigan. **Una línea** conserva su función para dibujos de líneas oscuras sobre fondo claro; umbralizar una foto puede unir zonas oscuras y deformar sus rasgos. No se reconstruyen detalles ausentes en el original.

El final ya no dice «dibujo terminado». Dice que se envió el recorrido y se recibió la señal final, y pide revisar la tinta. La A1 no mide el contacto de la pluma. La señal final usa 204/205, separados de los checkpoints 200/201; los valores altos tienen [evidencia empírica en A1 mini](https://github.com/sksat/bambu-rs/blob/main/docs/plate-changer.md), y no una garantía universal del fabricante. Se mantiene la barrera final y no se repiten movimientos inciertos.

Verificación: 84 pruebas sin impresora, incluida cobertura de malla con desplazamiento, activación en modo ajustado, nueve zonas, rasgos interiores entre tonos, reducción de ruido, recorte, multicolor y rechazo de un checkpoint retrasado como señal final. QA de escritorio/móvil de Foto a líneas y preparación/retorno de prueba de apoyo. Falta comprobar uniformidad física con las nueve cruces en esta A1.

## Actualización: imágenes grandes (3 de octubre de 2026)

La versión **2026.10.03.12** prepara las imágenes sin mantener una solicitud abierta durante toda la conversión. Muestra la etapa actual y descarta preparaciones anteriores si cambias un ajuste. Un solo trabajador evita acumular conversiones simultáneas; la ordenación espacial conserva los mismos trazos y sus extremos. La vista previa reutiliza el recorrido al mover o girar elementos.

El modo directo regula cuánto recorrido adelanta: una ventana estimada de 12 segundos, con reserva para alimentar la cola antes de que se vacíe. Un paquete largo puede superar la ventana. Los ACK y la espera de finalización reciben margen según el recorrido pendiente estimado; no se añade `M400` entre grupos normales ni se reenvían instrucciones inciertas. Pausa, cancelación y fin mantienen sus barreras físicas. La estimación no prueba la posición real ni reemplaza la confirmación de terminación.

Verificación: 76 pruebas automáticas, incluida una imagen de 4000 × 4000 píxeles convertida, rotada, exportada y transmitida completa a un receptor de prueba, además de 30 000 comandos consecutivos. Se comprobaron progreso y cancelación de conversiones en escritorio y móvil. Ninguna prueba mueve la impresora. La fluidez física debe comprobarse en la A1; el APK no se recompiló.

Comprueba **Versión 2026.10.03.12** en el encabezado de escritorio. Una instancia anterior necesita cerrarse y abrirse de nuevo con **Iniciar Pluma A1.bat** cuando no haya un trabajo activo. Recarga con **Ctrl+F5** y vuelve a cargar las imágenes si se reinició el servidor.

## Actualización: opción Una línea (3 de octubre de 2026)

La versión **2026.10.03.11** muestra **Una línea** (antes Trazo central) como estilo inicial para imágenes nuevas y al restablecer sus ajustes. Sigue el centro de la franja oscura, sin sus dos bordes ni sombreado. Los estilos de imágenes ya guardadas se conservan: selecciona el elemento y pulsa **Una línea** para convertirlo. Formas separadas pueden necesitar varios recorridos. Si el original ya contiene líneas huecas, cada lado puede seguir siendo un trazo separado; no reconstruye automáticamente una firma a partir de sus contornos.

Una instancia antigua sigue usando su conversor anterior aunque los archivos estén actualizados. Para cargar el cambio, cuando termine cualquier trabajo, cierra la terminal anterior, abre **Iniciar Pluma A1.bat**, recarga **Ctrl+F5** y vuelve a cargar la imagen si el programa lo pide. No basta abrir otro BAT mientras sigue activa la versión anterior.

## Actualización: recorte y trazo central (3 de octubre de 2026)

La versión **2026.10.03.10** añade **Recortar imagen…**: mueve el marco, arrastra sus esquinas o escribe porcentajes para elegir el área. Los encuadres rápidos incluyen cuadrado, vertical 3:4 y horizontal 4:3. **Aplicar recorte** cambia la conversión real, su proporción, los trazos exportados y los comandos de dibujo; **Cancelar** conserva lo anterior. **Imagen completa** recupera el original mientras siga cargado en el servidor. El diseño guarda el recorte y los ajustes; después de reiniciar el servidor puede ser necesario volver a cargar la imagen.

Elige **Trazo central** para dibujos de líneas oscuras sobre fondo claro: convierte cada franja en su centro, sin dibujar sus dos bordes. **Oscuridad que se conserva** controla el umbral; subirlo incluye tonos más claros. No convierte cualquier fotografía en una única línea continua: formas separadas y ramificaciones pueden necesitar varios trazos y levantadas. **Sólo bordes** marca contornos sin sombrear; una línea gruesa puede tener dos lados. **Boceto** combina bordes y sombras, y **Sombreado** usa rayado. Los controles del rayado sólo aparecen en los estilos que lo utilizan.

Puedes escribir **X/Y en la hoja**, **ancho**, **alto** y **rotación**. El alto de una imagen cambia su tamaño conservando la proporción; el alto del texto lo determina su contenido. Brillo, contraste, inversión y detalle modifican la conversión. **Restablecer ajustes del dibujo** recupera el estilo inicial sin quitar el recorte, la posición o la rotación. La vista previa sigue mostrando trazos reales; el envío se bloquea si salen del alcance de la pluma.

Verificación: 66 pruebas locales aprobadas, incluida conversión de centro/bordes, umbral, recorte inválido, proporción, original intacto y exportación. Revisión del editor en escritorio y móvil sin conexiones a la impresora; el comportamiento físico sigue pendiente. El APK no se recompiló. Para cargar la actualización, cuando termine cualquier trabajo, cierra la terminal anterior, abre **Iniciar Pluma A1.bat** y recarga con **Ctrl+F5**.

## Actualización: recorrido continuo y rotación (3 de octubre de 2026)

La versión **2026.10.03.9** elimina las esperas que vaciaban la cola de movimiento entre grupos durante el dibujo normal. Los paquetes conservan sus límites de 512 bytes y 24 líneas; se transmiten con ACK individuales y checkpoints de avance del intérprete. Un checkpoint intermedio **no acredita que la pluma haya terminado físicamente**: permite alimentar el siguiente grupo sin insertar una parada `M400`. La conexión y la velocidad de procesamiento del firmware todavía pueden dejar sin movimientos la cola; no se garantiza fluidez física sin comprobarlo en esta A1.

Se exige `M400` con un marcador nuevo antes de declarar una pausa, finalizar, cambiar de capa/pluma o levantar al cancelar. También se mantienen las confirmaciones físicas en homing, nivelación y posicionamientos de origen desconocido. Al pausar/cancelar no se envían nuevos cuerpos: se espera el final de los ya aceptados. Si falta ACK, cambia la conexión o falla la señal, no se repite el movimiento ni se levanta según una posición incierta. Los 20 segundos son el presupuesto nominal **de cada grupo**, no una garantía de tiempo físico ni del horizonte completo de la cola. El porcentaje de envío no equivale a avance físico; sólo el estado FINISH con barrera final confirma terminación.

Selecciona un texto o una imagen para ver **Rotación del elemento**: puedes escribir un ángulo, usar el deslizador, girar ±90° o restablecer a 0°. El giro se hace alrededor del centro del elemento, se guarda con el diseño y se aplica a los trazos reales de SVG/G-code/3MF y al envío. La selección, el arrastre y el cambio de tamaño siguen la orientación nueva. Si al girar salen trazos del alcance, la app bloquea el envío para que puedas moverlos o reducirlos.

Verificación: 58 pruebas locales de geometría, rotación, transmisión, pausas y cancelación aprobadas. La interfaz se probó en escritorio y móvil con conversiones reales y estado de impresora sustituido por datos de prueba: números, giro, composición, arrastre, tamaño y persistencia, sin conexiones ni movimientos físicos. El arranque automático de la terminal fue rechazado por la revisión automática debido a un límite de uso de la cuenta; no se ejecutó. Para cargar estos archivos hay que abrir el BAT y recargar el navegador cuando no haya un dibujo en curso. APK no recompilado.

## Actualización: menos paradas entre trazos (3 de octubre de 2026)

La versión **2026.10.03.8** reduce las esperas entre paquetes del dibujo directo. Antes se vaciaba la cola de movimiento y se esperaba una confirmación tras cada paquete pequeño, lo que hacía que la pluma avanzara, se detuviera y volviera a avanzar. Ahora se pueden transmitir hasta cuatro paquetes consecutivos, confirmando su aceptación individual y esperando el final de ejecución al terminar el grupo. Se conservan los paquetes de hasta 512 bytes y 24 líneas; el recorrido nominal de cada grupo no supera 20 segundos.

Las pausas para ajustar o cambiar la pluma, los cambios de capa, el homing, la nivelación y los primeros posicionamientos de origen desconocido siguen separados. Si pides pausa o cancelación entre paquetes, no se envían los paquetes pendientes: primero se confirma el final de lo ya aceptado. Una respuesta ausente o incierta detiene el envío sin repetir movimientos. Todavía puede haber una parada entre grupos; la mejora busca reducirlas, y la fluidez física debe comprobarse en esta A1.

Comparación sin impresora: un texto de muestra pasó de 29 a 11 esperas de ejecución y una curva de 1 000 segmentos, de 50 a 16. Se verificó que el orden y las coordenadas de los comandos permanecen idénticos. Son un 62 % y un 68 % menos de barreras, respectivamente; estas cifras no son una medición del tiempo real ni de la calidad sobre papel.

## Actualización: comandos directos (3 de octubre de 2026)

La versión **2026.10.03.7** añade **Mover A1…** y usa **Comandos directos (PC encendida)** como ruta inicial de envío. Texto, imágenes, ajuste y ubicación de hoja se transmiten por `gcode_line`. Esta ruta no sube STL, no crea un trabajo de filamento y no depende de un contenedor 3MF ni de microSD.

El programa anterior tampoco enviaba STL: el `.gcode.3mf` contenía instrucciones G-code, miniatura y un modelo 3D vacío. Su orden `project_file` podía ser aceptada sin que se observara arranque. La ruta de archivo se conserva como alternativa; su URL local usa ahora `ftp:///nombre.gcode.3mf`. **G-code puro (compatibilidad)** es otra alternativa desde microSD, pendiente de verificar en este firmware. No hay cambio automático entre rutas cuando una orden queda sin confirmar.

En **Mover A1…**, comienza con **Comprobar comandos (sin movimiento)**. Envía únicamente espera y una señal de estado, sin ejes, extrusión ni calentamiento. Si aparece rechazo de conexión, compara IP, serie y código con la pantalla A1, y verifica sus modos LAN y desarrollador. Introduce el código sólo en Ajustes de la app. [Bambu explica la autorización y el modo desarrollador](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/).

Una vez que la comprobación termine correctamente, los controles X/Y permiten pasos de 0.1, 1, 5 o 10 mm; Z permite 0.1 o 1 mm. Son direcciones de coordenadas G-code: en la A1 la cama se mueve en Y. Requieren referencia de ejes vigente y recorrido libre. **Referenciar ejes…** requiere retirar pluma y hoja; **Bajar a escritura** requiere altura calibrada. Los controles mantienen los límites del firmware y conservan el modo de coordenadas previo, siguiendo [los controles de Bambu Studio](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceCore/DevAxisCtrl.cpp).

El dibujo directo se envía por paquetes pequeños agrupados. Un ACK sólo confirma aceptación. En la versión 2026.10.03.9 los checkpoints intermedios permiten continuar sin `M400`; las pausas y el fin sí exigen barrera física y marcador fresco. Los marcadores 200/201 tienen [evidencia de la comunidad](https://github.com/sksat/bambu-rs/blob/main/docs/plate-changer.md); no constituyen una garantía oficial para todos los firmwares. La comprobación previa debe validarlos en tu A1. Si falla la respuesta o la señal, no se repiten los movimientos. Revisa la máquina y vuelve a comprobar comandos antes de continuar.

Las pausas para colocar/cambiar pluma se reanudan **en esta app**. La PC debe permanecer encendida, conectada y con el programa abierto. Pausa y cancelación detienen los bloques siguientes después de confirmar el bloque enviado; no son una parada de emergencia. La app sólo levanta la pluma al cancelar si conoce una altura confirmada y conserva la conexión. El ajuste queda listo al finalizar, no al pulsar Reanudar. El porcentaje mide comandos confirmados, no distancia dibujada; la estimación de tiempo de recorrido no incluye toda la espera por red.

**Estado real:** después de los rechazos de autorización de las primeras pruebas, el usuario observó movimiento al ajustar la pluma y luego dibujo por comandos directos. Durante la revisión de la lentitud, la app informó un dibujo local RUNNING al 36 %, conectado y sin error de impresora. El usuario describió paradas entre bloques, coherentes con las barreras anteriores. La revisión y las pruebas de la nueva agrupación no inician movimientos físicos; falta comprobar su fluidez y la calidad del resultado en esta A1. El APK existente no se recompiló.

Las secciones siguientes conservan la puesta en marcha y el historial de las pruebas anteriores; para envío directo se aplican las indicaciones de esta actualización.

Abre `Iniciar Pluma A1.bat` y entra en http://127.0.0.1:8765. La terminal permanece visible y muestra las solicitudes y errores mientras funciona el servidor; déjala abierta durante el uso. Si Pluma A1 ya está funcionando, el BAT abre esa misma instancia en el navegador y conserva la ventana hasta que pulses una tecla; los registros siguen en la terminal que inició el servidor. La aplicación convierte texto e imágenes en recorridos reales de pluma, con elevación Z entre trazos. El boceto usa contornos y rayado: la presión no varía para producir grises, y el resultado depende de la punta y el papel.

## Primera prueba

1. En Ajustes, elige A5 o media carta. La cama de la A1 es de 256 × 256 mm y el soporte limita el alcance. Carta y A4 sólo pueden aprovecharse parcialmente con esta versión.
2. Usa «Usar velocidades para primera prueba» y guarda. Las velocidades propuestas son 20 mm/s al escribir y 60 mm/s en viajes; no sustituyen la calibración del soporte.
3. Comprueba desplazamiento X/Y y alturas. Y −35 mm es una estimación, no una medida de tu accesorio. La altura de viaje debe dejar la punta y el soporte libres de papel, cinta y sujetadores.
4. Retira o sube la pluma para el homing. No inicies un homing con la punta sobresaliendo debajo de la boquilla. Si ejecutas la calibración nativa «Nivelar cama», retira también la hoja: esa función puede calentar la boquilla.
5. Ejecuta «Ajustar pluma». Cuando realmente se detenga en pausa, baja la punta hasta tocar el papel sin comprimir excesivamente el resorte. Reanuda y espera a que termine.
6. En Ajustes prepara la marca de prueba. Revisa el alcance antes de enviar. Si conservas la referencia de todos los ejes, puedes marcar «La pluma ya está ajustada». No uses ese modo después de apagar, perder pasos, liberar motores o mover cualquier eje a mano. Este modo no hace homing ni reactiva una compensación de cama que no se haya preparado antes.
7. Mide la cruz: debe estar a 30 mm de la izquierda y 30 mm desde abajo. Introduce las medidas reales y pulsa Corregir → Guardar. Los cambios invalidan el ajuste anterior: vuelve a ajustar la pluma.

El programa no inicia pruebas físicas por sí solo. El homing en frío, G29 y las pausas M400 U1 del perfil requieren comprobarse en la impresora y firmware concretos. Si la impresora no pausa o no permite el homing en frío, detén el trabajo y revisa el perfil antes de colocar la punta. No se considera validado físicamente hasta completar esa prueba.

## Texto e imágenes

Añade texto, escoge letra y modifica tamaño, inclinación y naturalidad. Arrastra los elementos dentro de la zona alcanzable. El editor actual compone una sola hoja: no reparte automáticamente textos largos en varias páginas.

Añade una imagen para generar Trazo central, Sólo bordes, Boceto o Sombreado. El navegador guarda los elementos y sus ajustes, incluido el recorte; si reinicias el servidor, puede ser necesario volver a cargar las imágenes. Para conservar un resultado terminado, exporta SVG o el archivo de máquina.

Los colores corresponden a cambios manuales de pluma, con pausa entre ellos. El AMS no cambia bolígrafos. La simulación es una vista de recorrido, no una predicción mecánica de la impresora.

## Red y exportación

En Ajustes puedes elegir **Material declarado en el archivo: PLA o PETG**. Es una declaración de compatibilidad del paquete: todos los trabajos conservan consumo cero, temperaturas cero y AMS desactivado. No carga una bobina ni cambia el material configurado en la impresora. Cambiarlo invalida trabajos preparados anteriormente. En esta A1 se dejó PETG porque los cuatro espacios del AMS informan PETG; la entrada externa no tiene material definido.

La conexión existente usa FTPS 990 para subir el archivo y MQTT 8883 para órdenes y estado. En Ajustes se introducen IP, serie y código de acceso de la impresora; no hace falta introducir la contraseña de la cuenta Bambu. El código queda en `config.json` local y no se devuelve a la interfaz. No compartas ese archivo ni expongas esta app en Internet; el acceso desde el celular es para una red local de confianza.

Los requisitos de autorización LAN dependen del firmware. Consulta el modo LAN y, si está disponible y es necesario, el modo desarrollador en la pantalla de tu A1. Si se rechaza el inicio, la app lo informa; si el archivo ya se subió, se puede comprobar en la microSD. Una subida no equivale a un inicio confirmado.

- **Descargar archivo:** paquete `.gcode.3mf` con G-code, miniatura y checksum. Su aceptación real requiere una prueba en tu firmware.
- **SVG:** trazos vectoriales en milímetros para revisar o conservar.
- **G-code puro:** disponible en `/api/export?job=ID&fmt=gcode` para inspección. No lo vuelvas a laminar con un perfil normal de impresión, que puede añadir calentamiento y purga.

## Verificación realizada el 3 de octubre de 2026

Ocho pruebas automáticas aprobadas: generación en frío, ausencia de homing en modo ajustado, pausa obligatoria, límites y coordenadas no finitas, validación transaccional de ajustes, caducidad de trabajos, tokens independientes de conversión, texto con acentos, conversión de imagen, exportación SVG y checksum 3MF. Revisión visual de escritorio y móvil, y lectura de estado LAN realizada. No se inició un trabajo físico ni se certificó la precisión del soporte. El APK existente no se recompiló ni se probó en teléfono con estos cambios.

Fuentes: [guía oficial A1](https://cdn1.bambulab.com/documentation/quick-start-a75adcb1d5d5e/Quick%20Start%20Guide%20for%20A1.pdf), [explicación oficial de FTP y MQTT en LAN](https://blog.bambulab.com/answering-network-security-concerns/).

## Diagnóstico de falta de movimiento (3 de octubre de 2026)

La impresora real responde por MQTT y FTPS; los archivos de ubicar/ajustar están en microSD. Firmware leído: 01.08.01.00. El usuario confirmó LAN y modo desarrollador activos. Las órdenes project_file devuelven success, actualizan el nombre y dejan gcode_state=IDLE, sin error de impresión comunicado. Esto no permite afirmar que empezó la ejecución.

Se añadieron CONFIG_BLOCK y EXECUTABLE_BLOCK al G-code, siguiendo la estructura real de Bambu Studio. La configuración completa de referencia proviene de un archivo A1 de la propia microSD, con plantillas de G-code vacías y temperaturas numéricas en cero. Nueve pruebas automáticas aprobadas. También se corrigió el formato de ams_mapping, el nombre de archivo en la orden, la correlación de secuencias y los avisos: aceptación no equivale a inicio. La app comprueba PREPARE/RUNNING/PAUSE y conserva el error en pantalla.

Los diagnósticos con sólo progreso, espera o pausa, sin movimiento ni calentamiento, tampoco produjeron transición de estado. Se compararon paquete propio y contenedor de referencia; no se confirmó una causa única. No se inició ningún homing ni dibujo físico durante este diagnóstico.

Se dejó `PRUEBA_PLUMA_SIN_MOVIMIENTO.gcode.3mf` en la raíz de la microSD y en la carpeta del proyecto. Sólo ejecuta M73 y M400. Al iniciarla manualmente debe detenerse en pausa; después de Reanudar espera seis segundos y finaliza. Comprobar esto desde la pantalla permite distinguir la lectura/ejecución del archivo del inicio por MQTT. Si la pantalla muestra un error, conservar su texto/código. El inicio real de ubicar/ajustar sigue sin verificarse; no marcarlo como resuelto.

Referencia del formato: [generador oficial de G-code de Bambu Studio](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/GCode.cpp).

## Corrección de materiales y arranque (3 de octubre de 2026)

Se eliminó la mezcla heredada de cinco materiales: cabecera, CONFIG y slice_info declaran un solo material con tipo, perfil, densidad e identificador coherentes. La orden usa ams_mapping=[-1] y ams_mapping2=[{"ams_id":255,"slot_id":0}], manteniendo use_ams=false. Referencia de esta asignación: [implementación de bambu-printer-manager](https://github.com/synman/bambu-printer-manager/blob/devel/src/bpm/bambuprinter.py).

Doce pruebas automáticas aprobadas, incluidas coherencia de PLA/PETG en todos los trabajos, ausencia de calentamiento/extrusión y rechazo de materiales desconocidos. PETG guardado y verificado desde la interfaz. Se corrigió también la apertura de varias instancias en Windows: el BAT reutiliza la copia activa. Para cargar cambios futuros, cierra la instancia anterior y vuelve a abrir el BAT.

Las pruebas reales de espera sin movimientos con PLA y PETG siguen recibiendo success pero permanecen IDLE/100%. La lectura de diagnóstico no comunicó errores HMS ni print_error; boquilla y cama tenían objetivo 0 °C. No se confirmó que el tipo de material fuera la causa única ni que ubicar/ajustar ya funcionen.

PRUEBA_PLUMA_SIN_MOVIMIENTO.gcode.3mf se actualizó en PC y microSD con PETG, consumo cero y sólo M73/M400. Falta abrirla desde la pantalla de la A1 y observar si pide selección de material, rechaza el archivo o llega a pausa. No usar un perfil normal para volver a laminarla: añadiría rutinas de impresión.

Reapertura del BAT verificada en la versión 2026.10.03.5: salida 0, mismo proceso servidor y una sola instancia escuchando en 8765. La comprobación de aplicación abierta usa /api/health sin conectar a la impresora; reconoce también versiones anteriores. La ventana sólo queda esperando una tecla cuando hay un error real de arranque. Esta corrección no inicia trabajos en la A1.

## Archivo antiguo llega al 100 % sin moverse

La A1 registró gcode_file=pluma_ajustar_pluma.gcode.3mf, distinto del envío reciente con sufijo 1d45da35. Se descargaron ambos desde microSD: el antiguo tiene sólo HEADER_BLOCK, sin CONFIG_BLOCK ni EXECUTABLE_BLOCK; el reciente contiene los bloques y movimientos. Que la pantalla muestre 100 % no confirma que ejecutara esos movimientos.

Se observó también print_error=0500-4004 (equipo ocupado); una lectura posterior ya informó 0. Se corrigió la lectura de estado: exige una respuesta posterior al pedido, no reutiliza una anterior; los ACK no reemplazan el estado real. La app muestra el código de error, bloquea envíos si la A1 indica un problema y exige un estado nuevo del mismo archivo para confirmar inicio. Espera de inicio ampliada a 15 segundos; clientes MQTT con identificadores únicos. Dieciséis pruebas automáticas aprobadas.

Se dejó AJUSTAR_PLUMA_A1_ACTUAL.gcode.3mf en PC y microSD, y se comprobó leyendo de vuelta que sus bytes coinciden. Incluye activación de motores M17, bloques completos, homing, nivelación y pausa para ajustar la punta, sin calentamiento ni extrusión. El archivo antiguo no se sobrescribió mientras la impresora lo tenía registrado. No se inició el archivo nuevo automáticamente ni se confirmó movimiento físico.

Prueba siguiente: retirar la pluma antes del homing; cerrar el trabajo anterior en la pantalla A1 y seleccionar AJUSTAR_PLUMA_A1_ACTUAL. Debe mover los ejes y llegar a pausa; sólo entonces colocar/ajustar la pluma. Si vuelve a terminar instantáneamente, conservar el nombre exacto y el error mostrado. Referencia del código: [catálogo de errores obtenido de la API de Bambu](https://github.com/synman/bambu-printer-manager/blob/devel/src/bpm/bambucommands.py).
