"""
Folhas de Serviço — Configuração centralizada.

Todos os caminhos, nomes de sheets e constantes do projecto
devem ser definidos aqui. Nenhum módulo deve hardcode estes valores.
"""

import os
import sys
from pathlib import Path


def _load_env_file() -> None:
    """Carrega um ficheiro .env simples antes de ler a configuração."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = Path(os.environ.get("FS_ENV_FILE", str(project_root / ".env")))
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# ---------------------------------------------------------------------------
# Caminho base da pasta sincronizada (OneDrive/SharePoint)
# Pode ser overridden pela variável de ambiente FS_BASE_PATH
# ---------------------------------------------------------------------------

def _detect_default_base_path() -> Path:
    """Determina a pasta base por defeito para modo script ou executável."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates = [
            Path.cwd(),
            exe_dir.parent.parent,
            exe_dir.parent,
            exe_dir,
        ]
        for candidate in candidates:
            if (candidate / "Excel").exists():
                return candidate
        return exe_dir

    return Path(
        r"C:\Users\joaoc\OneDrive - Sensorpoint - Soluções de Segurança, Lda"
        r"\Documentos\optimização\Folhas de serviço"
    )


_DEFAULT_BASE_PATH = _detect_default_base_path()

BASE_PATH: Path = Path(os.environ.get("FS_BASE_PATH", str(_DEFAULT_BASE_PATH)))

def _default_app_data_dir() -> Path:
    """Guarda dados operacionais fora do OneDrive quando não existe override."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Sensorpoint" / "FolhasServico"
    return BASE_PATH / "data"


APP_DATA_DIR: Path = Path(os.environ.get("FS_APP_DATA_DIR", str(_default_app_data_dir())))

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

AUTH_PROVIDER: str = os.environ.get("FS_AUTH_PROVIDER", "none").strip().lower()
MICROSOFT_AUTH_TENANT_ID: str = os.environ.get(
    "MICROSOFT_AUTH_TENANT_ID",
    os.environ.get("GRAPH_TENANT_ID", ""),
).strip()
MICROSOFT_AUTH_CLIENT_ID: str = os.environ.get(
    "MICROSOFT_AUTH_CLIENT_ID",
    os.environ.get("GRAPH_CLIENT_ID", ""),
).strip()
MICROSOFT_AUTH_CLIENT_SECRET: str = os.environ.get(
    "MICROSOFT_AUTH_CLIENT_SECRET",
    os.environ.get("GRAPH_CLIENT_SECRET", ""),
).strip()
MICROSOFT_AUTH_REDIRECT_URI: str = os.environ.get("MICROSOFT_AUTH_REDIRECT_URI", "").strip()
MICROSOFT_AUTH_ALLOWED_DOMAINS: list[str] = [
    domain.strip().lower().lstrip("@")
    for domain in os.environ.get(
        "MICROSOFT_AUTH_ALLOWED_DOMAINS",
        "sensorpoint.pt,sensorpoint.com",
    ).split(",")
    if domain.strip()
]

# ---------------------------------------------------------------------------
# Backend de armazenamento
# ---------------------------------------------------------------------------

STORAGE_BACKEND: str = os.environ.get("FS_STORAGE_BACKEND", "local").strip().lower()

GRAPH_TENANT_ID: str = os.environ.get("GRAPH_TENANT_ID", "").strip()
GRAPH_CLIENT_ID: str = os.environ.get("GRAPH_CLIENT_ID", "").strip()
GRAPH_CLIENT_SECRET: str = os.environ.get("GRAPH_CLIENT_SECRET", "").strip()
GRAPH_SITE_ID: str = os.environ.get("GRAPH_SITE_ID", "").strip()
GRAPH_DRIVE_ID: str = os.environ.get("GRAPH_DRIVE_ID", "").strip()
GRAPH_ACTIVE_PATH: str = os.environ.get("GRAPH_ACTIVE_PATH", "Activas").strip().strip("/")
GRAPH_ARCHIVE_PATH: str = os.environ.get("GRAPH_ARCHIVE_PATH", "Arquivadas").strip().strip("/")
GRAPH_CACHE_DIR: Path = Path(os.environ.get("GRAPH_CACHE_DIR", str(APP_DATA_DIR / "graph-cache")))

# ---------------------------------------------------------------------------
# Subpastas do projecto
# ---------------------------------------------------------------------------

_DEFAULT_EXCEL_ROOT: Path = GRAPH_CACHE_DIR / "Excel" if STORAGE_BACKEND == "graph" else BASE_PATH / "Excel"
_EXCEL_ROOT: Path = Path(os.environ.get("FS_EXCEL_ROOT", str(_DEFAULT_EXCEL_ROOT)))

EXCEL_ACTIVAS_DIR: Path = _EXCEL_ROOT / "Activas"
EXCEL_ARQUIVADAS_DIR: Path = _EXCEL_ROOT / "Arquivadas"
EXCEL_CANCELADAS_DIR: Path = _EXCEL_ROOT / "Canceladas"

ALL_DIRS: list[Path] = [
    EXCEL_ACTIVAS_DIR,
    EXCEL_ARQUIVADAS_DIR,
    EXCEL_CANCELADAS_DIR,
]

# ---------------------------------------------------------------------------
# Nomes obrigatórios das sheets no Excel
# ---------------------------------------------------------------------------

SHEET_LINK: str = "LINK"
SHEET_TEMPLATE: str = "FS"

REQUIRED_SHEETS: list[str] = [SHEET_LINK, SHEET_TEMPLATE]

# ---------------------------------------------------------------------------
# Estrutura do LINK
# ---------------------------------------------------------------------------

# Linha onde estão os labels dos campos (1-indexed, como no Excel)
LINK_LABELS_ROW: int = 2

# Linha onde estão os dados (1-indexed)
LINK_DATA_ROW: int = 3

# ---------------------------------------------------------------------------
# Naming de ficheiros
# ---------------------------------------------------------------------------

# Extensão esperada dos ficheiros Excel
EXCEL_EXTENSION: str = ".xlsx"

# ---------------------------------------------------------------------------
# Inicialização — criação automática de pastas
# ---------------------------------------------------------------------------


def ensure_directories() -> None:
    """Cria as pastas do projecto se ainda não existirem."""
    for directory in ALL_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
