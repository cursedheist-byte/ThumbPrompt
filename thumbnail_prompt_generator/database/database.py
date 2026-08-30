"""
SQLite persistence layer.

Kept deliberately simple (no ORM) so the whole data model is visible in
one file. Four tables:

- users             one row per account. Originally one row per
                     anonymous session; now also carries an optional
                     Google identity (google_sub/email/name/picture_url)
                     for signed-in users, and (now) plan_expires_at for
                     real, one-time paid-plan purchases. Anonymous rows
                     created before Google Sign-In was added simply have
                     NULL Google columns and keep working.
- usage_log         one row per usage-consuming event, for auditing
                     (includes generation charges as well as plan/top-up
                     purchases, tagged via the 'action' column).
- generations       stores the input + generated concepts for a
                     wizard run, so /api/finalize can look up context
                     by generation_id without re-sending everything
                     from the browser.
- payments          one row per Razorpay order, created up front and
                     transitioned to 'paid' only after server-side
                     signature verification. The unique index on
                     razorpay_order_id (and razorpay_payment_id) is what
                     makes credit-granting idempotent: a payment can only
                     ever be applied once, even if the client calls
                     /api/payment/verify twice for the same order.
"""

import calendar
import datetime
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT 'free',
    prompts_used INTEGER NOT NULL DEFAULT 0,
    prompts_remaining INTEGER NOT NULL DEFAULT 3,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    generation_id TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    input_json TEXT NOT NULL,
    concepts_json TEXT,
    final_prompt_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    razorpay_order_id TEXT NOT NULL,
    razorpay_payment_id TEXT,
    kind TEXT NOT NULL,               -- 'plan' or 'topup'
    plan TEXT,                        -- plan id, when kind='plan'
    topup_quantity INTEGER,           -- generation count, when kind='topup'
    amount_paise INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'created',  -- created | paid | failed
    generations_added INTEGER,
    created_at REAL NOT NULL,
    verified_at REAL,
    expires_at REAL,                  -- new plan expiry granted by this purchase (kind='plan' only)
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_order_id ON payments (razorpay_order_id);
-- Multiple rows can have a NULL razorpay_payment_id (before verification);
-- SQLite's unique index allows any number of NULLs through, so this still
-- prevents the same *verified* payment id from ever being applied twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_payment_id ON payments (razorpay_payment_id);
"""

# Columns added after the initial release. Applied as an idempotent
# migration in init_db() via ALTER TABLE, so existing databases (and
# existing users/usage_log/generations rows) are preserved untouched.
_USER_COLUMN_MIGRATIONS = {
    "google_sub": "ALTER TABLE users ADD COLUMN google_sub TEXT",
    "email": "ALTER TABLE users ADD COLUMN email TEXT",
    "name": "ALTER TABLE users ADD COLUMN name TEXT",
    "picture_url": "ALTER TABLE users ADD COLUMN picture_url TEXT",
    # NULL = no active paid plan (Free, or an expired plan that has
    # already been reverted). Set on verified plan purchase; cleared
    # when the plan lapses (see get_user_enforcing_expiry below).
    "plan_expires_at": "ALTER TABLE users ADD COLUMN plan_expires_at REAL",
}


def get_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


def _migrate_user_columns(cur):
    """Add any columns missing from an existing users table. Safe/
    idempotent: only ALTERs columns that don't exist yet, never touches
    existing rows."""
    cur.execute("PRAGMA table_info(users)")
    existing_columns = {row["name"] for row in cur.fetchall()}
    for column, ddl in _USER_COLUMN_MIGRATIONS.items():
        if column not in existing_columns:
            cur.execute(ddl)
    # A user may or may not have signed in with Google (google_sub is
    # NULL for anonymous rows), so this is a unique index rather than a
    # column-level UNIQUE constraint - SQLite allows multiple NULLs
    # through a unique index.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub "
        "ON users (google_sub)"
    )


def init_db():
    """Create tables if they don't exist, and migrate existing tables
    to the current column set. Safe to call on every boot."""
    with db_cursor() as cur:
        cur.executescript(SCHEMA)
        _migrate_user_columns(cur)


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

def create_user(plan=None):
    plan = plan or Config.DEFAULT_PLAN
    limit = Config.PLANS.get(plan, Config.PLANS[Config.DEFAULT_PLAN])["prompt_limit"]
    user_id = str(uuid.uuid4())
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, plan, prompts_used, prompts_remaining, created_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (user_id, plan, limit, time.time()),
        )
    return get_user(user_id)


def get_user(user_id):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_or_create_user(user_id):
    if user_id:
        user = get_user(user_id)
        if user:
            return user
    return create_user()


def get_user_enforcing_expiry(user_id):
    """Returns the user's current row, first lazily reverting an expired
    paid plan back to Free if needed.

    This is the server-side expiry enforcement point: every request that
    resolves "the current user" (see app.py's _current_user()) goes
    through here, so an expired plan can never keep granting paid
    benefits (ads_enabled, remaining paid/top-up credits) just because no
    background job has run yet. On expiry the account falls back to a
    fresh Free-plan state per product rules - any unused paid/top-up
    generations are not carried over.
    """
    user = get_user(user_id)
    if not user:
        return None

    expires_at = user.get("plan_expires_at")
    if user["plan"] != Config.DEFAULT_PLAN and expires_at and expires_at < time.time():
        free_limit = Config.PLANS[Config.DEFAULT_PLAN]["prompt_limit"]
        with db_cursor() as cur:
            cur.execute(
                "UPDATE users SET plan = ?, prompts_used = 0, prompts_remaining = ?, "
                "plan_expires_at = NULL WHERE id = ?",
                (Config.DEFAULT_PLAN, free_limit, user_id),
            )
        return get_user(user_id)
    return user


# ---------------------------------------------------------------------
# Google identity
# ---------------------------------------------------------------------

def get_user_by_google_sub(google_sub):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,))
        row = cur.fetchone()
    return dict(row) if row else None


def create_google_user(google_sub, email=None, name=None, picture_url=None, plan=None):
    plan = plan or Config.DEFAULT_PLAN
    limit = Config.PLANS.get(plan, Config.PLANS[Config.DEFAULT_PLAN])["prompt_limit"]
    user_id = str(uuid.uuid4())
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, plan, prompts_used, prompts_remaining, created_at, "
            "google_sub, email, name, picture_url) "
            "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
            (user_id, plan, limit, time.time(), google_sub, email, name, picture_url),
        )
    return get_user(user_id)


def update_google_profile(user_id, email=None, name=None, picture_url=None):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE users SET email = ?, name = ?, picture_url = ? WHERE id = ?",
            (email, name, picture_url, user_id),
        )
    return get_user(user_id)


def get_or_create_google_user(google_sub, email=None, name=None, picture_url=None):
    """Look up a user by their stable Google subject id, creating one
    on first login. On subsequent logins, refreshes the cached profile
    fields (name/email/picture can change on Google's side) without
    touching plan/usage. Existing usage stays attached to the same
    internal user id every time this Google account signs in."""
    user = get_user_by_google_sub(google_sub)
    if user:
        if (
            user.get("email") != email
            or user.get("name") != name
            or user.get("picture_url") != picture_url
        ):
            user = update_google_profile(
                user["id"], email=email, name=name, picture_url=picture_url
            )
        return user
    return create_google_user(google_sub, email=email, name=name, picture_url=picture_url)


def decrement_usage(user_id, generation_id=None, action="finalize"):
    """Atomically decrement prompts_remaining and log the event.

    Returns the updated user row, or None if the user had no usage left
    (caller must have already checked, but this is a safety net against
    race conditions).
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT prompts_remaining FROM users WHERE id = ?", (user_id,)
        )
        row = cur.fetchone()
        if not row or row["prompts_remaining"] <= 0:
            return None

        cur.execute(
            "UPDATE users SET prompts_used = prompts_used + 1, "
            "prompts_remaining = prompts_remaining - 1 WHERE id = ?",
            (user_id,),
        )
        cur.execute(
            "INSERT INTO usage_log (id, user_id, action, generation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, action, generation_id, time.time()),
        )
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        updated = cur.fetchone()
    return dict(updated)


# ---------------------------------------------------------------------
# Generations (wizard run state)
# ---------------------------------------------------------------------

def create_generation(user_id, input_data):
    generation_id = str(uuid.uuid4())
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO generations (id, user_id, input_json, status, created_at) "
            "VALUES (?, ?, ?, 'analyzing', ?)",
            (generation_id, user_id, json.dumps(input_data), time.time()),
        )
    return generation_id


