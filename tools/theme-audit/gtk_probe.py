#!/usr/bin/env python3
"""GTK3/4 控件级探针：在 Xvfb 里实例化控件、逐个快照，输出 JSON 测量结果。

用法（在 Xvfb 内运行，通常由 theme-audit.py 编排）：
    python3 gtk_probe.py --gtk 4 --theme deepin-dark --out /tmp/probe.json --shots /tmp/shots

环境（编排器已设置）：XDG_CONFIG_HOME 指向含 gtk-3.0/4.0 nosd.css 覆盖的舞台，
XDG_DATA_DIRS 指向含 themes/{deepin,deepin-dark} 的舞台。

测量口径：
- color: 控件快照中心（或指定比例点）像素，hex。
- geom : 控件 alpha 通道几何测量——bounding box、四角圆角半径（取角部首条
  不透明像素行的内缩距离拟合）、厚度类（找最长不透明跑长）。
- var  : gtk_style_context_lookup_color 读取的 @define-color 解析值（GTK4）
  或 style context lookup（GTK3）。
"""
import argparse
import json
import os
import sys

import gi


def rgba_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"


def texture_to_rgba(tex):
    """Gdk.Texture -> (w, h, bytes RGBA)。save_to_png 可靠但绕盘，直接 download。"""
    w, h = tex.get_width(), tex.get_height()
    data = tex.download()  # (bytes, stride)
    raw, stride = data[0], data[1]
    # GdkTexture.download 默认 BGRA8888 premultiplied（MEMORY 格式实现相关），
    # 这里按 downloader 的实际返回做 B/G/R/A 解序：GTK 返回格式以
    # GdkMemoryFormat 为准，稳妥起见用 save_to_bytes(png) + PIL 兜底。
    return w, h, raw, stride


def png_bytes(tex):
    bio = tex.save_to_png_bytes()
    return bytes(bio.get_data())


