pragma Singleton

import QtQuick
import Quickshell

Singleton {
  // Version constants for display (About page) and diagnostics.
  // Keep baseVersion in sync with the package version in nosd-pkgs
  // (guix/nosd/packages.scm %nosdshell-version / nix flake nosdshellVersion).
  readonly property string baseVersion: "1.7.7"
  readonly property bool isDevelopment: false
  readonly property string developmentSuffix: "-git"
  readonly property string currentVersion: `v${!isDevelopment ? baseVersion : baseVersion + developmentSuffix}`
}
