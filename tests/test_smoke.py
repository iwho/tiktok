from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toktok import PolymarketClient
from toktok.main import format_payload


def test_format_payload_pretty_json() -> None:
    rendered = format_payload({"slug": "demo", "active": True})
    assert '"slug": "demo"' in rendered
    assert rendered.startswith("{")
    assert "\n" in rendered


def test_package_exports_client() -> None:
    assert PolymarketClient.__name__ == "PolymarketClient"

