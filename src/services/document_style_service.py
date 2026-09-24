"""The same embedded branding for service sheets and maintenance checklists."""
import base64
from pathlib import Path


def load_document_assets(static_root):
    static_root = Path(static_root)
    styles = (static_root / "css" / "service-document.css").read_text(encoding="utf-8")
    logo = (static_root / "img" / "sensorpoint-logo.png").read_bytes()
    return styles, "data:image/png;base64," + base64.b64encode(logo).decode("ascii")
