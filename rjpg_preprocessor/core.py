from __future__ import annotations

import csv
import io
import json
import math
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational


THERMAL_WIDTH = 640
THERMAL_HEIGHT = 512
VISIBLE_SENSOR_WIDTH_MM = 7.4
VISIBLE_SENSOR_HEIGHT_MM = 5.55
VISIBLE_PIXEL_SIZE_UM = 1.85
VISIBLE_FOCAL_LENGTH_MM = 8.0
VISIBLE_PRINCIPAL_X_MM = VISIBLE_SENSOR_WIDTH_MM / 2.0
VISIBLE_PRINCIPAL_Y_MM = VISIBLE_SENSOR_HEIGHT_MM / 2.0
THERMAL_SENSOR_WIDTH_MM = 10.88
THERMAL_SENSOR_HEIGHT_MM = 8.704
THERMAL_PIXEL_SIZE_UM = 17.0
THERMAL_FOCAL_LENGTH_MM = 13.0
THERMAL_PRINCIPAL_X_MM = THERMAL_SENSOR_WIDTH_MM / 2.0
THERMAL_PRINCIPAL_Y_MM = THERMAL_SENSOR_HEIGHT_MM / 2.0


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


def find_exiftool() -> Path | None:
    candidates = [
        resource_path("tools/exiftool/exiftool.exe"),
        resource_path("tools/exiftool.exe"),
        Path("tools/exiftool/exiftool.exe"),
        Path("tools/exiftool.exe"),
    ]
    for path in candidates:
        if path.exists():
            return path.resolve()
    return None


def dms_to_decimal(values: Any, ref: str | None) -> float | None:
    if not values or len(values) < 3:
        return None
    deg, minute, second = [float(v) for v in values[:3]]
    sign = -1 if ref in {"S", "W"} else 1
    return sign * (deg + minute / 60.0 + second / 3600.0)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    return str(value).replace("\x00", "").strip()


@dataclass
class RadiometricParams:
    emissivity: float = 0.95
    object_distance: float = 100.0
    reflected_temp_k: float = 293.15
    atmospheric_temp_k: float = 293.15
    relative_humidity: float = 50.0
    ir_window_temp_k: float = 293.15
    ir_window_transmission: float = 1.0
    planck_r1: float = 0.0
    planck_r2: float = 1.0
    planck_b: float = 0.0
    planck_f: float = 1.0
    planck_o: float = 0.0
    ata1: float = 0.006569
    ata2: float = 0.01262
    atb1: float = -0.002276
    atb2: float = -0.00667
    atx: float = 1.9

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "Emissivity": self.emissivity,
            "ObjectDistance": self.object_distance,
            "ReflectedApparentTemperatureC": self.reflected_temp_k - 273.15,
            "AtmosphericTemperatureC": self.atmospheric_temp_k - 273.15,
            "RelativeHumidity": self.relative_humidity,
            "IRWindowTemperatureC": self.ir_window_temp_k - 273.15,
            "IRWindowTransmission": self.ir_window_transmission,
            "PlanckR1": self.planck_r1,
            "PlanckR2": self.planck_r2,
            "PlanckB": self.planck_b,
            "PlanckF": self.planck_f,
            "PlanckO": self.planck_o,
            "AtmosphericTransAlpha1": self.ata1,
            "AtmosphericTransAlpha2": self.ata2,
            "AtmosphericTransBeta1": self.atb1,
            "AtmosphericTransBeta2": self.atb2,
            "AtmosphericTransX": self.atx,
        }


@dataclass
class RjpgInfo:
    path: Path
    is_rjpg: bool
    width: int = 0
    height: int = 0
    thermal_width: int = 0
    thermal_height: int = 0
    make: str = ""
    model: str = ""
    software: str = ""
    focal_length: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    gps_time: str = ""
    fff_size: int = 0
    has_rgb: bool = False
    radiometric: RadiometricParams = field(default_factory=RadiometricParams)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "通过" if self.is_rjpg and not self.errors else "错误"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "file": self.path.name,
            "is_rjpg": self.is_rjpg,
            "display_size": [self.width, self.height],
            "thermal_size": [self.thermal_width, self.thermal_height],
            "make": self.make,
            "model": self.model,
            "software": self.software,
            "focal_length": self.focal_length,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "gps_time": self.gps_time,
            "has_rgb": self.has_rgb,
            "radiometric": self.radiometric.to_public_dict(),
            "errors": self.errors,
        }


