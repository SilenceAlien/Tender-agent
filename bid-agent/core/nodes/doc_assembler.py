"""DocumentAssembler node — merges sections and exports formatted DOCX.

Contract (from node_interfaces.md):
    def doc_assembler(state: AgentState) -> dict
    Input:  state.sections, state.requirements.format_rules
    Output: {export_path: str, node_status: {...}}
    Constraints:
    - T019: Merge all sections, inject format template (headings, fonts, TOC)
    - T020: Apply page margins (上3.7/下3.5/左2.8/右2.6cm), line spacing 28pt,
            first-line indent 2 chars
    - Export to data/exports/{project_id}_标书_v{round}.docx
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from core.state import AgentState, NodeStatus

logger = logging.getLogger(__name__)

# ── Format defaults (政府采购标准) ─────────────────────────────────────

DEFAULT_FORMAT = {
    "page_margin": "上3.7cm/下3.5cm/左2.8cm/右2.6cm",
    "font": "正文仿宋_GB2312",
    "line_spacing": "28磅",
    "title_levels": [],
    "seal_requirement": "加盖公章",
    "binding": "胶装",
}

# ── DOCX Builder ───────────────────────────────────────────────────────


def _format_margin(format_rules: dict) -> dict[str, float]:
    """Parse margin string into cm values.

    Format: "上3.7cm/下3.5cm/左2.8cm/右2.6cm"
    """
    import re

    margin_str = format_rules.get("page_margin", DEFAULT_FORMAT["page_margin"])
    margins: dict[str, float] = {"top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6}

    patterns = {
        "top": re.compile(r"上\s*(\d+\.?\d*)\s*cm"),
        "bottom": re.compile(r"下\s*(\d+\.?\d*)\s*cm"),
        "left": re.compile(r"左\s*(\d+\.?\d*)\s*cm"),
        "right": re.compile(r"右\s*(\d+\.?\d*)\s*cm"),
    }

    for key, pat in patterns.items():
        m = pat.search(margin_str)
        if m:
            margins[key] = float(m.group(1))

    return margins


def _cm_to_emu(cm: float) -> int:
    """Convert centimeters to EMU (English Metric Units, used by DOCX)."""
    return int(cm * 360000)


def _display_width(s: str) -> float:
    """Display width of a string: CJK chars = 2 units, ASCII = 1 unit."""
    return sum(2.0 if ord(c) > 127 else 1.0 for c in s)


def _wrap_label(label: str, max_dw: float = 10.0) -> list[str]:
    """Wrap a label into lines, each with display width ≤ ``max_dw``.

    Tries to break at spaces for ASCII text; breaks at any character
    for CJK text.  Returns a list of 1+ lines.
    """
    if _display_width(label) <= max_dw:
        return [label]

    lines: list[str] = []
    # If the label contains spaces, try word-based wrapping first
    if " " in label and _display_width(label.split()[0]) <= max_dw:
        words = label.split()
        current = ""
        current_dw = 0.0
        for word in words:
            word_dw = _display_width(word) + (1.0 if current else 0.0)
            if current_dw + word_dw > max_dw and current:
                lines.append(current)
                current = word
                current_dw = _display_width(word)
            else:
                current = word if not current else current + " " + word
                current_dw = _display_width(current)
        if current:
            lines.append(current)
        return lines

    # Character-based wrapping (CJK or no-spaces)
    current = ""
    current_dw = 0.0
    for char in label:
        char_dw = 2.0 if ord(char) > 127 else 1.0
        if current_dw + char_dw > max_dw and current:
            lines.append(current)
            current = char
            current_dw = char_dw
        else:
            current += char
            current_dw += char_dw
    if current:
        lines.append(current)
    return lines


def _clean_mermaid_label(label: str) -> str:
    """Clean HTML tags and extra whitespace from Mermaid node labels.

    Mermaid allows HTML-like tags inside node labels (e.g. ``<br>`` for
    line breaks).  These tags should not appear in the DOCX text output.
    """
    # Replace <br> variants with a space
    label = re.sub(r"<br\s*/?>", " ", label, flags=re.IGNORECASE)
    # Strip any remaining HTML tags
    label = re.sub(r"<[^>]+>", "", label)
    # Collapse multiple spaces
    label = re.sub(r"\s+", " ", label)
    return label.strip()


def _parse_mermaid_flowchart(
    mermaid_code: str,
) -> tuple[dict[str, str], list[tuple[str, str, str]], str]:
    """Parse Mermaid flowchart code into nodes, edges, and direction.

    Returns:
        (node_labels, edges, direction)
        - node_labels: {node_id: label}
        - edges: [(source, target, edge_label)]
        - direction: "TD" (top-down) or "LR" (left-right)
    """
    node_labels: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    direction = "TD"

    label_pattern = re.compile(
        r"([A-Za-z]\w*)\s*([\[\(\{])([^\]\)\}]+)[\]\)\}]"
    )
    edge_pattern = re.compile(
        r"([A-Za-z]\w*)\s*"       # source node ID
        r"-{2,}>?"                  # --> or ---> or ---
        r"\s*(?:\|([^|]+)\|)?\s*" # optional |edge label|
        r"([A-Za-z]\w*)"            # target node ID
    )
    dash_label_re = re.compile(
        r"([A-Za-z]\w*)\s*--\s*([^|>\-]+?)\s*-->\s*([A-Za-z]\w*)"
    )

    for raw_line in mermaid_code.strip().split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue

        # Extract direction
        dir_m = re.match(r"^(?:flowchart|graph)\s+(\w+)", line, re.IGNORECASE)
        if dir_m:
            d = dir_m.group(1).upper()
            direction = "LR" if d in ("LR", "RL") else "TD"
            continue

        if re.match(r"^subgraph\s+", line, re.IGNORECASE):
            continue
        if re.match(r"^end\s*$", line, re.IGNORECASE):
            continue

        # Normalise ``-- label -->`` to ``-->|label|``
        line = dash_label_re.sub(r"\1 -->|\2| \3", line)

        # Pass 1: extract all node labels
        for m in label_pattern.finditer(line):
            node_id = m.group(1)
            label = _clean_mermaid_label(m.group(3).strip())
            node_labels[node_id] = label

        # Pass 2: strip label syntax
        stripped = label_pattern.sub(r"\1", line)

        # Pass 3: handle ``&`` multi-node syntax
        amp_parts = re.split(r"\s*&\s*", stripped)
        if len(amp_parts) > 1:
            edge_sep_re = re.compile(r"\s*(-{2,}>?)\s*")
            all_sources: list[str] = []
            all_targets: list[str] = []
            edge_label = ""
            sep_found = False
            for ap in amp_parts:
                ap = ap.strip()
                sep_m = edge_sep_re.search(ap)
                if sep_m:
                    sep_found = True
                    sides = edge_sep_re.split(ap)
                    left = sides[0].strip()
                    lbl_m = re.search(r"\|([^|]+)\|", ap)
                    if lbl_m:
                        edge_label = lbl_m.group(1).strip()
                    if left:
                        all_sources.append(left)
                    right_parts = [s.strip() for s in sides[2:] if s and s.strip()]
                    for rp in right_parts:
                        rp = re.sub(r"\|[^|]*\|", "", rp).strip()
                        if rp:
                            all_targets.append(rp)
                else:
                    node_id = re.sub(r"\|[^|]*\|", "", ap).strip()
                    if node_id:
                        if not sep_found:
                            all_sources.append(node_id)
                        else:
                            all_targets.append(node_id)
            for src in all_sources:
                src = src.strip()
                if src not in node_labels:
                    node_labels[src] = src
                for tgt in all_targets:
                    tgt = tgt.strip()
                    if tgt not in node_labels:
                        node_labels[tgt] = tgt
                    edges.append((src, tgt, edge_label))
        else:
            for m in edge_pattern.finditer(stripped):
                src = m.group(1)
                edge_label = (m.group(2) or "").strip()
                tgt = m.group(3)
                edges.append((src, tgt, edge_label))
                if src not in node_labels:
                    node_labels[src] = src
                if tgt not in node_labels:
                    node_labels[tgt] = tgt

    return node_labels, edges, direction


def _mermaid_to_text_diagram(mermaid_code: str) -> list[str]:
    """Convert a simple Mermaid flowchart to a text-based tree diagram.

    Parses ``flowchart TD`` / ``flowchart LR`` syntax and produces
    an indented text representation suitable for DOCX output.

    Returns a list of text lines (each becomes a paragraph in DOCX).
    """
    lines: list[str] = []

    node_labels, edges, _ = _parse_mermaid_flowchart(mermaid_code)

    if not edges:
        # No edges parsed → just output the raw mermaid as a code block
        return ["[图表代码]", mermaid_code.strip()]

    # Find root nodes (nodes that never appear as a target)
    targets = {t for _, t, _ in edges}
    roots = [n for n in node_labels if n not in targets]
    if not roots:
        # Cyclic graph → pick the first source as root
        roots = [edges[0][0]]

    # Build adjacency list
    children: dict[str, list[tuple[str, str]]] = {}
    for src, tgt, label in edges:
        children.setdefault(src, []).append((tgt, label))

    # DFS to build text tree
    visited: set[str] = set()

    def _render_node(node_id: str, indent: str, is_last: bool) -> list[str]:
        if node_id in visited:
            return []
        visited.add(node_id)
        label = node_labels.get(node_id, node_id)
        prefix = indent + ("└── " if is_last else "├── ")
        result = [f"{prefix}{label}"]

        kids = children.get(node_id, [])
        child_indent = indent + ("    " if is_last else "│   ")
        for i, (kid_id, edge_label) in enumerate(kids):
            kid_is_last = (i == len(kids) - 1)
            if edge_label:
                result.append(f"{child_indent}    [{edge_label}]")
            result.extend(_render_node(kid_id, child_indent, kid_is_last))
        return result

    # Render from each root
    for i, root in enumerate(roots):
        root_is_last = (i == len(roots) - 1)
        label = node_labels.get(root, root)
        prefix = "┌── " if len(roots) == 1 else ("└── " if root_is_last else "├── ")
        lines.append(f"{prefix}{label}")
        kids = children.get(root, [])
        child_indent = "│   " if not root_is_last else "    "
        for j, (kid_id, edge_label) in enumerate(kids):
            kid_is_last = (j == len(kids) - 1)
            if edge_label:
                lines.append(f"{child_indent}    [{edge_label}]")
            lines.extend(_render_node(kid_id, child_indent, kid_is_last))

    return lines


def _mermaid_to_png(mermaid_code: str, output_path: str) -> str | None:
    """Render a Mermaid flowchart as a PNG image.

    Uses matplotlib as primary renderer.  Falls back to Pillow if
    matplotlib is not available.  Returns the path to the saved PNG,
    or ``None`` if rendering fails (caller should fall back to text).
    """
    # Try matplotlib first (better quality)
    result = _mermaid_to_png_matplotlib(mermaid_code, output_path)
    if result:
        return result

    # Fall back to Pillow
    result = _mermaid_to_png_pillow(mermaid_code, output_path)
    if result:
        return result

    return None


def _mermaid_to_png_matplotlib(mermaid_code: str, output_path: str) -> str | None:
    """Render Mermaid flowchart as PNG using matplotlib."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import numpy as np
        from matplotlib.patches import FancyBboxPatch

        # ── Chinese font setup ──
        available_fonts = {f.name for f in fm.fontManager.ttflist}
        cn_font = None
        for candidate in [
            "Arial Unicode MS",
            "PingFang SC",
            "Heiti TC",
            "STHeiti",
            "SimHei",
            "Microsoft YaHei",
            "WenQuanYi Micro Hei",
            "Songti SC",
            "STSong",
        ]:
            if candidate in available_fonts:
                cn_font = candidate
                break
        if cn_font:
            matplotlib.rcParams["font.sans-serif"] = [cn_font, "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False

        # ── Parse mermaid ──
        node_labels, edges, direction = _parse_mermaid_flowchart(mermaid_code)
        if not edges:
            return None

        # ── Build adjacency list and find roots ──
        children: dict[str, list[str]] = {}
        for src, tgt, _ in edges:
            children.setdefault(src, []).append(tgt)

        targets = {t for _, t, _ in edges}
        roots = [n for n in node_labels if n not in targets]
        if not roots:
            roots = [list(node_labels.keys())[0]]

        # ── Tree layout algorithm ──
        # Assign x positions to leaves sequentially, internal nodes are
        # centred above their children.  y = -depth for top-down.
        positions: dict[str, tuple[float, float]] = {}
        visited: set[str] = set()
        leaf_x = [0.0]

        def _assign(node: str, depth: int) -> float:
            if node in visited:
                return positions[node][0]
            visited.add(node)

            kids = children.get(node, [])
            # Filter out already-visited kids to prevent cycles
            unvisited_kids = [k for k in kids if k not in visited]

            if not unvisited_kids:
                x = leaf_x[0]
                leaf_x[0] += 1.0
                positions[node] = (x, float(-depth))
                return x

            child_xs = [_assign(k, depth + 1) for k in unvisited_kids]
            x = sum(child_xs) / len(child_xs)
            positions[node] = (x, float(-depth))
            return x

        for root in roots:
            _assign(root, 0)

        # Place any remaining isolated nodes
        for node_id in node_labels:
            if node_id not in positions:
                positions[node_id] = (leaf_x[0], 0.0)
                leaf_x[0] += 1.0

        # ── Calculate box sizes with label wrapping ──
        # Wrap long labels to keep boxes compact and prevent overlap.
        max_line_dw = 10.0  # max display width per line (≈ 5 CJK chars)
        wrapped_labels: dict[str, list[str]] = {}
        box_sizes: dict[str, tuple[float, float]] = {}
        for nid, label in node_labels.items():
            lines = _wrap_label(label, max_line_dw)
            wrapped_labels[nid] = lines
            # Box width based on the widest line
            widest_dw = max(_display_width(ln) for ln in lines)
            w = max(1.8, widest_dw * 0.22 + 0.6)
            # Box height grows with number of lines
            h = 0.65 * len(lines) + 0.15 * (len(lines) - 1)
            box_sizes[nid] = (w, h)

        # ── Dynamic spacing: ensure nodes never overlap ──
        # scale_x must be > max_box_width + gap; scale_y > max_box_height + gap
        max_box_w = max(w for w, _ in box_sizes.values())
        max_box_h = max(h for _, h in box_sizes.values())
        min_gap_x = 1.2   # minimum horizontal gap between adjacent boxes
        min_gap_y = 1.0   # minimum vertical gap between levels
        scale_x = max(2.8, max_box_w + min_gap_x)
        scale_y = max(1.8, max_box_h + min_gap_y)

        scaled: dict[str, tuple[float, float]] = {}
        for nid, (x, y) in positions.items():
            if direction == "LR":
                scaled[nid] = (y * scale_x, -x * scale_y)
            else:
                scaled[nid] = (x * scale_x, y * scale_y)
        positions = scaled

        # ── Determine figure size (matched to data aspect ratio) ──
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        pad = 1.5
        x_range = max(xs) - min(xs) + 2 * pad
        y_range = max(ys) - min(ys) + 2 * pad
        # Avoid division by zero for single-level diagrams
        aspect = x_range / y_range if y_range > 0.01 else 1.0

        # Calculate figure dimensions that match the data aspect ratio,
        # so set_aspect("equal") won't introduce excessive whitespace.
        max_fig_w = 20.0
        max_fig_h = 14.0
        min_fig_w = 6.0
        min_fig_h = 4.0

        if aspect >= 1.0:  # wider than tall
            fig_w = min(max_fig_w, max(min_fig_w, 10.0 * aspect))
            fig_h = fig_w / aspect
            if fig_h > max_fig_h:
                fig_h = max_fig_h
                fig_w = fig_h * aspect
        else:  # taller than wide
            fig_h = min(max_fig_h, max(min_fig_h, 10.0 / aspect))
            fig_w = fig_h * aspect
            if fig_w > max_fig_w:
                fig_w = max_fig_w
                fig_h = fig_w / aspect

        fig_w = max(min_fig_w, fig_w)
        fig_h = max(min_fig_h, fig_h)

        fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))
        ax.set_aspect("equal")
        ax.axis("off")
        fig.patch.set_facecolor("white")

        # ── Helper: distance from box centre to edge along a direction ──
        def _box_edge_offset(hw: float, hh: float, ux: float, uy: float) -> float:
            """Distance from box centre to rectangle edge along unit vector (ux, uy).

            For a box with half-width ``hw`` and half-height ``hh``, computes
            the parametric distance ``t`` such that ``(t*ux, t*uy)`` lies on
            the box border.  This replaces the old ``min(w, h)/2`` heuristic
            which placed arrow endpoints *inside* wide boxes.
            """
            tx = hw / abs(ux) if abs(ux) > 1e-9 else float("inf")
            ty = hh / abs(uy) if abs(uy) > 1e-9 else float("inf")
            return min(tx, ty)

        # ── Draw edges ──
        for src, tgt, edge_label in edges:
            if src not in positions or tgt not in positions:
                continue
            x1, y1 = positions[src]
            x2, y2 = positions[tgt]
            sw, sh = box_sizes.get(src, (1.8, 0.65))
            tw, th = box_sizes.get(tgt, (1.8, 0.65))

            dx = x2 - x1
            dy = y2 - y1
            dist = np.sqrt(dx * dx + dy * dy)
            if dist < 0.01:
                continue
            ux, uy = dx / dist, dy / dist

            # Calculate actual box-edge intersection points
            start_off = _box_edge_offset(sw / 2, sh / 2, ux, uy)
            end_off = _box_edge_offset(tw / 2, th / 2, -ux, -uy)
            # Clamp offsets so they never exceed the inter-box distance
            start_off = min(start_off, dist * 0.45)
            end_off = min(end_off, dist * 0.45)

            ax.annotate(
                "",
                xy=(x2 - ux * end_off, y2 - uy * end_off),
                xytext=(x1 + ux * start_off, y1 + uy * start_off),
                arrowprops=dict(
                    arrowstyle="->",
                    color="#5B6B7D",
                    lw=1.5,
                    connectionstyle="arc3,rad=0",
                ),
            )

            if edge_label:
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                ax.text(
                    mx,
                    my,
                    edge_label,
                    fontsize=7,
                    ha="center",
                    va="center",
                    color="#E65100",
                    bbox=dict(
                        boxstyle="round,pad=0.2",
                        facecolor="#FFF3E0",
                        edgecolor="#FFB74D",
                        alpha=0.95,
                    ),
                )

        # ── Draw nodes ──
        for nid, (x, y) in positions.items():
            lines = wrapped_labels.get(nid, [node_labels.get(nid, nid)])
            w, h = box_sizes.get(nid, (1.8, 0.65))

            # Rounded rectangle box
            box = FancyBboxPatch(
                (x - w / 2, y - h / 2),
                w,
                h,
                boxstyle="round,pad=0.12",
                facecolor="#E3F2FD",
                edgecolor="#1565C0",
                linewidth=1.5,
                zorder=2,
            )
            ax.add_patch(box)

            # Render wrapped label (newline-separated)
            display_text = "\n".join(lines)
            line_count = len(lines)
            font_sz = 9 if line_count <= 2 else 8
            ax.text(
                x,
                y,
                display_text,
                fontsize=font_sz,
                ha="center",
                va="center",
                color="#0D47A1",
                zorder=3,
                linespacing=1.3,
            )

        # ── Set axis limits (pad already computed above) ──
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)

        # ── Save ──
        fig.savefig(
            output_path,
            dpi=200,
            bbox_inches="tight",
            facecolor="white",
            edgecolor="none",
            pad_inches=0.3,
        )
        plt.close(fig)

        logger.info(f"Mermaid diagram rendered to {output_path}")
        return output_path

    except ImportError:
        logger.info("matplotlib not available, trying Pillow fallback")
        return None
    except Exception as e:
        logger.warning(f"Failed to render mermaid as image (matplotlib): {e}")
        return None


