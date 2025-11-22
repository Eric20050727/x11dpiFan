import os
import sys
import time
import subprocess
import ctypes

import wmi
import pythoncom

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QCheckBox,
    QSlider,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
)
from PyQt6.QtGui import QFont, QPainter, QPen, QColor


# 温度刷新间隔（秒）
TEMP_POLL_INTERVAL = 5


# ---------- 工具函数：管理员 & 资源路径 ----------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def base_dir_for_resources() -> str:
    """
    资源目录：
    - 运行 .py 时：脚本所在目录
    - 打包后单 exe：PyInstaller 解压目录 sys._MEIPASS
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def find_ipmicfg() -> str:
    base_dir = base_dir_for_resources()
    ipmi_exe = os.path.join(base_dir, "IPMICFG-Win.exe")
    if not os.path.exists(ipmi_exe):
        raise FileNotFoundError(f"未找到 IPMICFG-Win.exe：{ipmi_exe}")
    return ipmi_exe


def find_lhm_exe() -> str:
    """
    在资源目录下的 LibreHardwareMonitor\LibreHardwareMonitor.exe
    （适配：打包时 --add-data "LibreHardwareMonitor;LibreHardwareMonitor"）
    """
    base_dir = base_dir_for_resources()
    exe = os.path.join(base_dir, "LibreHardwareMonitor", "LibreHardwareMonitor.exe")
    if not os.path.exists(exe):
        raise FileNotFoundError(f"未找到 LibreHardwareMonitor.exe：{exe}")
    return exe
def has_lhm_sensors() -> bool:
    """检查 root\\LibreHardwareMonitor 里是否有温度传感器"""
    try:
        conn = wmi.WMI(namespace="root\\LibreHardwareMonitor")
        sensors = conn.Sensor(SensorType="Temperature")
        return len(sensors) > 0
    except Exception:
        return False

def ensure_lhm_running(log=None):
    """
    确保 LibreHardwareMonitor 在运行：
    - 如果 WMI 里已经有温度传感器，就直接用（可能是你自己开着 LHM）；
    - 否则从本地 LibreHardwareMonitor\\LibreHardwareMonitor.exe 启动一个，
      再检查一次是否出现传感器。
    """

    def logmsg(msg: str):
        if log is not None:
            log(msg)
        else:
            print(msg)

    logmsg("检查 LibreHardwareMonitor 状态...")

    # 第一次检查：有没有可用的温度传感器
    if has_lhm_sensors():
        logmsg("检测到 root\\LibreHardwareMonitor 已有温度传感器，直接使用现有实例。")
        return

    logmsg("未检测到有效的 LibreHardwareMonitor 温度传感器，准备启动内置 LibreHardwareMonitor.exe ...")

    # 找 exe
    try:
        exe = find_lhm_exe()
    except FileNotFoundError as e:
        logmsg(str(e))
        return

    # 后台启动（不弹黑框）
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen([exe], creationflags=creationflags)
        logmsg(f"已启动 LibreHardwareMonitor：{exe}")
        # 给它一点时间初始化并注册 WMI 命名空间
        time.sleep(3.0)
    except Exception as e:
        logmsg(f"启动 LibreHardwareMonitor 失败：{e}")
        return

    # 再检查一次传感器是否出现
    if has_lhm_sensors():
        logmsg("内置 LibreHardwareMonitor 已启动并提供温度传感器。")
    else:
        logmsg("警告：启动内置 LibreHardwareMonitor 后仍未检测到温度传感器。")


# ---------- 曲线图控件 ----------

class FanCurveWidget(QWidget):
    """
    简易风扇曲线图：
    X 轴：温度（°C）
    Y 轴：风扇百分比（0–100）
    折线 = 曲线，蓝色 X = 当前温度/转速点
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.curve_points = []      # [(temp, fan), ...]
        self.current_point = None   # (temp, fan) or None
        self.setMinimumHeight(160)

    def set_curve_points(self, points):
        self.curve_points = points or []
        self.update()

    def set_current_point(self, temp, fan):
        if temp is None or fan is None:
            self.current_point = None
        else:
            self.current_point = (float(temp), float(fan))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.fillRect(self.rect(), QColor(250, 250, 250))

        if not self.curve_points:
            return

        rect = self.rect()
        margin_left = 40
        margin_right = 20
        margin_top = 20
        margin_bottom = 30

        plot_rect = rect.adjusted(
            margin_left,
            margin_top,
            -margin_right,
            -margin_bottom,
        )

        temps = [p[0] for p in self.curve_points]
        fans = [p[1] for p in self.curve_points] + [0, 100]

        t_min, t_max = min(temps), max(temps)
        if t_max == t_min:
            t_min -= 5
            t_max += 5

        f_min, f_max = min(fans), max(fans)
        if f_max == f_min:
            f_min = 0
            f_max = 100

        def map_x(temp):
            return plot_rect.left() + (temp - t_min) / (t_max - t_min) * plot_rect.width()

        def map_y(fan):
            return plot_rect.bottom() - (fan - f_min) / (f_max - f_min) * plot_rect.height()

        # 坐标轴
        pen_axis = QPen(QColor(150, 150, 150))
        pen_axis.setWidth(2)
        painter.setPen(pen_axis)
        painter.drawLine(
            int(plot_rect.left()),
            int(plot_rect.bottom()),
            int(plot_rect.right()),
            int(plot_rect.bottom()),
        )
        painter.drawLine(
            int(plot_rect.left()),
            int(plot_rect.top()),
            int(plot_rect.left()),
            int(plot_rect.bottom()),
        )

        # Y 轴刻度（0,50,100）
        painter.setPen(QPen(QColor(180, 180, 180)))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for val in (0, 50, 100):
            y = map_y(val)
            painter.drawLine(
                int(plot_rect.left() - 5),
                int(y),
                int(plot_rect.left()),
                int(y),
            )
            painter.drawText(
                5,
                int(y + 3),
                f"{val}%",
            )

        # X 轴刻度（用曲线点的温度）
        for t in temps:
            x = map_x(t)
            painter.drawLine(
                int(x),
                int(plot_rect.bottom()),
                int(x),
                int(plot_rect.bottom() + 5),
            )
            painter.drawText(
                int(x - 12),
                int(plot_rect.bottom() + 18),
                f"{t}°",
            )

        # 画曲线
        pts = sorted(self.curve_points, key=lambda x: x[0])
        if len(pts) >= 2:
            pen_curve = QPen(QColor(80, 80, 80))
            pen_curve.setWidth(2)
            painter.setPen(pen_curve)
            prev_x = map_x(pts[0][0])
            prev_y = map_y(pts[0][1])
            for temp, fan in pts[1:]:
                x = map_x(temp)
                y = map_y(fan)
                painter.drawLine(
                    int(prev_x),
                    int(prev_y),
                    int(x),
                    int(y),
                )
                prev_x, prev_y = x, y

        # 当前点：蓝色 X
        if self.current_point is not None:
            temp, fan = self.current_point
            x = map_x(temp)
            y = map_y(fan)
            size = 6
            pen_x = QPen(QColor(0, 80, 200))
            pen_x.setWidth(2)
            painter.setPen(pen_x)
            painter.drawLine(
                int(x - size),
                int(y - size),
                int(x + size),
                int(y + size),
            )
            painter.drawLine(
                int(x - size),
                int(y + size),
                int(x + size),
                int(y - size),
            )


