"""The header's and the card controls' icons, as inline SVG drawn in the text
colour.

The shapes are Lucide's (lucide-static 0.454.0, https://lucide.dev), ISC
licence, Copyright (c) Lucide Contributors; portions MIT, Copyright (c) Cole
Bemis (Feather). A leaf: it imports nothing from the dashboard.
"""

# Each icon's <path> d attributes, in Lucide's order.
PATHS = {
    "refresh-cw": (
        "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8",
        "M21 3v5h-5",
        "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16",
        "M8 16H3v5",
    ),
    "rocket": (
        "M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z",
        "m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z",
        "M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0",
        "M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5",
    ),
    "zap": (
        "M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z",
    ),
    "brain": (
        "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z",
        "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z",
        "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4",
        "M17.599 6.5a3 3 0 0 0 .399-1.375",
        "M6.003 5.125A3 3 0 0 0 6.401 6.5",
        "M3.477 10.896a4 4 0 0 1 .585-.396",
        "M19.938 10.5a4 4 0 0 1 .585.396",
        "M6 18a4 4 0 0 1-1.967-.516",
        "M19.967 17.484A4 4 0 0 1 18 18",
    ),
    "sparkles": (
        "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z",
        "M20 3v4",
        "M22 5h-4",
        "M4 17v2",
        "M5 18H3",
    ),
    "pin": (
        "M12 17v5",
        "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z",
    ),
    "pin-off": (
        "M12 17v5",
        "M15 9.34V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H7.89",
        "m2 2 20 20",
        "M9 9v1.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h11",
    ),
    "alarm-clock": (
        # Lucide's <circle cx="12" cy="13" r="8"/>, as a path.
        "M20 13a8 8 0 1 1-16 0a8 8 0 1 1 16 0",
        "M12 9v4l2 2",
        "M5 3 2 6",
        "m22 6-3-3",
        "M6.38 18.7 4 21",
        "M17.64 18.67 20 21",
    ),
    "circle-help": (
        # Lucide's <circle cx="12" cy="12" r="10"/>, as a path.
        "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
        "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3",
        "M12 17h.01",
    ),
    "eye-off": (
        "M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49",
        "M14.084 14.158a3 3 0 0 1-4.242-4.242",
        "M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143",
        "m2 2 20 20",
    ),
    "alarm-clock-off": (
        "M6.87 6.87a8 8 0 1 0 11.26 11.26",
        "M19.9 14.25a8 8 0 0 0-9.15-9.15",
        "m22 6-3-3",
        "M6.26 18.67 4 21",
        "m2 2 20 20",
        "M4 4 2 6",
    ),
}


def svg(name: str, cls: str = "glyph") -> str:
    """Icon `name`, 16px, hidden from screen readers: the control around it
    carries the name."""
    paths = "".join(f'<path d="{d}"/>' for d in PATHS[name])
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" width="16" height="16" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        f' stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )
