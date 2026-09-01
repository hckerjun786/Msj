"""SJ Shop Telegram bot — WebApp shop + admin access control.

Admin Telegram chat id default: 5427735251 (override with ADMIN_TG_IDS env).

Commands (admin only):
  /grant <tg_id> [plan]     allow user to order
  /revoke <tg_id>           remove access
  /topup <tg_id> <amount>   add wallet balance
  /rate <rupees>            set per-order fee
  /users                    list recent users
  /whoami                   show your ids

Everyone:
  /start                    open shop WebApp
  /id                       show your Telegram id
"""

import json
import os
import time
import traceback

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "bot_config.json")
STATE_DIR = os.path.join(BASE_DIR, "state")
USERS_FILE = os.path.join(STATE_DIR, "users.json")
SETTINGS_FILE = os.path.join(STATE_DIR, "settings.json")


def load_cfg():
    cfg = {
        "token": "",
        "shop_url": "",
        "bot_username": "",
        "welcome_text": (
            "🛍️ <b>SJ Shop</b>\n\n"
            "Tap below to open the shop inside Telegram.\n"
            "New users need admin approval before ordering."
        ),
    }
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    cfg["token"] = os.environ.get("BOT_TOKEN", cfg.get("token", "")).strip()
    cfg["shop_url"] = os.environ.get(
        "SHOP_URL", os.environ.get("WEBAPP_URL", cfg.get("shop_url", ""))
    ).strip().rstrip("/")
    return cfg


CFG = load_cfg()
TOKEN = CFG["token"]
SHOP_URL = CFG["shop_url"]
API = "https://api.telegram.org/bot" + TOKEN if TOKEN else ""

ADMIN_TG_IDS = set()
for _p in str(os.getenv("ADMIN_TG_IDS", "5427735251") or "5427735251").split(","):
    _p = _p.strip()
    if _p.isdigit():
        ADMIN_TG_IDS.add(int(_p))
if not ADMIN_TG_IDS:
    ADMIN_TG_IDS.add(5427735251)


def api_call(method, **params):
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    r = httpx.post(API + "/" + method, json=params, timeout=60)
    r.raise_for_status()
    return r.json()


def current_shop_url():
    for key in ("SHOP_URL", "WEBAPP_URL", "RENDER_EXTERNAL_URL"):
        u = (os.environ.get(key) or "").strip().rstrip("/")
        if u.startswith("https://"):
            return u
    try:
        with open(os.path.join(BASE_DIR, "current_tunnel_url"), "r", encoding="utf-8") as f:
            u = f.read().strip()
            if u.startswith("https://"):
                return u
    except Exception:
        pass
    return SHOP_URL


def _load_users():
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _save_users(users):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=1)
    os.replace(tmp, USERS_FILE)


def _load_settings():
    s = {"per_order_price": 0.0}
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s.update(json.load(f) or {})
    except Exception:
        pass
    try:
        s["per_order_price"] = float(s.get("per_order_price") or 0)
    except Exception:
        s["per_order_price"] = 0.0
    return s


def _save_settings(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)


def _tg_key(tg_id: int) -> str:
    return "tg_%s" % int(tg_id)


def ensure_user(from_user: dict):
    tg_id = int(from_user.get("id"))
    key = _tg_key(tg_id)
    users = _load_users()
    u = users.get(key)
    is_admin = tg_id in ADMIN_TG_IDS
    name = " ".join(
        [str(from_user.get("first_name") or ""), str(from_user.get("last_name") or "")]
    ).strip() or (from_user.get("username") or key)
    if not u:
        nid = max([int(x.get("id") or 0) for x in users.values()] or [0]) + 1
        u = {
            "id": nid,
            "username": key,
            "password": "",
            "role": "admin" if is_admin else "user",
            "plan": "proplus" if is_admin else "free",
            "active": True if is_admin else False,
            "tg_id": tg_id,
            "tg_username": from_user.get("username") or "",
            "display_name": name,
            "created_at": int(time.time()),
            "last_seen": int(time.time()),
            "used": {"date": "", "count": 0},
            "trial_used": 0,
            "balance": 0.0,
        }
    else:
        u["tg_id"] = tg_id
        u["tg_username"] = from_user.get("username") or u.get("tg_username") or ""
        u["display_name"] = name or u.get("display_name")
        u["last_seen"] = int(time.time())
        if is_admin:
            u["role"] = "admin"
            u["active"] = True
            if u.get("plan") in (None, "", "free"):
                u["plan"] = "proplus"
    users[key] = u
    _save_users(users)
    return u


def is_admin(tg_id: int) -> bool:
    return int(tg_id) in ADMIN_TG_IDS


def send_msg(chat_id, text, reply_markup=None, parse_mode="HTML"):
    params = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return api_call("sendMessage", **params)


def open_shop_keyboard():
    url = current_shop_url()
    if not url:
        return None
    return {"inline_keyboard": [[{"text": "🛍️ Open SJ Shop", "web_app": {"url": url}}]]}


def send_start(chat_id, from_user, extra=None):
    u = ensure_user(from_user or {})
    url = current_shop_url()
    lines = [
        CFG.get("welcome_text") or "Open the shop:",
        "",
        f"Your Telegram ID: <code>{u.get('tg_id')}</code>",
    ]
    if u.get("role") == "admin":
        lines.append("Role: <b>ADMIN</b> — full access.")
        lines.append("Commands: /grant /revoke /topup /rate /users /whoami")
    elif u.get("active"):
        lines.append("Access: <b>ALLOWED</b> — you can order.")
    else:
        lines.append("Access: <b>PENDING</b> — ask admin to /grant your ID.")
    if extra:
        lines.append(extra)
    if not url:
        lines.append("\n⚠️ Shop URL not set. Set SHOP_URL / WEBAPP_URL.")
    send_msg(chat_id, "\n".join(lines), reply_markup=open_shop_keyboard())


def cmd_grant(chat_id, args, admin_id):
    if not is_admin(admin_id):
        send_msg(chat_id, "Admins only.")
        return
    if not args:
        send_msg(chat_id, "Usage: /grant &lt;telegram_id&gt; [plan]\nExample: /grant 123456789 pro")
        return
    try:
        tg_id = int(args[0])
    except Exception:
        send_msg(chat_id, "Invalid telegram id.")
        return
    plan = args[1] if len(args) > 1 else "pro"
    if plan not in ("free", "pro", "proplus"):
        plan = "pro"
    key = _tg_key(tg_id)
    users = _load_users()
    u = users.get(key) or {
        "id": max([int(x.get("id") or 0) for x in users.values()] or [0]) + 1,
        "username": key,
        "password": "",
        "role": "user",
        "plan": plan,
        "active": True,
        "tg_id": tg_id,
        "created_at": int(time.time()),
        "last_seen": 0,
        "used": {"date": "", "count": 0},
        "trial_used": 0,
        "balance": 0.0,
    }
    u["active"] = True
    u["plan"] = plan
    u["tg_id"] = tg_id
    users[key] = u
    _save_users(users)
    send_msg(chat_id, f"✅ Granted access to <code>{tg_id}</code>\nPlan: <b>{plan}</b>")
    try:
        send_msg(tg_id, "✅ Admin granted you shop access. Open the bot and tap Open SJ Shop.")
    except Exception:
        pass


def cmd_revoke(chat_id, args, admin_id):
    if not is_admin(admin_id):
        send_msg(chat_id, "Admins only.")
        return
    if not args:
        send_msg(chat_id, "Usage: /revoke &lt;telegram_id&gt;")
        return
    try:
        tg_id = int(args[0])
    except Exception:
        send_msg(chat_id, "Invalid telegram id.")
        return
    if tg_id in ADMIN_TG_IDS:
        send_msg(chat_id, "Cannot revoke admin.")
        return
    key = _tg_key(tg_id)
    users = _load_users()
    u = users.get(key)
    if not u:
        send_msg(chat_id, "User not found.")
        return
    u["active"] = False
    users[key] = u
    _save_users(users)
    send_msg(chat_id, f"⛔ Revoked <code>{tg_id}</code>")


def cmd_topup(chat_id, args, admin_id):
    if not is_admin(admin_id):
        send_msg(chat_id, "Admins only.")
        return
    if len(args) < 2:
        send_msg(chat_id, "Usage: /topup &lt;telegram_id&gt; &lt;amount&gt;")
        return
    try:
        tg_id = int(args[0])
        amount = float(args[1])
    except Exception:
        send_msg(chat_id, "Invalid args.")
        return
    if amount <= 0:
        send_msg(chat_id, "Amount must be &gt; 0.")
        return
    key = _tg_key(tg_id)
    users = _load_users()
    u = users.get(key)
    if not u:
        send_msg(chat_id, "User not found. They must /start the bot first.")
        return
    u["balance"] = round(float(u.get("balance") or 0) + amount, 2)
    users[key] = u
    _save_users(users)
    send_msg(chat_id, f"💰 Top-up ₹{amount:g} → balance ₹{u['balance']:g} for <code>{tg_id}</code>")
    try:
        send_msg(tg_id, f"💰 Admin added ₹{amount:g}. Wallet: ₹{u['balance']:g}")
    except Exception:
        pass


