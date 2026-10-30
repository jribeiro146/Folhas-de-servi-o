"""The same embedded branding for service sheets and maintenance checklists."""
import base64
from pathlib import Path


def load_document_assets(static_root, *, page_context=""):
    static_root = Path(static_root)
    styles = (static_root / "css" / "service-document.css").read_text(encoding="utf-8")
    if page_context:
        # CSS hex escapes preserve Unicode and prevent quotes/HTML from becoming
        # stylesheet syntax. A margin box repeats without overlapping page flow.
        escaped = "".join(f"\\{ord(character):x} " for character in str(page_context))
        styles += '\n@page { @bottom-left { content: "' + escaped + '"; font: 8px "Segoe UI", Arial, sans-serif; color: #6b7f92; width: 85%; } }'
    logo = (static_root / "img" / "sensorpoint-logo.png").read_bytes()
    return styles, "data:image/png;base64," + base64.b64encode(logo).decode("ascii")
