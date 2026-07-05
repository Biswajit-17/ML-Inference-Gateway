# Phase 6 — Interactive Frontend & Security Hardening Design & Roadmap

This document outlines the step-by-step implementation plan for serving a stunning, single-page web application directly from the gateway, implementing automatic documentation hiding in production, and hardening client-side input validations.

---

## 📅 Implementation Steps

### Step 1: Implement Swagger & ReDoc Auto-Hiding (Phase 6.1)

Harden the API by disabling interactive documentation pages (Swagger UI and ReDoc) when running in production to prevent schema disclosure to potential attackers.

*   **Logic:** Read `ENV` from the environment variables (loaded from `.env`).
*   **FastAPI Initialization:** Configure `FastAPI` instance properties conditionally:
    ```python
    app = FastAPI(
        title="HarvestGate ML Inference Gateway",
        docs_url="/docs" if ENV == "development" else None,
        redoc_url="/redoc" if ENV == "development" else None,
        openapi_url="/openapi.json" if ENV == "development" else None,
    )
    ```

---

### Step 2: Create a Single-Page Web Frontend (Phase 6.2)

Build a beautiful, interactive web interface using modern, premium styling that allows regular users to interact with the models easily.

*   **Location:** Create `gateway/static/index.html` and `gateway/static/index.css`.
*   **Aesthetics (Dark-Mode & Glassmorphism):**
    *   Curated color palette (rich indigo and deep slate background, vibrant emerald/blue accents).
    *   Glassmorphic styling with semi-transparent frosted cards, refined borders, and subtle drop-shadows.
    *   Interactive input sliders with live value indicators for soil elements (N, P, K) and rainfall.
    *   Dropdown pickers mapping Indian states and crops.
*   **User Interface Sections:**
    *   **Inference Request Form:** Group parameters cleanly with a toggle switch for "Request Agronomic Advisory (LLM)".
    *   **Results Area:** Cards showing predicted yield and relative suitability percentage, and a styled markdown block showing the LLM agronomic explanation.
    *   **Interactive State Indicators:** Spinner/loader animations while awaiting backend response.

---

### Step 3: Implement Client-Side Security & Rate-Limit Handling (Phase 6.3)

Secure the client interface by sanitizing user interactions and handling API status codes gracefully.

*   **Strict Input Mapping:** Constrain input ranges inside slider attributes (`min`, `max`, `step`) to match the gateway Pydantic schema constraints exactly (preventing out-of-bounds error responses).
*   **XSS Protection:** Render the LLM advisory response string using JS `textContent` mapping rather than `innerHTML` to prevent script execution of unsanitized remote strings.
*   **Rate-Limit (429) Handling:** Check response headers and status codes. If a `429 Too Many Requests` is returned (triggered by SlowAPI):
    *   Display a friendly warning overlay blocking the form.
    *   Implement a countdown timer showing the user when they can submit their next request.

---

### Step 4: Mount Static Directories (Phase 6.4)

Update the router configuration inside [gateway/main.py](file:///c:/Users/Biswajitrk/Documents/COdezzz/HarvestGate%20-%20ML%20Inference%20Gateway/gateway/main.py) to mount the static assets path.

*   **Static Mounting:** Mount the static folder using `fastapi.staticfiles.StaticFiles`.
*   **Root Handler:** Serve the `index.html` file on the root index endpoint `GET /`.
    ```python
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app.mount("/static", StaticFiles(directory="gateway/static"), name="static")

    @app.get("/")
    async def serve_frontend():
        return FileResponse("gateway/static/index.html")
    ```

---

## 🔒 Phase 6 Security Hardening Matrix

| Security Rule | Risk Addressed | Implementation Detail |
|---|---|---|
| **Docs Hiding (`docs_url=None`)** | API contract exposure & enumeration | Auto-disables `/docs`, `/redoc`, and `/openapi.json` when `ENV=production`. |
| **XSS Sanitization (`textContent`)** | Cross-Site Scripting (HTML injection) | Inserts LLM response directly into DOM nodes as plain text, preventing execution of injected script tags. |
| **Input Clipping** | Server resource waste / HTTP 422 | HTML5 slider attributes block submission of out-of-range numeric inputs. |
| **429 Feedback Loop** | API spamming and script loop abuse | Client UI disables input controls and shows countdown overlays upon rate-limit triggers. |
