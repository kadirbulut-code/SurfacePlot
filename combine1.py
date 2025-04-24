import sys
import os
import json
import threading
import time
import numpy as np
from PyQt5.QtWidgets import (QApplication, QWidget, QGridLayout, QLabel,QCheckBox, QLineEdit, QPushButton, QTextEdit, QFileDialog, QComboBox, QMessageBox)
from PyQt5.QtCore import Qt
from PyQt5.QtSerialPort import QSerialPortInfo
import serial
from PyQt5 import QtWidgets, QtCore
from stl import mesh
import pyvista as pv
from pyvistaqt import QtInteractor
import re
import subprocess


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
    gcode.append("G0 X0.000 Y0.000")
    gcode.append(f"G4 P{dwell_time}")

    x_start = -x_length / 2
    x_end = x_length / 2
    y_total_length = (line_count - 1) * line_spacing
    y_start = y_total_length / 2

    for i in range(line_count):
        y_pos = y_start - i * line_spacing
        gcode.append(f"G0 X{x_start:.3f} Y{y_pos:.3f} Z1.000")
        gcode.append(f"G1 Z0.000 F{feed_rate_cut:.1f}")
        gcode.append(f"G4 P{dwell_time}")
        gcode.append("M63")
        gcode.append(f"G1 X{x_end:.3f} F{feed_rate_rapid:.1f}")
        gcode.append("M64")
        gcode.append("G0 Z1.000")

    gcode.append("G0 Z20.000")
    gcode.append("G0 X0.000 Y0.000")
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
                    x = float(re.search(r'X(-?\d*\.?\d+)', line).group(1))
                if 'Y' in line:
                    y = float(re.search(r'Y(-?\d*\.?\d+)', line).group(1))
                if 'Z' in line:
                    z = float(re.search(r'Z(-?\d*\.?\d+)', line).group(1))

                coordinates.append([x, y, z])
                current_position = [x, y, z]

            elif line.startswith('G2') or line.startswith('G3'):
                gcode_parts = re.findall(r'[XYZIJ]-?\d*\.?\d+', line)
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
                        (center_x + radius * np.cos(angle), center_y + radius * np.sin(angle), z)
                        for angle in angles
                    ]

                    coordinates.extend(arc_points)
                    current_position = [arc_points[-1][0], arc_points[-1][1], z]
                else:
                    coordinates.append([end_x, end_y, z])
                    current_position = [end_x, end_y, z]
    return np.array(coordinates)

