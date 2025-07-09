import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QSpinBox,
    QDoubleSpinBox, QFontComboBox, QCheckBox, QPushButton, QGraphicsScene,
    QGraphicsPathItem, QDialog, QGraphicsView, QGridLayout, QFileDialog,
    QGraphicsLineItem, QGraphicsItemGroup, QGraphicsEllipseItem
)
from PyQt5.QtGui import (QFont, QPainterPath, QPen, QFontMetrics, QPainter,
                         QPainterPathStroker, QTransform, QKeySequence)
from PyQt5.QtCore import Qt, QPointF, QSettings, QTimer, QTime
import ezdxf

# Custom QPushButton for move actions.
class MoveButton(QPushButton):
    def __init__(self, text, group, dx, dy, move_callback, parent=None):
        super().__init__(text, parent)
        self.group = group  # "letter", "circle_left", or "circle_right"
        self.dx = dx
        self.dy = dy
        self.move_callback = move_callback
        self.long_press_threshold = 300  # ms threshold for long press detection
        self.repeat_interval = 100       # ms repeat interval during long press
        self.timer = QTimer(self)
        self.timer.setInterval(self.repeat_interval)
        self.timer.timeout.connect(self.on_timeout)
        self.single_shot_timer = QTimer(self)
        self.single_shot_timer.setSingleShot(True)
        self.single_shot_timer.timeout.connect(self.start_long_press)

    def mousePressEvent(self, event):
        self.single_shot_timer.start(self.long_press_threshold)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.single_shot_timer.isActive():
            self.single_shot_timer.stop()
            self.move_callback(self.group, self.dx, self.dy)
        else:
            self.timer.stop()
        super().mouseReleaseEvent(event)

    def start_long_press(self):
        self.timer.start()

    def on_timeout(self):
        self.move_callback(self.group, self.dx, self.dy)

# Custom GraphicsView with rubber band selection.
class GraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self._pan = False

    def wheelEvent(self, event):
        zoomFactor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(zoomFactor, zoomFactor)
        else:
            self.scale(1/zoomFactor, 1/zoomFactor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan = True
            self._panStartX = event.x()
            self._panStartY = event.y()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan:
            dx = event.x() - self._panStartX
            dy = event.y() - self._panStartY
            self._panStartX = event.x()
            self._panStartY = event.y()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - dx)
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - dy)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan = False
            self.setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

# LetterItem draws a dashed (or solid when selected) rectangle.
class LetterItem(QGraphicsPathItem):
    line_thickness = 1.0
    show_frame = True

    def __init__(self, path, letter):
        super().__init__(path)
        self.letter = letter
        self.setFlags(QGraphicsPathItem.ItemIsSelectable)
        self.setPen(QPen(Qt.NoPen))

    def paint(self, painter, option, widget):
        if not LetterItem.show_frame:
            return
        rect = self.path().boundingRect()
        if self.isSelected():
            pen = QPen(Qt.red, LetterItem.line_thickness, Qt.SolidLine)
        else:
            pen = QPen(Qt.black, LetterItem.line_thickness, Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect)

    def shape(self):
        path = self.path()
        if path.isEmpty():
            return QPainterPath()
        stroker = QPainterPathStroker()
        stroker.setWidth(5)
        return stroker.createStroke(path)

