# -*- coding: utf-8 -*-
"""
ChatGPT Free API - Undetected Chromedriver Wrapper
Uses undetected-chromedriver to bypass Cloudflare bot detection
and automate the free ChatGPT web interface.
"""

import asyncio
import logging
import os
import random
import re
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException


# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chatgpt-api")

# --- Constants ---------------------------------------------------------------
CHATGPT_URL = "https://chatgpt.com/"
MAX_WAIT_SECONDS = 120
POLL_INTERVAL = 1.0
MAX_RETRIES = 3

# Path for persistent browser profile
BROWSER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome_profile_uc")


def get_chrome_version_main() -> Optional[int]:
    """Helper to detect Google Chrome's major version number dynamically."""
    for binary in ['google-chrome', 'chrome', 'chromium-browser', 'chromium']:
        try:
            output = subprocess.check_output([binary, '--version'], stderr=subprocess.STDOUT)
            version_str = output.decode('utf-8')
            match = re.search(r'(?:Chrome|Chromium)\s+(\d+)', version_str, re.IGNORECASE)
            if match:
                return int(match.group(1))
        except Exception:
            continue
    return None


# --- Browser Manager ---------------------------------------------------------
class BrowserManager:
    """
    Manages a persistent Google Chrome instance using undetected-chromedriver.
    """

    def __init__(self):
        self._driver: Optional[uc.Chrome] = None
        self._lock = asyncio.Lock()

    def start_driver(self):
        """Starts undetected-chromedriver synchronously (run in executor)."""
        log.info("Starting undetected-chromedriver (headless=new)...")

        def create_options():
            opts = uc.ChromeOptions()
            opts.add_argument('--no-sandbox')
            opts.add_argument('--disable-dev-shm-usage')
            opts.add_argument('--headless=new')  # Use modern stealth headless mode
            opts.add_argument('--window-size=1920,1080')
            opts.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.101 Safari/537.36')
            opts.add_argument('--lang=en-US,en;q=0.9')
            opts.add_argument(f'--user-data-dir={BROWSER_DATA_DIR}')
            return opts

        chrome_version = get_chrome_version_main()

        # Attempt 1: Try with dynamically detected version
        if chrome_version:
            log.info("Detected local Chrome major version: %s. Initiating driver...", chrome_version)
            try:
                self._driver = uc.Chrome(options=create_options(), version_main=chrome_version)
                log.info("Undetected-chromedriver started successfully (using version %s)", chrome_version)
                return
            except Exception as e:
                log.warning("Failed to start with detected version %s (%s). Trying auto-detection...", chrome_version, e)

        # Attempt 2: Try without version_main parameter (let uc auto-detect)
        try:
            self._driver = uc.Chrome(options=create_options())
            log.info("Undetected-chromedriver started successfully (auto-detected)")
            return
        except Exception as e:
            log.warning("Failed to start chrome without version_main (%s). Trying fallback with version_main=147...", e)

        # Attempt 3: Try with hardcoded fallback (version 147)
        try:
            self._driver = uc.Chrome(options=create_options(), version_main=147)
            log.info("Undetected-chromedriver started successfully (fallback version 147)")
        except Exception as e:
            log.error("All driver start attempts failed: %s", e)
            raise e

    def stop_driver(self):
        """Stops undetected-chromedriver synchronously."""
        if self._driver:
            try:
                self._driver.quit()
                log.info("Chrome driver stopped")
            except Exception as e:
                log.error("Error stopping Chrome driver: %s", e)
            finally:
                self._driver = None

    async def start(self):
        """Asynchronously initialize the driver using the thread executor."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.start_driver)

    async def stop(self):
        """Asynchronously stop the driver."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.stop_driver)

    def _ensure_driver_alive(self):
        """Helper to verify driver is responsive, restart if crashed."""
        if not self._driver:
            self.start_driver()
            return

        try:
            # Try a lightweight call to check if driver is still responsive
            _ = self._driver.title
        except Exception:
            log.warning("Driver unresponsive, restarting...")
            self.stop_driver()
            self.start_driver()

    async def ask(self, prompt: str) -> str:
        """
        Send prompt to ChatGPT and return response.
        Runs inside an asyncio.Lock to prevent concurrent access.
        """
        async with self._lock:
            loop = asyncio.get_running_loop()
            last_error = None
            
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    # Run the synchronous automation steps in the thread pool
                    res = await loop.run_in_executor(None, self._do_ask_sync, prompt, attempt)
                    return res
                except Exception as e:
                    last_error = e
                    log.warning("Attempt %d failed: %s", attempt, e)
                    # Restart driver on failure to ensure clean state for retry
                    await loop.run_in_executor(None, self.stop_driver)
                    if attempt < MAX_RETRIES:
                        wait_time = random.uniform(3, 6)
                        log.info("Waiting %.1f seconds before retry...", wait_time)
                        await asyncio.sleep(wait_time)

            raise RuntimeError(f"All {MAX_RETRIES} attempts failed. Last error: {last_error}")

    def _do_ask_sync(self, prompt: str, attempt: int) -> str:
        """Synchronous automation sequence (optimized for speed and reliability)."""
        self._ensure_driver_alive()
        driver = self._driver

        log.info("[Attempt %d] Navigating to ChatGPT...", attempt)
        driver.get(CHATGPT_URL)

        # Wait dynamically for prompt input textarea to be clickable
        log.info("Waiting for prompt input...")
        textarea = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "prompt-textarea"))
        )

        # Type the prompt
        log.info("Typing prompt...")
        textarea.send_keys(prompt)
        time.sleep(0.1)

        # Locate send button
        log.info("Locating send button...")
        send_btn = None
        for selector in [
            'button[data-testid="send-button"]',
            'button[aria-label="Send prompt"]',
            '#composer-submit-button',
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_displayed() and btn.is_enabled():
                    send_btn = btn
                    log.info("Found send button with selector: %s", selector)
                    break
            except Exception:
                continue

        if send_btn:
            log.info("Clicking send button...")
            send_btn.click()
        else:
            log.info("Send button not found or not clickable, pressing Enter...")
            from selenium.webdriver.common.keys import Keys
            textarea.send_keys(Keys.ENTER)

        # Wait for generation to start and then complete
        log.info("Waiting for response to generate...")
        start_time = time.time()
        last_text = ""
        stable_count = 0
        STABLE_REQUIRED = 3

        # Wait for the send button to become disabled/hidden (meaning click registered & generating started)
        try:
            WebDriverWait(driver, 4).until_not(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="send-button"]'))
            )
        except Exception:
            log.warning("Send button did not transition to disabled state within 4s")

        while time.time() - start_time < MAX_WAIT_SECONDS:
            time.sleep(0.1)  # Ultra-fast poll interval (100ms)

            # Check if assistant element has appeared and extract text
            try:
                responses = driver.find_elements(By.CSS_SELECTOR, '[data-message-author-role="assistant"]')
                if responses:
                    current_text = responses[-1].text.strip()
                else:
                    current_text = ""
            except (NoSuchElementException, StaleElementReferenceException):
                current_text = ""
            except Exception as e:
                log.error("Critical error while retrieving assistant text: %s", e)
                raise

            # Check if the send button has returned (meaning generating stopped)
            has_send_button = False
            try:
                btn = driver.find_element(By.CSS_SELECTOR, 'button[data-testid="send-button"]')
                if btn.is_displayed() and btn.is_enabled():
                    has_send_button = True
            except (NoSuchElementException, StaleElementReferenceException):
                pass
            except Exception as e:
                log.error("Critical error while checking send button: %s", e)
                raise

            # If send button has returned and we have non-empty text, finish immediately!
            if has_send_button and current_text:
                log.info("Generation finished (send button returned with text)")
                return current_text

            # Stability fallback check for text content (fallback if send button doesn't update)
            if current_text and current_text == last_text and current_text.strip() != "Thinking":
                stable_count += 1
                if stable_count >= 80:  # 8 seconds of stillness
                    log.info("Generation finished (text stable fallback)")
                    return current_text
            else:
                stable_count = 0

            last_text = current_text

        if last_text:
            return last_text
            
        raise RuntimeError("Timed out waiting for ChatGPT response")

