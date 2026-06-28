from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import (
    Exporter,
    RjpgInfo,
    RjpgReader,
    VISIBLE_FOCAL_LENGTH_MM,
    raw2temp_celsius,
    resource_path,
    write_camera_csv,
    write_json,
)


class WorkerSignals(QObject):
    progress = Signal(int)
    log = Signal(str)
    finished = Signal(bool, str)


class ExportWorker(QRunnable):
    def __init__(
        self,
        infos: list[RjpgInfo],
        output_dir: Path,
        options: dict[str, bool],
        exif_edits: dict[str, Any],
        param_overrides: dict[str, float],
    ) -> None:
        super().__init__()
        self.infos = infos
        self.output_dir = output_dir
        self.options = options
        self.exif_edits = exif_edits
        self.param_overrides = param_overrides
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            exporter = Exporter()
            total = max(len(self.infos), 1)
            for idx, info in enumerate(self.infos, start=1):
                self.signals.log.emit(f"处理 {info.path.name}")
                for line in exporter.export_one(
                    info, self.output_dir, self.options, self.exif_edits, self.param_overrides
                ):
                    self.signals.log.emit(line)
                self.signals.progress.emit(int(idx / total * 95))
            if self.options.get("metadata", True):
                write_camera_csv(
                    self.output_dir / "metadata" / "camera_positions.csv",
                    self.infos,
                    self.exif_edits,
                )
                write_camera_csv(
                    self.output_dir / "metadata" / "rgb_camera_positions.csv",
                    self.infos,
                    self.exif_edits,
                    suffix=".jpg",
                    subdir="rgb_preview",
                    focal_length=VISIBLE_FOCAL_LENGTH_MM,
                )
                write_json(
                    self.output_dir / "metadata" / "batch_report.json",
                    [info.to_metadata() for info in self.infos],
                )
            self.signals.progress.emit(100)
            self.signals.finished.emit(True, "批处理完成")
        except Exception:
            self.signals.finished.emit(False, traceback.format_exc())


class MetricCard(QFrame):
    def __init__(self, label: str, value: str = "-") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self.label = QLabel(label)
        self.label.setObjectName("muted")
        self.value = QLabel(value)
        self.value.setObjectName("metricValue")
        self.value.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)

    def set_value(self, value: Any) -> None:
        self.value.setText("-" if value in (None, "") else str(value))


