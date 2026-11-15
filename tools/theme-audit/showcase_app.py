#!/usr/bin/env python3
"""showcase_app — 「设置页模仿窗」截图器：同一窗口在 GTK4/GTK3/Qt 各渲染一遍。

用法（由 theme-audit.py showcase 编排，也可单独跑）：
    python3 showcase_app.py --toolkit gtk4 --out /tmp/gtk4.png   # Xvfb 内
    python3 showcase_app.py --toolkit qt   --out /tmp/qt.png     # offscreen

窗口内容模仿 shell 控制中心设置模块（DESIGN §3.5.3/§3.5.4，zh_CN 文案）：
内容宽 352 px、窗口边距 12、组间距 10，三组「 boxed-list / Frame /
QGroupBox 」行 + 一条散落控件带。只用标准控件、不挂任何 CSS/QSS——
截的是主题自己的脸。

三组行内容（对照 BrightnessSubTab / ColorsSubTab）：
- A 亮度滑块 49% + 背光设备下拉「默认(intel_backlight)」（170 px）；
- B 亮度步长 SpinBox=7（170）+ 两条 Switch 行（Qt 无开关，用 QCheckBox）；
- C 自定义强调色 Entry（占位「例如 #2CA7F8」，170）+ 深色模式计划下拉「位置」。

快照口径：GTK4 = WidgetPaintable→Gsk render_texture→PNG（同 gtk_probe）；
GTK3 = 窗口 cairo draw→PNG；Qt = widget.grab()。主循环等待一律
iteration(False)+usleep 有界泵，不用 iteration(True) 忙等。
"""
import argparse
import os
import sys

CONTENT_W = 352          # DESIGN §3.5.3：frame 模式内容列宽
WINDOW_MARGIN = 12
GROUP_SPACING = 10
FIELD_W = 170            # 右侧控件列宽（shell 行内 field 宽）
# GTK4 实测行高（showcase 几何基准）：字段行 52 / 开关行 38 / 滑块行 56，
# 含 boxed-list 行上下 6px padding；Qt 侧逐行对齐。
ROW_FIELD = 52
ROW_SWITCH = 38
ROW_SLIDER = 56

L_BRIGHTNESS = "亮度"
L_BACKLIGHT = "背光设备"
L_STEP = "亮度步长"
L_MIN1 = "最低亮度限制为 1%"
L_EXT = "外部显示器亮度支持"
L_ACCENT = "自定义强调色"
L_DARKMODE = "深色模式计划"
PH_ACCENT = "例如 #2CA7F8"
BACKLIGHT_OPT = "默认(intel_backlight)"
DARKMODE_OPTS = ["关", "手动", "位置", "跟随系统"]
DARKMODE_CURRENT = "位置"
L_CANCEL = "取消"
L_OK = "确定"


# ---------------------------------------------------------------- GTK4

def pump_gtk(win, ready, extra_focus=None):
    """有界非阻塞主循环泵：等映射 + 若干空转帧让 CSS/焦点环画完。"""
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + 5_000_000
    while not ready() and GLib.get_monotonic_time() < deadline:
        if not ctx.iteration(False):
            GLib.usleep(10_000)
    if extra_focus is not None:
        extra_focus()
    for _ in range(40):
        if not ctx.iteration(False):
            GLib.usleep(10_000)


def activate_toplevel(win, gtk4):
    """Xvfb 无 WM：XSetInputFocus 让顶层窗口脱离 :backdrop，焦点环才画。"""
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    surf = win.get_surface() if gtk4 else win.get_window()
    try:
        surf.focus(0)          # gdk_toplevel_focus / gdk_window_focus
    except Exception as e:     # 非 X11 后端静默退回
        print(f"[warn] activate fallback: {e}", file=sys.stderr)
    for _ in range(20):
        if not ctx.iteration(False):
            GLib.usleep(10_000)
    print(f"[info] window active={win.is_active()}", file=sys.stderr)


