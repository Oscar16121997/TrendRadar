# coding=utf-8
"""Email the DeepSeek-ranked Douyin report and CSV through Gmail SMTP."""
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

RECIPIENT = "linoscaryosino@gmail.com"
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
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
message["Subject"] = f"抖音热点选题报告（DeepSeek评分）- {today}"
message["From"] = SENDER
message["To"] = RECIPIENT
message.set_content(
    "本邮件包含 TrendRadar 抖音最近7天高赞视频发现结果及 DeepSeek 海外选题评分。\n\n"
    "HTML 附件：适合直接浏览，包含 AI 综合评分、分项评分、中文判断、英文开场和视频链接。\n"
    "CSV 附件：适合 Excel 筛选、排序和进一步分析。\n\n"
    "注意：DeepSeek 本轮根据视频标题/描述与互动元数据进行分析，没有实际观看视频。"
)

message.add_attachment(
    HTML_REPORT.read_bytes(),
    maintype="text",
    subtype="html",
    filename="Douyin_DeepSeek_Selection_Report.html",
)
message.add_attachment(
    CSV_REPORT.read_bytes(),
    maintype="text",
    subtype="csv",
    filename="Douyin_DeepSeek_Selection_Report.csv",
)

with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
    smtp.login(SENDER, PASSWORD)
    smtp.send_message(message)

print(f"✅ DeepSeek 抖音选题报告已发送至 {RECIPIENT}")
