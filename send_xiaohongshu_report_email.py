# coding: utf-8
"""Email the DeepSeek-ranked Xiaohongshu report and CSV via Gmail SMTP."""
import os
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from pathlib import Path

RECIPIENTS = ["sylvialiu0927@gmail.com", "linoscaryosino@gmail.com"]
SENDER = os.environ.get("SMTP_USERNAME", "").strip()
PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
OUTPUT_DIR = Path(os.environ.get("XHS_OUTPUT_DIR", "output/xiaohongshu_full_discovery"))
HTML_REPORT = OUTPUT_DIR / "xiaohongshu_full_discovery.html"
CSV_REPORT = OUTPUT_DIR / "xiaohongshu_full_discovery.csv"

if not SENDER or not PASSWORD:
    raise RuntimeError("Missing SMTP_USERNAME or SMTP_PASSWORD GitHub Secrets.")
missing = [str(path) for path in (HTML_REPORT, CSV_REPORT) if not path.is_file()]
if missing:
    raise FileNotFoundError("Missing report attachment(s): " + ", ".join(missing))

today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
message = EmailMessage()
message["Subject"] = f"小红书趋势选题报告（DeepSeek评分）- {today}"
message["From"] = SENDER
message["To"] = ", ".join(RECIPIENTS)
message.set_content(
    "小红书趋势选题日报已直接嵌入本邮件正文。\n\n"
    "CSV 附件可用于 Excel 筛选和排序。AI仅依据笔记标题、正文描述和互动元数据分析，未观看图片/视频或读取评论正文。"
)
message.add_alternative(HTML_REPORT.read_text(encoding="utf-8"), subtype="html")
message.add_attachment(
    CSV_REPORT.read_bytes(), maintype="text", subtype="csv",
    filename="Xiaohongshu_DeepSeek_Selection_Report.csv",
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(SENDER, PASSWORD)
    smtp.send_message(message, from_addr=SENDER, to_addrs=RECIPIENTS)

print("小红书选题报告已发送至 " + ", ".join(RECIPIENTS))
