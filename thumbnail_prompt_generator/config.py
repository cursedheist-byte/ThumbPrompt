"""
Central configuration for the Thumbnail Prompt Generator.

Everything that a future developer would want to tune without touching
business logic lives here: usage plans, AI model settings, upload rules,
where usage is charged in the generation flow, Google OAuth credentials
for sign-in, and (now) Razorpay credentials + plan/top-up pricing for
real payments.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # ------------------------------------------------------------------
    # Flask / general
    # ------------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", 5000))

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_PATH = os.environ.get("DATABASE_PATH") or str(
        BASE_DIR / "database" / "app.db"
    )

    # ------------------------------------------------------------------
    # Gemini / Gemma AI provider
    # ------------------------------------------------------------------
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

    # NOTE ON MODEL NAME:
    # The spec requested "Gemma 4 31B". Google does not publish a model
    # under that exact name. As of mid-2026 the Gemini API serves the
    # Gemma 4 family (multimodal, text+image in) under IDs such as
    # "gemma-4-26b-a4b-it" (the closest published size to "31B") and
    # smaller "gemma-4-*-it" variants. This is set from an env var so it
    # can be swapped for any valid Gemini/Gemma model id without touching
    # code. Check https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api
    # for the current list before deploying.
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemma-4-26b-a4b-it")

    # Generation tuning
    GEMINI_TEMPERATURE_CONCEPTS = float(
        os.environ.get("GEMINI_TEMPERATURE_CONCEPTS", 0.9)
    )
    GEMINI_TEMPERATURE_FINAL = float(
        os.environ.get("GEMINI_TEMPERATURE_FINAL", 0.6)
    )
    GEMINI_MAX_OUTPUT_TOKENS = int(
        os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", 4096)
    )
    GEMINI_TIMEOUT_SECONDS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", 45))
    GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", 1))

    # ------------------------------------------------------------------
    # Google OAuth 2.0 (sign-in)
    # ------------------------------------------------------------------
    # Never hardcode these. Populate them via environment variables /
    # .env. GOOGLE_REDIRECT_URI must match, byte-for-byte, an
    # "Authorized redirect URI" configured on the OAuth client in
    # Google Cloud Console.
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.environ.get(
        "GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/auth/google/callback"
    )

    @classmethod
    def google_oauth_configured(cls):
        return bool(cls.GOOGLE_CLIENT_ID and cls.GOOGLE_CLIENT_SECRET)

    # ------------------------------------------------------------------
    # Razorpay (payments)
    # ------------------------------------------------------------------
    # Never hardcode these; read from environment / .env only.
    # RAZORPAY_KEY_ID is safe to hand to the frontend for Checkout.
    # RAZORPAY_KEY_SECRET must never leave the server.
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

    @classmethod
    def razorpay_configured(cls):
        return bool(cls.RAZORPAY_KEY_ID and cls.RAZORPAY_KEY_SECRET)

    # ------------------------------------------------------------------
    # Ads
    # ------------------------------------------------------------------
    # WHO sees an ad slot is never decided here - that's
    # PLANS[<plan>]["ads_enabled"] below, resolved server-side per user
    # in services/usage_service.get_usage_summary() and enforced via
    # database.get_user_enforcing_expiry() (an expired paid plan reverts
    # to Free, which flips ads_enabled back to True automatically).
    #
    # This section is only for the ad *unit* itself. Right now the
    # frontend renders a plain "ADVERTISEMENT" placeholder for local
    # development. Once this site is deployed and approved for Google
    # AdSense, set these from your AdSense dashboard (never hardcode a
    # real value here) and swap the placeholder <div id="analyzeAdSlot">
    # / <div id="finalizeAdSlot"> markup in templates/index.html for a
    # real <ins class="adsbygoogle" data-ad-client="..." data-ad-slot="...">
    # unit (values below) - the show/hide logic in app.js
    # (updateProcessingAdSlots) does not need to change.
    ADSENSE_CLIENT_ID = os.environ.get("ADSENSE_CLIENT_ID", "")
    ADSENSE_SLOT_ANALYZE_LOADING = os.environ.get("ADSENSE_SLOT_ANALYZE_LOADING", "")
    ADSENSE_SLOT_FINALIZE_LOADING = os.environ.get("ADSENSE_SLOT_FINALIZE_LOADING", "")

    @classmethod
    def adsense_configured(cls):
        return bool(cls.ADSENSE_CLIENT_ID)

    # ------------------------------------------------------------------
    # Usage / pricing plans
    # ------------------------------------------------------------------
    # Real, one-time (non-subscription) plan purchases. "prompt_limit" is
    # the number of generations granted on purchase (used as an absolute
    # allowance, not additive, on plan change/purchase). "validity_months"
    # is None for Free (never expires); paid plans expire that many real
    # calendar months after purchase (see services/payment_service.py -
    # month arithmetic is calendar-accurate, not a fixed day count).
    PLANS = {
        "free": {
            "label": "Free",
            "price_inr": 0,
            "prompt_limit": 3,
            "validity_months": None,
            "ads_enabled": True,
        },
        "starter": {
            "label": "Starter",
            "price_inr": 9,
            "prompt_limit": 15,
            "validity_months": 1,
            "ads_enabled": False,
        },
        "creator": {
            "label": "Creator",
            "price_inr": 49,
            "prompt_limit": 80,
            "validity_months": 3,
            "ads_enabled": False,
        },
        "power": {
            "label": "Power",
            "price_inr": 99,
            "prompt_limit": 200,
            "validity_months": 6,
            "ads_enabled": False,
        },
    }
    DEFAULT_PLAN = "free"

    # Top-up: ₹1 per extra generation, only purchasable while a paid plan
    # is active; expires together with that plan (see payment_service).
    TOPUP = {
        "price_inr": 1,
        "generations_per_unit": 1,
        "max_quantity": 100,  # sanity cap on a single top-up purchase
    }

    # Where, in the two-call AI flow, usage is decremented.
    # - Concept analysis is free to encourage exploration, but still
    #   requires prompt_remaining > 0 to prevent abuse by users with 0
    #   allowance.
    # - The final master prompt is the paid deliverable. Regenerating it
    #   (a fresh AI call) also charges, matching "regeneration must not
    #   bypass usage".
    USAGE_CHARGE_ON_ANALYZE = False
    USAGE_CHARGE_ON_FINALIZE = True

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or str(BASE_DIR / "uploads")
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    MAX_IMAGE_SIZE_BYTES = int(
        os.environ.get("MAX_IMAGE_SIZE_BYTES", 6 * 1024 * 1024)  # 6 MB
    )
    # Flask-level hard cap on the whole request (covers up to 3 images).
    MAX_CONTENT_LENGTH = int(
        os.environ.get("MAX_CONTENT_LENGTH", 20 * 1024 * 1024)  # 20 MB
    )
    # How long uploaded reference images are kept on disk before a cleanup
    # pass removes them. We do not need them once a generation is done.
    UPLOAD_RETENTION_MINUTES = int(
        os.environ.get("UPLOAD_RETENTION_MINUTES", 60)
    )

    # ------------------------------------------------------------------
    # Video categories -> dynamic step config for the frontend
    # ------------------------------------------------------------------
    CATEGORIES = [
        {"id": "gaming", "label": "Gaming"},
        {"id": "documentary", "label": "Documentary"},
        {"id": "vlog", "label": "Vlog"},
        {"id": "challenge", "label": "Challenge"},
        {"id": "entertainment", "label": "Entertainment"},
        {"id": "tech", "label": "Tech"},
        {"id": "educational", "label": "Educational"},
    ]
