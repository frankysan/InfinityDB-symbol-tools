#!/usr/bin/env python3
"""
Unified SVG font audit and text-to-path conversion pipeline.

What it does
------------
1) Recursively scan an input directory for SVG files.
2) Classify each parsed SVG into:
     - fonts_available : SVG has active text and all used fonts are resolvable
     - fonts_missing   : SVG has active text and at least one used font is missing/ambiguous
     - no_active_text  : SVG has no non-whitespace active text
3) Copy SVGs into a separate output directory tree preserving relative paths.
4) Convert SVGs from fonts_available/ into text_as_paths/:
     - normalizes many font aliases to CSS family names when safe
     - uses Inkscape by default, with persistent-shell and experimental usvg backends
     - retains Inkscape object-to-path fallback when Inkscape is selected
     - validates that no meaningful text remains
5) Optionally detect exact and visually identical SVGs across the ENTIRE source set.
   Duplicate representatives are chosen before text-to-path conversion so redundant
   files do not consume conversion time.
6) Optionally separate redundant classified copies under output_root/duplicates/.
7) Write CSV reports under output_root/reports/

Dependencies
------------
pip install -e ".[symbols]"

External tools:
    cargo install resvg   # default duplicate renderer
    cargo install usvg    # experimental text-to-path backend
    Inkscape              # text-to-path backend; persistent shell mode available

This script is written for Windows-friendly font discovery but does not
modify the input tree. All generated files go under the separate output root.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import unquote, urlparse


def optional_import(module_name: str) -> Any:
    """Import an optional dependency without creating static import errors."""
    try:
        return import_module(module_name)
    except ImportError:  # pragma: no cover - exercised on minimal installations
        return None


_fonttools_ttlib = optional_import("fontTools.ttLib")
TTCollection: Any = getattr(_fonttools_ttlib, "TTCollection", None)
TTFont: Any = getattr(_fonttools_ttlib, "TTFont", None)
cssselect2: Any = optional_import("cssselect2")
tinycss2: Any = optional_import("tinycss2")
winreg: Any = optional_import("winreg")


# Keep ElementTree serialization compatible with ordinary SVG consumers.
# Without these registrations, any SVG that we rewrite (for example after
# removing empty text placeholders) is emitted as <ns0:svg> with generated
# ns1/ns2 prefixes.  The XML is valid, but some lightweight SVG consumers
# such as Windows Explorer's thumbnail handler may fail to recognize it.
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
ET.register_namespace("sodipodi", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd")
ET.register_namespace("inkscape", "http://www.inkscape.org/namespaces/inkscape")
ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ET.register_namespace("cc", "http://creativecommons.org/ns#")
ET.register_namespace("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")


# ---------- configuration ----------

GENERIC_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
    "emoji",
    "math",
    "fangsong",
}

TEXT_ROOT_TAGS = {"text", "flowRoot"}
TEXT_RELATED_TAGS = {"text", "textPath", "tspan", "flowRoot", "flowPara", "flowSpan", "tref"}

CMAP_SUFFIXES = [
    re.compile(r"-(?:90ms|90msp|83pv)-RKSJ-[HV]$", re.I),
    re.compile(r"-KSC(?:pc)?-EUC-[HV]$", re.I),
    re.compile(r"-(?:GB|GBK|GBpc)-EUC-[HV]$", re.I),
    re.compile(r"-ETen-B5-[HV]$", re.I),
    re.compile(r"-Identity-[HV]$", re.I),
    re.compile(r"-Uni(?:JIS|KS|GB|CNS)-[A-Za-z0-9-]+-[HV]$", re.I),
]

EXCLUDED_DIR_NAMES = {
    "fonts_available",
    "fonts_missing",
    "no_active_text",
    "text_as_paths",
    "reports",
    "duplicates",
    "_failed_inkscape",
    "_failed_normalized",
}

WEIGHT_NAME_MAP = {
    "thin": "100",
    "hairline": "100",
    "extra light": "200",
    "extralight": "200",
    "ultra light": "200",
    "ultralight": "200",
    "light": "300",
    "regular": "400",
    "normal": "400",
    "roman": "400",
    "book": "400",
    "medium": "500",
    "semi bold": "600",
    "semibold": "600",
    "demi bold": "600",
    "demibold": "600",
    "bold": "700",
    "extra bold": "800",
    "extrabold": "800",
    "ultra bold": "800",
    "ultrabold": "800",
    "black": "900",
    "heavy": "900",
}

STRETCH_KEYWORDS = {
    "ultra-condensed": "ultra-condensed",
    "extra-condensed": "extra-condensed",
    "condensed": "condensed",
    "semi-condensed": "semi-condensed",
    "normal": "normal",
    "semi-expanded": "semi-expanded",
    "expanded": "expanded",
    "extra-expanded": "extra-expanded",
    "ultra-expanded": "ultra-expanded",
}


# ---------- general helpers ----------

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def normal_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def compact_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "", normal_key(value))


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FONT_ALIAS_CONFIG = Path(__file__).resolve().with_name("font-aliases.json")
_FONT_OVERRIDE_FIELDS = {"reference", "lookup", "subfamily", "weight", "style", "stretch"}


def require_font_dependencies() -> None:
    missing = []
    if TTFont is None or TTCollection is None:
        missing.append("fonttools")
    if tinycss2 is None:
        missing.append("tinycss2")
    if cssselect2 is None:
        missing.append("cssselect2")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing symbol font dependency/dependencies: {joined}. "
            "Install InfinityDB with the 'symbols' extra."
        )


def load_font_reference_overrides(
    path: Path = DEFAULT_FONT_ALIAS_CONFIG,
) -> dict[str, dict[str, str]]:
    """Load validated Infinity-specific legacy/exported font aliases."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load font alias config {path}: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "overrides"}:
        raise ValueError(
            "Font alias config must contain exactly schemaVersion and overrides"
        )
    if document["schemaVersion"] != 1:
        raise ValueError("Font alias config schemaVersion must be 1")
    rows = document["overrides"]
    if not isinstance(rows, list):
        raise ValueError("Font alias config overrides must be an array")

    result: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        context = f"Font alias config overrides[{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{context} must be an object")
        unknown = set(row) - _FONT_OVERRIDE_FIELDS
        if unknown:
            raise ValueError(
                f"{context} has unknown field(s): {', '.join(sorted(unknown))}"
            )
        if "reference" not in row or "lookup" not in row:
            raise ValueError(f"{context} must contain reference and lookup")
        normalized: dict[str, str] = {}
        for field, value in row.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{context}.{field} must be a non-empty string")
            normalized[field] = value.strip()
        key = normal_key(normalized.pop("reference"))
        if key in result:
            raise ValueError(f"{context}.reference duplicates another alias: {key}")
        result[key] = normalized
    return result


# Maintained project knowledge lives in config/symbols/font-aliases.json.
FONT_REFERENCE_OVERRIDES = load_font_reference_overrides()


def split_font_family_list(value: str) -> list[str]:
    result = []
    current = []
    quote = None
    escaped = False

    for ch in value:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == ",":
            name = "".join(current).strip()
            if name:
                result.append(name)
            current = []
            continue
        current.append(ch)

    name = "".join(current).strip()
    if name:
        result.append(name)
    return result


def css_quote_font_family(family: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", family):
        return family
    return "'" + family.replace("\\", "\\\\").replace("'", "\\'") + "'"


def strip_cmap_suffix(name: str) -> str:
    for pattern in CMAP_SUFFIXES:
        stripped = pattern.sub("", name)
        if stripped != name:
            return stripped
    return name


def ensure_clean_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_preserving_tree(source: Path, root: Path, destination_root: Path):
    relative = source.relative_to(root)
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


# ---------- font indexing ----------

@dataclass(frozen=True)
class FontFace:
    family: str
    subfamily: str
    full_name: str
    postscript_name: str
    path: str
    weight: str
    style: str
    stretch: str


@dataclass(frozen=True)
class AliasEntry:
    alias: str
    kind: str
    face: FontFace


def name_values(font: Any, name_id: int) -> set[str]:
    values = set()
    if "name" not in font:
        return values
    for record in font["name"].names:
        if record.nameID != name_id:
            continue
        try:
            value = record.toUnicode().strip()
        except Exception:
            continue
        if value:
            values.add(value)
    return values


def first_name(font: Any, *ids: int) -> str:
    if "name" not in font:
        return ""
    table = font["name"]
    for name_id in ids:
        try:
            value = table.getDebugName(name_id)
        except Exception:
            value = None
        if value:
            return value.strip()
    return ""


def style_from_subfamily(subfamily: str) -> str:
    key = normal_key(subfamily)
    if "italic" in key:
        return "italic"
    if "oblique" in key:
        return "oblique"
    return "normal"


def stretch_from_subfamily(subfamily: str) -> str:
    key = normal_key(subfamily).replace(" ", "-")
    for candidate in STRETCH_KEYWORDS:
        if candidate in key:
            return STRETCH_KEYWORDS[candidate]
    return "normal"


def weight_from_font(font: Any, subfamily: str) -> str:
    try:
        if "OS/2" in font:
            value = int(font["OS/2"].usWeightClass)
            if 1 <= value <= 1000:
                return str(value)
    except Exception:
        pass

    key = normal_key(subfamily)
    for name, weight in WEIGHT_NAME_MAP.items():
        if name in key:
            return weight

    # Adobe style shorthand occasionally uses M for Medium
    if key == "m":
        return "500"

    return "400"


def get_face(font: Any, path: Path) -> FontFace | None:
    if "name" not in font:
        return None

    table = font["name"]

    try:
        family = table.getBestFamilyName()
    except Exception:
        family = None
    if not family:
        family = first_name(font, 16, 1, 21)

    try:
        subfamily = table.getBestSubFamilyName()
    except Exception:
        subfamily = None
    if not subfamily:
        subfamily = first_name(font, 17, 2, 22)

    full_name = first_name(font, 4)
    postscript = first_name(font, 6)

    if not family:
        return None

    subfamily = subfamily or "Regular"

    return FontFace(
        family=family,
        subfamily=subfamily,
        full_name=full_name or family,
        postscript_name=postscript,
        path=str(path),
        weight=weight_from_font(font, subfamily),
        style=style_from_subfamily(subfamily),
        stretch=stretch_from_subfamily(subfamily),
    )


def aliases_for_font(font: Any, face: FontFace) -> list[AliasEntry]:
    aliases = {}

    for name_id in (1, 16, 21):
        for value in name_values(font, name_id):
            aliases[(value, "family")] = AliasEntry(value, "family", face)

    for value in name_values(font, 4):
        aliases[(value, "full-name")] = AliasEntry(value, "full-name", face)

    for value in name_values(font, 6):
        aliases[(value, "postscript")] = AliasEntry(value, "postscript", face)

    if (
        face.family
        and face.subfamily
        and normal_key(face.subfamily) not in {"regular", "normal", "roman"}
    ):
        value = f"{face.family} {face.subfamily}"
        aliases[(value, "family+style")] = AliasEntry(value, "family+style", face)

    return list(aliases.values())


def registry_font_paths() -> set[Path]:
    result = set()
    if winreg is None:
        return result

    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    locations = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ]

    for hive, key_path in locations:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue

        index = 0
        while True:
            try:
                _, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1

            if not isinstance(value, str):
                continue

            path = Path(os.path.expandvars(value))
            if not path.is_absolute():
                path = windows_fonts / path
            if path.exists():
                result.add(path.resolve())

        winreg.CloseKey(key)

    return result


