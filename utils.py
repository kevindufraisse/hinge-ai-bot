# utils.py
import os
import time

def log_profile(index: int, prompt: str, action: str, base_dir: str = "logs"):
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, "run.log")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] #{index:04d} | {action} | {prompt}\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[log_profile] error: {e}")

def take_screenshot(driver, index: int, base_dir: str = "shots"):
    try:
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, f"profile_{index:04d}.png")
        driver.save_screenshot(path)
    except Exception as e:
        print(f"[take_screenshot] error: {e}")
