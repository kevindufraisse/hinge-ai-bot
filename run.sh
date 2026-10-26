#!/usr/bin/env bash
#
# Démarre l'émulateur, Appium, puis lance le bot Hinge.
#
# Usage :
#   ./run.sh                # utilise $AVD_NAME ou "Medium_Phone_API_36"
#   ./run.sh Pixel_9a       # choisit l'AVD en argument
#
# Variables d'env optionnelles :
#   AVD_NAME   : nom de l'AVD à booter (défaut : 1er argument ou Medium_Phone_API_36)
#   APPIUM_PLATFORM_VERSION : version Android attendue (défaut : 16)

set -euo pipefail

# --- Chemins SDK ---
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"

ADB="$SDK/platform-tools/adb"
EMU="$SDK/emulator/emulator"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AVD_NAME="${AVD_NAME:-${1:-Medium_Phone_API_36}}"

[ -x "$ADB" ] || { echo "❌ adb introuvable : $ADB"; exit 1; }
[ -x "$EMU" ] || { echo "❌ emulator introuvable : $EMU"; exit 1; }

# --- 1) Vérifie que l'AVD existe ---
if ! "$EMU" -list-avds 2>/dev/null | grep -qx "$AVD_NAME"; then
  echo "❌ AVD '$AVD_NAME' introuvable. AVD disponibles :"
  "$EMU" -list-avds 2>/dev/null | sed 's/^/    /'
  echo
  echo "👉 Crée un AVD Android 14 (API 34) ou 15 (API 35) dans"
  echo "   Android Studio > Device Manager, puis relance : ./run.sh <nom-avd>"
  exit 1
fi

# --- 2) Démarre l'émulateur si aucun n'est en ligne ---
if ! "$ADB" devices | grep -qE 'emulator-[0-9]+[[:space:]]+device'; then
  echo "▶️  Boot de l'émulateur '$AVD_NAME'…"
  "$EMU" -avd "$AVD_NAME" -no-snapshot-load -no-boot-anim >/dev/null 2>&1 &
  "$ADB" wait-for-device
  until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    sleep 2
  done
  echo "✅ Émulateur booté."
else
  echo "ℹ️  Émulateur déjà en ligne."
fi

# --- 3) Démarre Appium si nécessaire ---
if ! curl -sf http://127.0.0.1:4723/status >/dev/null 2>&1; then
  echo "▶️  Démarrage d'Appium…"
  appium >"$REPO_DIR/appium.log" 2>&1 &
  until curl -sf http://127.0.0.1:4723/status >/dev/null 2>&1; do
    sleep 1
  done
  echo "✅ Appium prêt (logs dans $REPO_DIR/appium.log)."
else
  echo "ℹ️  Appium déjà en cours."
fi

# --- 4) Lance le bot ---
cd "$REPO_DIR"
source .venv/bin/activate
exec python3 appium_bot.py