# SettingsDialog for configuration.
class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Ayarlar")
        self.setGeometry(100, 100, 500, 600)
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        self.labelFont = QLabel("Varsayılan Font:", self)
        self.labelFont.setGeometry(20, 10, 150, 20)
        self.defaultFontCombo = QFontComboBox(self)
        self.defaultFontCombo.setGeometry(20, 30, 200, 30)

        self.labelFontSize = QLabel("Varsayılan Font Boyutu:", self)
        self.labelFontSize.setGeometry(20, 70, 150, 20)
        self.defaultFontSizeSpin = QSpinBox(self)
        self.defaultFontSizeSpin.setRange(5, 100)
        self.defaultFontSizeSpin.setGeometry(20, 90, 100, 30)

        self.labelCircleDiam = QLabel("Varsayılan Dış Çap:", self)
        self.labelCircleDiam.setGeometry(20, 130, 150, 20)
        self.defaultCircleDiameterSpin = QSpinBox(self)
        self.defaultCircleDiameterSpin.setRange(1, 200)
        self.defaultCircleDiameterSpin.setGeometry(20, 150, 100, 30)

        self.labelInnerRatio = QLabel("Varsayılan Delik Oranı (%):", self)
        self.labelInnerRatio.setGeometry(20, 190, 180, 20)
        self.defaultInnerRatioSpin = QSpinBox(self)
        self.defaultInnerRatioSpin.setRange(10, 100)
        self.defaultInnerRatioSpin.setGeometry(20, 210, 100, 30)

        self.labelMargin = QLabel("Margin:", self)
        self.labelMargin.setGeometry(20, 250, 150, 20)
        self.marginSpin = QDoubleSpinBox(self)
        self.marginSpin.setDecimals(4)
        self.marginSpin.setRange(0.0, 100)
        self.marginSpin.setSingleStep(0.01)
        self.marginSpin.setGeometry(20, 270, 100, 30)

        self.labelDashedThickness = QLabel("Kesikli Çizgi Kalınlığı:", self)
        self.labelDashedThickness.setGeometry(20, 310, 180, 20)
        self.dashedThicknessSpin = QDoubleSpinBox(self)
        self.dashedThicknessSpin.setDecimals(2)
        self.dashedThicknessSpin.setRange(0.1, 10)
        self.dashedThicknessSpin.setSingleStep(0.1)
        self.dashedThicknessSpin.setGeometry(20, 330, 100, 30)

        self.defaultBoldCB = QCheckBox("Varsayılan Kalın", self)
        self.defaultBoldCB.setGeometry(250, 30, 150, 30)
        self.defaultItalicCB = QCheckBox("Varsayılan İtalik", self)
        self.defaultItalicCB.setGeometry(250, 70, 150, 30)

        self.labelLineThickness = QLabel("Union Çizgi Kalınlığı:", self)
        self.labelLineThickness.setGeometry(250, 110, 180, 20)
        self.defaultLineThicknessSpin = QDoubleSpinBox(self)
        self.defaultLineThicknessSpin.setDecimals(2)
        self.defaultLineThicknessSpin.setRange(0.1, 10)
        self.defaultLineThicknessSpin.setSingleStep(0.1)
        self.defaultLineThicknessSpin.setGeometry(250, 130, 100, 30)

        self.labelLetterStep = QLabel("Harf Kaydırma Adımı:", self)
        self.labelLetterStep.setGeometry(250, 170, 180, 20)
        self.letterStepSpin = QDoubleSpinBox(self)
        self.letterStepSpin.setDecimals(2)
        self.letterStepSpin.setRange(0.01, 50)
        self.letterStepSpin.setSingleStep(0.01)
        self.letterStepSpin.setGeometry(250, 190, 100, 30)

        self.labelCircleStep = QLabel("Yuvarlak Kaydırma Adımı:", self)
        self.labelCircleStep.setGeometry(250, 230, 180, 20)
        self.circleStepSpin = QDoubleSpinBox(self)
        self.circleStepSpin.setDecimals(2)
        self.circleStepSpin.setRange(0.01, 50)
        self.circleStepSpin.setSingleStep(0.01)
        self.circleStepSpin.setGeometry(250, 250, 100, 30)
        
        # New setting: Blue circle pen thickness.
        self.labelBluePen = QLabel("Mavi Yuvarlak Çizgi Kalınlığı:", self)
        self.labelBluePen.setGeometry(20, 370, 200, 20)
        self.blueCirclePenSpin = QDoubleSpinBox(self)
        self.blueCirclePenSpin.setDecimals(2)
        self.blueCirclePenSpin.setRange(0.01, 5)
        self.blueCirclePenSpin.setSingleStep(0.01)
        self.blueCirclePenSpin.setGeometry(20, 390, 100, 30)

        self.okButton = QPushButton("Kaydet", self)
        self.okButton.setGeometry(100, 450, 80, 30)
        self.okButton.clicked.connect(self.accept)
        self.cancelButton = QPushButton("İptal", self)
        self.cancelButton.setGeometry(200, 450, 80, 30)
        self.cancelButton.clicked.connect(self.reject)

    def load_settings(self):
        self.defaultFontCombo.setCurrentFont(QFont(self.settings.value("defaultFont", "Arial")))
        self.defaultFontSizeSpin.setValue(int(self.settings.value("defaultFontSize", 40)))
        self.defaultCircleDiameterSpin.setValue(int(self.settings.value("defaultCircleDiameter", 60)))
        self.defaultInnerRatioSpin.setValue(int(self.settings.value("defaultInnerRatio", 60)))
        self.marginSpin.setValue(float(self.settings.value("margin", 0.01)))
        self.dashedThicknessSpin.setValue(float(self.settings.value("dashedThickness", 1.0)))
        self.defaultBoldCB.setChecked(self.settings.value("defaultBold", "false") == "true")
        self.defaultItalicCB.setChecked(self.settings.value("defaultItalic", "false") == "true")
        self.defaultLineThicknessSpin.setValue(float(self.settings.value("defaultLineThickness", 1.0)))
        self.letterStepSpin.setValue(float(self.settings.value("letterStep", 5.0)))
        self.circleStepSpin.setValue(float(self.settings.value("circleStep", 5.0)))
        self.blueCirclePenSpin.setValue(float(self.settings.value("blueCirclePenThickness", 0.1)))

    def save_settings(self):
        self.settings.setValue("defaultFont", self.defaultFontCombo.currentFont().family())
        self.settings.setValue("defaultFontSize", self.defaultFontSizeSpin.value())
        self.settings.setValue("defaultCircleDiameter", self.defaultCircleDiameterSpin.value())
        self.settings.setValue("defaultInnerRatio", self.defaultInnerRatioSpin.value())
        self.settings.setValue("margin", self.marginSpin.value())
        self.settings.setValue("dashedThickness", self.dashedThicknessSpin.value())
        self.settings.setValue("defaultBold", "true" if self.defaultBoldCB.isChecked() else "false")
        self.settings.setValue("defaultItalic", "true" if self.defaultItalicCB.isChecked() else "false")
        self.settings.setValue("defaultLineThickness", self.defaultLineThicknessSpin.value())
        self.settings.setValue("letterStep", self.letterStepSpin.value())
        self.settings.setValue("circleStep", self.circleStepSpin.value())
        self.settings.setValue("blueCirclePenThickness", self.blueCirclePenSpin.value())

