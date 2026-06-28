# RJPG2TIF

RJPG2TIF is a Windows desktop tool for inspecting and batch converting FLIR Duo Pro R radiometric JPEG (`*_R.JPG`) files. It extracts the embedded thermal raw data, calculates temperature rasters, exports TIFF/JPG derivatives, and writes camera metadata for photogrammetry workflows such as Agisoft Metashape.

The original R-JPG files are read-only inputs and are not modified.

## Features

- Batch scan JPG/R-JPG folders.
- Detect FLIR APP1/FFF data, embedded `RawThermalImage`, RGB preview, GPS, camera model, and radiometric parameters.
- Parse FLIR Duo Pro R 640 x 512 thermal raw arrays.
- Read Planck and atmospheric/radiometric parameters from FLIR CameraInfo records.
- Export:
  - raw UInt16 thermal TIFF
  - Float32 Celsius thermal TIFF
  - 8-bit thermal preview JPG
  - embedded RGB preview JPG
  - per-image metadata JSON
  - camera position CSV files
- Write EXIF/GPS metadata to derived files when ExifTool is available.
- Write Metashape-friendly EXIF/GPS metadata to `rgb_preview/*.jpg`, including visible-camera focal length and focal-plane resolution.
- Interactive pixel temperature validation with clickable crosshair, zoom, pan, and FLIR official-reading comparison.
- Windows icon and PyInstaller/Nuitka packaging support.

## Screenshots

UI references are included in this repository:

- `pyside6界面.png`
- `html界面.jpeg`
- `1.png`

## Requirements

- Windows 10/11
- Python 3.11 or newer
- Python packages listed in `requirements.txt`

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the application:

```powershell
python -m rjpg_preprocessor
```

or:

```powershell
python run_app.py
```

## Basic Workflow

1. Select an input folder containing FLIR Duo Pro R `*_R.JPG` files.
2. Click **Check Files** to inspect R-JPG validity, GPS fields, and radiometric parameters.
3. Review or edit the whitelisted EXIF/radiometric fields.
4. Select an output folder.
5. Choose export products.
6. Click **Start Processing**.

The step indicators in the header update as the workflow progresses.

## Output Structure

By default, outputs are written under `processed/`:

```text
processed/
  thermal_raw_uint16_tiff/
    <stem>.tif
  thermal_float32_celsius/
    <stem>.tif
  thermal_8bit_preview/
    <stem>.jpg
  rgb_preview/
    <stem>.jpg
  metadata/
    <stem>.json
    batch_report.json
    camera_positions.csv
    rgb_camera_positions.csv
```

### Thermal Outputs

- `thermal_raw_uint16_tiff`: raw thermal sensor values as UInt16.
- `thermal_float32_celsius`: calculated temperature in degrees Celsius as Float32.
- `thermal_8bit_preview`: display-oriented JPG preview, not intended for quantitative analysis.

### RGB Outputs

`rgb_preview/*.jpg` contains the embedded visible-light image extracted from the R-JPG. These JPG files receive standard EXIF/GPS metadata suitable for image alignment workflows.

## Metashape Notes

For visible-camera photogrammetry, use:

- images: `processed/rgb_preview/*.jpg`
- optional CSV: `processed/metadata/rgb_camera_positions.csv`

The RGB EXIF writer uses these visible-camera parameters:

```text
Sensor width:   7.4 mm
Sensor height:  5.55 mm
Pixel size:     1.85 um
Focal length:   8.0 mm
Principal X:    3.7 mm
Principal Y:    2.775 mm
```

The EXIF fields include:

- `Make`
- `Model`
- `Software`
- `FocalLength = 8.0 mm`
- `FocalLengthIn35mmFormat`
- `FocalPlaneXResolution`
- `FocalPlaneYResolution`
- `FocalPlaneResolutionUnit`
- GPS latitude, longitude, altitude, and direction when available

For thermal exports, the current thermal sensor constants are:

```text
Sensor width:   10.88 mm
Sensor height:  8.704 mm
Pixel size:     17 um
Focal length:   13.0 mm
Principal X:    5.44 mm
Principal Y:    4.352 mm
```

## ExifTool

ExifTool is optional but recommended for complete metadata copying.

The application searches these paths:

```text
tools/exiftool/exiftool.exe
tools/exiftool.exe
```

If ExifTool is present, derived TIFF/JPG files receive copied EXIF/GPS/XMP data plus corrected camera fields. If it is missing, processing still works. `rgb_preview/*.jpg` still receives basic Metashape-friendly EXIF/GPS using Pillow, while full metadata remains available in JSON and CSV outputs.

## Pixel Temperature Validation

The **Pixel Temperature Validation** tab lets you compare the software's calculated temperature against a FLIR Thermal Studio / ResearchIR reading:

- The initial crosshair is placed at the center of the thermal image.
- Click the validation image to select a pixel.
- Enter the official FLIR temperature reading.
- The software displays the calculated temperature and error.
- Use `Ctrl + mouse wheel` to zoom.
- Press the mouse wheel to pan.
- Press `Space` while the image has focus to return to fit view.

The thermal preview tab supports image navigation but does not display the crosshair.

## Packaging

### Nuitka

Install Nuitka:

```powershell
python -m pip install nuitka
```

Build a standalone folder:

```powershell
python -m nuitka `
  --standalone `
  --windows-console-mode=disable `
  --enable-plugin=pyside6 `
  --include-data-files=rjpg_preprocessor_icon.ico=rjpg_preprocessor_icon.ico `
  --windows-icon-from-ico=rjpg_preprocessor_icon.ico `
  --output-dir=build_nuitka `
  --output-filename=RjpgPreprocessor.exe `
  --zig `
  --assume-yes-for-downloads `
  run_app.py
```

The executable is created under:

```text
build_nuitka/run_app.dist/RjpgPreprocessor.exe
```

Distribute the entire `run_app.dist/` folder, not only the EXE.

### PyInstaller

PyInstaller packaging is also supported:

```powershell
python -m pip install pyinstaller
pyinstaller RjpgPreprocessor.spec
```

## Repository Notes

Ignored by Git:

- generated build folders
- processed output folders
- Python caches
- local ExifTool binaries

Sample R-JPG files and UI reference images are currently kept in the repository for testing and demonstration.

## Limitations

- The FLIR/FFF parser is tailored for FLIR Duo Pro R R-JPG files.
- The 8-bit preview is not quantitative.
- Temperature consistency depends on the radiometric parameters stored in the R-JPG and on matching the FLIR official software's measurement settings.
- Full EXIF/XMP copying requires ExifTool.

