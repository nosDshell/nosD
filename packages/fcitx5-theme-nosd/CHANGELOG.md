# 更新日志

本文件记录 fcitx5-theme-nosd 组件面向使用者的变更（nosd monorepo 的
`packages/fcitx5-theme-nosd/`；历史见原 fcitx5-theme-nosd 仓与
`debian/changelog`）。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.2.0] - 2026-11-16

### 变更

- *皮肤更名 nosd*：`deepin-light`/`deepin-dark` 两套 fcitx5 皮肤更名为
  `nosd`/`nosd-dark`（deepin 为历史残留），与 nosDshell 品牌一致。deb/arch
  打包物料与安装路径同步更新；fcitx5 配置里引用旧皮肤名的需改为
  `nosd`/`nosd-dark`。

## [1.1.0] - 2026-11-14

### 变更

- 原 fcitx5-theme-nosd 仓并入 nosd monorepo 成为独立组件：
  `deepin-light`、`deepin-dark` 两套 fcitx5 皮肤与 deb/arch 打包物料。
  安装路径不变（`share/fcitx5/themes/`）。版本号沿用原 debian
  changelog 的 1.1 线。
