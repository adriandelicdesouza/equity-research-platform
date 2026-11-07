"""Synthetic US Treasury and Bank of Canada payloads (shape per Blueprint §0.4; values invented).

Nothing here is real market data: the numbers are made up to exercise parsing, and the
layouts follow the documented provider formats (D58).
"""

from __future__ import annotations

import json
from typing import Any

TREASURY_HEADER = (
    'Date,"1 Mo","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr",'
    '"20 Yr","30 Yr"'
)


def treasury_csv(*rows: str, header: str = TREASURY_HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


# Newest first, like the published file. 2024-03-01 is a Friday.
TREASURY_GOOD = treasury_csv(
    "03/05/2024,5.50,5.51,5.48,5.45,5.38,5.02,4.55,4.37,4.17,4.17,4.15,4.42,4.29",
    "03/04/2024,5.49,5.50,5.47,5.44,5.37,5.01,4.54,4.36,4.16,4.16,4.14,4.41,4.28",
    "03/01/2024,5.48,5.49,5.46,5.43,5.36,5.00,4.53,4.35,4.15,4.15,4.13,4.40,4.27",
)


def valet(
    observations: list[dict[str, Any]],
    details: dict[str, dict[str, Any]] | None = None,
) -> bytes:
    if details is None:
        details = {
            "FXUSDCAD": {
                "label": "USD/CAD",
                "description": "US dollar to Canadian dollar daily exchange rate",
                "dimension": {"key": "d", "name": "date"},
            },
            "BD.CDN.10YR.DQ.YLD": {
                "label": "10 year",
                "description": "Government of Canada benchmark bond yields 10 year",
                "dimension": {"key": "d", "name": "date"},
            },
        }
    return json.dumps(
        {
            "terms": {"url": "https://example.invalid/terms"},
            "seriesDetail": details,
            "observations": observations,
        }
    ).encode()


VALET_GOOD = valet(
    [
        {"d": "2024-03-01", "FXUSDCAD": {"v": "1.3550"}, "BD.CDN.10YR.DQ.YLD": {"v": "3.41"}},
        {"d": "2024-03-04", "FXUSDCAD": {"v": "1.3562"}, "BD.CDN.10YR.DQ.YLD": {"v": "3.44"}},
        {"d": "2024-03-05", "FXUSDCAD": {"v": "1.3571"}, "BD.CDN.10YR.DQ.YLD": {"v": "3.47"}},
    ]
)
