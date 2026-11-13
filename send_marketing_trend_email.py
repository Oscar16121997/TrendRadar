# coding=utf-8
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

recipient = "linoscaryosino@gmail.com"
sender = os.environ.get("SMTP_USERNAME", "").strip()
password = os.environ.get("SMTP_PASSWORD", "").strip()
folder = Path(os.environ.get("MARKETING_OUTPUT_DIR", "output/marketing_trend"))
html_report = folder / "marketing_trend.html"
csv_report = folder / "marketing_trend.csv"

if not sender or not password:
    raise RuntimeError("SMTP_USERNAME or SMTP_PASSWORD is missing.")
missing = [str(p) for p in (html_report, csv_report) if not p.is_file()]
if missing:
    raise FileNotFoundError("Missing marketing report file(s): " + ", ".join(missing))

msg = EmailMessage()
msg["Subject"] = "营销号趋势日报 - " + datetime.now(timezone.utc).strftime("%Y-%m-%d")
msg["From"] = sender
msg["To"] = recipient
msg.set_content(
    "附件是营销号趋势日报：包含指定抖音账号近7天发布、点赞大于2000、收藏大于100的视频，"
    "并按 DeepSeek 海外选题综合评分降序排列。\n\n"
    "HTML 可直接浏览；CSV 可用 Excel 筛选。\n"
    "评分仅基于视频标题/描述与互动数据，AI没有观看视频画面。"
)
msg.add_attachment(html_report.read_bytes(), maintype="text", subtype="html",
                   filename="Marketing_Account_Trend.html")
msg.add_attachment(csv_report.read_bytes(), maintype="text", subtype="csv",
                   filename="Marketing_Account_Trend.csv")

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(sender, password)
    smtp.send_message(msg)
print("Marketing trend email sent to " + recipient)
