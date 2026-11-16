#!/usr/bin/env python3
"""spec/tokens.toml 三方一致性检查（nosd monorepo）

检查项：
1. GTK CSS 的圆角字面值必须落在 tokens.toml [radius] 集合内。
   覆盖简写 border-radius 与四条长写 border-*-radius；长写按属性逐条判定，
   不会被简写正则重复计数。
2. GTK CSS 的 -gtk-outline-radius 是派生值：形状半径 + 焦点环外扩。
   外扩量取同规则内的 outline-offset，缺省用 [radius.outline].offset。
   百分比（正圆/胶囊）不参与判定。
3. cinnamon/ 与 gnome-shell/ 下不得出现 @xxx 颜色引用或 @define-color（St 不认，
   只有 at-rule 合法）。
4. GTK CSS 中非 defaults 块不得出现硬编码 hex 颜色（defaults 块由
   '@define-color' 行界定：检查仅对不在 '@define-color' 声明里的颜色值生效）。
4. Kvantum 模板 kvconfig 必须给出 [GeneralColors] 全套角色。Kvantum 的
   polish(QPalette&) 只做 standardPalette()，而 standardPalette() 只填这里
   出现过的角色；漏掉的角色回落到 QPalette 默认构造值（Window = #efefef），
   表现为菜单栏/工具栏/页签栏露出的浅灰带。形状与几何由上游 KvLibadwaita
   决定，本检查只管颜色契约。
5. packages/nosdshell/Commons/Style.qml 的 radius 家族与 tokens.toml 一致。
6. Kvantum SVG 与模板 XML 良构性。Qt SVG 是严格 XML 解析：一个重复属性
   （如 ry="2" 出现两次）就让 QSvgRenderer.isValid() 为 false，Kvantum
   整图拒载、全部元素回落内置默认主题——单个字符级手误即可让主题整体
   失效，必须进检查。
7. 分发静态素材与 shell 渲染输入同源：nosd-kvantum-theme 下的 KvLibadwaita
   两套是上游原样（light 目录内放 Dark 变体），shell 模板由同一上游草稿
   生成；两者形状来源不同属预期，不做逐字节比对。

用法: tools/check-tokens.py [--stats] [nosd 仓根目录]
退出码: 0 = 全部通过；1 = 存在漂移（打印每一项）。
"""
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

GTK_PKG = Path("packages") / "nosd-gtk-theme"
KVANTUM_PKG = Path("packages") / "nosd-kvantum-theme"
SHELL_PKG = Path("packages") / "nosdshell"
THEME_DIRS = ("nosd", "nosd-dark")
GTK_SUBDIRS = ("gtk-3.0", "gtk-4.0")

# Kvantum 的 QPalette 角色全集。standardPalette() 只填充在 [GeneralColors]
# 里出现过的角色，其余保持 QPalette 默认构造值，所以必须全部给出。
GENERAL_COLOR_ROLES = {
    "window",
    "window.text",
    "base",
    "alt.base",
    "button",
    "button.text",
    "text",
    "light",
    "mid.light",
    "dark",
    "mid",
    "highlight",
    "highlight.text",
    "disabled.text",
    "shadow",
    "link",
    "link.visited",
    "tooltip.base",
    "tooltip.text",
    "progress.indicator.text",
    "progress.inactive.indicator.text",
    "inactive.window",
    "inactive.window.text",
    "inactive.base",
    "inactive.alt.base",
    "inactive.text",
    "inactive.highlight",
    "inactive.highlight.text",
}
LONG_PROPS = (
    "border-top-left-radius",
    "border-top-right-radius",
    "border-bottom-left-radius",
    "border-bottom-right-radius",
)
OUTLINE_PROP = "-gtk-outline-radius"
# St（Cinnamon / GNOME Shell）不认 @define-color 与任何 @xxx 颜色引用，
# 只有 at-rule 本身合法；这两类目录下的 @ 引用会让整条规则退化。
ST_SUBDIRS = ("cinnamon", "gnome-shell")
AT_RULE_ALLOW = {
    "import", "charset", "keyframes", "-webkit-keyframes", "font-face",
    "media", "supports", "namespace", "page",
}