def discover_font_files() -> set[Path]:
    paths = registry_font_paths()
    directories = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
    ]
    extensions = {".ttf", ".otf", ".ttc", ".otc"}

    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.suffix.casefold() in extensions:
                paths.add(path.resolve())

    return paths


def load_font_index():
    require_font_dependencies()
    exact = defaultdict(list)
    compact = defaultdict(list)
    font_files = discover_font_files()
    faces_loaded = 0

    for path in sorted(font_files):
        suffix = path.suffix.casefold()
        try:
            if suffix in {".ttc", ".otc"}:
                collection = TTCollection(str(path), lazy=True)
                fonts = collection.fonts
            else:
                collection = None
                fonts = [TTFont(str(path), lazy=True)]

            for font in fonts:
                face = get_face(font, path)
                if not face:
                    continue
                faces_loaded += 1
                for entry in aliases_for_font(font, face):
                    exact[normal_key(entry.alias)].append(entry)
                    compact[compact_key(entry.alias)].append(entry)

            if collection is not None:
                collection.close()
            else:
                for font in fonts:
                    font.close()

        except Exception as e:
            print(f"WARNING: Could not inspect font {path}: {e}", file=sys.stderr)

    return exact, compact, len(font_files), faces_loaded


def unique_families(entries: list[AliasEntry]) -> set[str]:
    return {normal_key(entry.face.family) for entry in entries}


def choose_face(entries: list[AliasEntry]) -> AliasEntry:
    def score(entry):
        sub = normal_key(entry.face.subfamily)
        return 0 if sub in {"regular", "normal", "roman", "book"} else 1
    return sorted(
        entries,
        key=lambda e: (
            score(e),
            e.face.subfamily.casefold(),
            e.face.full_name.casefold(),
        ),
    )[0]


def ambiguous_result():
    return {
        "status": "AMBIGUOUS",
        "match_type": "",
        "matched_name": "",
        "family": "",
        "subfamily": "",
        "full_name": "",
        "postscript": "",
        "font_file": "",
        "normalize": "YES",
        "weight": "",
        "style": "",
        "stretch": "",
    }


def match_result(entry: AliasEntry, match_type: str):
    return {
        "status": "FOUND",
        "match_type": match_type,
        "matched_name": entry.alias,
        "family": entry.face.family,
        "subfamily": entry.face.subfamily,
        "full_name": entry.face.full_name,
        "postscript": entry.face.postscript_name,
        "font_file": entry.face.path,
        "normalize": "NO" if entry.kind == "family" else "YES",
        "weight": entry.face.weight,
        "style": entry.face.style,
        "stretch": entry.face.stretch,
    }


def resolve_font_reference_override(
    reference: str,
    exact_index,
    compact_index,
    *,
    overrides=None,
):
    if overrides is None:
        overrides = FONT_REFERENCE_OVERRIDES
    override = overrides.get(normal_key(reference))
    if override is None:
        return None

    lookup = override["lookup"]

    entries = exact_index.get(normal_key(lookup), [])
    if not entries:
        entries = compact_index.get(compact_key(lookup), [])

    if not entries:
        # Do not manufacture a FOUND result if the intended installed font is
        # not actually present.  Falling back to ordinary matching will leave
        # the original reference MISSING, which is the safe classification.
        return None

    if len(unique_families(entries)) != 1:
        return ambiguous_result()

    chosen = choose_face(entries)
    result = match_result(chosen, "reference-override")
    result["normalize"] = "YES"

    # Variable-font aliases such as Jura-Bold and AdventPro-SemiBold need the
    # requested CSS weight rather than the variable font's default OS/2 weight.
    for field in ("subfamily", "weight", "style", "stretch"):
        value = override.get(field)
        if value:
            result[field] = value

    return result


def find_font(reference: str, exact_index, compact_index, *, overrides=None):
    key = normal_key(reference)
    if key in GENERIC_FAMILIES:
        return {
            "status": "GENERIC",
            "match_type": "generic",
            "matched_name": reference,
            "family": reference,
            "subfamily": "",
            "full_name": "",
            "postscript": "",
            "font_file": "",
            "normalize": "NO",
            "weight": "",
            "style": "",
            "stretch": "",
        }

    overridden = resolve_font_reference_override(
        reference,
        exact_index,
        compact_index,
        overrides=overrides,
    )
    if overridden is not None:
        return overridden

    entries = exact_index.get(key, [])
    if entries:
        if len(unique_families(entries)) == 1:
            chosen = choose_face(entries)
            return match_result(chosen, chosen.kind)
        return ambiguous_result()

    stripped = strip_cmap_suffix(reference)
    if stripped != reference:
        entries = exact_index.get(normal_key(stripped), [])
        if entries:
            if len(unique_families(entries)) == 1:
                result = match_result(choose_face(entries), "cmap-postscript")
                result["normalize"] = "YES"
                return result
            return ambiguous_result()

    for candidate in (reference, stripped):
        entries = compact_index.get(compact_key(candidate), [])
        if not entries:
            continue
        if len(unique_families(entries)) == 1:
            result = match_result(choose_face(entries), "normalized-alias")
            result["normalize"] = "YES"
            return result
        return ambiguous_result()

    return {
        "status": "MISSING",
        "match_type": "",
        "matched_name": "",
        "family": "",
        "subfamily": "",
        "full_name": "",
        "postscript": "",
        "font_file": "",
        "normalize": "",
        "weight": "",
        "style": "",
        "stretch": "",
    }


# ---------- CSS / SVG analysis ----------

def parse_declarations(content) -> list[tuple[str, str, bool]]:
    result = []
    if content is None:
        return result

    declarations = tinycss2.parse_declaration_list(
        content, skip_comments=True, skip_whitespace=True
    )

    for decl in declarations:
        if decl.type != "declaration":
            continue
        name = decl.lower_name
        value = tinycss2.serialize(decl.value).strip()
        if value:
            result.append((name, value, decl.important))
    return result


def build_css_matcher(root):
    require_font_dependencies()
    matcher = cssselect2.Matcher()
    declared_fonts = set()

    for elem in root.iter():
        if local_name(elem.tag) != "style":
            continue

        css_text = elem.text or ""
        rules = tinycss2.parse_stylesheet(css_text, skip_comments=True, skip_whitespace=True)

        for rule in rules:
            if rule.type != "qualified-rule":
                continue

            selector_text = tinycss2.serialize(rule.prelude).strip()
            declarations = parse_declarations(rule.content)

            family_decls = [
                (value, important)
                for name, value, important in declarations
                if name == "font-family"
            ]
            for value, _ in family_decls:
                declared_fonts.update(split_font_family_list(value))

            if not family_decls:
                continue

            try:
                selectors = cssselect2.compile_selector_list(selector_text)
            except Exception:
                continue

            for selector in selectors:
                matcher.add_selector(selector, family_decls)

    return matcher, declared_fonts


def parse_inline_font_family(style_value: str):
    chosen = None
    for name, value, important in parse_declarations(style_value):
        if name == "font-family":
            chosen = (value, important)
    return chosen


def better_candidate(current, candidate):
    # candidate = (important, specificity_tuple, order, value)
    if current is None:
        return candidate
    if candidate[0] != current[0]:
        return candidate if candidate[0] else current
    if candidate[1] != current[1]:
        return candidate if candidate[1] > current[1] else current
    return candidate if candidate[2] >= current[2] else current


def resolve_effective_font_families(root):
    matcher, declared_fonts = build_css_matcher(root)
    wrapped_root = cssselect2.ElementWrapper.from_xml_root(root)
    effective = {}

    def walk(wrapper, inherited_value=None):
        elem = wrapper.etree_element
        candidate = None

        attr_value = elem.attrib.get("font-family")
        if attr_value:
            declared_fonts.update(split_font_family_list(attr_value))
            candidate = better_candidate(candidate, (False, (0, 0, 0), -1, attr_value))

        for specificity, order, pseudo, payload in matcher.match(wrapper):
            if pseudo is not None:
                continue
            for value, important in payload:
                candidate = better_candidate(candidate, (important, specificity, order, value))

        style_value = elem.attrib.get("style")
        if style_value:
            inline = parse_inline_font_family(style_value)
            if inline:
                declared_fonts.update(split_font_family_list(inline[0]))
                value, important = inline
                candidate = better_candidate(
                    candidate,
                    (important, (1_000_000, 0, 0), 1_000_000, value),
                )

        if candidate is None:
            current_value = inherited_value
        else:
            current_value = candidate[3].strip()
            lower = current_value.casefold()
            if lower in {"inherit", "unset"}:
                current_value = inherited_value
            elif lower in {"initial", "revert", "revert-layer"}:
                current_value = None

        effective[id(elem)] = current_value
        for child in wrapper.iter_children():
            walk(child, current_value)

    walk(wrapped_root)
    return effective, declared_fonts


def has_text_ancestor(elem, parents):
    current = elem
    while current is not None:
        if local_name(current.tag) in TEXT_ROOT_TAGS:
            return True
        current = parents.get(current)
    return False


def collect_used_fonts(root):
    effective, declared_fonts = resolve_effective_font_families(root)
    parents = {child: parent for parent in root.iter() for child in parent}

    used = defaultdict(int)
    text_runs = 0
    found_active_text = False

    for elem in root.iter():
        if not has_text_ancestor(elem, parents):
            continue

        if elem.text and elem.text.strip():
            found_active_text = True
            family_value = effective.get(id(elem))
            if family_value:
                for family in split_font_family_list(family_value):
                    used[family] += 1
            text_runs += 1

        for child in list(elem):
            if child.tail and child.tail.strip():
                found_active_text = True
                family_value = effective.get(id(elem))
                if family_value:
                    for family in split_font_family_list(family_value):
                        used[family] += 1
                text_runs += 1

    return used, declared_fonts, text_runs, found_active_text