class RjpgReader:
    def inspect(self, path: Path) -> RjpgInfo:
        info = RjpgInfo(path=path, is_rjpg=False)
        try:
            with Image.open(path) as image:
                info.width, info.height = image.size
                exif = image.getexif()
                info.make = clean_text(exif.get(271))
                info.model = clean_text(exif.get(272))
                info.software = clean_text(exif.get(305))
                focal = exif.get(37386)
                info.focal_length = float(focal) if focal is not None else None
                gps = exif.get_ifd(34853) if hasattr(exif, "get_ifd") else {}
                info.latitude = dms_to_decimal(gps.get(2), gps.get(1))
                info.longitude = dms_to_decimal(gps.get(4), gps.get(3))
                info.altitude = float(gps.get(6)) if gps.get(6) is not None else None
                if gps.get(7):
                    info.gps_time = ":".join(f"{int(float(v)):02d}" for v in gps.get(7))
        except Exception as exc:
            info.errors.append(f"无法读取 JPEG/EXIF: {exc}")
            return info

        try:
            fff = self.extract_fff(path)
            info.fff_size = len(fff)
            records = self._records(fff)
            raw_record = self._record_data(fff, records, 1)
            if raw_record is None:
                info.errors.append("缺少 RawThermalImage 记录")
                return info
            width, height = self._raw_dimensions(raw_record)
            info.thermal_width = width
            info.thermal_height = height
            cam_record = self._record_data(fff, records, 32)
            if cam_record is not None:
                info.radiometric = self._read_radiometric_params(cam_record)
            info.has_rgb = self._record_data(fff, records, 14) is not None
            info.is_rjpg = (
                info.make.upper() == "FLIR"
                and "DUO PRO R" in info.model.upper()
                and width > 0
                and height > 0
            )
            if not info.is_rjpg:
                info.errors.append("文件不是 FLIR Duo Pro R R-JPG")
        except Exception as exc:
            info.errors.append(f"无法解析 FLIR/FFF 数据: {exc}")
        return info

    def extract_fff(self, path: Path) -> bytes:
        data = path.read_bytes()
        parts: list[tuple[int, bytes]] = []
        index = 2
        while index < len(data) - 1:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker == 0xDA:
                break
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index : index + 2], "big")
            segment = data[index + 2 : index + length]
            if (
                marker == 0xE1
                and segment.startswith(b"FLIR\x00\x01")
                and len(segment) > 8
                and segment[7:8] == b"-"
            ):
                parts.append((segment[6], segment[8:]))
            index += length
        if not parts:
            raise ValueError("未找到 FLIR APP1 数据段")
        return b"".join(chunk for _, chunk in sorted(parts, key=lambda item: item[0]))

    def _records(self, fff: bytes) -> list[tuple[int, int, int]]:
        magic, _, _, rec_dir, rec_count, *_ = struct.unpack_from(">4s16sIIIIH7HIII", fff, 0)
        if magic != b"FFF\x00":
            raise ValueError("FFF magic 不正确")
        records: list[tuple[int, int, int]] = []
        for idx in range(rec_count):
            typ, _sub, _ver, _idx, offset, length, *_ = struct.unpack_from(
                ">HHIIIIIII", fff, rec_dir + idx * 32
            )
            if typ and length:
                records.append((typ, offset, length))
        return records

    def _record_data(
        self, fff: bytes, records: list[tuple[int, int, int]], record_type: int
    ) -> bytes | None:
        for typ, offset, length in records:
            if typ == record_type:
                return fff[offset : offset + length]
        return None

    def _raw_dimensions(self, raw_record: bytes) -> tuple[int, int]:
        _image_type, width, height = struct.unpack_from("<HHH", raw_record, 0)
        return int(width), int(height)

    def read_raw_array(self, path: Path) -> tuple[np.ndarray, RadiometricParams]:
        fff = self.extract_fff(path)
        records = self._records(fff)
        raw_record = self._record_data(fff, records, 1)
        if raw_record is None:
            raise ValueError("缺少 RawThermalImage 记录")
        width, height = self._raw_dimensions(raw_record)
        pixels = np.frombuffer(raw_record[32:], dtype="<u2")
        expected = width * height
        if pixels.size < expected:
            raise ValueError("RawThermalImage 像素数量不足")
        array = pixels[:expected].reshape((height, width)).copy()
        cam_record = self._record_data(fff, records, 32)
        params = self._read_radiometric_params(cam_record) if cam_record else RadiometricParams()
        return array, params

    def extract_rgb_image(self, path: Path) -> Image.Image | None:
        fff = self.extract_fff(path)
        records = self._records(fff)
        record = self._record_data(fff, records, 14)
        if not record:
            return None
        start = record.find(b"\xff\xd8")
        end = record.rfind(b"\xff\xd9")
        if start >= 0 and end > start:
            return Image.open(io.BytesIO(record[start : end + 2])).convert("RGB")
        return None

    def _read_radiometric_params(self, cam: bytes) -> RadiometricParams:
        def f32(offset: int, default: float = 0.0) -> float:
            if offset + 4 > len(cam):
                return default
            value = struct.unpack_from("<f", cam, offset)[0]
            return value if math.isfinite(value) else default

        def i32(offset: int, default: int = 0) -> int:
            if offset + 4 > len(cam):
                return default
            return struct.unpack_from("<i", cam, offset)[0]

        return RadiometricParams(
            emissivity=f32(32, 0.95),
            object_distance=f32(36, 100.0),
            reflected_temp_k=f32(40, 293.15),
            atmospheric_temp_k=f32(48, 293.15),
            relative_humidity=f32(60, 0.5) * 100.0,
            ir_window_temp_k=f32(44, 293.15),
            ir_window_transmission=f32(52, 1.0),
            planck_r1=f32(88),
            planck_b=f32(92),
            planck_f=f32(96, 1.0),
            planck_o=float(i32(776, 0)),
            planck_r2=f32(780, 1.0),
            ata1=f32(112, 0.006569),
            ata2=f32(116, 0.01262),
            atb1=f32(120, -0.002276),
            atb2=f32(124, -0.00667),
            atx=f32(128, 1.9),
        )


