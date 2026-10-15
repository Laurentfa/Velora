#!/bin/zsh
# Construit « Velora.app » (dossier dist/). À lancer sur un Mac.
cd "$(dirname "$0")"
NG=$(.venv/bin/python -c "import nicegui,os;print(os.path.dirname(nicegui.__file__))")
.venv/bin/pyinstaller main.py --name "Velora" --windowed --noconfirm --clean \
  --icon assets/icon.icns --osx-bundle-identifier fr.lfauvel.mailia \
  --add-data "$NG:nicegui" \
  --collect-all exchangelib --collect-all webview --collect-all keyring --collect-all mammoth --collect-all openpyxl --collect-all pptx --collect-all pillow_heif --collect-all pypdf --collect-all zeroconf --collect-all segno --collect-all sqlcipher3 --collect-all dns --hidden-import LocalAuthentication --collect-submodules mailia \
  --collect-data tzdata
# Numérisation : macOS exige de déclarer l'usage du réseau local et des services Bonjour recherchés (scanners AirScan)
PL="dist/Velora.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSLocalNetworkUsageDescription string Velora cherche les scanners de votre réseau et reçoit les pages photographiées par votre téléphone." "$PL" 2>/dev/null
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices array" "$PL" 2>/dev/null
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:0 string _uscan._tcp" "$PL" 2>/dev/null
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:1 string _uscans._tcp" "$PL" 2>/dev/null
# Images distantes en http:// (logo de signature hébergé sans HTTPS, newsletters) : sans cette exception, la fenêtre macOS les bloque
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PL" 2>/dev/null
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsArbitraryLoadsInWebContent bool true" "$PL" 2>/dev/null
# langues de l'appli : sans cela macOS la croit uniquement anglaise et affiche en anglais ses propres menus (clic droit dans un champ : Copy, Paste, Look Up…)
/usr/libexec/PlistBuddy -c "Set :CFBundleDevelopmentRegion fr" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleDevelopmentRegion string fr" "$PL" 2>/dev/null
/usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations array" "$PL" 2>/dev/null
n=0; for lang in fr en; do /usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations:$n string $lang" "$PL" 2>/dev/null; n=$((n+1)); done
codesign --force --deep --sign - "dist/Velora.app" 2>/dev/null  # la modification du plist invalide la signature ad hoc : on la refait
echo "→ dist/Velora.app"