def save_concepts(generation_id, concepts):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE generations SET concepts_json = ?, status = 'concepts_ready' "
            "WHERE id = ?",
            (json.dumps(concepts), generation_id),
        )


def save_final_prompt(generation_id, final_prompt):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE generations SET final_prompt_json = ?, status = 'finalized' "
            "WHERE id = ?",
            (json.dumps(final_prompt), generation_id),
        )


def get_generation(generation_id, user_id=None):
    with db_cursor() as cur:
        if user_id:
            cur.execute(
                "SELECT * FROM generations WHERE id = ? AND user_id = ?",
                (generation_id, user_id),
            )
        else:
            cur.execute("SELECT * FROM generations WHERE id = ?", (generation_id,))
        row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    data["input_json"] = json.loads(data["input_json"]) if data["input_json"] else None
    data["concepts_json"] = json.loads(data["concepts_json"]) if data["concepts_json"] else None
    data["final_prompt_json"] = (
        json.loads(data["final_prompt_json"]) if data["final_prompt_json"] else None
    )
    return data


# ---------------------------------------------------------------------
# Payments (Razorpay orders + idempotent credit granting)
# ---------------------------------------------------------------------

def add_months(base_ts, months):
    """Adds real calendar months to a unix timestamp (UTC), clamping the
    day to the last valid day of the target month (e.g. Jan 31 + 1 month
    -> Feb 28/29, not Mar 3). Deliberately not a fixed 30/31-day offset,
    per spec ("do not approximate months as a fixed number of days")."""
    dt = datetime.datetime.fromtimestamp(base_ts, tz=datetime.timezone.utc)
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    new_dt = dt.replace(year=year, month=month, day=day)
    return new_dt.timestamp()


