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
import shutil
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
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

# Lock to prevent filesystem race conditions when launching Chrome instances concurrently
chrome_start_lock = asyncio.Lock()


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
class CaptchaOrStuckException(Exception):
    """Raised when the browser is stuck on a CAPTCHA or Cloudflare challenge."""
    pass


def start_driver_sync(profile_dir: str) -> uc.Chrome:
    """Starts an isolated undetected-chromedriver instance synchronously."""
    log.info("Starting isolated chrome driver with profile: %s", profile_dir)

    def create_options():
        opts = uc.ChromeOptions()
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--headless=new')  # Use modern stealth headless mode
        opts.add_argument('--window-size=1920,1080')
        opts.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.101 Safari/537.36')
        opts.add_argument('--lang=en-US,en;q=0.9')
        opts.add_argument(f'--user-data-dir={profile_dir}')
        return opts

    chrome_version = get_chrome_version_main()

    # Attempt 1: Try with dynamically detected version
    if chrome_version:
        log.info("Detected local Chrome major version: %s. Initiating driver...", chrome_version)
        try:
            return uc.Chrome(options=create_options(), version_main=chrome_version)
        except Exception as e:
            log.warning("Failed to start with detected version %s (%s). Trying auto-detection...", chrome_version, e)

    # Attempt 2: Try without version_main parameter (let uc auto-detect)
    try:
        return uc.Chrome(options=create_options())
    except Exception as e:
        log.warning("Failed to start chrome without version_main (%s). Trying fallback with version_main=147...", e)

    # Attempt 3: Try with hardcoded fallback (version 147)
    return uc.Chrome(options=create_options(), version_main=147)


def do_ask_sync_instance(driver: uc.Chrome, prompt: str) -> str:
    """Synchronous automation sequence for a single isolated browser instance."""
    log.info("Navigating to ChatGPT...")
    driver.get(CHATGPT_URL)

    # Wait dynamically for prompt input textarea to be clickable
    try:
        textarea = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.ID, "prompt-textarea"))
        )
    except Exception:
        raise CaptchaOrStuckException("Prompt textarea not found (possible CAPTCHA or Cloudflare block)")

    # Type the prompt
    log.info("Typing prompt...")
    textarea.send_keys(prompt)
    time.sleep(0.1)

    # Locate send button
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
                break
        except Exception:
            continue

    if send_btn:
        send_btn.click()
    else:
        from selenium.webdriver.common.keys import Keys
        textarea.send_keys(Keys.ENTER)

    # Wait for generation to start (up to 8 seconds).
    # If no assistant bubble or thinking indicator starts generating, raise CaptchaOrStuckException.
    log.info("Waiting for generation to start...")
    start_time = time.time()
    generation_started = False

    while time.time() - start_time < 8.0:
        try:
            # Check for Cloudflare Turnstile/CAPTCHA frames to abort immediately
            for cf_selector in ['iframe[src*="cloudflare"]', '#challenge-stage', '.cf-turnstile-wrapper']:
                elements = driver.find_elements(By.CSS_SELECTOR, cf_selector)
                if any(el.is_displayed() for el in elements):
                    raise CaptchaOrStuckException("Cloudflare/CAPTCHA challenge detected on page")

            responses = driver.find_elements(By.CSS_SELECTOR, '[data-message-author-role="assistant"]')
            if responses:
                txt = responses[-1].text.strip()
                if txt or "Thinking" in responses[-1].get_attribute("outerHTML") or "Thinking" in responses[-1].text:
                    generation_started = True
                    break
        except CaptchaOrStuckException:
            raise
        except Exception:
            pass
        time.sleep(0.2)

    if not generation_started:
        raise CaptchaOrStuckException("Generation failed to start within 8 seconds (possible CAPTCHA or stuck)")

    log.info("Generation started! Polling for completion...")
    start_time = time.time()
    last_text = ""
    stable_count = 0

    # Wait for the send button to become disabled/hidden initially
    try:
        WebDriverWait(driver, 2).until_not(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="send-button"]'))
        )
    except Exception:
        pass

    while time.time() - start_time < MAX_WAIT_SECONDS:
        time.sleep(0.2)  # Ultra-fast poll interval (200ms)

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

        # If send button has returned and we have non-empty text (not "Thinking"), finish immediately!
        if has_send_button and current_text and current_text != "Thinking":
            log.info("Generation finished (send button returned with text)")
            return current_text

        # Stability fallback check for text content (fallback if send button doesn't update)
        if current_text and current_text == last_text and current_text.strip() != "Thinking":
            stable_count += 1
            if stable_count >= 25:  # 5 seconds of stillness
                log.info("Generation finished (text stable fallback)")
                return current_text
        else:
            stable_count = 0

        last_text = current_text

    if last_text:
        return last_text
        
    raise RuntimeError("Timed out waiting for ChatGPT response")