def cmd_rate(chat_id, args, admin_id):
    if not is_admin(admin_id):
        send_msg(chat_id, "Admins only.")
        return
    if not args:
        s = _load_settings()
        send_msg(chat_id, f"Current per-order rate: ₹{float(s.get('per_order_price') or 0):g}\nUsage: /rate &lt;rupees&gt;")
        return
    try:
        rate = max(0.0, float(args[0]))
    except Exception:
        send_msg(chat_id, "Invalid rate.")
        return
    s = _load_settings()
    s["per_order_price"] = rate
    _save_settings(s)
    send_msg(chat_id, f"✅ Per-order rate set to ₹{rate:g}")


def cmd_users(chat_id, admin_id):
    if not is_admin(admin_id):
        send_msg(chat_id, "Admins only.")
        return
    users = _load_users()
    rows = sorted(users.values(), key=lambda x: int(x.get("last_seen") or 0), reverse=True)[:30]
    if not rows:
        send_msg(chat_id, "No users yet.")
        return
    lines = ["<b>Users</b> (latest 30)"]
    for u in rows:
        lines.append(
            f"• <code>{u.get('tg_id') or u.get('username')}</code> "
            f"{'✅' if u.get('active') or u.get('role')=='admin' else '⏳'} "
            f"{u.get('role')} {u.get('plan')} ₹{float(u.get('balance') or 0):g}"
        )
    send_msg(chat_id, "\n".join(lines))


def handle_message(m):
    chat = m.get("chat") or {}
    chat_id = chat.get("id")
    from_user = m.get("from") or {}
    text = (m.get("text") or "").strip()
    if not chat_id:
        return
    if not text:
        send_start(chat_id, from_user)
        return

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
    uid = int(from_user.get("id") or 0)

    if cmd in ("/start", "/open", "/shop", "/menu"):
        send_start(chat_id, from_user)
    elif cmd in ("/id", "/whoami"):
        ensure_user(from_user)
        send_msg(
            chat_id,
            f"Telegram ID: <code>{uid}</code>\nAdmin: {'yes' if is_admin(uid) else 'no'}",
        )
    elif cmd == "/grant":
        cmd_grant(chat_id, args, uid)
    elif cmd == "/revoke":
        cmd_revoke(chat_id, args, uid)
    elif cmd == "/topup":
        cmd_topup(chat_id, args, uid)
    elif cmd == "/rate":
        cmd_rate(chat_id, args, uid)
    elif cmd == "/users":
        cmd_users(chat_id, uid)
    elif cmd == "/admin":
        if is_admin(uid):
            send_msg(
                chat_id,
                "<b>Admin panel (Telegram)</b>\n"
                "/grant &lt;id&gt; [plan] — allow order access\n"
                "/revoke &lt;id&gt; — remove access\n"
                "/topup &lt;id&gt; &lt;₹&gt; — wallet credit\n"
                "/rate &lt;₹&gt; — per-order fee\n"
                "/users — list users\n"
                "Web admin also opens automatically in the shop for your account.",
            )
        else:
            send_msg(chat_id, "Admins only.")
    else:
        send_start(chat_id, from_user)


def main():
    if not TOKEN:
        print("ERROR: set BOT_TOKEN env or bot_config.json token", flush=True)
        return
    print("token_valid=getMe", flush=True)
    me = api_call("getMe")
    print("bot =@" + (me["result"].get("username") or "?"), flush=True)
    print("admin_ids=", sorted(ADMIN_TG_IDS), flush=True)
    try:
        api_call("deleteWebhook", drop_pending_updates=True)
    except Exception:
        pass

    def refresh_menu():
        url = current_shop_url()
        if not url:
            return False
        api_call(
            "setChatMenuButton",
            menu_button={
                "type": "web_app",
                "text": "🛍️ SJ Shop",
                "web_app": {"url": url},
            },
        )
        return True

    try:
        refresh_menu()
        print("menu_button=ok", flush=True)
    except Exception as e:
        print("menu_button=skip (" + str(e) + ")", flush=True)

    last_id = 0
    last_menu_refresh = time.time()
    while True:
        try:
            r = api_call("getUpdates", offset=last_id + 1, timeout=50)
            for upd in r.get("result") or []:
                last_id = max(last_id, int(upd.get("update_id") or 0))
                if "message" in upd:
                    handle_message(upd["message"])
            if time.time() - last_menu_refresh > 300:
                try:
                    refresh_menu()
                except Exception:
                    pass
                last_menu_refresh = time.time()
        except Exception:
            traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