def count_empty_text_objects(root):
    count = 0
    for elem in root.iter():
        if local_name(elem.tag) not in {"text", "flowRoot"}:
            continue
        if not "".join(elem.itertext()).strip():
            count += 1
    return count


def scan_svg(path: Path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as e:
        return None, str(e)

    used_fonts, declared_fonts, text_runs, found_active_text = collect_used_fonts(root)

    return {
        "used_fonts": used_fonts,
        "declared_fonts": declared_fonts,
        "text_runs": text_runs,
        "found_active_text": found_active_text,
        "empty_text_objects": count_empty_text_objects(root),
    }, None


# ---------- classification ----------


class ClassificationResult(TypedDict):
    available: int
    missing: int
    no_active_text: int
    aliases: int
    implicit_default: int
    empty_placeholder_files: int
    unused_decl_count: int
    parse_errors: int
    file_categories: dict[str, str]


def classify_svgs(
    input_root: Path,
    output_root: Path,
    exact_index: Any,
    compact_index: Any,
) -> ClassificationResult:
    available_root = output_root / "fonts_available"
    missing_root = output_root / "fonts_missing"
    no_active_root = output_root / "no_active_text"
    reports_root = output_root / "reports"

    for p in (available_root, missing_root, no_active_root, reports_root):
        ensure_clean_dir(p)

    svg_files = sorted(
        path for path in input_root.rglob("*.svg")
        if not any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(input_root).parts)
    )

    rows = []
    unused_rows = []
    parse_errors = []
    counts: ClassificationResult = {
        "available": 0,
        "missing": 0,
        "no_active_text": 0,
        "aliases": 0,
        "implicit_default": 0,
        "empty_placeholder_files": 0,
        "unused_decl_count": 0,
        "parse_errors": 0,
        "file_categories": {},
    }
    file_categories = counts["file_categories"]

    print(f"Scanning SVGs under: {input_root}")
    print(f"SVG files found: {len(svg_files)}")
    print()

    for svg in svg_files:
        relative = svg.relative_to(input_root)
        scan, error = scan_svg(svg)

        if scan is None:
            print(f"ERROR      {relative}: {error}")
            counts["parse_errors"] += 1
            parse_errors.append({"file": str(relative), "error": error})
            file_categories[str(relative)] = "parse_error"
            continue

        used_fonts = scan["used_fonts"]
        declared_fonts = scan["declared_fonts"]
        empty_count = scan["empty_text_objects"]
        found_active_text = scan["found_active_text"]

        if empty_count:
            counts["empty_placeholder_files"] += 1

        used_keys = {normal_key(x) for x in used_fonts}
        for declared in sorted(declared_fonts, key=str.casefold):
            if normal_key(declared) not in used_keys:
                unused_rows.append({
                    "file": str(relative),
                    "font_reference": declared,
                    "reason": "declared but not used by non-empty text",
                })
                counts["unused_decl_count"] += 1

        if not found_active_text:
            copy_preserving_tree(svg, input_root, no_active_root)
            counts["no_active_text"] += 1
            file_categories[str(relative)] = "no_active_text"
            print(f"NO TEXT    {relative}")
            continue

        # Active text exists. If no font-family could be resolved, treat as available/default.
        if not used_fonts:
            copy_preserving_tree(svg, input_root, available_root)
            counts["available"] += 1
            counts["implicit_default"] += 1
            file_categories[str(relative)] = "fonts_available"
            extra = f" ({empty_count} empty text placeholder(s) ignored)" if empty_count else ""
            print(f"DEFAULT    {relative}{extra}")
            continue

        file_missing = False
        file_has_alias = False

        for font in sorted(used_fonts, key=str.casefold):
            result = find_font(font, exact_index, compact_index)
            if result["status"] in {"MISSING", "AMBIGUOUS"}:
                file_missing = True
            if result["normalize"] == "YES":
                file_has_alias = True

            rows.append({
                "file": str(relative),
                "font_reference": font,
                "text_runs": used_fonts[font],
                "empty_text_objects": empty_count,
                **result,
            })

        if file_missing:
            copy_preserving_tree(svg, input_root, missing_root)
            counts["missing"] += 1
            file_categories[str(relative)] = "fonts_missing"
            print(f"MISSING    {relative}")
        else:
            copy_preserving_tree(svg, input_root, available_root)
            counts["available"] += 1
            file_categories[str(relative)] = "fonts_available"
            if file_has_alias:
                counts["aliases"] += 1
                marker = "ALIAS"
            else:
                marker = "AVAILABLE"
            extra = f" ({empty_count} empty text placeholder(s) ignored)" if empty_count else ""
            print(f"{marker:<10} {relative}{extra}")

    report_path = reports_root / "svg-font-report.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "file",
            "font_reference",
            "text_runs",
            "empty_text_objects",
            "status",
            "match_type",
            "matched_name",
            "family",
            "subfamily",
            "full_name",
            "postscript",
            "normalize",
            "weight",
            "style",
            "stretch",
            "font_file",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    missing_summary_path = reports_root / "missing-fonts.csv"
    usage = defaultdict(set)
    for row in rows:
        if row["status"] in {"MISSING", "AMBIGUOUS"}:
            usage[row["font_reference"]].add(row["file"])
    with missing_summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["font_reference", "files"])
        for font in sorted(usage, key=str.casefold):
            writer.writerow([font, len(usage[font])])

    unused_path = reports_root / "unused-font-declarations.csv"
    with unused_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "font_reference", "reason"])
        writer.writeheader()
        writer.writerows(unused_rows)

    parse_path = reports_root / "svg-parse-errors.csv"
    with parse_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "error"])
        writer.writeheader()
        writer.writerows(parse_errors)

    return counts


# ---------- normalization for conversion ----------

def build_parent_map(root):
    return {child: parent for parent in root.iter() for child in parent}


def _local_image_reference_path(href: str, source_svg: Path) -> Path | None:
    """Resolve a local SVG image reference without treating URLs as files."""
    value = href.strip()
    if not value or value.startswith("#") or value.casefold().startswith("data:"):
        return None

    # urlparse() interprets a Windows drive letter as a URI scheme, so detect
    # native absolute paths first.
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return Path(value)

    parsed = urlparse(value)
    if parsed.scheme == "file":
        raw_path = unquote(parsed.path)
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        elif os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        return Path(raw_path)
    if parsed.scheme or parsed.netloc:
        return None

    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        path = source_svg.parent / path
    return path


def _remove_unresolved_external_images(root, source_svg: Path) -> list[str]:
    """Remove broken local image links from a temporary conversion copy."""
    parents = build_parent_map(root)
    removed = []
    xlink_href = "{http://www.w3.org/1999/xlink}href"

    for elem in list(root.iter()):
        if local_name(elem.tag) != "image":
            continue
        href = elem.attrib.get("href") or elem.attrib.get(xlink_href)
        if not href:
            continue
        referenced = _local_image_reference_path(href, source_svg)
        if referenced is None or referenced.exists():
            continue
        parent = parents.get(elem)
        if parent is None:
            continue
        parent.remove(elem)
        removed.append(href)

    return removed


def parse_style_attribute(style_value: str) -> dict[str, str]:
    result = {}
    for name, value, _important in parse_declarations(style_value):
        result[name] = value
    return result


def serialize_style_attribute(mapping: dict[str, str]) -> str:
    return "; ".join(f"{k}:{v}" for k, v in mapping.items())


def get_property_from_elem(elem, prop_name: str):
    attr_value = elem.attrib.get(prop_name)
    if attr_value:
        return attr_value
    style_value = elem.attrib.get("style")
    if style_value:
        mapping = parse_style_attribute(style_value)
        return mapping.get(prop_name)
    return None


def set_property_on_elem(elem, prop_name: str, value: str):
    # Keep using direct SVG attrs for core text properties, because they are simple and explicit.
    elem.set(prop_name, value)


def determine_normalized_face(font_reference: str, exact_index, compact_index):
    result = find_font(font_reference, exact_index, compact_index)

    if result["status"] in {"MISSING", "AMBIGUOUS"}:
        return result

    # Generic families should not be normalized further.
    if result["status"] == "GENERIC":
        return result

    return result


