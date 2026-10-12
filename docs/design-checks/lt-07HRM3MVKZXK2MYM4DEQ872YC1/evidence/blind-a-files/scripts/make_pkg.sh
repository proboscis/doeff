#!/bin/sh
# 使い手 pkg を作る: $1 = 根の dir・$2 = answer の値
set -e
mkdir -p "$1/pkg"
: > "$1/pkg/__init__.py"
printf '(defmacro answer [] %s)\n' "$2" > "$1/pkg/macros.hy"
printf '(defn f []\n  (require pkg.macros [answer])\n  (defn g [] (answer))\n  (g))\n(setv value (f))\n' > "$1/pkg/user.hy"
