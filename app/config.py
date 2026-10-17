"""集中配置。全部从环境变量读取，禁止把密钥写进仓库。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _abs_db_path() -> str:
    """相对路径一律相对项目根解析，避免从别的 cwd 启动时写到别处去。"""
    raw = _env("DB_PATH", "data/app.db")
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    return str(p)


class Settings:
    # --- 基础 ---
    APP_NAME = _env("APP_NAME", "B站账号管理助手")
    SITE_URL = _env("SITE_URL", "http://127.0.0.1:8000")
    DEBUG = _env_bool("DEBUG", True)

    # --- 安全 ---
    SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
    SESSION_COOKIE = _env("SESSION_COOKIE", "bp_session")
    SESSION_MAX_AGE = _env_int("SESSION_MAX_AGE", 60 * 60 * 24 * 14)  # 14 天
    ADMIN_PATH = _env("ADMIN_PATH", "admin234").strip("/")
    CAPTCHA_TTL = _env_int("CAPTCHA_TTL", 300)          # 计算验证码有效期(秒)
    CAPTCHA_BYPASS_AFTER = _env_int("CAPTCHA_BYPASS_AFTER", 3)  # 登录成功几次后免验证码
    LOGIN_MAX_FAIL = _env_int("LOGIN_MAX_FAIL", 8)      # 同 IP 失败上限
    LOGIN_FAIL_WINDOW = _env_int("LOGIN_FAIL_WINDOW", 900)
    CODE_TTL = _env_int("CODE_TTL", 600)                # 邮箱验证码有效期
    CODE_RESEND = _env_int("CODE_RESEND", 60)           # 重发冷却

    # --- 数据库 ---
    DB_PATH = _abs_db_path()

    # --- 邮件 ---
    SMTP_HOST = _env("SMTP_HOST", "")
    SMTP_PORT = _env_int("SMTP_PORT", 465)
    SMTP_SSL = _env_bool("SMTP_SSL", True)
    SMTP_USER = _env("SMTP_USER", "")
    SMTP_PASS = _env("SMTP_PASS", "")
    SMTP_FROM = _env("SMTP_FROM", _env("SMTP_USER", "noreply@example.com"))
    # 未配置 SMTP 时，验证码只打印到控制台（开发模式）
    MAIL_DEV_PRINT = _env_bool("MAIL_DEV_PRINT", True)

    # --- 邀请 / 额度 ---
    INVITE_SELF_BONUS_MB = _env_int("INVITE_SELF_BONUS_MB", 300)
    INVITE_REF_BONUS_MB = _env_int("INVITE_REF_BONUS_MB", 30)

    # --- Bilibili 上游 ---
    BILI_API = _env("BILI_API", "https://api.bilibili.com")
    BILI_UA = _env(
        "BILI_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    BILI_TICKET_TTL = _env_int("BILI_TICKET_TTL", 3 * 24 * 3600)

    # --- 调度 ---
    WORKER_ENABLED = _env_bool("WORKER_ENABLED", True)
    WORKER_TICK = _env_int("WORKER_TICK", 30)

    # --- AI ---
    AI_API_BASE = _env("AI_API_BASE", "https://api.openai.com/v1")
    AI_API_KEY = _env("AI_API_KEY", "")
    AI_MODEL = _env("AI_MODEL", "gpt-4o-mini")
    AI_PERSONA = _env("AI_PERSONA", "AI 助手")


settings = Settings()

# 会员额度表：把站点上的对照表做成配置，后端直接引用
PLAN_LIMITS = {
    "free": {
        "like_per_day": 5,
        "upload_per_day": 1,
        "max_accounts": 2,
        "cron_tasks": 1,
        "cron_runs_per_day": 1,
        "uid_targets": 1,
        "ai_chat_per_day": 10,
        "ai_tasks": 1,
        "ai_action_interval": 30,      # 秒
        "ai_runs_per_day": 5,
        "ai_task_max_minutes": 10,
        "monitor_interval_min": 60,
        "dm_per_day": 10,
        "video_lib_mb": 200,
        "traffic_mb": 300,
    },
    "vip": {
        "like_per_day": 888,
        "upload_per_day": 88,
        "max_accounts": 50,
        "cron_tasks": -1,               # -1 = 不限
        "cron_runs_per_day": -1,
        "uid_targets": -1,
        "ai_chat_per_day": 888,
        "ai_tasks": -1,
        "ai_action_interval": 3,
        "ai_runs_per_day": -1,
        "ai_task_max_minutes": 1440,
        "monitor_interval_min": 1,
        "dm_per_day": -1,
        "video_lib_mb": 5120,
        "traffic_mb": 0,
    },
}

MONITOR_INTERVAL_BY_PLAN = {"month": 50, "quarter": 30, "year": 1}