def normalize_svg_for_conversion(source_svg: Path, target_svg: Path, exact_index, compact_index):
    """
    Rewrite aliases in a temporary SVG when safe.
    Returns dict with warnings and alias descriptions.
    """
    tree = ET.parse(source_svg)
    root = tree.getroot()
    warnings = []
    alias_descriptions = []
    changed = 0

    removed_image_references = _remove_unresolved_external_images(root, source_svg)
    if removed_image_references:
        warnings.append(
            f"Removed {len(removed_image_references)} unresolved external image "
            "reference(s): " + ", ".join(removed_image_references)
        )

    def normalize_font_value(value: str):
        nonlocal changed
        output_families = []
        seen_families = set()
        normalized_any = False

        for family_ref in split_font_family_list(value):
            info = determine_normalized_face(family_ref, exact_index, compact_index)

            if info["status"] in {"MISSING", "AMBIGUOUS"}:
                target_family = family_ref
            elif info["status"] == "GENERIC":
                target_family = family_ref
            elif info["normalize"] == "YES":
                target_family = info["family"] or family_ref
                normalized_any = True
                changed += 1

                alias_descriptions.append(
                    f"{family_ref} -> {target_family} "
                    f"[{info['subfamily']}; weight={info['weight']}; "
                    f"style={info['style']}; stretch={info['stretch']}]"
                )
            else:
                target_family = family_ref

            # A surprising number of exported SVGs contain both a PostScript
            # face name and a legacy alias for the same installed face, e.g.
            # "MicrogrammaD-BoldExte, MicrogrammaDBolExt".  Once both names
            # normalize to the same CSS family, retaining the duplicate fallback
            # adds no information and makes face-property inference harder.
            family_key = normal_key(target_family)
            if family_key not in seen_families:
                seen_families.add(family_key)
                output_families.append(target_family)

        return ", ".join(css_quote_font_family(x) for x in output_families), normalized_any

    def common_normalized_face(value: str):
        """Return one face description when all face-specific refs agree.

        Generic fallbacks are ignored.  Missing/ambiguous refs, conflicting
        canonical families, or conflicting CSS face properties make the result
        unsafe, in which case no weight/style/stretch is injected.
        """
        resolved = []

        for family_ref in split_font_family_list(value):
            info = determine_normalized_face(family_ref, exact_index, compact_index)
            if info["status"] in {"MISSING", "AMBIGUOUS"}:
                return None
            if info["status"] == "GENERIC":
                continue

            # A plain family reference does not by itself identify a specific
            # face.  Only aliases/overrides that require normalization carry
            # enough information to justify adding face properties.
            if info["status"] == "FOUND" and info["normalize"] == "YES":
                resolved.append(info)

        if not resolved:
            return None

        signatures = {
            (
                normal_key(info["family"]),
                info["weight"],
                info["style"],
                info["stretch"],
            )
            for info in resolved
        }
        if len(signatures) != 1:
            return None

        return resolved[0]

    def normalize_stylesheet(css_text: str) -> str:
        """Normalize font-family aliases inside ordinary CSS qualified rules.

        Only rules containing a font-family declaration are changed. Existing
        declarations, selectors, whitespace/comments and !important flags are
        otherwise left to tinycss2 serialization. If a single face-specific
        alias is normalized, add the corresponding CSS face properties when
        the rule does not already specify them.
        """
        rules = tinycss2.parse_stylesheet(
            css_text,
            skip_comments=False,
            skip_whitespace=False,
        )
        stylesheet_changed = False

        for rule in rules:
            if rule.type != "qualified-rule":
                continue

            declarations = tinycss2.parse_declaration_list(
                rule.content,
                skip_comments=False,
                skip_whitespace=False,
            )

            family_decl = None
            property_names = set()
            for decl in declarations:
                if decl.type != "declaration":
                    continue
                property_names.add(decl.lower_name)
                if decl.lower_name == "font-family":
                    # CSS cascade within one declaration block: the last
                    # declaration of equal importance wins. Normalizing all
                    # declarations is safe, but face properties should follow
                    # the last font-family declaration in the block.
                    family_decl = decl

            if family_decl is None:
                continue

            original_family = tinycss2.serialize(family_decl.value).strip()
            new_family, normalized_any = normalize_font_value(original_family)
            if not normalized_any:
                continue

            # Normalize every font-family declaration in the rule so an older
            # duplicate cannot retain an unresolved alias.
            for decl in declarations:
                if decl.type != "declaration" or decl.lower_name != "font-family":
                    continue
                original = tinycss2.serialize(decl.value).strip()
                replacement, changed_here = normalize_font_value(original)
                if changed_here:
                    decl.value = tinycss2.parse_component_value_list(replacement)

            info = common_normalized_face(original_family)
            if info is not None:
                additions = []
                if "font-weight" not in property_names and info["weight"]:
                    additions.append(("font-weight", info["weight"]))
                if (
                    "font-style" not in property_names
                    and info["style"]
                    and info["style"] != "normal"
                ):
                    additions.append(("font-style", info["style"]))
                if (
                    "font-stretch" not in property_names
                    and info["stretch"]
                    and info["stretch"] != "normal"
                ):
                    additions.append(("font-stretch", info["stretch"]))

                for name, value in additions:
                    important = " !important" if family_decl.important else ""
                    parsed = tinycss2.parse_declaration_list(
                        f"{name}:{value}{important};",
                        skip_comments=False,
                        skip_whitespace=False,
                    )
                    declarations.extend(parsed)

            rule.content = tinycss2.parse_component_value_list(
                tinycss2.serialize(declarations)
            )
            stylesheet_changed = True

        return tinycss2.serialize(rules) if stylesheet_changed else css_text

    # Normalize embedded stylesheets before walking individual text elements.
    # Classification already resolves font-family declarations from these
    # qualified rules, so conversion must apply the same alias resolution.
    for elem in root.iter():
        if local_name(elem.tag) == "style" and elem.text:
            elem.text = normalize_stylesheet(elem.text)

    for elem in root.iter():
        # font-family is inherited in SVG/CSS, so declarations on <g>, <svg>,
        # and other ancestors must be normalized too.  Restricting this pass to
        # text-related elements misses exactly those inherited declarations.

        # direct attr
        if "font-family" in elem.attrib:
            original = elem.attrib["font-family"]
            new_value, normalized_any = normalize_font_value(original)
            if normalized_any:
                elem.set("font-family", new_value)

                # Apply face properties when every face-specific alias in the
                # family fallback list resolves to the same canonical face.
                info = common_normalized_face(original)
                if info is not None:
                    if not get_property_from_elem(elem, "font-weight") and info["weight"]:
                        set_property_on_elem(elem, "font-weight", info["weight"])
                    if (
                        not get_property_from_elem(elem, "font-style")
                        and info["style"]
                        and info["style"] != "normal"
                    ):
                        set_property_on_elem(elem, "font-style", info["style"])
                    if (
                        not get_property_from_elem(elem, "font-stretch")
                        and info["stretch"]
                        and info["stretch"] != "normal"
                    ):
                        set_property_on_elem(elem, "font-stretch", info["stretch"])

        # inline style font-family
        style_value = elem.attrib.get("style")
        if style_value:
            mapping = parse_style_attribute(style_value)
            if "font-family" in mapping:
                new_value, normalized_any = normalize_font_value(mapping["font-family"])
                if normalized_any:
                    mapping["font-family"] = new_value

                    original_family = parse_style_attribute(style_value)["font-family"]
                    info = common_normalized_face(original_family)
                    if info is not None:
                        if "font-weight" not in mapping and info["weight"]:
                            mapping["font-weight"] = info["weight"]
                        if (
                            "font-style" not in mapping
                            and info["style"]
                            and info["style"] != "normal"
                        ):
                            mapping["font-style"] = info["style"]
                        if (
                            "font-stretch" not in mapping
                            and info["stretch"]
                            and info["stretch"] != "normal"
                        ):
                            mapping["font-stretch"] = info["stretch"]

                    elem.set("style", serialize_style_attribute(mapping))

    target_svg.parent.mkdir(parents=True, exist_ok=True)
    tree.write(target_svg, encoding="utf-8", xml_declaration=True)

    # de-duplicate aliases/warnings while preserving order
    dedup_aliases = list(dict.fromkeys(alias_descriptions))
    dedup_warnings = list(dict.fromkeys(warnings))

    return {
        "aliases_normalized": " | ".join(dedup_aliases),
        "warnings": " | ".join(dedup_warnings),
        "changed_count": changed,
    }


# ---------- output validation / cleanup ----------

def element_text_content(elem) -> str:
    return "".join(elem.itertext())


def find_meaningful_remaining_text(root):
    remaining = []
    for elem in root.iter():
        tag = local_name(elem.tag)
        if tag not in {"text", "textPath", "flowRoot", "flowPara"}:
            continue
        if element_text_content(elem).strip():
            remaining.append({
                "tag": tag,
                "id": elem.attrib.get("id", ""),
            })
    return remaining


def remove_empty_text_placeholders(tree):
    root = tree.getroot()
    parents = build_parent_map(root)
    removed = 0

    # Remove object-level empty text roots only.
    for elem in list(root.iter()):
        tag = local_name(elem.tag)
        if tag not in {"text", "flowRoot"}:
            continue
        if element_text_content(elem).strip():
            continue

        parent = parents.get(elem)
        if parent is None:
            continue
        parent.remove(elem)
        removed += 1

    return removed


def validate_output_svg(svg_path: Path):
    tree = ET.parse(svg_path)
    removed = remove_empty_text_placeholders(tree)
    if removed:
        tree.write(svg_path, encoding="utf-8", xml_declaration=True)

    # Reload to validate exact final state.
    tree = ET.parse(svg_path)
    root = tree.getroot()
    remaining = find_meaningful_remaining_text(root)
    return {
        "empty_removed": removed,
        "remaining": remaining,
    }


# ---------- Inkscape conversion ----------

