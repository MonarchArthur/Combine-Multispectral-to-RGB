"""Create native-resolution RGB surrogates from MicaSense Altum TIFF bands.

This script expects single-band Altum files named like:
    IMG_0000_1.tif ... IMG_0000_6.tif

Band order from Altum metadata:
    1 = Blue, 2 = Green, 3 = Red

The output is an RGB surrogate for software that requires RGB input. It is not
true sRGB camera color and is not certified absolute reflectance unless you
also have official panel reflectance values and adapt the calibration step.
"""

from __future__ import annotations

import argparse
import gc
import re
from pathlib import Path

import cv2
import numpy as np
import tifffile
from PIL import Image, ImageDraw


RGB_CHANNELS = {"R": "3", "G": "2", "B": "1"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create RGB TIFFs from MicaSense Altum visible bands."
    )
    parser.add_argument("--input", required=True, type=Path, help="Folder with Altum TIFFs.")
    parser.add_argument("--output", required=True, type=Path, help="Output folder.")
    parser.add_argument(
        "--panel-stems",
        nargs="*",
        default=[],
        help="Panel image stems, e.g. IMG_0000 IMG_0001. Optional.",
    )
    parser.add_argument(
        "--manual-panel-roi",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Manual panel ROI used when ReflectArea metadata is missing.",
    )
    parser.add_argument(
        "--exclude-stems",
        nargs="*",
        default=[],
        help="Image stems to exclude from output, usually panel frames.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Also write 8-bit PNG previews next to the 16-bit TIFF outputs.",
    )
    return parser.parse_args()


def grab_xmp(xmp: str, name: str, default: str = "") -> str:
    match = re.search(fr"<Camera:{name}>(.*?)</Camera:{name}>", xmp, flags=re.S)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else default


def grab_seq(xmp: str, name: str) -> list[float] | None:
    match = re.search(
        fr"<Camera:{name}>\s*<rdf:Seq>(.*?)</rdf:Seq>\s*</Camera:{name}>",
        xmp,
        flags=re.S,
    )
    if not match:
        return None
    values: list[float] = []
    for item in re.findall(r"<rdf:li>(.*?)</rdf:li>", match.group(1), flags=re.S):
        values.extend(
            float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", item, flags=re.I)
        )
    return values


class AltumProcessor:
    def __init__(self, input_dir: Path):
        self.input_dir = input_dir
        self._meta_cache: dict[tuple[str, str], dict] = {}
        self._vignette_cache: dict[tuple, np.ndarray] = {}

    def metadata(self, stem: str, channel: str) -> dict:
        key = (stem, channel)
        if key in self._meta_cache:
            return self._meta_cache[key]

        path = self.input_dir / f"{stem}_{channel}.tif"
        with tifffile.TiffFile(path) as tiff:
            tags = tiff.pages[0].tags
            meta = {
                "black": 0.0,
                "exposure": 1.0,
                "gain": 1.0,
                "irradiance": 1.0,
                "vcenter": None,
                "vpoly": None,
                "reflect_area": None,
                "band": "",
                "wavelength": "",
                "calibration_picture": "",
            }

            if 50714 in tags:
                meta["black"] = float(np.mean(tags[50714].value))

            if 34665 in tags:
                exif = tags[34665].value
                if "ExposureTime" in exif:
                    meta["exposure"] = exif["ExposureTime"][0] / exif["ExposureTime"][1]
                if "ISOSpeed" in exif:
                    meta["gain"] = float(exif["ISOSpeed"]) / 100.0

            if 700 in tags:
                xmp = tags[700].value.decode("utf-8", errors="replace")
                meta["band"] = grab_xmp(xmp, "BandName")
                meta["wavelength"] = grab_xmp(xmp, "CentralWavelength")
                meta["calibration_picture"] = grab_xmp(xmp, "CalibrationPicture")
                try:
                    meta["irradiance"] = float(grab_xmp(xmp, "Irradiance", "1"))
                except ValueError:
                    meta["irradiance"] = 1.0
                meta["vcenter"] = grab_seq(xmp, "VignettingCenter")
                meta["vpoly"] = grab_seq(xmp, "VignettingPolynomial")
                reflect_area = grab_seq(xmp, "ReflectArea")
                if reflect_area and len(reflect_area) >= 8:
                    meta["reflect_area"] = [
                        (int(reflect_area[i]), int(reflect_area[i + 1])) for i in range(0, 8, 2)
                    ]

        self._meta_cache[key] = meta
        return meta

    def vignette_map(self, shape: tuple[int, int], meta: dict) -> np.ndarray:
        key = (
            shape,
            tuple(meta["vcenter"]) if meta["vcenter"] else None,
            tuple(meta["vpoly"]) if meta["vpoly"] else None,
        )
        if key in self._vignette_cache:
            return self._vignette_cache[key]

        if not meta["vcenter"] or not meta["vpoly"]:
            correction = np.ones(shape, dtype=np.float32)
        else:
            cx, cy = meta["vcenter"][:2]
            yy, xx = np.indices(shape, dtype=np.float32)
            radius = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
            denom = np.ones(shape, dtype=np.float32)
            power = radius.copy()
            for coeff in meta["vpoly"]:
                denom += np.float32(coeff) * power
                power *= radius
            correction = (1.0 / np.maximum(denom, 1e-6)).astype(np.float32)

        self._vignette_cache[key] = correction
        return correction

    def corrected_signal(self, stem: str, channel: str) -> tuple[np.ndarray, dict]:
        meta = self.metadata(stem, channel)
        arr = tifffile.imread(self.input_dir / f"{stem}_{channel}.tif").astype(np.float32)
        arr -= meta["black"]
        np.maximum(arr, 0, out=arr)
        arr *= self.vignette_map(arr.shape, meta)
        arr /= max(meta["exposure"] * meta["gain"], 1e-9)
        return arr, meta


