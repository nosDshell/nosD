# 更新日志

本文件记录 nosd-kvantum-theme 组件面向使用者的变更（nosd monorepo 的
`packages/nosd-kvantum-theme/`）。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