def find_inkscape():
    for name in ("inkscape.com", "inkscape"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        Path(r"C:\Program Files\Inkscape\bin\inkscape.com"),
        Path(r"C:\Program Files\Inkscape\bin\inkscape.exe"),
        Path(r"C:\Program Files (x86)\Inkscape\bin\inkscape.com"),
        Path(r"C:\Program Files (x86)\Inkscape\bin\inkscape.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def find_resvg():
    found = shutil.which("resvg")
    if found:
        return found

    candidates = []
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        candidates.append(Path(cargo_home) / "bin" / "resvg.exe")

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / ".cargo" / "bin" / "resvg.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def find_usvg():
    found = shutil.which("usvg")
    if found:
        return found

    candidates = []
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        candidates.append(Path(cargo_home) / "bin" / "usvg.exe")

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(Path(user_profile) / ".cargo" / "bin" / "usvg.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


def resolve_text_converter(requested: str):
    if requested == "usvg":
        executable = find_usvg()
        if not executable:
            raise RuntimeError(
                "Could not find usvg in PATH or the Cargo bin directory. "
                "Install it with: cargo install usvg"
            )
        return "usvg", executable

    if requested in {"inkscape", "inkscape-shell"}:
        executable = find_inkscape()
        if not executable:
            raise RuntimeError("Could not find Inkscape in PATH or common installation paths.")
        return requested, executable

    if requested == "auto":
        # Prefer Inkscape shell mode: it retains Inkscape's text shaping and
        # color-font fidelity while avoiding per-file startup overhead.  usvg
        # remains a fallback because some source fonts render differently.
        executable = find_inkscape()
        if executable:
            return "inkscape-shell", executable
        executable = find_usvg()
        if executable:
            return "usvg", executable
        raise RuntimeError("Could not find either Inkscape or usvg.")

    raise RuntimeError(f"Unknown text converter: {requested}")


def executable_version(executable: str) -> str:
    try:
        result = run_subprocess([executable, "--version"])
    except Exception:
        return ""
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def run_subprocess(command):
    return subprocess.run(command, capture_output=True, text=True)


def inkscape_export_text_to_path(inkscape, source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        inkscape,
        str(source),
        "--export-text-to-path",
        "--export-overwrite",
        f"--export-filename={destination}",
    ]
    return run_subprocess(command)


class InkscapeShellWorker:
    """Persistent Inkscape --shell process used by one conversion worker thread.

    Inkscape shell mode accepts one semicolon-separated action line at a time.
    Keeping the process alive avoids paying Inkscape's substantial startup cost
    for every SVG.  Each Python worker thread owns its own shell process, so
    --jobs N means at most N persistent Inkscape instances.
    """

    def __init__(self, inkscape: str, output_timeout: float = 120.0):
        self.inkscape = inkscape
        self.output_timeout = float(output_timeout)
        self.process = None
        self.start()

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        # Shell stdout/stderr are intentionally discarded.  Completion is
        # synchronized on the exported SVG appearing and becoming stable.
        # This also avoids pipe-buffer deadlocks from Inkscape diagnostics.
        self.process = subprocess.Popen(
            [self.inkscape, "--shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def close(self):
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
            except Exception:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass

    def _wait_for_output(self, destination: Path):
        deadline = time.monotonic() + self.output_timeout
        last_size = None
        stable_checks = 0
        while time.monotonic() < deadline:
            if self.process is None or self.process.poll() is not None:
                return False, "Persistent Inkscape shell exited unexpectedly."
            try:
                if destination.exists():
                    size = destination.stat().st_size
                    if size > 0 and size == last_size:
                        stable_checks += 1
                        if stable_checks >= 3:
                            return True, ""
                    else:
                        stable_checks = 0
                    last_size = size
            except OSError:
                pass
            time.sleep(0.025)
        return (
            False,
            f"Timed out after {self.output_timeout:.0f}s waiting for Inkscape shell output.",
        )

    def convert_text_to_path(self, source: Path, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if destination.exists():
                destination.unlink()
        except OSError as e:
            return subprocess.CompletedProcess([], 1, "", str(e))

        # The action separator is ';'.  Rather than risk ambiguous parsing,
        # fall back to the normal one-shot backend for the very unusual case
        # of a semicolon in either path.
        if ";" in str(source) or ";" in str(destination):
            return inkscape_export_text_to_path(self.inkscape, source, destination)

        try:
            self.start()
            actions = ";".join([
                f"file-open:{source}",
                "export-text-to-path",
                "export-overwrite",
                f"export-filename:{destination}",
                "export-do",
                "file-close",
            ])
            if self.process is None or self.process.stdin is None:
                raise RuntimeError("Inkscape shell stdin is unavailable")
            self.process.stdin.write(actions + "\n")
            self.process.stdin.flush()
        except Exception as e:
            self.close()
            return subprocess.CompletedProcess(
                [],
                1,
                "",
                f"Could not submit command to Inkscape shell: {e}",
            )

        ok, error = self._wait_for_output(destination)
        if ok:
            return subprocess.CompletedProcess([], 0, "", "")

        # Kill a wedged shell so the next file starts with a clean instance.
        self.close()
        return subprocess.CompletedProcess([], 1, "", error)


def usvg_text_to_path(usvg, source: Path, destination: Path, resources_dir: Path | None = None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [usvg, "--quiet"]
    if resources_dir is not None:
        command.extend(["--resources-dir", str(resources_dir)])
    # usvg converts text to paths by default.  Do not pass --preserve-text.
    command.extend([str(source), str(destination)])
    return run_subprocess(command)


def inkscape_fallback_object_to_path(inkscape, source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    actions = ";".join([
        "select-by-element:flowRoot",
        "object-to-path",
        "select-by-element:text",
        "object-to-path",
        "export-overwrite",
        f"export-filename:{destination}",
        "export-do",
        "file-close",
    ])
    command = [
        inkscape,
        "--batch-process",
        f"--actions={actions}",
        str(source),
    ]
    return run_subprocess(command)


def convert_available_svgs(
    output_root: Path,
    exact_index,
    compact_index,
    dry_run=False,
    overwrite=False,
    keep_failed=True,
    jobs=2,
    duplicate_representatives=None,
    text_converter="inkscape",
):
    available_root = output_root / "fonts_available"
    converted_root = output_root / "text_as_paths"
    reports_root = output_root / "reports"
    failed_normalized_root = output_root / "_failed_normalized"
    failed_inkscape_initial_root = output_root / "_failed_inkscape" / "initial"
    failed_inkscape_fallback_root = output_root / "_failed_inkscape" / "fallback"
    failed_usvg_root = output_root / "_failed_usvg"

    if not available_root.is_dir():
        raise RuntimeError(f"Missing expected directory: {available_root}")

    jobs = max(1, int(jobs))
    duplicate_representatives = duplicate_representatives or {}

    converted_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)

    if keep_failed:
        failed_normalized_root.mkdir(parents=True, exist_ok=True)
        failed_inkscape_initial_root.mkdir(parents=True, exist_ok=True)
        failed_inkscape_fallback_root.mkdir(parents=True, exist_ok=True)
        failed_usvg_root.mkdir(parents=True, exist_ok=True)

    svg_files = sorted(available_root.rglob("*.svg"))
    total = len(svg_files)

    converter_name = text_converter
    converter_executable = None
    converter_version = ""
    if not dry_run and total:
        converter_name, converter_executable = resolve_text_converter(text_converter)
        converter_version = executable_version(converter_executable)

    shell_local = threading.local()
    shell_workers = []
    shell_workers_lock = threading.Lock()

    def get_inkscape_shell_worker():
        worker = getattr(shell_local, "worker", None)
        if worker is None:
            executable = converter_executable
            if executable is None:
                raise RuntimeError("Inkscape executable was not resolved.")
            worker = InkscapeShellWorker(executable)
            shell_local.worker = worker
            with shell_workers_lock:
                shell_workers.append(worker)
        return worker

    def close_inkscape_shell_workers():
        with shell_workers_lock:
            workers = list(shell_workers)
            shell_workers.clear()
        for worker in workers:
            try:
                worker.close()
            except Exception:
                pass
        try:
            shell_local.worker = None
        except Exception:
            pass

    print()
    print(f"Converting SVGs from: {available_root}")
    print(f"SVG files found: {total}")
    print(f"Parallel jobs: {jobs}")
    if dry_run:
        print(f"Text converter: {text_converter} (dry run)")
    else:
        version_suffix = f" ({converter_version})" if converter_version else ""
        print(f"Text converter: {converter_name}{version_suffix}")
    print()

    conversion_started = time.perf_counter()

    def process_one(index: int, source: Path, force=False):
        attempt_started = time.perf_counter()
        relative = source.relative_to(available_root)
        relative_key = relative.as_posix()
        destination = converted_root / relative

        def make_row(status, aliases="", warnings="", error=""):
            return {
                "file": relative_key,
                "status": status,
                "aliases_normalized": aliases,
                "warnings": warnings,
                "converter": converter_name if not dry_run else text_converter,
                "runtime_seconds": time.perf_counter() - attempt_started,
                "error": error,
            }

        representative = duplicate_representatives.get(relative_key)
        if representative:
            return (
                index,
                relative,
                make_row(
                    "SKIPPED_DUPLICATE",
                    warnings=f"Duplicate of {representative}",
                ),
                f"SKIPPED (duplicate of {representative})",
            )

        if destination.exists() and not (overwrite or force):
            return index, relative, make_row("SKIPPED"), "SKIPPED"

        if force and destination.exists():
            try:
                destination.unlink()
            except OSError:
                pass

        with tempfile.TemporaryDirectory(prefix="svg-font-pipeline-") as tmpdir:
            tmpdir = Path(tmpdir)
            normalized = tmpdir / relative.name

            try:
                norm = normalize_svg_for_conversion(
                    source,
                    normalized,
                    exact_index,
                    compact_index,
                )
            except Exception as e:
                if keep_failed:
                    try:
                        copy_preserving_tree(
                            source,
                            available_root,
                            failed_normalized_root,
                        )
                    except Exception:
                        pass
                return (
                    index,
                    relative,
                    make_row("FAILED_NORMALIZATION", error=str(e)),
                    "FAILED_NORMALIZATION",
                )

            if dry_run:
                return (
                    index,
                    relative,
                    make_row(
                        "DRY_RUN_OK",
                        aliases=norm["aliases_normalized"],
                        warnings=norm["warnings"],
                    ),
                    "DRY_RUN_OK",
                )

            if converter_name == "usvg":
                r1 = usvg_text_to_path(
                    converter_executable,
                    normalized,
                    destination,
                    resources_dir=source.parent,
                )
                converter_failure_status = "FAILED_USVG"
                converter_failure_label = "FAILED (usvg)"
                converter_failure_message = "usvg conversion failed"
            elif converter_name == "inkscape-shell":
                r1 = get_inkscape_shell_worker().convert_text_to_path(
                    normalized, destination
                )
                converter_failure_status = "FAILED_INKSCAPE"
                converter_failure_label = "FAILED (Inkscape shell)"
                converter_failure_message = "Persistent Inkscape shell export failed"
            else:
                r1 = inkscape_export_text_to_path(
                    converter_executable, normalized, destination
                )
                converter_failure_status = "FAILED_INKSCAPE"
                converter_failure_label = "FAILED (Inkscape)"
                converter_failure_message = "Inkscape export failed"

            if r1.returncode != 0 or not destination.exists():
                if keep_failed:
                    try:
                        copy_preserving_tree(
                            normalized,
                            tmpdir,
                            failed_normalized_root / relative.parent,
                        )
                    except Exception:
                        pass
                return (
                    index,
                    relative,
                    make_row(
                        converter_failure_status,
                        aliases=norm["aliases_normalized"],
                        warnings=norm["warnings"],
                        error=(r1.stderr or r1.stdout or converter_failure_message).strip(),
                    ),
                    converter_failure_label,
                )

            v1 = validate_output_svg(destination)
            warnings = []
            if norm["warnings"]:
                warnings.append(norm["warnings"])
            if v1["empty_removed"]:
                warnings.append(
                    f"Removed {v1['empty_removed']} empty text placeholder object(s) "
                    "from initial output."
                )

            if not v1["remaining"]:
                status = (
                    "CONVERTED+ALIASES"
                    if norm["aliases_normalized"]
                    else "CONVERTED"
                )
                return (
                    index,
                    relative,
                    make_row(
                        status,
                        aliases=norm["aliases_normalized"],
                        warnings=" | ".join(warnings),
                    ),
                    "OK",
                )

            initial_desc = ", ".join(
                f"{item['tag']}#{item['id']}" if item["id"] else item["tag"]
                for item in v1["remaining"]
            )
            warnings.append(
                f"Initial output still contained {len(v1['remaining'])} "
                f"text element(s): {initial_desc}."
            )

            if converter_name == "usvg":
                if keep_failed:
                    try:
                        copy_preserving_tree(
                            destination,
                            converted_root,
                            failed_usvg_root,
                        )
                    except Exception:
                        pass
                return (
                    index,
                    relative,
                    make_row(
                        "FAILED_REMAINING_TEXT",
                        aliases=norm["aliases_normalized"],
                        warnings=" | ".join(warnings),
                    ),
                    "FAILED (remaining text)",
                )

            # Preserve initial failed output for diagnosis if fallback also fails.
            if keep_failed:
                try:
                    copy_preserving_tree(
                        destination,
                        converted_root,
                        failed_inkscape_initial_root,
                    )
                except Exception:
                    pass

            r2 = inkscape_fallback_object_to_path(
                converter_executable, destination, destination
            )
            if r2.returncode != 0 or not destination.exists():
                if keep_failed:
                    try:
                        copy_preserving_tree(
                            normalized,
                            tmpdir,
                            failed_normalized_root / relative.parent,
                        )
                    except Exception:
                        pass
                return (
                    index,
                    relative,
                    make_row(
                        "FAILED_FALLBACK",
                        aliases=norm["aliases_normalized"],
                        warnings=" | ".join(warnings),
                        error=(r2.stderr or r2.stdout or "Inkscape fallback failed").strip(),
                    ),
                    "FAILED (Fallback)",
                )

            v2 = validate_output_svg(destination)
            if v2["empty_removed"]:
                warnings.append(
                    f"Removed {v2['empty_removed']} empty text placeholder object(s) "
                    "from fallback output."
                )

            if not v2["remaining"]:
                status = "CONVERTED+FALLBACK"
                if norm["aliases_normalized"]:
                    status = "CONVERTED+ALIASES+FALLBACK"
                return (
                    index,
                    relative,
                    make_row(
                        status,
                        aliases=norm["aliases_normalized"],
                        warnings=" | ".join(warnings),
                    ),
                    "OK (fallback)",
                )

            fallback_desc = ", ".join(
                f"{item['tag']}#{item['id']}" if item["id"] else item["tag"]
                for item in v2["remaining"]
            )
            warnings.append(
                f"Fallback still contains {len(v2['remaining'])} "
                f"text element(s): {fallback_desc}"
            )

            if keep_failed:
                try:
                    copy_preserving_tree(
                        normalized,
                        tmpdir,
                        failed_normalized_root / relative.parent,
                    )
                    copy_preserving_tree(
                        destination,
                        converted_root,
                        failed_inkscape_fallback_root,
                    )
                except Exception:
                    pass

            return (
                index,
                relative,
                make_row(
                    "FAILED_REMAINING_TEXT",
                    aliases=norm["aliases_normalized"],
                    warnings=" | ".join(warnings),
                ),
                "FAILED (remaining text)",
            )

    results_by_index = {}

    # Threads are appropriate here because the expensive work is performed by
    # independent converter subprocesses.  The font indexes are read-only, each
    # SVG has a unique destination, and every worker gets a private temp tree.
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        future_map = {
            executor.submit(process_one, index, source): (index, source)
            for index, source in enumerate(svg_files, 1)
        }

        completed = 0
        for future in as_completed(future_map):
            index, source = future_map[future]
            relative = source.relative_to(available_root)
            try:
                result_index, result_relative, row, display_status = future.result()
            except Exception as e:
                result_index = index
                result_relative = relative
                row = {
                    "file": relative.as_posix(),
                    "status": "FAILED_INTERNAL",
                    "aliases_normalized": "",
                    "warnings": "",
                    "converter": converter_name if not dry_run else text_converter,
                    "runtime_seconds": 0.0,
                    "error": str(e),
                }
                display_status = "FAILED (internal)"

            results_by_index[result_index] = row
            completed += 1
            if row.get("error") and row["status"] in {
                "FAILED_INKSCAPE",
                "FAILED_USVG",
                "FAILED_FALLBACK",
            }:
                error_lines = row["error"].splitlines()
                first_line = error_lines[0] if error_lines else row["error"]
                print(f"[{completed}/{total}] {result_relative} {display_status}: {first_line}")
            else:
                print(f"[{completed}/{total}] {result_relative} {display_status}")

    # The executor threads are finished; shut down their persistent shells
    # before any sequential retry pass.
    if converter_name == "inkscape-shell":
        close_inkscape_shell_workers()

    # Inkscape has had intermittent failures when multiple command-line
    # instances run concurrently.  Retrying process-level converter failures
    # serially is also useful for diagnosing any usvg subprocess failures.
    retry_indexes = [
        i for i in range(1, total + 1)
        if results_by_index.get(i, {}).get("status")
        in {"FAILED_INKSCAPE", "FAILED_USVG", "FAILED_FALLBACK"}
    ]

    if retry_indexes and jobs > 1 and not dry_run:
        print()
        print(f"Retrying {len(retry_indexes)} converter failure(s) sequentially...")
        for retry_number, index in enumerate(retry_indexes, 1):
            source = svg_files[index - 1]
            relative = source.relative_to(available_root)
            prior_runtime = float(
                results_by_index.get(index, {}).get("runtime_seconds", 0.0) or 0.0
            )
            try:
                result_index, result_relative, row, display_status = process_one(
                    index, source, force=True
                )
                row["runtime_seconds"] = prior_runtime + float(
                    row.get("runtime_seconds", 0.0) or 0.0
                )
            except Exception as e:
                result_index = index
                result_relative = relative
                row = {
                    "file": relative.as_posix(),
                    "status": "FAILED_INTERNAL",
                    "aliases_normalized": "",
                    "warnings": "",
                    "converter": converter_name if not dry_run else text_converter,
                    "runtime_seconds": prior_runtime,
                    "error": str(e),
                }
                display_status = "FAILED (internal)"

            results_by_index[result_index] = row
            if row.get("error") and row["status"] in {
                "FAILED_INKSCAPE",
                "FAILED_USVG",
                "FAILED_FALLBACK",
                "FAILED_INTERNAL",
            }:
                error_lines = row["error"].splitlines()
                first_line = error_lines[0] if error_lines else row["error"]
                print(
                    f"[retry {retry_number}/{len(retry_indexes)}] "
                    f"{result_relative} {display_status}: {first_line}"
                )
            else:
                print(
                    f"[retry {retry_number}/{len(retry_indexes)}] "
                    f"{result_relative} {display_status}"
                )

    if converter_name == "inkscape-shell":
        close_inkscape_shell_workers()

    # Keep CSV output deterministic even though jobs finish out of order.
    results = [results_by_index[i] for i in range(1, total + 1)]

    converted_statuses = {
        "DRY_RUN_OK",
        "CONVERTED",
        "CONVERTED+ALIASES",
        "CONVERTED+FALLBACK",
        "CONVERTED+ALIASES+FALLBACK",
    }
    failed_statuses = {
        "FAILED_NORMALIZATION",
        "FAILED_INKSCAPE",
        "FAILED_USVG",
        "FAILED_FALLBACK",
        "FAILED_REMAINING_TEXT",
        "FAILED_INTERNAL",
    }

    converted = sum(row["status"] in converted_statuses for row in results)
    skipped = sum(row["status"] == "SKIPPED" for row in results)
    skipped_duplicates = sum(row["status"] == "SKIPPED_DUPLICATE" for row in results)
    failed = sum(row["status"] in failed_statuses for row in results)

    report_path = reports_root / "svg-text-to-path-report.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "status",
                "aliases_normalized",
                "warnings",
                "converter",
                "runtime_seconds",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    elapsed_seconds = time.perf_counter() - conversion_started
    summary_path = reports_root / "text-conversion-summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "converter",
                "converter_version",
                "jobs",
                "elapsed_seconds",
                "files_found",
                "converted",
                "skipped_existing",
                "skipped_duplicates",
                "failed",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "converter": converter_name if not dry_run else text_converter,
            "converter_version": converter_version,
            "jobs": jobs,
            "elapsed_seconds": elapsed_seconds,
            "files_found": total,
            "converted": converted,
            "skipped_existing": skipped,
            "skipped_duplicates": skipped_duplicates,
            "failed": failed,
        })

    return {
        "converted": converted,
        "skipped": skipped,
        "skipped_duplicates": skipped_duplicates,
        "failed": failed,
        "report_path": report_path,
        "summary_path": summary_path,
        "elapsed_seconds": elapsed_seconds,
        "converter": converter_name if not dry_run else text_converter,
        "converter_version": converter_version,
    }


# ---------- duplicate detection ----------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_svg_for_duplicate_check(
    renderer: str,
    executable: str,
    source: Path,
    destination: Path,
    render_size: int,
):
    destination.parent.mkdir(parents=True, exist_ok=True)

    if renderer == "resvg":
        command = [
            executable,
            "--quiet",
            "--width",
            str(render_size),
            str(source),
            str(destination),
        ]
    elif renderer == "inkscape":
        command = [
            executable,
            str(source),
            "--export-type=png",
            f"--export-filename={destination}",
            f"--export-width={render_size}",
            "--export-background-opacity=0",
            "--export-overwrite",
        ]
    else:
        raise ValueError(f"Unsupported duplicate renderer: {renderer}")

    return run_subprocess(command)


def resolve_duplicate_renderer(requested: str):
    if requested == "resvg":
        executable = find_resvg()
        if not executable:
            raise RuntimeError(
                "Could not find resvg. Install it with 'cargo install resvg' "
                "or use --duplicate-renderer inkscape."
            )
        return "resvg", executable

    if requested == "inkscape":
        executable = find_inkscape()
        if not executable:
            raise RuntimeError(
                "Could not find Inkscape, which is required when "
                "--duplicate-renderer inkscape is selected."
            )
        return "inkscape", executable

    if requested == "auto":
        executable = find_resvg()
        if executable:
            return "resvg", executable
        executable = find_inkscape()
        if executable:
            return "inkscape", executable
        raise RuntimeError(
            "Could not find either resvg or Inkscape for visual duplicate detection."
        )

    raise ValueError(f"Unsupported duplicate renderer: {requested}")


def pillow_image_module() -> Any:
    image_module = optional_import("PIL.Image")
    if image_module is None:
        raise RuntimeError(
            "Visual duplicate detection requires Pillow. "
            "Install InfinityDB with the 'symbols' extra."
        )
    return image_module


def rgba_pixel_hash(png_path: Path) -> str:
    image_module = pillow_image_module()
    with image_module.open(png_path) as image:
        rgba = image.convert("RGBA")
        digest = hashlib.sha256()
        digest.update(f"{rgba.width}x{rgba.height}:RGBA\0".encode("ascii"))
        digest.update(rgba.tobytes())
        return digest.hexdigest()


def duplicate_relative_key(path: Path, input_root: Path) -> str:
    """Return one host-independent source-relative SVG path."""
    return path.relative_to(input_root).as_posix()


def duplicate_representative_sort_key(relative: Path, file_categories: dict[str, str]):
    # Prefer a source that already needs no text conversion.  If there is no
    # such member, prefer a font-safe convertible source over a missing-font
    # source.  Within the same classification, prefer the shorter filename;
    # lexical filename/path order keeps ties deterministic.
    category = file_categories.get(relative.as_posix(), "unknown")
    priority = {
        "no_active_text": 0,
        "fonts_available": 1,
        "fonts_missing": 2,
        "parse_error": 3,
        "unknown": 4,
    }.get(category, 4)
    return (
        priority,
        len(relative.name),
        relative.name.casefold(),
        str(relative).casefold(),
    )


class SymbolSetSizeMetrics(TypedDict):
    source_svg_files: int
    canonical_svg_files: int
    source_size_bytes: int
    canonical_size_bytes: int
    reclaimed_size_bytes: int
    reduction_percent: float


def symbol_set_size_metrics(
    svg_files: list[Path],
    input_root: Path,
    duplicate_representatives: dict[str, str],
) -> SymbolSetSizeMetrics:
    """Summarize source and canonical SVG-set sizes after deduplication."""
    source_size_bytes = sum(path.stat().st_size for path in svg_files)
    canonical_files = [
        path
        for path in svg_files
        if duplicate_relative_key(path, input_root) not in duplicate_representatives
    ]
    canonical_size_bytes = sum(path.stat().st_size for path in canonical_files)
    reclaimed_size_bytes = source_size_bytes - canonical_size_bytes
    reduction_percent = (
        reclaimed_size_bytes * 100.0 / source_size_bytes
        if source_size_bytes
        else 0.0
    )
    return {
        "source_svg_files": len(svg_files),
        "canonical_svg_files": len(canonical_files),
        "source_size_bytes": source_size_bytes,
        "canonical_size_bytes": canonical_size_bytes,
        "reclaimed_size_bytes": reclaimed_size_bytes,
        "reduction_percent": reduction_percent,
    }


def find_duplicate_svgs(
    input_root: Path,
    output_root: Path,
    file_categories: dict[str, str],
    render_size: int = 512,
    jobs: int = 2,
    renderer: str = "resvg",
    *,
    reports_root: Path | None = None,
):
    """Find duplicates across the ENTIRE original input SVG set.

    Byte-identical files are grouped first so only one member of each exact set
    needs to be rendered.  Those representatives are then rasterized with the
    selected renderer and hashed from decoded RGBA pixels to find SVGs that
    differ in XML but render identically.

    The returned duplicate_representatives mapping contains only redundant
    members: relative source path -> chosen representative relative source path.
    The input tree is never modified.
    """
    reports_root = reports_root or output_root / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)

    if render_size < 1:
        raise ValueError("Duplicate render size must be at least 1 pixel.")
    if jobs < 1:
        raise ValueError("Duplicate render jobs must be at least 1.")

    renderer_name, renderer_executable = resolve_duplicate_renderer(renderer)
    renderer_version = executable_version(renderer_executable)
    duplicate_started = time.perf_counter()

    pillow_image_module()

    svg_files = sorted(
        path for path in input_root.rglob("*.svg")
        if not any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(input_root).parts)
    )

    print()
    print("Duplicate detection (entire source set)")
    print("---------------------------------------")
    print(f"Source SVG files:   {len(svg_files)}")
    renderer_label = renderer_name + (f" ({renderer_version})" if renderer_version else "")
    print(f"Renderer:           {renderer_label}")
    print(f"Render width:       {render_size}px")
    print(f"Parallel jobs:      {jobs}")

    byte_groups = defaultdict(list)
    byte_hash_for_path = {}
    for path in svg_files:
        byte_hash = sha256_file(path)
        byte_hash_for_path[path] = byte_hash
        byte_groups[byte_hash].append(path)

    unique_byte_groups = sorted(
        byte_groups.items(),
        key=lambda item: duplicate_relative_key(item[1][0], input_root).casefold(),
    )
    saved_renders = len(svg_files) - len(unique_byte_groups)
    print(f"Unique byte sets:   {len(unique_byte_groups)}")
    if saved_renders:
        print(f"Renders avoided:    {saved_renders} (exact duplicates)")

    visual_hash_for_byte_hash = {}
    render_errors = []

    with tempfile.TemporaryDirectory(prefix="svg-duplicate-check-") as temp_str:
        temp_root = Path(temp_str)

        def render_one(index, byte_hash, paths):
            representative = paths[0]
            png = temp_root / f"render-{index:06d}.png"
            result = render_svg_for_duplicate_check(
                renderer_name, renderer_executable, representative, png, render_size
            )
            if result.returncode != 0 or not png.exists():
                error = (result.stderr or result.stdout or f"{renderer_name} render failed").strip()
                return index, byte_hash, paths, None, error
            try:
                visual_hash = rgba_pixel_hash(png)
            except Exception as e:
                return index, byte_hash, paths, None, str(e)
            return index, byte_hash, paths, visual_hash, ""

        tasks = [
            (index, byte_hash, paths)
            for index, (byte_hash, paths) in enumerate(unique_byte_groups, 1)
        ]
        failed_tasks = []

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            future_map = {
                executor.submit(render_one, index, byte_hash, paths): (index, byte_hash, paths)
                for index, byte_hash, paths in tasks
            }
            completed = 0
            for future in as_completed(future_map):
                index, byte_hash, paths = future_map[future]
                try:
                    _, result_hash, result_paths, visual_hash, error = future.result()
                except Exception as e:
                    result_hash = byte_hash
                    result_paths = paths
                    visual_hash = None
                    error = str(e)

                completed += 1
                relative = result_paths[0].relative_to(input_root)
                if visual_hash:
                    visual_hash_for_byte_hash[result_hash] = visual_hash
                    print(f"[{completed}/{len(tasks)}] {relative} OK")
                else:
                    first_line = (
                        error.splitlines()[0]
                        if error
                        else f"{renderer_name} render failed"
                    )
                    print(f"[{completed}/{len(tasks)}] {relative} FAILED: {first_line}")
                    failed_tasks.append((index, result_hash, result_paths))

        if failed_tasks and jobs > 1:
            print()
            print(f"Retrying {len(failed_tasks)} duplicate render failure(s) sequentially...")
            for retry_number, (index, byte_hash, paths) in enumerate(failed_tasks, 1):
                _, result_hash, result_paths, visual_hash, error = render_one(
                    index, byte_hash, paths
                )
                relative = result_paths[0].relative_to(input_root)
                if visual_hash:
                    visual_hash_for_byte_hash[result_hash] = visual_hash
                    print(f"[retry {retry_number}/{len(failed_tasks)}] {relative} OK")
                    continue

                first_line = error.splitlines()[0] if error else f"{renderer_name} render failed"
                print(
                    f"[retry {retry_number}/{len(failed_tasks)}] "
                    f"{relative} FAILED: {first_line}"
                )
                for path in result_paths:
                    render_errors.append({
                        "file": duplicate_relative_key(path, input_root),
                        "byte_sha256": result_hash,
                        "renderer": renderer_name,
                        "error": error,
                    })
        elif failed_tasks:
            for _index, byte_hash, paths in failed_tasks:
                # jobs=1 already was the sequential attempt.
                representative = paths[0]
                png = temp_root / f"failed-{len(render_errors):06d}.png"
                result = render_svg_for_duplicate_check(
                    renderer_name, renderer_executable, representative, png, render_size
                )
                error = (result.stderr or result.stdout or f"{renderer_name} render failed").strip()
                if result.returncode == 0 and png.exists():
                    try:
                        visual_hash_for_byte_hash[byte_hash] = rgba_pixel_hash(png)
                        continue
                    except Exception as e:
                        error = str(e)
                for path in paths:
                    render_errors.append({
                        "file": duplicate_relative_key(path, input_root),
                        "byte_sha256": byte_hash,
                        "renderer": renderer_name,
                        "error": error,
                    })

    # Build disjoint equivalence groups.  A successful rendered group supersedes
    # its exact-byte subgroups.  Exact groups are still used when rendering
    # failed, so byte-identical files can always be deduplicated safely.
    visual_groups = defaultdict(list)
    for path in svg_files:
        byte_hash = byte_hash_for_path[path]
        visual_hash = visual_hash_for_byte_hash.get(byte_hash)
        if visual_hash:
            visual_groups[visual_hash].append(path)

    grouped_paths = set()
    duplicate_sets = []  # (type, visual_hash, paths)

    for visual_hash, paths in visual_groups.items():
        if len(paths) < 2:
            continue
        paths = sorted(paths, key=lambda p: str(p.relative_to(input_root)).casefold())
        byte_hashes = {byte_hash_for_path[p] for p in paths}
        group_type = "exact" if len(byte_hashes) == 1 else "visual"
        duplicate_sets.append((group_type, visual_hash, paths))
        grouped_paths.update(paths)

    for _byte_hash, paths in byte_groups.items():
        if len(paths) < 2:
            continue
        remaining = [p for p in paths if p not in grouped_paths]
        if len(remaining) >= 2:
            duplicate_sets.append(("exact", "", sorted(remaining)))
            grouped_paths.update(remaining)

    duplicate_sets.sort(
        key=lambda item: duplicate_relative_key(item[2][0], input_root).casefold()
    )

    rows = []
    duplicate_representatives = {}
    exact_groups = 0
    visual_groups_count = 0

    for group_number, (group_type, visual_hash, paths) in enumerate(duplicate_sets, 1):
        if group_type == "exact":
            exact_groups += 1
        else:
            visual_groups_count += 1

        relatives = [p.relative_to(input_root) for p in paths]
        representative = min(
            relatives,
            key=lambda rel: duplicate_representative_sort_key(rel, file_categories),
        )
        group_id = f"D{group_number:04d}"

        for path, relative in zip(paths, relatives, strict=True):
            is_representative = relative == representative
            if not is_representative:
                duplicate_representatives[relative.as_posix()] = representative.as_posix()
            rows.append({
                "group": group_id,
                "type": group_type,
                "role": "representative" if is_representative else "duplicate",
                "representative": representative.as_posix(),
                "classification": file_categories.get(relative.as_posix(), "unknown"),
                "file": relative.as_posix(),
                "size_bytes": path.stat().st_size,
                "byte_sha256": byte_hash_for_path[path],
                "visual_sha256": visual_hash,
            })

    report_path = reports_root / "duplicate-groups.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "group",
                "type",
                "role",
                "representative",
                "classification",
                "file",
                "size_bytes",
                "byte_sha256",
                "visual_sha256",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    error_path = reports_root / "duplicate-render-errors.csv"
    with error_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "byte_sha256", "renderer", "error"])
        writer.writeheader()
        writer.writerows(render_errors)

    size_metrics = symbol_set_size_metrics(
        svg_files,
        input_root,
        duplicate_representatives,
    )

    duplicate_elapsed = time.perf_counter() - duplicate_started
    summary_path = reports_root / "duplicate-summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "renderer",
            "renderer_version",
            "jobs",
            "render_size",
            "source_svg_files",
            "canonical_svg_files",
            "unique_byte_sets",
            "renders_avoided_exact",
            "exact_groups",
            "visual_groups",
            "redundant_files",
            "render_errors",
            "source_size_bytes",
            "canonical_size_bytes",
            "reclaimed_size_bytes",
            "reduction_percent",
            "elapsed_seconds",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "renderer": renderer_name,
            "renderer_version": renderer_version,
            "jobs": jobs,
            "render_size": render_size,
            "source_svg_files": len(svg_files),
            "canonical_svg_files": size_metrics["canonical_svg_files"],
            "unique_byte_sets": len(unique_byte_groups),
            "renders_avoided_exact": saved_renders,
            "exact_groups": exact_groups,
            "visual_groups": visual_groups_count,
            "redundant_files": len(duplicate_representatives),
            "render_errors": len(render_errors),
            "source_size_bytes": size_metrics["source_size_bytes"],
            "canonical_size_bytes": size_metrics["canonical_size_bytes"],
            "reclaimed_size_bytes": size_metrics["reclaimed_size_bytes"],
            "reduction_percent": f"{size_metrics['reduction_percent']:.6f}",
            "elapsed_seconds": duplicate_elapsed,
        })

    print(f"Exact groups:       {exact_groups}")
    print(f"Visual groups:      {visual_groups_count}")
    print(f"Redundant files:    {len(duplicate_representatives)}")
    print(f"Render errors:      {len(render_errors)}")
    print(f"Source size:        {size_metrics['source_size_bytes']:,} bytes")
    print(f"Canonical size:     {size_metrics['canonical_size_bytes']:,} bytes")
    print(
        "Dedup reduction:    "
        f"{size_metrics['reclaimed_size_bytes']:,} bytes "
        f"({size_metrics['reduction_percent']:.2f}%)"
    )
    print(f"Elapsed:            {duplicate_elapsed:.3f}s")
    print(f"Report:             {report_path}")
    print(f"Summary:            {summary_path}")

    return {
        "source_svg_files": len(svg_files),
        "canonical_svg_files": size_metrics["canonical_svg_files"],
        "unique_byte_sets": len(unique_byte_groups),
        "renders_avoided_exact": saved_renders,
        "exact_groups": exact_groups,
        "visual_groups": visual_groups_count,
        "redundant_files": len(duplicate_representatives),
        "render_errors": len(render_errors),
        "source_size_bytes": size_metrics["source_size_bytes"],
        "canonical_size_bytes": size_metrics["canonical_size_bytes"],
        "reclaimed_size_bytes": size_metrics["reclaimed_size_bytes"],
        "reduction_percent": size_metrics["reduction_percent"],
        "render_size": render_size,
        "jobs": jobs,
        "renderer": renderer_name,
        "renderer_version": renderer_version,
        "elapsed_seconds": duplicate_elapsed,
        "duplicate_representatives": duplicate_representatives,
        "report_path": report_path,
        "error_path": error_path,
        "summary_path": summary_path,
    }


