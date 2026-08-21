#!/usr/bin/env python3
"""Local PR review UI. GitHub-style split diff, line comments, submit-to-file.

Usage:
  python3 server.py <pr-number> [--repo OWNER/REPO] [--port 8765] [--out PATH]
  python3 server.py --diff-file some.patch [--port 8765] [--out PATH]
  python3 server.py --git uncommitted|<ref>|<A>...<B> [--port 8765] [--out PATH]

On Submit the page POSTs all comments to /submit; the server writes them to
--out (JSON) and prints them to stdout. Watch that file to collect the review.
"""
import argparse
import errno
import hashlib
import http.cookies
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Human-readable token words: 4 are drawn via secrets.choice() and joined with
# hyphens (4 * 10 bits = 40 bits of entropy — a deliberate reduction from the
# 128-bit secrets.token_urlsafe(16) this replaces, traded for a URL a human
# can read and retype). All lowercase, 3-7 letters, [a-z]+ only, no
# duplicates. The assert below is cheap and prevents a typo'd duplicate from
# silently shrinking the entropy.
WORDLIST = (
    "abbey", "actor", "adjust", "agate", "almond", "amber", "anchor", "ancient",
    "anise", "ankle", "ant", "ape", "apple", "apricot", "apron", "arc",
    "archery", "argue", "arid", "arm", "arrive", "arrow", "artist", "ash",
    "asp", "autumn", "axe", "badge", "badger", "bag", "bagel", "bake",
    "baker", "balmy", "bamboo", "banana", "band", "banjo", "banner", "barber",
    "barley", "baron", "barrel", "barter", "basalt", "basil", "basket", "bass",
    "bat", "bay", "beach", "beacon", "bean", "bear", "beaver", "bee",
    "beetle", "begin", "beige", "bell", "belt", "bend", "bendy", "berry",
    "big", "bike", "birch", "bitter", "black", "blanket", "blend", "blender",
    "blind", "blink", "bloom", "blossom", "blouse", "blue", "boat", "boil",
    "bold", "bolt", "boot", "boulder", "bounce", "bow", "bowl", "bowling",
    "boxing", "boxy", "brass", "brave", "bread", "bream", "breezy", "brew",
    "brick", "bridge", "bright", "brisk", "broad", "bronze", "brook", "broth",
    "brow", "brown", "bucket", "buckle", "build", "builder", "bulb", "bumpy",
    "burly", "bus", "butter", "button", "cabbage", "cabin", "cabinet", "cable",
    "cactus", "calf", "calm", "camel", "camping", "candle", "canoe", "canvas",
    "canyon", "cap", "car", "carp", "carpet", "carrot", "carry", "cart",
    "carve", "cascade", "cashew", "castle", "cat", "catfish", "cave", "cavern",
    "cedar", "ceiling", "celery", "cello", "ceramic", "chain", "chair", "chant",
    "chapel", "char", "chart", "chat", "check", "cheek", "cheese", "chef",
    "cherry", "chess", "chest", "chewy", "chick", "chilly", "chime", "chin",
    "chisel", "chop", "cider", "circle", "clam", "clamp", "classic", "clay",
    "clever", "cliff", "climb", "close", "cloud", "cloudy", "clove", "clover",
    "cloves", "coat", "cobra", "cocoa", "coconut", "cod", "coffee", "coin",
    "collect", "comet", "compass", "condor", "cone", "cook", "cooker", "cookie",
    "cool", "copper", "coral", "corn", "cottage", "cotton", "couch", "count",
    "cove", "cow", "cozy", "crab", "cracker", "craft", "crane", "crate",
    "cream", "creamy", "creek", "crest", "cricket", "crimson", "crisp", "crow",
    "crown", "crunchy", "crystal", "cub", "cube", "cumin", "cup", "curtain",
    "curve", "curved", "cyan", "cycling", "dagger", "daisy", "dancer", "dart",
    "dash", "date", "dawn", "debate", "decade", "deep", "deer", "delta",
    "denim", "depart", "desert", "design", "desk", "dew", "diamond", "dice",
    "dill", "dim", "discuss", "distant", "dive", "diving", "dock", "doctor",
    "dodge", "doe", "dog", "dolphin", "domino", "donkey", "door", "dot",
    "double", "dove", "dozen", "drag", "dragon", "drape", "draw", "drawer",
    "dreary", "dress", "drill", "driver", "drum", "duck", "duke", "dull",
    "dune", "dusk", "dust", "eager", "eagle", "ear", "earn", "ebony",
    "eel", "eerie", "egret", "eight", "elbow", "elk", "elm", "emblem",
    "emerald", "emu", "end", "enter", "estuary", "etch", "evening", "ewe",
    "exit", "eye", "fabric", "fade", "faint", "falcon", "fancy", "farmer",
    "fawn", "fence", "fencing", "fennel", "fern", "ferret", "ferry", "field",
    "fig", "file", "finch", "finger", "finish", "fish", "fishing", "five",
    "fix", "fizzy", "flag", "flare", "flat", "flicker", "flimsy", "flint",
    "float", "floor", "flour", "flute", "fly", "fog", "foggy", "foot",
    "forest", "forge", "fork", "four", "fox", "fragile", "freezer", "fresh",
    "fridge", "frog", "frost", "frosty", "frown", "fry", "galaxy", "game",
    "garden", "garlic", "garnet", "gate", "gather", "gauge", "gecko", "gentle",
    "gerbil", "giant", "ginger", "glacier", "glass", "gleam", "glide", "glider",
    "glimmer", "global", "gloomy", "glove", "glow", "gnu", "goat", "gold",
    "golden", "golf", "gong", "goose", "grain", "granite", "grape", "grate",
    "gravel", "gravy", "gray", "green", "grey", "grill", "grim", "grin",
    "grinder", "grove", "grow", "guard", "guava", "guitar", "gulf", "guppy",
    "hail", "hair", "hammer", "hamster", "hand", "happy", "harbor", "hard",
    "hare", "harp", "hat", "haul", "hawk", "hazel", "hazy", "head",
    "heap", "heavy", "heel", "hen", "heron", "herring", "hiking", "hill",
    "hinge", "hip", "hockey", "hog", "honey", "hook", "hop", "horn",
    "horse", "hose", "hover", "huge", "hum", "humble", "humid", "hunting",
    "hut", "hyena", "indigo", "iris", "island", "ivory", "ivy", "jackal",
    "jacket", "jade", "jagged", "jam", "jar", "jasmine", "jasper", "jay",
    "jeans", "jelly", "jetty", "jogging", "jolly", "journal", "jug", "juice",
    "jump", "jungle", "kayak", "keeper", "kettle", "key", "khaki", "kind",
    "king", "kite", "kitten", "kiwi", "knee", "knife", "knight", "koala",
    "lace", "ladder", "ladle", "lady", "lagoon", "lake", "lamb", "lamp",
    "lance", "lantern", "lash", "latch", "laugh", "lean", "leap", "learn",
    "leather", "leave", "ledger", "leek", "leg", "lemon", "lentil", "letter",
    "lettuce", "level", "lift", "light", "lily", "lime", "line", "linen",
    "lion", "lip", "listen", "lively", "llama", "lobster", "local", "lock",
    "lodge", "lord", "loud", "lychee", "lynx", "mamba", "mango", "manor",
    "map", "maple", "marble", "marina", "market", "maroon", "marsh", "mason",
    "massive", "mast", "maze", "meadow", "medal", "melon", "memo", "mend",
    "merry", "messy", "metal", "meteor", "mild", "milk", "mince", "mineral",
    "mini", "minnow", "mint", "mirror", "mist", "misty", "mitten", "mix",
    "mixer", "modern", "mold", "mole", "month", "moon", "moose", "mop",
    "morning", "moss", "moth", "mouse", "mouth", "muffin", "mug", "mule",
    "mullet", "murky", "murmur", "mussel", "nail", "narrow", "navy", "nearby",
    "neat", "nebula", "neck", "newt", "night", "nimble", "nine", "noble",
    "noodle", "noon", "nose", "note", "notice", "nurse", "nutmeg", "oak",
    "oar", "oat", "oboe", "ocean", "olive", "one", "onion", "onyx",
    "opal", "open", "orange", "orbit", "orc", "orchard", "orchid", "organ",
    "osprey", "ostrich", "otter", "oval", "oven", "owl", "oyster", "paddle",
    "paint", "pair", "pajama", "pale", "palm", "pan", "panda", "pantry",
    "pants", "papaya", "paper", "parrot", "parsley", "pasta", "pastry", "pasture",
    "patch", "pause", "peach", "peacock", "peak", "peanut", "pear", "pearl",
    "pebble", "pecan", "peel", "peg", "pelican", "pennant", "pepper", "perch",
    "pewter", "piano", "pier", "pig", "pigeon", "pile", "pillow", "pilot",
    "pine", "pink", "pipe", "pitcher", "plain", "plan", "plane", "planet",
    "plastic", "plate", "plateau", "plaza", "pliers", "plug", "plum", "plumber",
    "polish", "pond", "poplar", "poppy", "porch", "possum", "poster", "pot",
    "potato", "pouch", "pour", "prairie", "pretzel", "prince", "print", "prism",
    "proud", "pull", "pumpkin", "pup", "puppet", "puppy", "purple", "purse",
    "push", "puzzle", "pyramid", "python", "quad", "quail", "quartz", "queen",
    "quiet", "quince", "quint", "quiver", "quiz", "rabbit", "rack", "radish",
    "raft", "rain", "rainy", "ram", "ranger", "rapids", "rat", "raven",
    "read", "red", "reed", "reef", "remote", "repair", "resume", "return",
    "rhino", "rib", "ribbon", "rice", "riddle", "ridge", "rigid", "ring",
    "rinse", "river", "roach", "roast", "robe", "robin", "rock", "rocket",
    "roll", "roof", "rooster", "rope", "rose", "rosy", "rough", "round",
    "row", "rowing", "rubber", "ruby", "rudder", "rug", "rugby", "rugged",
    "run", "running", "rural", "rustic", "sack", "saffron", "sage", "sail",
    "sailing", "sailor", "salad", "salmon", "salty", "sand", "sandal", "satin",
    "sauce", "savanna", "save", "savory", "saw", "scalp", "scarf", "scarlet",
    "scepter", "scooter", "scout", "screw", "scroll", "scrub", "sea", "seal",
    "season", "seven", "shack", "shallow", "shape", "share", "shark", "sharp",
    "sheep", "shelf", "shield", "shin", "shine", "ship", "shirt", "shoe",
    "shore", "short", "shout", "shrew", "shrimp", "shrine", "shutter", "shuttle",
    "shy", "silk", "silver", "simmer", "sing", "singer", "single", "six",
    "skating", "sketch", "skid", "skiing", "skip", "skirt", "skunk", "sky",
    "slate", "sled", "sleek", "sleet", "sleigh", "slice", "slide", "slipper",
    "sloth", "small", "smile", "smooth", "snail", "snake", "snow", "snowy",
    "snug", "soar", "soccer", "sock", "socket", "soda", "sofa", "soft",
    "sort", "soup", "sour", "sparkle", "sparrow", "spatula", "speak", "spear",
    "spend", "sphere", "spicy", "spider", "spin", "spinach", "spine", "sponge",
    "spoon", "spread", "spring", "sprint", "sprout", "spruce", "square", "squash",
    "squire", "stack", "stairs", "stale", "star", "stare", "start", "steam",
    "steamy", "steppe", "stern", "stew", "stiff", "stir", "stone", "stork",
    "storm", "stormy", "stout", "stove", "stream", "study", "sturdy", "suede",
    "sugar", "summer", "summit", "sun", "sundial", "sunny", "sunrise", "sunset",
    "supple", "surfing", "swamp", "swan", "sweater", "sweep", "sweet", "swift",
    "swim", "switch", "sword", "syrup", "table", "tailor", "talk", "tall",
    "tangy", "tart", "tassel", "tea", "teach", "teacher", "teal", "temple",
    "ten", "tennis", "test", "thick", "thigh", "thin", "thistle", "three",
    "thumb", "thyme", "ticket", "tidy", "tie", "tiger", "tiny", "toad",
    "toast", "toaster", "toasty", "today", "toe", "token", "tomato", "tongs",
    "tonight", "tooth", "top", "topaz", "torch", "tote", "tow", "tower",
    "trace", "trade", "train", "tram", "triple", "trolley", "trout", "truck",
    "trumpet", "tuba", "tulip", "tumble", "tuna", "tundra", "tune", "tunnel",
    "turkey", "turn", "turnip", "turtle", "tweed", "twist", "two", "unlock",
    "urban", "urchin", "valley", "valve", "van", "velvet", "verify", "vest",
)
assert len(WORDLIST) == 1024 and len(set(WORDLIST)) == 1024, \
    "WORDLIST must contain exactly 1024 distinct words"