def complete_visible_stems(input_dir: Path) -> list[str]:
    groups: dict[str, set[str]] = {}
    for path in input_dir.glob("*.tif"):
        match = re.match(r"^(IMG_\d{4})_([1-6])\.tif$", path.name, flags=re.I)
        if match:
            groups.setdefault(match.group(1), set()).add(match.group(2))
    return [stem for stem in sorted(groups) if {"1", "2", "3"}.issubset(groups[stem])]


def polygon_mask(shape: tuple[int, int], points: list[tuple[int, int]]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 1)
    return mask.astype(bool)


def estimate_panel_scale(
    processor: AltumProcessor,
    panel_stems: list[str],
    manual_roi: tuple[int, int, int, int] | None,
) -> tuple[dict[str, float], list[str]]:
    """Estimate channel scales so the panel target is neutral in R/G/B.

    If panel reflectance metadata/certificate values are unavailable, this is a
    relative normalization, not absolute reflectance calibration.
    """
    if not panel_stems:
        return {"1": 1.0, "2": 1.0, "3": 1.0}, ["No panel stems supplied; no panel normalization applied."]

    rows: list[str] = []
    scales: dict[str, float] = {}
    for channel in ("1", "2", "3"):
        channel_values = []
        for stem in panel_stems:
            arr, meta = processor.corrected_signal(stem, channel)
            if meta["reflect_area"]:
                values = arr[polygon_mask(arr.shape, meta["reflect_area"])]
                roi_note = f"ReflectArea={meta['reflect_area']}"
            elif manual_roi:
                left, top, right, bottom = manual_roi
                values = arr[top:bottom, left:right]
                roi_note = f"manual_roi={manual_roi}"
            else:
                raise RuntimeError(
                    f"{stem}_{channel} has no ReflectArea metadata; pass --manual-panel-roi."
                )

            values = values[np.isfinite(values) & (values > 0)]
            if values.size == 0:
                raise RuntimeError(f"Panel ROI for {stem}_{channel} has no valid pixels.")

            median_signal = float(np.median(values))
            signal_per_irradiance = median_signal / max(meta["irradiance"], 1e-9)
            channel_values.append(signal_per_irradiance)
            rows.append(
                f"{stem}_{channel} {meta['band']}: {roi_note}, "
                f"median_signal={median_signal:.8g}, irradiance={meta['irradiance']:.8g}, "
                f"signal_per_irradiance={signal_per_irradiance:.8g}"
            )
            del arr, values
            gc.collect()

        scales[channel] = float(np.median(channel_values))
    return scales, rows