def separate_duplicate_outputs(
    input_root: Path,
    output_root: Path,
    file_categories: dict[str, str],
    duplicate_representatives: dict[str, str],
):
    """Move redundant *output copies* to output_root/duplicates/.

    The original input files are never moved or modified.  Classified copies are
    moved from their normal category trees when present.  A parse-error/unknown
    duplicate has no classified copy, so the original source is copied into the
    duplicates tree for visibility.
    """
    duplicates_root = output_root / "duplicates"
    ensure_clean_dir(duplicates_root)

    moved = 0
    copied = 0
    rows = []

    for relative_str, representative in sorted(
        duplicate_representatives.items(), key=lambda item: item[0].casefold()
    ):
        relative = Path(relative_str)
        category = file_categories.get(relative_str, "unknown")
        destination = duplicates_root / category / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        source_copy = output_root / category / relative
        if (
            category in {"fonts_available", "fonts_missing", "no_active_text"}
            and source_copy.exists()
        ):
            shutil.move(str(source_copy), str(destination))
            action = "moved"
            moved += 1
        else:
            shutil.copy2(input_root / relative, destination)
            action = "copied"
            copied += 1

        rows.append({
            "file": relative_str,
            "classification": category,
            "representative": representative,
            "action": action,
            "destination": str(destination.relative_to(output_root)),
        })

    report_path = output_root / "reports" / "duplicate-separation.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["file", "classification", "representative", "action", "destination"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print()
    print("Duplicate separation")
    print("--------------------")
    print(f"Moved classified copies: {moved}")
    print(f"Copied uncategorized:    {copied}")
    print(f"Duplicates directory:    {duplicates_root}")

    return {"moved": moved, "copied": copied, "report_path": report_path}


