# FLIR Duo Pro R R-JPG 预处理工作台

这是一个基于 Python/PySide6 的 Windows 桌面软件，用于批量检查和处理 FLIR Duo Pro R 的 `*_R.JPG`。

## 启动

```powershell
python -m rjpg_preprocessor
```

## 当前能力

- 扫描输入目录中的 JPG/R-JPG。
- 检测 FLIR APP1/FFF 数据、RawThermalImage、相机信息和 GPS。
- 解析 Duo Pro R R-JPG 中的 640x512 raw thermal 数据。
- 读取 CameraInfo 中的 Planck 和辐射参数。
- 按 FLIR radiometric `raw2temp` 思路导出 Float32 Celsius TIFF。
- 导出 Raw UInt16 TIFF、8-bit 预览、RGB 预览、JSON、CSV。
- 原始 R-JPG 只读，不会被修改。

## ExifTool

软件会优先查找：

```text
tools/exiftool/exiftool.exe
tools/exiftool.exe
```

如果存在，会把标准 EXIF/GPS 复制/写入派生 TIFF/JPG。如果不存在，处理仍可完成，但 TIFF/JPG 的 EXIF 写入会跳过，完整元数据仍保存在 `metadata/*.json` 和 `camera_positions.csv`。

## 打包建议

安装 PyInstaller 后使用文件夹版：

```powershell
pyinstaller RjpgPreprocessor.spec
```

如果暂时没有 `tools/exiftool/exiftool.exe`，也可以先运行软件，但派生 TIFF/JPG 的 EXIF 写入会跳过。

## 温度一致性验证

`thermal_float32_celsius` 使用 R-JPG 中的 Planck 参数和辐射参数计算。为了确认与 FLIR Thermal Studio / ResearchIR 一致，请在官方软件中记录同一像素坐标温度，再用 GUI 的“像素温度验证”面板比对。
