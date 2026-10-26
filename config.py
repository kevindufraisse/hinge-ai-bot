import os
import subprocess

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


def _adb_path() -> str:
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        return os.path.join(sdk, "platform-tools", "adb")
    return os.path.join(os.path.expanduser("~/Library/Android/sdk"), "platform-tools", "adb")


def _adb_devices() -> list[str]:
    """Liste les UDID des appareils/émulateurs en ligne (`adb devices`)."""
    try:
        out = subprocess.run(
            [_adb_path(), "devices"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except Exception:
        return []
    devices: list[str] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def resolve_device_name() -> str:
    """UDID du 1er appareil en ligne, ou fallback si aucun.

    Priorité : variable d'env APPIUM_DEVICE_NAME > détection `adb devices`.
    """
    explicit = os.getenv("APPIUM_DEVICE_NAME", "").strip()
    if explicit:
        return explicit
    devices = _adb_devices()
    if devices:
        # Préfère un émulateur (emulator-XXXX) s'il y en a un.
        emus = [d for d in devices if d.startswith("emulator-")]
        return (emus or devices)[0]
    return "emulator-5554"


APPIUM_CAPS = {
    "platformName": os.getenv("APPIUM_PLATFORM_NAME", "Android"),
    "platformVersion": os.getenv("APPIUM_PLATFORM_VERSION", "16"),
    "deviceName": resolve_device_name(),
    "appPackage": "co.hinge.app",
    "appActivity": "co.hinge.app.ui.AppActivity",
    "automationName": "UiAutomator2",
    "noReset": True,
    "newCommandTimeout": 600,
    # Android 16 (API 36) : le serveur UIAutomator2 démarre très lentement (~28 s)
    # et crashe de façon intermittente. On monte le délai de lancement du serveur.
    "uiautomator2ServerLaunchTimeout": int(os.getenv("UIA2_LAUNCH_TIMEOUT", "120000")),
}
