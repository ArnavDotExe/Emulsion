"""Shared fixtures: synthetic XMP builders and the (optional) private preset folder."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PRIVATE_PRESETS = REPO_ROOT / "presets" / "private"


def make_xmp(attrs: dict[str, str] | None = None, children: str = "") -> str:
    """Build a minimal Lightroom-style XMP preset document.

    ``attrs`` become ``crs:`` attributes on the top-level rdf:Description;
    ``children`` is raw XML placed inside it (curves, look, nested preset...).
    """
    attrs = {"PresetType": "Normal", "UUID": "00000000000000000000000000000001", **(attrs or {})}
    attr_xml = "\n".join(f'   crs:{k}="{v}"' for k, v in attrs.items())
    return f"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
{attr_xml}>
   <crs:Name><rdf:Alt><rdf:li xml:lang="x-default">Test Preset</rdf:li></rdf:Alt></crs:Name>
   <crs:Group><rdf:Alt><rdf:li xml:lang="x-default">Test Group</rdf:li></rdf:Alt></crs:Group>
{children}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def curve_xml(tag: str, points: list[tuple[int, int]]) -> str:
    items = "".join(f"<rdf:li>{x}, {y}</rdf:li>" for x, y in points)
    return f"<crs:{tag}><rdf:Seq>{items}</rdf:Seq></crs:{tag}>"


@pytest.fixture
def private_presets() -> Path:
    if not PRIVATE_PRESETS.is_dir() or not any(PRIVATE_PRESETS.rglob("*.xmp")):
        pytest.skip("private presets not present (gitignored); skipping integration test")
    return PRIVATE_PRESETS