# ---------- pipeline ----------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Unified SVG font audit, source-level duplicate detection, "
            "and text-to-path pipeline."
        )
    )
    parser.add_argument("input_root", help="Directory containing source SVG files")
    parser.add_argument("output_root", help="Separate directory for all generated outputs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify normally, but only dry-run the conversion stage",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing converted outputs",
    )
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="Keep normalized and failed converter diagnostic files",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Stop after classification and optional duplicate analysis",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Number of parallel conversion/render jobs (default: 4)",
    )
    parser.add_argument(
        "--find-duplicates",
        action="store_true",
        help=(
            "Detect exact and visually identical SVGs across the entire original "
            "input set before conversion"
        ),
    )
    parser.add_argument(
        "--separate-duplicates",
        action="store_true",
        help=(
            "Move redundant classified output copies under output_root/duplicates; "
            "implies --find-duplicates"
        ),
    )
    parser.add_argument(
        "--duplicate-render-size",
        type=int,
        default=512,
        help="Raster width in pixels for source-level visual duplicate detection (default: 512)",
    )
    parser.add_argument(
        "--text-converter",
        choices=("inkscape", "inkscape-shell", "usvg", "auto"),
        default="inkscape-shell",
        help=(
            "Text-to-path backend (default: inkscape-shell). "
            "inkscape-shell keeps one persistent Inkscape instance per job; "
            "usvg is experimental; auto prefers persistent Inkscape when installed."
        ),
    )
    parser.add_argument(
        "--duplicate-renderer",
        choices=("resvg", "inkscape", "auto"),
        default="resvg",
        help=(
            "Renderer for visual duplicate detection "
            "(default: resvg; auto prefers resvg then Inkscape)"
        ),
    )
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.duplicate_render_size < 1:
        parser.error("--duplicate-render-size must be at least 1")
    if args.separate_duplicates:
        args.find_duplicates = True

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()

    if not input_root.is_dir():
        print(f"Input directory does not exist: {input_root}", file=sys.stderr)
        return 1

    try:
        input_root.relative_to(output_root)
        print(
            "Refusing to use an output directory that is an ancestor of the input directory.",
            file=sys.stderr,
        )
        return 1
    except ValueError:
        pass

    try:
        output_root.relative_to(input_root)
        print(
            "Refusing to write output inside the input directory. "
            "Please choose a separate output directory.",
            file=sys.stderr,
        )
        return 1
    except ValueError:
        pass

    output_root.mkdir(parents=True, exist_ok=True)

    print("Reading installed font metadata...")
    exact_index, compact_index, font_file_count, face_count = load_font_index()
    print(f"Font files inspected: {font_file_count}")
    print(f"Font faces inspected: {face_count}")
    print()

    # Classification is cheap and intentionally covers every source file.  Its
    # category map lets duplicate detection choose the most useful canonical
    # representative without ever limiting the duplicate scan to conversion
    # targets.
    classification = classify_svgs(input_root, output_root, exact_index, compact_index)
    file_categories = classification.get("file_categories", {})

    print()
    print("Classification summary")
    print("----------------------")
    print(f"SVGs with all USED fonts available: {classification['available']}")
    print(f"  containing aliases:               {classification['aliases']}")
    print(f"SVGs with missing USED fonts:       {classification['missing']}")
    print(f"SVGs with no active text:           {classification['no_active_text']}")
    print(f"SVGs using an implicit/default font:{classification['implicit_default']}")
    print(f"SVGs with empty text placeholders:  {classification['empty_placeholder_files']}")
    print(f"Unused font declarations ignored:   {classification['unused_decl_count']}")
    print(f"SVG parse errors:                   {classification['parse_errors']}")

    duplicate_representatives = {}
    if args.find_duplicates:
        try:
            duplicate_result = find_duplicate_svgs(
                input_root,
                output_root,
                file_categories,
                render_size=args.duplicate_render_size,
                jobs=args.jobs,
                renderer=args.duplicate_renderer,
            )
            duplicate_representatives = duplicate_result["duplicate_representatives"]
        except Exception as e:
            print()
            print(f"Duplicate detection failed: {e}", file=sys.stderr)
            return 1

        if args.separate_duplicates:
            try:
                separate_duplicate_outputs(
                    input_root,
                    output_root,
                    file_categories,
                    duplicate_representatives,
                )
            except Exception as e:
                print()
                print(f"Duplicate separation failed: {e}", file=sys.stderr)
                return 1

    if args.classify_only:
        print()
        print(f"Reports written to: {output_root / 'reports'}")
        return 0

    # If duplicates were separated, redundant fonts_available copies are no
    # longer present in that tree.  If they were not separated, this mapping
    # still prevents their conversion and marks them SKIPPED_DUPLICATE.
    conversion = convert_available_svgs(
        output_root,
        exact_index,
        compact_index,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        keep_failed=args.keep_failed,
        jobs=args.jobs,
        duplicate_representatives=duplicate_representatives,
        text_converter=args.text_converter,
    )

    print()
    print("Conversion summary")
    print("------------------")
    version_suffix = (
        f" ({conversion['converter_version']})"
        if conversion.get("converter_version") else ""
    )
    print(f"Converter:          {conversion['converter']}{version_suffix}")
    print(f"Elapsed:            {conversion['elapsed_seconds']:.3f} s")
    print(f"Converted:          {conversion['converted']}")
    print(f"Skipped existing:   {conversion['skipped']}")
    print(f"Skipped duplicates: {conversion['skipped_duplicates']}")
    print(f"Failed:             {conversion['failed']}")

    print()
    print(f"Output root:       {output_root}")
    print(f"Classification:    {output_root / 'fonts_available'}")
    print(f"Missing fonts:     {output_root / 'fonts_missing'}")
    print(f"No active text:    {output_root / 'no_active_text'}")
    if args.separate_duplicates:
        print(f"Duplicates:        {output_root / 'duplicates'}")
    print(f"Converted SVGs:    {output_root / 'text_as_paths'}")
    print(f"Reports:           {output_root / 'reports'}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
