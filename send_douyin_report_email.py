# coding=utf-8
"""Email the DeepSeek-ranked Douyin report and CSV through Gmail SMTP."""
import os
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from pathlib import Path

RECIPIENTS = ["sylvialiu0927@gmail.com", "linoscaryosino@gmail.com"]
SENDER = os.environ.get("SMTP_USERNAME", "").strip()
PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
OUTPUT_DIR = Path(os.environ.get("DOUYIN_OUTPUT_DIR", "output/douyin_ai_analysis"))
HTML_REPORT = OUTPUT_DIR / "douyin_full_discovery.html"
CSV_REPORT = OUTPUT_DIR / "douyin_full_discovery.csv"

if not SENDER or not PASSWORD:
    raise RuntimeError(
        "Missing SMTP_USERNAME or SMTP_PASSWORD GitHub Secrets; "
        "DeepSeek report email was not sent."
    )

missing = [str(path) for path in (HTML_REPORT, CSV_REPORT) if not path.is_file()]
if missing:
    raise FileNotFoundError("Missing report attachment(s): " + ", ".join(missing))

message = EmailMessage()
today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
message["Subject"] = f"抖音热点选题报告（DeepSeek评分）- {today}"
message["From"] = SENDER
message["To"] = ", ".join(RECIPIENTS)
message.set_content(
    "中国抖音热点选题日报已直接嵌入本邮件正文。手机端可点击每条视频的“打开抖音 App”；若邮箱不支持 App 跳转，可使用网页备用链接。\n\n"
    "CSV 附件可用于 Excel 筛选和排序。AI仅根据标题/描述和互动元数据评分，没有实际观看视频画面。"
)
message.add_alternative(HTML_REPORT.read_text(encoding="utf-8"), subtype="html")
message.add_attachment(
    CSV_REPORT.read_bytes(),
    maintype="text",
    subtype="csv",
    filename="Douyin_DeepSeek_Selection_Report.csv",
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(SENDER, PASSWORD)
    smtp.send_message(message, from_addr=SENDER, to_addrs=RECIPIENTS)

print(f"✅ DeepSeek 抖音选题报告已发送至 {\", \".join(RECIPIENTS)}")