def create_payment_order(
    user_id, razorpay_order_id, kind, amount_paise, currency, plan=None, topup_quantity=None
):
    """Records a Razorpay order as soon as it's created, in 'created'
    status. This row is what /api/payment/verify looks up and what makes
    verification idempotent - credits are only ever granted once, on the
    transition out of 'created'."""
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO payments (id, user_id, razorpay_order_id, kind, plan, "
            "topup_quantity, amount_paise, currency, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)",
            (
                str(uuid.uuid4()),
                user_id,
                razorpay_order_id,
                kind,
                plan,
                topup_quantity,
                amount_paise,
                currency,
                time.time(),
            ),
        )


def get_payment_by_order_id(razorpay_order_id):
    with db_cursor() as cur:
        cur.execute("SELECT * FROM payments WHERE razorpay_order_id = ?", (razorpay_order_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def mark_payment_failed(razorpay_order_id):
    """Only marks a still-'created' order as failed - never touches an
    order that has already been paid, so a late/duplicate failure signal
    can't undo a successful, already-credited payment."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE payments SET status = 'failed' WHERE razorpay_order_id = ? AND status = 'created'",
            (razorpay_order_id,),
        )


def apply_verified_plan_payment(razorpay_order_id, razorpay_payment_id, user_id, plan_id, credits, expires_at):
    """Atomically transitions the payment row created->paid and grants
    the plan's credits/expiry to the user, in one SQLite transaction.

    The `AND status != 'paid'` guard makes this idempotent: if this
    payment was already applied (e.g. the client retried /api/payment/
    verify, or two requests raced), rowcount is 0 and we grant nothing a
    second time.

    Returns (applied, user_row) where applied=False means "already
    processed, here's the current state" rather than "just processed".
    """
    with db_cursor() as cur:
        cur.execute(
            "UPDATE payments SET status = 'paid', razorpay_payment_id = ?, verified_at = ?, "
            "generations_added = ?, expires_at = ? WHERE razorpay_order_id = ? AND status != 'paid'",
            (razorpay_payment_id, time.time(), credits, expires_at, razorpay_order_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return False, dict(cur.fetchone())

        # One-time purchase, not a subscription: the new plan's allowance
        # replaces (not adds to) the current allowance.
        cur.execute(
            "UPDATE users SET plan = ?, prompts_used = 0, prompts_remaining = ?, "
            "plan_expires_at = ? WHERE id = ?",
            (plan_id, credits, expires_at, user_id),
        )
        cur.execute(
            "INSERT INTO usage_log (id, user_id, action, generation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, "plan_purchase", razorpay_order_id, time.time()),
        )
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return True, dict(cur.fetchone())


def apply_verified_topup_payment(razorpay_order_id, razorpay_payment_id, user_id, quantity):
    """Same idempotency pattern as apply_verified_plan_payment, but adds
    generations to the existing allowance rather than replacing it, and
    leaves plan/plan_expires_at untouched (a top-up expires together with
    the plan that was active when it was purchased)."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE payments SET status = 'paid', razorpay_payment_id = ?, verified_at = ?, "
            "generations_added = ? WHERE razorpay_order_id = ? AND status != 'paid'",
            (razorpay_payment_id, time.time(), quantity, razorpay_order_id),
        )
        if cur.rowcount == 0:
            cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return False, dict(cur.fetchone())

        cur.execute(
            "UPDATE users SET prompts_remaining = prompts_remaining + ? WHERE id = ?",
            (quantity, user_id),
        )
        cur.execute(
            "INSERT INTO usage_log (id, user_id, action, generation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, "topup_purchase", razorpay_order_id, time.time()),
        )
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return True, dict(cur.fetchone())
