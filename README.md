# Combine Multi

Create RGB images from single-band MicaSense Altum multispectral TIFFs.
This project is for workflows where Plot Phenix expects normal-ish RGB images, but the source data comes from a MicaSense Altum camera as separate band files. The converter uses the Altum visible bands:

- Red: band 3
- Green: band 2
- Blue: band 1

The output is a three-channel RGB TIFF that is easier to inspect, align, or import into RGB-only tools. It is still derived from multispectral data, so it should not be treated as a true sRGB camera photo or certified absolute reflectance product.

## What The Script Does

`scripts/create_altum_rgb.py` processes complete Altum captures named like this:

```text
IMG_0000_1.tif
IMG_0000_2.tif
IMG_0000_3.tif
IMG_0000_4.tif
IMG_0000_5.tif
IMG_0000_6.tif
```

For each image stem, such as `IMG_0000`, the script looks for the visible bands `1`, `2`, and `3`. It then:

1. Reads image metadata from TIFF, EXIF, and XMP tags.
2. Subtracts the sensor black level.
3. Applies vignetting correction when vignetting metadata exists.
4. Corrects for exposure time and ISO gain.
5. Normalizes with DLS irradiance metadata.
6. Optionally normalizes visible bands against reflectance panel frames.
7. Aligns red and blue bands to the green band using OpenCV ECC affine alignment.
8. Crops the aligned image to the shared area where all three RGB bands overlap.
9. Writes 16-bit RGB TIFF files.
10. Optionally writes 8-bit PNG previews.
11. Then crops image bounds that don't contain all three bands. (this happens because of slight offset in sensors)

## Repository Layout

```text
.
|-- scripts/
|   `-- create_altum_rgb.py       Main converter
|-- 000/                          Example/input Altum capture folder
|-- 001/                          Example/input Altum capture folder
|-- flight/                       Example/input Altum capture folder
|-- Converted/                    Suggested parent folder for new outputs
|-- copy_*.log                    Robocopy logs from copying source data
`-- README.md                     This guide
```

Large input and output folders are ignored by Git, so this repository is mainly the processing script and documentation. Keep raw flight folders beside `scripts/` when using the example paths below. Historical output folders may also be present locally if previous conversions were kept.

## Requirements

Use Python 3.10 or newer. The script depends on:

- `numpy`
- `opencv-python`
- `tifffile`
- `Pillow`

The script also imports standard-library modules including `argparse`, `gc`, `re`, and `pathlib`; those do not need to be installed separately.

One clean Conda setup is:

```powershell
conda create -n altum-rgb python=3.11
conda activate altum-rgb
pip install numpy opencv-python tifffile pillow
```

## Input Data

Put one Altum flight folder somewhere under the project folder. The converter expects one TIFF per band and uses this naming pattern:

```text
IMG_####_BAND.tif
```

Examples:

```text
flight/
|-- IMG_0000_1.tif
|-- IMG_0000_2.tif
|-- IMG_0000_3.tif
|-- IMG_0000_4.tif
|-- IMG_0000_5.tif
|-- IMG_0000_6.tif
|-- IMG_0001_1.tif
|-- IMG_0001_2.tif
`-- IMG_0001_3.tif
```

Only captures with bands `1`, `2`, and `3` are converted. Bands `4`, `5`, and `6` may be present, but they are not used for the RGB output.

## Basic Usage

Run the converter from PowerShell:

```powershell
& 'D:\Conda\envs\ML\python.exe' 'D:\Play\Combine Multi\scripts\create_altum_rgb.py' `
  --input 'D:\Play\Combine Multi\flight' `
  --output 'D:\Play\Combine Multi\Converted\flight_rgb' `
  --preview
```

This creates:

```text
Converted/flight_rgb/
|-- tif_16bit_rgb/
|   `-- IMG_####_RGB.tif
|-- png_preview/
|   `-- IMG_####_RGB_preview.png
`-- README.txt
```

Omit `--preview` if you only want the 16-bit TIFF outputs.

## Panel Calibration

Panel normalization is optional. When you provide panel stems, the script measures the panel in bands `1`, `2`, and `3`, then uses the median panel signal to normalize the output channels relative to each other. However, its better to just include the panel calibration frames if present. The median panel signal doens't work very well.

Use panel calibration frames from the same input folder:

```powershell
& 'D:\Conda\envs\ML\python.exe' 'D:\Play\Combine Multi\scripts\create_altum_rgb.py' `
  --input 'D:\Play\Combine Multi\flight' `
  --output 'D:\Play\Combine Multi\Converted\flight_panel_normalized' `
  --panel-stems IMG_0000 IMG_0001 `
  --exclude-stems IMG_0000 IMG_0001 `
  --preview
```

Use panel frames from a different folder:

```powershell
& 'D:\Conda\envs\ML\python.exe' 'D:\Play\Combine Multi\scripts\create_altum_rgb.py' `
  --input 'D:\Play\Combine Multi\000' `
  --panel-input 'D:\Play\Combine Multi\001' `
  --output 'D:\Play\Combine Multi\Converted\000_using_001_panel' `
  --panel-stems IMG_0353 `
  --preview