def _mermaid_to_png_pillow(mermaid_code: str, output_path: str) -> str | None:
    """Render Mermaid flowchart as PNG using Pillow (fallback renderer).

    Uses Pillow's ImageDraw to draw boxes and lines.  Less polished
    than matplotlib but has no external dependencies beyond Pillow.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        # ── Parse mermaid ──
        node_labels, edges, direction = _parse_mermaid_flowchart(mermaid_code)
        if not edges:
            return None

        # ── Build adjacency list and find roots ──
        children: dict[str, list[str]] = {}
        for src, tgt, _ in edges:
            children.setdefault(src, []).append(tgt)

        targets = {t for _, t, _ in edges}
        roots = [n for n in node_labels if n not in targets]
        if not roots:
            roots = [list(node_labels.keys())[0]]

        # ── Tree layout ──
        positions: dict[str, tuple[float, float]] = {}
        visited: set[str] = set()
        leaf_x = [0.0]

        def _assign(node: str, depth: int) -> float:
            if node in visited:
                return positions[node][0]
            visited.add(node)
            kids = children.get(node, [])
            unvisited_kids = [k for k in kids if k not in visited]
            if not unvisited_kids:
                x = leaf_x[0]
                leaf_x[0] += 1.0
                positions[node] = (x, float(-depth))
                return x
            child_xs = [_assign(k, depth + 1) for k in unvisited_kids]
            x = sum(child_xs) / len(child_xs)
            positions[node] = (x, float(-depth))
            return x

        for root in roots:
            _assign(root, 0)
        for node_id in node_labels:
            if node_id not in positions:
                positions[node_id] = (leaf_x[0], 0.0)
                leaf_x[0] += 1.0

        # ── Font setup ──
        font_size = 16
        font = None
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()

        # ── Layout parameters ──
        box_pad = 12
        h_gap = 50
        v_gap = 60
        line_h = 20  # pixel height per text line
        max_line_dw_pillow = 10.0

        # Wrap labels and compute per-node box sizes
        wrapped_labels_pl: dict[str, list[str]] = {}
        box_widths: dict[str, int] = {}
        box_heights: dict[str, int] = {}
        for nid, label in node_labels.items():
            lines = _wrap_label(label, max_line_dw_pillow)
            wrapped_labels_pl[nid] = lines
            widest_dw = max(_display_width(ln) for ln in lines)
            box_widths[nid] = max(100, int(widest_dw * 10) + box_pad * 2)
            box_heights[nid] = 44 + (len(lines) - 1) * line_h

        max_bw = max(box_widths.values())
        max_bh = max(box_heights.values())
        unit_w = max_bw + h_gap
        unit_h = max_bh + v_gap

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        x_range = max(xs) - min(xs) if len(xs) > 1 else 1
        y_range = max(ys) - min(ys) if len(ys) > 1 else 1

        # For LR direction, swap dimensions: depth → horizontal, leaf index → vertical
        if direction == "LR":
            img_w = int((y_range + 2) * unit_w) + 40
            img_h = int((x_range + 2) * unit_h) + 40
        else:
            img_w = int((x_range + 2) * unit_w) + 40
            img_h = int((y_range + 2) * unit_h) + 40

        pixel_pos: dict[str, tuple[int, int]] = {}
        for nid, (x, y) in positions.items():
            if direction == "LR":
                # Root (y=0, depth=0) → left (small px); leaves → right
                px = int((-y + 1) * unit_w) + 20
                # Leaf index (x) → vertical, top to bottom
                py = int((x - min(xs) + 1) * unit_h) + 20
            else:
                px = int((x - min(xs) + 1) * unit_w) + 20
                py = int((max(ys) - y + 1) * unit_h) + 20
            pixel_pos[nid] = (px, py)

        # ── Draw ──
        img = Image.new("RGB", (img_w, img_h), "white")
        draw = ImageDraw.Draw(img)

        # Draw edges first (under boxes)
        for src, tgt, edge_label in edges:
            if src not in pixel_pos or tgt not in pixel_pos:
                continue
            x1, y1 = pixel_pos[src]
            x2, y2 = pixel_pos[tgt]
            w1 = box_widths.get(src, 100)
            w2 = box_widths.get(tgt, 100)
            h1 = box_heights.get(src, 44)
            h2 = box_heights.get(tgt, 44)

            if direction == "LR":
                sx = x1 + w1 // 2
                sy = y1
                ex = x2 - w2 // 2
                ey = y2
            else:
                sx = x1
                sy = y1 + h1 // 2
                ex = x2
                ey = y2 - h2 // 2

            # Draw L-shaped connector for tree layout
            if direction != "LR":
                mid_y = (sy + ey) // 2
                draw.line([(sx, sy), (sx, mid_y), (ex, mid_y), (ex, ey)],
                          fill="#5B6B7D", width=2)
            else:
                mid_x = (sx + ex) // 2
                draw.line([(sx, sy), (mid_x, sy), (mid_x, ey), (ex, ey)],
                          fill="#5B6B7D", width=2)

            # Arrowhead
            ah = 8
            if direction != "LR":
                draw.polygon([(ex, ey), (ex - ah, ey - ah), (ex - ah, ey + ah)],
                             fill="#5B6B7D")
            else:
                draw.polygon([(ex, ey), (ex - ah, ey - ah), (ex + ah, ey - ah)],
                             fill="#5B6B7D")

            if edge_label:
                mx, my = (sx + ex) // 2, (sy + ey) // 2
                tw = _display_width(edge_label) * 8 + 8
                draw.rounded_rectangle(
                    [mx - tw // 2, my - 10, mx + tw // 2, my + 10],
                    radius=4, fill="#FFF3E0", outline="#FFB74D",
                )
                draw.text((mx - tw // 2 + 4, my - 7), edge_label,
                          fill="#E65100", font=font)

        # Draw nodes
        for nid, (px, py) in pixel_pos.items():
            lines = wrapped_labels_pl.get(nid, [node_labels.get(nid, nid)])
            bw = box_widths.get(nid, 100)
            bh = box_heights.get(nid, 44)
            x0 = px - bw // 2
            y0 = py - bh // 2
            draw.rounded_rectangle(
                [x0, y0, x0 + bw, y0 + bh],
                radius=8, fill="#E3F2FD", outline="#1565C0", width=2,
            )
            # Draw each line centered in box
            total_text_h = len(lines) * line_h
            start_y = py - total_text_h // 2 + (line_h - font_size) // 2
            for li, line in enumerate(lines):
                tw = int(_display_width(line) * 10)
                draw.text((px - tw // 2, start_y + li * line_h), line,
                          fill="#0D47A1", font=font)

        img.save(output_path, "PNG")
        logger.info(f"Mermaid diagram rendered (Pillow) to {output_path}")
        return output_path

    except ImportError:
        logger.warning("Pillow not available for diagram rendering")
        return None
    except Exception as e:
        logger.warning(f"Failed to render mermaid as image (Pillow): {e}")
        return None


# ── Markdown table parser ──────────────────────────────────────────────


def _is_markdown_table_line(line: str) -> bool:
    """Check if a line looks like a markdown table row."""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]


def _is_separator_line(line: str) -> bool:
    """Check if a line is a markdown table separator (e.g. |---|---|)."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    inner = stripped[1:-1]
    # Separator cells should only contain dashes, colons, and spaces
    cells = inner.split("|")
    for cell in cells:
        cell = cell.strip()
        if not cell:
            return False
        if not re.match(r"^:?-{2,}:?$", cell):
            return False
    return True


