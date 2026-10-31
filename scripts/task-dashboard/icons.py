"""The header's icons, and the cards' kind, status, CI and control icons, as
inline SVG drawn in the text colour.

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
    "list-checks": (
        "m3 17 2 2 4-4",
        "m3 7 2 2 4-4",
        "M13 6h8",
        "M13 12h8",
        "M13 18h8",
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
    # The card's leading icons and CI marks below. Lucide draws them with
    # <circle>, <line>, <polyline> and <rect>; each is written as a path.
    "git-pull-request": (
        "M21 18a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M9 6a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M13 6h3a2 2 0 0 1 2 2v7",
        "M6 9v12",
    ),
    "git-pull-request-draft": (
        "M21 18a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M9 6a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M18 6V5",
        "M18 11v-1",
        "M6 9v12",
    ),
    "circle": ("M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",),
    "circle-dot": (
        "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
        "M13 12a1 1 0 1 1-2 0a1 1 0 1 1 2 0",
    ),
    # Not Lucide's: its circle with the right half filled, for in progress.
    "circle-half": (
        "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
        "fill:M12 2a10 10 0 0 1 0 20z",
    ),
    "circle-check": (
        "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
        "m9 12 2 2 4-4",
    ),
    "circle-slash": (
        "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
        "M9 15 15 9",
    ),
    "circle-dashed": (
        "M10.1 2.182a10 10 0 0 1 3.8 0",
        "M13.9 21.818a10 10 0 0 1-3.8 0",
        "M17.609 3.721a10 10 0 0 1 2.69 2.7",
        "M2.182 13.9a10 10 0 0 1 0-3.8",
        "M20.279 17.609a10 10 0 0 1-2.7 2.69",
        "M21.818 10.1a10 10 0 0 1 0 3.8",
        "M3.721 6.391a10 10 0 0 1 2.7-2.69",
        "M6.391 20.279a10 10 0 0 1-2.69-2.7",
    ),
    "terminal": ("M4 17 10 11 4 5", "M12 19h8"),
    "git-branch": (
        "M6 3v12",
        "M21 6a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M9 18a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
        "M18 9a9 9 0 0 1-9 9",
    ),
    "file-text": (
        "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z",
        "M14 2v4a2 2 0 0 0 2 2h4",
        "M10 9H8",
        "M16 13H8",
        "M16 17H8",
    ),
    "layers": (
        "m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z",
        "m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65",
        "m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65",
    ),
    "pen-tool": (
        "M15.707 21.293a1 1 0 0 1-1.414 0l-1.586-1.586a1 1 0 0 1 0-1.414l5.586-5.586a1 1 0 0 1 1.414 0l1.586 1.586a1 1 0 0 1 0 1.414z",
        "m18 13-1.375-6.874a1 1 0 0 0-.746-.776L3.235 2.028a1 1 0 0 0-1.207 1.207L5.35 15.879a1 1 0 0 0 .776.746L13 18",
        "m2.3 2.3 7.286 7.286",
        "M13 11a2 2 0 1 1-4 0a2 2 0 1 1 4 0",
    ),
    "check": ("M20 6 9 17l-5-5",),
    "x": ("M18 6 6 18", "m6 6 12 12"),
    # Not Lucide's: a filled dot, GitHub's mark for a check still running.
    "dot-fill": ("fill:M17 12a5 5 0 1 1-10 0a5 5 0 1 1 10 0",),
}


def _path(d: str) -> str:
    # "fill:" marks a shape drawn solid in the text colour, not stroked.
    if d.startswith("fill:"):
        return f'<path d="{d[5:]}" fill="currentColor" stroke="none"/>'
    return f'<path d="{d}"/>'


def svg(name: str, cls: str = "glyph") -> str:
    """Icon `name`, 16px, hidden from screen readers: the control around it
    carries the name."""
    paths = "".join(_path(d) for d in PATHS[name])
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" width="16" height="16" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        f' stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )
