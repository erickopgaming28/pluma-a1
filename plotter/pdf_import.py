"""Render a selected PDF page locally for the existing image tracing workflow."""

import math
import re
import threading

import numpy as np


# Match the server's upload ceiling and the existing image importer resolution.
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_IMAGE_SIDE = 1600
MAX_RENDER_PIXELS = MAX_IMAGE_SIDE * MAX_IMAGE_SIDE
MAX_PAGE_POINTS = 14_400  # 200 inches: the conventional PDF page-size ceiling.
RENDER_SCALE = 2.5  # Up to 180 dpi for smaller pages.
_PDF_LOCK = threading.Lock()


def _page_number(page):
    if isinstance(page, bool):
        raise ValueError("La página del PDF debe ser un número entero desde 1.")
    if isinstance(page, int):
        number = page
    elif isinstance(page, str) and re.fullmatch(r"[0-9]+", page.strip()):
        # Bound parsing too: the form field is untrusted input.
        if len(page.strip()) > 6:
            raise ValueError("La página solicitada no existe en este PDF.")
        number = int(page.strip())
    else:
        raise ValueError("La página del PDF debe ser un número entero desde 1.")
    if number < 1:
        raise ValueError("La página del PDF debe ser un número entero desde 1.")
    return number


def pdf_to_image(data, page=1):
    """Return RGB pixels and page metadata; ``page`` is one-based.

    This is a real PDF rasterization, not circuit-netlist or vector conversion.
    Width/height describe the visible, rotated page in standard PDF points.
    Pixels are copied before closing PDFium's resources so callers can cache them.
    """
    selected = _page_number(page)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("No llegó ningún PDF.")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("El PDF supera el límite de 20 MB.")
    if b"%PDF-" not in data[:1024]:
        raise ValueError("Ese archivo no parece ser un PDF válido.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ValueError(
            "Falta el lector de PDF (pypdfium2). Actualiza las dependencias de la aplicación."
        ) from exc

    # The HTTP server is threaded; serialize the native PDFium rendering calls.
    with _PDF_LOCK:
        return _render_pdf(pdfium, bytes(data), selected)


def _render_pdf(pdfium, data, selected):

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise ValueError("El PDF tiene contraseña. Importa una copia desbloqueada.") from exc
        raise ValueError("No pude abrir el PDF: el archivo está dañado o no es compatible.") from exc

    try:
        pages = len(document)
        if pages < 1:
            raise ValueError("El PDF no contiene páginas.")
        if pages > MAX_PDF_PAGES:
            raise ValueError("El PDF supera el límite de 200 páginas. Exporta solo las necesarias.")
        if selected > pages:
            raise ValueError(f"La página {selected} no existe; el PDF tiene {pages} páginas.")
        pdf_page = document[selected - 1]
        try:
            width, height = pdf_page.get_size()
            if not all(math.isfinite(v) and 0 < v <= MAX_PAGE_POINTS for v in (width, height)):
                raise ValueError("La página del PDF tiene dimensiones inválidas o demasiado grandes.")
            scale = min(RENDER_SCALE, MAX_IMAGE_SIDE / max(width, height))
            # Explicit preflight prevents allocating an oversized native bitmap.
            pixel_width, pixel_height = math.ceil(width * scale), math.ceil(height * scale)
            if pixel_width * pixel_height > MAX_RENDER_PIXELS:
                raise ValueError("La página excede el límite de píxeles para importar un PDF.")
            bitmap = pdf_page.render(
                scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255),
                force_bitmap_format=pdfium.raw.FPDFBitmap_BGR,
                limit_image_cache=True,
            )
            try:
                rgb = np.array(bitmap.to_numpy(), dtype=np.uint8, copy=True)
            finally:
                bitmap.close()
        finally:
            pdf_page.close()
    except pdfium.PdfiumError as exc:
        raise ValueError("No pude dibujar esa página del PDF. Prueba exportarla como imagen.") from exc
    finally:
        document.close()

    warnings = [
        "El PDF se importa como imagen para trazar; no conserva conexiones eléctricas editables.",
        "Confirma la escala del plano antes de usar sus medidas: el documento puede estar reducido.",
    ]
    if pages > 1:
        warnings.append(f"Se importó la página {selected} de {pages}; las otras páginas no se añadieron.")
    if scale < RENDER_SCALE:
        warnings.append("La imagen se ajustó a un máximo de 1600 píxeles por lado.")
    return {
        "image": rgb,
        "pages": pages,
        "page": selected,
        "width_mm": round(width * 25.4 / 72, 6),
        "height_mm": round(height * 25.4 / 72, 6),
        "warnings": warnings,
    }