def _add_markdown_heading(
    doc,
    text: str,
    level: int,
    actual_font: str,
    qn,
    Pt,
    RGBColor,
    placeholder_re,
) -> None:
    """Add a markdown heading (##, ###, etc.) to the document."""
    body_para = doc.add_paragraph()
    body_para.paragraph_format.first_line_indent = Pt(0)
    body_para.paragraph_format.space_before = Pt(6)
    body_para.paragraph_format.space_after = Pt(3)

    # Font and size by heading level
    heading_font = "黑体" if level <= 2 else "楷体"
    size_map = {1: 16, 2: 14, 3: 13, 4: 12, 5: 12, 6: 12}
    heading_size = size_map.get(level, 12)

    _add_runs_with_placeholders(
        body_para, text, actual_font, qn, Pt, RGBColor, placeholder_re, bold=True
    )
    for run in body_para.runs:
        run.font.size = Pt(heading_size)
        run.font.name = heading_font
        rpr = run._element.find(qn("w:rPr"))
        if rpr is None:
            rpr = run._element.makeelement(qn("w:rPr"), {})
            run._element.insert(0, rpr)
        rFonts = rpr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.append(rFonts)
        rFonts.set(qn("w:eastAsia"), heading_font)


def _add_markdown_list_item(
    doc,
    text: str,
    marker: str,
    actual_font: str,
    qn,
    Pt,
    RGBColor,
    placeholder_re,
) -> None:
    """Add a markdown list item (-, *, or 1.) to the document."""
    body_para = doc.add_paragraph()
    body_para.paragraph_format.first_line_indent = Pt(0)
    body_para.paragraph_format.left_indent = Pt(24)
    body_para.paragraph_format.space_after = Pt(0)

    # Determine bullet/number prefix
    if marker in ("-", "*"):
        prefix = "• "
    else:
        prefix = marker + " "

    # Add prefix run
    prefix_run = body_para.add_run(prefix)
    prefix_run.font.name = actual_font
    rpr = prefix_run._element.find(qn("w:rPr"))
    if rpr is None:
        rpr = prefix_run._element.makeelement(qn("w:rPr"), {})
        prefix_run._element.insert(0, rpr)
    rFonts = rpr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), actual_font)

    # Add text with placeholder/bold support
    _add_runs_with_placeholders(
        body_para, text, actual_font, qn, Pt, RGBColor, placeholder_re
    )


