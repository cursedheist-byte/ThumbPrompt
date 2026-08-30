"""
Flask entrypoint. Routes are intentionally thin: they parse the request,
delegate to services/, and shape the JSON response. All business logic
(usage limits, AI calls, validation, payments) lives in services/ and
ai/.

Google Sign-In: server-side OAuth 2.0 via Authlib. session["user_id"]
is only ever set from a value we generated ourselves (either straight
from Google's OAuth callback via database.get_or_create_google_user,
or - previously - an anonymous session). It is never read from a
client-supplied query/form parameter, so a request cannot select
another account by passing a user_id.

Payments: real Razorpay integration (see services/payment_service.py).
Every payment endpoint requires the existing Google-authenticated
session and always acts on the session's user - there is no path for a
request to create an order or apply credits for a different user_id.
"""

import logging
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from ai.gemini_client import GeminiClientError, GeminiNotConfiguredError
from ai.prompt_engine import PromptEngineError
from config import Config
from database import database
from services import payment_service, thumbnail_service, usage_service
from services.payment_service import PaymentServiceError
from services.thumbnail_service import ThumbnailServiceError
from services.usage_service import UsageLimitExceededError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

database.init_db()

# ---------------------------------------------------------------------
# Google OAuth client
# ---------------------------------------------------------------------
# Registered even if credentials are blank so imports/route registration
# never fail at boot; /auth/google checks Config.google_oauth_configured()
# and fails fast with a clear error instead of a confusing Google-side one.
oauth = OAuth(app)
google_oauth = oauth.register(
    name="google",
    client_id=Config.GOOGLE_CLIENT_ID,
    client_secret=Config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ---------------------------------------------------------------------
# Session / user helpers
# ---------------------------------------------------------------------

def _current_user():
    """Returns the signed-in user, or None if no one is authenticated.

    Uses database.get_user_enforcing_expiry() rather than a plain
    get_user() lookup, so that every authenticated request first
    lazily reverts an expired paid plan back to Free server-side -
    this is the single enforcement point for plan expiry across the
    whole app (usage checks, the usage pill, ads_enabled, top-up
    eligibility all flow from whatever this returns).
    """
    user_id = session.get("user_id")
    if not user_id:
        return None
    return database.get_user_enforcing_expiry(user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_user() is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------

@app.route("/")
def index():
    user = _current_user()
    if not user:
        return render_template(
            "index.html",
            logged_in=False,
            google_oauth_configured=Config.google_oauth_configured(),
            auth_error=request.args.get("error"),
        )
    return render_template(
        "index.html",
        logged_in=True,
        user=user,
        categories=Config.CATEGORIES,
        usage=usage_service.get_usage_summary(user),
        plans=Config.PLANS,
        topup=Config.TOPUP,
    )


# ---------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------

@app.route("/auth/google")
def auth_google():
    if not Config.google_oauth_configured():
        logger.error("Google OAuth requested but GOOGLE_CLIENT_ID/SECRET are not set.")
        return redirect(url_for("index", error="google_not_configured"))
    # Authlib stores a per-request 'state' value in the session and
    # verifies it on callback (CSRF protection for the OAuth flow).
    return google_oauth.authorize_redirect(Config.GOOGLE_REDIRECT_URI)


@app.route("/auth/google/callback")
def auth_google_callback():
    try:
        token = google_oauth.authorize_access_token()
    except Exception as exc:  # noqa: BLE001 - surface any OAuth failure the same way
        logger.warning("Google OAuth callback failed: %s", exc)
        return redirect(url_for("index", error="google_auth_failed"))

    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = google_oauth.userinfo(token=token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fetching Google userinfo failed: %s", exc)
            return redirect(url_for("index", error="google_auth_failed"))

    google_sub = userinfo.get("sub")
    if not google_sub:
        logger.warning("Google OAuth callback response missing 'sub'.")
        return redirect(url_for("index", error="google_auth_failed"))

    user = database.get_or_create_google_user(
        google_sub,
        email=userinfo.get("email"),
        name=userinfo.get("name"),
        picture_url=userinfo.get("picture"),
    )
    session["user_id"] = user["id"]
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------

@app.route("/api/usage", methods=["GET"])
@login_required
def api_usage():
    user = _current_user()
    return jsonify({"ok": True, "usage": usage_service.get_usage_summary(user)})


@app.route("/api/create-order", methods=["POST"])
@login_required
def api_create_order():
    """Creates a Razorpay order for a plan purchase or a top-up. The
    server decides the price/credits from config.Config in both cases -
    the client only picks WHICH plan or HOW MANY top-up units; it can
    never influence the amount charged (see services/payment_service.py)."""
    user = _current_user()
    payload = request.get_json(silent=True) or {}
    order_kind = payload.get("type")

    try:
        if order_kind == "plan":
            order = payment_service.create_plan_order(user, payload.get("plan"))
        elif order_kind == "topup":
            order = payment_service.create_topup_order(user, payload.get("quantity", 1))
        else:
            return jsonify({"ok": False, "error": "Invalid order type."}), 400
    except PaymentServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "order": order})


@app.route("/api/payment/verify", methods=["POST"])
@login_required
def api_verify_payment():
    """Verifies a completed Razorpay Checkout payment server-side and,
    only on success, grants the associated plan/top-up credits. Never
    trusts a client-sent "payment successful" status on its own - the
    Razorpay signature is what's actually checked."""
    user = _current_user()
    payload = request.get_json(silent=True) or {}

    try:
        updated_user, already_processed = payment_service.verify_and_apply(
            user,
            razorpay_order_id=payload.get("razorpay_order_id"),
            razorpay_payment_id=payload.get("razorpay_payment_id"),
            razorpay_signature=payload.get("razorpay_signature"),
        )
    except PaymentServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "already_processed": already_processed,
            "usage": usage_service.get_usage_summary(updated_user),
        }
    )


