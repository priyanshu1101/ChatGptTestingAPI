# ChatGPT Free API 🤖

A REST API wrapper around the **free ChatGPT web interface** using browser automation (`undetected-chromedriver` and Selenium).  
No API key needed. No login required. Bypasses Cloudflare security and runs fully in headless mode.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
```

## Run

```bash
python chatgpt_api.py
```

Server starts at **http://localhost:8000**

## API Endpoints

### `GET /` — Info
### `GET /health` — Health check
### `POST /chat` — Send a prompt

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the capital of France?"}'
```

**Response:**
```json
{
  "id": "uuid-here",
  "response": "The capital of France is Paris.",
  "model": "chatgpt-free",
  "prompt": "What is the capital of France?",
  "elapsed_seconds": 2.8
}
```

## Interactive Docs

Visit **http://localhost:8000/docs** for the Swagger UI.

## Features & Optimizations

- **Cloudflare Bypass**: Powered by `undetected-chromedriver` to prevent bot-detection triggers.
- **Stealth Headless Mode**: Runs in modern `--headless=new` mode with customized viewport, user-agent, and language headers to prevent fingerprint blocking.
- **Dynamic Response Wait**: Monitors the real DOM status and returns the output the instant ChatGPT's send button becomes enabled again (no artificial sleeps or delays).
- **Concurrency Serialization**: Serializes all ChatGPT browser calls through an `asyncio.Lock` to guarantee clean context states.