# Files matching any of these render auto-collapsed: the ones a human almost
# never needs to read line-by-line. Path-segment patterns anchor at the start
# of the path or right after a "/" so they can't fire on a mere substring
# (e.g. "src/vendors.py" must not match the "vendor/" tree pattern).
GENERATED_PATTERNS = [
    # lockfiles
    r"\.lock$",
    r"(^|/)package-lock\.json$",
    r"(^|/)go\.sum$",
    # generated code
    r"\.pb\.go$",
    r"_pb2\.py$",
    r"_pb2_grpc\.py$",
    r"\.generated\.[^/]+$",
    r"(^|/)__generated__/",
    r"(^|/)generated/",
    # bundles and snapshots
    r"\.min\.(js|css)$",
    r"\.map$",
    r"(^|/)__snapshots__/",
    r"\.snap$",
    # vendored trees
    r"(^|/)vendor/",
    # Dart (this tool was first written for a Flutter codebase)
    r"\.(g|gr|gql|freezed|config|mocks|fakes|req|data|var|schema)\.dart$",
    r"\.pb\.dart$",
]
GENERATED = re.compile("|".join(GENERATED_PATTERNS))


def sh(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True)
    except OSError as e:  # gh not on PATH, not executable, ...
        raise RuntimeError(f"command failed: {' '.join(args)}: {e}") from e
    if out.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _git_diff_args(git_dir, spec):
    """Map a --git spec to a `git diff` argv, pinned at the given repo dir.
    `uncommitted` -> diff HEAD; an explicit `A...B` range is passed through
    verbatim; any other spec is treated as a single ref diffed against HEAD.
    A spec starting with `-` would reach git as an option (e.g. --output=
    writes a file), so it is rejected, and --end-of-options backstops any
    other option-shaped ref."""
    if spec.startswith("-"):
        raise RuntimeError(f"invalid --git spec: {spec!r} (must be 'uncommitted', a ref, or A...B)")
    if spec == "uncommitted":
        return ["git", "-C", git_dir, "diff", "--end-of-options", "HEAD"]
    if "..." in spec:
        return ["git", "-C", git_dir, "diff", "--end-of-options", spec]
    return ["git", "-C", git_dir, "diff", "--end-of-options", f"{spec}...HEAD"]


def get_diff(pr, repo, diff_file, git_dir=None, git_spec=None):
    if diff_file:
        with open(diff_file) as f:
            return f.read()
    if git_spec:
        # Re-run the git command every call rather than caching it, so /state
        # and /refresh see whatever the command would report now. For
        # `uncommitted` that means worktree edits. For a ref or an A...B
        # range the diff is commit-to-commit: it only changes if the refs
        # themselves move (a new commit on the branch, a rebase, ...), not on
        # a worktree edit that hasn't been committed.
        return sh(_git_diff_args(git_dir, git_spec))
    args = ["gh", "pr", "diff", str(pr)]
    if repo:
        args += ["--repo", repo]
    return sh(args)


def source_sig(diff_text):
    return hashlib.sha1(diff_text.encode("utf-8")).hexdigest()


def resolve_gh(pr, repo, diff_file, git_spec=None):
    """Owner/repo/pr/head-sha for posting inline comments, or None when there is no PR."""
    if diff_file or git_spec or not pr:
        return None
    args = ["gh", "pr", "view", str(pr), "--json", "url,number,headRefOid"]
    if repo:
        args += ["--repo", repo]
    out = sh(args)  # RuntimeError (gh failed) propagates to main()'s handler
    try:
        j = json.loads(out)
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/", j.get("url", ""))
        if not m:
            return None
        return {"owner": m.group(1), "repo": m.group(2), "pr": j.get("number", pr), "sha": j["headRefOid"]}
    except Exception:
        return None


def get_meta(pr, repo, diff_file, title=None, git_dir=None, git_spec=None):
    if diff_file or git_spec or not pr:
        if git_spec:
            label = "uncommitted changes" if git_spec == "uncommitted" else git_spec
            repo_name = os.path.basename(git_dir) if git_dir else ""
            default = f"{label} ({repo_name})" if repo_name else label
            return {"title": title or default, "url": "", "number": ""}
        return {"title": title or diff_file or "local diff", "url": "", "number": pr or ""}
    args = ["gh", "pr", "view", str(pr), "--json", "title,url,number"]
    if repo:
        args += ["--repo", repo]
    out = sh(args)  # RuntimeError (gh failed) propagates to main()'s handler
    try:
        return json.loads(out)
    except Exception:
        return {"title": f"PR #{pr}", "url": "", "number": pr}


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Per-side optional quoting: git quotes a path (core.quotepath) only when it
# needs to, so a header can be fully quoted, fully unquoted, or mixed. Git
# never C-quotes an ordinary space, so an unquoted side may itself contain
# spaces; the plain (both-unquoted) pattern is tried first since a lone " b/"
# literal is the only delimiter it has, and each quoted/mixed pattern anchors
# on the quote(s) instead so an unquoted counterpart's spaces don't confuse it.
_HEADER_QUOTED = r'"((?:[^"\\]|\\.)*)"'
_HEADER_PATTERNS = [
    (re.compile(r"^diff --git a/(.*) b/(.*)$"), False, False),
    (re.compile(rf"^diff --git {_HEADER_QUOTED} {_HEADER_QUOTED}$"), True, True),
    (re.compile(rf"^diff --git {_HEADER_QUOTED} b/(.*)$"), True, False),
    (re.compile(rf"^diff --git a/(.*) {_HEADER_QUOTED}$"), False, True),
]