def _parse_markdown_table_row(line: str) -> list[str]:
    """Parse a markdown table row into cell values."""
    stripped = line.strip()
    # Remove leading and trailing pipes
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    # Split by pipe, but handle escaped pipes (\|)
    cells = []
    current = ""
    i = 0
    while i < len(stripped):
        if stripped[i] == "\\" and i + 1 < len(stripped) and stripped[i + 1] == "|":
            current += "|"
            i += 2
        elif stripped[i] == "|":
            cells.append(current.strip())
            current = ""
            i += 1
        else:
            current += stripped[i]
            i += 1
    cells.append(current.strip())
    return cells


def _add_table_to_docx(
    doc,
    header: list[str],
    rows: list[list[str]],
    actual_font: str,
    qn,
    Pt,
    RGBColor,
    placeholder_re,
) -> None:
    """Add a formatted Word table from parsed markdown table data.

    Handles placeholder text (【待填写：...】) within cells by rendering
    them in red bold, consistent with body text formatting.
    """

    # Normalise column count
    col_count = max(len(header), max((len(r) for r in rows), default=0))
    # Pad header and rows to the same width
    header = header + [""] * (col_count - len(header))
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    table = doc.add_table(rows=1 + len(rows), cols=col_count)
    table.style = "Table Grid"

    # Header row — bold with shaded background
    for j, cell_text in enumerate(header):
        cell = table.cell(0, j)
        # Clear default paragraph
        cell.paragraphs[0].clear()
        para = cell.paragraphs[0]
        para.alignment = 1  # WD_ALIGN_PARAGRAPH.CENTER
        _add_runs_with_placeholders(
            para, cell_text, actual_font, qn, Pt, RGBColor, placeholder_re, bold=True
        )
        # Shade the header cell
        from docx.oxml import OxmlElement
        tc_pr = cell._element.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), "D9E2F3")
        shd.set(qn("w:val"), "clear")
        tc_pr.append(shd)

    # Data rows
    for i, row in enumerate(rows, start=1):
        for j, cell_text in enumerate(row):
            cell = table.cell(i, j)
            cell.paragraphs[0].clear()
            para = cell.paragraphs[0]
            _add_runs_with_placeholders(
                para, cell_text, actual_font, qn, Pt, RGBColor, placeholder_re
            )
            # Set font size for table cells (slightly smaller)
            for run in para.runs:
                run.font.size = Pt(10.5)