# --- Global browser manager --------------------------------------------------
browser_mgr = BrowserManager()


# --- FastAPI App --------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start browser on startup, close on shutdown."""
    await browser_mgr.start()
    yield
    await browser_mgr.stop()


app = FastAPI(
    title="ChatGPT Free API",
    description="A REST API wrapper around the free ChatGPT web interface using undetected-chromedriver.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request / Response Models ------------------------------------------------
class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000, description="The prompt to send to ChatGPT")


class ChatResponse(BaseModel):
    id: str = Field(description="Unique response ID")
    response: str = Field(description="ChatGPT's response text")
    model: str = Field(default="chatgpt-free", description="Model identifier")
    prompt: str = Field(description="The original prompt")
    elapsed_seconds: float = Field(description="Time taken to get the response")


# --- Endpoints ----------------------------------------------------------------
@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "running",
        "service": "ChatGPT Free API",
        "version": "1.0.0",
        "endpoints": {
            "POST /chat": "Send a prompt and get a response",
            "GET /health": "Health check",
        },
    }


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a prompt to ChatGPT and get the response.
    """
    start_time = time.time()

    try:
        response_text = await browser_mgr.ask(request.prompt)
    except Exception as e:
        log.error("Failed to get response: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get response from ChatGPT: {str(e)}",
        )

    elapsed = round(time.time() - start_time, 2)

    return ChatResponse(
        id=str(uuid.uuid4()),
        response=response_text,
        model="chatgpt-free",
        prompt=request.prompt,
        elapsed_seconds=elapsed,
    )


# --- Entry Point --------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "chatgpt_api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