def raw2temp_celsius(raw: np.ndarray, params: RadiometricParams) -> np.ndarray:
    raw = raw.astype(np.float64)
    emissivity = max(params.emissivity, 0.001)
    distance = max(params.object_distance, 0.0)
    humidity = max(params.relative_humidity / 100.0, 0.0)
    window_trans = max(params.ir_window_transmission, 0.001)
    window_emissivity = 1.0 - window_trans
    window_reflectivity = 0.0
    atm_temp_c = params.atmospheric_temp_k - 273.15

    h2o = humidity * np.exp(
        1.5587
        + 0.06939 * atm_temp_c
        - 0.00027816 * atm_temp_c**2
        + 0.00000068455 * atm_temp_c**3
    )
    tau1 = params.atx * np.exp(
        -math.sqrt(distance / 2.0) * (params.ata1 + params.atb1 * math.sqrt(max(h2o, 0.0)))
    ) + (1.0 - params.atx) * np.exp(
        -math.sqrt(distance / 2.0) * (params.ata2 + params.atb2 * math.sqrt(max(h2o, 0.0)))
    )
    tau1 = max(float(tau1), 0.001)
    tau2 = tau1

    def planck_raw(temp_k: float) -> float:
        return params.planck_r1 / (
            params.planck_r2 * (np.exp(params.planck_b / temp_k) - params.planck_f)
        ) - params.planck_o

    raw_refl1 = planck_raw(params.reflected_temp_k)
    raw_refl1_attn = (1.0 - emissivity) / emissivity * raw_refl1
    raw_atm = planck_raw(params.atmospheric_temp_k)
    raw_atm1_attn = (1.0 - tau1) / emissivity / tau1 * raw_atm
    raw_window = planck_raw(params.ir_window_temp_k)
    einv = 1.0 / emissivity / tau1 / window_trans
    raw_window_attn = window_emissivity * einv * raw_window
    raw_refl2 = raw_refl1
    raw_refl2_attn = window_reflectivity * einv * raw_refl2
    ediv = einv / tau2
    raw_atm2_attn = (1.0 - tau2) * ediv * raw_atm

    raw_object = (
        raw * ediv
        - raw_atm1_attn
        - raw_atm2_attn
        - raw_window_attn
        - raw_refl1_attn
        - raw_refl2_attn
    )
    denominator = params.planck_r2 * (raw_object + params.planck_o)
    with np.errstate(divide="ignore", invalid="ignore"):
        temp_k = params.planck_b / np.log(params.planck_r1 / denominator + params.planck_f)
    temp_c = temp_k - 273.15
    return temp_c.astype(np.float32)