def _add_runs_with_placeholders(
    para,
    text: str,
    actual_font: str,
    qn,
    Pt,
    RGBColor,
    placeholder_re,
    bold: bool = False,
) -> None:
    """Add runs to a paragraph, splitting by placeholders and **bold** markers.

    Placeholder segments (【待填写：...】) are rendered red+bold.
    ``**bold**`` markdown segments are rendered bold.
    ``<br>`` tags are converted to line breaks.
    """
    import re as _re

    # Replace <br> with a special marker we can split on
    text = _re.sub(r"<br\s*/?>", "\n", text, flags=_re.IGNORECASE)

    # Split by newlines first (for <br> handling)
    lines = text.split("\n")
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            para.add_run().add_break()

        # Split by placeholder pattern
        parts = placeholder_re.split(line)
        for part in parts:
            if not part:
                continue

            # Check if this part is a placeholder
            is_placeholder = bool(placeholder_re.fullmatch(part))

            # Handle **bold** markdown within non-placeholder parts
            if not is_placeholder:
                bold_segments = _re.split(r"(\*\*[^*]+\*\*)", part)
                for seg in bold_segments:
                    if not seg:
                        continue
                    if seg.startswith("**") and seg.endswith("**"):
                        # Bold text
                        run = para.add_run(seg[2:-2])
                        run.bold = True
                    else:
                        run = para.add_run(seg)
                        if bold:
                            run.bold = True
                    run.font.name = actual_font
                    rpr = run._element.find(qn("w:rPr"))
                    if rpr is None:
                        rpr = run._element.makeelement(qn("w:rPr"), {})
                        run._element.insert(0, rpr)
                    rFonts = rpr.find(qn("w:rFonts"))
                    if rFonts is None:
                        rFonts = rpr.makeelement(qn("w:rFonts"), {})
                        rpr.append(rFonts)
                    rFonts.set(qn("w:eastAsia"), actual_font)
            else:
                # Placeholder segment — red bold
                run = para.add_run(part)
                run.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
                run.font.name = actual_font
                rpr = run._element.find(qn("w:rPr"))
                if rpr is None:
                    rpr = run._element.makeelement(qn("w:rPr"), {})
                    run._element.insert(0, rpr)
                rFonts = rpr.find(qn("w:rFonts"))
                if rFonts is None:
                    rFonts = rpr.makeelement(qn("w:rFonts"), {})
                    rpr.append(rFonts)
                rFonts.set(qn("w:eastAsia"), actual_font)