class GCodeApp(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.initUI()

        
        
    def initUI(self):
        self.setWindowTitle("IDEA SUMO Laser Surface Generator")
        self.setGeometry(100, 100, 1600, 600)

        ##STL buttons
        self.x_step = 2.0
        self.y_step = 3.0

        self.height_map = None
        self.current_stl_mesh = None
        self.gcode_path = None
        
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

        
         # Save Directory
        self.save_directory_label = QtWidgets.QLabel(self)
        self.save_directory_label.setText("Save Directory:")
        self.save_directory_label.setGeometry(QtCore.QRect(10, 10, 100, 20))

        self.save_directory_input = QtWidgets.QLineEdit(self)
        self.save_directory_input.setText(self.settings["save_directory"])
        self.save_directory_input.setGeometry(QtCore.QRect(170, 10, 250, 20))

        self.save_directory_button = QtWidgets.QPushButton(self)
        self.save_directory_button.setText("Browse")
        self.save_directory_button.setGeometry(QtCore.QRect(430, 10, 100, 20))
        self.save_directory_button.clicked.connect(self.browse_directory)

        # Zero Point Input
        self.zero_point_label = QtWidgets.QLabel(self)
        self.zero_point_label.setText("Zero Point:")
        self.zero_point_label.setGeometry(QtCore.QRect(10, 40, 100, 20))

        self.zero_point_input = QtWidgets.QLineEdit(self)
        self.zero_point_input.setText(str(self.settings["zero_point"]))
        self.zero_point_input.setGeometry(QtCore.QRect(170, 40, 100, 20))

        self.zero_point_save_button = QtWidgets.QPushButton(self)
        self.zero_point_save_button.setText("Save Settings")
        self.zero_point_save_button.setGeometry(QtCore.QRect(10, 290, 100, 30))
        self.zero_point_save_button.clicked.connect(self.save_all_settings)

        # Line Count
        self.line_count_label = QtWidgets.QLabel(self)
        self.line_count_label.setText("Line Count:")
        self.line_count_label.setGeometry(QtCore.QRect(10, 70, 150, 20))

        self.line_count_input = QtWidgets.QLineEdit(self)
        self.line_count_input.setText(str(self.settings["line_count"]))
        self.line_count_input.setGeometry(QtCore.QRect(170, 70, 100, 20))

        # Line Spacing
        self.line_spacing_label = QtWidgets.QLabel(self)
        self.line_spacing_label.setText("Line Spacing (mm):")
        self.line_spacing_label.setGeometry(QtCore.QRect(10, 100, 150, 20))

        self.line_spacing_input = QtWidgets.QLineEdit(self)
        self.line_spacing_input.setText(str(self.settings["line_spacing"]))
        self.line_spacing_input.setGeometry(QtCore.QRect(170, 100, 100, 20))

        # X-axis Length
        self.x_length_label = QtWidgets.QLabel(self)
        self.x_length_label.setText("X-axis Length (mm):")
        self.x_length_label.setGeometry(QtCore.QRect(10, 130, 150, 20))

        self.x_length_input = QtWidgets.QLineEdit(self)
        self.x_length_input.setText(str(self.settings["x_length"]))
        self.x_length_input.setGeometry(QtCore.QRect(170, 130, 100, 20))

        # Cut Feed Rate
        self.feed_rate_cut_label = QtWidgets.QLabel(self)
        self.feed_rate_cut_label.setText("Cut Feed Rate (mm/min):")
        self.feed_rate_cut_label.setGeometry(QtCore.QRect(10, 160, 150, 20))

        self.feed_rate_cut_input = QtWidgets.QLineEdit(self)
        self.feed_rate_cut_input.setText(str(self.settings["feed_rate_cut"]))
        self.feed_rate_cut_input.setGeometry(QtCore.QRect(170, 160, 100, 20))

        # Rapid Feed Rate
        self.feed_rate_rapid_label = QtWidgets.QLabel(self)
        self.feed_rate_rapid_label.setText("Rapid Feed Rate (mm/min):")
        self.feed_rate_rapid_label.setGeometry(QtCore.QRect(10, 190, 150, 20))

        self.feed_rate_rapid_input = QtWidgets.QLineEdit(self)
        self.feed_rate_rapid_input.setText(str(self.settings["feed_rate_rapid"]))
        self.feed_rate_rapid_input.setGeometry(QtCore.QRect(170, 190, 100, 20))

        # Dwell Time
        self.dwell_time_label = QtWidgets.QLabel(self)
        self.dwell_time_label.setText("Dwell Time (ms):")
        self.dwell_time_label.setGeometry(QtCore.QRect(10, 220, 150, 20))

        self.dwell_time_input = QtWidgets.QLineEdit(self)
        self.dwell_time_input.setText(str(self.settings["dwell_time"]))
        self.dwell_time_input.setGeometry(QtCore.QRect(170, 220, 100, 20))

        
        # Generate Button
        self.generate_button = QtWidgets.QPushButton(self)
        self.generate_button.setText("Generate G-Code")
        self.generate_button.setGeometry(QtCore.QRect(170, 290, 100, 30))
        self.generate_button.clicked.connect(self.on_generate)

        # G-Code Output
        self.gcode_output_label = QtWidgets.QLabel(self)
        self.gcode_output_label.setText("Generated G-Code:")
        self.gcode_output_label.setGeometry(QtCore.QRect(300, 30, 100, 30))

        self.gcode_output = QtWidgets.QTextEdit(self)
        self.gcode_output.setReadOnly(True)
        self.gcode_output.setGeometry(QtCore.QRect(300, 60, 250, 260))

         # Serial Port Selection
        self.serial_port_label = QtWidgets.QLabel(self)
        self.serial_port_label.setText("Select Serial Port:")
        self.serial_port_label.setGeometry(QtCore.QRect(10, 320, 120, 30))

        self.serial_port_combo = QtWidgets.QComboBox(self)
        self.serial_port_combo.setGeometry(QtCore.QRect(10, 350, 120, 30))

        self.refresh_ports_button = QtWidgets.QPushButton(self)
        self.refresh_ports_button.setText("Refresh Ports")
        self.refresh_ports_button.setGeometry(QtCore.QRect(10, 390, 120, 30))
        self.refresh_ports_button.clicked.connect(self.refresh_ports)

        # Start and Stop Buttons
        self.start_serial_button = QtWidgets.QPushButton(self)
        self.start_serial_button.setText("Start Serial Monitor")
        self.start_serial_button.setGeometry(QtCore.QRect(10, 430, 150, 40))
        self.start_serial_button.clicked.connect(self.start_serial_read)

        self.stop_serial_button = QtWidgets.QPushButton(self)
        self.stop_serial_button.setText("Stop Serial Monitor")
        self.stop_serial_button.setGeometry(QtCore.QRect(10, 490, 150, 40))
        self.stop_serial_button.clicked.connect(self.stop_serial_read)

        # Baud Rate Input
        self.baud_rate_label = QtWidgets.QLabel(self)
        self.baud_rate_label.setText("Baud Rate:")
        self.baud_rate_label.setGeometry(QtCore.QRect(10, 250, 100, 20))

        self.baud_rate_input = QtWidgets.QLineEdit(self)
        self.baud_rate_input.setText(str(self.settings["baud_rate"]))
        self.baud_rate_input.setGeometry(QtCore.QRect(170, 250, 100, 20))

        # Serial Monitor Output
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
            QMessageBox.critical(self, "Error", f"Failed to load data: {str(e)})")

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
        self.sizeLabel.setText(f"Model Dimensions: X: {x_max - x_min:.2f}, Y: {y_max - y_min:.2f}, Z: {z_max - z_min:.2f}")

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
        self.plotter.add_mesh(poly_data, show_edges=show_edges, color=color, smooth_shading=True, lighting=False, ambient=False, diffuse=True, specular=True)

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
                if not (np.isnan(self.height_map[j, i]) or np.isnan(self.height_map[j, i+1]) or \
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
    def open_gcode_app(self):
        try:
            subprocess.Popen(["python", "gcode12qt.py"])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open GCode App: {e}")

    
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

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory", self.settings["save_directory"])
        if directory:
            self.save_directory_input.setText(directory)
            self.settings["save_directory"] = directory
            save_settings(self.settings)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GCodeApp()
    window.show()
    sys.exit(app.exec_())