def run_gtk4(out):
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Gsk

    Gtk.init()
    win = Gtk.Window()
    win.set_default_size(CONTENT_W + 2 * WINDOW_MARGIN, -1)
    print("gtk-theme=" + str(
        Gtk.Settings.get_default().get_property("gtk-theme-name")),
        file=sys.stderr)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GROUP_SPACING)
    for m in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
        getattr(outer, f"set_{m}")(WINDOW_MARGIN)
    win.set_child(outer)

    def group():
        lb = Gtk.ListBox()
        lb.add_css_class("boxed-list")
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        outer.append(lb)
        return lb

    def hbox(margins=True):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if margins:
            b.set_margin_start(10)
            b.set_margin_end(10)
            b.set_margin_top(8)
            b.set_margin_bottom(8)
        return b

    def row(label_text, control):
        box = hbox()
        lab = Gtk.Label(label=label_text, xalign=0)
        lab.set_hexpand(True)
        box.append(lab)
        box.append(control)
        r = Gtk.ListBoxRow()
        r.set_child(box)
        return r

    # ---- 组 A：亮度滑块 + 背光设备
    ga = group()
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    vbox.set_margin_start(10)
    vbox.set_margin_end(10)
    vbox.set_margin_top(8)
    vbox.set_margin_bottom(8)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    head.append(Gtk.Label(label=L_BRIGHTNESS, xalign=0, hexpand=True))
    head.append(Gtk.Label(label="49%", xalign=1))
    vbox.append(head)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(49)
    scale.set_hexpand(True)
    scale.set_draw_value(False)
    vbox.append(scale)
    r = Gtk.ListBoxRow()
    r.set_child(vbox)
    ga.append(r)

    cb = Gtk.DropDown.new_from_strings([BACKLIGHT_OPT, "intel_backlight"])
    cb.set_selected(0)
    cb.set_size_request(FIELD_W, -1)
    ga.append(row(L_BACKLIGHT, cb))

    # ---- 组 B：步长 + 两条开关
    gb = group()
    spin = Gtk.SpinButton.new_with_range(1, 50, 1)
    spin.set_value(7)
    spin.set_size_request(FIELD_W, -1)
    gb.append(row(L_STEP, spin))
    sw_on = Gtk.Switch()
    sw_on.set_active(True)
    gb.append(row(L_MIN1, sw_on))
    sw_off = Gtk.Switch()
    gb.append(row(L_EXT, sw_off))

    # ---- 组 C：强调色输入 + 深色模式计划
    gc = group()
    en = Gtk.Entry()
    en.set_placeholder_text(PH_ACCENT)
    en.set_size_request(FIELD_W, -1)
    gc.append(row(L_ACCENT, en))
    cb2 = Gtk.DropDown.new_from_strings(DARKMODE_OPTS)
    cb2.set_selected(DARKMODE_OPTS.index(DARKMODE_CURRENT))
    cb2.set_size_request(FIELD_W, -1)
    gc.append(row(L_DARKMODE, cb2))

    # ---- 散落控件带
    strip = hbox(margins=False)
    strip.append(Gtk.Button(label=L_CANCEL))
    ok = Gtk.Button(label=L_OK)
    strip.append(ok)
    chk = Gtk.CheckButton()
    chk.set_active(True)
    strip.append(chk)
    rgrp = Gtk.CheckButton()
    rdo = Gtk.CheckButton()
    rdo.set_group(rgrp)
    rdo.add_css_class("radio")
    rdo.set_active(True)
    strip.append(rdo)
    outer.append(strip)

    pb = Gtk.ProgressBar()
    pb.set_fraction(0.6)
    outer.append(pb)

    focus_entry = Gtk.Entry()
    outer.append(focus_entry)

    win.present()
    pump_gtk(win, lambda: win.is_visible() and win.get_width() > 1,
             extra_focus=focus_entry.grab_focus)
    activate_toplevel(win, gtk4=True)
    focus_entry.grab_focus()

    snap = Gtk.Snapshot()
    paintable = Gtk.WidgetPaintable.new(win)
    paintable.snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    if node is None:
        sys.exit("gtk4 showcase: 空 render node")
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(win.get_display())
    tex = renderer.render_texture(node, node.get_bounds())
    tex.save_to_png(out)
    renderer.unrealize()
    win.destroy()


# ---------------------------------------------------------------- GTK3

