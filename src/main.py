"""
Ponto de entrada da aplicação web local de Folhas de Serviço.

Garante a estrutura de pastas, escolhe uma porta livre e inicia o servidor
Flask local, abrindo o browser por defeito na interface da webapp.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import ensure_directories
from src.web.application import app

DEFAULT_HOST = os.environ.get("FS_HOST", "localhost")
DEFAULT_PORT = int(os.environ.get("FS_PORT", "5001"))
MAX_PORT_ATTEMPTS = 20


def pick_port(start_port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, max_attempts: int = MAX_PORT_ATTEMPTS) -> int:
    """Escolhe a primeira porta livre a partir da porta preferida."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port

    end_port = start_port + max_attempts - 1
    raise RuntimeError(f"Não foi encontrada nenhuma porta livre entre {start_port} e {end_port}.")


def open_browser(url: str) -> None:
    """Abre o browser por defeito após um pequeno atraso."""
    if os.environ.get("FS_NO_BROWSER") == "1":
        return

    time.sleep(1)
    webbrowser.open(url)


def main() -> None:
    """Arranque principal do servidor local e da interface web."""
    try:
        ensure_directories()
        probe_host = "127.0.0.1" if DEFAULT_HOST == "0.0.0.0" else DEFAULT_HOST
        display_host = "localhost" if DEFAULT_HOST == "0.0.0.0" else DEFAULT_HOST
        port = pick_port(host=probe_host)
        url = f"http://{display_host}:{port}"

        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

        print(f"Servidor web local ativo em {url}")
        app.run(host=DEFAULT_HOST, port=port, debug=False, use_reloader=False)
    except Exception as exc:
        print(f"Erro crítico ao arrancar a aplicação: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
