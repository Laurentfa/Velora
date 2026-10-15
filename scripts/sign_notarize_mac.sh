#!/bin/zsh
# Signature « Developer ID » + notarisation Apple + agrafage : l'appli s'ouvre sans avertissement sur les Mac des utilisateurs, et le
# Trousseau ne redemande plus l'accès après chaque mise à jour. NON TESTÉ : il faut un compte Apple Developer (99 $/an).
#
# Variables à définir avant de lancer :
#   DEVELOPER_ID   ex : "Developer ID Application: Votre Nom (ABCDE12345)"
#   APPLE_ID       l'adresse du compte Apple Developer
#   TEAM_ID        l'identifiant d'équipe (10 caractères)
#   APP_PASSWORD   mot de passe d'application créé sur appleid.apple.com (jamais le mot de passe du compte)
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/Velora.app"
: "${DEVELOPER_ID:?}" "${APPLE_ID:?}" "${TEAM_ID:?}" "${APP_PASSWORD:?}"
echo "→ signature"
codesign --force --deep --options runtime --timestamp --entitlements scripts/mailia.entitlements --sign "$DEVELOPER_ID" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "→ envoi à Apple (quelques minutes)"
ditto -c -k --keepParent "$APP" dist/Velora.zip
xcrun notarytool submit dist/Velora.zip --apple-id "$APPLE_ID" --team-id "$TEAM_ID" --password "$APP_PASSWORD" --wait
xcrun stapler staple "$APP"
spctl --assess --type execute --verbose "$APP"
echo "→ prêt : $APP (à compresser pour la distribution)"