def make_preview(temp_c: np.ndarray) -> np.ndarray:
    finite = temp_c[np.isfinite(temp_c)]
    if finite.size == 0:
        return np.zeros(temp_c.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite, [2, 98])
    if hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((temp_c - lo) / (hi - lo), 0, 1)
    return (scaled * 255).astype(np.uint8)


def apply_param_overrides(params: RadiometricParams, overrides: dict[str, float]) -> RadiometricParams:
    result = RadiometricParams(**params.__dict__)
    for key, value in overrides.items():
        if hasattr(result, key):
            setattr(result, key, value)
    return result


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_camera_csv(
    path: Path,
    infos: list[RjpgInfo],
    edits: dict[str, Any] | None = None,
    suffix: str = ".tif",
    subdir: str = "",
    focal_length: float | None = None,
) -> None:
    edits = edits or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["filename", "longitude", "latitude", "altitude", "focal_length", "datetime"],
        )
        writer.writeheader()
        for info in infos:
            filename = info.path.with_suffix(suffix).name
            if subdir:
                filename = f"{subdir}/{filename}"
            writer.writerow(
                {
                    "filename": filename,
                    "longitude": edits.get("longitude", info.longitude),
                    "latitude": edits.get("latitude", info.latitude),
                    "altitude": edits.get("altitude", info.altitude),
                    "focal_length": focal_length
                    if focal_length is not None
                    else edits.get("focal_length", info.focal_length),
                    "datetime": edits.get("datetime", ""),
                }
            )


def _first_metadata_value(edits: dict[str, Any], info: RjpgInfo, key: str) -> Any:
    value = edits.get(key)
    if value not in (None, ""):
        return value
    return getattr(info, key, None)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rational(value: float | int, max_denominator: int = 1000000) -> IFDRational:
    fraction = Fraction(float(value)).limit_denominator(max_denominator)
    return IFDRational(fraction.numerator, fraction.denominator)


def _decimal_to_dms(value: float) -> tuple[IFDRational, IFDRational, IFDRational]:
    absolute = abs(float(value))
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60.0
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60.0
    return (_rational(degrees), _rational(minutes), _rational(seconds))


def _focal_35mm_equivalent(focal_mm: float, sensor_width_mm: float, sensor_height_mm: float) -> int:
    sensor_diagonal = math.hypot(sensor_width_mm, sensor_height_mm)
    full_frame_diagonal = math.hypot(36.0, 24.0)
    return int(round(focal_mm * full_frame_diagonal / sensor_diagonal))


def _append_tag(cmd: list[str], tag: str, value: Any) -> None:
    if value not in (None, ""):
        cmd.append(f"-{tag}={value}")


def _append_gps_tags(cmd: list[str], edits: dict[str, Any], info: RjpgInfo) -> None:
    latitude = _float_or_none(_first_metadata_value(edits, info, "latitude"))
    longitude = _float_or_none(_first_metadata_value(edits, info, "longitude"))
    altitude = _float_or_none(_first_metadata_value(edits, info, "altitude"))
    direction = _float_or_none(edits.get("direction"))

    if latitude is not None:
        _append_tag(cmd, "GPSLatitude", abs(latitude))
        _append_tag(cmd, "GPSLatitudeRef", "S" if latitude < 0 else "N")
    if longitude is not None:
        _append_tag(cmd, "GPSLongitude", abs(longitude))
        _append_tag(cmd, "GPSLongitudeRef", "W" if longitude < 0 else "E")
    if altitude is not None:
        _append_tag(cmd, "GPSAltitude", abs(altitude))
        _append_tag(cmd, "GPSAltitudeRef", 1 if altitude < 0 else 0)
    if direction is not None:
        _append_tag(cmd, "GPSImgDirection", direction)
        _append_tag(cmd, "GPSImgDirectionRef", "T")


