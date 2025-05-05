import sys
import os
import json
import threading
import time
import numpy as np
import re
import subprocess
import serial
import copy  # Added to perform deep copies of STL mesh

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QTabWidget,
    QMainWindow,
    QGridLayout,
    QLabel,
    QCheckBox,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QComboBox,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton
)
from PyQt5.QtCore import Qt
from PyQt5.QtSerialPort import QSerialPortInfo

from stl import mesh
import pyvista as pv
from pyvistaqt import QtInteractor

# ----------------------- Shared Utility Functions -----------------------
SETTINGS_FILE = "settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as file:
            return json.load(file)
    else:
        settings = {
            "line_count": 10,
            "line_spacing": 5.0,
            "x_length": 100.0,
            "feed_rate_cut": 1200.0,
            "feed_rate_rapid": 3000.0,
            "dwell_time": 100,
            "save_directory": os.getcwd(),
            "serial_port": "",
            "baud_rate": 9600,
            "zero_point": 8150
        }
        save_settings(settings)
        return settings

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as file:
        json.dump(settings, file, indent=4)

def generate_gcode(line_count, line_spacing, x_length, feed_rate_cut, feed_rate_rapid, dwell_time):
    gcode = []
    gcode.append("M6 T9")
    gcode.append("G43 H9")
    gcode.append("G54")
    gcode.append("G0 X0.0000 Y0.0000")
    gcode.append(f"G4 P{dwell_time}")

    x_start = -x_length / 2
    x_end = x_length / 2
    y_total_length = (line_count - 1) * line_spacing
    y_start = y_total_length / 2

    for i in range(line_count):
        y_pos = y_start - i * line_spacing
        gcode.append(f"G0 X{x_start:.3f} Y{y_pos:.3f} Z1.0000")
        gcode.append(f"G1 Z0.0000 F{feed_rate_cut:.1f}")
        gcode.append(f"G4 P{dwell_time}")
        gcode.append("M63")
        gcode.append(f"G1 X{x_end:.3f} F{feed_rate_rapid:.1f}")
        gcode.append("M64")
        gcode.append("G0 Z1.0000")

    gcode.append("G0 Z20.0000")
    gcode.append("G0 X0.0000 Y0.0000")
    gcode.append("M30")
    return "\n".join(gcode)

def smooth_height_map(height_map, threshold=0.5, max_iterations=10):
    y_count, x_count = height_map.shape
    for _ in range(max_iterations):
        adjusted = False
        for j in range(y_count):
            for i in range(x_count):
                if i < x_count - 1:
                    diff = height_map[j, i+1] - height_map[j, i]
                    if abs(diff) > threshold:
                        mean_val = (height_map[j, i+1] + height_map[j, i]) / 2.0
                        height_map[j, i] = mean_val
                        height_map[j, i+1] = mean_val
                        adjusted = True
                if j < y_count - 1:
                    diff = height_map[j+1, i] - height_map[j, i]
                    if abs(diff) > threshold:
                        mean_val = (height_map[j+1, i] + height_map[j, i]) / 2.0
                        height_map[j, i] = mean_val
                        height_map[j+1, i] = mean_val
                        adjusted = True
        if not adjusted:
            break
    return height_map