def normalized_band(processor: AltumProcessor, stem: str, channel: str, panel_scale: dict[str, float]):
    arr, meta = processor.corrected_signal(stem, channel)
    arr = arr / max(meta["irradiance"], 1e-9) / max(panel_scale[channel], 1e-9)
    return arr


def display_norm(arr: np.ndarray) -> np.ndarray:
    finite = arr[np.isfinite(arr)]
    lo, hi = np.percentile(finite, (1, 99))
    if hi <= lo:
        hi = lo + 1
    return np.clip((arr - lo) / (hi - lo), 0, 1).astype(np.float32)


def estimate_warp(source: np.ndarray, target: np.ndarray):
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
    try:
        cc, warp = cv2.findTransformECC(
            display_norm(target),
            display_norm(source),
            warp,
            cv2.MOTION_AFFINE,
            criteria,
            None,
            5,
        )
        return warp, cc
    except cv2.error:
        return np.eye(2, 3, dtype=np.float32), None


def warp_apply(arr: np.ndarray, warp: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    return cv2.warpAffine(
        arr,
        warp,
        (shape[1], shape[0]),
        flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def to_uint16(arr: np.ndarray, high: float) -> np.ndarray:
    return (np.clip(arr / high, 0, 1) * 65535 + 0.5).astype(np.uint16)


def preview_from_uint16(rgb16: np.ndarray) -> np.ndarray:
    x = rgb16.astype(np.float32) / 65535.0
    x = np.power(np.clip(x, 0, 1), 1 / 1.6)
    return (x * 255 + 0.5).astype(np.uint8)


def save_panel_check(
    processor: AltumProcessor,
    panel_stem: str,
    manual_roi: tuple[int, int, int, int] | None,
    output_dir: Path,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    channels = []
    for channel in ("3", "2", "1"):
        arr, _ = processor.corrected_signal(panel_stem, channel)
        lo, hi = np.percentile(arr, (0.5, 99.5))
        if hi <= lo:
            hi = lo + 1
        channels.append((np.clip((arr - lo) / (hi - lo), 0, 1) * 255 + 0.5).astype(np.uint8))
    image = Image.fromarray(np.dstack(channels), "RGB")
    draw = ImageDraw.Draw(image)
    if manual_roi:
        draw.rectangle(manual_roi, outline=(255, 0, 0), width=5)
    image.save(output_dir / f"{panel_stem}_panel_roi_check.png")


def main() -> None:
    args = parse_args()
    input_dir = args.input
    output_dir = args.output
    tif_dir = output_dir / "tif_16bit_rgb"
    png_dir = output_dir / "png_preview"
    tif_dir.mkdir(parents=True, exist_ok=True)
    if args.preview:
        png_dir.mkdir(parents=True, exist_ok=True)

    manual_roi = tuple(args.manual_panel_roi) if args.manual_panel_roi else None
    processor = AltumProcessor(input_dir)

    stems = complete_visible_stems(input_dir)
    exclude = set(args.exclude_stems) | set(args.panel_stems)
    output_stems = [stem for stem in stems if stem not in exclude]
    if not output_stems:
        raise RuntimeError("No complete visible-band captures to process.")

    panel_scale, panel_rows = estimate_panel_scale(processor, args.panel_stems, manual_roi)
    if args.panel_stems:
        save_panel_check(processor, args.panel_stems[0], manual_roi, output_dir / "panel_checks")

    # Estimate fixed red/blue -> green alignment from representative output frames.
    sample_indices = np.linspace(0, len(output_stems) - 1, min(6, len(output_stems))).round().astype(int)
    sample_stems = [output_stems[int(i)] for i in sample_indices]
    r_warps = []
    b_warps = []
    align_log = []
    for stem in sample_stems:
        red = normalized_band(processor, stem, "3", panel_scale)
        green = normalized_band(processor, stem, "2", panel_scale)
        blue = normalized_band(processor, stem, "1", panel_scale)
        rw, rcc = estimate_warp(red, green)
        bw, bcc = estimate_warp(blue, green)
        if rcc is not None:
            r_warps.append(rw)
        if bcc is not None:
            b_warps.append(bw)
        align_log.append(f"{stem}: red_to_green_cc={rcc}, blue_to_green_cc={bcc}")
        del red, green, blue
        gc.collect()

    red_warp = np.median(np.stack(r_warps), axis=0).astype(np.float32) if r_warps else np.eye(2, 3, dtype=np.float32)
    blue_warp = np.median(np.stack(b_warps), axis=0).astype(np.float32) if b_warps else np.eye(2, 3, dtype=np.float32)

    # One fixed global scale for all outputs in this run.
    samples = []
    for stem in output_stems:
        red = normalized_band(processor, stem, "3", panel_scale)
        green = normalized_band(processor, stem, "2", panel_scale)
        blue = normalized_band(processor, stem, "1", panel_scale)
        red = warp_apply(red, red_warp, green.shape)
        blue = warp_apply(blue, blue_warp, green.shape)
        for band in (red, green, blue):
            valid = band[np.isfinite(band) & (band > 0)]
            if valid.size:
                samples.append(valid.ravel()[::1000].astype(np.float32, copy=True))
        del red, green, blue
        gc.collect()

    scale_sample = np.concatenate(samples) if samples else np.array([1.0], dtype=np.float32)
    global_high = float(np.percentile(scale_sample, 99.8))
    if global_high <= 0:
        global_high = 1.0

    for idx, stem in enumerate(output_stems, start=1):
        red = normalized_band(processor, stem, "3", panel_scale)
        green = normalized_band(processor, stem, "2", panel_scale)
        blue = normalized_band(processor, stem, "1", panel_scale)
        red = warp_apply(red, red_warp, green.shape)
        blue = warp_apply(blue, blue_warp, green.shape)
        rgb16 = np.dstack([to_uint16(red, global_high), to_uint16(green, global_high), to_uint16(blue, global_high)])

        description = (
            "MicaSense Altum RGB surrogate. R=channel3, G=channel2, B=channel1. "
            "Black/vignette/exposure/gain/DLS irradiance corrected, panel-normalized if panel was supplied, "
            "and red/blue aligned to green. Not true sRGB and not absolute reflectance without official panel values."
        )
        tifffile.imwrite(
            tif_dir / f"{stem}_RGB.tif",
            rgb16,
            photometric="rgb",
            compression="deflate",
            metadata=None,
            description=description,
        )
        if args.preview:
            Image.fromarray(preview_from_uint16(rgb16), "RGB").save(png_dir / f"{stem}_RGB_preview.png", optimize=True)
        del red, green, blue, rgb16
        gc.collect()
        if idx % 25 == 0 or idx == len(output_stems):
            print(f"Wrote {idx}/{len(output_stems)}")

    readme = f"""Altum RGB processing run

Input:
  {input_dir}

Outputs:
  16-bit RGB TIFFs: {tif_dir}
  8-bit previews: {png_dir if args.preview else 'not requested'}

Band order:
  R = channel 3
  G = channel 2
  B = channel 1

Panel stems:
  {', '.join(args.panel_stems) if args.panel_stems else 'none'}

Manual panel ROI:
  {manual_roi if manual_roi else 'none'}

Excluded stems:
  {', '.join(sorted(exclude)) if exclude else 'none'}

Output stems:
  {', '.join(output_stems)}

Processing:
  1. BlackLevel subtraction.
  2. VignettingPolynomial correction.
  3. Exposure time and ISO gain correction.
  4. XMP/DLS irradiance normalization.
  5. Panel ROI normalization if panel stems were supplied.
  6. Red and blue aligned to green using affine ECC alignment.
  7. One fixed global scale for this run: 99.8 percentile {global_high:.8g} -> 65535.

Limitations:
  - This is a multispectral-derived RGB surrogate, not a normal sRGB camera photo.
  - Without official per-band panel reflectance/albedo values, panel normalization is relative.
  - Edges can show color fringes after alignment; crop borders if your software is sensitive to this.

Panel measurements:
{chr(10).join(panel_rows)}

Alignment log:
{chr(10).join(align_log)}

Red-to-green affine warp:
{red_warp}

Blue-to-green affine warp:
{blue_warp}
"""
    (output_dir / "README.txt").write_text(readme, encoding="utf-8")
    print(f"Done: {len(output_stems)} RGB images written to {output_dir}")


if __name__ == "__main__":
    main()
