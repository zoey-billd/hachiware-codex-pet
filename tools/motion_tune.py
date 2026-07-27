#!/usr/bin/env python3
"""Tune motion cadence for the Hachiware Codex pet atlas.

This script keeps the original drawings but adjusts frame registration and a
few row-specific frame choices so the in-app loops feel less jumpy.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


COLUMNS = 8
ROWS = 9
CELL_WIDTH = 192
CELL_HEIGHT = 208

ROW_SPECS = [
    ("idle", 0, 6),
    ("running-right", 1, 8),
    ("running-left", 2, 8),
    ("waving", 3, 4),
    ("jumping", 4, 5),
    ("failed", 5, 8),
    ("waiting", 6, 6),
    ("running", 7, 6),
    ("review", 8, 6),
]

ROW_DURATIONS = {
    "idle": [280, 110, 110, 140, 140, 320],
    "running-right": [120, 120, 120, 120, 120, 120, 120, 220],
    "running-left": [120, 120, 120, 120, 120, 120, 120, 220],
    "waving": [140, 140, 140, 280],
    "jumping": [140, 140, 140, 140, 280],
    "failed": [140, 140, 140, 140, 140, 140, 140, 240],
    "waiting": [150, 150, 150, 150, 150, 260],
    "running": [120, 120, 120, 120, 120, 220],
    "review": [150, 150, 150, 150, 150, 280],
}


@dataclass(frozen=True)
class FrameMetrics:
    bbox: tuple[int, int, int, int] | None
    alpha_pixels: int
    center_x: float | None
    center_y: float | None
    width: int
    height: int


def clear_transparent_rgb(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    data = bytearray(rgba.tobytes())
    for index in range(0, len(data), 4):
        if data[index + 3] == 0:
            data[index] = 0
            data[index + 1] = 0
            data[index + 2] = 0
    return Image.frombytes("RGBA", rgba.size, bytes(data))


def load_atlas(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        atlas = opened.convert("RGBA")
    if atlas.size != (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT):
        raise SystemExit(f"expected 1536x1872 atlas, got {atlas.size}")
    return atlas


def split_frames(atlas: Image.Image) -> dict[str, list[Image.Image]]:
    rows: dict[str, list[Image.Image]] = {}
    for state, row_index, frame_count in ROW_SPECS:
        frames = []
        for column in range(frame_count):
            left = column * CELL_WIDTH
            top = row_index * CELL_HEIGHT
            frames.append(atlas.crop((left, top, left + CELL_WIDTH, top + CELL_HEIGHT)))
        rows[state] = frames
    return rows


def alpha_bbox(frame: Image.Image) -> tuple[int, int, int, int] | None:
    return frame.getchannel("A").getbbox()


def frame_metrics(frame: Image.Image) -> FrameMetrics:
    bbox = alpha_bbox(frame)
    alpha_pixels = sum(frame.getchannel("A").histogram()[1:])
    if bbox is None:
        return FrameMetrics(None, 0, None, None, 0, 0)
    left, top, right, bottom = bbox
    return FrameMetrics(
        bbox=bbox,
        alpha_pixels=alpha_pixels,
        center_x=(left + right - 1) / 2,
        center_y=(top + bottom - 1) / 2,
        width=right - left,
        height=bottom - top,
    )


def normalize_to_anchor(
    frame: Image.Image,
    *,
    target_center_x: float,
    target_bottom: int,
    target_height: int | None = None,
    max_scale_delta: float = 0.08,
    extra_y: int = 0,
) -> Image.Image:
    bbox = alpha_bbox(frame)
    if bbox is None:
        return frame.copy()

    left, top, right, bottom = bbox
    source = frame.crop(bbox)
    width = right - left
    height = bottom - top
    scale = 1.0
    if target_height is not None and height > 0:
        scale = target_height / height
        scale = max(1.0 - max_scale_delta, min(1.0 + max_scale_delta, scale))
        scale = min(scale, (CELL_WIDTH - 4) / width, (CELL_HEIGHT - 4) / height)
        if abs(scale - 1.0) > 0.005:
            new_size = (
                max(1, round(width * scale)),
                max(1, round(height * scale)),
            )
            source = source.resize(new_size, Image.Resampling.LANCZOS)

    width, height = source.size
    dx = round(target_center_x - width / 2)
    dy = round((target_bottom + extra_y) - height)
    dx = max(2, min(CELL_WIDTH - width - 2, dx))
    dy = max(2, min(CELL_HEIGHT - height - 2, dy))
    result = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
    result.alpha_composite(source, (dx, dy))
    return clear_transparent_rgb(result)


def compose_atlas(rows: dict[str, list[Image.Image]]) -> Image.Image:
    atlas = Image.new("RGBA", (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT), (0, 0, 0, 0))
    for state, row_index, frame_count in ROW_SPECS:
        for column, frame in enumerate(rows[state][:frame_count]):
            atlas.alpha_composite(clear_transparent_rgb(frame), (column * CELL_WIDTH, row_index * CELL_HEIGHT))
    return clear_transparent_rgb(atlas)


def save_frames(rows: dict[str, list[Image.Image]], output_root: Path) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    for state, frames in rows.items():
        state_dir = output_root / state
        state_dir.mkdir(parents=True, exist_ok=True)
        for index, frame in enumerate(frames):
            clear_transparent_rgb(frame).save(state_dir / f"{index:02d}.png")


def save_previews(rows: dict[str, list[Image.Image]], output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for state, durations in ROW_DURATIONS.items():
        frames = [clear_transparent_rgb(frame) for frame in rows[state]]
        frames[0].save(
            output_dir / f"{state}.gif",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
            optimize=False,
        )


def diagnostics(rows: dict[str, list[Image.Image]]) -> list[dict[str, object]]:
    result = []
    for state, frames in rows.items():
        metrics = [frame_metrics(frame) for frame in frames]
        jumps = []
        for index in range(len(metrics)):
            current = metrics[index]
            nxt = metrics[(index + 1) % len(metrics)]
            if current.bbox is None or nxt.bbox is None:
                jumps.append({"from": index, "to": (index + 1) % len(metrics), "center_delta": None})
                continue
            jumps.append(
                {
                    "from": index,
                    "to": (index + 1) % len(metrics),
                    "center_delta": round(
                        abs((nxt.center_x or 0) - (current.center_x or 0))
                        + abs((nxt.center_y or 0) - (current.center_y or 0)),
                        2,
                    ),
                    "height_delta": abs(nxt.height - current.height),
                    "area_delta": abs(nxt.alpha_pixels - current.alpha_pixels),
                }
            )
        result.append(
            {
                "state": state,
                "frames": [
                    {
                        "index": index,
                        "bbox": metric.bbox,
                        "center": [metric.center_x, metric.center_y],
                        "height": metric.height,
                        "alpha_pixels": metric.alpha_pixels,
                    }
                    for index, metric in enumerate(metrics)
                ],
                "jumps": jumps,
                "max_center_delta": max(
                    (jump["center_delta"] or 0 for jump in jumps),
                    default=0,
                ),
                "max_height_delta": max((jump["height_delta"] for jump in jumps if "height_delta" in jump), default=0),
                "max_area_delta": max((jump["area_delta"] for jump in jumps if "area_delta" in jump), default=0),
            }
        )
    return result


def tune(rows: dict[str, list[Image.Image]]) -> tuple[dict[str, list[Image.Image]], dict[str, object]]:
    tuned = {state: [frame.copy() for frame in frames] for state, frames in rows.items()}
    notes: dict[str, object] = {}

    for state in ("running-right", "running-left"):
        frames = tuned[state]
        target_center_x = 96.0
        target_bottom = 203
        target_height = 190
        bounce = [0, 0, -1, -2, 0, -1, 0, 0]
        tuned[state] = [
            normalize_to_anchor(
                frame,
                target_center_x=target_center_x,
                target_bottom=target_bottom,
                target_height=target_height,
                max_scale_delta=0.04,
                extra_y=bounce[index],
            )
            for index, frame in enumerate(frames)
        ]
        notes[state] = "Normalized gait frames to a shared center, height, and near-stable baseline with a tiny bounce."

    jumping_source = tuned["jumping"]
    jumping_order = [0, 1, 2, 3, 4]
    jumping_offsets = [0, -4, -8, -4, 0]
    tuned["jumping"] = [
        normalize_to_anchor(
            jumping_source[source_index],
            target_center_x=96.0,
            target_bottom=203,
            target_height=180,
            max_scale_delta=0.03,
            extra_y=jumping_offsets[target_index],
        )
        for target_index, source_index in enumerate(jumping_order)
    ]
    notes["jumping"] = "Re-registered jump frames into a clearer down-up-down arc instead of a scale pop."

    failed_source = tuned["failed"]
    failed_order = [0, 1, 4, 3, 6, 3, 4, 1]
    tuned["failed"] = [
        normalize_to_anchor(
            failed_source[source_index],
            target_center_x=96.0,
            target_bottom=203,
            target_height=196,
            max_scale_delta=0.06,
        )
        for source_index in failed_order
    ]
    notes["failed"] = (
        "Removed the abrupt standing-to-lying cuts from the loop; kept a sad standing/crouching failure cycle."
    )

    review_source = tuned["review"]
    review_order = [0, 1, 3, 5, 2, 1]
    tuned["review"] = [
        normalize_to_anchor(
            review_source[source_index],
            target_center_x=96.0,
            target_bottom=203,
            target_height=196,
            max_scale_delta=0.05,
        )
        for source_index in review_order
    ]
    notes["review"] = "Reordered review into a smaller focus/tilt cycle and normalized registration."

    waiting_source = tuned["waiting"]
    waiting_order = [2, 5, 3, 1, 0, 4]
    tuned["waiting"] = [
        normalize_to_anchor(
            waiting_source[source_index],
            target_center_x=96.0,
            target_bottom=203,
            target_height=196,
            max_scale_delta=0.04,
        )
        for source_index in waiting_order
    ]
    notes["waiting"] = "Reordered waiting from neutral to expectant poses, ending on the worried hold."

    return tuned, notes


def make_contact_sheet(rows: dict[str, list[Image.Image]], output: Path) -> None:
    scale = 1
    label_h = 28
    width = COLUMNS * CELL_WIDTH * scale
    height = ROWS * (CELL_HEIGHT * scale + label_h)
    sheet = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    from PIL import ImageDraw

    draw = ImageDraw.Draw(sheet)
    for state, row_index, frame_count in ROW_SPECS:
        y = row_index * (CELL_HEIGHT * scale + label_h)
        draw.text((4, y + 6), f"row {row_index}: {state} ({frame_count} frames)", fill=(0, 0, 0, 255))
        for column in range(COLUMNS):
            x = column * CELL_WIDTH * scale
            cell_y = y + label_h
            fill = (238, 244, 238, 255) if column < frame_count else (245, 245, 245, 255)
            draw.rectangle((x, cell_y, x + CELL_WIDTH - 1, cell_y + CELL_HEIGHT - 1), fill=fill, outline=(40, 120, 70, 255))
            if column < frame_count:
                sheet.alpha_composite(rows[state][column], (x, cell_y))
                draw.text((x + 4, cell_y + 4), str(column), fill=(0, 0, 0, 255))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frames-root", required=True)
    parser.add_argument("--previews-dir", required=True)
    parser.add_argument("--contact-sheet", required=True)
    parser.add_argument("--diagnostics-out", required=True)
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    frames_root = Path(args.frames_root).expanduser().resolve()
    previews_dir = Path(args.previews_dir).expanduser().resolve()
    contact_sheet = Path(args.contact_sheet).expanduser().resolve()
    diagnostics_out = Path(args.diagnostics_out).expanduser().resolve()

    original_rows = split_frames(load_atlas(input_path))
    tuned_rows, notes = tune(original_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    compose_atlas(tuned_rows).save(output_path, format="WEBP", lossless=True, quality=100, method=6, exact=True)
    save_frames(tuned_rows, frames_root)
    save_previews(tuned_rows, previews_dir)
    make_contact_sheet(tuned_rows, contact_sheet)

    result = {
        "ok": True,
        "input": str(input_path),
        "output": str(output_path),
        "notes": notes,
        "before": diagnostics(original_rows),
        "after": diagnostics(tuned_rows),
    }
    diagnostics_out.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output_path), "diagnostics": str(diagnostics_out)}, indent=2))


if __name__ == "__main__":
    main()
