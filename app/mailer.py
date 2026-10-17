"""SMTP 发信；未配置时降级为控制台输出，便于本地开发登录流程。"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from .config import settings


def send_mail(to: str, subject: str, text: str) -> tuple[bool, str]:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        if settings.MAIL_DEV_PRINT:
            print("\n[MAIL-DEV] to=%s\n主题: %s\n%s\n" % (to, subject, text), flush=True)
            return True, "dev-console"
        return False, "服务器未配置发信服务"

    msg = EmailMessage()
    msg["From"] = formataddr((settings.APP_NAME, settings.SMTP_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    try:
        if settings.SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ctx, timeout=15) as s:
                s.login(settings.SMTP_USER, settings.SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(settings.SMTP_USER, settings.SMTP_PASS)
                s.send_message(msg)
        return True, "sent"
    except Exception as exc:  # 网络/认证异常都收敛成可读文案
        return False, "邮件发送失败：%s" % exc


def send_code(to: str, code: str, scene: str) -> tuple[bool, str]:
    titles = {
        "register": "注册验证码",
        "reset": "找回密码验证码",
        "verify": "异地登录安全验证",
    }
    title = titles.get(scene, "验证码")
    body = (
        "%s\n\n你的%s是：%s\n%d 分钟内有效，请勿转发给他人。\n"
        "如果这不是你本人操作，忽略本邮件即可。\n"
        % (settings.APP_NAME, title, code, settings.CODE_TTL // 60)
    )
    return send_mail(to, "【%s】%s" % (settings.APP_NAME, title), body)
