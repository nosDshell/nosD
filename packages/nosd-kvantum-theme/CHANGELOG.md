# 更新日志

本文件记录 nosd-kvantum-theme 组件面向使用者的变更（nosd monorepo 的
`packages/nosd-kvantum-theme/`）。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.0] - 2026-11-17

控件形状换成上游 Colloid，主题名保持 `KvLibadwaita` 不变；并按 adw-gtk3 的圆角规格对齐。

### 变更

- 主题目录内容换成上游 `Kvantum/Colloid/` 原样，文件名保持
  `KvLibadwaita.kvconfig|svg` + 同目录 `KvLibadwaitaDark.kvconfig|svg`。
- shell 模板改为 `kvantum-colloid.{svg,kvconfig}`：SVG 只替换语义色
  （accent → primary、紫 → tertiary、红 → error），灰阶与控件参数保持上游。
- 圆角按 adw-gtk3 对齐：按钮/输入框/下拉框 6 → 9px，菜单 10px，菜单项 6px，
  ToolTip 8px。
- 菜单项上下内边距 4 → 6px，文字左右留白 6 → 8px。
- 禁用 Kvantum 焦点框：上游未定义 `focus` 元素，回落的内置直角虚框与圆角控件不协调。

## [0.3.0] - 2026-11-16

控件形状改用上游 KvLibadwaita，本组件只保留颜色注入。

### 变更

- 主题目录 `nosd/` 改为 `KvLibadwaita/`，内容为上游 src 原样：浅色
  `KvLibadwaita.kvconfig|svg` + 同目录 `KvLibadwaitaDark.kvconfig|svg`
  （Kvantum 原生「浅色目录内放深色变体」布局）。
- 删除自研 `nosd.kvconfig`/`nosd.svg`、两份 `.tpl` 与 `tools/svg-round.py`；
  shell 渲染输入的唯一源是 `packages/nosdshell/Assets/Templates/kvantum-kvlibadwaita.*`。
- 上游素材按 GPL-3.0-only 收录，新增 `LICENSES/GPL-3.0-only.txt` 与素材内 `CREDITS`。
- 运行时主题名改为 `KvLibadwaita`；`theme=nosd` 的部署需同步改
  `~/.config/Kvantum/kvantum.kvconfig`。


## [0.2.0] - 2026-11-16

nosd Kvantum 主题与 nosDshell 设置页一比一对齐：控件面、叠加梯、按钮胶囊与
field 形态收敛到壳层令牌，11 套配色 × 明暗 22 组跨端截图审计全绿。

### 变更

- *与 shell 设置页一比一对齐（DESIGN §8）*：控件面接入壳层叠加梯，滑槽用
  `on_surface × fill-opacity` 相对合成（有效不透明度≈标称 2/3），叠在设置行卡上
  比卡面亮一档；按钮胶囊半径、field 去细边、指示器调色板跟随，数值统一走
  `spec/tokens.toml`。
- *滚动条滑块接入叠加梯 indicator 档*：滑块亮度与 shell 一致。

### 修复

- *控件级几何与状态按令牌收敛*：圆角/边距/状态色对齐 shell，消除漂移。

## [0.1.0] - 2026-11-14

### 变更

- 由 nosd-app-themes 仓的 `kvantum/nosd/` 拆分为独立组件：nosd Kvantum
  主题（`nosd.kvconfig` + `nosd.svg`，附 `.tpl` 模板源）与
  `tools/svg-round.py`。内容与 nosd-app-themes v0.2.0 中的版本一致，
  安装路径不变（`share/Kvantum/nosd/`）。
