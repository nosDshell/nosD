#!/usr/bin/env python3
"""theme-audit — GTK/Kvantum 主题控件级一致性审计。

monorepo 共享管线：用 tools/nosd-theme（shell 同款渲染器）把仓库模板按
指定配色方案渲染到隔离 XDG 舞台，再分别用 PyGObject（GTK3/4，Xvfb）与
PyQt6（offscreen，qt6ct→Kvantum 完整链）对控件做像素与几何测量，对照
spec/tokens.toml 与跨端一致性契约逐条报告。

迭代回路：改主题文件 → 重跑本脚本 → 看 FAIL 明细，全程秒级。

用法：
    tools/theme-audit/theme-audit.py audit                 # 默认 Kanagawa × dark+light 全量
    tools/theme-audit/theme-audit.py audit --scheme Gruvbox --mode dark
    tools/theme-audit/theme-audit.py stage --scheme Kanagawa --mode dark   # 只渲染舞台
    tools/theme-audit/theme-audit.py probe --scheme Kanagawa --mode dark   # 渲染+测量，不判定
    tools/theme-audit/theme-audit.py showcase --scheme Deepin --mode dark  # 四列对照截图

环境覆盖（Guix 自动探测失败时用）：
    NOSD_THEME_BIN     nosd-theme 二进制路径
    NOSD_PYQT_SITEPKG  PyQt6 site-packages（用 : 分隔可加 sip 包）
    NOSD_KVANTUM_PLUG  kvantum 的 lib/qt*/plugins 目录
    NOSD_GTK_TYPELIB   GI_TYPELIB_PATH 追加项
    NOSD_XVFB_RUN      xvfb-run 路径（gtk 探针需要显示）
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GTK_PKG = ROOT / "packages" / "nosd-gtk-theme"
KV_PKG = ROOT / "packages" / "nosd-kvantum-theme"
SHELL_PKG = ROOT / "packages" / "nosdshell"
SCHEMES = SHELL_PKG / "Assets" / "ColorScheme"
TOKENS = ROOT / "spec" / "tokens.toml"
AUDIT_DIR = Path(__file__).resolve().parent

THEME_FOR_MODE = {"dark": "nosd-dark", "light": "nosd"}

# --- showcase：shell 参照列的几何 -----------------------------------------
# verify.sh 的无头 sway 输出固定 1920x1080；控制中心帧 408 px 贴右缘
# （ControlCenterPanel.qml: panelAnchorRight + preferredWidth=controlCenterWidth
# =408），内部 = 56 px 模块导航 rail + 352 px 内容列（Style.qml:
# settingsRailWidth=56, settingsModuleContentWidth=352）。内容列像素区间
# x∈[1920-352, 1920) = [1568,1920)。SHELL_CROP_Y0 是模块头「显示」行上缘，
# 按实际截图量出；下方取 815 px 盖到「深色模式计划」行底。
VERIFY_DIR = Path("/tmp/nosd-verify-showcase")
SHELL_CROP_X0 = 1920 - 352
# 实测 showcase-Deepin-dark 截图：模块头「显示」行起于 y≈90；y90..905 覆盖
# 显示组（亮度滑块/背光设备/步长/两开关）、夜间模式组、个性化→颜色组
# （深色模式/同步系统主题/自定义强调色/深色模式计划），与 showcase 模仿窗
# 的 A/B/C 三组一一对应。
SHELL_CROP_Y0 = 90
SHELL_CROP_H = 815
SHELL_SHOT = "settings-display.png"
ZOOM_REGION_H = 260          # -x3.png 取每列顶部这段做 3x 最近邻放大

# ---------------------------------------------------------------- 环境探测

def newest(pattern):
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def _semver(path):
    """从 ...-nosd-theme-X.Y.Z/bin/nosd-theme 提取版本元组，供排序。"""
    m = re.search(r"nosd-theme-(\d+(?:\.\d+)*)", str(path))
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def find_nosd_theme():
    for cand in (
        os.environ.get("NOSD_THEME_BIN"),
        str(SHELL_PKG / "tools" / "nosd-theme" / "target" / "release" / "nosd-theme"),
        str(SHELL_PKG / "tools" / "nosd-theme" / "target" / "debug" / "nosd-theme"),
    ):
        if cand and Path(cand).exists():
            return cand
    # store 里有两条版本线：独立的 nosd-theme-*（1.x 旧线）与随 shell 同版本
    # 打包的 nosd-theme-<nosdshell 版本>（功能更全，含 accent 派生族）。按
    # 语义版本取最新，而不是按文件名字典序。
    store_bins = glob.glob("/gnu/store/*-nosd-theme-*/bin/nosd-theme")
    store_bins.sort(key=lambda p: _semver(p), reverse=True)
    for cand in store_bins + [shutil.which("nosd-theme")]:
        if cand and Path(cand).exists():
            return cand
    sys.exit("找不到 nosd-theme 二进制：设 NOSD_THEME_BIN 或先 `just tools`")


def find_pyqt():
    if os.environ.get("NOSD_PYQT_SITEPKG"):
        return os.environ["NOSD_PYQT_SITEPKG"].split(":")
    pkgs = []
    for pat in ("/gnu/store/*-python-pyqt-*/lib/python*/site-packages",
                "/gnu/store/*-python-pyqt6-sip-*/lib/python*/site-packages"):
        for hit in sorted(glob.glob(pat)):
            if hit not in pkgs:
                pkgs.append(hit)
    if not pkgs:
        sys.exit("找不到 PyQt6/pyqt6-sip site-packages（设 NOSD_PYQT_SITEPKG）")
    return pkgs


def find_kvantum_plugins():
    if os.environ.get("NOSD_KVANTUM_PLUG"):
        return os.environ["NOSD_KVANTUM_PLUG"]
    for pat in ("/gnu/store/*-kvantum-*/lib/qt6/plugins",
                "/gnu/store/*-kvantum-*/lib/qt*/plugins"):
        hit = newest(pat)
        if hit and (Path(hit) / "styles" / "libkvantum.so").exists():
            return hit
    sys.exit("找不到 kvantum style 插件（设 NOSD_KVANTUM_PLUG）")


def find_typelib():
    extra = os.environ.get("NOSD_GTK_TYPELIB")
    parts = [p for p in (extra.split(":") if extra else []) if p]
    home = Path.home() / ".guix-home" / "profile" / "lib" / "girepository-1.0"
    if home.is_dir():
        parts.append(str(home))
    gi = newest("/gnu/store/*-gobject-introspection-*/lib/girepository-1.0")
    if gi:
        parts.append(gi)
    return ":".join(dict.fromkeys(parts))


def find_xvfb():
    return os.environ.get("NOSD_XVFB_RUN") or shutil.which("xvfb-run") or sys.exit("需要 xvfb-run")

# ---------------------------------------------------------------- 渲染与舞台

def run(cmd, env=None, capture=False):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([str(c) for c in cmd], env=e,
                          capture_output=capture, text=True)


def render(nosd_theme, scheme_json, mode, work):
    """用 shell 同款渲染器出调色板与全部模板产物，返回 (palette, paths)。"""
    pal_out = work / "palette.json"
    kv = work / "Kvantum" / "KvLibadwaita"
    kv.mkdir(parents=True, exist_ok=True)
    renders = [
        (SHELL_PKG / "Assets" / "Templates" / "kvantum-kvlibadwaita.svg",
         kv / "KvLibadwaita.svg"),
        (SHELL_PKG / "Assets" / "Templates" / "kvantum-kvlibadwaita.kvconfig",
         kv / "KvLibadwaita.kvconfig"),
        (SHELL_PKG / "Assets" / "Templates" / "gtk3.css",
         work / "gtk-3.0" / "nosd.css"),
        (SHELL_PKG / "Assets" / "Templates" / "gtk4.css",
         work / "gtk-4.0" / "nosd.css"),
        (SHELL_PKG / "Assets" / "Templates" / "qtct.conf",
         work / "qt6ct" / "colors" / "nosdshell.conf"),
    ]
    cmd = [nosd_theme, "--scheme", scheme_json, "--mode", mode, "-o", pal_out]
    for src, dst in renders:
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-r", f"{src}:{dst}"]
    r = run(cmd, capture=True)
    if r.returncode != 0:
        sys.exit(f"nosd-theme 渲染失败：{r.stderr or r.stdout}")
    palette = json.loads(Path(pal_out).read_text())[mode]
    return palette


def make_stage(work):
    """装出隔离的 XDG_CONFIG_HOME 与 XDG_DATA_HOME。"""
    cfg = work / "cfg"
    data = work / "data"
    (cfg / "Kvantum").mkdir(parents=True, exist_ok=True)
    (data / "themes").mkdir(parents=True, exist_ok=True)
    # 渲染产物搬进 cfg 布局：Kvantum 主题是 cfg/Kvantum/nosd（不是嵌套 Kvantum/）
    kv_rendered = work / "Kvantum" / "KvLibadwaita"
    if kv_rendered.exists():
        shutil.move(str(kv_rendered), str(cfg / "Kvantum" / "KvLibadwaita"))
        shutil.rmtree(work / "Kvantum", ignore_errors=True)
    for sub in ("gtk-3.0", "gtk-4.0", "qt6ct"):
        src = work / sub
        if src.exists():
            shutil.move(str(src), str(cfg / sub))
    (cfg / "Kvantum" / "kvantum.kvconfig").write_text("[General]\ntheme=KvLibadwaita\n")
    for gtkv in ("gtk-3.0", "gtk-4.0"):
        d = cfg / gtkv
        d.mkdir(exist_ok=True)
        (d / "gtk.css").write_text('@import url("nosd.css");\n')
        (d / "settings.ini").write_text(
            "[Settings]\ngtk-application-prefer-dark-theme=1\n"
            "gtk-font-name=DejaVu Sans 9\n")
    # qt6ct：走与真机同一条链 style=Kvantum + nosdshell.conf 调色板
    qt6 = cfg / "qt6ct"
    qt6.mkdir(exist_ok=True)
    (qt6 / "qt6ct.conf").write_text(
        "[Appearance]\nstyle=Kvantum\ncustom_palette=true\n"
        f"color_scheme_path={qt6 / 'colors' / 'nosdshell.conf'}\n"
        "[Fonts]\n"
        'fixed="DejaVu Sans,9,-1,5,50,0,0,0,0,0"\n'
        'general="DejaVu Sans,9,-1,5,50,0,0,0,0,0"\n')
    for t in ("nosd", "nosd-dark"):
        dst = data / "themes" / t
        if not dst.exists():
            os.symlink(GTK_PKG / t, dst)
    return cfg, data


def probe_env(cfg, data):
    env = {
        "XDG_CONFIG_HOME": str(cfg),
        "XDG_DATA_HOME": str(data),
        # 主题查 themes/ 子树；/usr/share 给 GSettings schema 与图标
        "XDG_DATA_DIRS": f"{data}:/usr/share:{Path.home()}/.guix-home/profile/share:/run/current-system/profile/share",
        "HOME": os.environ["HOME"],
        "LANG": "zh_CN.UTF-8",
    }
    return env

# ---------------------------------------------------------------- 探针

def probe_gtk(gtkv, theme, cfg, data, outdir, env):
    out = outdir / f"gtk{gtkv}.json"
    shots = outdir / f"gtk{gtkv}-shots"
    e = probe_env(cfg, data)
    e.update({
        "GI_TYPELIB_PATH": find_typelib(),
        "GDK_BACKEND": "x11",
        "GTK_THEME": theme,
    })
    r = run([find_xvfb(), "-a", "-s", "-screen 0 1280x900x24",
             sys.executable, AUDIT_DIR / "gtk_probe.py",
             "--gtk", gtkv, "--theme", theme,
             "--out", out, "--shots", shots], env=e, capture=True)
    if r.returncode != 0 or not out.exists():
        sys.exit(f"gtk{gtkv} 探针失败：{r.stderr[-3000:] if r.stderr else r.stdout[-3000:]}")
    if r.stderr and "parsing error" in r.stderr.lower():
        print(f"[warn] gtk{gtkv} CSS 解析告警：{r.stderr[-1000:]}")
    return json.loads(out.read_text())


def probe_qt(cfg, data, outdir, env):
    out = outdir / "qt.json"
    shots = outdir / "qt-shots"
    e = probe_env(cfg, data)
    e.update({
        "PYTHONPATH": ":".join(find_pyqt()),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_PLATFORMTHEME": "qt6ct",
        # offscreen 下 qt6ct 对 style=Kvantum 的解析不可靠（实测回落
        # qt6ct-style）；override 直指同一个 libkvantum.so，调色板仍由
        # qt6ct platformtheme 经 nosdshell.conf 提供，链路不变。
        "QT_STYLE_OVERRIDE": "kvantum",
        "QT_PLUGIN_PATH": f"{find_kvantum_plugins()}:{os.environ.get('QT_PLUGIN_PATH','')}",
    })
    r = run([sys.executable, AUDIT_DIR / "qt_probe.py",
             "--out", out, "--shots", shots], env=e, capture=True)
    if r.returncode != 0 or not out.exists():
        sys.exit(f"qt 探针失败：{r.stderr[-3000:] if r.stderr else r.stdout[-3000:]}")
    for line in (r.stderr or "").splitlines():
        if "qt.svg" in line or "kvantum" in line.lower():
            print(f"[warn] qt: {line}")
    res = json.loads(out.read_text())
    if res.get("style") != "kvantum":
        print(f"[warn] Qt 探针 style={res.get('style')!r}，不是 kvantum：结果不可信")
    return res

# ---------------------------------------------------------------- showcase

def showcase_gtk(gtkv, theme, cfg, data, out_png):
    """在 Xvfb 里跑 showcase_app.py 的 GTK 端，环境与探针完全一致。"""
    e = probe_env(cfg, data)
    e.update({
        "GI_TYPELIB_PATH": find_typelib(),
        "GDK_BACKEND": "x11",
        "GTK_THEME": theme,
    })
    r = run([find_xvfb(), "-a", "-s", "-screen 0 1280x900x24",
             sys.executable, AUDIT_DIR / "showcase_app.py",
             "--toolkit", f"gtk{gtkv}", "--out", out_png],
            env=e, capture=True)
    if r.returncode != 0 or not Path(out_png).exists():
        sys.exit(f"gtk{gtkv} showcase 失败："
                 f"{r.stderr[-3000:] if r.stderr else r.stdout[-3000:]}")
    if r.stderr:
        for line in r.stderr.splitlines():
            low = line.lower()
            if "error" in low or "warn" in low or "[info]" in low:
                print(f"[gtk{gtkv}] {line}")


def showcase_qt(cfg, data, out_png):
    """offscreen 跑 showcase_app.py 的 Qt 端，环境同 probe_qt。"""
    e = probe_env(cfg, data)
    e.update({
        "PYTHONPATH": ":".join(find_pyqt()),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_PLATFORMTHEME": "qt6ct",
        "QT_STYLE_OVERRIDE": "kvantum",
        "QT_PLUGIN_PATH": f"{find_kvantum_plugins()}:{os.environ.get('QT_PLUGIN_PATH','')}",
    })
    r = run([sys.executable, AUDIT_DIR / "showcase_app.py",
             "--toolkit", "qt", "--out", out_png], env=e, capture=True)
    if r.returncode != 0 or not Path(out_png).exists():
        sys.exit(f"qt showcase 失败："
                 f"{r.stderr[-3000:] if r.stderr else r.stdout[-3000:]}")
    style = None
    for line in (r.stderr or "").splitlines():
        if line.startswith("style="):
            style = line.split("=", 1)[1].strip()
        elif "qt.svg" in line or "kvantum" in line.lower() or "[info]" in line.lower():
            print(f"[qt] {line}")
    if style != "kvantum":
        print(f"[warn] Qt showcase style={style!r}，不是 kvantum：截图不可信")


def shell_ref(scheme, mode, work, refresh=False):
    """verify.sh settings-display 截图 → 裁出 352px 内容列（PIL Image）。

    截图缓存在 VERIFY_DIR，--refresh-shell 强制重跑。verify.sh 是唯一允许
    跑 shell 的方式（nosdshell/AGENTS.md「运行与校验」）；--settings 的
    JSON 深合到内置 seed 上（verify.sh 头部 merge 逻辑）。
    """
    name = f"showcase-{scheme}-{mode}"
    shot = VERIFY_DIR / "shots" / name / SHELL_SHOT
    if refresh or not shot.exists():
        # 复用旧 home/config 会让设置页带着陈旧状态启动（行内容整列空渲染）；
        # 刷新时重置可重建缓存，verify.sh 会重新种子化。
        for sub in ("home", "state", "config"):
            shutil.rmtree(VERIFY_DIR / sub, ignore_errors=True)
        seed = Path(work) / f"seed-{scheme}-{mode}.json"
        seed.write_text(json.dumps({
            "colorSchemes": {
                "predefinedScheme": scheme,
                "darkMode": mode == "dark",
            }}, ensure_ascii=False))
        r = run([SHELL_PKG / "Scripts" / "test" / "verify.sh", name,
                 "--settings", seed, "--scenes", "settings-display"],
                env={"NOSD_VERIFY_DIR": str(VERIFY_DIR),
                     # 不生成 foot 陪跑窗口：它们会透过半透明面板渗色
                     "NOSD_SPAWN_WINDOWS": "0",
                     # 本机需要 fork 构建的 qs（上游 0.3.2 无 Services.Keyring）
                     "QS_PKG": os.environ.get("QS_PKG", "quickshell-nosd")},
                capture=True)
        if not shot.exists():
            tail = ((r.stderr or "") + (r.stdout or ""))[-3000:]
            sys.exit(f"verify.sh 未产出 {shot}：\n{tail}")
    from PIL import Image
    img = Image.open(shot).convert("RGB")
    return img.crop((SHELL_CROP_X0, SHELL_CROP_Y0,
                     SHELL_CROP_X0 + 352, SHELL_CROP_Y0 + SHELL_CROP_H))


def compose(columns, out_png):
    """columns = [(label, PIL Image)] → 带标签四列拼图，1:1 像素。"""
    from PIL import Image, ImageDraw
    label_h, gap = 22, 6
    w = sum(i.width for _, i in columns) + gap * (len(columns) - 1)
    h = label_h + max(i.height for _, i in columns)
    canvas = Image.new("RGB", (w, h), "#202020")
    d = ImageDraw.Draw(canvas)
    x = 0
    for label, img in columns:
        tw = d.textlength(label)
        d.text((x + (img.width - tw) / 2, 6), label, fill="#e0e0e0")
        canvas.paste(img.convert("RGB"), (x, label_h))
        x += img.width + gap
    canvas.save(out_png)


ZOOM_BOTTOM_H = 160           # -x3-loose.png 取每列底部这段（散落控件带）


def zoom3(columns, out_png, bottom=False):
    """同构图：每列裁同一高度带（顶部或底部），3x 最近邻放大后再拼。"""
    from PIL import Image
    h = ZOOM_BOTTOM_H if bottom else ZOOM_REGION_H
    scaled = []
    for label, img in columns:
        ch = min(h, img.height)
        box = ((0, img.height - ch, img.width, img.height) if bottom
               else (0, 0, img.width, ch))
        scaled.append((label, img.crop(box)
                       .resize((img.width * 3, ch * 3),
                               Image.Resampling.NEAREST)))
    compose(scaled, out_png)


def groove_step_checks(columns, palette, tokens):
    """滑槽未填充段须比所在卡面亮一档 strong（DESIGN §8.3）。

    叠加梯若烘焙成实色，滑槽(strong)与设置行卡(strong)同值→隐形。
    这里在 showcase 真实卡面上量相对梯阶：期望 groove = card + α strong×(fg−card)。
    """
    alpha = tokens["overlay"]["strong"]
    fg = hex2rgb(palette["on_surface"])
    prim = hex2rgb(palette["primary"])

    def near(p, c, tol=24):
        return all(abs(a - b) <= tol for a, b in zip(p, c))

    res = []
    for name, im in columns:
        if name == "shell":
            continue
        im = im.convert("RGBA")
        px = im.load()
        w, h = im.size
        found = None
        for y in range(10, min(h, 160)):
            runs = []
            prev = None
            start = 0
            for x in range(w):
                p = px[x, y][:3]
                if p != prev:
                    if prev is not None:
                        runs.append((start, x - 1, prev))
                    prev = p
                    start = x
            runs.append((start, w - 1, prev))
            fill = [r for r in runs if near(r[2], prim) and r[1] - r[0] > 20]
            if not fill:
                continue
            f = fill[0]
            # 旋钮在填充段右端。浅色窗口边距(#f8f8f8)也近白，须先限定「填充右侧」
            # 再取首个白跑长，否则左边距会被误认成旋钮（深色边距#181818 不近白，
            # 故旧逻辑只在浅色翻车）。
            knob = [r for r in runs
                    if near(r[2], (255, 255, 255), 12) and 3 <= r[1] - r[0] <= 22
                    and r[0] > f[1]]
            if knob:
                k = knob[0]
                left = [r for r in runs if r[1] < f[0] and r[1] - r[0] > 4]
                right = [r for r in runs if r[0] > k[1] and r[1] - r[0] > 4]
                if left and right:
                    found = (left[-1][2], right[0][2])
                    break
        if not found:
            res.append(("WARN", f"{name} 滑槽梯阶", "未定位滑块行"))
            continue
        card, groove = found
        exp = tuple(round(c + alpha * (f - c)) for c, f in zip(card, fg))
        ok = all(abs(a - b) <= 3 for a, b in zip(groove, exp))
        res.append(("OK" if ok else "FAIL", f"{name} 滑槽比卡面亮一档 strong",
                    f"卡面{card} 滑槽{groove} 期望{exp}"))
    return res


def do_showcase(a, mode, work, outdir):
    """渲染+舞台 → gtk4/gtk3/qt 各一张设置模仿窗 → 与 shell 参照拼四列。"""
    from PIL import Image  # noqa: F401
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sp = scheme_path(a.scheme)
    theme = THEME_FOR_MODE[mode]
    palette = render(find_nosd_theme(), sp, mode, work)
    cfg, data = make_stage(work)

    shots = Path(outdir)
    shots.mkdir(parents=True, exist_ok=True)
    stem = shots / f"col-{a.scheme}-{mode}"

    columns = []
    if not a.no_shell:
        columns.append(("shell", shell_ref(a.scheme, mode, work,
                                           refresh=a.refresh_shell)))
    else:
        print("[info] --no-shell：跳过 shell 参照列")
    showcase_gtk(4, theme, cfg, data, Path(f"{stem}-gtk4.png"))
    columns.append(("gtk4", Image.open(f"{stem}-gtk4.png")))
    showcase_gtk(3, theme, cfg, data, Path(f"{stem}-gtk3.png"))
    columns.append(("gtk3", Image.open(f"{stem}-gtk3.png")))
    showcase_qt(cfg, data, Path(f"{stem}-qt.png"))
    columns.append(("qt", Image.open(f"{stem}-qt.png")))

    tokens = tomllib.loads(TOKENS.read_text())
    gres = groove_step_checks(columns, palette, tokens)
    for lv, name, detail in gres:
        print(f"  [{lv}] {name}：{detail}")
    if any(lv == "FAIL" for lv, _, _ in gres):
        sys.exit("showcase 滑槽梯阶断言失败")

    out = shots / f"showcase-{a.scheme}-{mode}.png"
    compose(columns, out)
    zoom3(columns, shots / f"showcase-{a.scheme}-{mode}-x3.png")
    zoom3(columns, shots / f"showcase-{a.scheme}-{mode}-x3-loose.png",
          bottom=True)
    print(f"showcase: {out}")

# ---------------------------------------------------------------- 契约判定

def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def close(c1, c2, tol=10):
    if not c1 or not c2:
        return False
    a, b = hex2rgb(c1), hex2rgb(c2)
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def shade(c, f):
    r, g, b = hex2rgb(c)
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"


def evaluate(mode, palette, tokens, g4, g3, qt):
    """返回 [(level, 名称, 说明)]。level: FAIL/WARN/OK。"""
    res = []

    def check(name, ok, detail=""):
        res.append(("OK" if ok else "FAIL", name, detail))

    P = palette
    surf, onsurf = P["surface"], P["on_surface"]
    scont = P["surface_container"]
    sclowest = P["surface_container_lowest"]
    primary, onprimary = P["primary"], P["on_primary"]
    outline_v = P["outline_variant"]

    v4 = g4.get("vars", {})
    v3 = g3.get("vars", {})
    w4 = g4.get("widgets", {})
    w3 = g3.get("widgets", {})
    wq = qt.get("widgets", {})
    qpal = qt.get("palette", {})

    # 1) GTK 变量契约：nosd.css 覆盖应解析为方案色
    check("gtk4 window_bg == surface", close(v4.get("window_bg_color"), surf, 2),
          f"{v4.get('window_bg_color')} vs {surf}")
    check("gtk4 accent == primary", close(v4.get("accent_bg_color"), primary, 2),
          f"{v4.get('accent_bg_color')} vs {primary}")
    check("gtk4 popover == surface_container",
          close(v4.get("popover_bg_color"), scont, 2),
          f"{v4.get('popover_bg_color')} vs {scont}")
    if v3:
        # GTK3 变量名沿用 theme_* 命名；accent 取选中背景（GTK3 无 accent_bg_color）
        check("gtk3 theme_bg == surface", close(v3.get("theme_bg_color"), surf, 2),
              f"{v3.get('theme_bg_color')} vs {surf}")
        check("gtk3 accent == primary",
              close(v3.get("theme_selected_bg_color") or v3.get("accent_bg_color"),
                    primary, 2),
              f"{v3.get('theme_selected_bg_color')} vs {primary}")
    else:
        res.append(("WARN", "gtk3 探针", "无数据（--skip-gtk3 或探针失败）"))

    # 叠加梯档（nosd-theme 派生键；旧渲染器缺席时跳过，不阻断）
    ovl_field = P.get("overlay_field")
    ovl_strong = P.get("overlay_strong")
    ovl_ind = P.get("overlay_indicator")

    # 2) Qt palette 契约（[GeneralColors] 全套角色）
    check("qt palette window == surface", close(qpal.get("window"), surf, 2),
          f"{qpal.get('window')} vs {surf}")
    if ovl_field:
        # qt Base 角色 = 可编辑面 = overlay_field（DESIGN §1.3 梯档烘焙）
        check("qt palette base == overlay_field",
              close(qpal.get("base"), ovl_field, 2),
              f"{qpal.get('base')} vs {ovl_field}")
    else:
        check("qt palette base == surface",
              close(qpal.get("base"), surf, 2),
              f"{qpal.get('base')} vs {surf}")
    check("qt palette highlight == primary", close(qpal.get("highlight"), primary, 2),
          f"{qpal.get('highlight')} vs {primary}")
    check("qt palette text == on_surface", close(qpal.get("text"), onsurf, 2),
          f"{qpal.get('text')} vs {onsurf}")
    check("qt palette tooltip == surface_container",
          close(qpal.get("tool_tip_base"), scont, 2),
          f"{qpal.get('tool_tip_base')} vs {scont}")
    check("qt palette link == primary", close(qpal.get("link"), primary, 2),
          f"{qpal.get('link')} vs {primary}")

    # 3a) 契约面：控件面应等于叠加梯烘焙色（按钮=strong、字段=field）。
    #     dominant 是控件面主色；描边/文字是少数像素。
    if ovl_strong:
        for src, label in ((w4, "gtk4"), (wq, "qt"), (w3, "gtk3")):
            d = src.get("button", {}).get("dominant")
            if d:
                check(f"{label} 按钮面 ≈ overlay_strong", close(d, ovl_strong, 14),
                      f"{d} vs {ovl_strong}")
        # GTK3 下拉是按钮形态（切换钮+箭头），面色取 strong 而非 field
        d = w3.get("combo", {}).get("dominant")
        if d:
            check("gtk3 combo面 ≈ overlay_strong", close(d, ovl_strong, 14),
                  f"{d} vs {ovl_strong}")
    if ovl_field:
        for wname in ("entry", "combo"):
            for src, label in ((w4, "gtk4"), (wq, "qt")):
                d = src.get(wname, {}).get("dominant")
                if d:
                    check(f"{label} {wname}面 ≈ overlay_field",
                          close(d, ovl_field, 14), f"{d} vs {ovl_field}")
        for wname in ("entry", "spin"):
            d = w3.get(wname, {}).get("dominant")
            if d:
                check(f"gtk3 {wname}面 ≈ overlay_field",
                      close(d, ovl_field, 14), f"{d} vs {ovl_field}")
    if ovl_ind:
        # 滚动条滑块 = indicator 梯档：GTK 半透明滑块按窗口色合成取 edge，
        # Qt 实心滑块取 dominant。GTK 允许更大容差（内容色参与合成）。
        for src, label, key, tol in ((w4, "gtk4", "edge", 20),
                                   (w3, "gtk3", "edge", 20),
                                   (wq, "qt", "dominant", 14)):
            d = src.get("scrollbar", {}).get(key)
            if d:
                check(f"{label} 滚动条滑块 ≈ overlay_indicator",
                      close(d, ovl_ind, tol), f"{d} vs {ovl_ind}")

    # 3b) 跨端「同一张脸」：关键控件像素对齐。
    pairs = [
        ("按钮面", w4.get("button", {}).get("dominant"),
         wq.get("button", {}).get("dominant"), 14),
        ("输入框面", w4.get("entry", {}).get("dominant"),
         wq.get("entry", {}).get("dominant"), 14),
        ("下拉框面", w4.get("combo", {}).get("dominant"),
         wq.get("combo", {}).get("dominant"), 14),
        ("开关 accent", w4.get("switch_on", {}).get("center"),
         wq.get("button_toggled", {}).get("dominant"), 24),
        # 进度条：填充色 = accent，是控件内最饱和区域（避开文字像素；
        # GTK 槽在控件中部、Qt 在底部，位置无关采样）
        ("进度填充", w4.get("progressbar", {}).get("strip"),
         wq.get("progressbar", {}).get("strip"), 24),
    ]
    for name, g, q, tol in pairs:
        if g and q:
            check(f"gtk4↔qt {name}", close(g, q, tol),
                  f"gtk {g} vs qt {q}")
        else:
            res.append(("WARN", f"gtk4↔qt {name}", f"缺数据 gtk={g} qt={q}"))

    # GTK3↔GTK4 同脸：同语义控件跨代面色一致（combo 除外——GTK3 是按钮形态）
    if w3:
        for name in ("button", "entry", "spin"):
            g, q = (w3.get(name, {}).get("dominant"),
                    w4.get(name, {}).get("dominant"))
            if g and q:
                check(f"gtk3↔gtk4 {name}面", close(g, q, 14),
                      f"gtk3 {g} vs gtk4 {q}")

    # 4) accent 跟随：勾选/切换须命中 primary 家族。
    # 勾选标记是细笔画，用 saturated（最饱和像素）抓 accent。
    for src, key, label in ((w4, "switch_on", "gtk4"),
                            (w4, "check_on", "gtk4"),
                            (w4, "radio_on", "gtk4"),
                            (w3, "switch_on", "gtk3"),
                            (w3, "check_on", "gtk3"),
                            (w3, "radio_on", "gtk3"),
                            (wq, "check_on", "qt"),
                            (wq, "radio_on", "qt")):
        rec = src.get(key, {})
        sat = rec.get("saturated") or rec.get("center")
        if sat:
            check(f"{label} {key} accent", close(sat, primary, 45),
                  f"{sat} vs {primary}")
    qtb = wq.get("button_toggled", {})
    if qtb.get("dominant"):
        # center 会落在按钮文字 AA 像素上，面色用 dominant
        check("qt button_toggled accent", close(qtb["dominant"], primary, 30),
              f"{qtb['dominant']} vs {primary}")
    pb3 = w3.get("progressbar", {})
    if pb3.get("dominant"):
        check("gtk3 进度填充 accent", close(pb3["dominant"], primary, 45),
              f"{pb3['dominant']} vs {primary}")
    tb3 = w3.get("button_toggled", {})
    if tb3.get("saturated"):
        # GTK3 toggled 面是压暗色，accent 在描边/指示像素
        check("gtk3 button_toggled accent", close(tb3["saturated"], primary, 45),
              f"{tb3['saturated']} vs {primary}")

    # 链接 = accent_standalone 族（背景上可读的 accent 文本色）。链接是稀疏
    # accent 文字：取「离窗口底色最远」的字芯像素（farbg）。合成后 AA 边缘数量
    # 压过字芯使 dominant 落到混色；带色底（Kanagawa 浅色）饱和度又压过文字使
    # saturated 量到背景——farbg 对中性底与带色底都稳健。
    for src, label in ((w4, "gtk4"), (w3, "gtk3")):
        d = src.get("link", {}).get("farbg")
        if d and P.get("accent_standalone_color"):
            check(f"{label} link ≈ accent_standalone",
                  close(d, P["accent_standalone_color"], 14),
                  f"{d} vs {P['accent_standalone_color']}")

    # 容器面：menubar/toolbar/notebook 贴窗口 surface（Qt 端采样）
    for wname in ("menubar", "toolbar", "notebook"):
        d = wq.get(wname, {}).get("dominant")
        if d:
            check(f"qt {wname} ≈ surface", close(d, surf, 4),
                  f"{d} vs {surf}")

    # 5) 几何令牌：按钮=radiusPill 胶囊、文本字段=radiusItem（DESIGN.md / Style.qml）
    rt = tokens["radius"]
    exp_radius = {"button": tokens["controls"]["button_radius"],
                  "entry": rt["radiusItem"],
                  "combo": rt["radiusItem"], "spin": rt["radiusItem"]}

    def radius_est(rec):
        g = rec.get("face") or rec.get("geom", {})
        return g.get("radius_est")

    for src, name in ((w4, "button"), (w3, "button"),
                      (w4, "entry"), (w3, "entry")):
        rec = src.get(name, {})
        est = radius_est(rec)
        want = exp_radius[name]
        if est is None:
            res.append(("WARN", f"{name} 圆角", "无可测角"))
            continue
        check(f"{name} 圆角 ≈ {want}", abs(est - want) <= 2,
              f"估计 {est}（实测角 {(rec.get('face') or rec.get('geom', {})).get('r_tl')},"
              f"{(rec.get('face') or rec.get('geom', {})).get('r_tr')},"
              f"{(rec.get('face') or rec.get('geom', {})).get('r_bl')},"
              f"{(rec.get('face') or rec.get('geom', {})).get('r_br')}）")

    # 6) 渲染高度：字段总高 / 开关 / 进度条厚度（DESIGN §8.3）。
    #    静态语义在 check-tokens；这里量渲染结果，防 padding/边框/SVG 元素撑高。
    ct = tokens["controls"]

    def size_of(rec):
        return rec.get("alloc") or rec.get("size")

    for src, label in ((w4, "gtk4"), (w3, "gtk3")):
        for wname in ("entry", "spin", "combo"):
            sz = size_of(src.get(wname, {}))
            if sz:
                check(f"{label} {wname} 渲染高 == {ct['field_height']}",
                      abs(sz[1] - ct["field_height"]) <= 1, f"{sz[1]}")
    sz = size_of(w4.get("dropdown", {}))
    if sz:
        check(f"gtk4 dropdown 渲染高 == {ct['field_height']}",
              abs(sz[1] - ct["field_height"]) <= 1, f"{sz[1]}")
    for src, label in ((w4, "gtk4"), (w3, "gtk3")):
        sz = size_of(src.get("switch_on", {}))
        if sz:
            check(f"{label} switch 高 == {ct['switch_height']}",
                  abs(sz[1] - ct["switch_height"]) <= 1, f"{sz[1]}")
        sz = size_of(src.get("progressbar", {}))
        if sz:
            check(f"{label} 进度条厚 == {ct['slider_groove']}",
                  abs(sz[1] - ct["slider_groove"]) <= 1, f"{sz[1]}")
    for src, label in ((w4, "gtk4"), (w3, "gtk3")):
        sz = size_of(src.get("button", {}))
        if sz:
            check(f"{label} button 渲染高 == {ct['button_height']}",
                  abs(sz[1] - ct["button_height"]) <= 1, f"{sz[1]}")
    return res


def report(mode, scheme, results):
    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print(f"\n== {scheme} / {mode} ==")
    for lv, name, detail in results:
        if lv != "OK":
            print(f"  {lv:4} {name:42} {detail}")
    print(f"  -> {len(results) - len(fails) - len(warns)} OK / "
          f"{len(warns)} WARN / {len(fails)} FAIL")
    return len(fails)

# ---------------------------------------------------------------- 主流程

def scheme_path(name):
    p = Path(name)
    if p.exists():
        return p
    cand = SCHEMES / name / f"{name}.json"
    if cand.exists():
        return cand
    hits = list(SCHEMES.glob(f"*/{name}.json")) + list(SCHEMES.glob(f"{name}/*.json"))
    if hits:
        return hits[0]
    sys.exit(f"配色方案不存在：{name}（在 {SCHEMES} 下找）")


def do_one(args, mode, work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    sp = scheme_path(args.scheme)
    theme = THEME_FOR_MODE[mode]
    bin = find_nosd_theme()
    palette = render(bin, sp, mode, work)
    cfg, data = make_stage(work)
    env = os.environ.copy()
    g4 = probe_gtk(4, theme, cfg, data, work, env)
    g3 = probe_gtk(3, theme, cfg, data, work, env) if not args.skip_gtk3 else {"widgets": {}, "vars": {}}
    qt = probe_qt(cfg, data, work, env)
    return palette, g4, g3, qt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("audit", "stage", "probe", "showcase"))
    ap.add_argument("--scheme", default="Kanagawa")
    ap.add_argument("--mode", default="both", choices=("dark", "light", "both"))
    ap.add_argument("--work", default=None, help="舞台目录（默认 mktemp）")
    ap.add_argument("--skip-gtk3", action="store_true")
    ap.add_argument("--keep", action="store_true", help="不清理舞台")
    ap.add_argument("--out", default="/tmp/nosd-showcase",
                    help="showcase 出图目录")
    ap.add_argument("--no-shell", action="store_true",
                    help="showcase：跳过 shell 参照列")
    ap.add_argument("--refresh-shell", action="store_true",
                    help="showcase：无视缓存重跑 verify.sh")
    a = ap.parse_args()

    modes = ["dark", "light"] if a.mode == "both" else [a.mode]
    total_fail = 0
    tokens = tomllib.loads(TOKENS.read_text())

    for mode in modes:
        if a.work:
            work = Path(a.work) / f"{a.scheme}-{mode}"
            shutil.rmtree(work, ignore_errors=True)
            cleanup = False
        else:
            work = Path(tempfile.mkdtemp(prefix=f"audit-{a.scheme}-{mode}-"))
            cleanup = not a.keep
        try:
            if a.cmd == "showcase":
                do_showcase(a, mode, work, a.out)
            elif a.cmd == "probe":
                palette, g4, g3, qt = do_one(a, mode, work)
                print(json.dumps({"palette": palette, "gtk4": g4,
                                  "gtk3": g3, "qt": qt},
                                 ensure_ascii=False, indent=1))
            elif a.cmd == "audit":
                palette, g4, g3, qt = do_one(a, mode, work)
                results = evaluate(mode, palette, tokens, g4, g3, qt)
                total_fail += report(mode, a.scheme, results)
            else:
                palette, g4, g3, qt = do_one(a, mode, work)
                print(f"舞台就绪：{work}")
        finally:
            if cleanup:
                shutil.rmtree(work, ignore_errors=True)

    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