def run_gtk3(out):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import cairo

    win = Gtk.Window()
    win.set_default_size(CONTENT_W + 2 * WINDOW_MARGIN, -1)
    print("gtk-theme=" + str(
        Gtk.Settings.get_default().get_property("gtk-theme-name")),
        file=sys.stderr)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GROUP_SPACING)
    for m in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
        getattr(outer, f"set_{m}")(WINDOW_MARGIN)
    win.add(outer)

    def group():
        lb = Gtk.ListBox()
        lb.get_style_context().add_class("boxed-list")
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        outer.pack_start(lb, False, False, 0)
        return lb

    def hbox(margins=True):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if margins:
            b.set_margin_start(10)
            b.set_margin_end(10)
            b.set_margin_top(8)
            b.set_margin_bottom(8)
        return b

    def row(label_text, control):
        box = hbox()
        lab = Gtk.Label(label=label_text, xalign=0)
        lab.set_hexpand(True)
        box.pack_start(lab, True, True, 0)
        box.pack_start(control, False, False, 0)
        r = Gtk.ListBoxRow()
        r.add(box)
        return r

    ga = group()
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    vbox.set_margin_start(10)
    vbox.set_margin_end(10)
    vbox.set_margin_top(8)
    vbox.set_margin_bottom(8)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    head.pack_start(Gtk.Label(label=L_BRIGHTNESS, xalign=0), True, True, 0)
    head.pack_start(Gtk.Label(label="49%", xalign=1), False, False, 0)
    vbox.pack_start(head, False, False, 0)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_value(49)
    scale.set_draw_value(False)
    vbox.pack_start(scale, False, False, 0)
    r = Gtk.ListBoxRow()
    r.add(vbox)
    ga.add(r)

    cb = Gtk.ComboBoxText()
    cb.append("auto", BACKLIGHT_OPT)
    cb.append("intel_backlight", "intel_backlight")
    cb.set_active(0)
    cb.set_size_request(FIELD_W, -1)
    ga.add(row(L_BACKLIGHT, cb))

    gb = group()
    spin = Gtk.SpinButton.new_with_range(1, 50, 1)
    spin.set_value(7)
    spin.set_size_request(FIELD_W, -1)
    gb.add(row(L_STEP, spin))
    sw_on = Gtk.Switch()
    sw_on.set_active(True)
    gb.add(row(L_MIN1, sw_on))
    sw_off = Gtk.Switch()
    gb.add(row(L_EXT, sw_off))

    gc = group()
    en = Gtk.Entry()
    en.set_placeholder_text(PH_ACCENT)
    en.set_size_request(FIELD_W, -1)
    gc.add(row(L_ACCENT, en))
    cb2 = Gtk.ComboBoxText()
    for i, t in enumerate(DARKMODE_OPTS):
        cb2.append(str(i), t)
    cb2.set_active(DARKMODE_OPTS.index(DARKMODE_CURRENT))
    cb2.set_size_request(FIELD_W, -1)
    gc.add(row(L_DARKMODE, cb2))

    strip = hbox(margins=False)
    strip.pack_start(Gtk.Button(label=L_CANCEL), False, False, 0)
    ok = Gtk.Button(label=L_OK)
    strip.pack_start(ok, False, False, 0)
    chk = Gtk.CheckButton()
    chk.set_active(True)
    strip.pack_start(chk, False, False, 0)
    rdo = Gtk.RadioButton.new_from_widget(None)
    rdo.set_active(True)
    strip.pack_start(rdo, False, False, 0)
    outer.pack_start(strip, False, False, 0)

    pb = Gtk.ProgressBar()
    pb.set_fraction(0.6)
    outer.pack_start(pb, False, False, 0)

    focus_entry = Gtk.Entry()
    outer.pack_start(focus_entry, False, False, 0)

    win.show_all()
    pump_gtk(win, win.get_mapped, extra_focus=focus_entry.grab_focus)
    activate_toplevel(win, gtk4=False)
    focus_entry.grab_focus()

    alloc = win.get_allocation()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surf)
    win.draw(cr)
    surf.write_to_png(out)
    win.destroy()


# ---------------------------------------------------------------- Qt

