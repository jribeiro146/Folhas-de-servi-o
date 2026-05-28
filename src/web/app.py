"""
Folhas de Serviço — Aplicação Web (Flask)

Controladores web para a interface da aplicação local.
Comunica diretamente com as camadas existentes de serviço (Fase 1).
"""

from src.web.application import app, create_app

__all__ = ["app", "create_app"]
