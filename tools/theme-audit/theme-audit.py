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

THEME_FOR_MODE = {"dark": "deepin-dark", "light": "deepin"}

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
    kv = work / "Kvantum" / "nosd"
    kv.mkdir(parents=True, exist_ok=True)
    renders = [
        (KV_PKG / "nosd" / "nosd.svg.tpl", kv / "nosd.svg"),
        (KV_PKG / "nosd" / "nosd.kvconfig.tpl", kv / "nosd.kvconfig"),
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
    kv_rendered = work / "Kvantum" / "nosd"
    if kv_rendered.exists():
        shutil.move(str(kv_rendered), str(cfg / "Kvantum" / "nosd"))
        shutil.rmtree(work / "Kvantum", ignore_errors=True)
    for sub in ("gtk-3.0", "gtk-4.0", "qt6ct"):
        src = work / sub
        if src.exists():
            shutil.move(str(src), str(cfg / sub))
    (cfg / "Kvantum" / "kvantum.kvconfig").write_text("[General]\ntheme=nosd\n")
    for gtkv in ("gtk-3.0", "gtk-4.0"):
        d = cfg / gtkv
        d.mkdir(exist_ok=True)
        (d / "gtk.css").write_text('@import url("nosd.css");\n')
        (d / "settings.ini").write_text(
            "[Settings]\ngtk-application-prefer-dark-theme=1\n")
    # qt6ct：走与真机同一条链 style=Kvantum + nosdshell.conf 调色板
    qt6 = cfg / "qt6ct"
    qt6.mkdir(exist_ok=True)
    (qt6 / "qt6ct.conf").write_text(
        "[Appearance]\nstyle=Kvantum\ncustom_palette=true\n"
        f"color_scheme_path={qt6 / 'colors' / 'nosdshell.conf'}\n")
    for t in ("deepin", "deepin-dark"):
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
    # 1) GTK 变量契约：nosd.css 覆盖应解析为方案色
    check("gtk4 window_bg == surface", close(v4.get("window_bg_color"), surf, 2),
          f"{v4.get('window_bg_color')} vs {surf}")
    check("gtk4 accent == primary", close(v4.get("accent_bg_color"), primary, 2),
          f"{v4.get('accent_bg_color')} vs {primary}")
    check("gtk4 popover == surface_container",
          close(v4.get("popover_bg_color"), scont, 2),
          f"{v4.get('popover_bg_color')} vs {scont}")

    w4 = g4.get("widgets", {})
    wq = qt.get("widgets", {})
    qpal = qt.get("palette", {})

    # 2) Qt palette 契约（[GeneralColors] 全套角色）
    check("qt palette window == surface", close(qpal.get("window"), surf, 2),
          f"{qpal.get('window')} vs {surf}")
    # qt Base 角色 = GTK view_bg = surface（视图与窗口同色）；
    # 控件面由 SVG interior 画，不是 Base。
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

    # 3) 跨端「同一张脸」：关键控件像素对齐。
    # dominant = 控件面主色；GTK 暗色控件按 deepin 语义 shade(base,~0.85)，
    # Qt 侧单 SVG 无法按模式分支，统一取 surface——暗态差 ~6 是已知取舍，
    # 亮态完全一致。
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

    # 4) accent 跟随：勾选/切换须命中 primary 家族。
    # 勾选标记是细笔画，用 saturated（最饱和像素）抓 accent。
    for src, key, label in ((w4, "switch_on", "gtk4"),
                            (w4, "check_on", "gtk4"),
                            (w4, "radio_on", "gtk4"),
                            (wq, "check_on", "qt"),
                            (wq, "radio_on", "qt")):
        rec = src.get(key, {})
        sat = rec.get("saturated") or rec.get("center")
        if sat:
            check(f"{label} {key} accent", close(sat, primary, 45),
                  f"{sat} vs {primary}")
    qtb = wq.get("button_toggled", {})
    if qtb.get("center"):
        check("qt button_toggled accent", close(qtb["center"], primary, 30),
              f"{qtb['center']} vs {primary}")

    # 5) 几何令牌：按钮=radiusRow、文本字段=radiusItem（DESIGN.md / Style.qml）
    rt = tokens["radius"]
    exp_radius = {"button": rt["radiusRow"], "entry": rt["radiusItem"],
                  "combo": rt["radiusItem"], "spin": rt["radiusItem"]}

    def radius_est(rec):
        g = rec.get("face") or rec.get("geom", {})
        return g.get("radius_est")

    for src, name in ((w4, "button"), (wq, "button"), (w4, "entry"),
                      (wq, "entry"), (wq, "combo"), (wq, "spin")):
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
    ap.add_argument("cmd", choices=("audit", "stage", "probe"))
    ap.add_argument("--scheme", default="Kanagawa")
    ap.add_argument("--mode", default="both", choices=("dark", "light", "both"))
    ap.add_argument("--work", default=None, help="舞台目录（默认 mktemp）")
    ap.add_argument("--skip-gtk3", action="store_true")
    ap.add_argument("--keep", action="store_true", help="不清理舞台")
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
            palette, g4, g3, qt = do_one(a, mode, work)
            if a.cmd == "probe":
                print(json.dumps({"palette": palette, "gtk4": g4,
                                  "gtk3": g3, "qt": qt},
                                 ensure_ascii=False, indent=1))
            elif a.cmd == "audit":
                results = evaluate(mode, palette, tokens, g4, g3, qt)
                total_fail += report(mode, a.scheme, results)
            else:
                print(f"舞台就绪：{work}")
        finally:
            if cleanup:
                shutil.rmtree(work, ignore_errors=True)

    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