# ---------- LibreHardwareMonitor 读取封装 ----------

class LibreHWReader:
    """在工作线程中通过 WMI 访问 LibreHardwareMonitor 的温度传感器"""

    def __init__(self):
        try:
            self.conn = wmi.WMI(namespace="root\\LibreHardwareMonitor")
        except wmi.x_wmi as e:
            raise RuntimeError(
                "无法连接到 root\\LibreHardwareMonitor。\n\n"
                "请确认 LibreHardwareMonitor 已启动。"
            ) from e

    def read_max_cpu_temp(self):
        all_cpu_temps = {}
        max_core = None
        package_temp = None

        for sensor in self.conn.Sensor(SensorType="Temperature"):
            if sensor.Value is None:
                continue

            name = sensor.Name
            upper_name = name.upper()
            value = float(sensor.Value)

            if "CPU" in upper_name:
                all_cpu_temps[name] = value

        if "CPU Package" in all_cpu_temps:
            package_temp = all_cpu_temps["CPU Package"]

        for name, value in all_cpu_temps.items():
            if name.upper().startswith("CPU CORE #"):
                if (max_core is None) or (value > max_core):
                    max_core = value

        if max_core is not None:
            return max_core
        return package_temp


# ---------- 后台线程：周期读取温度 ----------