def measure_alpha(img):
    """PIL RGBA 图 -> 几何测量 dict。"""
    from PIL import Image  # noqa: F401
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
    # 四角圆角：在 y = y0+d（d=1..H/2）处找首个不透明 x；外角内缩量随 d 的
    # 变化满足 x(d) ≈ r - sqrt(r²-d²)。取 d≈r 处读数近似半径。
    def corner(name, top, left):
        edge_y = y0 if top else y1
        edge_x = x0 if left else x1
        max_d = min(20, (y1 - y0) // 2)
        first = None
        for d in range(max_d):
            y = edge_y + (d if top else -d)
            row_xs = [x for x in range(edge_x, edge_x + (1 if left else -1) * min(30, w), 1 if left else -1)
                      if px[x, y] > 8]
            if not row_xs:
                continue
            inner = abs(row_xs[0] - edge_x)
            if first is None:
                first = inner
            if inner <= first * 0.05 or inner == 0:
                # 达到直边：d ≈ 半径
                return max(0, d)
        return None
    m["r_tl"] = corner("tl", True, True)
    m["r_tr"] = corner("tr", True, False)
    m["r_bl"] = corner("bl", False, True)
    m["r_br"] = corner("br", False, False)
    return m

def measure_face(img, tol=14, bg=None):
    """在主色区域掩码上测控件面的几何与圆角（排除文字/边距像素干扰）。

    img 已按窗口底色合成（alpha 叠加面需实底才显色）；bg 为窗口底色，
    计主色时排除它，免得控件四周的底边距压过控件面。
    """
    from collections import Counter
    rgba = img.convert("RGBA").load()
    w, h = img.size
    cnt = Counter()
    for y in range(h):
        for x in range(w):
            p = rgba[x, y]
            if p[3] > 200:
                if bg is not None and abs(p[0]-bg[0]) <= 2 and abs(p[1]-bg[1]) <= 2 and abs(p[2]-bg[2]) <= 2:
                    continue
                cnt[p[:3]] += 1
    if not cnt:
        return {"empty": True}
    r0, g0, b0 = cnt.most_common(1)[0][0]
    def near(p):
        return p[3] > 100 and abs(p[0]-r0) <= tol and abs(p[1]-g0) <= tol and abs(p[2]-b0) <= tol
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if near(rgba[x, y]):
                xs.append(x); ys.append(y)
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
    # 取均值抗噪；GTK 描边外缘 AA 使掩码读数偏低约 2px，+2 估计设计半径
    m["radius_est"] = round(sum(rs) / len(rs)) + 2 if rs else None
    return m



def center_hex(img):
    w, h = img.size
    p = img.convert("RGBA").getpixel((w // 2, h // 2))
    return rgba_hex(p[0], p[1], p[2])


def saturated_hex(img, min_alpha=200, bg=None):
    """最饱和的不透明像素（accent 勾/点/链接字芯总是控件里饱和度最高的区域）。

    bg 为窗口底色：带色底的方案（如 Kanagawa 浅色的米黄底）本身饱和度可能
    压过 accent 文字，须排除底色再取最饱和，否则量到的是背景而非字芯。
    """
    best, best_s = None, -1
    for r, g, b, a in img.convert("RGBA").getdata():
        if a < min_alpha:
            continue
        if bg is not None and abs(r-bg[0]) <= 2 and abs(g-bg[1]) <= 2 and abs(b-bg[2]) <= 2:
            continue
        s = max(r, g, b) - min(r, g, b)
        if s > best_s:
            best_s, best = s, (r, g, b)
    return rgba_hex(*best) if best else None


def far_bg_hex(img, bg, min_alpha=200):
    """距窗口底色最远的不透明像素 = 稀疏文字的字芯纯色。

    AA 边缘是文字与底色的混色、离底色更近，字芯离底色最远。带色底（Kanagawa
    浅色米黄）自身饱和度会压过文字，「最饱和」量到的是背景；链接等稀疏文本
    改用「离底最远」，中性底与带色底都稳健。
    """
    best, bd = None, -1
    for r, g, b, a in img.convert("RGBA").getdata():
        if a < min_alpha:
            continue
        d = (r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2
        if d > bd:
            bd, best = d, (r, g, b)
    return rgba_hex(*best) if best else None


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
    return rgba_hex(*cnt.most_common(1)[0][0])


def edge_hex(img, bg_hex=None, cols=12, rows=40):
    """右上缘主色：滚动条滑块贴控件右缘顶端（value=0 时 thumb 在顶部）。
    半透明滑块先按窗口底色合成再取主色。"""
    from collections import Counter
    from PIL import Image
    img = img.convert("RGBA")
    if bg_hex:
        r, g, b = int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)
        img = Image.alpha_composite(
            Image.new("RGBA", img.size, (r, g, b, 255)), img)
    w, h = img.size
    cnt = Counter()
    for y in range(min(rows, h)):
        for x in range(max(0, w - cols), w):
            p = img.getpixel((x, y))
            if p[3] > 120:
                cnt[p[:3]] += 1
    return rgba_hex(*cnt.most_common(1)[0][0]) if cnt else None


def dominant_hex(img, min_alpha=200, bg=None):
    """控件面主色：不透明像素里出现最多的颜色（跳过文字这类少数派像素）。

    img 已按窗口底色合成；bg 为窗口底色，排除它免得底边距压过控件面。
    """
    from collections import Counter
    cnt = Counter()
    for r, g, b, a in img.convert("RGBA").getdata():
        if a >= min_alpha:
            if bg is not None and abs(r-bg[0]) <= 2 and abs(g-bg[1]) <= 2 and abs(b-bg[2]) <= 2:
                continue
            cnt[(r, g, b)] += 1
    if not cnt:
        return None
    r, g, b = cnt.most_common(1)[0][0]
    return rgba_hex(r, g, b)


def probe_gtk4(theme, outdir):
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Gsk, Graphene, GLib

    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ["GTK_THEME"] = theme
    Gtk.init()
    from PIL import Image
    import io

    win = Gtk.Window()
    win.set_default_size(480, 700)
    grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    grid.set_margin_start(10)
    grid.set_margin_end(10)
    grid.set_margin_top(10)
    win.set_child(grid)

    widgets = {}

    def add(name, w):
        widgets[name] = w
        grid.append(w)

    btn = Gtk.Button(label="按钮")
    add("button", btn)
    tbtn = Gtk.ToggleButton(label="切换")
    tbtn.set_active(True)
    add("button_toggled", tbtn)
    chk = Gtk.CheckButton()
    chk.set_active(True)
    add("check_on", chk)
    chk2 = Gtk.CheckButton()
    add("check_off", chk2)
    # radio 形态：独立成组，不能 set_group(chk)——同组互斥会把 chk
    # 反勾选，check_on 会拍到未勾选态
    rdo_grp = Gtk.CheckButton()
    rdo = Gtk.CheckButton()
    rdo.set_group(rdo_grp)
    rdo.add_css_class("radio")
    rdo.set_active(True)
    add("radio_on", rdo)
    entry = Gtk.Entry()
    entry.set_text("文本")
    add("entry", entry)
    sw = Gtk.Switch()
    add("switch_off", sw)
    sw2 = Gtk.Switch()
    sw2.set_active(True)
    add("switch_on", sw2)
    sld = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    sld.set_value(60)
    sld.set_size_request(200, -1)
    add("slider", sld)
    pb = Gtk.ProgressBar()
    pb.set_fraction(0.6)
    add("progressbar", pb)
    sb = Gtk.SpinButton.new_with_range(0, 100, 1)
    # 取中间值：value=0 时 down 钮禁用、面色落 idle，不代表常规态
    sb.set_value(50)
    add("spin", sb)
    cb = Gtk.ComboBoxText()
    cb.append("a", "选项A")
    cb.append("b", "选项B")
    cb.set_active(0)
    add("combo", cb)
    dd = Gtk.DropDown.new_from_strings(["选项A", "选项B"])
    dd.set_selected(0)
    add("dropdown", dd)
    nb = Gtk.Notebook()
    nb.append_page(Gtk.Label(label="x"), Gtk.Label(label="页签一"))
    nb.append_page(Gtk.Label(label="y"), Gtk.Label(label="页签二"))
    nb.set_size_request(220, 90)
    add("notebook", nb)
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    add("separator", sep)
    # 持久滚动条：overlay 瞬态滑条是 0.4 透明度的指示器，快照里近似不可见；
    # 关掉全局 overlay-scrolling，ALWAYS 给出真实 trough+slider 供测量
    Gtk.Settings.get_default().set_property("gtk-overlay-scrolling", False)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
    sw.set_size_request(160, 120)
    lb = Gtk.ListBox()
    for i in range(40):
        lb.append(Gtk.Label(label=f"列表项 {i}", xalign=0))
    sw.set_child(lb)
    add("scrollbar", sw)
    lab = Gtk.Label(label="正文文本")
    add("label", lab)
    dimb = Gtk.Button(label="禁用")
    dimb.set_sensitive(False)
    add("button_disabled", dimb)
    lb = Gtk.LinkButton(uri="https://example.invalid", label="链接")
    add("link", lb)

    win.present()
    ctx = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + 3_000_000
    while (not win.is_visible() or win.get_width() <= 1) and GLib.get_monotonic_time() < deadline:
        ctx.iteration(True)
    for _ in range(20):
        ctx.iteration(False)

    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(win.get_display())
    os.makedirs(outdir, exist_ok=True)
    out = {"theme": theme, "gtk": 4, "widgets": {}, "vars": {}}

    style = win.get_style_context()
    for name in ("window_bg_color", "window_fg_color", "view_bg_color",
                 "view_fg_color", "accent_bg_color", "accent_fg_color",
                 "popover_bg_color", "headerbar_bg_color", "borders",
                 "theme_selected_bg_color", "theme_selected_fg_color",
                 "insensitive_fg_color", "insensitive_base_color"):
        ok, col = style.lookup_color(name)
        if ok:
            out["vars"][name] = rgba_hex(
                int(col.red * 255), int(col.green * 255), int(col.blue * 255))

    for name, w in widgets.items():
        snap = Gtk.Snapshot()
        paintable = Gtk.WidgetPaintable.new(w)
        paintable.snapshot(snap, w.get_width(), w.get_height())
        node = snap.to_node()
        if node is None:
            out["widgets"][name] = {"error": "no render node"}
            continue
        bounds = node.get_bounds()
        tex = renderer.render_texture(node, bounds)
        png = png_bytes(tex)
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        # alpha 叠加面（strong/field…）须按窗口底色合成才显色：孤立快照的透明底
        # 会把半透明面读成前景纯色。合成后计主色排除底色，免边距压过控件面。
        surf = out["vars"].get("window_bg_color") or "#000000"
        srgb = tuple(int(surf[i:i + 2], 16) for i in (1, 3, 5))
        comp = Image.alpha_composite(Image.new("RGBA", img.size, srgb + (255,)), img)
        comp.save(os.path.join(outdir, f"{name}.png"))
        out["widgets"][name] = {
            "center": center_hex(comp),
            "dominant": dominant_hex(comp, bg=srgb),
            "saturated": saturated_hex(comp, bg=srgb),
            "farbg": far_bg_hex(comp, srgb),
            "strip": strip_hex(comp),
            "edge": edge_hex(img, out["vars"].get("window_bg_color")),
            "alloc": [w.get_width(), w.get_height()],
            "geom": measure_alpha(img),
            "face": measure_face(comp, bg=srgb),
        }
    renderer.unrealize()
    win.destroy()
    return out


def probe_gtk3(theme, outdir):
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
    import cairo
    from PIL import Image
    import io

    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ["GTK_THEME"] = theme
    Gtk.init([])

    win = Gtk.Window()
    win.set_default_size(480, 700)
    grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.add(grid)

    widgets = {}

    def add(name, w):
        widgets[name] = w
        grid.pack_start(w, False, False, 4)

    add("button", Gtk.Button(label="按钮"))
    tbtn = Gtk.ToggleButton(label="切换")
    tbtn.set_active(True)
    add("button_toggled", tbtn)
    chk = Gtk.CheckButton()
    chk.set_active(True)
    add("check_on", chk)
    add("check_off", Gtk.CheckButton())
    rdo = Gtk.RadioButton.new_from_widget(None)
    rdo.set_active(True)
    add("radio_on", rdo)
    add("entry", Gtk.Entry())
    sw = Gtk.Switch()
    add("switch_off", sw)
    sw2 = Gtk.Switch()
    sw2.set_active(True)
    add("switch_on", sw2)
    sld = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    sld.set_value(60)
    sld.set_size_request(200, -1)
    add("slider", sld)
    pb = Gtk.ProgressBar()
    pb.set_fraction(0.6)
    add("progressbar", pb)
    sb = Gtk.SpinButton.new_with_range(0, 100, 1)
    sb.set_value(50)
    add("spin", sb)
    cb = Gtk.ComboBoxText()
    cb.append("a", "选项A")
    cb.set_active(0)
    add("combo", cb)
    nb = Gtk.Notebook()
    nb.append_page(Gtk.Label(label="x"), Gtk.Label(label="页签一"))
    nb.append_page(Gtk.Label(label="y"), Gtk.Label(label="页签二"))
    nb.set_size_request(220, 90)
    add("notebook", nb)
    add("separator", Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
    Gtk.Settings.get_default().set_property("gtk-overlay-scrolling", False)
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
    sw.set_size_request(160, 120)
    lb = Gtk.ListBox()
    for i in range(40):
        lb.add(Gtk.Label(label=f"列表项 {i}", xalign=0))
    sw.add(lb)
    add("scrollbar", sw)
    lb2 = Gtk.LinkButton(uri="https://example.invalid", label="链接")
    add("link", lb2)
    add("label", Gtk.Label(label="正文文本"))
    dimb = Gtk.Button(label="禁用")
    dimb.set_sensitive(False)
    add("button_disabled", dimb)

    win.show_all()
    ctx = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + 3_000_000
    while not win.get_mapped() and GLib.get_monotonic_time() < deadline:
        if not ctx.iteration(False):
            GLib.usleep(10_000)
    for _ in range(30):
        if not ctx.iteration(False):
            GLib.usleep(10_000)

    os.makedirs(outdir, exist_ok=True)
    out = {"theme": theme, "gtk": 3, "widgets": {}, "vars": {}}
    style = win.get_style_context()
    for name in ("window_bg_color", "window_fg_color", "view_bg_color",
                 "theme_bg_color", "theme_fg_color", "theme_base_color",
                 "accent_bg_color", "theme_selected_bg_color", "borders"):
        try:
            ok, col = style.lookup_color(name)
            if ok:
                out["vars"][name] = rgba_hex(
                    int(col.red * 255), int(col.green * 255), int(col.blue * 255))
        except Exception:
            pass

    for name, w in widgets.items():
        w.realize()
        alloc = w.get_allocation()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
        cr = cairo.Context(surf)
        w.draw(cr)
        img = Image.frombuffer("RGBA", (alloc.width, alloc.height),
                               surf.get_data(), "raw", "BGRA", 0, 1)
        # alpha 叠加面按窗口底色合成才显色；cairo 是预乘 alpha，用二次表面合成
        # （而非 PIL alpha_composite，那会再乘一次 alpha）。计主色排除底色。
        bg_hex = out["vars"].get("theme_bg_color") or "#000000"
        brgb = tuple(int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        csurf = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
        ccr = cairo.Context(csurf)
        ccr.set_source_rgb(brgb[0] / 255, brgb[1] / 255, brgb[2] / 255)
        ccr.paint()
        ccr.set_source_surface(surf, 0, 0)
        ccr.paint()
        comp = Image.frombuffer("RGBA", (alloc.width, alloc.height),
                                csurf.get_data(), "raw", "BGRA", 0, 1)
        comp.save(os.path.join(outdir, f"{name}.png"))
        out["widgets"][name] = {
            "center": center_hex(comp),
            "dominant": dominant_hex(comp, bg=brgb),
            "saturated": saturated_hex(comp, bg=brgb),
            "farbg": far_bg_hex(comp, brgb),
            "strip": strip_hex(comp),
            "edge": edge_hex(img, out["vars"].get("theme_bg_color")),
            "alloc": [alloc.width, alloc.height],
            "geom": measure_alpha(img),
            "face": measure_face(comp, bg=brgb),
        }
    win.destroy()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gtk", type=int, choices=(3, 4), required=True)
    ap.add_argument("--theme", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", required=True)
    a = ap.parse_args()
    res = probe_gtk4(a.theme, a.shots) if a.gtk == 4 else probe_gtk3(a.theme, a.shots)
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