async def ask_single_browser(prompt: str, instance_id: str) -> str:
    """Manages starting, executing, and cleaning up a single isolated browser task."""
    profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".chrome_profile_{instance_id}")
    os.makedirs(profile_dir, exist_ok=True)
    
    driver = None
    try:
        loop = asyncio.get_running_loop()
        # Acquire lock to start driver sequentially, preventing filesystem download race conditions
        async with chrome_start_lock:
            driver = await loop.run_in_executor(None, start_driver_sync, profile_dir)
            # Add a small delay to let browser port binding settle before the next instance starts
            await asyncio.sleep(0.5)

        # Execute the automation steps
        response_text = await loop.run_in_executor(None, do_ask_sync_instance, driver, prompt)
        return response_text
    finally:
        # Guarantee driver quit
        if driver:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, driver.quit)
                log.info("Closed Chrome driver instance %s", instance_id)
            except Exception as e:
                log.error("Error stopping Chrome driver instance %s: %s", instance_id, e)
        # Guarantee profile directory cleanup
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            log.info("Cleaned up profile directory for instance %s", instance_id)
        except Exception as e:
            log.error("Error cleaning up profile directory %s: %s", profile_dir, e)


class BrowserManager:
    """
    Manages isolated, on-demand Google Chrome instances running in parallel
    to handle requests concurrently and bypass CAPTCHAs.
    """

    def __init__(self):
        pass

    async def start(self):
        log.info("BrowserManager initialized in stateless parallel concurrency mode")

    async def stop(self):
        log.info("BrowserManager stopped")

    async def ask(self, prompt: str) -> str:
        """
        Launches parallel browser instances, races them, and returns
        the first one that successfully generates a response.
        """
        num_instances = 2  # Run 2 parallel races to optimize speed and resource usage
        errors = []

        for attempt in range(1, MAX_RETRIES + 1):
            log.info("[Attempt %d] Launching %d parallel browser race instances...", attempt, num_instances)
            
            # Generate unique instance IDs
            instance_ids = [f"{uuid.uuid4().hex}" for _ in range(num_instances)]
            
            # Create parallel tasks
            tasks = [
                asyncio.create_task(ask_single_browser(prompt, idx))
                for idx in instance_ids
            ]

            response_text = None

            # Process tasks as they complete
            for future in asyncio.as_completed(tasks):
                try:
                    res = await future
                    if res and not response_text:
                        response_text = res
                        log.info("Success! A browser instance won the race. Canceling remaining tasks...")
                        # Cancel all other tasks
                        for t in tasks:
                            if not t.done():
                                t.cancel()
                        break
                except Exception as e:
                    log.warning("A browser instance task failed: %s", e)
                    errors.append(e)

            # Wait for all tasks to clean up completely
            await asyncio.gather(*tasks, return_exceptions=True)

            if response_text:
                return response_text

            if attempt < MAX_RETRIES:
                wait_time = random.uniform(2.0, 4.0)
                log.info("All parallel browser instances failed on attempt %d. Retrying in %.1f seconds...", attempt, wait_time)
                await asyncio.sleep(wait_time)

        raise RuntimeError(f"All parallel browser attempts failed. Errors: {[str(err) for err in errors]}")

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
    prompt: str = Field(..., min_length=1, max_length=10000, description="The prompt to send")
    model: Optional[str] = Field("chatgpt-free", description="Model identifier: 'chatgpt-free', 'gemini-1.5-flash', etc.")
    gemini_key: Optional[str] = Field(None, description="Optional Gemini API key from the client")


class ChatResponse(BaseModel):
    id: str = Field(description="Unique response ID")
    response: str = Field(description="Response text")
    model: str = Field(default="chatgpt-free", description="Model identifier")
    prompt: str = Field(description="The original prompt")
    elapsed_seconds: float = Field(description="Time taken to get the response")


# --- Helper for Gemini API calls ---------------------------------------------
def call_gemini_sync(prompt: str, api_key: str, model_name: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError(f"Failed to parse response: {data}")


# --- Endpoints ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the Web UI chat interface."""
    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="index.html Web UI template not found")


@app.get("/info")
async def info():
    """Service metadata info endpoint."""
    return {
        "status": "running",
        "service": "ChatGPT Free API",
        "version": "1.0.0",
        "endpoints": {
            "POST /chat": "Send a prompt and get a response",
            "GET /health": "Health check",
            "GET /": "Serve the Web UI",
        },
    }


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a prompt to ChatGPT or Gemini and get the response.
    """
    start_time = time.time()
    selected_model = request.model or "chatgpt-free"

    try:
        if selected_model.startswith("gemini"):
            # Check for API key (prioritizing request payload then environment)
            api_key = request.gemini_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise HTTPException(
                    status_code=400,
                    detail="Gemini API Key is missing. Provide it in the UI settings or set the GEMINI_API_KEY environment variable."
                )
            
            # Map friendly names if necessary
            model_endpoint = selected_model
            if model_endpoint not in ["gemini-1.5-flash", "gemini-1.5-pro"]:
                model_endpoint = "gemini-1.5-flash"  # fallback
                
            loop = asyncio.get_running_loop()
            response_text = await loop.run_in_executor(None, call_gemini_sync, request.prompt, api_key, model_endpoint)
        else:
            response_text = await browser_mgr.ask(request.prompt)
    except HTTPException as he:
        raise he
    except Exception as e:
        log.error("Failed to get response for model %s: %s", selected_model, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get response from {selected_model}: {str(e)}",
        )

    elapsed = round(time.time() - start_time, 2)

    return ChatResponse(
        id=str(uuid.uuid4()),
        response=response_text,
        model=selected_model,
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
