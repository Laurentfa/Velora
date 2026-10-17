#!/bin/zsh
# Fabrique à partir de dist/Velora.app les deux fichiers à publier : le .dmg (première installation) et le .zip (mises à jour automatiques).
# Usage : scripts/package_mac.sh 1.2.0      (après ./build_mac.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:?numéro de version, ex : 1.2.0}"
OUT="dist/release"; rm -rf "$OUT" "dist/dmg"; mkdir -p "$OUT" "dist/dmg"
ditto -c -k --keepParent dist/Velora.app "$OUT/Velora-$V-mac.zip"
cp -R dist/Velora.app "dist/dmg/"
ln -s /Applications "dist/dmg/Applications"
cp INSTALLER.md "dist/dmg/Lisez-moi (première ouverture).txt"
# hdiutil répond parfois « Resource busy » sur les machines de construction : on réessaie
for try in 1 2 3 4; do hdiutil create -volname "Velora" -srcfolder "dist/dmg" -ov -format UDZO "$OUT/Velora-$V-mac.dmg" >/dev/null && break; [ $try = 4 ] && exit 1; sleep 10; done
rm -rf "dist/dmg"
ls -lh "$OUT"