# Main window.
class LaserNecklaceDesigner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lazer Kolye Tasarımı")
        self.setGeometry(50, 50, 920, 750)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        self.settings = QSettings("MyCompany", "LaserNecklaceDesigner")
        self.letter_adjustment_step = 5.0
        self.circle_adjustment_step = 5.0
        self.left_circle_offset_x = 0
        self.left_circle_offset_y = 0
        self.right_circle_offset_x = 0
        self.right_circle_offset_y = 0
        self.margin_setting = 0.01
        self.dashed_line_thickness = 1.0
        self.letter_items = []
        self.current_text = ""
        self.current_font_size = self.font_size_default()
        self.custom_positions = []  # Preserve custom positions.
        self.undo_stack = []
        self.redo_stack = []
        self.save_path = self.settings.value("dxfSavePath", "")
        self.center_cross_item = None  # Will hold the center cross group.
        # New movable circle items.
        self.left_circle_item = None
        self.right_circle_item = None
        self.blue_circle_pen_thickness = 0.1
        self.init_ui()
        self.load_defaults()

    def font_size_default(self):
        return int(self.settings.value("defaultFontSize", 40))

    def init_ui(self):
        self.central = QWidget(self)
        self.setCentralWidget(self.central)
        
        # Top controls.
        self.label_text = QLabel("Yazı:", self.central)
        self.label_text.setGeometry(10, 10, 50, 25)
        self.text_line_edit = QLineEdit(self.central)
        self.text_line_edit.setGeometry(70, 10, 300, 25)
        self.text_line_edit.setPlaceholderText("Yazı giriniz...")
        self.text_line_edit.textChanged.connect(self.rebuild_letters)
        
        self.settings_button = QPushButton("Ayarlar", self.central)
        self.settings_button.setGeometry(380, 10, 80, 25)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        
        self.label_font = QLabel("Font:", self.central)
        self.label_font.setGeometry(10, 45, 50, 25)
        self.font_combo = QFontComboBox(self.central)
        self.font_combo.setGeometry(70, 45, 200, 25)
        self.font_combo.currentFontChanged.connect(self.rebuild_letters)
        
        self.label_font_size = QLabel("Font Boyutu:", self.central)
        self.label_font_size.setGeometry(280, 45, 100, 25)
        self.font_size_spin = QSpinBox(self.central)
        self.font_size_spin.setRange(5, 100)
        self.font_size_spin.setGeometry(380, 45, 80, 25)
        self.font_size_spin.setValue(self.current_font_size)
        self.font_size_spin.valueChanged.connect(self.rebuild_letters)
        
        self.bold_checkbox = QCheckBox("Kalın", self.central)
        self.bold_checkbox.setGeometry(10, 80, 80, 25)
        self.bold_checkbox.stateChanged.connect(self.rebuild_letters)
        self.italic_checkbox = QCheckBox("İtalik", self.central)
        self.italic_checkbox.setGeometry(100, 80, 80, 25)
        self.italic_checkbox.stateChanged.connect(self.rebuild_letters)
        
        self.label_circle_diam = QLabel("Dış Yuvarlak Çapı:", self.central)
        self.label_circle_diam.setGeometry(10, 115, 150, 25)
        self.circle_diameter_spin = QSpinBox(self.central)
        self.circle_diameter_spin.setRange(1, 200)
        self.circle_diameter_spin.setGeometry(160, 115, 80, 25)
        self.circle_diameter_spin.setValue(int(self.settings.value("defaultCircleDiameter", 60)))
        self.circle_diameter_spin.valueChanged.connect(self.update_union_overlay)
        
        self.label_inner_ratio = QLabel("İç Yuvarlak (%) (Delik Oranı):", self.central)
        self.label_inner_ratio.setGeometry(250, 115, 200, 25)
        self.inner_ratio_spin = QSpinBox(self.central)
        self.inner_ratio_spin.setRange(10, 100)
        self.inner_ratio_spin.setGeometry(460, 115, 80, 25)
        self.inner_ratio_spin.setValue(int(self.settings.value("defaultInnerRatio", 60)))
        self.inner_ratio_spin.valueChanged.connect(self.update_union_overlay)
        
        # Checkbox for letter frame.
        self.frame_visibility_checkbox = QCheckBox("Harf Çerçevesi", self.central)
        self.frame_visibility_checkbox.setGeometry(550, 10, 150, 25)
        self.frame_visibility_checkbox.setChecked(True)
        self.frame_visibility_checkbox.toggled.connect(self.toggle_frame_visibility)
        
        # Graphics view.
        self.scene = QGraphicsScene()
        self.graphics_view = GraphicsView(self.scene, self.central)
        self.graphics_view.setGeometry(10, 150, 600, 400)
        
        # Letter arrow controls.
        self.letter_arrow_widget = QWidget(self.central)
        self.letter_arrow_widget.setGeometry(620, 150, 100, 100)
        letter_arrow_layout = QGridLayout(self.letter_arrow_widget)
        self.arrow_up = MoveButton("↑", "letter", 0, -self.letter_adjustment_step, self.move_callback)
        self.arrow_left = MoveButton("←", "letter", -self.letter_adjustment_step, 0, self.move_callback)
        self.arrow_right = MoveButton("→", "letter", self.letter_adjustment_step, 0, self.move_callback)
        self.arrow_down = MoveButton("↓", "letter", 0, self.letter_adjustment_step, self.move_callback)
        letter_arrow_layout.addWidget(self.arrow_up, 0, 1)
        letter_arrow_layout.addWidget(self.arrow_left, 1, 0)
        letter_arrow_layout.addWidget(self.arrow_right, 1, 2)
        letter_arrow_layout.addWidget(self.arrow_down, 2, 1)
        
        # Zoom buttons.
        self.zoom_in_button = QPushButton("Yakınlaştır", self.central)
        self.zoom_in_button.setGeometry(620, 260, 100, 30)
        self.zoom_in_button.clicked.connect(self.zoom_in_selected_letters)
        self.zoom_out_button = QPushButton("Uzaklaştır", self.central)
        self.zoom_out_button.setGeometry(620, 300, 100, 30)
        self.zoom_out_button.clicked.connect(self.zoom_out_selected_letters)
        
        # Circle arrow controls.
        self.circle_widget = QWidget(self.central)
        self.circle_widget.setGeometry(620, 350, 100, 150)
        circle_layout = QGridLayout(self.circle_widget)
        # Left circle controls.
        circle_layout.addWidget(QLabel("Sol Yuvarlak:"), 0, 0, 1, 2)
        self.circle_up = MoveButton("↑", "circle_left", 0, -self.circle_adjustment_step, self.move_callback)
        self.circle_down = MoveButton("↓", "circle_left", 0, self.circle_adjustment_step, self.move_callback)
        self.circle_left = MoveButton("←", "circle_left", -self.circle_adjustment_step, 0, self.move_callback)
        self.circle_right = MoveButton("→", "circle_left", self.circle_adjustment_step, 0, self.move_callback)
        circle_layout.addWidget(self.circle_up, 1, 0)
        circle_layout.addWidget(self.circle_down, 1, 1)
        circle_layout.addWidget(self.circle_left, 2, 0)
        circle_layout.addWidget(self.circle_right, 2, 1)
        # Right circle controls.
        circle_layout.addWidget(QLabel("Sağ Yuvarlak:"), 3, 0, 1, 2)
        self.circle2_up = MoveButton("↑", "circle_right", 0, -self.circle_adjustment_step, self.move_callback)
        self.circle2_down = MoveButton("↓", "circle_right", 0, self.circle_adjustment_step, self.move_callback)
        self.circle2_left = MoveButton("←", "circle_right", -self.circle_adjustment_step, 0, self.move_callback)
        self.circle2_right = MoveButton("→", "circle_right", self.circle_adjustment_step, 0, self.move_callback)
        circle_layout.addWidget(self.circle2_up, 4, 0)
        circle_layout.addWidget(self.circle2_down, 4, 1)
        circle_layout.addWidget(self.circle2_left, 5, 0)
        circle_layout.addWidget(self.circle2_right, 5, 1)
        
        # Bottom controls.
        self.dxf_dim_label = QLabel("DXF Boyutları: X = 0.00, Y = 0.00", self.central)
        self.dxf_dim_label.setGeometry(10, 560, 400, 30)
        self.save_button = QPushButton("DXF olarak kaydet", self.central)
        self.save_button.setGeometry(10, 600, 150, 30)
        self.save_button.clicked.connect(self.export_to_dxf)
        
        # QLineEdit for save file path.
        self.save_path_line_edit = QLineEdit(self.central)
        self.save_path_line_edit.setGeometry(170, 600, 250, 30)
        self.save_path_line_edit.setPlaceholderText("Dosya yolu...")
        self.save_path_line_edit.setReadOnly(True)
        # Browse button.
        self.browse_button = QPushButton("Browse", self.central)
        self.browse_button.setGeometry(430, 600, 100, 30)
        self.browse_button.clicked.connect(self.browse_save_path)
        
        # Mirror checkboxes.
        self.x_mirror_checkbox = QCheckBox("X Mirror", self.central)
        self.x_mirror_checkbox.setGeometry(10, 640, 100, 25)
        self.y_mirror_checkbox = QCheckBox("Y Mirror", self.central)
        self.y_mirror_checkbox.setGeometry(120, 640, 100, 25)
        # Checkbox to toggle center cross.
        self.center_cross_checkbox = QCheckBox("Show Center Cross", self.central)
        self.center_cross_checkbox.setGeometry(230, 640, 150, 25)
        self.center_cross_checkbox.toggled.connect(lambda _: self.update_union_overlay())
        
        # Status label.
        self.status_label = QLabel("", self.central)
        self.status_label.setGeometry(10, 680, 600, 25)

    def keyPressEvent(self, event):
        # Use WASD keys to move selected letters or selected circles.
        if event.key() in [Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D]:
            dx, dy = 0, 0
            if event.key() == Qt.Key_W:
                dy = -self.letter_adjustment_step
            elif event.key() == Qt.Key_S:
                dy = self.letter_adjustment_step
            elif event.key() == Qt.Key_A:
                dx = -self.letter_adjustment_step
            elif event.key() == Qt.Key_D:
                dx = self.letter_adjustment_step
            # Check if any letter is selected.
            letters_selected = any(isinstance(item, LetterItem) for item in self.scene.selectedItems())
            for item in self.scene.selectedItems():
                if isinstance(item, LetterItem):
                    item.setPos(item.pos() + QPointF(dx, dy))
                elif item is self.left_circle_item or item is self.right_circle_item:
                    # If any letter is selected, don't update circle offsets; they follow union_overlay update.
                    if not letters_selected:
                        if item is self.left_circle_item:
                            self.left_circle_offset_x += dx
                            self.left_circle_offset_y += dy
                        else:
                            self.right_circle_offset_x += dx
                            self.right_circle_offset_y += dy
            self.update_union_overlay()
        elif event.matches(QKeySequence.Undo):
            self.undo()
        elif event.matches(QKeySequence.Redo):
            self.redo()
        else:
            super().keyPressEvent(event)

    def toggle_frame_visibility(self, checked):
        LetterItem.show_frame = checked
        for item in self.letter_items:
            item.update()

    def move_callback(self, group, dx, dy):
        if group == "letter":
            self.push_undo_state()
            self.move_selected_letter(dx, dy)
        elif group == "circle_left":
            self.move_circle(True, dx, dy)
        elif group == "circle_right":
            self.move_circle(False, dx, dy)

    def load_defaults(self):
        default_font = QFont(self.settings.value("defaultFont", "Arial"))
        self.font_combo.setCurrentFont(default_font)
        self.font_size_spin.setValue(int(self.settings.value("defaultFontSize", 40)))
        self.circle_diameter_spin.setValue(int(self.settings.value("defaultCircleDiameter", 60)))
        self.inner_ratio_spin.setValue(int(self.settings.value("defaultInnerRatio", 60)))
        self.bold_checkbox.setChecked(self.settings.value("defaultBold", "false") == "true")
        self.italic_checkbox.setChecked(self.settings.value("defaultItalic", "false") == "true")
        self.letter_adjustment_step = float(self.settings.value("letterStep", 5.0))
        self.circle_adjustment_step = float(self.settings.value("circleStep", 5.0))
        self.current_font_size = int(self.settings.value("defaultFontSize", 40))
        self.default_line_thickness = float(self.settings.value("defaultLineThickness", 1.0))
        self.margin_setting = float(self.settings.value("margin", 0.01))
        self.dashed_line_thickness = float(self.settings.value("dashedThickness", 1.0))
        LetterItem.line_thickness = self.dashed_line_thickness
        for btn in (self.arrow_up, self.arrow_down, self.arrow_left, self.arrow_right):
            btn.dx = (btn.dx/abs(btn.dx)) * self.letter_adjustment_step if btn.dx != 0 else 0
            btn.dy = (btn.dy/abs(btn.dy)) * self.letter_adjustment_step if btn.dy != 0 else 0
        for btn in (self.circle_up, self.circle_down, self.circle_left, self.circle_right,
                    self.circle2_up, self.circle2_down, self.circle2_left, self.circle2_right):
            btn.dx = (btn.dx/abs(btn.dx)) * self.circle_adjustment_step if btn.dx != 0 else 0
            btn.dy = (btn.dy/abs(btn.dy)) * self.circle_adjustment_step if btn.dy != 0 else 0
        self.save_path = self.settings.value("dxfSavePath", "")
        self.save_path_line_edit.setText(self.save_path)
        self.blue_circle_pen_thickness = float(self.settings.value("blueCirclePenThickness", 0.1))

    def open_settings_dialog(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() == QDialog.Accepted:
            dialog.save_settings()
            self.load_defaults()
            self.rebuild_letters()

    def browse_save_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "DXF Kaydet", "", "DXF Files (*.dxf)")
        if path:
            self.save_path = path
            self.save_path_line_edit.setText(path)
            self.settings.setValue("dxfSavePath", path)

    def rebuild_letters(self):
        new_text = self.text_line_edit.text()
        new_font_size = self.font_size_spin.value()
        font = self.font_combo.currentFont()
        font.setPointSize(new_font_size)
        font.setBold(self.bold_checkbox.isChecked())
        font.setItalic(self.italic_checkbox.isChecked())
        fm = QFontMetrics(font)
        # Center the full text on x-axis.
        total_width = sum(fm.horizontalAdvance(letter) for letter in new_text)
        x_cursor = -total_width / 2.0
        
        if new_text == self.current_text and len(self.letter_items) == len(new_text):
            for idx, letter_item in enumerate(self.letter_items):
                letter = new_text[idx]
                path = QPainterPath()
                path.addText(0, fm.ascent(), font, letter)
                letter_item.setPath(path)
                letter_item.setPos(x_cursor, 0)
                x_cursor += fm.horizontalAdvance(letter)
            self.update_union_overlay()
        else:
            for item in self.letter_items:
                self.scene.removeItem(item)
            self.letter_items = []
            self.custom_positions = []
            for letter in new_text:
                path = QPainterPath()
                path.addText(0, fm.ascent(), font, letter)
                letter_item = LetterItem(path, letter)
                letter_item.setPos(x_cursor, 0)
                self.scene.addItem(letter_item)
                self.letter_items.append(letter_item)
                self.custom_positions.append(QPointF(x_cursor, 0))
                x_cursor += fm.horizontalAdvance(letter)
            self.update_union_overlay()
        self.current_text = new_text
        self.current_font_size = new_font_size

    def move_selected_letter(self, dx, dy):
        for item in self.letter_items:
            if item.isSelected():
                item.setPos(item.pos() + QPointF(dx, dy))
        self.update_union_overlay()

    def move_circle(self, is_left, dx, dy):
        if is_left:
            self.left_circle_offset_x += dx
            self.left_circle_offset_y += dy
        else:
            self.right_circle_offset_x += dx
            self.right_circle_offset_y += dy
        self.update_union_overlay()

    def update_union_overlay(self):
        for item in self.scene.items():
            if isinstance(item, QGraphicsPathItem) and item.data(1) == "union":
                self.scene.removeItem(item)
        if not self.letter_items:
            return
        union_path = QPainterPath()
        first = True
        for letter_item in self.letter_items:
            if first:
                union_path = letter_item.mapToScene(letter_item.path())
                first = False
            else:
                union_path = union_path.united(letter_item.mapToScene(letter_item.path()))
        union_path.translate(self.margin_setting, self.margin_setting)
        bounds = union_path.boundingRect()
        circle_diam = self.circle_diameter_spin.value()
        outer_radius = circle_diam / 2.0
        inner_ratio = self.inner_ratio_spin.value() / 100.0
        inner_radius = outer_radius * inner_ratio
        overlap = 5
        default_left_center = QPointF(bounds.left() + overlap, bounds.top() + bounds.height()/2)
        default_right_center = QPointF(bounds.right() - overlap, bounds.top() + bounds.height()/2)
        left_circle_center = QPointF(default_left_center.x() + self.left_circle_offset_x,
                                     default_left_center.y() + self.left_circle_offset_y)
        right_circle_center = QPointF(default_right_center.x() + self.right_circle_offset_x,
                                      default_right_center.y() + self.right_circle_offset_y)
        left_outer = QPainterPath()
        left_outer.addEllipse(left_circle_center, outer_radius, outer_radius)
        right_outer = QPainterPath()
        right_outer.addEllipse(right_circle_center, outer_radius, outer_radius)
        left_inner = QPainterPath()
        left_inner.addEllipse(left_circle_center, inner_radius, inner_radius)
        right_inner = QPainterPath()
        right_inner.addEllipse(right_circle_center, inner_radius, inner_radius)
        combined = union_path.united(left_outer).united(right_outer)
        combined = combined.subtracted(left_inner).subtracted(right_inner)
        union_item = QGraphicsPathItem(combined)
        pen = QPen(Qt.black, self.default_line_thickness)
        union_item.setPen(pen)
        union_item.setData(1, "union")
        union_item.setZValue(1000)  # lower than circles
        self.scene.addItem(union_item)
        dims = combined.boundingRect()
        self.dxf_dim_label.setText("DXF Boyutları: X = {:.2f}, Y = {:.2f}".format(dims.width(), dims.height()))
        
        # Update or create movable circle items.
        if self.left_circle_item is None:
            self.left_circle_item = QGraphicsEllipseItem()
            self.left_circle_item.setFlag(QGraphicsEllipseItem.ItemIsSelectable, True)
            # Do not set ItemIsMovable so that mouse dragging is disabled.
            self.left_circle_item.setPen(QPen(Qt.blue, self.blue_circle_pen_thickness))
            self.left_circle_item.setZValue(2000)
            self.scene.addItem(self.left_circle_item)
        else:
            self.left_circle_item.setPen(QPen(Qt.blue, self.blue_circle_pen_thickness))
        self.left_circle_item.setRect(left_circle_center.x()-outer_radius, left_circle_center.y()-outer_radius, outer_radius*2, outer_radius*2)
        
        if self.right_circle_item is None:
            self.right_circle_item = QGraphicsEllipseItem()
            self.right_circle_item.setFlag(QGraphicsEllipseItem.ItemIsSelectable, True)
            # Do not allow mouse move.
            self.right_circle_item.setPen(QPen(Qt.blue, self.blue_circle_pen_thickness))
            self.right_circle_item.setZValue(2000)
            self.scene.addItem(self.right_circle_item)
        else:
            self.right_circle_item.setPen(QPen(Qt.blue, self.blue_circle_pen_thickness))
        self.right_circle_item.setRect(right_circle_center.x()-outer_radius, right_circle_center.y()-outer_radius, outer_radius*2, outer_radius*2)
        
        # Draw center cross if checkbox is checked.
        if self.center_cross_checkbox.isChecked():
            self.draw_center_cross(union_path.boundingRect().center())
        else:
            if self.center_cross_item is not None:
                self.scene.removeItem(self.center_cross_item)
                self.center_cross_item = None

    def draw_center_cross(self, center):
        if self.center_cross_item is not None:
            self.scene.removeItem(self.center_cross_item)
        hor_line = QGraphicsLineItem(center.x()-200, center.y(), center.x()+200, center.y())
        ver_line = QGraphicsLineItem(center.x(), center.y()-200, center.x(), center.y()+200)
        pen = QPen(Qt.red, 0.1, Qt.DashLine)
        hor_line.setPen(pen)
        ver_line.setPen(pen)
        group = self.scene.createItemGroup([hor_line, ver_line])
        self.center_cross_item = group

    def zoom_in_selected_letters(self):
        selected = [item for item in self.letter_items if item.isSelected()]
        if len(selected) < 2:
            return
        self.push_undo_state()
        selected.sort(key=lambda item: item.pos().x())
        left = selected[0].pos().x()
        right = selected[-1].pos().x()
        current_gap = right - left
        delta = self.letter_adjustment_step
        new_gap = max(0, current_gap - 2 * delta)
        center = (left + right) / 2.0
        new_left = center - new_gap / 2.0
        n = len(selected)
        for i, item in enumerate(selected):
            new_x = new_left + (new_gap * i / (n - 1)) if n > 1 else new_left
            item.setPos(new_x, item.pos().y())
        self.update_union_overlay()

    def zoom_out_selected_letters(self):
        selected = [item for item in self.letter_items if item.isSelected()]
        if len(selected) < 2:
            return
        self.push_undo_state()
        selected.sort(key=lambda item: item.pos().x())
        left = selected[0].pos().x()
        right = selected[-1].pos().x()
        current_gap = right - left
        delta = self.letter_adjustment_step
        new_gap = current_gap + 2 * delta
        center = (left + right) / 2.0
        new_left = center - new_gap / 2.0
        n = len(selected)
        for i, item in enumerate(selected):
            new_x = new_left + (new_gap * i / (n - 1)) if n > 1 else new_left
            item.setPos(new_x, item.pos().y())
        self.update_union_overlay()

    def push_undo_state(self):
        state = [item.pos() for item in self.letter_items]
        self.undo_stack.append(state)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        current_state = [item.pos() for item in self.letter_items]
        self.redo_stack.append(current_state)
        state = self.undo_stack.pop()
        for item, pos in zip(self.letter_items, state):
            item.setPos(pos)
        self.update_union_overlay()

    def redo(self):
        if not self.redo_stack:
            return
        current_state = [item.pos() for item in self.letter_items]
        self.undo_stack.append(current_state)
        state = self.redo_stack.pop()
        for item, pos in zip(self.letter_items, state):
            item.setPos(pos)
        self.update_union_overlay()

    def export_to_dxf(self):
        if not self.letter_items:
            return
        union_path = QPainterPath()
        first = True
        for letter_item in self.letter_items:
            if first:
                union_path = letter_item.mapToScene(letter_item.path())
                first = False
            else:
                union_path = union_path.united(letter_item.mapToScene(letter_item.path()))
        union_path.translate(self.margin_setting, self.margin_setting)
        br = union_path.boundingRect()
        sx = -1 if self.x_mirror_checkbox.isChecked() else 1
        sy = 1 if self.y_mirror_checkbox.isChecked() else -1
        dx = br.x() + br.width() if self.x_mirror_checkbox.isChecked() else 0
        centerY = br.y() + br.height()/2
        dy = 0 if self.y_mirror_checkbox.isChecked() else 2*centerY
        transform = QTransform(sx, 0, 0, sy, dx, dy)
        fixed_union = union_path * transform

        circle_diam = self.circle_diameter_spin.value()
        outer_radius = circle_diam / 2.0
        inner_ratio = self.inner_ratio_spin.value() / 100.0
        inner_radius = outer_radius * inner_ratio
        overlap = 5
        bounds = fixed_union.boundingRect()
        default_left_center = QPointF(bounds.left() + overlap, bounds.top() + bounds.height()/2)
        default_right_center = QPointF(bounds.right() - overlap, bounds.top() + bounds.height()/2)
        left_circle_center = QPointF(default_left_center.x() + self.left_circle_offset_x,
                                     default_left_center.y() + self.left_circle_offset_y)
        right_circle_center = QPointF(default_right_center.x() + self.right_circle_offset_x,
                                      default_right_center.y() + self.right_circle_offset_y)
        left_outer = QPainterPath()
        left_outer.addEllipse(left_circle_center, outer_radius, outer_radius)
        right_outer = QPainterPath()
        right_outer.addEllipse(right_circle_center, outer_radius, outer_radius)
        left_inner = QPainterPath()
        left_inner.addEllipse(left_circle_center, inner_radius, inner_radius)
        right_inner = QPainterPath()
        right_inner.addEllipse(right_circle_center, inner_radius, inner_radius)
        combined = fixed_union.united(left_outer).united(right_outer)
        combined = combined.subtracted(left_inner).subtracted(right_inner)
        polygons = combined.toSubpathPolygons()
        doc = ezdxf.new(dxfversion='R2010')
        msp = doc.modelspace()
        for poly in polygons:
            points = [(pt.x(), pt.y()) for pt in poly]
            if len(points) > 2:
                msp.add_lwpolyline(points, close=True)
        filename = self.save_path if self.save_path else "laser_necklace_design.dxf"
        doc.saveas(filename)
        self.status_label.setText(f"{filename} kaydedildi.")
        print(f"DXF dosyası '{filename}' kaydedildi.")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = LaserNecklaceDesigner()
    window.show()
    sys.exit(app.exec_())
