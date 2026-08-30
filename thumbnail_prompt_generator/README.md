# ThumbPrompt — AI YouTube Thumbnail Prompt Generator

Turns a video idea into 3 original thumbnail strategy concepts, then a
production-ready image-generation prompt you paste into ChatGPT,
Midjourney, or any other image generator. **This app does not generate
images itself** — it generates the *prompt* you use elsewhere.

Flask + SQLite + vanilla HTML/CSS/JS. No React, no Node.js, no npm.

---

## 1. What this actually does

```
User answers a short wizard (category → details → title → references)
        │
        ▼
  Gemini call #1  →  3 original thumbnail concepts (JSON)
        │
   user picks one
        │
        ▼
  Gemini call #2  →  1 final master image-generation prompt (JSON)
        │
        ▼
  User copies the prompt into an image generator
```

Only **two** AI calls happen per full run, no matter how much the user
clicks around the wizard (see "Cost control" below).

---

## 2. Project structure

```
thumbnail_prompt_generator/
├── app.py                      Flask routes only — no business logic
├── config.py                   All tunables: plans, model name, upload limits
├── requirements.txt
├── .env.example
│
├── database/
│   └── database.py             SQLite schema + queries (users, usage_log, generations)
│
├── ai/
│   ├── gemini_client.py        The ONLY file that imports google.generativeai
│   ├── prompt_engine.py        Builds the 2 prompts, validates AI responses
│   └── schemas.py              Expected JSON shapes + validators
│
├── research/
│   ├── base.py                 Abstract interface for a future live-research module
│   └── provider.py             Offline provider shipping generalized design patterns
│
├── services/
│   ├── thumbnail_service.py    Orchestrates the whole workflow
│   ├── usage_service.py        Plan/usage-limit logic
│   └── upload_service.py       Upload validation + temp file handling
│
├── prompts/
│   └── thumbnail_system_prompt.py   The system prompt (thumbnail strategist role)
│
├── templates/index.html        The wizard UI (Jinja2)
├── static/css/style.css
├── static/js/app.js            Wizard state machine + fetch() calls
└── uploads/                    Temp image storage (auto-cleaned)
```

Nothing calls Gemini except `ai/gemini_client.py`. Nothing touches SQL
except `database/database.py`. Routes in `app.py` only call `services/`.
This is deliberate so any piece (AI provider, DB, research layer) can be
swapped independently.

---

## 3. Local setup

**Requirements:** Python 3.12+ (3.9+ will also work).

```bash
# 1. Move into the project folder
cd thumbnail_prompt_generator

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the env template and fill in your key
cp .env.example .env
```

Now open `.env` and set:

```
GEMINI_API_KEY=your_actual_key_here
```

Get a key from **Google AI Studio**: https://aistudio.google.com/app/apikey
The key is read server-side only (`config.py` → `Config.GEMINI_API_KEY`)
and is never sent to the browser.

