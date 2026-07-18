"""Batch conversion for the 2025-07-11 Altum folders.

Writes RGB surrogate TIFFs under 07112025 and copies captures that cannot
produce RGB bands into 07112025/could_not_convert.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(r"D:\Play\Combine Multi")
CONVERTER = ROOT / "scripts" / "create_altum_rgb.py"
OUTPUT_ROOT = ROOT / "07112025"
FAILED_ROOT = OUTPUT_ROOT / "could_not_convert"


@dataclass(frozen=True)
class Job:
    name: str
    input_dir: Path
    output_dir: Path
    panel_input_dir: Path | None = None
    panel_stems: tuple[str, ...] = ()
    manual_panel_roi: tuple[int, int, int, int] | None = None


JOBS = (
    Job(
        "000",
        ROOT / "000",
        OUTPUT_ROOT / "000",
        panel_input_dir=ROOT / "001",
        panel_stems=("IMG_0353",),
    ),
    Job(
        "001",
        ROOT / "001",
        OUTPUT_ROOT / "001",
        panel_stems=("IMG_0353", "IMG_0354", "IMG_0355"),
    ),
    Job(
        "flight",
        ROOT / "flight",
        OUTPUT_ROOT / "flight",
        panel_stems=("IMG_0000", "IMG_0001"),
        manual_panel_roi=(1155, 535, 1285, 675),
    ),
)


def visible_band_groups(input_dir: Path) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for path in input_dir.glob("IMG_[0-9][0-9][0-9][0-9]_[1-6].tif"):
        stem, band = path.stem.rsplit("_", 1)
        groups.setdefault(stem, set()).add(band)
    return groups


def copy_failed_group(job: Job, stem: str, reason: str, rows: list[dict[str, str]]) -> None:
    target = FAILED_ROOT / job.name / stem
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(job.input_dir.glob(f"{stem}_*.tif")):
        shutil.copy2(src, target / src.name)
        copied.append(src.name)
    rows.append(
        {
            "source_folder": job.name,
            "stem": stem,
            "reason": reason,
            "copied_files": " ".join(copied),
        }
    )


def record_unconvertible_visible_groups() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for job in JOBS:
        groups = visible_band_groups(job.input_dir)
        for stem in sorted(groups):
            missing_visible = sorted({"1", "2", "3"} - groups[stem])
            if missing_visible:
                copy_failed_group(
                    job,
                    stem,
                    "missing visible band(s): " + ",".join(missing_visible),
                    rows,
                )
    return rows


def write_manifest(rows: list[dict[str, str]]) -> None:
    FAILED_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = FAILED_ROOT / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("source_folder", "stem", "reason", "copied_files"),
        )
        writer.writeheader()
        writer.writerows(rows)


def run_job(job: Job) -> int:
    command = [
        sys.executable,
        str(CONVERTER),
        "--input",
        str(job.input_dir),
        "--output",
        str(job.output_dir),
    ]
    if job.panel_stems:
        if job.panel_input_dir:
            command.extend(["--panel-input", str(job.panel_input_dir)])
        command.extend(["--panel-stems", *job.panel_stems])
    if job.manual_panel_roi:
        command.extend(["--manual-panel-roi", *(str(v) for v in job.manual_panel_roi)])

    print(f"Running {job.name}: {' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        error_dir = FAILED_ROOT / job.name
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / "_conversion_error.txt").write_text(
            f"Conversion command failed with exit code {result.returncode}.\n"
            f"Command: {' '.join(command)}\n",
            encoding="utf-8",
        )
    return result.returncode


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    failed_rows = record_unconvertible_visible_groups()
    write_manifest(failed_rows)

    return_code = 0
    for job in JOBS:
        if not job.input_dir.exists():
            copy_failed_group(job, "ALL", f"missing input folder: {job.input_dir}", failed_rows)
            write_manifest(failed_rows)
            return_code = 1
            continue
        return_code = max(return_code, run_job(job))

    write_manifest(failed_rows)
    print(f"Unconvertible visible groups recorded: {len(failed_rows)}", flush=True)
    print(f"Output root: {OUTPUT_ROOT}", flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
