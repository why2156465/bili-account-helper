"""安全原语：口令散列、会话、验证码、Cookie 加解密、邀请码。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from .config import settings

# ---------------- 口令 ----------------
PBKDF2_ROUNDS = 120_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ROUNDS, salt.hex(), dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def password_problem(pw: str) -> str | None:
    """与前端一致的校验：8-20 位且至少含一个字母。"""
    if not isinstance(pw, str):
        return "密码格式不正确"
    if len(pw) < 8:
        return "新密码至少 8 位"
    if len(pw) > 20:
        return "新密码最多 20 位"
    if not any(c.isascii() and c.isalpha() for c in pw):
        return "新密码必须至少包含一个字母"
    return None


# ---------------- Cookie 加密 ----------------
def _fernet_key() -> bytes:
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_cookie(plain: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(_fernet_key()).encrypt(plain.encode()).decode()


def decrypt_cookie(token: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(_fernet_key()).decrypt(token.encode()).decode()


def mask_cookie(cookie: str) -> str:
    """给前端看的最小信息：只保留键名与长度。"""
    keys = []
    for part in cookie.split(";"):
        part = part.strip()
        if not part:
            continue
        k = part.split("=", 1)[0].strip()
        if k in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"):
            keys.append(k)
    return "keys=%s len=%d" % ("|".join(keys), len(cookie))


# ---------------- 令牌 ----------------
def new_token(n: int = 32) -> str:
    return secrets.token_urlsafe(n)


def sign(value: str) -> str:
    mac = hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
    return "%s.%s" % (value, mac[:32])


def unsign(signed: str) -> str | None:
    if "." not in signed:
        return None
    value, mac = signed.rsplit(".", 1)
    expect = hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]
    return value if hmac.compare_digest(mac, expect) else None


def new_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(7))


# ---------------- 计算验证码 ----------------
def make_captcha() -> tuple[str, str]:
    """返回 (题目, 答案)。加减乘，整数结果且不为负。"""
    a, b = secrets.randbelow(20) + 1, secrets.randbelow(20) + 1
    op = secrets.choice(["+", "-", "×"])
    if op == "-" and b > a:
        a, b = b, a
    q = "%d %s %d = ?" % (a, op, b)
    ans = {"+": a + b, "-": a - b, "×": a * b}[op]
    return q, str(ans)


def check_captcha(user_answer: str, expected: str) -> bool:
    return (user_answer or "").strip() == str(expected).strip()


def verify_captcha_token(conn, token: str, answer: str) -> tuple[bool, str]:
    """校验并消费一次性验证码；返回 (ok, 错误信息)。"""
    if not token:
        return False, "请先获取计算验证码"
    row = conn.execute("SELECT * FROM captchas WHERE token=?", (token,)).fetchone()
    if not row:
        return False, "验证码已失效，请换一题"
    if row["used"]:
        return False, "验证码已使用，请换一题"
    if row["expires_at"] < int(time.time()):
        return False, "验证码已过期，请换一题"
    if not check_captcha(answer, row["answer"]):
        conn.execute("UPDATE captchas SET used=1 WHERE token=?", (token,))
        return False, "计算结果不对，请重试"
    conn.execute("UPDATE captchas SET used=1 WHERE token=?", (token,))
    return True, ""


# ---------------- 邮箱验证码 ----------------
def make_email_code() -> str:
    return "%06d" % secrets.randbelow(1_000_000)


def code_fingerprint(code: str, email: str) -> str:
    salt = settings.SECRET_KEY.encode()
    return hashlib.sha256(salt + code.encode() + email.encode()).hexdigest()