def strip_comments(text: str) -> str:
    """把 /* ... */ 换成空格，保留换行，使偏移与行号仍可对上原文件。"""
    out = []
    i, n, in_comment = 0, len(text), False
    while i < n:
        if not in_comment and text.startswith("/*", i):
            in_comment = True
            out.append("  ")
            i += 2
            continue
        if in_comment and text.startswith("*/", i):
            in_comment = False
            out.append("  ")
            i += 2
            continue
        ch = text[i]
        out.append(("\n" if ch == "\n" else " ") if in_comment else ch)
        i += 1
    return "".join(out)


def _vertical_extras(text: str, block_id: int) -> float:
    """同一规则内上下 padding + 上下边框之和（GTK3 内容高语义补差）。"""
    if block_id < 0:
        return 0.0
    depth = 0
    end = block_id
    for i in range(block_id, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = text[block_id:end]
    extra = 0.0
    m = re.search(r"(?:^|;)\s*padding\s*:\s*([^;]+)", block)
    if m:
        nums = [float(p.rstrip("px")) for p in m.group(1).split()]
        if nums:
            extra += 2 * nums[0]
    m = re.search(r"(?:^|;)\s*border\s*:\s*([^;]+)", block)
    if m and m.group(1).strip() != "none":
        bm = re.match(r"\s*([0-9.]+)px", m.group(1))
        if bm:
            extra += 2 * float(bm.group(1))
    return extra


def iter_decls(text: str):
    """逐个产出 (selector, prop, value, block_id, offset)。

    block_id 用开块 '{' 的偏移标识，用来把同一规则内的声明归到一组
    （派生圆角要看同规则的 outline-offset）。
    """
    def build(decl: str, start: int):
        """把一段声明文本变成元组；偏移取首个非空白字符，行号才对得上。"""
        if ":" not in decl:
            return None
        off = start + (len(decl) - len(decl.lstrip()))
        prop, val = decl.split(":", 1)
        return (
            stack[-1] if stack else "",
            prop.strip(),
            val.strip(),
            ids[-1] if ids else -1,
            off,
        )

    i, n = 0, len(text)
    stack, ids, buf = [], [], []
    start = 0
    while i < n:
        c = text[i]
        if c == "{":
            stack.append("".join(buf).strip())
            ids.append(i)
            buf = []
            start = i + 1
        elif c == "}":
            # 收块前先 flush：规则可以写成 `radius: Xpx}` 而不带分号，
            # 不清一次缓冲就会把最后一条声明整个吞掉。
            item = build("".join(buf), start)
            buf = []
            if item:
                yield item
            if stack:
                stack.pop()
                ids.pop()
            start = i + 1
        elif c == ";":
            item = build("".join(buf), start)
            buf = []
            if item:
                yield item
            start = i + 1
        else:
            buf.append(c)
        i += 1


def radius_tokens(expr: str):
    """拆出一个圆角值表达式里的数值：简写可带 1-4 个值。"""
    for tok in expr.split():
        tok = tok.strip().rstrip(",")
        if not tok:
            continue
        if tok.endswith("%"):
            yield tok, None  # 正圆/胶囊，不按 px 判定
            continue
        try:
            yield tok, float(tok.rstrip("px"))
        except ValueError:
            continue


def selector_parts(selector: str):
    return [part.strip() for part in selector.split(",")]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_stats = "--stats" in sys.argv[1:]
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    tokens = tomllib.loads((root / "spec" / "tokens.toml").read_text())
    controls = tokens.get("controls", {})
    # [radius] 下除令牌外还有 exempt/outline 子表，只取整数项。
    allowed_radii = {v for v in tokens["radius"].values() if isinstance(v, int)}
    exempt = tokens["radius"].get("exempt", {})
    outline_offset_default = tokens["radius"].get("outline", {}).get("offset", 0)
    failures = []
    # 目录缺失会让 GTK 检查整段静默跳过（改名事故），必须显式报错。
    for theme in THEME_DIRS:
        if not (root / GTK_PKG / theme).is_dir():
            failures.append(f"{GTK_PKG / theme} 不存在：THEME_DIRS 与实际目录漂移")
    stats = {"short": 0, "long": 0, "outline": 0, "ok": 0, "zero": 0, "pct": 0, "drift": 0}

    hex_re = re.compile(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b")
    for theme in THEME_DIRS:
        for sub in GTK_SUBDIRS:
            css_dir = root / GTK_PKG / theme / sub
            if not css_dir.is_dir():
                continue
            for css in sorted(css_dir.rglob("*.css")):
                raw = css.read_text(errors="replace")
                text = strip_comments(raw)
                rel = css.relative_to(root)
                # 先收集本文件的声明，才能按规则取 outline-offset。
                decls = list(iter_decls(text))
                def px(expr: str) -> float:
                    try:
                        return float(expr.strip().rstrip("px"))
                    except ValueError:
                        return 0.0

                offsets = {
                    block: px(val) for _, prop, val, block, _ in decls if prop == "outline-offset"
                }

                def lineno(off: int) -> int:
                    return text.count("\n", 0, off) + 1

                def exempted(selector: str, tok: str) -> bool:
                    # 精确匹配：选择器列表逐项相等，不做子串匹配，
                    # 否则 .help_buttonx / .help_button:hover 会被误放行。
                    parts = selector_parts(selector)
                    return any(
                        rule.get("radius") == tok and key in parts
                        for key, rule in exempt.items()
                    )

                for selector, prop, val, block, off in decls:
                    if prop == "border-radius":
                        stats["short"] += 1
                        for tok, num in radius_tokens(val):
                            if num is None:
                                stats["pct"] += 1
                            elif num == 0:
                                stats["zero"] += 1
                            elif num in allowed_radii or exempted(selector, tok):
                                stats["ok"] += 1
                            else:
                                stats["drift"] += 1
                                failures.append(
                                    f"{rel}:{lineno(off)} border-radius {tok} 不在令牌集合 {sorted(allowed_radii)}"
                                )
                                break
                    elif prop in LONG_PROPS:
                        stats["long"] += 1
                        for tok, num in radius_tokens(val):
                            if num is None:
                                stats["pct"] += 1
                            elif num == 0:
                                stats["zero"] += 1
                            elif num in allowed_radii or exempted(selector, tok):
                                stats["ok"] += 1
                            else:
                                stats["drift"] += 1
                                failures.append(
                                    f"{rel}:{lineno(off)} {prop} {tok} 不在令牌集合 {sorted(allowed_radii)}"
                                )
                                break
                    elif prop == OUTLINE_PROP:
                        stats["outline"] += 1
                        for tok, num in radius_tokens(val):
                            if num is None:
                                stats["pct"] += 1
                                continue
                            offset = offsets.get(block, outline_offset_default)
                            shape = num - offset
                            if shape == 0 or shape in allowed_radii or exempted(selector, tok):
                                stats["ok"] += 1
                            else:
                                stats["drift"] += 1
                                failures.append(
                                    f"{rel}:{lineno(off)} {prop} {tok} 与形状半径不自洽"
                                    f"（{tok} - outline-offset {offset:g} = {shape:g}，"
                                    f"应落在 {sorted(allowed_radii)} 或 0）"
                                )
                                break

                # 硬编码颜色：@define-color 行允许字面值（默认值声明），其余行不允许。
                # 跳过 url(...) 内 / url 文件名 里的 hex 片段（如 titlebutton@2x.png）。
                for lineno_, line in enumerate(raw.splitlines(), 1):
                    stripped = line.strip()
                    if "@define-color" in stripped:
                        continue
                    for m in hex_re.finditer(stripped):
                        if "url(" in stripped:
                            cleaned = re.sub(r"url\([^)]*\)", "", stripped)
                            if not hex_re.search(cleaned):
                                continue
                        failures.append(
                            f"{rel}:{lineno_} 硬编码颜色 {m.group(0)}（须走 @define-color）"
                        )
                        break

    # 3. St 目录（cinnamon / gnome-shell）不得出现 @xxx 颜色引用
    at_re = re.compile(r"@([\w-]+)")
    for theme in THEME_DIRS:
        for sub in ST_SUBDIRS:
            css_dir = root / GTK_PKG / theme / sub
            if not css_dir.is_dir():
                continue
            for css in sorted(css_dir.rglob("*.css")):
                raw = css.read_text(errors="replace")
                # 注释里提到 @define-color 是说明文字，不算引用。
                for lineno_, line in enumerate(strip_comments(raw).splitlines(), 1):
                    cleaned = re.sub(r"url\([^)]*\)", "", line)
                    for m in at_re.finditer(cleaned):
                        name = m.group(1).lower()
                        if name in AT_RULE_ALLOW:
                            continue
                        failures.append(
                            f"{css.relative_to(root)}:{lineno_} St 不支持 @{m.group(1)}"
                            f"（只能用 at-rule；accent 走 @import 注入）"
                        )
                        break

    # 4. kvconfig 必须给出 [GeneralColors] 全套角色
    kvcfg = root / SHELL_PKG / "Assets" / "Templates" / "kvantum-kvlibadwaita.kvconfig"
    if kvcfg.exists():
        text = kvcfg.read_text()
        rel = kvcfg.relative_to(root)
        if "[GeneralColors]" not in text:
            failures.append(
                f"{rel} 缺少 [GeneralColors] 节：Kvantum 只填出现过的角色，"
                f"缺失项回落到 Qt 默认调色板（Window #efefef）"
            )
        else:
            block = text.split("[GeneralColors]", 1)[1].split("\n[", 1)[0]
            present = set(re.findall(r"^([a-z.]+)\.color\s*=", block, re.M))
            missing = GENERAL_COLOR_ROLES - present
            if missing:
                failures.append(
                    f"{rel} 的 [GeneralColors] 缺角色：" + ", ".join(sorted(missing))
                )

    # 5. nosdshell Style.qml 半径——monorepo 内是硬依赖，缺失即漂移
    style_qml = root / SHELL_PKG / "Commons" / "Style.qml"
    if not style_qml.exists():
        failures.append(f"{style_qml.relative_to(root)} 不存在")
    else:
        style = style_qml.read_text()
        mapping = {
            "radiusXS": "radiusRow",
            "radiusS": "radiusItem",
            "radiusM": "radiusPopup",
            "radiusL": "radiusWindow",
        }
        for prop, token in mapping.items():
            m = re.search(rf"property int {prop}: Math\.round\((\d+) \*", style)
            if m and int(m.group(1)) != tokens["radius"][token]:
                failures.append(f"Style.qml {prop}={m.group(1)} 与 {token}={tokens['radius'][token]} 不一致")
        # 按钮胶囊半径（NButton buttonRadius 的令牌来路）
        m = re.search(r"property int radiusPill: (\d+)", style)
        if m and "button_radius" in controls and int(m.group(1)) != controls["button_radius"]:
            failures.append(
                f"Style.qml radiusPill={m.group(1)} 与 controls.button_radius={controls['button_radius']} 不一致"
            )

    # 6. Kvantum SVG / 模板 XML 良构性（QSvgRenderer 严格解析，坏一处整图失效）
    placeholder_re = re.compile(r"\{\{[^}]*\}\}")
    svg_paths = [
        root / KVANTUM_PKG / "KvLibadwaita" / "KvLibadwaita.svg",
        root / KVANTUM_PKG / "KvLibadwaita" / "KvLibadwaitaDark.svg",
        root / SHELL_PKG / "Assets" / "Templates" / "kvantum-kvlibadwaita.svg",
    ]
    for svg in svg_paths:
        rel = svg.relative_to(root)
        if not svg.exists():
            failures.append(f"{rel} 不存在")
            continue
        body = placeholder_re.sub("#123456", svg.read_text(errors="replace"))
        try:
            ET.fromstring(body)
        except ET.ParseError as e:
            failures.append(
                f"{rel} XML 解析失败：{e}"
                "（QSvgRenderer 会拒载整图，Kvantum 全部回落内置默认主题）"
            )

    # 8. [overlay] 梯级 ↔ GTK 叠加面 @define-color 的 α；
    #    [controls] 控件几何 ↔ GTK 控件尺寸 / kvconfig / Style.qml。
    overlay = tokens.get("overlay", {})
    overlay_re = re.compile(
        r"@define-color\s+(idle|subtle|hover|field|strong|checked|press|indicator)"
        r"(?:_color)?\s+alpha\(\s*@[\w-]+\s*,\s*([0-9.]+)\s*\)"
    )
    # 7b. shell GTK 模板（审计/真机渲染的真正来源）叠加梯必须是 alpha 相对形式。
    #     模板 define 在运行时覆盖主题包静态 define；若烘焙成 overlay_* 实色，
    #     滑槽(strong)与设置行卡(strong)同值→滑槽隐形（DESIGN §8.1 要求 GTK 用
    #     alpha(前景色,α)）。overlay_re 只匹配 alpha 形式，烘焙实色会判缺项。
    for tpl_name in ("gtk3.css", "gtk4.css"):
        tpl = root / SHELL_PKG / "Assets" / "Templates" / tpl_name
        if not tpl.exists():
            failures.append(f"{tpl.relative_to(root)} 不存在")
            continue
        ttext = strip_comments(tpl.read_text(errors="replace"))
        trel = tpl.relative_to(root)
        tlevels = {}
        for m in overlay_re.finditer(ttext):
            level, alpha = m.group(1), float(m.group(2))
            tlevels[level] = alpha
            if level in overlay and abs(alpha - overlay[level]) > 1e-6:
                failures.append(
                    f"{trel} {level}_color α={alpha:g} 与"
                    f" overlay.{level}={overlay[level]} 不一致"
                )
        for level in ("idle", "field", "strong", "checked", "press"):
            if level not in tlevels:
                failures.append(
                    f"{trel} 缺 @define-color {level}_color"
                    f" alpha(<前景色>, {overlay.get(level)})"
                    "（GTK 叠加梯须 alpha 相对形式，不得烘焙实色）"
                )
    # 选择器 → 必须等于令牌值的 min-* 几何（px）。
    gtk_geom = {
        ("scale slider", "min-height"): "slider_knob",
        ("scale slider", "min-width"): "slider_knob",
        ("scale trough", "min-height"): "slider_groove",
        ("scale.vertical trough", "min-width"): "slider_groove",
        ("switch", "min-width"): "switch_width",
        ("switch", "min-height"): "switch_height",
        ("switch slider", "min-width"): "switch_knob",
        ("switch slider", "min-height"): "switch_knob",
        ("entry", "min-height"): "field_height",
        ("combobox button.combo", "min-height"): "field_height",
        ("spinbutton:not(.vertical)", "min-height"): "field_height",
        ("dropdown > button", "min-height"): "field_height",
        ("button", "min-height"): "button_height",
        ("progressbar trough", "min-height"): "slider_groove",
        ("progressbar > trough > progress", "min-height"): "slider_groove",
        ("progressbar progress", "min-height"): "slider_groove",
        ("headerbar progressbar trough", "min-height"): "slider_groove",
        (".primary-toolbar progressbar trough", "min-height"): "slider_groove",
    }
    for theme in THEME_DIRS:
        for sub in GTK_SUBDIRS:
            css_dir = root / GTK_PKG / theme / sub
            if not css_dir.is_dir():
                continue
            for css in sorted(css_dir.rglob("*.css")):
                raw = css.read_text(errors="replace")
                text = strip_comments(raw)
                rel = css.relative_to(root)
                found_levels = set()
                for m in overlay_re.finditer(text):
                    level, alpha = m.group(1), float(m.group(2))
                    found_levels.add(level)
                    if level in overlay and abs(alpha - overlay[level]) > 1e-6:
                        ln = text.count("\n", 0, m.start()) + 1
                        failures.append(
                            f"{rel}:{ln} {level}_color α={alpha:g} 与"
                            f" overlay.{level}={overlay[level]} 不一致"
                        )
                # 完整控件样式表（含 entry 规则）必须声明四个核心叠加面；
                # window-controls/gtk4-compat 这类片段不强制。
                if re.search(r"^entry\s*\{", text, re.M):
                    for level in ("idle", "field", "strong", "checked"):
                        if level not in found_levels:
                            failures.append(
                                f"{rel} 缺 @define-color {level}_color"
                                f" alpha(<前景色>, {overlay.get(level)})"
                            )
                for selector, prop, val, _block, off in iter_decls(text):
                    # 按钮胶囊半径必须恰为 button_radius（集合成员检查不够：
                    # 4/6px 也在令牌集合内，但会把胶囊压成圆角矩形）。
                    if (selector, prop) == ("button", "border-radius") and "button_radius" in controls:
                        if val.rstrip("px") != str(controls["button_radius"]):
                            ln = text.count("\n", 0, off) + 1
                            failures.append(
                                f"{rel}:{ln} button border-radius={val}"
                                f" 与 controls.button_radius={controls['button_radius']}px 不一致"
                            )
                    want = gtk_geom.get((selector, prop))
                    if want is None or want not in controls:
                        continue
                    try:
                        num = float(val.rstrip("px"))
                    except ValueError:
                        continue
                    # GTK3 的 min-height 是内容高，总高还要加上下 padding/边框；
                    # GTK4 的 min-height 含边框与内边距，直接比令牌。
                    if sub == "gtk-3.0" and want in ("field_height", "button_height"):
                        num += _vertical_extras(text, _block)
                    if num != controls[want]:
                        ln = text.count("\n", 0, off) + 1
                        failures.append(
                            f"{rel}:{ln} {selector} {prop}={val} 与"
                            f" controls.{want}={controls[want]}px 不一致"
                        )
    # Style.qml 侧控件几何（沿用 #5 的 Math.round(N * 缩放) 形式）
    if style_qml.exists():
        style = style_qml.read_text()
        for prop, token in (
            ("switchWidth", "switch_width"),
            ("switchHeight", "switch_height"),
            ("settingsFieldHeight", "field_height"),
            ("settingsFieldWidth", "field_width"),
            ("detailRowHeight", "row_height"),
        ):
            m = re.search(rf"property (?:int|real) {prop}: Math\.round\((\d+)", style)
            if m and token in controls and int(m.group(1)) != controls[token]:
                failures.append(
                    f"Style.qml {prop}={m.group(1)} 与 controls.{token}={controls[token]} 不一致"
                )

    if want_stats:
        print(
            "radius 统计："
            f"简写 {stats['short']} 条 / 长写 {stats['long']} 条 / outline {stats['outline']} 条"
        )
        print(
            f"  合令牌 {stats['ok']} · 零值 {stats['zero']} · 百分比 {stats['pct']}"
            f" · 漂移 {stats['drift']}"
        )

    if failures:
        print(f"check-tokens: {len(failures)} 处漂移")
        for f in failures:
            print(" -", f)
        return 1
    print("check-tokens: 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
