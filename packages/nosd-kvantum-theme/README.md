# nosd-kvantum-theme

nosDshell 的 Kvantum Qt 控件主题。形状直接采用上游
[KvLibadwaita](https://github.com/GabePoel/KvLibadwaita)
（Libadwaita 风格的 Kvantum 主题），本仓不再自研控件形状。

## 内容

| 路径 | 内容 |
| --- | --- |
| `KvLibadwaita/KvLibadwaita.kvconfig` `KvLibadwaita.svg` | 浅色主题（上游原样） |
| `KvLibadwaita/KvLibadwaitaDark.kvconfig` `KvLibadwaitaDark.svg` | 深色变体（上游原样，放在浅色目录内，Kvantum 原生契约） |
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
（`packages/nosdshell/Assets/Templates/kvantum-kvlibadwaita.{svg,kvconfig}`），
渲染到 `~/.config/Kvantum/KvLibadwaita/`，覆盖这里的静态素材。
模板与静态素材形状同源（上游草稿 vs 上游产物），颜色来源不同属预期。

## 许可

`KvLibadwaita/` 下的主题素材来自上游 KvLibadwaita，GPL-3.0-only，
见仓根 `LICENSES/GPL-3.0-only.txt` 与素材目录内 `CREDITS`。

## 打包

分发在 [`nosd-pkgs`](https://github.com/nosDshell/nosd-pkgs)：
`guix install nosd-kvantum-theme` 或 flake 的同名包（下次发版时同步 install-plan）。

## 发版

独立组件发版：tag `nosd-kvantum-theme-v<version>`，见仓根
[`docs/adr/0001`](../../docs/adr/0001-monorepo.md) 与
`tools/release/build.py`。