def write_basic_rgb_exif(dst: Path, edits: dict[str, Any], info: RjpgInfo) -> str:
    try:
        with Image.open(dst) as image:
            image = image.convert("RGB")
            exif = image.getexif()
            width, height = image.size

            make = _first_metadata_value(edits, info, "make")
            model = _first_metadata_value(edits, info, "model")
            software = _first_metadata_value(edits, info, "software")
            focal_length = VISIBLE_FOCAL_LENGTH_MM
            focal_35mm = _focal_35mm_equivalent(
                focal_length, VISIBLE_SENSOR_WIDTH_MM, VISIBLE_SENSOR_HEIGHT_MM
            )
            datetime_value = edits.get("datetime")

            if make not in (None, ""):
                exif[271] = str(make)
            if model not in (None, ""):
                exif[272] = str(model)
            if software not in (None, ""):
                exif[305] = str(software)
            if datetime_value not in (None, ""):
                exif[306] = str(datetime_value)

            exif_ifd = exif.get_ifd(34665)
            exif_ifd[37386] = _rational(focal_length)
            exif_ifd[41486] = _rational(25.4 / (VISIBLE_PIXEL_SIZE_UM / 1000.0))
            exif_ifd[41487] = _rational(25.4 / (VISIBLE_PIXEL_SIZE_UM / 1000.0))
            exif_ifd[41488] = 2
            exif_ifd[41989] = focal_35mm
            if datetime_value not in (None, ""):
                exif_ifd[36867] = str(datetime_value)
                exif_ifd[36868] = str(datetime_value)
            exif_ifd[40962] = width
            exif_ifd[40963] = height
            exif[34665] = exif_ifd

            gps_ifd = exif.get_ifd(34853)
            latitude = _float_or_none(_first_metadata_value(edits, info, "latitude"))
            longitude = _float_or_none(_first_metadata_value(edits, info, "longitude"))
            altitude = _float_or_none(_first_metadata_value(edits, info, "altitude"))
            direction = _float_or_none(edits.get("direction"))
            if latitude is not None:
                gps_ifd[1] = "S" if latitude < 0 else "N"
                gps_ifd[2] = _decimal_to_dms(latitude)
            if longitude is not None:
                gps_ifd[3] = "W" if longitude < 0 else "E"
                gps_ifd[4] = _decimal_to_dms(longitude)
            if altitude is not None:
                gps_ifd[5] = b"\x01" if altitude < 0 else b"\x00"
                gps_ifd[6] = _rational(abs(altitude))
            if direction is not None:
                gps_ifd[16] = "T"
                gps_ifd[17] = _rational(direction)
            exif[34853] = gps_ifd

            image.save(dst, quality=95, exif=exif.tobytes())
        return "Basic Metashape EXIF written for rgb_preview"
    except Exception as exc:
        return f"Basic Metashape EXIF write failed for rgb_preview: {exc}"


def copy_metadata_with_exiftool(src: Path, dst: Path, edits: dict[str, Any]) -> str:
    exiftool = find_exiftool()
    if not exiftool:
        return "ExifTool 未打包，跳过 TIFF/JPG EXIF 写入；元数据已写入 JSON/CSV"
    cmd = [
        str(exiftool),
        "-overwrite_original",
        "-TagsFromFile",
        str(src),
        "-EXIF:All",
        "-GPS:All",
        "-XMP:All",
    ]
    mapping = {
        "make": "Make",
        "model": "Model",
        "datetime": "DateTimeOriginal",
    }
    for key, tag in mapping.items():
        _append_tag(cmd, tag, edits.get(key))
    thermal_35mm = _focal_35mm_equivalent(
        THERMAL_FOCAL_LENGTH_MM, THERMAL_SENSOR_WIDTH_MM, THERMAL_SENSOR_HEIGHT_MM
    )
    _append_tag(cmd, "FocalLength", THERMAL_FOCAL_LENGTH_MM)
    _append_tag(cmd, "FocalLengthIn35mmFormat", thermal_35mm)
    _append_tag(cmd, "FocalPlaneXResolution", 25.4 / (THERMAL_PIXEL_SIZE_UM / 1000.0))
    _append_tag(cmd, "FocalPlaneYResolution", 25.4 / (THERMAL_PIXEL_SIZE_UM / 1000.0))
    _append_tag(cmd, "FocalPlaneResolutionUnit", "inches")
    _append_gps_tags(cmd, edits, RjpgInfo(path=src, is_rjpg=True))
    cmd.append(str(dst))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return f"ExifTool 写入失败: {proc.stderr.strip() or proc.stdout.strip()}"
    return "ExifTool 元数据写入完成"