class TempWorker(QThread):
    tempsUpdated = pyqtSignal(object, float)  # max_temp, dt_ms
    errorOccurred = pyqtSignal(str, float)    # error_message, dt_ms

    def __init__(self, interval_sec=TEMP_POLL_INTERVAL, parent=None):
        super().__init__(parent)
        self.interval_sec = interval_sec
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        pythoncom.CoInitialize()
        try:
            try:
                reader = LibreHWReader()
            except Exception as e:
                self.errorOccurred.emit(str(e), 0.0)
                return

            while self._running:
                start = time.perf_counter()
                try:
                    max_temp = reader.read_max_cpu_temp()
                    dt_ms = (time.perf_counter() - start) * 1000.0
                    self.tempsUpdated.emit(max_temp, dt_ms)
                except Exception as e:
                    dt_ms = (time.perf_counter() - start) * 1000.0
                    self.errorOccurred.emit(str(e), dt_ms)

                total = self.interval_sec
                step = 0.1
                loops = int(total / step)
                for _ in range(loops):
                    if not self._running:
                        break
                    time.sleep(step)
        finally:
            pythoncom.CoUninitialize()


# ---------- 主窗口 ----------

class MainWindow(QMainWindow):
    def __init__(self, ipmi_exe: str):
        super().__init__()
        self.ipmi_exe = ipmi_exe
        self.last_auto_target = None
        self.last_max_temp = None

        self.setWindowTitle("X11 Fan Master - 自动曲线")
        self.resize(800, 650)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # UI
        self.create_temperature_group(main_layout)
        self.create_auto_control_group(main_layout)
        self.create_manual_control_group(main_layout)
        self.create_log_area(main_layout)

        # 这里检查 / 启动 LibreHardwareMonitor，并写入日志区
        ensure_lhm_running(log=self.append_log)

        # 温度线程
        self.worker = TempWorker(interval_sec=TEMP_POLL_INTERVAL, parent=self)
        self.worker.tempsUpdated.connect(self.on_temps_updated)
        self.worker.errorOccurred.connect(self.on_temp_error)
        self.worker.start()

        self.append_log(f"使用 IPMICFG：{self.ipmi_exe}")
        if not is_admin():
            self.append_log("警告：当前进程不是管理员，IPMICFG 可能无法访问 BMC。")

        # 初始化曲线图
        self.update_curve_widget()

    # ----- UI 构建 -----

    def create_temperature_group(self, layout: QVBoxLayout):
        group = QGroupBox("温度")
        vbox = QVBoxLayout(group)

        font_label = QFont()
        font_label.setPointSize(11)

        font_big = QFont()
        font_big.setPointSize(20)
        font_big.setBold(True)

        row = QHBoxLayout()
        self.cpu_label = QLabel("CPU 最大温度：")
        self.cpu_label.setFont(font_label)
        self.cpu_value = QLabel("--.- °C")
        self.cpu_value.setFont(font_big)
        row.addWidget(self.cpu_label)
        row.addStretch()
        row.addWidget(self.cpu_value)
        vbox.addLayout(row)

        row2 = QHBoxLayout()
        self.delay_label = QLabel("上次读取：-- ms")
        self.delay_label.setFont(font_label)
        row2.addWidget(self.delay_label)
        row2.addStretch()
        vbox.addLayout(row2)

        layout.addWidget(group)

    def create_auto_control_group(self, layout: QVBoxLayout):
        group = QGroupBox("自动控制")
        vbox = QVBoxLayout(group)

        self.auto_check = QCheckBox("启用自动风扇控制")
        self.auto_check.toggled.connect(self.on_auto_toggled)
        vbox.addWidget(self.auto_check)

        curve_box = QGroupBox("风扇曲线 (°C → %)")
        curve_layout = QVBoxLayout(curve_box)

        self.temp_spins = []
        self.fan_spins = []

        # 默认曲线：50/65/75/80℃ → 20/30/60/100%
        default_temps = [50, 65, 75, 80]
        default_fans = [20, 30, 60, 100]

        for i in range(4):
            row = QHBoxLayout()
            label = QLabel(f"点 {i+1} 温度：")
            temp_spin = QSpinBox()
            temp_spin.setRange(-20, 120)
            temp_spin.setValue(default_temps[i])
            temp_spin.valueChanged.connect(
                lambda _v, self=self: self.update_curve_widget()
            )

            fan_label = QLabel("风扇占空比：")
            fan_spin = QSpinBox()
            fan_spin.setRange(0, 100)
            fan_spin.setValue(default_fans[i])
            fan_spin.valueChanged.connect(
                lambda _v, self=self: self.update_curve_widget()
            )

            self.temp_spins.append(temp_spin)
            self.fan_spins.append(fan_spin)

            row.addWidget(label)
            row.addWidget(temp_spin)
            row.addStretch()
            row.addWidget(fan_label)
            row.addWidget(fan_spin)
            curve_layout.addLayout(row)

        vbox.addWidget(curve_box)

        self.curve_widget = FanCurveWidget()
        vbox.addWidget(self.curve_widget)

        row_target = QHBoxLayout()
        self.auto_target_label = QLabel("当前自动目标：-- %")
        row_target.addWidget(self.auto_target_label)
        row_target.addStretch()
        vbox.addLayout(row_target)

        layout.addWidget(group)

    def create_manual_control_group(self, layout: QVBoxLayout):
        group = QGroupBox("手动风扇控制")
        vbox = QVBoxLayout(group)

        row_cpu = QHBoxLayout()
        self.cpu_slider_label = QLabel("CPU 风扇")
        self.cpu_slider_value = QLabel("0%")
        self.cpu_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_slider.setRange(0, 100)
        self.cpu_slider.valueChanged.connect(
            lambda v: (
                self.cpu_slider_value.setText(f"{v}%"),
                self.update_curve_widget(),
            )
        )
        self.cpu_slider.sliderReleased.connect(self.on_cpu_manual_released)

        row_cpu.addWidget(self.cpu_slider_label)
        row_cpu.addWidget(self.cpu_slider)
        row_cpu.addWidget(self.cpu_slider_value)
        vbox.addLayout(row_cpu)

        row_per = QHBoxLayout()
        self.per_slider_label = QLabel("外设风扇")
        self.per_slider_value = QLabel("0%")
        self.per_slider = QSlider(Qt.Orientation.Horizontal)
        self.per_slider.setRange(0, 100)
        self.per_slider.valueChanged.connect(
            lambda v: self.per_slider_value.setText(f"{v}%")
        )
        self.per_slider.sliderReleased.connect(self.on_per_manual_released)

        row_per.addWidget(self.per_slider_label)
        row_per.addWidget(self.per_slider)
        row_per.addWidget(self.per_slider_value)
        vbox.addLayout(row_per)

        reset_btn = QPushButton("恢复 BMC 自动风扇模式")
        reset_btn.clicked.connect(self.on_reset_bmc_auto)
        vbox.addWidget(reset_btn)

        layout.addWidget(group)

    def create_log_area(self, layout: QVBoxLayout):
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit)

    # ----- 日志 & IPMI -----

    def append_log(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.log_edit.appendPlainText(f"[{ts}] {text}")
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum()
        )

    def run_ipmi(self, args, desc=""):
        cmd = [self.ipmi_exe] + args
        self.append_log(f"执行 IPMICFG：{' '.join(cmd)} {desc}")

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(
                cmd,
                cwd=os.path.dirname(self.ipmi_exe),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="mbcs",
                errors="ignore",
                creationflags=creationflags,
            )
        except Exception as e:
            self.append_log(f"IPMICFG 运行异常: {e}")
            return False

        if result.stdout.strip():
            self.append_log("IPMICFG 标准输出:\n" + result.stdout.strip())
        if result.stderr.strip():
            self.append_log("IPMICFG 错误输出:\n" + result.stderr.strip())

        if result.returncode != 0:
            self.append_log(f"IPMICFG 退出码 {result.returncode} (失败)")
            return False

        self.append_log("IPMICFG 命令执行成功。")
        return True

    def set_fan_pwm(self, zone: int, percent: int):
        p = max(0, min(100, int(round(percent))))
        hex_val = f"0x{p:02x}"
        zone_hex = f"0x{zone:02x}"
        args = ["-raw", "0x30", "0x70", "0x66", "0x01", zone_hex, hex_val]
        self.run_ipmi(args, desc=f"(zone={zone}, {p}%)")

    # ----- 自动控制 & 曲线图 -----

    def compute_auto_target(self, temp_c: float) -> int:
        points = [
            (self.temp_spins[i].value(), self.fan_spins[i].value())
            for i in range(4)
        ]
        points.sort(key=lambda x: x[0])

        if temp_c <= points[0][0]:
            return points[0][1]
        if temp_c >= points[-1][0]:
            return points[-1][1]

        for (t1, p1), (t2, p2) in zip(points, points[1:]):
            if t1 <= temp_c <= t2:
                if t2 == t1:
                    return max(p1, p2)
                frac = (temp_c - t1) / (t2 - t1)
                val = p1 + frac * (p2 - p1)
                return int(round(val))

        return points[-1][1]

    def apply_auto_from_temp(self, temp_c: float):
        if temp_c is None:
            return
        target = self.compute_auto_target(temp_c)
        self.auto_target_label.setText(f"当前自动目标：{target}%")
        if target == self.last_auto_target:
            return
        self.last_auto_target = target
        self.set_fan_pwm(0, target)
        self.set_fan_pwm(1, target)
        self.update_curve_widget()

    def update_curve_widget(self):
        points = [
            (self.temp_spins[i].value(), self.fan_spins[i].value())
            for i in range(4)
        ]
        self.curve_widget.set_curve_points(points)

        if self.last_max_temp is None:
            self.curve_widget.set_current_point(None, None)
            return

        if self.auto_check.isChecked():
            y = self.compute_auto_target(self.last_max_temp)
        else:
            y = self.cpu_slider.value()

        self.curve_widget.set_current_point(self.last_max_temp, y)

    def on_auto_toggled(self, checked: bool):
        self.cpu_slider.setEnabled(not checked)
        self.per_slider.setEnabled(not checked)
        self.last_auto_target = None

        if checked and self.last_max_temp is not None:
            self.apply_auto_from_temp(self.last_max_temp)
        else:
            self.auto_target_label.setText("当前自动目标：-- %")
            self.update_curve_widget()

    # ----- 温度线程回调 -----

    def on_temps_updated(self, max_temp, dt_ms: float):
        self.last_max_temp = max_temp

        if max_temp is None:
            self.cpu_value.setText("--.- °C")
            self.cpu_value.setStyleSheet("color: gray;")
        else:
            self.cpu_value.setText(f"{max_temp:.1f} °C")
            if max_temp >= 80.0:
                self.cpu_value.setStyleSheet("color: red;")
            else:
                self.cpu_value.setStyleSheet("color: black;")

        self.delay_label.setText(f"上次读取：{dt_ms:.0f} ms")

        if self.auto_check.isChecked() and max_temp is not None:
            self.apply_auto_from_temp(max_temp)
        else:
            self.update_curve_widget()

    def on_temp_error(self, message: str, dt_ms: float):
        self.cpu_value.setText("--.- °C")
        self.cpu_value.setStyleSheet("color: gray;")
        self.delay_label.setText(f"读取失败，耗时 {dt_ms:.0f} ms")
        self.append_log(f"读取 LibreHardwareMonitor 温度失败：{message}")
        self.update_curve_widget()

    # ----- 手动控制槽函数 -----

    def on_cpu_manual_released(self):
        value = self.cpu_slider.value()
        self.set_fan_pwm(0, value)
        self.update_curve_widget()

    def on_per_manual_released(self):
        value = self.per_slider.value()
        self.set_fan_pwm(1, value)

    def on_reset_bmc_auto(self):
        args = ["-raw", "0x30", "0x45", "0x01", "0x01"]
        ok = self.run_ipmi(args, desc="(reset to BMC auto fan mode)")
        if ok:
            self.cpu_slider.setValue(0)
            self.per_slider.setValue(0)
            self.last_auto_target = None
            self.update_curve_widget()

    # ----- 关闭窗口时，停线程 -----

    def closeEvent(self, event):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
        event.accept()


# ---------- 程序入口 ----------

def main():
    if not is_admin():
        print("警告：当前进程不是管理员，IPMICFG 可能无法访问 BMC。")

    try:
        ipmi_exe = find_ipmicfg()
    except FileNotFoundError as e:
        print(e)
        return

    app = QApplication(sys.argv)
    window = MainWindow(ipmi_exe)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