@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    user = _current_user()

    try:
        usage_service.ensure_has_usage(user)
    except UsageLimitExceededError:
        return jsonify(
            {
                "ok": False,
                "error": "You've used all the prompts on your current plan. "
                "Upgrade your plan to keep generating thumbnail concepts.",
                "usage": usage_service.get_usage_summary(user),
            }
        ), 402

    try:
        result = thumbnail_service.start_analysis(
            user_id=user["id"], form=request.form, files=request.files
        )
    except ThumbnailServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except GeminiNotConfiguredError as exc:
        logger.error("Gemini not configured: %s", exc)
        return jsonify(
            {"ok": False, "error": f"AI provider is not configured: {exc}"}
        ), 503
    except PromptEngineError as exc:
        logger.error("Concept generation failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "The AI could not generate thumbnail concepts right "
             "now. Please try again in a moment."}
        ), 502

    if Config.USAGE_CHARGE_ON_ANALYZE:
        try:
            usage_service.charge_usage(user["id"], result["generation_id"], action="analyze")
        except UsageLimitExceededError:
            return jsonify(
                {"ok": False, "error": "Usage limit reached.", "usage": usage_service.get_usage_summary(user)}
            ), 402

    fresh_user = database.get_user(user["id"])
    return jsonify(
        {
            "ok": True,
            "generation_id": result["generation_id"],
            "video_understanding": result["video_understanding"],
            "concepts": result["concepts"],
            "usage": usage_service.get_usage_summary(fresh_user),
        }
    )


@app.route("/api/finalize", methods=["POST"])
@login_required
def api_finalize():
    user = _current_user()

    try:
        usage_service.ensure_has_usage(user)
    except UsageLimitExceededError:
        return jsonify(
            {
                "ok": False,
                "error": "You've used all the prompts on your current plan. "
                "Upgrade your plan to generate the final prompt.",
                "usage": usage_service.get_usage_summary(user),
            }
        ), 402

    generation_id = request.form.get("generation_id")
    concept_index_raw = request.form.get("concept_index")

    if not generation_id or concept_index_raw is None:
        return jsonify({"ok": False, "error": "Missing generation_id or concept_index."}), 400

    try:
        concept_index = int(concept_index_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "concept_index must be a number."}), 400

    try:
        result = thumbnail_service.finalize_prompt(
            user_id=user["id"],
            generation_id=generation_id,
            concept_index=concept_index,
            files=request.files,
        )
    except ThumbnailServiceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except GeminiNotConfiguredError as exc:
        logger.error("Gemini not configured: %s", exc)
        return jsonify(
            {"ok": False, "error": f"AI provider is not configured: {exc}"}
        ), 503
    except PromptEngineError as exc:
        logger.error("Final prompt generation failed: %s", exc)
        return jsonify(
            {"ok": False, "error": "The AI could not generate the final prompt right "
             "now. Please try again."}
        ), 502

    if Config.USAGE_CHARGE_ON_FINALIZE:
        try:
            usage_service.charge_usage(user["id"], generation_id, action="finalize")
        except UsageLimitExceededError:
            return jsonify(
                {"ok": False, "error": "Usage limit reached.", "usage": usage_service.get_usage_summary(user)}
            ), 402

    fresh_user = database.get_user(user["id"])
    return jsonify(
        {
            "ok": True,
            "final_image_prompt": result["final_image_prompt"],
            "structured_breakdown": result["structured_breakdown"],
            "usage": usage_service.get_usage_summary(fresh_user),
        }
    )


# ---------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------

@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"ok": False, "error": "Upload too large. Please use smaller images."}), 413


@app.errorhandler(500)
def server_error(exc):
    logger.exception("Unhandled server error: %s", exc)
    return jsonify({"ok": False, "error": "An unexpected server error occurred."}), 500


if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