def parse_height_map_file(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
    sections = []
    current_section = None
    for line in lines:
        line = line.strip()
        if line.startswith("--- Bölüm"):
            if current_section is not None:
                sections.append(current_section)
            current_section = []
        elif current_section is not None and line:
            try:
                current_section.append(float(line))
            except ValueError:
                continue
    if current_section is not None:
        sections.append(current_section)
    max_length = max(len(section) for section in sections)
    height_map = np.full((len(sections), max_length), np.nan)
    for i, section in enumerate(sections):
        height_map[i, :len(section)] = section
    return height_map

def parse_gcode(file_path):
    coordinates = []
    current_position = [0.0, 0.0, 0.2]
    with open(file_path, 'r') as file:
        lines = file.readlines()
        for line in lines:
            line = line.strip()
            if line.startswith('G1') or line.startswith('G0'):
                x, y, z = current_position
                if 'X' in line:
                    x = float(re.search(r'X([-+]?\d*\.?\d+)', line).group(1))
                if 'Y' in line:
                    y = float(re.search(r'Y([-+]?\d*\.?\d+)', line).group(1))
                if 'Z' in line:
                    z = float(re.search(r'Z([-+]?\d*\.?\d+)', line).group(1))
                coordinates.append([x, y, z])
                current_position = [x, y, z]
            elif line.startswith('G2') or line.startswith('G3'):
                gcode_parts = re.findall(r'[XYZIJ][-+]?\d*\.?\d+', line)
                gcode_parts = {item[0]: float(item[1:]) for item in gcode_parts}
                x, y, z = current_position
                i = gcode_parts.get('I', None)
                j = gcode_parts.get('J', None)
                end_x = gcode_parts.get('X', x)
                end_y = gcode_parts.get('Y', y)
                if i is not None and j is not None:
                    center_x = x + i
                    center_y = y + j
                    radius = np.sqrt(i**2 + j**2)
                    direction = 'clockwise' if line.startswith('G2') else 'counterclockwise'
                    num_steps = 20
                    start_angle = np.arctan2(y - center_y, x - center_x)
                    end_angle = np.arctan2(end_y - center_y, end_x - center_x)
                    if direction == 'clockwise':
                        if end_angle < start_angle:
                            end_angle += 2 * np.pi
                    else:
                        if end_angle > start_angle:
                            end_angle -= 2 * np.pi
                    angles = np.linspace(start_angle, end_angle, num_steps)
                    arc_points = [
                        (center_x + radius * np.cos(angle),
                         center_y + radius * np.sin(angle), z)
                        for angle in angles
                    ]
                    coordinates.extend(arc_points)
                    current_position = [arc_points[-1][0], arc_points[-1][1], z]
                else:
                    coordinates.append([end_x, end_y, z])
                    current_position = [end_x, end_y, z]
    return np.array(coordinates)

def remove_consecutive_duplicates_gcode(lines):
    if not lines:
        return lines
    filtered = [lines[0]]
    for line in lines[1:]:
        if line.strip() != filtered[-1].strip():
            filtered.append(line)
    return filtered

# ----------------------- G-Code Editor Widget with STL Viewer -----------------------
# This widget is adapted from the gcode_editor.py source, with additions to apply offsets based on STL height.
class GCodeEditorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.interpolation_steps = 10
        self.arc_steps = 20
        self.gcode_lines = []
        self.modified_gcode = []
        self.center_choice = "Center"
        self.stl_mesh = None           # Currently used STL mesh
        self.stl_mesh_original = None  # Original STL mesh (for reapplying offsets)
        self.initUI()
    
    def initUI(self):
        self.setGeometry(10, 10, 800, 850)
        # UI Elements created using setGeometry:
        self.load_stl_button = QPushButton("STL Dosyasını Yükle", self)
        self.load_stl_button.setGeometry(10, 10, 150, 30)
        self.load_stl_button.clicked.connect(self.load_stl)
        
        self.load_gcode_button = QPushButton("G-Code Dosyasını Yükle", self)
        self.load_gcode_button.setGeometry(170, 10, 150, 30)
        self.load_gcode_button.clicked.connect(self.load_gcode)
        
        self.steps_label = QLabel("Ara Nokta Sayısı (G0/G1):", self)
        self.steps_label.setGeometry(10, 50, 180, 30)
        
        self.steps_input = QLineEdit(self)
        self.steps_input.setGeometry(200, 50, 50, 30)
        self.steps_input.setText(str(self.interpolation_steps))
        
        self.arc_steps_label = QLabel("Arc Ara Nokta Sayısı (G2):", self)
        self.arc_steps_label.setGeometry(10, 90, 180, 30)
        
        self.arc_steps_input = QLineEdit(self)
        self.arc_steps_input.setGeometry(200, 90, 50, 30)
        self.arc_steps_input.setText(str(self.arc_steps))
        
        # New: Offset settings for STL in the G-Code Editor tab
        self.offset_label = QLabel("Offset (X, Y, Z in mm):", self)
        self.offset_label.setGeometry(270, 50, 150, 30)
        self.offset_x_input = QLineEdit(self)
        self.offset_x_input.setGeometry(430, 50, 50, 30)
        self.offset_x_input.setText("0.0")
        self.offset_y_input = QLineEdit(self)
        self.offset_y_input.setGeometry(490, 50, 50, 30)
        self.offset_y_input.setText("0.0")
        self.offset_z_input = QLineEdit(self)
        self.offset_z_input.setGeometry(550, 50, 50, 30)
        self.offset_z_input.setText("0.0")
        
        # Labels to show STL dimensions and G-Code stats
        self.stlDimLabel = QLabel("STL Boyutları: (Yüklenmedi)", self)
        self.stlDimLabel.setGeometry(10, 130, 500, 30)
        
        self.gcodeOrigDimLabel = QLabel("Orijinal G-Code Boyutları: (Yüklenmedi)", self)
        self.gcodeOrigDimLabel.setGeometry(10, 170, 500, 30)
        
        self.gcodeModDimLabel = QLabel("Değiştirilmiş G-Code Boyutları: (Oluşturulmadı)", self)
        self.gcodeModDimLabel.setGeometry(10, 210, 500, 30)
        
        self.status_label = QLabel("Durum: Bekleniyor...", self)
        self.status_label.setGeometry(10, 250, 500, 30)
        
        # Radio button group for center selection:
        self.center_groupbox = QtWidgets.QGroupBox("G-Code Merkezini Seçin", self)
        self.center_groupbox.setGeometry(10, 290, 300, 120)
        self.radio_buttons = {}
        radio_positions = [
            ("Top-Left", 0, 0),
            ("Top-Center", 0, 1),
            ("Top-Right", 0, 2),
            ("Middle-Left", 1, 0),
            ("Center", 1, 1),
            ("Middle-Right", 1, 2),
            ("Bottom-Left", 2, 0),
            ("Bottom-Center", 2, 1),
            ("Bottom-Right", 2, 2),
        ]
        for text, row, col in radio_positions:
            rb = QRadioButton(text, self.center_groupbox)
            rb.setGeometry(10 + col * 90, 20 + row * 30, 80, 25)
            if text == "Center":
                rb.setChecked(True)
            rb.toggled.connect(self.radio_button_changed)
            self.radio_buttons[text] = rb
        
        self.apply_center_button = QPushButton("Merkez Dönüşümünü Uygula", self)
        self.apply_center_button.setGeometry(10, 420, 180, 30)
        self.apply_center_button.clicked.connect(self.apply_center_transformation)
        
        self.generate_gcode_button = QPushButton("Yeni G-Code Oluştur", self)
        self.generate_gcode_button.setGeometry(200, 420, 180, 30)
        self.generate_gcode_button.clicked.connect(self.generate_new_gcode)
        
        self.remove_duplicates_button = QPushButton("Tekrarlanan Satırları Temizle", self)
        self.remove_duplicates_button.setGeometry(390, 420, 180, 30)
        self.remove_duplicates_button.clicked.connect(self.remove_duplicate_lines)
        
        self.save_cnc_button = QPushButton("Yeni G-Code'u *.cnc Olarak Kaydet", self)
        self.save_cnc_button.setGeometry(10, 460, 250, 30)
        self.save_cnc_button.clicked.connect(self.save_cnc)
        
        self.original_text = QPlainTextEdit(self)
        self.original_text.setGeometry(10, 500, 380, 300)
        self.original_text.setReadOnly(True)
        
        self.modified_text = QPlainTextEdit(self)
        self.modified_text.setGeometry(400, 500, 380, 300)
        self.modified_text.setReadOnly(True)
        
        # Create a plotter for both STL and G-Code visualization
        self.plotter = QtInteractor(self)
        self.plotter.setGeometry(800, 10, 800, 800)
        self.plotter.show_axes()
        self.plotter.show_bounds()
    
    def update_stl_offset(self):
        # Reapply the current offset values to the original STL mesh.
        if self.stl_mesh_original is not None:
            self.stl_mesh = copy.deepcopy(self.stl_mesh_original)
            try:
                off_x = float(self.offset_x_input.text())
                off_y = float(self.offset_y_input.text())
                off_z = float(self.offset_z_input.text())
            except ValueError:
                off_x, off_y, off_z = 0.0, 0.0, 0.0
            self.stl_mesh.vectors += np.array([off_x, off_y, off_z])
            all_points = self.stl_mesh.vectors.reshape(-1, 3)
            min_vals = np.min(all_points, axis=0)
            max_vals = np.max(all_points, axis=0)
            stl_dim_str = (f"X: [{min_vals[0]:.4f}, {max_vals[0]:.4f}]  "
                           f"Y: [{min_vals[1]:.4f}, {max_vals[1]:.4f}]  "
                           f"Z: [{min_vals[2]:.4f}, {max_vals[2]:.4f}]")
            self.stlDimLabel.setText("STL Boyutları: " + stl_dim_str)
    
    def load_stl(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "STL Dosyasını Seç", "", "STL Files (*.stl)", options=options)
        if file_path:
            try:
                self.stl_mesh = mesh.Mesh.from_file(file_path)
                self.stl_mesh_original = copy.deepcopy(self.stl_mesh)
                self.update_stl_offset()
                self.status_label.setText(f"STL Yüklendi: {file_path}")
                n_triangles = self.stl_mesh.vectors.shape[0]
                all_points = self.stl_mesh.vectors.reshape(-1, 3)
                faces = np.hstack([np.full((n_triangles, 1), 3),
                                    np.arange(n_triangles * 3).reshape(n_triangles, 3)]).flatten()
                poly = pv.PolyData(all_points, faces)
                self.plotter.clear()
                self.plotter.add_mesh(poly, color="lightgray", opacity=0.7)
                self.plotter.reset_camera()
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"STL yüklenirken hata: {str(e)}")
        else:
            self.status_label.setText("STL Yükleme İptal Edildi.")
    
    def load_gcode(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "G-Code Dosyasını Seç", "", "G-Code Files (*.gcode *.nc *.cnc)", options=options)
        if file_path:
            try:
                with open(file_path, "r") as file:
                    self.gcode_lines = file.readlines()
                self.original_text.setPlainText("".join(self.gcode_lines))
                self.status_label.setText(f"G-Code Yüklendi: {file_path}")
                dim, count = self.compute_gcode_stats(self.gcode_lines)
                self.gcodeOrigDimLabel.setText(f"Orijinal G-Code Boyutları: {dim}  Satır Sayısı: {count}")
            except Exception as e:
                QMessageBox.critical(self, "Hata", f"G-Code yüklenirken hata: {str(e)}")
        else:
            self.status_label.setText("G-Code Yükleme İptal Edildi.")
    
    def compute_gcode_stats(self, lines):
        xs, ys, zs = [], [], []
        pattern = re.compile(r"[Gg][0|1|2].*")
        for line in lines:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    xs.append(coords["X"])
                if coords.get("Y") is not None:
                    ys.append(coords["Y"])
                if coords.get("Z") is not None:
                    zs.append(coords["Z"])
        if xs and ys and zs:
            dim_str = f"X: [{min(xs):.4f}, {max(xs):.4f}]  Y: [{min(ys):.4f}, {max(ys):.4f}]  Z: [{min(zs):.4f}, {max(zs):.4f}]"
        else:
            dim_str = "Bilinmiyor"
        return dim_str, len(lines)
    
    def save_gcode(self):
        try:
            if not self.stl_mesh or not self.gcode_lines:
                QMessageBox.warning(self, "Hata", "Önce STL ve G-Code dosyalarını yükleyin!")
                return
            try:
                self.interpolation_steps = int(self.steps_input.text())
                self.arc_steps = int(self.arc_steps_input.text())
            except ValueError:
                QMessageBox.warning(self, "Hata", "Ara nokta sayıları geçerli bir sayı olmalıdır.")
                return
            options = QFileDialog.Options()
            save_path, _ = QFileDialog.getSaveFileName(self, "Güncellenmiş G-Code'u Kaydet", "", "G-Code Files (*.gcode *.nc)", options=options)
            if not save_path:
                self.status_label.setText("Kaydetme İptal Edildi.")
                return
            mod_code = self.modify_gcode()
            self.modified_gcode = self.apply_center_offset(mod_code)
            self.modified_gcode = remove_consecutive_duplicates_gcode(self.modified_gcode)
            with open(save_path, "w") as file:
                file.writelines(self.modified_gcode)
            self.status_label.setText(f"Güncellenmiş G-Code Kaydedildi: {save_path}")
            QMessageBox.information(self, "Başarılı", "Güncellenmiş G-Code kaydedildi!")
            self.modified_text.setPlainText("".join(self.modified_gcode))
            dim, count = self.compute_gcode_stats(self.modified_gcode)
            self.gcodeModDimLabel.setText(f"Değiştirilmiş G-Code Boyutları: {dim}  Satır: {count}")
        except Exception as e:
            self.status_label.setText("Kaydetme sırasında hata!")
            QMessageBox.critical(self, "Hata", f"Kaydetme sırasında hata: {str(e)}")
    
    def save_cnc(self):
        if not self.modified_gcode:
            QMessageBox.warning(self, "Uyarı", "Önce yeni G-Code oluşturun!")
            return
        options = QFileDialog.Options()
        save_path, _ = QFileDialog.getSaveFileName(self, "Güncellenmiş G-Code'u *.cnc Olarak Kaydet", "", "CNC Files (*.cnc)", options=options)
        if not save_path:
            self.status_label.setText("Kaydetme İptal Edildi.")
            return
        try:
            with open(save_path, "w") as file:
                file.writelines(self.modified_gcode)
            self.status_label.setText(f"Güncellenmiş G-Code (*.cnc) Kaydedildi: {save_path}")
            QMessageBox.information(self, "Başarılı", "Yeni G-Code *.cnc olarak kaydedildi!")
        except Exception as e:
            self.status_label.setText("Kaydetme sırasında hata!")
            QMessageBox.critical(self, "Hata", f"Kaydetme sırasında hata: {str(e)}")
    
    def apply_xy_offset_to_gcode(self, lines):
        try:
            off_x = float(self.offset_x_input.text())
            off_y = float(self.offset_y_input.text())
        except ValueError:
            off_x, off_y = 0.0, 0.0
        new_lines = []
        pattern = re.compile(r"^(G0|G1)")
        for line in lines:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    coords["X"] += off_x
                if coords.get("Y") is not None:
                    coords["Y"] += off_y
                new_lines.append(self.generate_gcode_line(coords, command=line.split()[0]))
            else:
                new_lines.append(line)
        return new_lines
    
    def generate_new_gcode(self):
        try:
            if not self.stl_mesh or not self.gcode_lines:
                QMessageBox.warning(self, "Hata", "Önce STL ve G-Code dosyalarını yükleyin!")
                return
            try:
                self.interpolation_steps = int(self.steps_input.text())
                self.arc_steps = int(self.arc_steps_input.text())
            except ValueError:
                QMessageBox.warning(self, "Hata", "Ara nokta sayıları geçerli bir sayı olmalıdır.")
                return
            self.update_stl_offset()
            new_code = self.modify_gcode()
            self.modified_gcode = self.apply_xy_offset_to_gcode(new_code)
            self.modified_text.setPlainText("".join(self.modified_gcode))
            dim, count = self.compute_gcode_stats(self.modified_gcode)
            self.gcodeModDimLabel.setText(f"Değiştirilmiş G-Code Boyutları: {dim}  Satır: {count}")
            self.status_label.setText("Yeni G-Code oluşturuldu. (Merkez dönüşümü uygulanmadı)")
        except Exception as e:
            QMessageBox.critical(self, "Hata", f"Yeni G-Code oluşturulurken hata: {str(e)}")
            self.status_label.setText("Hata oluştu!")
    
    def remove_duplicate_lines(self):
        if not self.modified_gcode:
            QMessageBox.warning(self, "Uyarı", "Önce yeni G-Code oluşturun!")
            return
        self.modified_gcode = remove_consecutive_duplicates_gcode(self.modified_gcode)
        self.modified_text.setPlainText("".join(self.modified_gcode))
        self.status_label.setText("Tekrarlanan satırlar kaldırıldı.")
    
    def modify_gcode(self):
        new_lines = []
        first_cut = True
        current_pos = {"X": None, "Y": None, "Z": None, "F": None}
        last_xy = None  # To store last valid X and Y positions
        for line in self.gcode_lines:
            # Check for G0 lines that include a Z parameter.
            if line.startswith("G0") and re.search(r"Z\s*([-+]?\d*\.?\d+)", line):
                # If line has explicit X and Y tokens:
                if "X" in line and "Y" in line:
                    coords = self.parse_coordinates(line)
                    stl_z = self.get_z_height_from_stl(coords["X"], coords["Y"])
                    if stl_z is not None:
                        new_z = coords["Z"] + stl_z
                        new_line = re.sub(r"Z\s*([-+]?\d*\.?\d+)", f"Z{new_z:.4f}", line)
                        new_lines.append(new_line)
                        last_xy = {"X": coords["X"], "Y": coords["Y"]}
                        continue
                # Otherwise, if no X/Y in line, use last_xy.
                elif last_xy is not None:
                    z_match = re.search(r"Z\s*([-+]?\d*\.?\d+)", line)
                    if z_match:
                        orig_z = float(z_match.group(1))
                        stl_z = self.get_z_height_from_stl(last_xy["X"], last_xy["Y"])
                        if stl_z is not None:
                            new_z = orig_z + stl_z
                            new_line = re.sub(r"Z\s*([-+]?\d*\.?\d+)", f"Z{new_z:.4f}", line)
                            new_lines.append(new_line)
                            continue
            # For other lines starting with G0 or G1,
            if line.startswith("G0") or line.startswith("G1"):
                new_coords = self.parse_coordinates(line)
                for axis in ["X", "Y", "Z", "F"]:
                    new_coords[axis] = (new_coords.get(axis) if new_coords.get(axis) is not None 
                                        else (current_pos.get(axis) if current_pos.get(axis) is not None else 0))
                if new_coords.get("X") is not None and new_coords.get("Y") is not None:
                    last_xy = {"X": new_coords["X"], "Y": new_coords["Y"]}
                if current_pos["X"] is None or current_pos["Y"] is None:
                    current_pos = new_coords
                    new_lines.append(self.generate_gcode_line(new_coords, command=line.split()[0]))
                    continue
                segment_points = self.interpolate_segment(current_pos, new_coords, steps=self.interpolation_steps)
                for pt in segment_points:
                    stl_z = self.get_z_height_from_stl(pt["X"], pt["Y"])
                    if stl_z is not None:
                        if first_cut:
                            pt["Z"] = stl_z  
                        else:
                            pt["Z"] = stl_z - abs(pt["Z"])
                    new_lines.append(self.generate_gcode_line(pt, command="G1"))
                first_cut = False
                current_pos = new_coords
            elif line.startswith("G2"):
                new_coords = self.parse_coordinates(line)
                new_coords["Z"] = new_coords.get("Z", current_pos.get("Z", 0))
                I = new_coords.get("I")
                J = new_coords.get("J")
                if current_pos["X"] is None or current_pos["Y"] is None:
                    current_pos = new_coords
                    new_lines.append(line)
                    continue
                arc_points = self.interpolate_arc(current_pos, new_coords, I, J, steps=self.arc_steps)
                for pt in arc_points:
                    stl_z = self.get_z_height_from_stl(pt["X"], pt["Y"])
                    if stl_z is not None:
                        if first_cut:
                            pt["Z"] = stl_z
                        else:
                            pt["Z"] = stl_z - abs(pt["Z"])
                    new_lines.append(self.generate_gcode_line(pt, command="G1"))
                first_cut = False
                current_pos = new_coords
            else:
                new_lines.append(line)
        return new_lines
    
    def interpolate_segment(self, start, end, steps=10):
        points = []
        for i in range(0, steps + 1):
            factor = i / float(steps)
            pt = {
                "X": start["X"] + (end["X"] - start["X"]) * factor,
                "Y": start["Y"] + (end["Y"] - start["Y"]) * factor,
                "Z": start["Z"] + (end["Z"] - start["Z"]) * factor,
                "F": end.get("F"),
            }
            points.append(pt)
        return points
    
    def interpolate_arc(self, start, end, I, J, steps=10):
        points = []
        center_x = start["X"] + (I if I is not None else 0)
        center_y = start["Y"] + (J if J is not None else 0)
        radius = np.sqrt((I if I is not None else 0)**2 + (J if J is not None else 0)**2)
        start_angle = np.arctan2(start["Y"] - center_y, start["X"] - center_x)
        end_angle = np.arctan2(end["Y"] - center_y, end["X"] - center_x)
        if end_angle > start_angle:
            end_angle -= 2 * np.pi
        delta_angle = end_angle - start_angle
        for i in range(0, steps + 1):
            factor = i / float(steps)
            theta = start_angle + delta_angle * factor
            x = center_x + radius * np.cos(theta)
            y = center_y + radius * np.sin(theta)
            z = start["Z"] + (end["Z"] - start["Z"]) * factor
            pt = {"X": x, "Y": y, "Z": z, "F": end.get("F")}
            points.append(pt)
        return points
    
    def get_z_height_from_stl(self, x, y):
        try:
            if self.stl_mesh is None:
                return None
            centroids = np.mean(self.stl_mesh.vectors, axis=1)
            distances = np.sqrt((centroids[:, 0] - x)**2 + (centroids[:, 1] - y)**2)
            nearest_index = np.argmin(distances)
            z_height = centroids[nearest_index, 2]
            return z_height
        except Exception as e:
            print(f"Z yüksekliği hesaplanırken hata: {str(e)}")
            return None
    
    def parse_coordinates(self, line):
        coords = {}
        parts = line.strip().split()
        for part in parts:
            if part.startswith("X"):
                try:
                    coords["X"] = float(part[1:])
                except ValueError:
                    coords["X"] = None
            elif part.startswith("Y"):
                try:
                    coords["Y"] = float(part[1:])
                except ValueError:
                    coords["Y"] = None
            elif part.startswith("Z"):
                try:
                    coords["Z"] = float(part[1:])
                except ValueError:
                    coords["Z"] = None
            elif part.startswith("F"):
                try:
                    coords["F"] = float(part[1:])
                except ValueError:
                    coords["F"] = None
            elif part.startswith("I"):
                try:
                    coords["I"] = float(part[1:])
                except ValueError:
                    coords["I"] = None
            elif part.startswith("J"):
                try:
                    coords["J"] = float(part[1:])
                except ValueError:
                    coords["J"] = None
            elif part.startswith("S"):
                try:
                    coords["S"] = float(part[1:])
                except ValueError:
                    coords["S"] = None
        return coords
    
    def generate_gcode_line(self, coords, command="G1"):
        # Rebuild the line preserving additional tokens (like S for spindle speed)
        line = f"{command}"
        if coords.get("X") is not None:
            line += f" X{coords['X']:.4f}"
        if coords.get("Y") is not None:
            line += f" Y{coords['Y']:.4f}"
        if coords.get("Z") is not None:
            line += f" Z{coords['Z']:.4f}"
        if coords.get("F") is not None:
            line += f" F{coords['F']:.1f}"
        if coords.get("S") is not None:
            line += f" S{int(coords['S'])}"
        line += "\n"
        return line
    
    def radio_button_changed(self):
        for text, rb in self.radio_buttons.items():
            if rb.isChecked():
                self.center_choice = text
                break
    
    def apply_center_transformation(self):
        if not self.modified_gcode:
            QMessageBox.warning(self, "Uyarı", "Önce yeni G-Code oluşturup kaydedin!")
            return
        xs, ys = [], []
        pattern = re.compile(r"^(G0|G1)")
        for line in self.modified_gcode:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    xs.append(coords["X"])
                if coords.get("Y") is not None:
                    ys.append(coords["Y"])
        if not xs or not ys:
            QMessageBox.warning(self, "Uyarı", "Dönüştürülecek koordinat bulunamadı!")
            return
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        current_center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        if self.center_choice == "Top-Left":
            desired_center = (min_x, max_y)
        elif self.center_choice == "Top-Center":
            desired_center = ((min_x + max_x) / 2, max_y)
        elif self.center_choice == "Top-Right":
            desired_center = (max_x, max_y)
        elif self.center_choice == "Middle-Left":
            desired_center = (min_x, (min_y + max_y) / 2)
        elif self.center_choice == "Center":
            desired_center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        elif self.center_choice == "Middle-Right":
            desired_center = (max_x, (min_y + max_y) / 2)
        elif self.center_choice == "Bottom-Left":
            desired_center = (min_x, min_y)
        elif self.center_choice == "Bottom-Center":
            desired_center = ((min_x + max_x) / 2, min_y)
        elif self.center_choice == "Bottom-Right":
            desired_center = (max_x, min_y)
        else:
            desired_center = current_center
        offset_x = desired_center[0] - current_center[0]
        offset_y = desired_center[1] - current_center[1]
        transformed_lines = []
        for line in self.modified_gcode:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    coords["X"] += offset_x
                if coords.get("Y") is not None:
                    coords["Y"] += offset_y
                transformed_lines.append(self.generate_gcode_line(coords, command=line.split()[0]))
            else:
                transformed_lines.append(line)
        self.modified_gcode = transformed_lines
        self.modified_text.setPlainText("".join(self.modified_gcode))
        dim, count = self.compute_gcode_stats(self.modified_gcode)
        self.gcodeModDimLabel.setText(f"Değiştirilmiş G-Code Boyutları: {dim}  Satır: {count}")
        self.status_label.setText("Merkez dönüşümü uygulandı.")
    
    def apply_center_offset(self, lines):
        xs, ys = [], []
        pattern = re.compile(r"^(G0|G1)")
        for line in lines:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    xs.append(coords["X"])
                if coords.get("Y") is not None:
                    ys.append(coords["Y"])
        if not xs or not ys:
            return lines
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        current_center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        if self.center_choice == "Top-Left":
            desired_center = (min_x, max_y)
        elif self.center_choice == "Top-Center":
            desired_center = ((min_x + max_x) / 2, max_y)
        elif self.center_choice == "Top-Right":
            desired_center = (max_x, max_y)
        elif self.center_choice == "Middle-Left":
            desired_center = (min_x, (min_y + max_y) / 2)
        elif self.center_choice == "Center":
            desired_center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        elif self.center_choice == "Middle-Right":
            desired_center = (max_x, (min_y + max_y) / 2)
        elif self.center_choice == "Bottom-Left":
            desired_center = (min_x, min_y)
        elif self.center_choice == "Bottom-Center":
            desired_center = ((min_x + max_x) / 2, min_y)
        elif self.center_choice == "Bottom-Right":
            desired_center = (max_x, min_y)
        else:
            desired_center = current_center
        offset_x = desired_center[0] - current_center[0]
        offset_y = desired_center[1] - current_center[1]
        transformed_lines = []
        for line in lines:
            if pattern.match(line):
                coords = self.parse_coordinates(line)
                if coords.get("X") is not None:
                    coords["X"] += offset_x
                if coords.get("Y") is not None:
                    coords["Y"] += offset_y
                transformed_lines.append(self.generate_gcode_line(coords, command=line.split()[0]))
            else:
                transformed_lines.append(line)
        return transformed_lines

