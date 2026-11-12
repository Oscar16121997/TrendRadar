# coding=utf-8
"""Send the generated TrendRadar HTML report by Gmail SMTP."""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

recipient = "linoscaryosino@gmail.com"
sender = os.environ.get("SMTP_USERNAME", "").strip()
password = os.environ.get("SMTP_PASSWORD", "").strip()
report = Path("index.html")

if not sender or not password:
    print("⚠️ 邮件未发送：请在 GitHub Actions Secrets 配置 SMTP_USERNAME 和 SMTP_PASSWORD。")
    raise SystemExit(0)

if not report.exists():
    raise FileNotFoundError("找不到 index.html，无法发送日报。")

message = EmailMessage()
message["Subject"] = "TrendRadar 每日热点报告 - " + datetime.now().strftime("%Y-%m-%d")
message["From"] = sender
message["To"] = recipient
message.set_content("TrendRadar 今日热点报告已生成，请查看附件 HTML 文件。")
message.add_attachment(
    report.read_bytes(),
    maintype="text",
    subtype="html",
    filename="TrendRadar_热点日报.html",
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(sender, password)
    smtp.send_message(message)

print("✅ TrendRadar 日报已发送至 " + recipient)
