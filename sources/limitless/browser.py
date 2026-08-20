from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import logging
import os
import shutil
import tempfile
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("ptcgp")
netlog = logging.getLogger("ptcgp.net")

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # optional fallback if not installed
    ChromeDriverManager = None


def make_chrome(*, headless: bool = True, detach: bool = False) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-logging")
    opts.add_argument("--remote-debugging-pipe")
    opts.add_argument("--no-sandbox")
    opts.page_load_strategy = "eager"
    opts.add_experimental_option("detach", detach)
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    tmp_profile = Path(tempfile.mkdtemp(prefix="selenium-profile-"))
    opts.add_argument(f"--user-data-dir={tmp_profile}")

    try:
        if ChromeDriverManager is not None:
            service = Service(ChromeDriverManager().install(), log_output=os.devnull)
            driver = webdriver.Chrome(service=service, options=opts)
        else:
            raise RuntimeError("webdriver-manager non disponibile")
    except Exception:
        service = Service(log_output=os.devnull)
        driver = webdriver.Chrome(service=service, options=opts)

    driver._profile_dir = str(tmp_profile)
    driver._profile_is_temp = True
    return driver


def close_chrome(driver: webdriver.Chrome) -> None:
    try:
        driver.quit()
    finally:
        try:
            if getattr(driver, "_profile_is_temp", False):
                shutil.rmtree(getattr(driver, "_profile_dir", ""), ignore_errors=True)
        except Exception:
            pass


@contextmanager
def chrome(*, headless: bool = True, detach: bool = False):
    drv = make_chrome(headless=headless, detach=detach)
    try:
        yield drv
    finally:
        close_chrome(drv)


def wait_css(driver: webdriver.Chrome, css: str, timeout: int = 20):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))


def safe_get(driver: webdriver.Chrome, url: str, *, wait_css_selector: Optional[str] = None, timeout: int = 20) -> None:
    netlog.debug("GET (selenium): %s", url)
    driver.get(url)
    if wait_css_selector:
        try:
            wait_css(driver, wait_css_selector, timeout)
        except TimeoutException:
            log.error("Timeout in attesa di '%s' su %s", wait_css_selector, url)
            raise


def polite_sleep(seconds: float) -> None:
    time.sleep(float(seconds))

__all__ = ["make_chrome", "close_chrome", "chrome", "wait_css", "safe_get", "polite_sleep"]