# ----------------------- Combine (Surface & STL) Widget -----------------------
# This widget is adapted from the combine1.py source.
class CombineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        self.height_map = None
        self.current_stl_mesh = None
        self.gcode_path = None
        self.x_step = 2.0
        self.y_step = 3.0
        self.initUI()
    
    def initUI(self):
        self.setWindowTitle("IDEA SUMO Laser Surface Generator")
        self.setGeometry(100, 100, 1600, 600)
        self.loadButton = QPushButton("Load Data", self)
        self.loadButton.setGeometry(610, 10, 100, 30)
        self.loadButton.clicked.connect(self.load_data)
        
        self.smoothButton = QPushButton("Smooth", self)
        self.smoothButton.setGeometry(720, 10, 100, 30)
        self.smoothButton.clicked.connect(self.smooth_data)
        self.smoothButton.setEnabled(False)
        
        self.saveButton = QPushButton("Save STL", self)
        self.saveButton.setGeometry(830, 10, 100, 30)
        self.saveButton.clicked.connect(self.save_stl)
        self.saveButton.setEnabled(False)
        
        self.loadGCodeButton = QPushButton("Load GCode", self)
        self.loadGCodeButton.setGeometry(940, 10, 100, 30)
        self.loadGCodeButton.clicked.connect(self.load_gcode)
        
        self.frontViewButton = QPushButton("Front View", self)
        self.frontViewButton.setGeometry(1050, 10, 100, 30)
        self.frontViewButton.clicked.connect(self.set_front_view)
        
        self.topViewButton = QPushButton("Top View", self)
        self.topViewButton.setGeometry(1160, 10, 100, 30)
        self.topViewButton.clicked.connect(self.set_top_view)
        
        self.sideViewButton = QPushButton("Side View", self)
        self.sideViewButton.setGeometry(1270, 10, 100, 30)
        self.sideViewButton.clicked.connect(self.set_side_view)
        
        self.showEdgesCheck = QCheckBox("Show Edges", self)
        self.showEdgesCheck.setGeometry(1380, 10, 100, 30)
        self.showEdgesCheck.stateChanged.connect(self.update_view)
        
        self.colorCombo = QComboBox(self)
        self.colorCombo.setGeometry(1490, 10, 100, 30)
        self.colorCombo.addItems(["lightblue", "lightgreen", "lightgray", "pink"])
        self.colorCombo.currentIndexChanged.connect(self.update_view)
        
        self.sizeLabel = QLabel("Model Dimensions: X: -, Y: -, Z: -", self)
        self.sizeLabel.setGeometry(610, 50, 300, 30)
        
        self.xStepInput = QLineEdit(str(self.x_step), self)
        self.xStepInput.setGeometry(610, 90, 150, 30)
        self.xStepInput.textChanged.connect(self.update_steps)
        
        self.yStepInput = QLineEdit(str(self.y_step), self)
        self.yStepInput.setGeometry(770, 90, 150, 30)
        self.yStepInput.textChanged.connect(self.update_steps)
        
        self.xDimensionInput = QLineEdit(self)
        self.xDimensionInput.setGeometry(930, 90, 150, 30)
        self.xDimensionInput.setPlaceholderText("Set X Dimension")
        self.xDimensionInput.textChanged.connect(self.update_x_dimension)
        
        self.yDimensionInput = QLineEdit(self)
        self.yDimensionInput.setGeometry(1090, 90, 150, 30)
        self.yDimensionInput.setPlaceholderText("Set Y Dimension")
        self.yDimensionInput.textChanged.connect(self.update_y_dimension)
        
        self.plotter = QtInteractor(self)
        self.plotter.setGeometry(610, 130, 600, 600)
        self.plotter.show_axes()
        self.plotter.show_bounds()
        
        self.save_directory_label = QtWidgets.QLabel(self)
        self.save_directory_label.setText("Save Directory:")
        self.save_directory_label.setGeometry(QtCore.QRect(10, 10, 100, 20))
        
        self.save_directory_input = QLineEdit(self)
        self.save_directory_input.setText(self.settings["save_directory"])
        self.save_directory_input.setGeometry(QtCore.QRect(170, 10, 250, 20))
        
        self.save_directory_button = QtWidgets.QPushButton(self)
        self.save_directory_button.setText("Browse")
        self.save_directory_button.setGeometry(QtCore.QRect(430, 10, 100, 20))
        self.save_directory_button.clicked.connect(self.browse_directory)
        
        self.zero_point_label = QtWidgets.QLabel(self)
        self.zero_point_label.setText("Zero Point:")
        self.zero_point_label.setGeometry(QtCore.QRect(10, 40, 100, 20))
        
        self.zero_point_input = QLineEdit(self)
        self.zero_point_input.setText(str(self.settings["zero_point"]))
        self.zero_point_input.setGeometry(QtCore.QRect(170, 40, 100, 20))
        
        self.zero_point_save_button = QtWidgets.QPushButton(self)
        self.zero_point_save_button.setText("Save Settings")
        self.zero_point_save_button.setGeometry(QtCore.QRect(10, 290, 100, 30))
        self.zero_point_save_button.clicked.connect(self.save_all_settings)
        
        self.line_count_label = QtWidgets.QLabel(self)
        self.line_count_label.setText("Line Count:")
        self.line_count_label.setGeometry(QtCore.QRect(10, 70, 150, 20))
        
        self.line_count_input = QtWidgets.QLineEdit(self)
        self.line_count_input.setText(str(self.settings["line_count"]))
        self.line_count_input.setGeometry(QtCore.QRect(170, 70, 100, 20))
        
        self.line_spacing_label = QtWidgets.QLabel(self)
        self.line_spacing_label.setText("Line Spacing (mm):")
        self.line_spacing_label.setGeometry(QtCore.QRect(10, 100, 150, 20))
        
        self.line_spacing_input = QtWidgets.QLineEdit(self)
        self.line_spacing_input.setText(str(self.settings["line_spacing"]))
        self.line_spacing_input.setGeometry(QtCore.QRect(170, 100, 100, 20))
        
        self.x_length_label = QtWidgets.QLabel(self)
        self.x_length_label.setText("X-axis Length (mm):")
        self.x_length_label.setGeometry(QtCore.QRect(10, 130, 150, 20))
        
        self.x_length_input = QtWidgets.QLineEdit(self)
        self.x_length_input.setText(str(self.settings["x_length"]))
        self.x_length_input.setGeometry(QtCore.QRect(170, 130, 100, 20))
        
        self.feed_rate_cut_label = QtWidgets.QLabel(self)
        self.feed_rate_cut_label.setText("Cut Feed Rate (mm/min):")
        self.feed_rate_cut_label.setGeometry(QtCore.QRect(10, 160, 150, 20))
        
        self.feed_rate_cut_input = QtWidgets.QLineEdit(self)
        self.feed_rate_cut_input.setText(str(self.settings["feed_rate_cut"]))
        self.feed_rate_cut_input.setGeometry(QtCore.QRect(170, 160, 100, 20))
        
        self.feed_rate_rapid_label = QtWidgets.QLabel(self)
        self.feed_rate_rapid_label.setText("Rapid Feed Rate (mm/min):")
        self.feed_rate_rapid_label.setGeometry(QtCore.QRect(10, 190, 150, 20))
        
        self.feed_rate_rapid_input = QtWidgets.QLineEdit(self)
        self.feed_rate_rapid_input.setText(str(self.settings["feed_rate_rapid"]))
        self.feed_rate_rapid_input.setGeometry(QtCore.QRect(170, 190, 100, 20))
        
        self.dwell_time_label = QtWidgets.QLabel(self)
        self.dwell_time_label.setText("Dwell Time (ms):")
        self.dwell_time_label.setGeometry(QtCore.QRect(10, 220, 150, 20))
        
        self.dwell_time_input = QtWidgets.QLineEdit(self)
        self.dwell_time_input.setText(str(self.settings["dwell_time"]))
        self.dwell_time_input.setGeometry(QtCore.QRect(170, 220, 100, 20))
        
        self.generate_button = QtWidgets.QPushButton(self)
        self.generate_button.setText("Generate G-Code")
        self.generate_button.setGeometry(QtCore.QRect(170, 290, 100, 30))
        self.generate_button.clicked.connect(self.on_generate)
        
        self.gcode_output_label = QtWidgets.QLabel(self)
        self.gcode_output_label.setText("Generated G-Code:")
        self.gcode_output_label.setGeometry(QtCore.QRect(300, 30, 100, 30))
        
        self.gcode_output = QtWidgets.QTextEdit(self)
        self.gcode_output.setReadOnly(True)
        self.gcode_output.setGeometry(QtCore.QRect(300, 60, 250, 260))
        
        self.serial_port_label = QtWidgets.QLabel(self)
        self.serial_port_label.setText("Select Serial Port:")
        self.serial_port_label.setGeometry(QtCore.QRect(10, 320, 120, 30))
        
        self.serial_port_combo = QtWidgets.QComboBox(self)
        self.serial_port_combo.setGeometry(QtCore.QRect(10, 350, 120, 30))
        
        self.refresh_ports_button = QtWidgets.QPushButton(self)
        self.refresh_ports_button.setText("Refresh Ports")
        self.refresh_ports_button.setGeometry(QtCore.QRect(10, 390, 120, 30))
        self.refresh_ports_button.clicked.connect(self.refresh_ports)
        
        self.start_serial_button = QtWidgets.QPushButton(self)
        self.start_serial_button.setText("Start Serial Monitor")
        self.start_serial_button.setGeometry(QtCore.QRect(10, 430, 150, 40))
        self.start_serial_button.clicked.connect(self.start_serial_read)
        
        self.stop_serial_button = QtWidgets.QPushButton(self)
        self.stop_serial_button.setText("Stop Serial Monitor")
        self.stop_serial_button.setGeometry(QtCore.QRect(10, 490, 150, 40))
        self.stop_serial_button.clicked.connect(self.stop_serial_read)
        
        self.baud_rate_label = QtWidgets.QLabel(self)
        self.baud_rate_label.setText("Baud Rate:")
        self.baud_rate_label.setGeometry(QtCore.QRect(10, 250, 100, 20))
        
        self.baud_rate_input = QtWidgets.QLineEdit(self)
        self.baud_rate_input.setText(str(self.settings["baud_rate"]))
        self.baud_rate_input.setGeometry(QtCore.QRect(170, 250, 100, 20))
        
        self.serial_output_label = QtWidgets.QLabel(self)
        self.serial_output_label.setText("Serial Monitor:")
        self.serial_output_label.setGeometry(QtCore.QRect(300, 320, 150, 20))
        
        self.serial_output = QtWidgets.QTextEdit(self)
        self.serial_output.setReadOnly(True)
        self.serial_output.setGeometry(QtCore.QRect(300, 340, 250, 250))
        
        self.refresh_ports()
    
    def update_steps(self):
        try:
            self.x_step = float(self.xStepInput.text())
            self.y_step = float(self.yStepInput.text())
            self.update_view()
        except ValueError:
            pass
    
    def update_x_dimension(self):
        try:
            x_dimension = float(self.xDimensionInput.text())
            if self.height_map is not None:
                _, x_count = self.height_map.shape
                self.x_step = x_dimension / (x_count - 1)
                self.xStepInput.setText(str(self.x_step))
                self.update_view()
        except ValueError:
            pass
    
    def update_y_dimension(self):
        try:
            y_dimension = float(self.yDimensionInput.text())
            if self.height_map is not None:
                y_count, _ = self.height_map.shape
                self.y_step = y_dimension / (y_count - 1)
                self.yStepInput.setText(str(self.y_step))
                self.update_view()
        except ValueError:
            pass
    
    def load_data(self):
        data_file, _ = QFileDialog.getOpenFileName(self, "Select Data File", "", "Text Files (*.txt);;All Files (*)")
        if not data_file:
            return
        try:
            self.height_map = parse_height_map_file(data_file)
            self.update_view()
            self.saveButton.setEnabled(True)
            self.smoothButton.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load data: {str(e)}")
    
    def smooth_data(self):
        if self.height_map is None:
            QMessageBox.warning(self, "Warning", "No data loaded.")
            return
        self.height_map = smooth_height_map(self.height_map, threshold=0.5, max_iterations=10)
        self.update_view()
    
    def update_model_dimensions(self, vertices):
        x_min, x_max = vertices[:, 0].min(), vertices[:, 0].max()
        y_min, y_max = vertices[:, 1].min(), vertices[:, 1].max()
        z_min, z_max = vertices[:, 2].min(), vertices[:, 2].max()
        self.sizeLabel.setText(f"Model Dimensions: X: {x_max-x_min:.2f}, Y: {y_max-y_min:.2f}, Z: {z_max-z_min:.2f}")
    
    def update_view(self):
        if self.height_map is None:
            return
        vertices, faces, stl_mesh = self.create_stl_mesh_from_height_map()
        self.current_stl_mesh = stl_mesh
        self.update_model_dimensions(vertices)
        face_data = np.hstack([np.full((faces.shape[0], 1), 3), faces]).flatten()
        poly_data = pv.PolyData(vertices, face_data)
        show_edges = self.showEdgesCheck.isChecked()
        color = self.colorCombo.currentText()
        self.plotter.clear()
        self.plotter.add_mesh(poly_data, show_edges=show_edges, color=color,
                              smooth_shading=True, lighting=False, ambient=False, diffuse=True, specular=True)
        if self.gcode_path:
            coordinates = parse_gcode(self.gcode_path)
            self.plot_gcode(coordinates)
        self.plotter.reset_camera()
    
    def create_stl_mesh_from_height_map(self):
        y_count, x_count = self.height_map.shape
        x_coords = np.arange(0, x_count * self.x_step, self.x_step)
        y_coords = np.arange(0, y_count * self.y_step, self.y_step)
        vertices = []
        for j in range(y_count):
            for i in range(x_count):
                if not np.isnan(self.height_map[j, i]):
                    x_val = x_coords[i]
                    y_val = y_coords[j]
                    z_val = self.height_map[j, i]
                    vertices.append([x_val, y_val, z_val])
        vertices = np.array(vertices)
        center = np.mean(vertices, axis=0)
        vertices -= center
        faces = []
        for j in range(y_count - 1):
            for i in range(x_count - 1):
                if not (np.isnan(self.height_map[j, i]) or np.isnan(self.height_map[j, i+1]) or
                        np.isnan(self.height_map[j+1, i]) or np.isnan(self.height_map[j+1, i+1])):
                    v0 = j * x_count + i
                    v1 = j * x_count + (i + 1)
                    v2 = (j + 1) * x_count + i
                    v3 = (j + 1) * x_count + (i + 1)
                    faces.append([v0, v1, v2])
                    faces.append([v2, v1, v3])
        faces = np.array(faces)
        stl_mesh = mesh.Mesh(np.zeros(faces.shape[0], dtype=mesh.Mesh.dtype))
        for idx, f in enumerate(faces):
            for k in range(3):
                stl_mesh.vectors[idx][k] = vertices[f[k], :]
        return vertices, faces, stl_mesh
    
    def save_stl(self):
        if self.current_stl_mesh is None:
            QMessageBox.warning(self, "Warning", "No STL data to save.")
            return
        save_file, _ = QFileDialog.getSaveFileName(self, "Save STL", "", "STL Files (*.stl);;All Files (*)")
        if save_file:
            self.current_stl_mesh.save(save_file)
            QMessageBox.information(self, "Info", "STL saved successfully!")
    
    def load_gcode(self):
        gcode_file, _ = QFileDialog.getOpenFileName(self, "Select GCode File", "", "GCode Files (*.cnc);;All Files (*)")
        if gcode_file:
            self.gcode_path = gcode_file
            self.update_view()
    
    def set_front_view(self):
        self.plotter.view_isometric()
    
    def set_top_view(self):
        self.plotter.view_xy()
    
    def set_side_view(self):
        self.plotter.view_yz()
    
    def plot_gcode(self, coordinates):
        if coordinates.shape[0] > 1:
            lines = np.vstack([coordinates[:-1], coordinates[1:]])
            self.plotter.add_lines(lines, color="red", width=2)
    
    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory", self.settings["save_directory"])
        if directory:
            self.save_directory_input.setText(directory)
            self.settings["save_directory"] = directory
            save_settings(self.settings)
    
    def refresh_ports(self):
        available_ports = [port.portName() for port in QSerialPortInfo.availablePorts() if port.portName() != "COM1"]
        self.serial_port_combo.clear()
        self.serial_port_combo.addItems(available_ports)
        self.start_serial_button.setEnabled(len(available_ports) > 0)
    
    def save_all_settings(self):
        try:
            self.settings["save_directory"] = self.save_directory_input.text()
            self.settings["zero_point"] = int(self.zero_point_input.text())
            self.settings["line_count"] = int(self.line_count_input.text())
            self.settings["line_spacing"] = float(self.line_spacing_input.text())
            self.settings["x_length"] = float(self.x_length_input.text())
            self.settings["feed_rate_cut"] = float(self.feed_rate_cut_input.text())
            self.settings["feed_rate_rapid"] = float(self.feed_rate_rapid_input.text())
            self.settings["dwell_time"] = int(self.dwell_time_input.text())
            self.settings["baud_rate"] = int(self.baud_rate_input.text())
            save_settings(self.settings)
            QMessageBox.information(self, "Success", "All settings have been saved.")
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid input value. Please check your entries.")
    
    def on_generate(self):
        try:
            line_count = int(self.line_count_input.text())
            line_spacing = float(self.line_spacing_input.text())
            x_length = float(self.x_length_input.text())
            feed_rate_cut = float(self.feed_rate_cut_input.text())
            feed_rate_rapid = float(self.feed_rate_rapid_input.text())
            dwell_time = int(self.dwell_time_input.text())
    
            gcode = generate_gcode(line_count, line_spacing, x_length, feed_rate_cut, feed_rate_rapid, dwell_time)
            self.gcode_output.setText(gcode)
    
            save_directory = self.save_directory_input.text()
            if not os.path.exists(save_directory):
                os.makedirs(save_directory)
            file_path = os.path.join(save_directory, "generated_gcode.cnc")
            with open(file_path, "w") as file:
                file.write(gcode)
            QMessageBox.information(self, "Success", f"G-code saved to {file_path}")
    
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid input values. Please check your entries.")
    
    def start_serial_read(self):
        try:
            port_name = self.serial_port_combo.currentText()
            baud_rate = int(self.baud_rate_input.text())
            self.serial_connection = serial.Serial(port_name, baud_rate, timeout=1)
            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
            QMessageBox.information(self, "Info", "Serial monitor started.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to start serial monitor: {e}")
    
    def read_serial_data(self):
        try:
            section_counter = 1
            last_data_time = time.time()
            is_reading = False
            data_file_path = os.path.join(self.settings["save_directory"], "data.txt")
            with open(data_file_path, "w") as file:
                file.write(f"--- Section {section_counter} ---\n")
                while self.serial_connection.is_open:
                    if self.serial_connection.in_waiting > 0:
                        raw_line = self.serial_connection.readline().decode('utf-8', errors='replace').strip()
                        if raw_line.isdigit():
                            sensor_value = int(raw_line)
                            zero_point = self.settings.get("zero_point", 8150)
                            height_mm = (sensor_value - zero_point) * 0.01
                            output = f"Sensor: {sensor_value}, Height: {height_mm:.2f} mm"
                            self.serial_output.append(output)
                            file.write(f"{height_mm:.2f}\n")
                            file.flush()
                            last_data_time = time.time()
                            is_reading = True
                    if is_reading and (time.time() - last_data_time > 0.5):
                        section_counter += 1
                        self.serial_output.append(f"--- Section {section_counter} ---")
                        file.write(f"\n--- Section {section_counter} ---\n")
                        file.flush()
                        is_reading = False
        except Exception as e:
            self.serial_output.append(f"Error: {e}")
    
    def stop_serial_read(self):
        try:
            if hasattr(self, 'serial_connection') and self.serial_connection.is_open:
                self.serial_connection.close()
                QMessageBox.information(self, "Info", "Serial monitor stopped.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to stop serial monitor: {e}")

# ----------------------- Main Window with Tabs -----------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Birlesim")
        self.setGeometry(50, 50, 1700, 900)
        self.tab_widget = QTabWidget(self)
        self.setCentralWidget(self.tab_widget)
        self.combine_tab = CombineWidget(self)
        self.gcode_editor_tab = GCodeEditorWidget(self)
        self.tab_widget.addTab(self.combine_tab, "Surface & STL")
        self.tab_widget.addTab(self.gcode_editor_tab, "G-Code Editor")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main = MainWindow()
    main.show()
    sys.exit(app.exec_())