```bash
# 5. Run it
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

The SQLite database file is created automatically on first run at
`database/app.db` (via `database.init_db()`, called once at import time
in `app.py`) — no manual migration step needed.

---

## 4. A note on the AI model name

The original spec asked for "Gemma 4 31B". Google does not publish a
model under that exact name. As of mid-2026, the Gemini API serves the
**Gemma 4** family (multimodal, text + image input) and the closest
published size to "31B" is the instruction-tuned **26B-A4B** variant, so
that's the default in `.env.example`:

```
GEMINI_MODEL=gemma-4-26b-a4b-it
```

`ai/gemini_client.py` reads this from `Config.GEMINI_MODEL`, so you can
point it at any valid Gemini/Gemma model id (e.g. a `gemini-*` model if
you'd rather use a Gemini model instead of an open Gemma checkpoint)
without touching any code. Check the current list at
https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api before
deploying, since available model ids do change.

---

## 5. Usage / pricing system (no real payments yet)

Plans and limits live in `config.py → Config.PLANS`:

| Plan    | Price | Prompts |
|---------|-------|---------|
| Free    | ₹0    | 5       |
| Starter | ₹9    | 25      |
| Creator | ₹49   | 75      |
| Power   | ₹99   | 200     |

- No real payment processing is implemented. `POST /api/plan` is a demo
  endpoint that instantly switches a session's plan and tops up its
  allowance — replace its internals in `services/usage_service.py` /
  `database.set_plan()` once you wire up a real payment provider.
- Users are anonymous, tracked via a server-side session cookie mapped
  to a `users` row in SQLite (`database.get_or_create_user`). Swap in
  real auth later without changing the usage-tracking logic.
- **What counts as a "prompt":** generating the *final* master image
  prompt is the paid action (`Config.USAGE_CHARGE_ON_FINALIZE = True`).
  The 3-concept exploration step is free by default
  (`Config.USAGE_CHARGE_ON_ANALYZE = False`) so users can browse ideas
  without burning their allowance — but it still requires at least 1
  prompt of remaining allowance to start, to prevent free-tier abuse.
  Regenerating the final prompt also charges, so regeneration can't
  bypass the limit. Both flags are one-line changes in `config.py` if
  you'd rather charge differently.
- Every usage-consuming event is logged to the `usage_log` table with a
  timestamp for auditing.

---

## 6. Cost control

- Exactly **one** Gemini call for the 3 concepts, and **one** Gemini
  call for the final prompt. Nothing else in the wizard (navigating
  steps, uploading files, switching plans) calls the AI.
- Prompts sent to Gemini are built compactly in
  `ai/prompt_engine.py::_describe_inputs()` — only the fields relevant
  to what the user actually filled in are included.
- The system prompt (`prompts/thumbnail_system_prompt.py`) is sent once
  per call via Gemini's `system_instruction`, not repeated in the user
  message.

---

## 7. Uploads & privacy

- Face photos, reference thumbnails, and "specific element" images are
  validated (type + size, see `services/upload_service.py`) then sent
  to Gemini as inline image data for that single request.
- The browser holds the actual `File` objects in memory during the
  wizard and re-attaches them on the `/api/finalize` call if the
  final-prompt step needs them again — the server does not persist
  uploaded images to disk by default.
- `services/upload_service.py` also exposes `save_temp_file()` and
  `cleanup_old_uploads()` (deletes anything in `uploads/` older than
  `UPLOAD_RETENTION_MINUTES`, default 60) in case a future flow needs
  on-disk persistence between requests.

---

## 8. Error handling

Handled explicitly, without crashing the server:
- Missing/invalid Gemini API key → `503` with a clear message.
- Gemini rate limit / quota / timeout → retried once, then a clean
  `502` if it still fails.
- Malformed JSON from the model → one automatic recovery attempt
  (strips markdown fences / extracts the first `{...}` block), then one
  full retry request if still invalid, before returning a user-facing
  error.
- Missing required fields (category, topic, title) → `400` with the
  specific field named.
- Oversized/invalid uploads → `400` (per-file) or `413` (whole request
  over `MAX_CONTENT_LENGTH`).
- Usage limit reached → `402` with the current usage summary attached
  so the frontend can show it.

---

## 9. What was and wasn't verified

Built and tested in a **network-isolated** environment, so the
following were verified directly:
- All Python files import and byte-compile cleanly.
- The full SQLite layer (users, usage decrement, generations) was
  exercised directly and behaves correctly, including the "usage
  reaches 0" case.
- The Flask app was actually run locally: every route
  (`/`, `/api/usage`, `/api/plan`, `/api/analyze`, `/api/finalize`)
  was hit with `curl`, including validation errors (missing title,
  invalid category, bad file extension, oversized upload → `413`,
  finalize with an unknown `generation_id`), and the "AI provider not
  configured" `503` path (since `google-generativeai` could not be
  installed offline).
- Templates render and both `static/css/style.css` and
  `static/js/app.js` are served correctly.

**Not verified** (requires network access this environment did not
have): actually installing `google-generativeai` from PyPI, and making
a real call to the Gemini API. The client code
(`ai/gemini_client.py`) is written against the documented
`google.generativeai` SDK surface (`configure`, `GenerativeModel`,
`generate_content`, `response_mime_type: "application/json"`,
multimodal parts as `{mime_type, data}` dicts), but you should do one
real end-to-end run with a valid `GEMINI_API_KEY` before shipping.

---

## 10. Known simplifications (MVP scope, by design)

- No real authentication — sessions are anonymous, cookie-based.
- No real payment processing — plan switching is a demo endpoint.
- The research module (`research/`) ships only generalized, offline
  design-pattern lists per category, not live web research — but it's
  built behind an abstract interface (`research/base.py`) specifically
  so a live provider can be swapped in later with no other code
  changes (see `research/provider.py::get_research_provider()`).
