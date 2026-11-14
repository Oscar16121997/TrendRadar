# coding=utf-8
"""Email the daily high-like Douyin report and CSV through Gmail SMTP."""
import os
import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage
from pathlib import Path

RECIPIENTS = ["sylvialiu0927@gmail.com", "linoscaryosino@gmail.com"]
SENDER = os.environ.get("SMTP_USERNAME", "").strip()
PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
OUTPUT_DIR = Path(os.environ.get("DOUYIN_DAILY_VIRAL_OUTPUT_DIR", "output/douyin_daily_viral"))
HTML_REPORT = OUTPUT_DIR / "douyin_daily_viral.html"
CSV_REPORT = OUTPUT_DIR / "douyin_daily_viral.csv"

if not SENDER or not PASSWORD:
    raise RuntimeError(
        "Missing SMTP_USERNAME or SMTP_PASSWORD GitHub Secrets; "
        "daily viral report email was not sent."
    )

missing = [str(path) for path in (HTML_REPORT, CSV_REPORT) if not path.is_file()]
if missing:
    raise FileNotFoundError("Missing report attachment(s): " + ", ".join(missing))

message = EmailMessage()
today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
message["Subject"] = f"抖音当日高赞热点日报（点赞>1万，排除政治宣传）- {today}"
message["From"] = SENDER
message["To"] = ", ".join(RECIPIENTS)
message.set_content(
    "抖音当日高赞热点日报已直接嵌入本邮件正文。仅收录北京时间当天发布、采集时累计点赞严格超过10,000、"
    "并由 AI 判定为中国国内且非政治宣传的视频。政治宣传及政治属性不明确的内容均不纳入。\n\n"
    "CSV 附件可用于 Excel 筛选和排序。AI 仅根据标题/描述和互动元数据分析，没有实际观看视频画面。"
)
message.add_alternative(HTML_REPORT.read_text(encoding="utf-8"), subtype="html")
message.add_attachment(
    CSV_REPORT.read_bytes(),
    maintype="text",
    subtype="csv",
    filename="Douyin_Daily_Viral_Report.csv",
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(SENDER, PASSWORD)
    smtp.send_message(message, from_addr=SENDER, to_addrs=RECIPIENTS)

print("✅ 抖音当日高赞热点日报已发送至 " + ", ".join(RECIPIENTS))