# ── DOCX Builder ───────────────────────────────────────────────────────


def _build_docx(
    sections: dict[str, str],
    format_rules: dict,
    export_path: str,
) -> str:
    """Build a DOCX file with proper formatting.

    Args:
        sections: {section_name: content}
        format_rules: format configuration dict
        export_path: output file path

    Returns:
        Absolute path to the exported file
    """
    import re

    import tempfile

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()

    # ── Page setup ──────────────────────────────────────────────────────
    margins = _format_margin(format_rules)
    for section in doc.sections:
        section.top_margin = Cm(margins["top"])
        section.bottom_margin = Cm(margins["bottom"])
        section.left_margin = Cm(margins["left"])
        section.right_margin = Cm(margins["right"])

    # ── Default font ────────────────────────────────────────────────────
    style = doc.styles["Normal"]
    font = style.font
    font_name = format_rules.get("font", DEFAULT_FORMAT["font"])
    # Map format to actual font names
    font_map = {
        "正文仿宋_GB2312": "仿宋_GB2312",
        "正文仿宋": "仿宋",
        "标题黑体": "黑体",
    }
    actual_font = font_map.get(font_name, "仿宋")
    font.name = actual_font
    font.size = Pt(12)  # 小四号

    # Set East Asian font
    rpr = style.element.find(qn("w:rPr"))
    if rpr is None:
        rpr = style.element.makeelement(qn("w:rPr"), {})
        style.element.append(rpr)
    rFonts = rpr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), actual_font)

    # ── Line spacing 28pt ───────────────────────────────────────────────
    line_spacing_raw = format_rules.get("line_spacing", DEFAULT_FORMAT["line_spacing"])
    try:
        line_spacing_pt = float(line_spacing_raw.replace("磅", "").strip())
    except (ValueError, AttributeError):
        line_spacing_pt = 28.0

    pf = style.paragraph_format
    pf.line_spacing = Pt(line_spacing_pt)
    pf.space_after = Pt(0)
    pf.space_before = Pt(0)

    # ── First-line indent 2 chars (≈ 24pt for 小四号) ──────────────────
    pf.first_line_indent = Pt(24)

    # ── Title page ──────────────────────────────────────────────────────
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_para.add_run("投  标  书")
    title_run.font.size = Pt(22)
    title_run.font.name = "黑体"
    title_run.bold = True
    title_run.element.find(qn("w:rPr")).find(qn("w:rFonts")).set(qn("w:eastAsia"), "黑体")

    # Date line
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date_para.add_run(datetime.now().strftime("%Y年%m月%d日"))
    date_run.font.size = Pt(14)

    doc.add_page_break()

    # ── Table of Contents placeholder ───────────────────────────────────
    toc_heading = doc.add_paragraph()
    toc_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    toc_run = toc_heading.add_run("目  录")
    toc_run.font.size = Pt(16)
    toc_run.font.name = "黑体"
    toc_run.bold = True

    # Usable page width for right-aligned tab stop (A4 = 21cm)
    usable_width_cm = 21.0 - margins["left"] - margins["right"]

    for i, section_name in enumerate(sections.keys(), 1):
        toc_line = doc.add_paragraph()
        # Right-aligned tab stop with dotted leader at the right margin
        toc_line.paragraph_format.tab_stops.add_tab_stop(
            Cm(usable_width_cm), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS
        )
        toc_run = toc_line.add_run(f"{section_name}\t第{i}页")
        toc_run.font.size = Pt(12)

    doc.add_page_break()

    # ── Section content ─────────────────────────────────────────────────
    title_levels = format_rules.get("title_levels", [])

    # Temp directory for rendered diagram images (cleaned up after save)
    import shutil
    _diagram_img_dir = tempfile.mkdtemp(prefix="bid_diagram_")
    _diagram_counter = 0

    for section_name, content in sections.items():
        # Determine heading font from format rules
        if title_levels and len(title_levels) > 0:
            heading_font = title_levels[0] if len(title_levels) > 0 else "黑体"
        else:
            heading_font = "黑体"

        # Section heading
        heading_para = doc.add_paragraph()
        heading_run = heading_para.add_run(section_name)
        heading_run.font.size = Pt(16)
        heading_run.font.name = heading_font
        heading_run.bold = True

        # Section body — split by lines, then split each line by
        # 【待填写：...】 placeholders so they can be styled red+bold.
        # Mermaid code blocks (```mermaid ... ```) are converted to
        # text-based tree diagrams for DOCX output.
        placeholder_re = re.compile(r"(【待填写[^】]*】)")
        mermaid_block_re = re.compile(
            r"```mermaid\s*\n([\s\S]*?)```", re.MULTILINE
        )

        # Split content into segments: text and mermaid blocks
        pos = 0
        content_segments: list[tuple[str, str]] = []  # (type, content)
        for m in mermaid_block_re.finditer(content):
            if m.start() > pos:
                text_before = content[pos:m.start()]
                if text_before.strip():
                    content_segments.append(("text", text_before))
            content_segments.append(("mermaid", m.group(1)))
            pos = m.end()
        if pos < len(content):
            remaining = content[pos:]
            if remaining.strip():
                content_segments.append(("text", remaining))

        # If no segments (no mermaid), treat entire content as text
        if not content_segments:
            content_segments = [("text", content)]

        for seg_type, seg_content in content_segments:
            if seg_type == "mermaid":
                # Try to render as PNG image first; fall back to text tree
                _diagram_counter += 1
                img_path = str(
                    Path(_diagram_img_dir) / f"diagram_{_diagram_counter}.png"
                )
                rendered = _mermaid_to_png(seg_content, img_path)

                if rendered and Path(rendered).exists():
                    # ── Spacer paragraph BEFORE the image ──
                    # Prevents preceding text from overlapping the image.
                    spacer_before = doc.add_paragraph()
                    spacer_before.paragraph_format.first_line_indent = Pt(0)
                    spacer_before.paragraph_format.space_before = Pt(0)
                    spacer_before.paragraph_format.space_after = Pt(0)
                    spacer_before.paragraph_format.line_spacing = Pt(6)

                    # ── Image paragraph ──
                    # Override the default 28pt fixed line spacing so the
                    # paragraph can grow to fit the image height; otherwise
                    # the image overflows and is covered by the next paragraph.
                    body_para = doc.add_paragraph()
                    body_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    body_para.paragraph_format.first_line_indent = Pt(0)
                    body_para.paragraph_format.space_before = Pt(6)
                    body_para.paragraph_format.space_after = Pt(6)
                    body_para.paragraph_format.line_spacing = 1.0  # single spacing
                    run = body_para.add_run()

                    # A4 height 29.7cm − top/bottom margins ≈ 22.5cm;
                    # leave room for heading + surrounding text.
                    max_height_cm = 20.0
                    target_width_cm = usable_width_cm

                    # Read image dimensions to calculate scaled height
                    try:
                        from PIL import Image as _PILImage

                        with _PILImage.open(rendered) as _img:
                            _iw, _ih = _img.size
                        if _iw > 0 and _ih > 0:
                            aspect_ratio = _ih / _iw
                            scaled_h = target_width_cm * aspect_ratio
                            if scaled_h > max_height_cm:
                                # Height exceeds page — scale by height instead
                                run.add_picture(
                                    rendered, height=Cm(max_height_cm)
                                )
                            else:
                                run.add_picture(
                                    rendered, width=Cm(target_width_cm)
                                )
                        else:
                            run.add_picture(
                                rendered, width=Cm(target_width_cm)
                            )
                    except Exception:
                        # Fallback: width-only (original behaviour)
                        run.add_picture(rendered, width=Cm(target_width_cm))

                    # ── Spacer paragraph AFTER the image ──
                    # Prevents following text from overlapping the image.
                    spacer_after = doc.add_paragraph()
                    spacer_after.paragraph_format.first_line_indent = Pt(0)
                    spacer_after.paragraph_format.space_before = Pt(0)
                    spacer_after.paragraph_format.space_after = Pt(0)
                    spacer_after.paragraph_format.line_spacing = Pt(6)
                else:
                    # Fall back to text tree diagram
                    tree_lines = _mermaid_to_text_diagram(seg_content)
                    for tree_line in tree_lines:
                        body_para = doc.add_paragraph()
                        body_run = body_para.add_run(tree_line)
                        body_run.font.name = "楷体"
                        body_run.font.size = Pt(11)
                        # Set East Asian font
                        rpr = body_run._element.find(qn("w:rPr"))
                        if rpr is None:
                            rpr = body_run._element.makeelement(qn("w:rPr"), {})
                            body_run._element.insert(0, rpr)
                        rFonts = rpr.find(qn("w:rFonts"))
                        if rFonts is None:
                            rFonts = rpr.makeelement(qn("w:rFonts"), {})
                            rpr.append(rFonts)
                        rFonts.set(qn("w:eastAsia"), "楷体")
                        body_para.paragraph_format.first_line_indent = Pt(0)
                continue

            # Normal text segment — process line by line, handling:
            # - Markdown tables (| ... | ... |)
            # - Markdown headings (##, ###, etc.)
            # - Markdown list items (-, *, 1.)
            # - **bold** text and <br> line breaks
            # - Placeholder text 【待填写：...】 (red bold)
            heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
            list_re = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.+)$")

            all_lines = seg_content.split("\n")
            line_idx = 0
            while line_idx < len(all_lines):
                line = all_lines[line_idx].strip()

                # Skip empty lines
                if not line:
                    line_idx += 1
                    continue

                # ── Markdown table block ──
                if _is_markdown_table_line(line):
                    table_lines = [line]
                    line_idx += 1
                    while (
                        line_idx < len(all_lines)
                        and _is_markdown_table_line(all_lines[line_idx].strip())
                    ):
                        table_lines.append(all_lines[line_idx].strip())
                        line_idx += 1

                    # Parse header (first line), skip separator, collect data rows
                    header = _parse_markdown_table_row(table_lines[0])
                    data_rows = []
                    for tl in table_lines[1:]:
                        if _is_separator_line(tl):
                            continue
                        data_rows.append(_parse_markdown_table_row(tl))

                    _add_table_to_docx(
                        doc, header, data_rows, actual_font, qn, Pt,
                        RGBColor, placeholder_re,
                    )
                    continue

                # ── Markdown heading ──
                heading_m = heading_re.match(line)
                if heading_m:
                    level = len(heading_m.group(1))
                    heading_text = heading_m.group(2).strip()
                    _add_markdown_heading(
                        doc, heading_text, level, actual_font, qn,
                        Pt, RGBColor, placeholder_re,
                    )
                    line_idx += 1
                    continue

                # ── Markdown list item ──
                list_m = list_re.match(line)
                if list_m:
                    marker = list_m.group(2)
                    item_text = list_m.group(3).strip()
                    _add_markdown_list_item(
                        doc, item_text, marker, actual_font, qn,
                        Pt, RGBColor, placeholder_re,
                    )
                    line_idx += 1
                    continue

                # ── Normal text paragraph ──
                body_para = doc.add_paragraph()
                _add_runs_with_placeholders(
                    body_para, line, actual_font, qn, Pt,
                    RGBColor, placeholder_re,
                )
                line_idx += 1

    # ── Seal requirements ───────────────────────────────────────────────
    doc.add_page_break()
    seal_para = doc.add_paragraph()
    seal_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    seal_req = format_rules.get("seal_requirement", DEFAULT_FORMAT["seal_requirement"])
    seal_run = seal_para.add_run(f"（{seal_req}）")
    seal_run.font.size = Pt(12)

    # ── Save ────────────────────────────────────────────────────────────
    doc.save(export_path)
    logger.info(f"DOCX exported to {export_path} ({len(sections)} sections)")

    # Clean up temp diagram images
    try:
        shutil.rmtree(_diagram_img_dir, ignore_errors=True)
    except Exception:
        pass

    return export_path


