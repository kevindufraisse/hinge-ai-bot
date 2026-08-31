import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

APPIUM_CAPS = {
    "platformName": os.getenv("APPIUM_PLATFORM_NAME", "Android"),
    "platformVersion": os.getenv("APPIUM_PLATFORM_VERSION", "16"),
    "deviceName": os.getenv("APPIUM_DEVICE_NAME", "emulator-5554"),
    "appPackage": "co.hinge.app",
    "appActivity": "co.hinge.app.ui.AppActivity",
    "automationName": "UiAutomator2",
    "noReset": True,
    "newCommandTimeout": 600,
}
