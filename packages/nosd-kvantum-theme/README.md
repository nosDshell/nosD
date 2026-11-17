# nosd-kvantum-theme

nosDshell 的 Kvantum Qt 控件主题。形状与控件参数直接采用上游
[Colloid-kde](https://github.com/vinceliuice/Colloid-kde)
（Vince Liuice 的 Kvantum 主题），本仓不再自研控件形状。

## 内容

| 路径 | 内容 |
| --- | --- |
| `KvLibadwaita/KvLibadwaita.kvconfig` `KvLibadwaita.svg` | 浅色主题（上游 Colloid 原样） |
| `KvLibadwaita/KvLibadwaitaDark.kvconfig` `KvLibadwaitaDark.svg` | 深色变体（上游 Colloid 原样，放在浅色目录内，Kvantum 原生契约） |
| `KvLibadwaita/CREDITS` | 上游来源与致谢 |

## 安装

```sh
make install          # 装到 $PREFIX/share/Kvantum/KvLibadwaita（PREFIX 默认 /usr）
```

Qt 应用侧：安装 `kvantum` 包，`~/.config/qt5ct/qt5ct.conf` 与 `~/.config/qt6ct/qt6ct.conf` 设 `style=Kvantum`，
`~/.config/Kvantum/kvantum.kvconfig` 写 `theme=KvLibadwaita`。
浅色与深色变体由 Qt style 名选择（`kvantum` → 浅色，`kvantum-dark` → 深色）。

## 与 nosDshell 的关系

nosDshell 运行时会把壁纸调色板代入自己的模板
（`packages/nosdshell/Assets/Templates/kvantum-colloid.{svg,kvconfig}`），
渲染到 `~/.config/Kvantum/KvLibadwaita/`，覆盖这里的静态素材。
模板在上游形状基础上只替换语义色（accent → primary、紫 → tertiary、红 → error），
灰阶与控件参数保持上游值；圆角按 adw-gtk3 的规格对齐。

## 许可

`KvLibadwaita/` 下的主题素材来自上游 Colloid-kde，GPL-3.0-only，
见仓根 `LICENSES/GPL-3.0-only.txt` 与素材目录内 `CREDITS`。