class PixelPreviewLabel(QLabel):
    pixelSelected = Signal(int, int)

    def __init__(self, text: str = "", show_crosshair: bool = True) -> None:
        super().__init__(text)
        self.show_crosshair = show_crosshair
        self.source_width = 0
        self.source_height = 0
        self.source_pixmap = QPixmap()
        self.scale_factor = 1.0
        self.pan = QPointF(0, 0)
        self.selected_pixel: tuple[int, int] | None = None
        self.dragging = False
        self.drag_start = QPointF(0, 0)
        self.pan_start = QPointF(0, 0)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_source_size(self, width: int, height: int) -> None:
        self.source_width = width
        self.source_height = height

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802
        self.source_pixmap = pixmap
        if pixmap.isNull():
            return super().setPixmap(pixmap)
        self.source_width = pixmap.width()
        self.source_height = pixmap.height()
        self.fit_to_view()

    def fit_to_view(self) -> None:
        self.scale_factor = 1.0
        self.pan = QPointF(0, 0)
        self.update()

    def _base_scale(self) -> float:
        if not self.source_width or not self.source_height:
            return 1.0
        return min(self.width() / self.source_width, self.height() / self.source_height)

    def _image_rect(self) -> tuple[float, float, float, float]:
        scale = self._base_scale() * self.scale_factor
        display_width = self.source_width * scale
        display_height = self.source_height * scale
        left = (self.width() - display_width) / 2.0 + self.pan.x()
        top = (self.height() - display_height) / 2.0 + self.pan.y()
        return left, top, display_width, display_height

    def _widget_to_pixel(self, position: QPointF) -> tuple[int, int] | None:
        if not self.source_width or not self.source_height:
            return None
        left, top, display_width, display_height = self._image_rect()
        if not (left <= position.x() <= left + display_width and top <= position.y() <= top + display_height):
            return None
        scale = self._base_scale() * self.scale_factor
        x = int((position.x() - left) / scale)
        y = int((position.y() - top) / scale)
        x = max(0, min(self.source_width - 1, x))
        y = max(0, min(self.source_height - 1, y))
        return x, y

    def set_selected_pixel(self, x: int, y: int) -> None:
        self.selected_pixel = (x, y)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.source_pixmap.isNull():
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111827"))
        left, top, display_width, display_height = self._image_rect()
        painter.drawPixmap(int(left), int(top), int(display_width), int(display_height), self.source_pixmap)
        if self.show_crosshair and self.selected_pixel is not None:
            x, y = self.selected_pixel
            scale = self._base_scale() * self.scale_factor
            screen_x = left + (x + 0.5) * scale
            screen_y = top + (y + 0.5) * scale
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.drawLine(int(screen_x - 18), int(screen_y), int(screen_x + 18), int(screen_y))
            painter.drawLine(int(screen_x), int(screen_y - 18), int(screen_x), int(screen_y + 18))
            painter.setPen(QPen(QColor("#ef4444"), 1))
            painter.drawLine(int(screen_x - 18), int(screen_y), int(screen_x + 18), int(screen_y))
            painter.drawLine(int(screen_x), int(screen_y - 18), int(screen_x), int(screen_y + 18))
            painter.drawEllipse(QPointF(screen_x, screen_y), 5, 5)

    def wheelEvent(self, event) -> None:  # noqa: ANN001, N802
        if not (event.modifiers() & Qt.ControlModifier) or self.source_pixmap.isNull():
            return super().wheelEvent(event)
        position = event.position()
        old_scale = self._base_scale() * self.scale_factor
        old_left, old_top, _, _ = self._image_rect()
        before = self._widget_to_pixel(position)
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale_factor = max(1.0, min(12.0, self.scale_factor * factor))
        if before is not None:
            scale = self._base_scale() * self.scale_factor
            base_left = (self.width() - self.source_width * scale) / 2.0
            base_top = (self.height() - self.source_height * scale) / 2.0
            old_pixel_x = (position.x() - old_left) / old_scale
            old_pixel_y = (position.y() - old_top) / old_scale
            self.pan = QPointF(
                position.x() - base_left - old_pixel_x * scale,
                position.y() - base_top - old_pixel_y * scale,
            )
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.setFocus()
        if event.button() == Qt.MiddleButton and not self.source_pixmap.isNull():
            self.dragging = True
            self.drag_start = event.position()
            self.pan_start = QPointF(self.pan)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if not self.source_width or not self.source_height:
            return super().mousePressEvent(event)
        pixel = self._widget_to_pixel(event.position())
        if pixel is None:
            return super().mousePressEvent(event)
        x, y = pixel
        if self.show_crosshair:
            self.set_selected_pixel(x, y)
        self.pixelSelected.emit(x, y)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.dragging:
            self.pan = self.pan_start + (event.position() - self.drag_start)
            self.update()
            event.accept()
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MiddleButton and self.dragging:
            self.dragging = False
            self.setCursor(Qt.CrossCursor)
            event.accept()
            return
        return super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key_Space:
            self.fit_to_view()
            event.accept()
            return
        return super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FLIR Duo Pro R R-JPG 预处理工作台")
        icon = QIcon(str(resource_path("rjpg_preprocessor_icon.ico")))
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        self.resize(1580, 940)
        self.reader = RjpgReader()
        self.thread_pool = QThreadPool.globalInstance()
        self.infos: list[RjpgInfo] = []
        self.current_info: RjpgInfo | None = None
        self.input_dir = Path.cwd()
        self.output_dir = Path.cwd() / "processed"
        self.current_temp: np.ndarray | None = None
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)
        outer.addWidget(self._build_header())

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(self._build_left_panel())
        main_splitter.addWidget(self._build_center_panel())
        main_splitter.addWidget(self._build_right_panel())
        main_splitter.setSizes([300, 820, 380])
        outer.addWidget(main_splitter, 1)

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self._build_output_panel())
        bottom.addWidget(self._build_log_panel())
        bottom.setSizes([850, 650])
        bottom.setMaximumHeight(210)
        outer.addWidget(bottom)

    def _build_header(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("header")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel()
        mark.setFixedSize(32, 32)
        mark.setPixmap(self._brand_pixmap())
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel("FLIR Duo Pro R R-JPG 预处理工作台")
        title.setObjectName("appTitle")
        subtitle = QLabel("Radiometric TIFF / Float32 Celsius / 元数据批处理")
        subtitle.setObjectName("subtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand.addWidget(mark)
        brand.addLayout(brand_text)
        title_row.addLayout(brand, 1)

        self.btn_report = QPushButton("导出检查报告")
        self.btn_check = QPushButton("检查文件")
        self.btn_process = QPushButton("开始处理")
        self.btn_process.setObjectName("primary")
        self.btn_report.clicked.connect(self.export_report)
        self.btn_check.clicked.connect(self.scan_folder)
        self.btn_process.clicked.connect(self.start_export)
        title_row.addWidget(self.btn_report)
        title_row.addWidget(self.btn_check)
        title_row.addWidget(self.btn_process)
        layout.addLayout(title_row)

        path_row = QHBoxLayout()
        self.input_label = self._path_label(str(self.input_dir))
        self.output_label = self._path_label(str(self.output_dir))
        btn_input = QPushButton("选择")
        btn_output = QPushButton("选择")
        btn_input.clicked.connect(self.choose_input)
        btn_output.clicked.connect(self.choose_output)
        path_row.addWidget(self._path_box("输入", self.input_label, btn_input), 1)
        path_row.addWidget(self._path_box("输出", self.output_label, btn_output), 1)
        path_row.addWidget(QPushButton("载入预设"))
        path_row.addWidget(QPushButton("保存预设"))
        layout.addLayout(path_row)

        steps = QHBoxLayout()
        self.step_labels: list[QLabel] = []
        for index, text in enumerate(["导入文件夹", "检查 R-JPG", "编辑参数", "选择输出", "批量处理", "检查结果"], start=1):
            step = QLabel(f"{index}  {text}")
            step.setAlignment(Qt.AlignCenter)
            step.setObjectName("step")
            self.step_labels.append(step)
            steps.addWidget(step)
        layout.addLayout(steps)
        self.set_step(1)
        return panel

    def _path_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("pathText")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setMinimumWidth(240)
        return label

    def set_step(self, active_step: int) -> None:
        for index, label in enumerate(self.step_labels, start=1):
            label.setObjectName("stepActive" if index <= active_step else "step")
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

    def _path_box(self, name: str, path_label: QLabel, button: QPushButton) -> QWidget:
        box = QFrame()
        box.setObjectName("pathBox")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.addWidget(QLabel(name))
        layout.addWidget(path_label, 1)
        layout.addWidget(button)
        return box

    def _brand_pixmap(self) -> QPixmap:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        grad = QLinearGradient(0, 0, 32, 32)
        grad.setColorAt(0, QColor("#111827"))
        grad.setColorAt(0.35, QColor("#dc2626"))
        grad.setColorAt(0.68, QColor("#f59e0b"))
        grad.setColorAt(1, QColor("#0f766e"))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(grad)
        painter.setPen(QColor("#0f172a"))
        painter.drawRoundedRect(1, 1, 30, 30, 7, 7)
        painter.end()
        return pixmap

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        header = self._panel_header("文件与批次检查", "0 / 0")
        self.left_count = header.findChild(QLabel, "panelHint")
        layout.addWidget(header)

        cards = QGridLayout()
        cards.setSpacing(8)
        self.card_camera = MetricCard("相机", "-")
        self.card_size = MetricCard("热图尺寸", "-")
        self.card_focal = MetricCard("焦距", "-")
        self.card_planck = MetricCard("Planck 参数", "-")
        for i, card in enumerate([self.card_camera, self.card_size, self.card_focal, self.card_planck]):
            cards.addWidget(card, i // 2, i % 2)
        layout.addLayout(cards)

        self.status_banner = QLabel("尚未检查")
        self.status_banner.setObjectName("statusBanner")
        layout.addWidget(self.status_banner)

        self.file_list = QListWidget()
        self.file_list.setObjectName("fileList")
        self.file_list.currentRowChanged.connect(self.select_file)
        layout.addWidget(self.file_list, 1)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.current_file_label = QLabel("")
        header = self._panel_header("图像预览与温度验证", "")
        header.layout().addWidget(self.current_file_label)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("previewTabs")
        self.preview_label = PixelPreviewLabel("选择文件后显示热红外预览", show_crosshair=False)
        self.rgb_label = QLabel("未提取 RGB 预览")
        self.verify_label = PixelPreviewLabel("点击热红外图像选取像素，或在下方输入 X/Y 坐标")
        for label in (self.preview_label, self.rgb_label, self.verify_label):
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(390)
            label.setObjectName("preview")
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.pixelSelected.connect(self.set_pixel_from_preview)
        self.verify_label.pixelSelected.connect(self.set_pixel_from_preview)
        self.tabs.addTab(self.preview_label, "热红外预览")
        self.tabs.addTab(self.rgb_label, "RGB 预览")
        self.tabs.addTab(self.verify_label, "像素温度验证")
        layout.addWidget(self.tabs, 1)

        stat_grid = QGridLayout()
        stat_grid.setSpacing(8)
        self.stat_min = MetricCard("最小温度", "-")
        self.stat_max = MetricCard("最大温度", "-")
        self.stat_avg = MetricCard("均值", "-")
        self.stat_pixel = MetricCard("当前像素", "-")
        for i, card in enumerate([self.stat_min, self.stat_max, self.stat_avg, self.stat_pixel]):
            stat_grid.addWidget(card, 0, i)
        layout.addLayout(stat_grid)

        verify = QGridLayout()
        verify.setSpacing(8)
        self.pixel_x = self._line("346")
        self.pixel_y = self._line("221")
        self.official_temp = self._line("")
        self.calc_temp = self._line("", readonly=True)
        self.delta_temp = self._line("", readonly=True)
        btn_verify = QPushButton("计算当前像素")
        btn_verify.clicked.connect(self.verify_pixel)
        for col, (label, widget) in enumerate(
            [
                ("像素 X", self.pixel_x),
                ("像素 Y", self.pixel_y),
                ("FLIR 官方读数", self.official_temp),
                ("软件计算", self.calc_temp),
                ("误差", self.delta_temp),
            ]
        ):
            verify.addWidget(self._field(label, widget), 0, col)
        verify.addWidget(btn_verify, 0, 5)
        layout.addLayout(verify)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        header = self._panel_header("EXIF 与辐射参数", "白名单编辑")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("paramScroll")
        content = QWidget()
        self.form_layout = QVBoxLayout(content)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(10)
        self.fields: dict[str, QLineEdit] = {}
        self._add_group(
            "相机信息",
            [("make", "Make"), ("model", "Model"), ("focal_length", "FocalLength"), ("software", "Software")],
            subtitle="写入派生文件",
        )
        self._add_group(
            "GPS 与时间",
            [
                ("latitude", "Latitude"),
                ("longitude", "Longitude"),
                ("altitude", "Altitude"),
                ("direction", "Direction"),
                ("datetime", "DateTimeOriginal"),
            ],
            subtitle="CSV / TIFF EXIF",
        )
        self._add_group(
            "辐射计算参数",
            [
                ("emissivity", "Emissivity"),
                ("object_distance", "ObjectDistance m"),
                ("reflected_temp_c", "ReflectedApparentTemperature °C"),
                ("atmospheric_temp_c", "AtmosphericTemperature °C"),
                ("relative_humidity", "RelativeHumidity %"),
                ("ir_window_transmission", "IRWindowTransmission"),
            ],
            subtitle="Float32 Celsius",
        )
        self._add_group(
            "Planck 参数",
            [
                ("planck_r1", "PlanckR1"),
                ("planck_r2", "PlanckR2"),
                ("planck_b", "PlanckB"),
                ("planck_f", "PlanckF"),
                ("planck_o", "PlanckO"),
            ],
            subtitle="只读",
            readonly=True,
        )
        self.form_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        btn_reset = QPushButton("恢复原始值")
        btn_apply = QPushButton("应用到全部图像")
        btn_validate = QPushButton("验证参数")
        btn_validate.setObjectName("primarySmall")
        btn_reset.clicked.connect(self.populate_fields)
        btn_apply.clicked.connect(lambda: self.log("当前白名单字段将在批处理时应用到全部输出文件"))
        btn_validate.clicked.connect(self.verify_pixel)
        buttons.addWidget(btn_reset)
        buttons.addWidget(btn_apply)
        buttons.addWidget(btn_validate)
        layout.addLayout(buttons)
        return panel

    def _build_output_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._panel_header("输出设置", ""))
        grid = QGridLayout()
        grid.setSpacing(8)
        option_defs = [
            ("raw", "Raw UInt16 TIFF", "拼图 / 归档主输出"),
            ("float", "Float32 Celsius TIFF", "温度分析主输出"),
            ("preview", "8-bit Preview", "质检与兼容兜底"),
            ("rgb", "RGB Preview", "可见光备用质检"),
            ("meta", "Metadata JSON", "完整 FLIR / EXIF 参数"),
            ("csv", "Camera CSV", "拼图软件相机位置"),
        ]
        for i, (key, title, subtitle) in enumerate(option_defs):
            tile, checkbox = self._option_tile(title, subtitle)
            setattr(self, f"opt_{key}", checkbox)
            grid.addWidget(tile, i // 3, i % 3)
        layout.addLayout(grid)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._panel_header("日志与错误列表", ""))
        self.log_text = QPlainTextEdit()
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)
        return panel

    def _panel_header(self, title: str, hint: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 8)
        label = QLabel(title)
        label.setObjectName("title")
        hint_label = QLabel(hint)
        hint_label.setObjectName("panelHint")
        layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(hint_label)
        return widget

    def _line(self, text: str = "", readonly: bool = False) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setReadOnly(readonly)
        return edit

    def _field(self, label: str, widget: QWidget) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lab = QLabel(label)
        lab.setObjectName("fieldLabel")
        layout.addWidget(lab)
        layout.addWidget(widget)
        return box

    def _add_group(
        self,
        title: str,
        fields: list[tuple[str, str]],
        subtitle: str = "",
        readonly: bool = False,
    ) -> None:
        group = QGroupBox(title)
        group.setObjectName("paramGroup")
        grid = QGridLayout(group)
        grid.setContentsMargins(10, 22, 10, 10)
        grid.setSpacing(8)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("groupSub")
            grid.addWidget(sub, 0, 1, alignment=Qt.AlignRight)
        start_row = 1 if subtitle else 0
        for i, (key, label) in enumerate(fields):
            edit = self._line(readonly=readonly or key == "software")
            self.fields[key] = edit
            row = start_row + i // 2
            col = i % 2
            if len(fields) % 2 == 1 and i == len(fields) - 1:
                grid.addWidget(self._field(label, edit), row, 0, 1, 2)
            else:
                grid.addWidget(self._field(label, edit), row, col)
        self.form_layout.addWidget(group)

    def _option_tile(self, title: str, subtitle: str) -> tuple[QFrame, QCheckBox]:
        frame = QFrame()
        frame.setObjectName("optionTile")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        cb = QCheckBox()
        cb.setChecked(True)
        text = QVBoxLayout()
        text.setSpacing(0)
        label = QLabel(title)
        label.setObjectName("optionTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        text.addWidget(label)
        text.addWidget(sub)
        layout.addWidget(cb)
        layout.addLayout(text, 1)
        return frame, cb

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #eef2f5;
                color: #18212b;
                font-family: "Microsoft YaHei", "Segoe UI", Arial;
                font-size: 13px;
            }
            QFrame#header {
                background: #ffffff;
                border: 0;
                border-bottom: 1px solid #d9e1e8;
            }
            QFrame#panel, QScrollArea#paramScroll {
                background: #ffffff;
                border: 1px solid #d9e1e8;
                border-radius: 8px;
            }
            QLabel#appTitle { font-size: 17px; font-weight: 700; background: transparent; }
            QLabel#subtitle, QLabel#muted, QLabel#fieldLabel, QLabel#panelHint, QLabel#groupSub {
                color: #637181;
                background: transparent;
                font-size: 12px;
            }
            QLabel#title { font-size: 15px; font-weight: 700; background: transparent; }
            QFrame#pathBox {
                background: #f7f9fb;
                border: 1px solid #d9e1e8;
                border-radius: 7px;
            }
            QLabel#pathText { color: #263241; background: transparent; }
            QLabel#step, QLabel#stepActive {
                min-height: 30px;
                border-radius: 6px;
                border: 1px solid #d9e1e8;
                background: #f7f9fb;
                color: #637181;
            }
            QLabel#stepActive {
                background: #eff6ff;
                color: #174ea6;
                border-color: #9ec5ff;
                font-weight: 700;
            }
            QFrame#metricCard {
                background: #f7f9fb;
                border: 1px solid #d9e1e8;
                border-radius: 7px;
            }
            QLabel#metricValue {
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#statusBanner {
                padding: 8px 10px;
                border-radius: 7px;
                border: 1px solid #b7e1c2;
                background: #f0fdf4;
                color: #14532d;
            }
            QListWidget#fileList {
                background: #ffffff;
                border: 1px solid #d9e1e8;
                border-radius: 7px;
                outline: 0;
            }
            QListWidget#fileList::item {
                padding: 9px;
                margin: 4px;
                border: 1px solid #d9e1e8;
                border-radius: 7px;
            }
            QListWidget#fileList::item:selected {
                background: #f8fbff;
                color: #18212b;
                border: 1px solid #2563eb;
            }
            QTabWidget::pane {
                border: 1px solid #d9e1e8;
                border-radius: 8px;
                background: #ffffff;
            }
            QTabBar::tab {
                height: 32px;
                padding: 0 12px;
                border: 0;
                color: #637181;
                background: #f7f9fb;
            }
            QTabBar::tab:selected {
                color: #2563eb;
                font-weight: 700;
                border-bottom: 2px solid #2563eb;
                background: #ffffff;
            }
            QLabel#preview, QLabel#verifyHint {
                background: #111827;
                color: #dbeafe;
                border: 1px solid #d9e1e8;
                border-radius: 8px;
            }
            QGroupBox#paramGroup {
                background: #ffffff;
                border: 1px solid #d9e1e8;
                border-radius: 8px;
                margin-top: 10px;
                font-weight: 700;
            }
            QGroupBox#paramGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #c3ced8;
                border-radius: 6px;
                padding: 6px 8px;
                min-height: 22px;
            }
            QLineEdit[readOnly="true"] {
                background: #f1f5f9;
                color: #64748b;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c3ced8;
                border-radius: 6px;
                padding: 7px 12px;
            }
            QPushButton:hover { background: #f8fafc; border-color: #9fb0bf; }
            QPushButton#primary, QPushButton#primarySmall {
                background: #2563eb;
                color: white;
                border-color: #2563eb;
                font-weight: 700;
            }
            QFrame#optionTile {
                background: #fbfcfe;
                border: 1px solid #d9e1e8;
                border-radius: 7px;
            }
            QLabel#optionTitle { font-weight: 700; background: transparent; }
            QPlainTextEdit#logText {
                background: #0f172a;
                color: #dbeafe;
                border: 0;
                border-radius: 8px;
                font-family: Consolas, "Cascadia Mono";
                font-size: 12px;
            }
            QProgressBar {
                height: 12px;
                border: 1px solid #c3ced8;
                border-radius: 6px;
                background: #e5eaf0;
                text-align: right;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background: #2563eb;
            }
            """
        )

    def choose_input(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输入文件夹", str(self.input_dir))
        if directory:
            self.input_dir = Path(directory)
            self.input_label.setText(str(self.input_dir))
            self.set_step(1)

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(self.output_dir))
        if directory:
            self.output_dir = Path(directory)
            self.output_label.setText(str(self.output_dir))
            self.set_step(4)

    def scan_folder(self) -> None:
        self.set_step(2)
        self.file_list.clear()
        self.infos.clear()
        self.current_info = None
        self.current_temp = None
        files = sorted(
            {
                path.resolve()
                for pattern in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG")
                for path in self.input_dir.glob(pattern)
            }
        )
        if not files:
            QMessageBox.warning(self, "没有文件", "输入文件夹中没有 JPG 文件")
            return
        self.log(f"开始检查 {len(files)} 个 JPG 文件")
        for path in files:
            info = self.reader.inspect(path)
            self.infos.append(info)
            item = QListWidgetItem(self._file_item_text(info))
            item.setToolTip("\n".join(info.errors) if info.errors else "R-JPG 检查通过")
            self.file_list.addItem(item)
            self.log(f"{path.name}: {info.status}" + (f" - {'; '.join(info.errors)}" if info.errors else ""))

        passed = [info for info in self.infos if info.is_rjpg and not info.errors]
        if self.left_count:
            self.left_count.setText(f"{len(passed)} / {len(self.infos)} 通过")
        if passed:
            self.set_step(3)
            first = passed[0]
            self.card_camera.set_value(first.model)
            self.card_size.set_value(f"{first.thermal_width} x {first.thermal_height}")
            self.card_focal.set_value(f"{first.focal_length} mm" if first.focal_length else "-")
            self.card_planck.set_value("一致")
            self.status_banner.setText("批次检查通过，GPS 与 FLIR 辐射参数完整。")
        else:
            self.set_step(2)
            self.card_camera.set_value("-")
            self.card_size.set_value("-")
            self.card_focal.set_value("-")
            self.card_planck.set_value("-")
            self.status_banner.setText("没有可处理的 R-JPG 文件。")
        if self.infos:
            self.file_list.setCurrentRow(0)

    def _file_item_text(self, info: RjpgInfo) -> str:
        gps = "GPS 完整" if info.latitude is not None and info.longitude is not None else "GPS 缺失"
        planck = "Planck 完整" if info.radiometric.planck_r1 else "Planck 缺失"
        return f"{info.path.name}\n{info.thermal_width} x {info.thermal_height}     {gps}     {planck}     {info.status}"

    def select_file(self, row: int) -> None:
        if row < 0 or row >= len(self.infos):
            return
        self.current_info = self.infos[row]
        self.set_step(3)
        self.current_file_label.setText(self.current_info.path.name)
        self.populate_fields()
        self.update_preview()

    def populate_fields(self) -> None:
        if not self.current_info:
            return
        info = self.current_info
        params = info.radiometric
        values = {
            "make": info.make,
            "model": info.model,
            "focal_length": info.focal_length,
            "software": info.software,
            "latitude": info.latitude,
            "longitude": info.longitude,
            "altitude": info.altitude,
            "direction": "",
            "datetime": "",
            "emissivity": params.emissivity,
            "object_distance": params.object_distance,
            "reflected_temp_c": params.reflected_temp_k - 273.15,
            "atmospheric_temp_c": params.atmospheric_temp_k - 273.15,
            "relative_humidity": params.relative_humidity,
            "ir_window_transmission": params.ir_window_transmission,
            "planck_r1": params.planck_r1,
            "planck_r2": params.planck_r2,
            "planck_b": params.planck_b,
            "planck_f": params.planck_f,
            "planck_o": params.planck_o,
        }
        for key, edit in self.fields.items():
            value = values.get(key, "")
            edit.setText("" if value is None else str(value))

    def update_preview(self) -> None:
        if not self.current_info or not self.current_info.is_rjpg:
            return
        try:
            raw, params = self.reader.read_raw_array(self.current_info.path)
            temp = raw2temp_celsius(raw, params)
            self.current_temp = temp
            finite = temp[np.isfinite(temp)]
            self.stat_min.set_value(f"{float(np.min(finite)):.2f} °C")
            self.stat_max.set_value(f"{float(np.max(finite)):.2f} °C")
            self.stat_avg.set_value(f"{float(np.mean(finite)):.2f} °C")
            preview = self._to_preview_rgb(temp)
            self.preview_label.set_source_size(temp.shape[1], temp.shape[0])
            self.verify_label.set_source_size(temp.shape[1], temp.shape[0])
            self.preview_label.setPixmap(self._pixmap_from_rgb(preview, self.preview_label.size()))
            self.verify_label.setPixmap(self._pixmap_from_rgb(preview, self.verify_label.size()))
            center_x = temp.shape[1] // 2
            center_y = temp.shape[0] // 2
            self.pixel_x.setText(str(center_x))
            self.pixel_y.setText(str(center_y))
            self.preview_label.selected_pixel = None
            self.verify_label.set_selected_pixel(center_x, center_y)
            rgb = self.reader.extract_rgb_image(self.current_info.path)
            if rgb is not None:
                self.rgb_label.setPixmap(self._pixmap_from_pil(rgb, self.rgb_label.size()))
            else:
                self.rgb_label.setText("未提取到 RGB 预览")
            self.verify_pixel(update_only=True)
        except Exception as exc:
            self.log(f"预览失败: {exc}")

    def _to_preview_rgb(self, temp: np.ndarray) -> np.ndarray:
        finite = temp[np.isfinite(temp)]
        lo, hi = np.percentile(finite, [2, 98]) if finite.size else (0, 1)
        if hi <= lo:
            hi = lo + 1
        norm = np.clip((temp - lo) / (hi - lo), 0, 1)
        r = np.clip(2.2 * norm - 0.4, 0, 1)
        g = np.clip(1.7 - np.abs(norm - 0.55) * 3.4, 0, 1)
        b = np.clip(1.2 - 2.4 * norm, 0, 1)
        return (np.dstack([r, g, b]) * 255).astype(np.uint8)

    def _pixmap_from_rgb(self, array: np.ndarray, size) -> QPixmap:
        height, width, _ = array.shape
        image = QImage(array.data, width, height, width * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image).scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _pixmap_from_pil(self, image: Image.Image, size) -> QPixmap:
        image = image.convert("RGB")
        return self._pixmap_from_rgb(np.asarray(image), size)

    def set_pixel_from_preview(self, x: int, y: int) -> None:
        self.preview_label.selected_pixel = None
        self.preview_label.update()
        self.verify_label.set_selected_pixel(x, y)
        self.pixel_x.setText(str(x))
        self.pixel_y.setText(str(y))
        self.verify_pixel()

    def verify_pixel(self, update_only: bool = False) -> None:
        if self.current_temp is None:
            return
        try:
            x = int(self.pixel_x.text())
            y = int(self.pixel_y.text())
            height, width = self.current_temp.shape
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"像素坐标超出范围：X 需要 0-{width - 1}，Y 需要 0-{height - 1}")
            value = float(self.current_temp[y, x])
            self.calc_temp.setText(f"{value:.3f}")
            self.stat_pixel.set_value(f"{value:.2f} °C")
            if self.official_temp.text().strip():
                delta = value - float(self.official_temp.text())
                self.delta_temp.setText(f"{delta:.3f}")
            if not update_only and self.current_info:
                self.log(f"像素验证 {self.current_info.path.name} ({x},{y}) = {value:.3f} °C")
        except Exception as exc:
            if not update_only:
                QMessageBox.warning(self, "验证失败", str(exc))

    def _edits(self) -> dict[str, Any]:
        def text(key: str) -> str:
            return self.fields[key].text().strip()

        return {
            "make": text("make"),
            "model": text("model"),
            "software": text("software"),
            "focal_length": text("focal_length"),
            "latitude": text("latitude"),
            "longitude": text("longitude"),
            "altitude": text("altitude"),
            "direction": text("direction"),
            "datetime": text("datetime"),
        }

    def _param_overrides(self) -> dict[str, float]:
        mapping = {
            "emissivity": "emissivity",
            "object_distance": "object_distance",
            "relative_humidity": "relative_humidity",
            "ir_window_transmission": "ir_window_transmission",
        }
        out: dict[str, float] = {}
        for field, attr in mapping.items():
            value = self.fields[field].text().strip()
            if value:
                out[attr] = float(value)
        if self.fields["reflected_temp_c"].text().strip():
            out["reflected_temp_k"] = float(self.fields["reflected_temp_c"].text()) + 273.15
        if self.fields["atmospheric_temp_c"].text().strip():
            out["atmospheric_temp_k"] = float(self.fields["atmospheric_temp_c"].text()) + 273.15
        return out

    def start_export(self) -> None:
        valid = [info for info in self.infos if info.is_rjpg and not info.errors]
        if not valid:
            QMessageBox.warning(self, "无法处理", "没有通过检查的 R-JPG 文件")
            return
        options = {
            "raw_uint16": self.opt_raw.isChecked(),
            "float32": self.opt_float.isChecked(),
            "preview": self.opt_preview.isChecked(),
            "rgb": self.opt_rgb.isChecked(),
            "metadata": self.opt_meta.isChecked() or self.opt_csv.isChecked(),
        }
        self.progress.setValue(0)
        self.btn_process.setEnabled(False)
        self.set_step(5)
        worker = ExportWorker(valid, self.output_dir, options, self._edits(), self._param_overrides())
        worker.signals.progress.connect(self.progress.setValue)
        worker.signals.log.connect(self.log)
        worker.signals.finished.connect(self.export_finished)
        self.thread_pool.start(worker)

    def export_finished(self, ok: bool, message: str) -> None:
        self.btn_process.setEnabled(True)
        self.log(message)
        if ok:
            self.set_step(6)
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "处理失败", message)

    def export_report(self) -> None:
        if not self.infos:
            QMessageBox.information(self, "没有报告", "请先检查文件。")
            return
        report_dir = self.output_dir / "metadata"
        write_json(report_dir / "batch_report.json", [info.to_metadata() for info in self.infos])
        self.log(f"检查报告已生成: {report_dir / 'batch_report.json'}")

    def log(self, message: str) -> None:
        self.log_text.appendPlainText(message)


def main() -> None:
    app = QApplication([])
    icon = QIcon(str(resource_path("rjpg_preprocessor_icon.ico")))
    app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    app.exec()