def write_metashape_rgb_exif(src: Path, dst: Path, edits: dict[str, Any], info: RjpgInfo) -> str:
    exiftool = find_exiftool()
    if not exiftool:
        return write_basic_rgb_exif(dst, edits, info)

    width = height = None
    try:
        with Image.open(dst) as image:
            width, height = image.size
    except Exception:
        pass

    focal_length = VISIBLE_FOCAL_LENGTH_MM
    focal_35mm = _focal_35mm_equivalent(
        focal_length, VISIBLE_SENSOR_WIDTH_MM, VISIBLE_SENSOR_HEIGHT_MM
    )
    datetime_value = edits.get("datetime")
    make = _first_metadata_value(edits, info, "make")
    model = _first_metadata_value(edits, info, "model")
    software = _first_metadata_value(edits, info, "software")

    cmd = [
        str(exiftool),
        "-overwrite_original",
        "-TagsFromFile",
        str(src),
        "-EXIF:All",
        "-GPS:All",
        "-XMP:All",
        "-Orientation=1",
    ]
    _append_tag(cmd, "Make", make)
    _append_tag(cmd, "Model", model)
    _append_tag(cmd, "LensModel", model)
    _append_tag(cmd, "Software", software)
    _append_tag(cmd, "FocalLength", focal_length)
    _append_tag(cmd, "FocalLengthIn35mmFormat", focal_35mm)
    _append_tag(cmd, "FocalPlaneXResolution", 25.4 / (VISIBLE_PIXEL_SIZE_UM / 1000.0))
    _append_tag(cmd, "FocalPlaneYResolution", 25.4 / (VISIBLE_PIXEL_SIZE_UM / 1000.0))
    _append_tag(cmd, "FocalPlaneResolutionUnit", "inches")
    _append_tag(cmd, "DateTimeOriginal", datetime_value)
    _append_tag(cmd, "CreateDate", datetime_value)
    _append_tag(cmd, "ModifyDate", datetime_value)
    _append_tag(cmd, "ExifImageWidth", width)
    _append_tag(cmd, "ExifImageHeight", height)
    _append_gps_tags(cmd, edits, info)
    cmd.append(str(dst))

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return f"Metashape EXIF write failed for rgb_preview: {proc.stderr.strip() or proc.stdout.strip()}"
    return "Metashape EXIF written for rgb_preview"


class Exporter:
    def __init__(self) -> None:
        self.reader = RjpgReader()

    def export_one(
        self,
        info: RjpgInfo,
        output_dir: Path,
        options: dict[str, bool],
        exif_edits: dict[str, Any],
        param_overrides: dict[str, float],
    ) -> list[str]:
        logs: list[str] = []
        raw, params = self.reader.read_raw_array(info.path)
        params = apply_param_overrides(params, param_overrides)
        stem = info.path.stem

        if options.get("raw_uint16", True):
            out = output_dir / "thermal_raw_uint16_tiff" / f"{stem}.tif"
            out.parent.mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(out, raw.astype(np.uint16), photometric="minisblack")
            logs.append(f"写入 {out}")
            logs.append(copy_metadata_with_exiftool(info.path, out, exif_edits))

        temp_c: np.ndarray | None = None
        if options.get("float32", True) or options.get("preview", True):
            temp_c = raw2temp_celsius(raw, params)

        if options.get("float32", True) and temp_c is not None:
            out = output_dir / "thermal_float32_celsius" / f"{stem}.tif"
            out.parent.mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(out, temp_c, photometric="minisblack")
            logs.append(f"写入 {out}")
            logs.append(copy_metadata_with_exiftool(info.path, out, exif_edits))

        if options.get("preview", True) and temp_c is not None:
            preview = make_preview(temp_c)
            out = output_dir / "thermal_8bit_preview" / f"{stem}.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(preview).save(out, quality=95)
            logs.append(f"写入 {out}")
            logs.append(copy_metadata_with_exiftool(info.path, out, exif_edits))

        if options.get("rgb", True):
            rgb = self.reader.extract_rgb_image(info.path)
            if rgb is not None:
                out = output_dir / "rgb_preview" / f"{stem}.jpg"
                out.parent.mkdir(parents=True, exist_ok=True)
                rgb.save(out, quality=95)
                logs.append(f"写入 {out}")
                logs.append(write_metashape_rgb_exif(info.path, out, exif_edits, info))
            else:
                logs.append(f"{info.path.name} 未提取到内嵌 RGB JPG")

        if options.get("metadata", True):
            payload = info.to_metadata()
            payload["radiometric_used"] = params.to_public_dict()
            payload["exif_edits"] = exif_edits
            out = output_dir / "metadata" / f"{stem}.json"
            write_json(out, payload)
            logs.append(f"写入 {out}")
        return logs
