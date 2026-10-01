"""Retry only transient Windows denial of a local atomic replacement."""
import os
import time
from pathlib import Path


def replace_local(source: Path, destination: Path) -> None:
    for attempt in range(8):
        try:
            os.replace(str(source), str(destination))
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 7:
                raise
            time.sleep(min(.01 * (2 ** attempt), .1))