# ── LangGraph Node ─────────────────────────────────────────────────────


def doc_assembler(
    state: AgentState,
    export_dir: str | None = None,
) -> dict:
    """Assemble all sections into a formatted DOCX document.

    Args:
        state: AgentState with sections and format_rules
        export_dir: Optional export directory (defaults to data/exports/)

    Returns:
        {export_path: str, node_status: {...}}
    """
    sections = state.get("sections", {})
    format_rules = state.get("requirements", {}).get("format_rules", DEFAULT_FORMAT)

    if not sections:
        logger.warning("No sections to assemble")
        return {
            "export_path": "",
            "node_status": {
                **state.get("node_status", {}),
                "DocumentAssembler": NodeStatus.FAILED.value,
            },
        }

    # Determine export path
    if export_dir is None:
        project_root = Path(__file__).parent.parent.parent
        export_dir = str(project_root / "data" / "exports")
    Path(export_dir).mkdir(parents=True, exist_ok=True)

    # Filename: {timestamp}_标书_v{round}.docx
    current_round = state.get("current_round", 0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_标书_v{current_round}.docx"
    export_path = str(Path(export_dir) / filename)

    try:
        result_path = _build_docx(sections, format_rules, export_path)
        logger.info(f"DocumentAssembler: exported to {result_path}")

        return {
            "export_path": result_path,
            "node_status": {
                **state.get("node_status", {}),
                "DocumentAssembler": NodeStatus.COMPLETED.value,
            },
        }
    except Exception as e:
        logger.error(f"DocumentAssembler failed: {e}")
        return {
            "export_path": "",
            "node_status": {
                **state.get("node_status", {}),
                "DocumentAssembler": NodeStatus.FAILED.value,
            },
        }
