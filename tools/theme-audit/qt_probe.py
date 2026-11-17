#!/usr/bin/env python3
"""Kvantum/Qt6 控件级探针：PyQt6 offscreen 平台实例化控件、grab() 抓像素。

用法（由 theme-audit.py 编排）：
    QT_QPA_PLATFORM=offscreen python3 qt_probe.py --out /tmp/probe.json --shots /tmp/shots

环境（编排器已设置）：
- XDG_CONFIG_HOME 指向含 Kvantum/{kvantum.kvconfig,KvLibadwaita/*} 与 qt6ct 的舞台；
- QT_QPA_PLATFORMTHEME=qt6ct（走完整部署链：qt6ct 读 style=Kvantum）；
- PYTHONPATH 指向 PyQt6 与 pyqt6-sip 的 site-packages。

输出 JSON：palette 角色实测值（QPalette 直读，验证 [GeneralColors] 契约）、
各控件 grab 图的中心像素与 alpha 几何测量。
"""
import argparse
import json
import os
import sys


def hexof(c):
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


def img_to_pil(img):
    from PIL import Image
    img = img.convertToFormat(img.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    data = bytes(ptr.asstring(w * h * 4))
    return Image.frombytes("RGBA", (w, h), data)


def measure_alpha(img):
    a = img.getchannel("A")
    w, h = img.size
    px = a.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if px[x, y] > 8:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {"empty": True}
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    m = {"w": x1 - x0 + 1, "h": y1 - y0 + 1}

    def corner(top, left):
        edge_y = y0 if top else y1
        edge_x = x0 if left else x1
        max_d = min(20, (y1 - y0) // 2)
        first = None
        for d in range(max_d):
            y = edge_y + (d if top else -d)
            xs_ = [x for x in range(edge_x, edge_x + (1 if left else -1) * min(30, w), 1 if left else -1)
                   if px[x, y] > 8]
            if not xs_:
                continue
            inner = abs(xs_[0] - edge_x)
            if first is None:
                first = inner
            if inner <= max(0, first * 0.05):
                return max(0, d)
        return None

    m["r_tl"] = corner(True, True)
    m["r_tr"] = corner(True, False)
    m["r_bl"] = corner(False, True)
    m["r_br"] = corner(False, False)
    return m

def measure_face(img, tol=14, aa=False):
    """在主色区域掩码上测控件面的几何与圆角（排除文字/边距像素干扰）。"""
    from collections import Counter
    rgba = img.convert("RGBA").load()
    w, h = img.size
    cnt = Counter()
    for y in range(h):
        for x in range(w):
            p = rgba[x, y]
            if p[3] > 200:
                cnt[p[:3]] += 1
    if not cnt:
        return {"empty": True}
    r0, g0, b0 = cnt.most_common(1)[0][0]
    # Kvantum 把 widget 矩形画满（无 alpha），可见圆角来自描边/辉光弧线。
    # 角部是平涂窗口色，控件区有渐变/描边——以"与字面角像素差异"划分控件外，
    # 容差收紧到 ≤3 才能分开 surface 面与 window 底。
    cp = rgba[0, 0]
    def outside(p):
        return (p[3] > 100 and abs(p[0]-cp[0]) <= 3
                and abs(p[1]-cp[1]) <= 3 and abs(p[2]-cp[2]) <= 3)
    def near(p):
        # 控件剪影 = 一切非"窗口透出"像素：面色可能与描边差到 tol 外
        #（field 面 + outline 描边分属两个键），角部内缩量在剪影上。
        if aa:
            # 小圆角（<=4px）端帽全是 AA 混合像素：剪影掩码把凹口 AA 也算
            # 面内，内缩恒 0。按"离面色近还是离窗底近"二分覆盖度 >0.5。
            return (p[3] > 100
                    and abs(p[0]-r0)+abs(p[1]-g0)+abs(p[2]-b0)
                    <= abs(p[0]-cp[0])+abs(p[1]-cp[1])+abs(p[2]-cp[2]))
        return p[3] > 100 and not outside(p)
    def near_plain(p):
        return (p[3] > 100 and abs(p[0]-r0) <= tol and abs(p[1]-g0) <= tol
                and abs(p[2]-b0) <= tol)
    def scan(pred):
        xs_, ys_ = [], []
        for y in range(h):
            for x in range(w):
                if pred(rgba[x, y]):
                    xs_.append(x); ys_.append(y)
        return xs_, ys_
    xs, ys = scan(near)
    if len(xs) < 5:
        # 方形控件：角像素即面色，outside 排除过度——退化为不带排除的掩码
        xs, ys = scan(near_plain)
        near = near_plain
    if not xs:
        return {"empty": True}
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    m = {"w": x1 - x0 + 1, "h": y1 - y0 + 1}
    def corner(top, left):
        ey = y0 if top else y1
        ex = x0 if left else x1
        first = None
        pts = []
        for d in range(min(20, (y1 - y0) // 2 + 1)):
            y = ey + (d if top else -d)
            if not (0 <= y < h):
                continue
            step = 1 if left else -1
            xs_ = [x for x in range(ex, ex + step * min(40, w), step)
                   if 0 <= x < w and near(rgba[x, y])]
            if not xs_:
                continue
            inner = abs(xs_[0] - ex)
            if first is None:
                first = inner
            if inner <= max(0, first * 0.06):
                break
            if d >= 1 and inner >= 1:
                pts.append((d, inner))
        if not pts:
            return 0
        # 圆弧内缩剖面拟合：(R-inner)²+(R-d)²=R² → R=inner+d+sqrt(2·inner·d)。
        # 旧算法返回「变平深度」，小半径近似成立、胶囊半径系统性偏小。
        rs = sorted(i + d + (2 * i * d) ** 0.5 for d, i in pts)
        return rs[len(rs) // 2]
    m["r_tl"], m["r_tr"] = corner(True, True), corner(True, False)
    m["r_bl"], m["r_br"] = corner(False, True), corner(False, False)
    rs = [v for v in (m["r_tl"], m["r_tr"], m["r_bl"], m["r_br"]) if v is not None]
    m["radius_est"] = round(sum(rs) / len(rs)) if rs else None
    return m



def center_hex(img):
    w, h = img.size
    p = img.getpixel((w // 2, h // 2))
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def saturated_hex(img, min_alpha=200):
    """最饱和的不透明像素（accent 勾/点总是控件里饱和度最高的区域）。"""
    best, best_s = None, -1
    for r, g, b, a in img.convert("RGBA").getdata():
        if a < min_alpha:
            continue
        s = max(r, g, b) - min(r, g, b)
        if s > best_s:
            best_s, best = s, (r, g, b)
    return f"#{best[0]:02x}{best[1]:02x}{best[2]:02x}" if best else None


def strip_hex(img, min_alpha=200, rows=4):
    """底部条带（进度条/滑块填充区）里的主色。"""
    from collections import Counter
    w, h = img.size
    y0 = max(0, h - rows)
    cnt = Counter()
    for y in range(y0, h):
        for x in range(w):
            p = img.convert("RGBA").getpixel((x, y))
            if p[3] >= min_alpha:
                cnt[p[:3]] += 1
    if not cnt:
        return None
    r, g, b = cnt.most_common(1)[0][0]
    return f"#{r:02x}{g:02x}{b:02x}"


def edge_hex(img):
    p = img.convert("RGBA").getpixel((img.size[0] // 2, 0))
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def bar_region(img):
    """进度条渲染条的行范围：非背景像素铺满宽度的行。"""
    from collections import Counter
    rgba = img.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    bg = Counter(px[x, y] for y in range(h) for x in range(w)).most_common(1)[0][0]
    ys = [y for y in range(h)
          if sum(1 for x in range(w)
                 if px[x, y][3] > 8 and px[x, y] != bg) > w / 2]
    return (ys[0], ys[-1]) if ys else None


def pb_fill(img):
    """条左半（填充段）主色作为 strip，条居中时底部采样会落在背景上。"""
    reg = bar_region(img)
    if not reg:
        return {"bar_h": 0}
    from collections import Counter
    rgba = img.convert("RGBA")
    w, _ = rgba.size
    px = rgba.load()
    cnt = Counter(px[x, y][:3] for y in range(reg[0], reg[1] + 1)
                  for x in range(w // 2))
    r, g, b = cnt.most_common(1)[0][0]
    return {"bar_h": reg[1] - reg[0] + 1, "strip": f"#{r:02x}{g:02x}{b:02x}"}


def dominant_hex(img, min_alpha=200):
    from collections import Counter
    cnt = Counter()
    for r, g, b, a in img.getdata():
        if a >= min_alpha:
            cnt[(r, g, b)] += 1
    if not cnt:
        return None
    r, g, b = cnt.most_common(1)[0][0]
    return f"#{r:02x}{g:02x}{b:02x}"


def sample_at(img, fx, fy):
    w, h = img.size
    p = img.getpixel((int(w * fx), int(h * fy)))
    return f"#{p[0]:02x}{p[1]:02x}{p[2]:02x}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", required=True)
    a = ap.parse_args()

    from PyQt6.QtWidgets import (
        QApplication, QPushButton, QCheckBox, QRadioButton, QSlider,
        QProgressBar, QLineEdit, QComboBox, QSpinBox, QTabWidget,
        QMenuBar, QWidget, QLabel, QToolBar,
    )
    from PyQt6.QtCore import Qt, QRect, QPoint
    from PyQt6.QtGui import QPalette, QCursor

    app = QApplication(sys.argv)
    style_name = QApplication.style().objectName()
    out = {"style": style_name, "palette": {}, "widgets": {}}
    pal = app.palette()
    roles = {
        "window": QPalette.ColorRole.Window,
        "window_text": QPalette.ColorRole.WindowText,
        "base": QPalette.ColorRole.Base,
        "alt_base": QPalette.ColorRole.AlternateBase,
        "button": QPalette.ColorRole.Button,
        "button_text": QPalette.ColorRole.ButtonText,
        "text": QPalette.ColorRole.Text,
        "highlight": QPalette.ColorRole.Highlight,
        "highlight_text": QPalette.ColorRole.HighlightedText,
        "tool_tip_base": QPalette.ColorRole.ToolTipBase,
        "tool_tip_text": QPalette.ColorRole.ToolTipText,
        "link": QPalette.ColorRole.Link,
        "shadow": QPalette.ColorRole.Shadow,
        "placeholder_text": QPalette.ColorRole.PlaceholderText,
    }
    for k, r in roles.items():
        out["palette"][k] = hexof(pal.color(r))
    out["palette"]["disabled_text"] = hexof(
        pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))

    os.makedirs(a.shots, exist_ok=True)

    def settle(ms=400):
        # Kvantum animate_states=true：状态色/焦点环随动画淡入，
        # 立即 grab 会拍到 alpha 0。泵事件 + 真实等待让动画落定。
        import time
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)

    def grab(name, w, extra=None, focus=False, aa_face=False, hover=False):
        if not focus:
            # offscreen 下 show() 自动聚焦顶层控件且光标停在 (0,0) 落在
            # 控件内 = hover；常态采样须把光标挪出控件、禁焦点策略，
            # 保证测到 -normal 元素而不是 -focused。
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        w.adjustSize()
        w.show()
        app.processEvents()
        settle()
        if not focus:
            fw = app.focusWidget()
            if fw is not None:
                fw.clearFocus()
            if hover:
                # transient 滑块只在光标进入时才以全量 alpha 出现
                QCursor.setPos(w.mapToGlobal(w.rect().center()))
            else:
                QCursor.setPos(w.mapToGlobal(w.rect().bottomRight())
                               + QPoint(500, 500))
            app.processEvents()
            settle()
        if focus:
            app.setActiveWindow(w)
            w.setFocus()
            settle()
        pm = w.grab()
        pm.save(os.path.join(a.shots, f"{name}.png"))
        img = img_to_pil(pm.toImage())
        rec = {"size": [w.width(), w.height()], "center": center_hex(img),
               "dominant": dominant_hex(img), "saturated": saturated_hex(img),
               "strip": strip_hex(img),
               "geom": measure_alpha(img), "face": measure_face(img, aa=aa_face)}
        if extra:
            rec.update(extra(img))
        out["widgets"][name] = rec
        w.hide()
        w.deleteLater()

    b = QPushButton("按钮")
    b.setFixedHeight(30)  # NButton 契约总高（DESIGN §8.3）
    grab("button", b)
    tb = QPushButton("切换")
    tb.setCheckable(True)
    tb.setChecked(True)
    tb.setFixedHeight(30)
    grab("button_toggled", tb)
    db = QPushButton("禁用")
    db.setEnabled(False)
    db.setFixedHeight(30)
    grab("button_disabled", db)
    cb = QCheckBox()
    cb.setChecked(True)
    grab("check_on", cb)
    grab("check_off", QCheckBox())
    rb = QRadioButton()
    rb.setChecked(True)
    grab("radio_on", rb)
    le = QLineEdit("文本")
    grab("entry", le, extra=lambda img: {"top_edge": edge_hex(img)})
    lef = QLineEdit("焦点")
    grab("entry_focused", lef, extra=lambda img: {"top_edge": edge_hex(img)},
         focus=True)
    sl = QSlider(Qt.Orientation.Horizontal)
    sl.setRange(0, 100)
    sl.setValue(60)
    sl.resize(200, sl.sizeHint().height())
    grab("slider", sl)
    pb = QProgressBar()
    pb.setRange(0, 100)
    pb.setValue(60)
    # Kvantum 1.1.8 无 theme 侧 textless 开关，shell 进度条无文字由应用侧关
    pb.setTextVisible(False)
    pb.resize(200, pb.sizeHint().height())
    grab("progressbar", pb, extra=pb_fill)
    grab("spin", QSpinBox())
    co = QComboBox()
    co.addItems(["选项A", "选项B"])
    grab("combo", co)
    tw = QTabWidget()
    tw.addTab(QLabel("x"), "页签一")
    tw.addTab(QLabel("y"), "页签二")
    tw.resize(220, 90)
    grab("notebook", tw)
    mb = QMenuBar()
    mb.addMenu("文件")
    mb.addMenu("编辑")
    mb.resize(300, mb.sizeHint().height())
    grab("menubar", mb)
    tbr = QToolBar()
    tbr.addAction("动作一")
    tbr.addAction("动作二")
    tbr.resize(300, tbr.sizeHint().height())
    grab("toolbar", tbr)
    grab("label", QLabel("正文文本"))

    # 滚动条：QScrollBar 需要一个实际的范围才有滑块
    from PyQt6.QtWidgets import QScrollBar
    sb = QScrollBar(Qt.Orientation.Horizontal)
    sb.setRange(0, 100)
    sb.setPageStep(30)
    sb.setValue(50)  # 滑块居中：center 采样落在滑块上而非槽里
    sb.resize(160, sb.sizeHint().height())
    grab("scrollbar", sb, aa_face=True)
    # transient_scrollbar 静止时滑块整体淡出(~0.45×)；悬停才是全量 alpha。
    # hover 抓一张用于滑块色阶审计（focused 元素 = GTK slider:hover 一档）。
    sb2 = QScrollBar(Qt.Orientation.Horizontal)
    sb2.setRange(0, 100)
    sb2.setPageStep(30)
    sb2.setValue(50)
    sb2.resize(160, sb2.sizeHint().height())
    grab("scrollbar_hover", sb2, aa_face=True, hover=True)

    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
