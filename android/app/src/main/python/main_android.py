"""Arranque del motor de Pluma A1 dentro de Android.

Reutiliza app.py tal cual y sólo ajusta lo que cambia en el teléfono: dónde están los
archivos, dónde se guarda la configuración y cómo se averigua el número de serie."""
import os
import ssl
import socket
import tempfile
from pathlib import Path


def serial_from_printer(ip):
    """El certificado TLS de la impresora lleva su número de serie como nombre."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((ip, 8883), timeout=5) as raw, ctx.wrap_socket(raw) as tls:
        pem = ssl.DER_cert_to_PEM_cert(tls.getpeercert(True))
    fd, name = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        subject = ssl._ssl._test_decode_cert(name).get("subject", ())
    finally:
        os.unlink(name)
    for part in subject:
        for key, value in part:
            if key == "commonName":
                return value
    return ""


def run(base):
    base = Path(base)
    import app as A
    from flask import request
    from plotter import fonts

    fonts.FONT_DIR = base / "fonts"
    A.app.static_folder = str(base / "static")
    A.CONFIG_FILE = base / "config.json"
    A.config.clear()
    A.config.update(A.load_config())
    A.lan_url = lambda: ""  # la app del teléfono no se comparte con otros dispositivos

    # en el teléfono no hay búsqueda automática: con sólo la IP se rellena el número de serie
    save_config = A.app.view_functions["api_config"]

    def api_config():
        p = (request.get_json(force=True) or {}).get("printer") or {}
        if p.get("ip") and not p.get("serial"):
            try:
                p["serial"] = serial_from_printer(p["ip"])
            except Exception:
                pass
        return save_config()

    A.app.view_functions["api_config"] = api_config
    A.app.run(host="127.0.0.1", port=A.PORT, threaded=True)
