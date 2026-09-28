"""Gera e lê um PDF fictício sem importar a app, filas ou configuração privada.

Na imagem candidata: docker run --rm --network none IMAGEM python -B tools/smoke_pdf.py
O processo usa o utilizador/HOME/sandbox da imagem; nunca eleva privilégios.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main() -> int:
    # Estes dois valores pertencem ao navegador que se pretende verificar.
    # Os restantes valores operacionais, incluindo pastas e credenciais, são
    # eliminados ANTES de qualquer import de código da aplicação.
    browser_environment = {
        key: os.environ[key]
        for key in ("FS_PDF_BROWSER_PATH", "FS_PDF_BROWSER_NO_SANDBOX")
        if key in os.environ
    }
    from tools.test_runtime import configure, block_outbound_network

    runtime = configure()
    os.environ.update(browser_environment)
    os.environ["FS_PDF_TEMP_DIR"] = str(runtime / "browser-temp")
    block_outbound_network()

    from pypdf import PdfReader
    from src.services.local_pdf_service import LocalPdfService

    marker = "SENSORPOINT PDF SMOKE 2026"
    source = runtime / "synthetic.html"
    source.write_text(
        "<!doctype html><html lang='pt'><head><meta charset='utf-8'>"
        "<title>PDF sintético</title><style>@page{size:A4;margin:20mm}"
        "body{font-family:sans-serif}section{break-before:page}</style></head>"
        f"<body><h1>{marker}</h1><p>Cliente fictício. Sem dados operacionais.</p>"
        "<section><h2>Segunda página</h2><p>Verificação de paginação e leitura.</p>"
        "</section></body></html>",
        encoding="utf-8",
    )
    destination = LocalPdfService().export_html_pdf(source, runtime / "synthetic.pdf")
    reader = PdfReader(destination, strict=True)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(reader.pages) != 2 or marker not in text or "Segunda" not in text:
        raise RuntimeError("PDF sintético ilegível ou com paginação/conteúdo inesperados.")
    print(json.dumps({
        "status": "ok",
        "pages": len(reader.pages),
        "bytes": destination.stat().st_size,
        "pdf": str(destination),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "home": str(Path.home()),
        "sandbox_disabled": os.environ.get("FS_PDF_BROWSER_NO_SANDBOX", "false").lower(),
        "isolation": "dados novos, sem .env/filas/app; rede Python bloqueada",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