_GIT_ESCAPES = {"\\": 0x5C, '"': 0x22, "a": 0x07, "b": 0x08, "f": 0x0C,
                "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
_OCTAL = set("01234567")


def _unquote_git_path(s):
    """Decode a git-quoted path: the full C escape set git emits (\\a \\b \\f
    \\n \\r \\t \\v \\\\ \\") plus \\NNN octal byte escapes (all three digits
    must be octal), then decode the resulting bytes as UTF-8."""
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nc = s[i + 1]
            if nc in _GIT_ESCAPES:
                out.append(_GIT_ESCAPES[nc])
                i += 2
                continue
            if i + 4 <= n and all(d in _OCTAL for d in s[i + 1:i + 4]):
                out.append(int(s[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
        out.extend(c.encode("utf-8"))
        i += 1
    return bytes(out).decode("utf-8", errors="replace")


def _parse_git_header(raw):
    """Return (old, new) paths from a `diff --git` line. Tries the plain,
    unquoted form first; falls back to git's per-side quoted form (emitted
    under core.quotepath for non-ASCII paths), quoted or not per side."""
    for pattern, old_quoted, new_quoted in _HEADER_PATTERNS:
        m = pattern.match(raw)
        if not m:
            continue
        old = _unquote_git_path(m.group(1)) if old_quoted else m.group(1)
        new = _unquote_git_path(m.group(2)) if new_quoted else m.group(2)
        if old_quoted and old.startswith("a/"):
            old = old[2:]
        if new_quoted and new.startswith("b/"):
            new = new[2:]
        return old, new
    return "", ""


def _strip_diff_path(s):
    """Strip a trailing tab-plus-timestamp (git and `diff -u` both append one
    on plain ---/+++ lines) and a leading a/ or b/ prefix, if present."""
    s = s.split("\t", 1)[0]
    if s != "/dev/null" and (s.startswith("a/") or s.startswith("b/")):
        s = s[2:]
    return s


def parse_diff(text):
    files = []
    cur = None
    hunk = None
    old_ln = new_ln = 0
    hunk_old_left = hunk_new_left = 0  # old/new lines the active hunk still owes, per its @@ counts
    pend_del = []
    pend_add = []
    headerless = False  # cur was opened from a bare ---/+++ pair, no `diff --git`
    pend_old_path = None  # a `--- ` line seen while awaiting its `+++ ` pair

    def flush_pairs():
        nonlocal pend_del, pend_add
        n = max(len(pend_del), len(pend_add))
        for k in range(n):
            left = pend_del[k] if k < len(pend_del) else {"t": "empty"}
            right = pend_add[k] if k < len(pend_add) else {"t": "empty"}
            hunk["rows"].append({"l": left, "r": right})
        pend_del, pend_add = [], []

    def close_hunk():
        # A hunk that ends still owing lines against its own @@ header means the
        # diff was cut short. Only the *preview* mode cares — it reconstructs a
        # whole file from these rows, and rendering a truncated one as a complete
        # document is the "confidently wrong" failure the design refuses.
        if hunk is not None and cur is not None and (hunk_old_left > 0 or hunk_new_left > 0):
            cur["truncated"] = True

    def hunk_exhausted():
        # A unified diff's hunk extent is exactly its @@ header counts, so
        # this is the only reliable way to tell "no more hunk body lines
        # coming" from "the next line just happens to start with --- /+++"
        # (e.g. a deleted/added line whose content itself starts with "-- "
        # or "++ ").
        return hunk is None or (hunk_old_left <= 0 and hunk_new_left <= 0)

    for raw in text.split("\n"):
        if raw.startswith("diff --git"):
            close_hunk()
            flush_pairs() if hunk else None
            old, new = _parse_git_header(raw)
            cur = {
                "old": old,
                "new": new,
                "display": new,
                "status": "modified",
                "generated": bool(GENERATED.search(new)) if new else False,
                "binary": False,
                "truncated": False,
                "hunks": [],
                "adds": 0,
                "dels": 0,
            }
            files.append(cur)
            hunk = None
            headerless = False
            pend_old_path = None
            continue
        if raw.startswith("--- ") and (cur is None or headerless) and hunk_exhausted():
            # No `diff --git` header preceded this: fall back to opening a
            # file entry from the ---/+++ pair (a plain unified patch, or the
            # next file in a headerless multi-file patch). Gated on
            # hunk_exhausted() so a deleted line that itself starts with
            # "--- " mid-hunk falls through to ordinary hunk-body parsing
            # instead.
            close_hunk()
            flush_pairs() if hunk else None
            pend_old_path = raw[4:]
            hunk = None
            continue
        if pend_old_path is not None and raw.startswith("+++ "):
            old = _strip_diff_path(pend_old_path)
            new = _strip_diff_path(raw[4:])
            if new == "/dev/null":
                status, name = "deleted", old
            elif old == "/dev/null":
                status, name = "added", new
            else:
                status, name = "modified", new
            cur = {
                "old": name if old == "/dev/null" else old,
                "new": name if new == "/dev/null" else new,
                "display": name,
                "status": status,
                "generated": bool(GENERATED.search(name)) if name else False,
                "binary": False,
                "truncated": False,
                "hunks": [],
                "adds": 0,
                "dels": 0,
            }
            files.append(cur)
            headerless = True
            pend_old_path = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur["status"] = "added"
            continue
        if raw.startswith("deleted file mode"):
            cur["status"] = "deleted"
            continue
        if raw.startswith("rename from") or raw.startswith("rename to") or raw.startswith("copy "):
            cur["status"] = "renamed"
            continue
        if raw.startswith("Binary files"):
            cur["binary"] = True
            continue
        # In headerless mode these would swallow a deleted/added line whose
        # content itself happens to start with "-- "/"++ " (raw "--- "/"+++
        # "); such lines belong to an active hunk and are handled by the
        # tag-based body parsing below instead. After a `diff --git` header,
        # these are always the real ---/+++ path lines to skip.
        if not headerless and raw.startswith("--- "):
            continue
        if not headerless and raw.startswith("+++ "):
            continue
        hm = HUNK.match(raw)
        if hm:
            close_hunk()
            if hunk:
                flush_pairs()
            old_ln = int(hm.group(1))
            new_ln = int(hm.group(3))
            hunk_old_left = int(hm.group(2)) if hm.group(2) else 1
            hunk_new_left = int(hm.group(4)) if hm.group(4) else 1
            hunk = {"header": raw, "section": hm.group(5).strip(), "rows": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is None:
            continue
        if raw.startswith("\\"):  # \ No newline at end of file
            continue
        tag = raw[:1]
        body = raw[1:]
        if tag == " ":
            flush_pairs()
            hunk["rows"].append({
                "l": {"t": "ctx", "n": old_ln, "s": body},
                "r": {"t": "ctx", "n": new_ln, "s": body},
            })
            old_ln += 1
            new_ln += 1
            hunk_old_left -= 1
            hunk_new_left -= 1
        elif tag == "-":
            pend_del.append({"t": "del", "n": old_ln, "s": body})
            old_ln += 1
            cur["dels"] += 1
            hunk_old_left -= 1
        elif tag == "+":
            pend_add.append({"t": "add", "n": new_ln, "s": body})
            new_ln += 1
            cur["adds"] += 1
            hunk_new_left -= 1
    close_hunk()
    if hunk:
        flush_pairs()
    for f in files:
        # Which side, if either, carries all the content. "r" covers added
        # files and pure appends (context + adds, nothing deleted); "l" covers
        # deletions. None means both sides are live, so only a split view can
        # show the change honestly.
        if f["dels"] == 0 and f["adds"] > 0:
            f["single"] = "r"
        elif f["adds"] == 0 and f["dels"] > 0:
            f["single"] = "l"
        else:
            f["single"] = None
    return files


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f4f6f8; --surface:#ffffff; --surface2:#eef1f4; --border:#d7dde3;
  --text:#1b222c; --dim:#63707e; --accent:#b26a00; --accent-bg:rgba(232,163,23,.13);
  --add-bg:rgba(46,160,67,.12); --add-gut:rgba(46,160,67,.22); --add-num:#1a7f37;
  --del-bg:rgba(207,34,46,.10); --del-gut:rgba(207,34,46,.18); --del-num:#b3202b;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0c1320; --surface:#0f1826; --surface2:#0b111b; --border:#1e2a3a;
  --text:#d6deea; --dim:#8493a5; --accent:#e8a317; --accent-bg:rgba(232,163,23,.14);
  --add-bg:rgba(46,160,67,.16); --add-gut:rgba(46,160,67,.28); --add-num:#3fb950;
  --del-bg:rgba(248,81,73,.15); --del-gut:rgba(248,81,73,.26); --del-num:#f85149;
}}
:root[data-theme=dark]{
  --bg:#0c1320; --surface:#0f1826; --surface2:#0b111b; --border:#1e2a3a;
  --text:#d6deea; --dim:#8493a5; --accent:#e8a317; --accent-bg:rgba(232,163,23,.14);
  --add-bg:rgba(46,160,67,.16); --add-gut:rgba(46,160,67,.28); --add-num:#3fb950;
  --del-bg:rgba(248,81,73,.15); --del-gut:rgba(248,81,73,.26); --del-num:#f85149;}
:root[data-theme=light]{
  --bg:#f4f6f8; --surface:#ffffff; --surface2:#eef1f4; --border:#d7dde3;
  --text:#1b222c; --dim:#63707e; --accent:#b26a00; --accent-bg:rgba(232,163,23,.13);
  --add-bg:rgba(46,160,67,.12); --add-gut:rgba(46,160,67,.22); --add-num:#1a7f37;
  --del-bg:rgba(207,34,46,.10); --del-gut:rgba(207,34,46,.18); --del-num:#b3202b;}
/* dart token colors */
:root{--tk-k:#7c3aed;--tk-s:#0a7d3f;--tk-c:#6a737d;--tk-n:#b3560f;--tk-t:#1668b8;--tk-an:#b26a00}
@media (prefers-color-scheme:dark){:root{--tk-k:#c792ea;--tk-s:#7ee787;--tk-c:#8b98a5;--tk-n:#f0a35e;--tk-t:#6cb6ff;--tk-an:#e8a317}}
:root[data-theme=dark]{--tk-k:#c792ea;--tk-s:#7ee787;--tk-c:#8b98a5;--tk-n:#f0a35e;--tk-t:#6cb6ff;--tk-an:#e8a317}
:root[data-theme=light]{--tk-k:#7c3aed;--tk-s:#0a7d3f;--tk-c:#6a737d;--tk-n:#b3560f;--tk-t:#1668b8;--tk-an:#b26a00}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--ui);font-size:14px;line-height:1.5}
header{position:sticky;top:0;z-index:20;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:14px;padding:10px 18px;flex-wrap:nowrap}
header .title{font-weight:650;font-size:15px;line-height:1.35;min-width:0;flex:1 1 0;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;
  word-break:break-word}
header .title.clamped{-webkit-mask-image:linear-gradient(to bottom,#000 48%,transparent 96%);
  mask-image:linear-gradient(to bottom,#000 48%,transparent 96%)}
header .title a{color:var(--accent);text-decoration:none}
header .controls{flex:0 1 auto;min-width:0;display:flex;align-items:center;gap:12px;
  flex-wrap:wrap;justify-content:flex-end}
.btn{font:inherit;color:var(--text);background:var(--surface2);border:1px solid var(--border);
  border-radius:7px;padding:6px 12px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);color:#fff;border-color:transparent;font-weight:600}
.btn.primary:disabled{opacity:.5;cursor:not-allowed}
.btn.approve{background:var(--add-num);color:#fff;border-color:transparent;font-weight:600}
.btn.approve:hover{filter:brightness(1.05)}
.btn.refresh{background:var(--accent);color:#fff;border-color:transparent;font-weight:600;
  animation:pulse 2s ease-in-out infinite}
.btn.refresh:hover{filter:brightness(1.05)}
.btn.refresh:disabled{opacity:.6;cursor:progress;animation:none}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 var(--accent-bg)}50%{box-shadow:0 0 0 5px var(--accent-bg)}}
.count{color:var(--dim);font-variant-numeric:tabular-nums}
main{max-width:1180px;margin:0 auto;padding:18px}
.file{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:16px}
.file-head{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;
  position:sticky;top:52px;background:var(--surface);border-bottom:1px solid var(--border);
  border-radius:10px 10px 0 0;z-index:10}
.file-head .chev{color:var(--dim);transition:transform .15s;flex:none}
.file.collapsed .chev{transform:rotate(-90deg)}
.file.collapsed .file-body{display:none}
.file-head .path{font-family:var(--mono);font-size:12.5px;word-break:break-all}
.file-head .badge{font-size:11px;color:var(--dim);border:1px solid var(--border);border-radius:20px;padding:1px 8px}
.file-head .stat{font-family:var(--mono);font-size:12px}
.file-head .stat .a{color:var(--add-num)} .file-head .stat .d{color:var(--del-num)}
.file-head .grow{flex:1}
.file-head label{display:flex;gap:5px;align-items:center;color:var(--dim);font-size:12px;cursor:pointer}
.modes{display:flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;flex:none}
.modes button{background:var(--surface);color:var(--dim);border:0;border-left:1px solid var(--border);
  font:inherit;font-size:11px;padding:2px 9px;cursor:pointer}
.modes button:first-child{border-left:0}
.modes button[aria-pressed="true"]{background:var(--accent-bg);color:var(--accent);font-weight:600}
.file-body{overflow-x:auto}
.hunk-head{font-family:var(--mono);font-size:12px;color:var(--dim);background:var(--surface2);
  padding:3px 12px;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.grid{display:grid;grid-template-columns:46px minmax(0,1fr) 46px minmax(0,1fr);min-width:760px}
.grid.single{grid-template-columns:46px minmax(0,1fr);min-width:380px}
/* preview: rendered markdown. Visually bounded so attacker prose does not
   inherit the page's own authority — see the design doc. */
.md-wrap{padding:14px 18px 20px}
.md-warn{font-size:11.5px;color:var(--dim);border:1px dashed var(--border);
  border-radius:6px;padding:5px 9px;margin-bottom:14px}
.md-doc{max-width:52em;line-height:1.6}
.md-doc h1,.md-doc h2,.md-doc h3,.md-doc h4,.md-doc h5,.md-doc h6{
  margin:1.1em 0 .45em;line-height:1.3}
.md-doc h1{font-size:1.6em} .md-doc h2{font-size:1.35em} .md-doc h3{font-size:1.15em}
.md-doc p,.md-doc ul,.md-doc ol,.md-doc blockquote{margin:.55em 0}
.md-doc ul,.md-doc ol{padding-left:1.5em}
.md-doc li{margin:.15em 0}
.md-doc blockquote{border-left:3px solid var(--border);padding-left:.9em;color:var(--dim)}
.md-doc code{font-family:var(--mono);font-size:.9em;background:var(--surface2);
  padding:.1em .3em;border-radius:3px}
.md-code{background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  padding:9px 11px;overflow-x:auto}
.md-code code{background:none;padding:0;white-space:pre}
.md-table{border-collapse:collapse;margin:.7em 0;display:block;overflow-x:auto}
.md-table th,.md-table td{border:1px solid var(--border);padding:4px 9px;text-align:left}
.md-table th{background:var(--surface2)}
.md-link{color:var(--accent)}
.md-deadlink{color:var(--del-num);text-decoration:line-through}
.md-title,.md-img,.md-def{color:var(--dim);font-family:var(--mono);font-size:.85em}
.md-raw{display:block;font-family:var(--mono);font-size:.85em;color:var(--dim);
  background:var(--surface2);border-left:3px solid var(--del-gut);
  padding:3px 8px;white-space:pre-wrap;word-break:break-word}
.md-fm{border:1px solid var(--border);border-radius:6px;background:var(--surface2);
  padding:7px 10px;margin-bottom:1.2em;font-family:var(--mono);font-size:12px}
.md-fm-row{display:flex;gap:10px;color:var(--dim)}
.md-fm-k{min-width:8em;color:var(--text);opacity:.75}
.md-target{cursor:pointer;border-radius:3px}
.md-target:hover{background:var(--accent-bg);box-shadow:0 0 0 3px var(--accent-bg)}
.md-cmt{margin:.5em 0;border-radius:6px}
.num{font-family:var(--mono);font-size:12px;color:var(--dim);text-align:right;padding:1px 8px;
  user-select:none;border-right:1px solid var(--border);white-space:nowrap}
.code{font-family:var(--mono);font-size:12.5px;padding:1px 10px;white-space:pre-wrap;word-break:break-word;
  cursor:pointer;position:relative}
.code:hover{background:var(--accent-bg)}
.code.empty,.num.empty{background:var(--surface2);cursor:default}
.code.empty:hover{background:var(--surface2)}
.code.r-add{background:var(--add-bg)} .num.r-add{background:var(--add-gut);color:var(--add-num)}
.code.r-del{background:var(--del-bg)} .num.r-del{background:var(--del-gut);color:var(--del-num)}
.code .plus{display:none;position:absolute;left:2px;top:50%;transform:translateY(-50%);
  background:var(--accent);color:#fff;border-radius:4px;width:16px;height:16px;line-height:16px;
  text-align:center;font-size:12px}
.code:hover .plus{display:block}
.code .cmt-kill{display:none;position:absolute;right:6px;top:50%;transform:translateY(-50%);
  background:var(--del-num);color:#fff;border-radius:4px;min-width:16px;height:16px;line-height:16px;
  text-align:center;font-size:12px;padding:0 3px;cursor:pointer;z-index:2}
.code:hover .cmt-kill{display:block}
.code .cmt-kill:hover{filter:brightness(1.08)}
.code.mark-remove{text-decoration:line-through;text-decoration-color:var(--del-num);opacity:.55}
.cmt-saved.dismiss{border-left:3px solid var(--del-num)}
.cmt-saved.dismiss .del{color:var(--del-num);font-weight:700;font-size:18px}
.grid.single .cmt-row{grid-column:1/3}
.cmt-row{grid-column:1/5;background:var(--surface2);border-top:1px solid var(--border);
  border-bottom:1px solid var(--border);padding:10px 12px}
.cmt-anchor{font-family:var(--mono);font-size:11px;color:var(--accent);margin-bottom:6px}
.cmt textarea{width:100%;font:inherit;font-family:var(--ui);background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:7px;padding:8px;resize:vertical;min-height:52px}
.cmt-actions{display:flex;gap:8px;margin-top:7px}
.saved{display:flex;gap:8px;align-items:flex-start;background:var(--surface);border:1px solid var(--border);
  border-left:3px solid var(--accent);border-radius:7px;padding:8px 10px;margin-bottom:6px}
.saved .txt{flex:1;white-space:pre-wrap}
.saved .del,.saved .edit{color:var(--dim);cursor:pointer;border:none;background:none;font-size:16px;line-height:1}
.saved .edit{font-size:13px}
.saved .del:hover,.saved .edit:hover{color:var(--text)}
.cmt-anchor .ghdest{display:inline-flex;align-items:center;gap:4px;color:#8b949e;border:1px solid var(--border);
  border-radius:20px;padding:0 7px;margin-left:6px;font-size:10.5px;vertical-align:1px}
.cmt-saved.gh .saved{border-left-color:#6e7681}
.btn.gh{background:#24292f;color:#fff;border-color:#24292f;font-weight:600;display:inline-flex;align-items:center;gap:6px}
.btn.gh:hover{filter:brightness(1.2);border-color:#24292f}
.btn.gh:disabled{opacity:.6;cursor:progress}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;
  padding:11px 18px;border-radius:9px;box-shadow:0 6px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .25s;z-index:50}
.toast.show{opacity:1}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:60;padding:20px}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;max-width:560px;width:100%;padding:18px;box-shadow:0 14px 44px rgba(0,0,0,.32)}
.modal h2{margin:0 0 4px;font-size:16px}
.modal .sub{color:var(--dim);font-size:13px;margin-bottom:12px}
.modal textarea{width:100%;min-height:120px;font:inherit;font-family:var(--ui);background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;resize:vertical}
.modal .actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.empty-note{color:var(--dim);padding:14px}
/* highlight.js token classes mapped onto the same palette (light + dark) */
.hljs-keyword,.hljs-literal,.hljs-selector-tag,.hljs-doctag{color:var(--tk-k)}
.hljs-string,.hljs-regexp,.hljs-meta .hljs-string,.hljs-addition{color:var(--tk-s)}
.hljs-comment{color:var(--tk-c);font-style:italic}
.hljs-number,.hljs-symbol,.hljs-bullet{color:var(--tk-n)}
.hljs-title,.hljs-title.class_,.hljs-title.function_,.hljs-type,.hljs-built_in,.hljs-selector-class{color:var(--tk-t)}
.hljs-meta,.hljs-attr,.hljs-attribute,.hljs-name,.hljs-selector-attr,.hljs-selector-id{color:var(--tk-an)}
.hljs-emphasis{font-style:italic}.hljs-strong{font-weight:600}
.hunk-head{cursor:pointer;user-select:none;display:flex;gap:8px;align-items:center}
.hunk-head .hchev{color:var(--dim);transition:transform .15s;flex:none}
.hunk.collapsed .hchev{transform:rotate(-90deg)}
.hunk.collapsed .grid{display:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
</style></head><body>
<header>
  <span class="title" id="title">__TITLE_HTML__</span>
  <div class="controls">
    <button class="btn refresh" id="refresh" hidden>↻ Refresh</button>
    <span class="count" id="fcount"></span>
    <button class="btn" id="expandAll">Expand all</button>
    <button class="btn" id="collapseAll">Collapse all</button>
    <button class="btn" id="theme">◐</button>
    <button class="btn primary" id="submit" disabled>Submit review (0)</button>
  </div>
</header>
<main id="root"></main>
<div class="toast" id="toast"></div>
<div class="modal-bg" id="finishBg">
  <div class="modal" role="dialog" aria-modal="true" aria-label="Finish your review">
    <h2>Finish your review</h2>
    <div class="sub" id="finishSub"></div>
    <textarea id="finishSummary" placeholder="Overall review comment (optional)"></textarea>
    <div class="actions">
      <button class="btn" id="finishCancel">Cancel</button>
      <button class="btn primary" id="finishSubmit">Submit review</button>
      <button class="btn approve" id="finishApprove">Approve</button>
    </div>
  </div>
</div>
<script src="vendor/highlight.min.js"></script>
<script src="vendor/dart.min.js"></script>
<script src="vendor/marked.umd.js"></script>
<script>
const DIFF = /*__DIFF_JSON__*/[];
const META = /*__META_JSON__*/{};
const comments = {}; // anchor -> {file,line,side,code,text}
// path -> view mode the user picked. Survives /refresh (unlike comments, which
// refresh discards): a view preference cannot go stale the way an anchor can.
// Refresh ends in location.reload(), so this has to outlive the page, not just
// the render — an in-memory map would silently drop every override. Keyed by
// the per-launch token path so a later review on the same port cannot inherit
// the previous one's choices, and scoped to sessionStorage so it dies with the
// tab. Guarded because a storage-disabled browser would otherwise throw here
// and take the whole page script down with it.
const VIEW_MODES_KEY = 'local-review:viewModes:' + location.pathname;
let viewModes = {};
try{ viewModes = JSON.parse(sessionStorage.getItem(VIEW_MODES_KEY)) || {}; }catch(e){}
function saveViewModes(){
  try{ sessionStorage.setItem(VIEW_MODES_KEY, JSON.stringify(viewModes)); }catch(e){}
}
const HAS_PR = !!(META && META.github);   // only offer GitHub posting when the diff is a PR
const GH_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true" style="vertical-align:-2px"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>';
const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const root = document.getElementById('root');

// Syntax highlighting via highlight.js (vendored). Pick a language per file from
// its extension; fall back to auto-detection when the extension is unknown.
// Runtime contract: langForFile guards every mapping with hljs.getLanguage(),
// so a value naming a language the bundle lacks falls back to highlightAuto —
// wrong values degrade quality, never break rendering. getLanguage also
// accepts registered aliases, so the grammar-name grep below is an audit aid
// (the factory names), not the complete set of accepted names:
//   grep -o 'grmr_[a-zA-Z0-9_]*' vendor/highlight.min.js | sort -u
const LANG_MAP = {
  dart:'dart', swift:'swift', kt:'kotlin', kts:'kotlin', java:'java',
  js:'javascript', jsx:'javascript', mjs:'javascript', cjs:'javascript',
  ts:'typescript', tsx:'typescript', py:'python', rb:'ruby', go:'go', rs:'rust',
  c:'c', h:'c', cc:'cpp', cpp:'cpp', cxx:'cpp', hpp:'cpp', hh:'cpp', cs:'csharp',
  m:'objectivec', mm:'objectivec', php:'php',
  sh:'bash', bash:'bash', zsh:'bash', yml:'yaml', yaml:'yaml', json:'json',
  xml:'xml', html:'xml', htm:'xml', vue:'xml', svg:'xml', plist:'xml',
  css:'css', scss:'scss', less:'less', sql:'sql', md:'markdown', markdown:'markdown',
  toml:'ini', ini:'ini', lua:'lua', r:'r', pl:'perl', pm:'perl',
  make:'makefile', mk:'makefile',
};
function langForFile(file){
  if(typeof hljs==='undefined') return null;
  const base = (file.new || file.display || '').toLowerCase().split('/').pop();
  const ext = base.includes('.') ? base.split('.').pop() : '';
  // Filename special cases route through the same getLanguage guard as the
  // extension map: the vendored bundle has makefile but no dockerfile grammar,
  // and an unguarded name made hlLine catch the error and render plain text.
  const l = (base==='makefile' || base==='dockerfile') ? base : LANG_MAP[ext];
  return (l && hljs.getLanguage(l)) ? l : null;
}
// Highlight one source line and report whether the whole line is a comment.
// Comment detection reuses the highlighter's own tokens, so it works for any
// language hljs knows (// , # , -- , /* */ , <!-- -->, …) with no per-language rules.
function hlLine(src, lang){
  if(src==null || src==='') return {html:' ', isComment:false};
  if(typeof hljs==='undefined') return {html:esc(src), isComment:false};
  let res;
  try{
    res = lang ? hljs.highlight(src, {language:lang, ignoreIllegals:true})
               : hljs.highlightAuto(src);
  }catch(e){ return {html:esc(src), isComment:false}; }
  const html = res.value;
  if(html.indexOf('hljs-comment')===-1) return {html, isComment:false};
  const tmp = document.createElement('div'); tmp.innerHTML = html;
  const allNS = (tmp.textContent||'').replace(/\s+/g,'');
  let cmt=''; tmp.querySelectorAll('.hljs-comment').forEach(s=>{cmt+=s.textContent;});
  const isComment = allNS.length>0 && allNS===cmt.replace(/\s+/g,'');
  return {html, isComment};
}

// View modes. `split` is the two-sided diff; `single` drops the dead side and
// is offered only when one side carries all the content (parse_diff decides
// that, and reports it as file.single === 'r' | 'l' | null).
const MODE_LABEL = {split:'Split', single:'Single', preview:'Preview'};
// preview is offered only for a WHOLLY-ADDED markdown file: a modified file's
// diff carries hunk fragments, and a fence opened outside the hunk never
// closes, so the rendering would be confidently wrong. It also drops out
// entirely when vendor/marked.umd.js is absent.
function canPreview(file){
  // file.truncated: a hunk that ended owing lines against its @@ header. The
  // reconstruction would be a partial file rendered as a whole document, which
  // is the one thing preview is required to refuse rather than guess at.
  return file.status === 'added' && !file.binary && !file.truncated
      && isMarkdown(file) && previewAvailable() && !previewDeclined[file.display];
}
// describe() can also decline at render time (over the byte cap, or a lexer
// throw). Record that so the mode stops being offered instead of staying
// selected on a file it cannot render.
const previewDeclined = {};
function legalModes(file){
  const m = file.single ? ['split','single'] : ['split'];
  if(canPreview(file)) m.push('preview');
  return m;
}
function defaultMode(file){
  if(canPreview(file)) return 'preview';
  return file.single ? 'single' : 'split';
}
function modeFor(file){
  const pick = viewModes[file.display];
  // A file's status can move under /refresh — a new file gets committed, an
  // append picks up a deletion — and an override that is no longer legal is
  // dropped rather than honoured.
  if(pick && legalModes(file).indexOf(pick) === -1){
    delete viewModes[file.display];
    saveViewModes();
  }
  return viewModes[file.display] || defaultMode(file);
}

// ---- preview: markdown as a document ------------------------------------
// The security property is that no attacker-derived string ever reaches an
// HTML parser. marked's LEXER is used; its parser and renderer, the half that
// emits HTML, are never called. describe() turns tokens into a plain tree and
// is a pure function (no DOM), so the security-critical half is testable under
// bare node; materialize() turns that tree into elements and has no logic.
// Full rationale: dev_docs/designs/local-review-markdown-preview.md

// >>> PURE-PREVIEW-BEGIN — everything to the matching END marker touches no
// DOM and no page state. scripts/test-local-review.sh slices exactly this
// region out and runs it under bare node against a security corpus. Keep it
// pure: one `document.` reference in here silently disables that whole suite.
const PREVIEW_MAX_BYTES = 512 * 1024;
const PREVIEW_MAX_DEPTH = 24;

// The only elements describe() may name. An element type is NEVER derived from
// a token type — an unknown token becomes a span of text, never a tag.
const SAFE_TAGS = ['div','span','p','h1','h2','h3','h4','h5','h6','ul','ol','li',
  'table','thead','tbody','tr','th','td','pre','code','blockquote','em','strong',
  'del','a','hr','br'];

// Absolute, allow-listed scheme, and nothing else. A bare scheme test on an
// unresolved string would pass a relative href, which resolves under /<token>/
// — inside the authorized tree. Requiring the scheme in the raw text also
// rejects protocol-relative "//host/path", which a canonical-origin check
// alone would wave through.
function safeHref(raw){
  const s = String(raw == null ? '' : raw).trim();
  if(!/^(https?|mailto):/i.test(s)) return null;
  let u;
  try{ u = new URL(s); }catch(e){ return null; }
  return ['http:','https:','mailto:'].indexOf(u.protocol) === -1 ? null : u.href;
}

// Frontmatter is not YAML-parsed: split on the first colon, keep an ARRAY of
// pairs (a plain object would take a __proto__ key straight into prototype
// pollution), and render both halves as text. An unterminated opener is not
// frontmatter at all.
function splitFrontmatter(src){
  const lines = src.split('\n');
  if(lines[0] !== '---') return {pairs: null, body: src, offset: 0};
  let close = -1;
  for(let i = 1; i < lines.length; i++){ if(lines[i] === '---'){ close = i; break; } }
  if(close === -1) return {pairs: null, body: src, offset: 0};
  const pairs = [];
  for(let i = 1; i < close; i++){
    const c = lines[i].indexOf(':');
    pairs.push(c === -1 ? [lines[i], ''] : [lines[i].slice(0, c), lines[i].slice(c + 1).trim()]);
  }
  return {pairs, body: lines.slice(close + 1).join('\n'), offset: close + 1};
}

// Where a token sits in the source, measured rather than assumed. A token's
// raw carries N newlines and the NEXT token starts N lines later — a heading's
// raw is "# One" (0 newlines) while the `space` token after it is "\n\n" (2),
// so anything like `newlines || 1` over-counts every single block. The token
// itself ends one line earlier when its raw closes with a newline.
function spanOf(raw, line){
  const s = String(raw == null ? '' : raw);
  const nl = s.split('\n').length - 1;
  const end = line + nl - (s.charAt(s.length - 1) === '\n' ? 1 : 0);
  return {line, endLine: Math.max(line, end), advance: nl};
}

const node = (tag, opts) => Object.assign({tag, text: null, attrs: null, kids: []}, opts || {});
const textNode = s => node('span', {text: String(s == null ? '' : s)});

// Inline tokens. Only `a` ever carries an attribute, and only after safeHref.
function describeInline(toks, depth){
  const out = [];
  if(!Array.isArray(toks)) return out;
  // At the depth cutoff, emit the remaining source as inert text rather than
  // returning nothing. Dropping it would let 25-deep nesting hide text that
  // still reaches the agent through the payload's `code` field, which is the
  // one thing "What the reviewer must be able to see" forbids. The block side
  // already did this; the inline side silently did not.
  if(depth > PREVIEW_MAX_DEPTH){
    out.push(node('span', {attrs:{class:'md-raw'},
      text: toks.map(t => String(t.raw == null ? '' : t.raw)).join('')}));
    return out;
  }
  for(const t of toks){
    switch(t.type){
      case 'text': case 'escape':
        out.push(textNode(t.text)); break;
      case 'strong': out.push(node('strong', {kids: describeInline(t.tokens, depth+1)})); break;
      case 'em':     out.push(node('em',     {kids: describeInline(t.tokens, depth+1)})); break;
      case 'del':    out.push(node('del',    {kids: describeInline(t.tokens, depth+1)})); break;
      case 'codespan': out.push(node('code', {text: String(t.text == null ? '' : t.text)})); break;
      case 'br':     out.push(node('br')); break;
      case 'link': {
        const href = safeHref(t.href);
        const kids = describeInline(t.tokens, depth+1);
        // A rejected link is not silently dropped: the reviewer sees the text
        // and the URL it pointed at, as inert text.
        if(!href){
          out.push(node('span', {attrs:{class:'md-deadlink'}, kids:
            kids.concat([textNode(' <' + String(t.href) + '>')])}));
        }else{
          out.push(node('a', {attrs: {href, class: 'md-link'}, kids}));
        }
        // The title emission used to sit after a `break` in the rejected
        // branch, so the ONE case that hides a title was the hostile one —
        // exactly where a payload author would put text meant for the agent
        // and not the reviewer. It now runs on both paths.
        if(t.title) out.push(node('span', {attrs:{class:'md-title'}, text: ' "' + t.title + '"'}));
        break;
      }
      case 'image':
        // Never loaded. A remote image would turn a review into a beacon.
        // Title included for the same reason links carry theirs.
        out.push(node('span', {attrs:{class:'md-img'},
          text: '[image: ' + String(t.text || '') + ' <' + String(t.href || '') + '>'
                + (t.title ? ' "' + t.title + '"' : '') + ']'}));
        break;
      case 'html':
        out.push(node('span', {attrs:{class:'md-raw'}, text: String(t.raw == null ? '' : t.raw)}));
        break;
      default:
        out.push(textNode(t.raw != null ? t.raw : (t.text != null ? t.text : '')));
    }
  }
  return out;
}

function describeInline_(t, depth){
  return t.tokens ? describeInline(t.tokens, depth) : [textNode(t.text)];
}

// Block tokens -> {tag, text, attrs, kids, line, endLine}. `line`/`endLine` are
// 1-based source lines and are what a comment anchors to.
function describeBlock(t, ln, depth){
  const span = {line: ln.line, endLine: ln.endLine};
  if(depth > PREVIEW_MAX_DEPTH) return node('span', Object.assign({text: t.raw || ''}, span));
  switch(t.type){
    case 'space': return null;
    case 'hr': return node('hr', span);
    case 'heading':
      return node('h' + Math.min(Math.max(t.depth|0, 1), 6),
        Object.assign({kids: describeInline_(t, depth+1), attrs:{class:'md-block'}}, span));
    case 'paragraph':
      return node('p', Object.assign({kids: describeInline_(t, depth+1), attrs:{class:'md-block'}}, span));
    case 'code':
      // textContent only, deliberately unhighlighted: highlighting means the
      // hljs innerHTML path, and a carve-out at this boundary costs more than
      // syntax colour. See issue #385.
      return node('pre', Object.assign({attrs:{class:'md-block md-code'},
        kids:[node('code', {text: String(t.text == null ? '' : t.text)})]}, span));
    case 'blockquote':
      return node('blockquote', Object.assign({attrs:{class:'md-block'},
        kids: describeBlocks(t.tokens || [], ln, depth+1)}, span));
    case 'html':
      // Visible as raw HTML, inert. The reviewer needs to know it is there.
      return node('div', Object.assign({attrs:{class:'md-block md-raw'},
        text: String(t.raw == null ? '' : t.raw)}, span));
    case 'def':
      // A reference definition renders nothing in normal markdown, but its URL
      // and title are in the file and reach the agent.
      return node('div', Object.assign({attrs:{class:'md-block md-def'},
        text: '[' + String(t.tag||'') + ']: ' + String(t.href||'') +
              (t.title ? ' "' + t.title + '"' : '')}, span));
    case 'list': {
      const kids = [];
      let cursor = ln.line;
      for(const item of (t.items || [])){
        const isp = spanOf(item.raw, cursor);
        cursor += isp.advance;
        kids.push(node('li', Object.assign({attrs:{class:'md-block'},
          kids: item.tokens ? describeBlocks(item.tokens, isp, depth+1)
                            : describeInline_(item, depth+1)}, isp)));
      }
      return node(t.ordered ? 'ol' : 'ul', Object.assign({kids}, span));
    }
    case 'table': {
      // Rows carry no `raw` of their own (measured, not assumed), so row spans
      // are counted off the table token's own text.
      const kids = [];
      // The header row is a target like any other. It was the one
      // uncommentable element in a preview, which is backwards — the header
      // names the columns and is what a reviewer most often wants to argue with.
      const head = node('tr', Object.assign({attrs:{class:'md-block'},
        kids: (t.header||[]).map(c => node('th', {kids: describeInline(c.tokens, depth+1)}))},
        {line: ln.line, endLine: ln.line}));
      kids.push(node('thead', {kids:[head]}));
      let cursor = ln.line + 2;                     // header row + separator
      const body = [];
      for(const row of (t.rows || [])){
        const rsp = {line: cursor, endLine: cursor};
        cursor += 1;
        body.push(node('tr', Object.assign({attrs:{class:'md-block'},
          kids: row.map(c => node('td', {kids: describeInline(c.tokens, depth+1)}))}, rsp)));
      }
      kids.push(node('tbody', {kids: body}));
      return node('table', Object.assign({attrs:{class:'md-table'}, kids}, span));
    }
    case 'text':
      // A list_item's inner `text` token, and the reason this case exists at
      // all. It is MARKED-UP text: a tight list item carries its inline tokens
      // here, so emitting `raw` printed `**Hook:**` literally in every tight
      // list (a loose one goes through `paragraph` and always rendered).
      //
      // No md-block here. This token spans the SAME lines as the enclosing li,
      // and blockAnchor keys on line alone — so marking both made two nested
      // targets share one comment, with the chip dedupe silently eating
      // whichever rendered first. Measured before that fix: `- one\n- two\n
      // para\n` gave [li@1, div@1, li@2-3, div@2-3]. Containers stay the
      // target; this is just their text.
      //
      // The length test is not redundant with the truthiness one: `tokens: []`
      // is truthy, and would render neither kids nor text — the one outcome
      // "nothing in the file may be invisible" forbids. No token in the
      // vendored marked reaches here that way (60k fuzzed inputs, zero hits);
      // the guard is for the next version bump, not for a live bug.
      return node('div', Object.assign({attrs:{class:'md-plain'},
        kids: (t.tokens && t.tokens.length) ? describeInline(t.tokens, depth+1) : [],
        text: (t.tokens && t.tokens.length) ? null : String(t.raw != null ? t.raw : '')}, span));
    default:
      // An UNKNOWN token stays inert: its raw source as text, no element
      // derived from the token type and no walk of its fields. That is one of
      // the three bullets under "The invariant" in the design doc, and the
      // security corpus asserts it directly — which is why the `text` token
      // above is handled by name rather than by widening this branch.
      return node('div', Object.assign({attrs:{class:'md-plain'},
        text: String(t.raw != null ? t.raw : '')}, span));
  }
}

function describeBlocks(toks, ln, depth){
  const out = [];
  let cursor = ln.line;
  for(const t of toks){
    const span = spanOf(t.raw, cursor);
    cursor += span.advance;
    const d = describeBlock(t, span, depth);
    if(d) out.push(d);
  }
  return out;
}

// Entry point. `src` is the reconstructed file; returns a description tree or
// null when preview must decline.
function describe(src){
  if(typeof marked === 'undefined' || !marked.lexer) return null;
  // Encoded length, not src.length: the latter counts UTF-16 code units, so a
  // CJK or emoji document sailed past a cap named _BYTES at roughly three
  // times its stated size — and that oversized input is what reaches the lexer
  // marked has a live OOM advisory against.
  const bytes = (typeof TextEncoder !== 'undefined')
    ? new TextEncoder().encode(src).length : src.length;
  if(bytes > PREVIEW_MAX_BYTES) return null;
  // marked normalizes CRLF before tokenizing, so offsets must be computed
  // against normalized text or every anchor after line 1 drifts.
  const norm = src.replace(/\r\n/g, '\n');
  const fm = splitFrontmatter(norm);
  let toks;
  try{ toks = marked.lexer(fm.body); }catch(e){ return null; }
  const kids = describeBlocks(toks, {line: 1 + fm.offset, endLine: 1 + fm.offset}, 0);
  if(fm.pairs){
    const rows = fm.pairs.map((kv, i) => node('div', {attrs:{class:'md-fm-row'},
      line: 2 + i, endLine: 2 + i,
      kids:[node('span', {attrs:{class:'md-fm-k'}, text: kv[0]}),
            node('span', {attrs:{class:'md-fm-v'}, text: kv[1]})]}));
    kids.unshift(node('div', {attrs:{class:'md-fm'}, kids: rows}));
  }
  return node('div', {attrs:{class:'md-doc'}, kids});
}

// <<< PURE-PREVIEW-END

// No logic here on purpose: everything security-relevant is decided in
// describe() and is testable without a DOM.
function materialize(desc, ctx){
  const tag = SAFE_TAGS.indexOf(desc.tag) === -1 ? 'span' : desc.tag;
  const el = document.createElement(tag);
  const cls = desc.attrs && desc.attrs.class;
  if(cls) el.className = cls;
  if(desc.attrs && desc.attrs.href && tag === 'a'){
    el.href = desc.attrs.href; el.rel = 'noopener noreferrer'; el.target = '_blank';
  }
  if(desc.text != null) el.textContent = desc.text;
  for(const k of desc.kids) el.appendChild(materialize(k, ctx));
  // Only leaf targets are commentable — describe() marks them md-block, so a
  // whole table or list is never one target while its rows/items are.
  if(ctx && desc.line != null && cls && cls.indexOf('md-block') !== -1){
    attachBlockComment(el, desc, ctx);
  }
  return el;
}

// A composer or chip is a <div>, and inserting one after a <tr> makes it a
// child of <tbody>. That is legal via the DOM API (no parser fixup) but CSS
// wraps it in an anonymous single-cell row, so it renders crushed into the
// first column. Wrap it in a real full-width row instead. Tables are half of
// what preview exists to show, so this is the primary path, not an edge case.
function insertAfterBlock(el, box){
  if(el.tagName !== 'TR'){ el.insertAdjacentElement('afterend', box); return; }
  const span = el.children.length || 1;
  const tr = document.createElement('tr');
  tr.className = 'md-cmt-row';
  const td = document.createElement('td');
  td.colSpan = span;
  td.appendChild(box);
  tr.appendChild(td);
  el.insertAdjacentElement('afterend', tr);
}

// The wrapper row, not the box, is what must be removed on delete.
const boxHost = box => (box.parentNode && box.parentNode.tagName === 'TD')
  ? box.parentNode.parentNode : box;

function blockAnchor(desc, ctx){
  return {key: `${ctx.file.new}|R${desc.line}`, path: ctx.file.new, side: 'R',
          line: desc.line, endLine: desc.endLine,
          code: ctx.lines[desc.line - 1] || ''};
}

function attachBlockComment(el, desc, ctx){
  el.classList.add('md-target');
  el.__mdDesc = desc;
  el.addEventListener('click', e => {
    if(e.target.closest('a')) return;                 // let a real link be a link
    if(e.target.closest('.cmt, .cmt-saved')) return;  // don't re-open from the chip
    const sel = window.getSelection();
    if(sel && !sel.isCollapsed && sel.toString().length) return;
    e.stopPropagation();                              // innermost target wins
    openBlockComposer(el, desc, ctx);
  });
  // Chips are NOT rendered here: materialize() is still building the tree, so
  // el has no parent yet and insertAdjacentElement('afterend') would silently
  // do nothing. They are flushed once the tree is in the document.
}

function openBlockComposer(el, desc, ctx){
  const a = blockAnchor(desc, ctx);
  const existing = comments[a.key];
  const range = a.endLine > a.line ? `R${a.line}–R${a.endLine}` : `R${a.line}`;
  const box = document.createElement('div');
  box.className = 'cmt-row md-cmt';
  box.innerHTML = `<div class="cmt-anchor">${esc(a.path)} : ${range}</div>
    <div class="cmt">
      <textarea placeholder="Leave a comment on this block…"></textarea>
      <div class="cmt-actions">
        <button class="btn primary save">${existing ? 'Update comment' : 'Add comment'}</button>
        ${HAS_PR ? `<button class="btn gh save-gh" title="Mark this for the PR — Claude posts it after you submit">${GH_ICON} Comment on GitHub</button>` : ''}
        <button class="btn cancel">Cancel</button>
      </div></div>`;
  insertAfterBlock(el, box);
  const ta = box.querySelector('textarea');
  if(existing) ta.value = existing.text;
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  const commit = github => () => {
    const v = ta.value.trim(); if(!v) return;
    // Both modes key a comment as `path|R<line>`, so a line comment made in
    // Split and a block comment made here are the SAME entry. Editing must
    // therefore preserve what the comment already is: re-deriving kind and
    // endLine from whichever mode you happen to be in silently changes what
    // the agent is told the comment covers.
    const kind = existing ? existing.kind : 'block';
    const endLine = existing ? existing.endLine : a.endLine;
    const c = {file: a.path, line: a.line, side: 'R', code: a.code, text: v,
               github: github || !!(existing && existing.github)};
    if(kind) c.kind = kind;
    if(endLine != null) c.endLine = endLine;
    comments[a.key] = c;
    boxHost(box).remove(); refreshCounts(); renderBlockChip(el, desc, ctx);
  };
  box.querySelector('.save').onclick = commit(false);
  const gh = box.querySelector('.save-gh');
  if(gh) gh.onclick = commit(true);
  box.querySelector('.cancel').onclick = () => boxHost(box).remove();
}

function renderBlockChip(el, desc, ctx){
  if(!el.parentNode) return;   // afterend is a no-op on a detached node
  const a = blockAnchor(desc, ctx);
  const root = ctx.root || document;
  const dup = root.querySelector(`.cmt-saved[data-k="${CSS.escape(a.key)}"]`);
  if(dup) boxHost(dup).remove();
  const c = comments[a.key]; if(!c) return;
  const range = c.endLine > c.line ? `R${c.line}–R${c.endLine}` : `R${c.line}`;
  const chip = document.createElement('div');
  chip.className = 'cmt-row cmt-saved md-cmt' + (c.github ? ' gh' : '');
  chip.dataset.k = a.key;
  chip.innerHTML = `<div class="cmt-anchor">${esc(c.file)} : ${range}${c.github?` <span class="ghdest">${GH_ICON} GitHub</span>`:''}</div>
    <div class="saved"><span class="txt">${esc(c.text)}</span>
      <button class="edit" title="Edit">✎</button>
      <button class="del" title="Delete">×</button></div>`;
  chip.querySelector('.del').onclick = ev => {
    ev.stopPropagation(); delete comments[a.key]; boxHost(chip).remove(); refreshCounts();
  };
  const edit = ev => { ev.stopPropagation(); boxHost(chip).remove(); openBlockComposer(el, desc, ctx); };
  chip.querySelector('.edit').onclick = edit;
  chip.querySelector('.saved .txt').onclick = edit;
  insertAfterBlock(el, chip);
}

// The reconstructed source of a wholly-added file: every right-side row, in
// order. parse_diff drops the "\ No newline at end of file" marker, so a file
// without a trailing newline round-trips with one added — immaterial to
// rendering, and noted in the design doc.
function sourceOf(file){
  const out = [];
  for(const h of file.hunks) for(const r of h.rows){
    if(r.r && r.r.t !== 'empty') out.push(r.r.s);
  }
  return out;
}

function isMarkdown(file){
  return /\.(md|markdown)$/i.test(String(file.display || ''));
}

function previewAvailable(){
  return typeof marked !== 'undefined' && !!marked.lexer;
}

// Returns true when it handled the file by falling back.
function fallbackToSingle(file, why){
  previewDeclined[file.display] = true;
  viewModes[file.display] = file.single ? 'single' : 'split';
  saveViewModes();
  toast('Preview unavailable: ' + why);
  return true;
}

// Built before the file header is rendered, so a decline can change which
// modes the control offers instead of leaving Preview selected on a file it
// cannot render.
function previewOf(file){
  const lines = sourceOf(file);
  const desc = describe(lines.join('\n'));
  return desc ? {desc, lines} : null;
}

function renderPreview(file, body, built){
  const lines = built.lines, desc = built.desc;
  const wrap = document.createElement('div');
  wrap.className = 'md-wrap';
  wrap.innerHTML = '<div class="md-warn">Rendered from the diff. Content is untrusted — links open externally and images are not loaded.</div>';
  const ctx = {file, lines, root: wrap};
  wrap.appendChild(materialize(desc, ctx));
  body.appendChild(wrap);
  // Flush saved comments only now that the tree is attached. Comments outlive
  // the DOM that created them, and a mode switch rebuilds this tree — if they
  // are not restored here, the submit count claims comments the page cannot
  // show, which is precisely what it did before this call existed.
  wrap.querySelectorAll('.md-target').forEach(el => {
    if(el.__mdDesc) renderBlockChip(el, el.__mdDesc, ctx);
  });
}

function anchorOf(file, cell){
  const path = cell.t==='del' ? file.old : file.new;
  const side = cell.t==='del' ? 'L' : 'R';
  return {key:`${path}|${side}${cell.n}`, path, side, line:cell.n, code:cell.s};
}

// Collapse/"Viewed" live outside the DOM so switching a file's view mode
// re-renders that file without silently unchecking it.
const fileUi = {};
function uiFor(file){
  if(!fileUi[file.display]) fileUi[file.display] = {collapsed: !!file.generated, viewed: false};
  return fileUi[file.display];
}

function render(){
  root.innerHTML='';
  if(!DIFF.length){root.innerHTML='<div class="empty-note">No changes in this diff.</div>';return;}
  DIFF.forEach((file, fi) => root.appendChild(renderFile(file, fi)));
  refreshCounts();
}

function renderFile(file, fi){
  const ui = uiFor(file);
  const el = document.createElement('section');
  el.className = 'file' + (ui.collapsed ? ' collapsed' : '');
  el.dataset.fi = fi;
  const stat = `<span class="stat"><span class="a">+${file.adds}</span> <span class="d">-${file.dels}</span></span>`;
  const gen = file.generated ? '<span class="badge">generated</span>' : '';
  const status = file.status!=='modified' ? `<span class="badge">${file.status}</span>` : '';
  let modes = legalModes(file), mode = modeFor(file);
  // Try the preview before drawing the header: if describe() declines, the
  // mode is dropped and the control below must not still offer it.
  let built = null;
  if(mode === 'preview'){
    built = previewOf(file);
    if(!built){
      fallbackToSingle(file, 'document too large or could not be parsed');
      modes = legalModes(file); mode = modeFor(file);
    }
  }
  const modeCtl = modes.length>1 ? `<span class="modes">${modes.map(m =>
    `<button data-mode="${m}" aria-pressed="${m===mode}">${MODE_LABEL[m]}</button>`).join('')}</span>` : '';
  el.innerHTML = `<div class="file-head">
      <span class="chev">▾</span>
      <span class="path">${esc(file.display)}</span>
      ${status}${gen}
      <span class="grow"></span>
      ${stat}
      ${modeCtl}
      <label><input type="checkbox" class="viewed"> Viewed</label>
    </div><div class="file-body"></div>`;
  const body = el.querySelector('.file-body');
  if(file.binary){ body.innerHTML='<div class="empty-note">Binary file not shown.</div>'; }
  else if(mode === 'preview'){ renderPreview(file, body, built); }
  else{
    const lang = langForFile(file);
    file.hunks.forEach(h => {
      const hunkEl = document.createElement('div'); hunkEl.className='hunk';
      const hh = document.createElement('div'); hh.className='hunk-head';
      hh.innerHTML = `<span class="hchev">▾</span><span>${esc(h.header)}${h.section? '  '+esc(h.section):''}</span>`;
      hh.addEventListener('click', () => hunkEl.classList.toggle('collapsed'));
      hunkEl.appendChild(hh);
      const g = document.createElement('div');
      g.className = mode==='single' ? 'grid single' : 'grid';
      g.dataset.cols = mode==='single' ? '2' : '4';
      const rlines = [], made = [];
      h.rows.forEach(row => { const m = addRow(g, file, row, lang, mode); if(m.r) rlines.push(m.r);
        if(m.l) made.push(m.l); if(m.r) made.push(m.r); });
      assignCommentBlocks(rlines, file, g);
      // Comments outlive the DOM that created them: a mode switch rebuilds
      // this grid, and every saved comment has to come back with it or the
      // "Submit review (N)" count claims comments the page cannot show.
      made.forEach(m => {
        const c = comments[anchorOf(file, m.cell).key];
        if(c && c.kind !== 'dismiss-comments') rerenderSaved(g, file, m.cell, m.codeEl);
      });
      hunkEl.appendChild(g);
      body.appendChild(hunkEl);
    });
  }
  el.querySelectorAll('.modes button').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();                       // the file-head click toggles collapse
    viewModes[file.display] = b.dataset.mode;
    saveViewModes();
    el.replaceWith(renderFile(file, fi));
  }));
  // head interactions
  el.querySelector('.file-head').addEventListener('click', e => {
    if(e.target.closest('label') || e.target.closest('.modes')) return;
    ui.collapsed = el.classList.toggle('collapsed');
  });
  const viewedBox = el.querySelector('.viewed');
  viewedBox.checked = ui.viewed;
  viewedBox.addEventListener('change', e => {
    ui.viewed = e.target.checked;
    ui.collapsed = e.target.checked;
    el.classList.toggle('collapsed', e.target.checked);
  });
  return el;
}

function addRow(g, file, row, lang, mode){
  const made = {l:null, r:null};
  // In single mode only the live side is emitted, so the grid is two columns
  // wide and there are no `empty` filler cells at all.
  const sides = mode==='single' ? [file.single] : ['l','r'];
  sides.forEach(sideKey => {
    const cell = row[sideKey];
    const num = document.createElement('div');
    const code = document.createElement('div');
    const cls = cell.t==='add'?'r-add':cell.t==='del'?'r-del':'';
    if(cell.t==='empty'){
      num.className='num empty'; code.className='code empty';
    }else{
      num.className='num'+(cls?' '+cls:''); num.textContent=cell.n!=null?cell.n:'';
      code.className='code'+(cls?' '+cls:'');
      const {html, isComment} = hlLine(cell.s, lang);
      const kill = (sideKey==='r' && isComment)
        ? '<span class="cmt-kill" title="Remove this comment block">⊘</span>' : '';
      code.innerHTML = `<span class="plus">+</span>${kill}${html||' '}`;
      code.addEventListener('click', e => {
        if(e.target.closest('.cmt-kill')) return;
        const sel = window.getSelection();          // don't hijack a drag-to-select for copy/paste
        if(sel && !sel.isCollapsed && sel.toString().length) return;
        openComposer(file, cell, g, code);
      });
      made[sideKey] = {cell, codeEl:code, isComment, kill: kill ? code.querySelector('.cmt-kill') : null};
    }
    g.appendChild(num); g.appendChild(code);
  });
  return made;
}

// A "dismiss comment block" comment covers the maximal run of contiguous comment
// lines on the new side. One tap on any line of the run tells Claude to delete it.
function assignCommentBlocks(rlines, file, g){
  for(let i=0;i<rlines.length;i++){
    const rc = rlines[i];
    if(!(rc.isComment && rc.kill)) continue;
    let a=i; while(a>0 && rlines[a-1].isComment && rlines[a-1].cell.n===rlines[a].cell.n-1) a--;
    let b=i; while(b<rlines.length-1 && rlines[b+1].isComment && rlines[b+1].cell.n===rlines[b].cell.n+1) b++;
    const run = rlines.slice(a, b+1);
    rc.kill.onclick = ev => { ev.stopPropagation(); dismissBlock(file, run, g); };
    // Restore a dismissal the user already made, so a mode switch that rebuilds
    // this grid does not lose the strike-through and its chip.
    if(rc === run[0] && (comments[`${file.new}|R${run[0].cell.n}`] || {}).kind === 'dismiss-comments'){
      run.forEach(x => x.codeEl.classList.add('mark-remove'));
      showDismissChip(g, run, `${file.new}|R${run[0].cell.n}`);
    }
  }
}

function dismissBlock(file, run, g){
  const first = run[0].cell, last = run[run.length-1].cell;
  const path = file.new;
  const key = `${path}|R${first.n}`;
  const text = run.length>1 ? `Remove these ${run.length} comment lines.` : 'Remove this comment.';
  comments[key] = {file:path, line:first.n, side:'R', code:first.s, endLine:last.n, text, kind:'dismiss-comments'};
  run.forEach(rc => rc.codeEl.classList.add('mark-remove'));
  refreshCounts();
  showDismissChip(g, run, key);
}

function showDismissChip(g, run, key){
  const last = run[run.length-1];
  const ex = g.querySelector(`.cmt-saved[data-k="${CSS.escape(key)}"]`); if(ex) ex.remove();
  const c = comments[key];
  const range = c.endLine>c.line ? `R${c.line}–R${c.endLine}` : `R${c.line}`;
  const chip = document.createElement('div');
  chip.className='cmt-row cmt-saved dismiss'; chip.dataset.k=key;
  chip.innerHTML = `<div class="cmt-anchor">${esc(c.file)} : ${range} · marked for removal</div>
    <div class="saved"><span class="txt">${esc(c.text)}</span>
      <button class="del" title="Undo removal">×</button></div>`;
  chip.querySelector('.del').onclick = () => {
    delete comments[key];
    run.forEach(rc => rc.codeEl.classList.remove('mark-remove'));
    chip.remove(); refreshCounts();
  };
  insertAfterRow(g, last.codeEl, chip);
}

// The Split/Single composer must not strip a block comment's shape either.
// Editing a Preview comment from the source view would otherwise drop kind and
// endLine, and the agent would then read "line 12" for a remark about a
// nine-line table — the exact thing endLine exists to prevent.
function keepShape(existing, c){
  if(existing && existing.kind) c.kind = existing.kind;
  if(existing && existing.endLine != null) c.endLine = existing.endLine;
  return c;
}

function openComposer(file, cell, grid, codeEl){
  const a = anchorOf(file, cell);
  // find the DOM row index to insert after: insert a full-width comment row right after this code cell's row
  const existing = comments[a.key];
  const cmt = document.createElement('div');
  cmt.className='cmt-row';
  cmt.innerHTML = `<div class="cmt-anchor">${esc(a.path)} : ${a.side}${a.line}</div>
    <div class="cmt">
      <textarea placeholder="Leave a comment on this line…"></textarea>
      <div class="cmt-actions">
        <button class="btn primary save">${existing ? 'Update comment' : 'Add comment'}</button>
        ${HAS_PR ? `<button class="btn gh save-gh" title="Mark this for the PR — Claude posts it after you submit">${GH_ICON} Comment on GitHub</button>` : ''}
        <button class="btn cancel">Cancel</button>
      </div></div>`;
  // insert after the code cell's grid cell (append at end of grid keeps it after; better: place right after row)
  insertAfterRow(grid, codeEl, cmt);
  const ta = cmt.querySelector('textarea');
  if(existing) ta.value = existing.text;
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  cmt.querySelector('.save').onclick = () => {
    const v = ta.value.trim(); if(!v) return;
    comments[a.key] = keepShape(existing, {file:a.path, line:a.line, side:a.side, code:a.code, text:v, github:!!(existing && existing.github)});
    cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  };
  cmt.querySelector('.cancel').onclick = () => cmt.remove();
  const ghBtn = cmt.querySelector('.save-gh');
  if(ghBtn) ghBtn.onclick = () => {   // same as a normal comment, just flagged for the PR; the AGENT posts it
    const v = ta.value.trim(); if(!v) return;
    comments[a.key] = keepShape(existing, {file:a.path, line:a.line, side:a.side, code:a.code, text:v, github:true});
    cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  };
  cmt.querySelectorAll('.saved .del').forEach(b => b.onclick = () => {
    delete comments[b.dataset.k]; cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  });
}
function rerenderSaved(grid, file, cell, codeEl){
  // show a persistent saved chip under the line if a comment exists
  const a = anchorOf(file, cell);
  // remove any existing chip row we placed for this anchor
  const existing = grid.querySelector(`.cmt-saved[data-k="${CSS.escape(a.key)}"]`);
  if(existing) existing.remove();
  const c = comments[a.key]; if(!c) return;
  const chip = document.createElement('div');
  chip.className='cmt-row cmt-saved' + (c.github ? ' gh' : ''); chip.dataset.k=a.key;
  chip.innerHTML = `<div class="cmt-anchor">${esc(a.path)} : ${a.side}${a.line}${c.github?` <span class="ghdest" title="Claude will post this on the PR after you submit">${GH_ICON} GitHub</span>`:''}</div>
    <div class="saved"><span class="txt">${esc(c.text)}</span>
      <button class="edit" title="Edit">✎</button>
      <button class="del" title="Delete">×</button></div>`;
  chip.querySelector('.del').onclick = () => { delete comments[a.key]; chip.remove(); refreshCounts(); };
  const editSaved = () => { chip.remove(); openComposer(file, cell, grid, codeEl); };
  chip.querySelector('.edit').onclick = editSaved;
  chip.querySelector('.saved .txt').onclick = editSaved;
  insertAfterRow(grid, codeEl, chip);
}
function insertAfterRow(grid, codeEl, node){
  // codeEl is a code cell of a visual row: [l-num, l-code, r-num, r-code] in
  // split mode, [num, code] in single mode. Inserting a full-width .cmt-row
  // right after an l-code would split the row and shove the right-side cells
  // onto the next line, so advance to the row's last cell (skipping .cmt-row
  // nodes, which sit between rows), then past any comment rows already there.
  const cols = Number(grid.dataset.cols) || 4;
  const cells = Array.from(grid.children).filter(c=>!c.classList.contains('cmt-row'));
  const idx = cells.indexOf(codeEl);
  let ref = cells[Math.min(idx - (idx % cols) + (cols - 1), cells.length - 1)];
  while(ref.nextSibling && ref.nextSibling.classList.contains('cmt-row')) ref = ref.nextSibling;
  if(ref.nextSibling) grid.insertBefore(node, ref.nextSibling); else grid.appendChild(node);
}

function refreshCounts(){
  const n = Object.keys(comments).length;
  const btn = document.getElementById('submit');
  btn.textContent = `Submit review (${n})`; btn.disabled = false;
  document.getElementById('fcount').textContent =
    `${DIFF.length} file${DIFF.length!==1?'s':''} · ${n} comment${n!==1?'s':''}`;
}

// Keep fileUi in step with the DOM, or a later mode switch re-renders the file
// from a stale collapsed flag and appears to undo the button.
function setAllCollapsed(on){
  document.querySelectorAll('.file').forEach(f => f.classList.toggle('collapsed', on));
  DIFF.forEach(file => { uiFor(file).collapsed = on; });
}
document.getElementById('expandAll').onclick = () => setAllCollapsed(false);
document.getElementById('collapseAll').onclick = () => setAllCollapsed(true);
document.getElementById('theme').onclick = () => {
  const r=document.documentElement;
  const cur=r.getAttribute('data-theme')|| (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
  r.setAttribute('data-theme', cur==='dark'?'light':'dark');
};

// Out-of-sync detection: the server recomputes the diff from the same source it
// was launched with and reports whether it differs from what this page is showing.
// When it does, reveal the Refresh button so the user can rebuild on demand.
const refreshBtn = document.getElementById('refresh');
const REFRESH_LABEL = '↻ Refresh';
let refreshArmed = false, refreshArmTimer = null;
function disarmRefresh(){ refreshArmed = false; clearTimeout(refreshArmTimer); refreshBtn.textContent = REFRESH_LABEL; }
async function checkSync(){
  try{
    const r = await fetch('state', {cache:'no-store'});
    if(!r.ok) return;
    const s = await r.json();
    refreshBtn.hidden = !s.stale;
    if(refreshBtn.hidden) disarmRefresh();
  }catch(e){ /* transient; try again next tick */ }
}
async function doRefresh(){
  refreshBtn.disabled = true; refreshBtn.textContent = 'Refreshing…';
  try{
    const r = await fetch('refresh', {method:'POST'});
    if(!r.ok) throw new Error(await r.text());
    location.reload();
  }catch(e){
    toast('Refresh failed: '+e.message);
    refreshBtn.disabled = false; refreshBtn.textContent = REFRESH_LABEL;
  }
}
// One click when there is nothing to lose. When comments are pending, the first
// click arms an inline confirm on the button itself (no native dialog, which some
// browsers block); a second click within 4s reloads and discards them.
refreshBtn.onclick = () => {
  const n = Object.keys(comments).length;
  if(n && !refreshArmed){
    refreshArmed = true;
    refreshBtn.textContent = `Discard ${n} comment${n!==1?'s':''} & refresh?`;
    refreshArmTimer = setTimeout(disarmRefresh, 4000);
    return;
  }
  clearTimeout(refreshArmTimer); refreshArmed = false;
  doRefresh();
};
setInterval(checkSync, 6000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) checkSync(); });
window.addEventListener('focus', checkSync);
checkSync();
const submitBtn = document.getElementById('submit');
const finishBg = document.getElementById('finishBg');
const finishSummary = document.getElementById('finishSummary');
const finishSubmit = document.getElementById('finishSubmit');
const finishApprove = document.getElementById('finishApprove');
function updateFinishBtn(){                 // Submit needs feedback; Approve is always allowed
  const n = Object.keys(comments).length;
  finishSubmit.disabled = (n===0 && !finishSummary.value.trim());
}
submitBtn.onclick = () => {                 // step 1: open the finish-review window
  const n = Object.keys(comments).length;
  const g = Object.values(comments).filter(c=>c.github).length;
  document.getElementById('finishSub').textContent =
    `${n} line comment${n!==1?'s':''} on this review${g?`, ${g} will be posted to GitHub`:''}. Add an optional overall comment, then submit or approve.`;
  finishBg.classList.add('show'); finishSummary.focus(); updateFinishBtn();
};
finishSummary.addEventListener('input', updateFinishBtn);
document.getElementById('finishCancel').onclick = () => finishBg.classList.remove('show');
finishBg.addEventListener('click', e => { if(e.target===finishBg) finishBg.classList.remove('show'); });
async function doSubmit(approved){          // step 2: submit the round, optionally approving
  const payload = {meta:META, summary:finishSummary.value.trim(), approved, comments:Object.values(comments)};
  finishSubmit.disabled = true; finishApprove.disabled = true;
  try{
    const res = await fetch('submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!res.ok) throw new Error(await res.text());
    const info = await res.json().catch(()=>({}));
    finishBg.classList.remove('show');
    let msg = approved
      ? 'Approved — Claude will make sure the PR is up and marked ready.'
      : `Submitted ${payload.comments.length} comment(s) — switch back to Claude.`;
    // "flagged", not "posted": the server no longer posts. Say what actually
    // happened, so the reviewer doesn't leave believing a PR comment is live.
    if(info.github_flagged) msg += ` · ${info.github_flagged} flagged for the PR — Claude will post`;
    toast(msg);
    submitBtn.textContent = approved ? 'Approved ✓' : 'Submitted ✓'; submitBtn.disabled = true;
  }catch(e){
    toast('Submit failed: '+e.message);
    finishSubmit.disabled = false; finishApprove.disabled = false;
  }
}
finishSubmit.onclick = () => doSubmit(false);
finishApprove.onclick = () => doSubmit(true);
function toast(msg){
  const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 4000);
}
// Fade the title only when it truly overflows two lines (so short titles keep crisp edges).
function fitTitle(){
  const t = document.getElementById('title'); if(!t) return;
  t.classList.remove('clamped');
  if(t.scrollHeight - t.clientHeight > 1) t.classList.add('clamped');
}
window.addEventListener('resize', fitTitle);
render();
fitTitle();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    page = b""
    out_path = "pr_comments.json"
    vendor_dir = ""
    token = ""  # random path segment every route is mounted under
    port = 0  # bound port, needed to validate Origin on POSTs
    # source the diff was generated from, so the server can recompute it on demand
    pr = None
    repo = None
    diff_file = None
    git_dir = None  # repo pinned at startup, for --git mode
    git_spec = None  # --git spec: "uncommitted", a ref, or an A...B range
    diff_sig = ""
    _last_sig = None
    _last_check = 0.0
    gh = None  # {owner, repo, pr, sha} when the diff is an associated PR, else None
    srv = None  # the running server, set by main() so a handler can shut it down
    once = False  # shut the server down after a successful /submit
    title = None  # --title override for the header (diff-file mode)
    # Serializes submissions across handler threads: the browser's double-click
    # guard doesn't bind other local callers, and concurrent /submit posts
    # would duplicate GitHub comments and race the OUT write.
    _submit_lock = threading.Lock()
    _submitted = False

    def log_message(self, *a):
        pass

    def current_sig(self):
        # Recompute the signature of the live source, throttled so PR mode does not
        # shell out to `gh` on every poll. Returns the served signature on failure
        # (a source we cannot read is not a reason to nag about being out of sync).
        now = time.monotonic()
        if Handler._last_sig is not None and (now - Handler._last_check) < 4.0:
            return Handler._last_sig
        try:
            sig = source_sig(get_diff(Handler.pr, Handler.repo, Handler.diff_file,
                                       Handler.git_dir, Handler.git_spec))
        except Exception:
            return Handler.diff_sig
        Handler._last_sig = sig
        Handler._last_check = now
        return sig

    def end_headers(self):
        # The token is a path segment, so it rides in the Referer of anything
        # the page navigates to — and preview renders links authored by whoever
        # wrote the diff. Hooked here rather than at each send_header site
        # because every response, including send_error's, funnels through this.
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cookie_name(self):
        # Cookies aren't port-scoped (RFC 6265): 127.0.0.1:8765 and :8766 share
        # one jar for host 127.0.0.1. Naming the cookie after the port is how
        # two concurrent reviews avoid clobbering each other's session.
        return f"local_review_{Handler.port}"

    def _route_path(self):
        # Every route is mounted under /<token>/. A request whose first path
        # segment isn't the token gets a bare 404 from the caller — this just
        # strips the prefix so route code below is unchanged. Returns None
        # when the token segment doesn't match.
        prefix = "/" + Handler.token
        p = self.path
        if p.startswith(prefix + "/"):
            return p[len(prefix):]
        # No token in the path: fall back to the session cookie set by the
        # one-time redirect in do_GET, so the browser never has to carry the
        # token again after the first hit.
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            jar = http.cookies.SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(self._cookie_name())
            if morsel and morsel.value == Handler.token:
                return p
        return None

    def _origin_ok(self):
        # Belt-and-braces behind the path token: reject a cross-origin POST
        # outright. Requests with no Origin header (curl, urllib, and the
        # served page's own same-origin fetches, which may omit it) pass.
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        allowed = {
            f"http://127.0.0.1:{Handler.port}",
            f"http://localhost:{Handler.port}",
            f"http://review.localhost:{Handler.port}",
        }
        return origin in allowed

    def do_GET(self):
        if self.path == "/" + Handler.token:
            # Redirect the slashless alias: the page's asset/fetch URLs are
            # relative, so serving it here would resolve them outside the
            # token prefix and render a 200 that doesn't work.
            self.send_response(301)
            self.send_header("Location", "/" + Handler.token + "/")
            self.end_headers()
            return
        if self.path == "/" + Handler.token + "/":
            # Exchange the token for a session cookie and land on the bare
            # path, so the browser's history/Referer never carry the token
            # again. Must be 302, not 301: a browser caches a 301 forever,
            # and a cached one would break a later review bound to this port.
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                f"{self._cookie_name()}={Handler.token}; HttpOnly; SameSite=Strict; Path=/",
            )
            self.end_headers()
            return
        path = self._route_path()
        if path is None:
            self.send_response(404)
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.page)
            return
        if path == "/state":
            sig = self.current_sig()
            self._send_json(200, {"stale": sig != Handler.diff_sig, "sig": sig})
            return
        if path.startswith("/vendor/"):
            name = path[len("/vendor/"):].split("?", 1)[0]
            fp = os.path.join(self.vendor_dir, name)
            if re.fullmatch(r"[\w.\-]+\.js", name) and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = self._route_path()
        if path is None:
            self.send_response(404)
            self.end_headers()
            return
        if not self._origin_ok():
            self.send_response(403)
            self.end_headers()
            return
        if path == "/refresh":
            try:
                diff = get_diff(Handler.pr, Handler.repo, Handler.diff_file,
                                 Handler.git_dir, Handler.git_spec)
                meta = get_meta(Handler.pr, Handler.repo, Handler.diff_file, Handler.title,
                                 Handler.git_dir, Handler.git_spec)
                Handler.gh = resolve_gh(Handler.pr, Handler.repo, Handler.diff_file, Handler.git_spec)
                # The reviewed SHA, not the current head. resolve_gh() pins it
                # once at launch, so it names the commit this page's diff was
                # rendered from. The agent posts with it; re-fetching headRefOid
                # at posting time would anchor comments to whatever the PR has
                # advanced to, which the reviewer never saw.
                meta["github"] = bool(Handler.gh)
                if Handler.gh:
                    meta["sha"] = Handler.gh["sha"]
                files = parse_diff(diff)
                Handler.page = build_page(files, meta)
                Handler.diff_sig = source_sig(diff)
                Handler._last_sig = Handler.diff_sig
                Handler._last_check = time.monotonic()
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
                return
            self._send_json(200, {"ok": True, "files": len(files)})
            return
        if path != "/submit":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        try:
            payload = json.loads(data)
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        # Claim the single submission slot: concurrent posts are rejected, and
        # in --once mode a completed submission keeps the slot (the server is
        # going down; stragglers get 409 instead of a duplicate post).
        with Handler._submit_lock:
            if Handler._submitted:
                self._send_json(409, {"ok": False, "error": "a submission is already in progress or completed"})
                return
            Handler._submitted = True
        # Durability is signalled via self._durable (set right after
        # os.replace), not a return value: a BrokenPipeError while writing the
        # response must not release the slot of a completed --once submission.
        # A durable stay-alive submission released its own slot before the
        # response (see _do_submit); this finally only covers failure paths.
        self._durable = False
        try:
            self._do_submit(payload)
        finally:
            if not self._durable:
                with Handler._submit_lock:
                    Handler._submitted = False

    def _do_submit(self, payload):
        # The temp file is created up front so an unwritable or missing --out
        # directory is caught before anything is printed: the human-readable
        # block below reads as a completed submission, and the caller polls for
        # OUT, so failing after printing would report a round that never landed.
        # (This preflight also used to protect a GitHub-posting step; the server
        # no longer posts — #381.)
        out_dir = os.path.dirname(os.path.abspath(self.out_path)) or "."
        try:
            if os.path.isdir(self.out_path):
                raise OSError(f"{self.out_path} is a directory")
            fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".out-", suffix=".tmp")
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"cannot write --out: {e}"})
            return
        # human-readable to stdout
        header = "REVIEW APPROVED" if payload.get("approved") else "REVIEW SUBMITTED"
        print(f"\n===== {header} =====", flush=True)
        if payload.get("approved"):
            if Handler.gh:
                print("APPROVED: ensure the PR is pushed to GitHub and marked ready for review.", flush=True)
            else:
                print("APPROVED: local review — no PR to update.", flush=True)
        if payload.get("summary"):
            print(f"SUMMARY: {payload['summary']}", flush=True)
        for c in payload.get("comments", []):
            end = c.get("endLine")
            rng = f"-{c['side']}{end}" if end and end != c["line"] else ""
            tag = " [dismiss-comments]" if c.get("kind") == "dismiss-comments" else ""
            tag += " [→github]" if c.get("github") else ""
            print(f"{c['file']}:{c['side']}{c['line']}{rng}{tag}  {c['text']}", flush=True)

        try:
            # No gh write happens here, by design. The page can reach /submit,
            # so anything this handler can do, a web page the user has open can
            # cause. A `gh` post is not undoable; the file write is. The button
            # now only records intent (github: true) and the AGENT posts, where
            # the write is visible in the transcript and interruptible.
            # See dev_docs/local-review.md, and issue #381.
            flagged = [c for c in payload.get("comments", []) if c.get("github")]
            if Handler.gh and flagged:
                print(f"GITHUB: {len(flagged)} comment(s) flagged for the PR — "
                      "the agent posts these, this server does not.", flush=True)
            print("============================\n", flush=True)

            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.out_path)
            self._durable = True
            # Release the slot HERE for a stay-alive server, before the
            # response goes out: a sequential caller that has seen the 200 must
            # never race the release (do_POST's finally runs after the flush,
            # and CI hit exactly that window as a spurious 409). A --once
            # server keeps the slot; do_POST's finally handles failure paths.
            if not Handler.once:
                with Handler._submit_lock:
                    Handler._submitted = False
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            os.unlink(tmp_path)
            raise

        # The --once shutdown is scheduled in a finally AFTER the response
        # write attempt: in the try so the flushed 200 can't race interpreter
        # exit (daemon handler threads die with serve_forever), in the finally
        # so a client disconnect mid-response still shuts the server down.
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "count": len(payload.get("comments", [])),
                                         "github_flagged": len(flagged)}).encode())
            self.wfile.flush()
        finally:
            if Handler.once and Handler.srv:
                threading.Thread(target=Handler.srv.shutdown, daemon=True).start()



# Longest first, so an alternation can never match a prefix of another marker.
TEMPLATE_MARKERS = ("/*__DIFF_JSON__*/[]", "/*__META_JSON__*/{}", "__TITLE_HTML__", "__TITLE__")
TEMPLATE_MARKER = re.compile("|".join(re.escape(m) for m in TEMPLATE_MARKERS))


def build_page(files, meta):
    title = f"Review · {meta.get('title','diff')}"
    if meta.get("number"):
        title = f"#{meta['number']} · {meta.get('title','')}"
    if meta.get("url"):
        title_html = f'<a href="{esc_py(meta["url"])}" target="_blank">#{meta.get("number","")}</a> {esc_py(meta.get("title",""))}'
    else:
        title_html = esc_py(meta.get("title", "local diff"))
    # Every placeholder in ONE pass, never chained .replace() calls. Each
    # substituted value can itself contain another marker's literal text — a
    # diff of this very file carries all four, and a PR title can carry any of
    # them — so a second pass would substitute into what the first just
    # inserted and corrupt it. re.sub never rescans its own replacements.
    subs = {
        "/*__DIFF_JSON__*/[]": _json_for_script(files),
        "/*__META_JSON__*/{}": _json_for_script(meta),
        "__TITLE__": esc_py(title),
        "__TITLE_HTML__": title_html,
    }
    html = TEMPLATE_MARKER.sub(lambda m: subs[m.group(0)], PAGE)
    return html.encode("utf-8")


def esc_py(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _json_for_script(obj):
    # Interpolated into a <script> element: a diff line containing </script>
    # (or a raw U+2028/U+2029 line terminator, which json.dumps leaves as-is)
    # must not be able to break out of the script context or the JS string.
    return (
        json.dumps(obj)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def bind_server(port):
    """Bind the ThreadingHTTPServer. An explicit --port must bind exactly as
    asked, so its OSError propagates. With no --port, try the stable default
    8765 first and fall back to autoselect (port 0) if that one's busy.
    Returns (server, fell_back)."""
    if port is not None:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler), False
    try:
        return ThreadingHTTPServer(("127.0.0.1", 8765), Handler), False
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise  # permission/resource errors are not contention; fail loud
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr", nargs="?", help="PR number")
    ap.add_argument("--repo")
    ap.add_argument("--diff-file")
    ap.add_argument("--git", help="live diff spec: uncommitted | <ref> | <A>...<B>")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--out", default="pr_comments.json")
    ap.add_argument("--title", help="human title for the header (else the --diff-file path is shown)")
    ap.add_argument("--once", action="store_true", help="shut down after a successful /submit")
    args = ap.parse_args()

    if sum(bool(x) for x in (args.pr, args.diff_file, args.git)) != 1:
        print("error: provide exactly one of a PR number, --diff-file, or --git", file=sys.stderr)
        sys.exit(2)

    git_dir = None
    try:
        if args.git:
            # Pin the repo once at startup: every later git diff runs against
            # this dir, so a cwd change after launch can't shift the source.
            git_dir = sh(["git", "rev-parse", "--show-toplevel"]).strip()
        diff = get_diff(args.pr, args.repo, args.diff_file, git_dir, args.git)
        meta = get_meta(args.pr, args.repo, args.diff_file, args.title, git_dir, args.git)
        gh = resolve_gh(args.pr, args.repo, args.diff_file, args.git)
    except RuntimeError as e:
        # Flatten here, not in sh(): the /refresh JSON error path reuses the
        # same RuntimeError and wants its full multiline stderr (e.g. git's
        # "unknown revision" fatal plus its usage hint), while this one-line
        # stderr message is the startup contract the skill polls for.
        msg = "; ".join(ln.strip() for ln in str(e).splitlines() if ln.strip())
        print(f"error: {msg}", file=sys.stderr)
        sys.exit(1)
    # See the note at the /refresh site: this is the reviewed SHA, pinned at
    # launch, and it is what the agent must post against.
    meta["github"] = bool(gh)
    if gh:
        meta["sha"] = gh["sha"]
    files = parse_diff(diff)
    Handler.page = build_page(files, meta)
    Handler.out_path = args.out
    Handler.vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    Handler.pr = args.pr
    Handler.repo = args.repo
    Handler.diff_file = args.diff_file
    Handler.git_dir = git_dir
    Handler.git_spec = args.git
    Handler.title = args.title
    Handler.diff_sig = source_sig(diff)
    Handler.gh = gh
    Handler.once = args.once
    Handler.token = "-".join(secrets.choice(WORDLIST) for _ in range(4))

    srv, fell_back = bind_server(args.port)
    Handler.srv = srv
    port = srv.server_address[1]
    Handler.port = port
    if fell_back:
        print(f"port 8765 busy; using {port}", file=sys.stderr)
    machine_url = f"http://127.0.0.1:{port}/{Handler.token}/"
    vanity_url = f"http://review.localhost:{port}/{Handler.token}/"
    print(f"Review UI: {vanity_url}   ({len(files)} files)  out={args.out}", flush=True)
    print(f"LOCAL_REVIEW_URL={machine_url}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