```

### Manual Panel ROI

Some panel TIFFs do not include usable `ReflectArea` metadata. In that case, pass a manual rectangular region of interest:

```powershell
& 'D:\Conda\envs\ML\python.exe' 'D:\Play\Combine Multi\scripts\create_altum_rgb.py' `
  --input 'D:\Play\Combine Multi\flight' `
  --output 'D:\Play\Combine Multi\Converted\flight_panel_normalized' `
  --panel-stems IMG_0000 IMG_0001 `
  --manual-panel-roi 1155 535 1285 675 `
  --exclude-stems IMG_0000 IMG_0001 `
  --preview
```

The ROI values are:

```text
LEFT TOP RIGHT BOTTOM
```

When panel stems are supplied, the script writes a panel check image under:

```text
<output>/panel_checks/
```

Open that image and confirm the rectangle is covering only the reflectance panel, not shadows, frame edges, labels, soil, vegetation, or sky. The ROI is usually includeded in the metadata. Before a drone flight the program ususally forces you to calibrate reflectance and determines the ROI.

## Command-Line Options

```text
--input PATH
    Folder containing Altum TIFF files.

--output PATH
    Folder where converted RGB files and run notes will be written.

--panel-input PATH
    Optional folder containing panel TIFFs when panel frames are outside --input.

--panel-stems IMG_0000 IMG_0001
    Optional panel capture stems. These are also excluded from output.

--manual-panel-roi LEFT TOP RIGHT BOTTOM
    Optional rectangular panel ROI, used when ReflectArea metadata is missing.

--exclude-stems IMG_0000 IMG_0001
    Extra image stems to skip. Use this for panel frames, test shots, or bad captures.

--preview
    Also write 8-bit PNG previews.
```

## Output Details

The main output files are written to:

```text
<output>/tif_16bit_rgb/IMG_####_RGB.tif
```

These are 16-bit, three-channel TIFFs with:

- channel 1: red from Altum band 3
- channel 2: green from Altum band 2
- channel 3: blue from Altum band 1
- Deflate compression
- A TIFF description noting the processing assumptions

If `--preview` is used, preview PNGs are written to:

```text
<output>/png_preview/IMG_####_RGB_preview.png
```

The previews are 8-bit images with display normalization and a simple gamma adjustment. Use them for quick visual review, not quantitative analysis.

Each run also writes:

```text
<output>/README.txt
```

That file records:

- input and output paths
- panel input path
- selected panel stems
- manual ROI, if used
- excluded stems
- converted stems
- processing steps
- crop rectangle and output size
- global scaling percentile
- panel measurements
- red-to-green and blue-to-green alignment matrices

## Cropping Edge Pixels

After red and blue are aligned to the green band, the outside edges of an RGB image may contain pixels where one or more bands are missing. These areas often appear as black borders, colored fringes, or edge strips that do not contain valid data from all three visible bands.

The script automatically crops those edges before saving the TIFFs and PNG previews. It computes one shared crop from the red-to-green and blue-to-green alignment transforms, then applies that same crop to every output image so the whole dataset keeps matching dimensions and each saved RGB pixel has all three visible bands.

## Suggested Workflow

1. Copy the raw Altum folder into this project.
2. Identify panel frames by opening candidate RGB previews or individual bands.
3. If the panel metadata includes `ReflectArea`, use `--panel-stems`.
4. If `ReflectArea` is missing or wrong, choose a clean rectangle and use `--manual-panel-roi`.
5. Add panel frames to `--exclude-stems` so they are not converted as flight imagery.
6. Run with `--preview` the first time.
7. Inspect `<output>/panel_checks/` and `<output>/png_preview/`.
8. Re-run with adjusted panel stems or ROI if the panel check image looks wrong.
9. Check the generated `<output>/README.txt` crop values if you need to know how much border was removed.
10. Use the TIFFs in `<output>/tif_16bit_rgb/` for the downstream RGB-only workflow.

## Important Limitations

- These outputs are RGB surrogates, not normal camera photographs.
- Panel normalization is relative unless official per-band panel reflectance values are incorporated.
- The script does not currently use Altum red edge, near infrared, or thermal bands.
- Red and blue are aligned to green with one fixed affine transform estimated from sample frames. Difficult scenes can still show edge color fringes.
- The output images are slightly smaller than the raw bands because edge pixels without all three bands are cropped away.
- The global 16-bit scale is computed per run, so images from different runs may not be directly comparable unless processed together with the same settings.

## Troubleshooting

`No complete visible-band captures to process.`

The input folder does not contain any stems with all three visible bands: `_1.tif`, `_2.tif`, and `_3.tif`. Check the folder path and file names.

`has no ReflectArea metadata; pass --manual-panel-roi.`

The panel file does not have usable panel ROI metadata. Add `--manual-panel-roi LEFT TOP RIGHT BOTTOM`.

Panel normalization looks wrong.

Check `<output>/panel_checks/`. The ROI should cover only the flat panel surface. Avoid shadows, overexposed areas, labels, edges, and anything outside the panel.

Output colors look unusual.

That can be expected. The output is made from multispectral bands and corrected/scaled for consistency, not rendered like a consumer RGB camera. In my experience they look more saturated than normal RGB images. If they have a green tint, your calibration is off.

Python cannot import `cv2`, `numpy`, `tifffile`, or `PIL`.

Install the dependencies in the environment you are using:

```powershell
pip install numpy opencv-python tifffile pillow
```

## Notes For Future Runs

For repeatable work, keep a note of:

- input folder name
- output folder name
- panel stem or stems
- manual ROI values, if used
- whether PNG previews were written
- any stems excluded because they were calibration images, test captures, blurry, or incomplete