def run_qt(out):
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QLabel, QPushButton, QCheckBox, QRadioButton,
        QSlider, QProgressBar, QLineEdit, QComboBox, QSpinBox, QGroupBox,
        QVBoxLayout, QHBoxLayout,
    )
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPalette

    app = QApplication(sys.argv)
    print(f"style={QApplication.style().objectName()}", file=sys.stderr)
    win = QWidget()
    outer = QVBoxLayout(win)
    outer.setContentsMargins(WINDOW_MARGIN, WINDOW_MARGIN,
                             WINDOW_MARGIN, WINDOW_MARGIN)
    outer.setSpacing(GROUP_SPACING)
    win.setFixedWidth(CONTENT_W + 2 * WINDOW_MARGIN)

    groups = []

    def group():
        gb = QGroupBox()          # 无标题组框 ≈ boxed-list 分组
        lay = QVBoxLayout(gb)
        gb.ensurePolished()
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(0)
        outer.addWidget(gb)
        groups.append((gb, lay))
        return lay

    def sep(lay):
        # shell 分隔线 = fg×0.15 叠在组面上；Kvantum HLine 不显色，应用层自绘
        pal = win.palette()
        face = pal.color(QPalette.ColorRole.Button).getRgb()
        fg = pal.color(QPalette.ColorRole.Text).getRgb()
        c = [round(f * 0.85 + t * 0.15) for f, t in zip(face[:3], fg[:3])]
        f = QWidget()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background-color: rgb({c[0]},{c[1]},{c[2]});")
        lay.addWidget(f)

    def row(lay, label_text, control, field=True):
        if lay.count():
            sep(lay)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(label_text), 1)
        if field:
            control.setFixedWidth(FIELD_W)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignRight)
        w = QWidget()
        w.setLayout(h)
        w.setFixedHeight(ROW_FIELD if field else ROW_SWITCH)
        lay.addWidget(w)

    # ---- 组 A
    ga = group()
    av = QVBoxLayout()
    av.setContentsMargins(0, 8, 0, 8)
    av.setSpacing(4)
    head = QHBoxLayout()
    head.addWidget(QLabel(L_BRIGHTNESS), 1)
    head.addWidget(QLabel("49%"), 0)
    av.addLayout(head)
    sl = QSlider(Qt.Orientation.Horizontal)
    sl.setRange(0, 100)
    sl.setValue(49)
    av.addWidget(sl)
    aw = QWidget()
    aw.setLayout(av)
    aw.setFixedHeight(ROW_SLIDER)
    ga.addWidget(aw)
    cb = QComboBox()
    cb.addItems([BACKLIGHT_OPT, "intel_backlight"])
    row(ga, L_BACKLIGHT, cb)

    # ---- 组 B（Qt 无开关控件：check 行 = 右对齐 QCheckBox 指示器）
    gb = group()
    sp = QSpinBox()
    sp.setRange(1, 50)
    sp.setValue(7)
    row(gb, L_STEP, sp)
    ck1 = QCheckBox()
    ck1.setChecked(True)
    row(gb, L_MIN1, ck1, field=False)
    ck2 = QCheckBox()
    row(gb, L_EXT, ck2, field=False)

    # ---- 组 C
    gc = group()
    le = QLineEdit()
    le.setPlaceholderText(PH_ACCENT)
    row(gc, L_ACCENT, le)
    cb2 = QComboBox()
    cb2.addItems(DARKMODE_OPTS)
    cb2.setCurrentText(DARKMODE_CURRENT)
    row(gc, L_DARKMODE, cb2)

    # Kvantum 组框装饰自带 overhead，总高固定为行高之和，与 GTK boxed-list 对齐
    for gb, lay in groups:
        h = sum(lay.itemAt(i).widget().minimumHeight()
                for i in range(lay.count()))
        gb.setFixedHeight(h)

    # ---- 散落控件带
    strip = QHBoxLayout()
    strip.addWidget(QPushButton(L_CANCEL))
    ok = QPushButton(L_OK)
    ok.setDefault(True)
    strip.addWidget(ok)
    ck = QCheckBox()
    ck.setChecked(True)
    strip.addWidget(ck)
    rb = QRadioButton()
    rb.setChecked(True)
    strip.addWidget(rb)
    strip.addStretch(1)
    outer.addLayout(strip)

    pb = QProgressBar()
    pb.setRange(0, 100)
    pb.setValue(60)
    pb.setTextVisible(False)
    outer.addWidget(pb)

    focus_entry = QLineEdit()
    outer.addWidget(focus_entry)

    win.resize(CONTENT_W + 2 * WINDOW_MARGIN,
               win.sizeHint().height())
    win.show()
    app.processEvents()
    app.setActiveWindow(win)
    win.activateWindow()
    focus_entry.setFocus()
    # Kvantum animate_states：焦点环随动画淡入，grab 前等动画落定
    import time
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    print(f"[info] qt active={win.isActiveWindow()} "
          f"focus={focus_entry.hasFocus()}", file=sys.stderr)
    win.grab().save(out)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolkit", choices=("gtk4", "gtk3", "qt"), required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    {"gtk4": run_gtk4, "gtk3": run_gtk3, "qt": run_qt}[a.toolkit](a.out)


if __name__ == "__main__":
    main()
