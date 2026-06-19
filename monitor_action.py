#!/usr/bin/env python3
"""
海州区人民政府公告页面监控脚本 (GitHub Actions 版)
每次运行检查一次，如有变化发邮件通知，并自动更新快照到仓库
"""

import hashlib
import json
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ============ 配置 ============
TARGET_URL = "http://www.lyghz.gov.cn/lyghzqrmzf/tzggg/content/2716f26e-95fe-410c-bada-93d8ffec5eda.html"
SNAPSHOT_FILE = "snapshot.json"
HASH_FILE = "content_hash.txt"
LOG_FILE = "monitor.log"
BEIJING_TZ = timezone(timedelta(hours=8))

# 邮件配置（从 GitHub Secrets 读取）
SMTP_SERVER = "smtp.163.com"
SMTP_PORT = 465
SMTP_USER = os.environ.get("SMTP_USER", "googleing@163.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = SMTP_USER
TO_EMAILS = os.environ.get("NOTIFY_EMAILS", "googleing@163.com,13775596811@139.com").split(",")

# ============ 工具函数 ============
def now_str():
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    ts = now_str()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_page(url, retries=3, timeout=30):
    """抓取页面"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.encoding = resp.apparent_encoding or "utf-8"
            if resp.status_code == 200:
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")
                info = {"title": "", "pub_date": "", "source": "", "content_text": "", "status_code": 200}
                for meta in soup.find_all("meta"):
                    name = meta.get("name", "")
                    if name == "ArticleTitle":
                        info["title"] = meta.get("content", "")
                    elif name == "PubDate":
                        info["pub_date"] = meta.get("content", "")
                    elif name == "ContentSource":
                        info["source"] = meta.get("content", "")
                content_div = soup.select_one("div.content")
                if content_div:
                    info["content_text"] = content_div.get_text(strip=True)
                if not info["title"]:
                    title_tag = soup.find("title")
                    if title_tag:
                        info["title"] = title_tag.get_text(strip=True)
                return html, info
            else:
                last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            last_error = str(e)
        if attempt < retries - 1:
            import time
            time.sleep(5 * (attempt + 1))
    raise Exception(f"抓取失败: {last_error}")


def compute_hashes(html, info):
    hh = hashlib.sha256(html.encode("utf-8")).hexdigest()
    tc = f"{info['title']}|{info['pub_date']}|{info['source']}|{info['content_text']}"
    th = hashlib.sha256(tc.encode("utf-8")).hexdigest()
    return hh, th


def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_snapshot(info, html_hash, text_hash):
    snap = {
        "url": TARGET_URL,
        "page_title": info["title"],
        "publish_date": info["pub_date"],
        "source": info["source"],
        "html_hash": html_hash,
        "text_hash": text_hash,
        "last_check_time": now_str(),
        "change_history": []
    }
    old = load_snapshot()
    if old:
        snap["first_check_time"] = old.get("first_check_time", now_str())
        snap["change_history"] = old.get("change_history", [])
    else:
        snap["first_check_time"] = now_str()
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    return snap


def save_hashes(html_hash, text_hash):
    with open(HASH_FILE, "w", encoding="utf-8") as f:
        f.write(f"{html_hash}|{text_hash}")


def load_hashes():
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE, "r", encoding="utf-8") as f:
            parts = f.read().strip().split("|")
            if len(parts) == 2:
                return parts[0], parts[1]
    return None, None


def detect_changes(old_info, new_info):
    changes = []
    if old_info["title"] != new_info["title"]:
        changes.append(f"📌 标题已更改\n  旧: {old_info['title']}\n  新: {new_info['title']}")
    if old_info["pub_date"] != new_info["pub_date"]:
        changes.append(f"📅 发布日期已更改\n  旧: {old_info['pub_date']}\n  新: {new_info['pub_date']}")
    if old_info["source"] != new_info["source"]:
        changes.append(f"🏛 来源已更改\n  旧: {old_info['source']}\n  新: {new_info['source']}")
    old_text = old_info.get("content_text", "")
    new_text = new_info.get("content_text", "")
    if old_text != new_text:
        if not old_text:
            changes.append(f"📝 页面正文首次出现 ({len(new_text)}字)")
        elif not new_text:
            changes.append(f"⚠️ 页面正文已消失")
        else:
            diff = len(new_text) - len(old_text)
            pct = abs(diff) / max(len(old_text), 1) * 100
            changes.append(f"📝 页面正文有变化（字数变化: {diff:+d}, {pct:.1f}%）")
    return changes


def send_email(subject, body):
    if not SMTP_PASSWORD:
        log("⚠️ 未配置SMTP密码，跳过邮件发送")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = ", ".join(TO_EMAILS)
        msg["Subject"] = subject
        html_body = f"""<html><head><meta charset="utf-8"></head>
<body style="font-family:'Microsoft YaHei',sans-serif;max-width:700px;">
<h2 style="color:#c0392b;">🔔 海州区人民政府公告页面监控提醒</h2>
<hr style="border:1px solid #eee;">
<pre style="white-space:pre-wrap;font-family:'Microsoft YaHei',sans-serif;font-size:14px;line-height:1.8;background:#f9f9f9;padding:15px;border-radius:8px;">{body}</pre>
<hr style="border:1px solid #eee;">
<p style="color:#888;font-size:12px;">
📎 监控页面：<a href="{TARGET_URL}">{TARGET_URL}</a><br>
⏰ 检测时间：{now_str()}<br>
🤖 由 GitHub Actions 自动发送
</p></body></html>"""
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())
        log(f"✅ 邮件已发送至: {', '.join(TO_EMAILS)}")
        return True
    except Exception as e:
        log(f"❌ 邮件发送失败: {e}")
        return False


def record_change(snap, info, html_hash, text_hash, diff_summary):
    entry = {
        "time": now_str(),
        "new_title": info["title"],
        "new_pub_date": info["pub_date"],
        "new_source": info["source"],
        "new_html_hash": html_hash,
        "new_text_hash": text_hash,
        "diff_summary": diff_summary,
    }
    snap["change_history"].append(entry)
    snap["page_title"] = info["title"]
    snap["publish_date"] = info["pub_date"]
    snap["source"] = info["source"]
    snap["html_hash"] = html_hash
    snap["text_hash"] = text_hash
    snap["last_check_time"] = now_str()
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)


# ============ 主逻辑 ============
def run():
    log("=" * 50)
    log(f"🔍 检查页面: {TARGET_URL}")

    try:
        html, info = fetch_page(TARGET_URL)
        html_hash, text_hash = compute_hashes(html, info)
        saved_hh, saved_th = load_hashes()
        snap = load_snapshot()

        # 首次运行
        if saved_hh is None:
            log("📸 首次运行，建立基准快照...")
            save_hashes(html_hash, text_hash)
            snap = save_snapshot(info, html_hash, text_hash)
            log(f"✅ 基准已建立: {info['title']} | {info['pub_date']} | {info['source']}")
            log(f"   正文: {len(info['content_text'])} 字符 | HTML哈希: {html_hash[:16]}...")
            return "baseline_created"

        # 无变化
        if html_hash == saved_hh and text_hash == saved_th:
            log(f"✅ 页面无变化")
            if snap:
                snap["last_check_time"] = now_str()
                with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                    json.dump(snap, f, ensure_ascii=False, indent=2)
            return "no_change"

        # 检测到变化！
        log(f"🚨 页面有变化！HTML: {html_hash != saved_hh}, 文本: {text_hash != saved_th}")

        old_info = {
            "title": snap.get("page_title", "") if snap else "",
            "pub_date": snap.get("publish_date", "") if snap else "",
            "source": snap.get("source", "") if snap else "",
            "content_text": "",
        }

        changes = detect_changes(old_info, info)
        if not changes:
            changes.append("🔄 HTML结构变化，但标题/日期/来源/正文未检测到明显差异")

        diff_summary = "\n\n".join(changes)

        if snap:
            record_change(snap, info, html_hash, text_hash, diff_summary)
        else:
            snap = save_snapshot(info, html_hash, text_hash)

        save_hashes(html_hash, text_hash)

        subject = f"🔔 海州区公告页面有变动 - {datetime.now(BEIJING_TZ).strftime('%m-%d %H:%M')}"
        body = f"""检测到监控页面发生变化！

📄 页面标题：{info['title']}
📅 发布日期：{info['pub_date']}
🏛 信息来源：{info['source']}
🔗 页面地址：{TARGET_URL}

━━━━━━━━━━━━━━━━━━━━
变动详情：
━━━━━━━━━━━━━━━━━━━━
{diff_summary}

━━━━━━━━━━━━━━━━━━━━
💡 建议：请立即打开页面查看最新内容。"""

        send_email(subject, body)
        log(f"📋 变化详情:\n{diff_summary}")
        return "change_detected"

    except Exception as e:
        log(f"❌ 检查失败: {e}")
        subject = f"⚠️ 海州区公告页面监控异常 - {datetime.now(BEIJING_TZ).strftime('%m-%d %H:%M')}"
        body = f"""监控系统无法访问目标页面！

🔗 页面地址：{TARGET_URL}
❌ 错误信息：{str(e)}
⏰ 检测时间：{now_str()}

请检查网络连接或页面是否可访问。"""
        send_email(subject, body)
        return "error"


if __name__ == "__main__":
    result = run()
    print(f"\n>>> RESULT: {result}")
