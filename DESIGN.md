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

La barra de diseño agrupa nombre, historial y archivos. El panel de creación contiene elementos, opciones de la selección y los desplegables de preparación de la A1 y ayuda. La hoja conserva sus herramientas de vista, con Regla, Medidas y Editar márgenes como accesos al panel de herramientas. La revisión y el envío quedan al pie de la hoja. Los estilos explican su resultado antes de seleccionarlos.

En pantallas de al menos 1280 px, la hoja ocupa el centro y el panel de herramientas se abre a su derecha; ambos paneles laterales tienen desplazamiento independiente. En pantallas más estrechas, las herramientas aparecen debajo de la hoja. La ayuda de un diseño vacío es una franja bajo el lienzo. Los controles avanzados permanecen en desplegables para conservar espacio de dibujo.

Los botones miden al menos 44 px de alto. Los formularios conservan etiquetas visibles, ayuda y foco. En pantallas bajas la página se desplaza para mantener una hoja útil; en celular se presenta primero la hoja y después las opciones.

## Comportamiento

Editar un diseño no mueve la impresora. Deshacer y rehacer afectan sólo al diseño. Los archivos personales incluyen imágenes de trabajo y excluyen credenciales y calibración. Cambiar velocidad afecta al siguiente trabajo. Los elementos pueden salir del área durante la edición; sólo los recorridos físicamente válidos pueden enviarse.

Las comprobaciones automáticas y de navegador no certifican presión, tinta ni movimiento físico. No se sustituye la confirmación del recorrido por un porcentaje de transmisión.
