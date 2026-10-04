# Diseño de Pluma A1

La interfaz es un taller para operar una pluma o lápiz en una Bambu Lab A1. El diseño usa color para distinguir acciones y herramientas, conserva la hoja blanca como referencia física y permite trabajar con computadora o celular.

## Lenguaje visual

- Azul: cabecera, acción principal de envío y selección del elemento.
- Verde: área de trabajo, guardado y preparación de la punta.
- Coral: conversión y estilos de imagen.
- Violeta: texto y escritura.
- Ámbar: velocidad y atención. Rojo: errores, valores extra y límites.

Los valores se declaran como roles en `static/style.css`, con paletas claras y oscuras explícitas. La apariencia puede elegirse o seguir al sistema. Las etiquetas y la forma de los controles también identifican los estados; el color por sí solo no transmite información.

## Organización

La barra de diseño agrupa nombre, historial y archivos. El panel contiene elementos y opciones de la selección. La hoja mantiene sus herramientas de vista; preparación y revisión están próximas al envío. Los estilos explican su resultado antes de seleccionarlos. Las instrucciones poco frecuentes quedan en desplegables.

Los botones miden al menos 44 px de alto. Los formularios conservan etiquetas visibles, ayuda y foco. En pantallas bajas la página se desplaza para mantener una hoja útil; en celular se presenta primero la hoja y después las opciones.

## Comportamiento

Editar un diseño no mueve la impresora. Deshacer y rehacer afectan sólo al diseño. Los archivos personales incluyen imágenes de trabajo y excluyen credenciales y calibración. Cambiar velocidad afecta al siguiente trabajo. Los elementos pueden salir del área durante la edición; sólo los recorridos físicamente válidos pueden enviarse.

Las comprobaciones automáticas y de navegador no certifican presión, tinta ni movimiento físico. No se sustituye la confirmación del recorrido por un porcentaje de transmisión.
