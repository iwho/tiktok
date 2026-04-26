from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import toktok.main as main_module
from toktok.exceptions import PolymarketNotFoundError


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.payload = kwargs.pop("payload", None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_market_by_slug(self, slug: str):
        return {
            "slug": slug,
            "question": "Sample market",
        }


def test_main_prints_market_payload(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main_module, "PolymarketClient", FakeClient)

    assert main_module.main(["sample-market"]) == 0

    captured = capsys.readouterr()
    assert '"slug": "sample-market"' in captured.out
    assert captured.err == ""


def test_main_requires_slug(capsys) -> None:
    assert main_module.main([]) == 2

    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()


def test_main_returns_error_for_api_failure(monkeypatch, capsys) -> None:
    class NotFoundClient(FakeClient):
        def get_market_by_slug(self, slug: str):
            raise PolymarketNotFoundError(slug)

    monkeypatch.setattr(main_module, "PolymarketClient", NotFoundClient)

    assert main_module.main(["missing-market"]) == 1

    captured = capsys.readouterr()
    assert "missing-market" in captured.err


def test_main_trade_loop_requires_private_key(capsys) -> None:
    assert main_module.main(["--trade-loop"]) == 1

    captured = capsys.readouterr()
    assert "TOKTOK_PRIVATE_KEY" in captured.err


