#!/usr/bin/env python3
"""
海州区人民政府公告页面监控脚本
监控目标：教师资格认定公告页面
如检测到页面内容变化，发送邮件通知
"""

import hashlib
import json
import os
import smtplib
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ============ 配置 ============
TARGET_URL = "http://www.lyghz.gov.cn/lyghzqrmzf/tzggg/content/2716f26e-95fe-410c-bada-93d8ffec5eda.html"
SNAPSHOT_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_FILE = os.path.join(SNAPSHOT_DIR, "snapshot_baseline.json")
CONTENT_HASH_FILE = os.path.join(SNAPSHOT_DIR, "content_hash.txt")
LOG_FILE = os.path.join(SNAPSHOT_DIR, "monitor.log")

# 邮件配置
SMTP_SERVER = "smtp.163.com"
SMTP_PORT = 465
SMTP_USER = "googleing@163.com"  # 发件邮箱（需开启SMTP）
SMTP_PASSWORD = "VDuWJPbNnDQncrVW"  # 163邮箱授权码
FROM_EMAIL = "googleing@163.com"
TO_EMAILS = [
    "googleing@163.com",
    "13775596811@139.com"
]

# 监控间隔（秒），默认30分钟
CHECK_INTERVAL = 30 * 60

# ============ 日志 ============
def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============ 页面抓取 ============
def fetch_page(url, retries=3, timeout=30):
    """抓取页面，返回 (html_content, parsed_info_dict)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.encoding = resp.apparent_encoding or "utf-8"
            if resp.status_code == 200:
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # 提取关键信息
                info = {
                    "title": "",
                    "pub_date": "",
                    "source": "",
                    "content_text": "",
                    "status_code": 200,
                }

                # 从 meta 提取
                for meta in soup.find_all("meta"):
                    name = meta.get("name", "")
                    content = meta.get("content", "")
                    if name == "ArticleTitle":
                        info["title"] = content
                    elif name == "PubDate":
                        info["pub_date"] = content
                    elif name == "ContentSource":
                        info["source"] = content

                # 提取正文
                content_div = soup.select_one("div.content")
                if content_div:
                    info["content_text"] = content_div.get_text(strip=True)

                # 如果没有 meta title，用 <title>
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
            time.sleep(5 * (attempt + 1))

    raise Exception(f"无法抓取页面 ({retries}次尝试均失败): {last_error}")


# ============ 哈希计算 ============
def compute_hashes(html, info):
    """计算HTML和纯文本的哈希"""
    html_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
    text_content = f"{info['title']}|{info['pub_date']}|{info['source']}|{info['content_text']}"
    text_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
    return html_hash, text_hash


# ============ 快照管理 ============
def load_baseline():
    """加载基准快照"""
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_saved_hashes():
    """加载保存的哈希值"""
    if os.path.exists(CONTENT_HASH_FILE):
        with open(CONTENT_HASH_FILE, "r", encoding="utf-8") as f:
            data = f.read().strip().split("|")
            if len(data) == 2:
                return data[0], data[1]
    return None, None


def save_hashes(html_hash, text_hash):
    """保存哈希值"""
    with open(CONTENT_HASH_FILE, "w", encoding="utf-8") as f:
        f.write(f"{html_hash}|{text_hash}")


def update_baseline(info, html_hash, text_hash):
    """更新基准快照"""
    baseline = {
        "url": TARGET_URL,
        "page_title": info["title"],
        "publish_date": info["pub_date"],
        "source": info["source"],
        "html_hash": html_hash,
        "text_hash": text_hash,
        "first_check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_change_detected": None,
        "change_history": [],
    }

    # 保留旧记录的 change_history
    old = load_baseline()
    if old and "change_history" in old:
        baseline["change_history"] = old["change_history"]
    if old and "first_check_time" in old:
        baseline["first_check_time"] = old["first_check_time"]

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)

    return baseline


def record_change(baseline, info, html_hash, text_hash, diff_summary):
    """记录变化到快照"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    change_entry = {
        "time": now,
        "new_title": info["title"],
        "new_pub_date": info["pub_date"],
        "new_source": info["source"],
        "new_html_hash": html_hash,
        "new_text_hash": text_hash,
        "diff_summary": diff_summary,
    }

    if "change_history" not in baseline:
        baseline["change_history"] = []

    baseline["change_history"].append(change_entry)
    baseline["last_change_detected"] = now
    baseline["last_check_time"] = now
    baseline["page_title"] = info["title"]
    baseline["publish_date"] = info["pub_date"]
    baseline["source"] = info["source"]
    baseline["html_hash"] = html_hash
    baseline["text_hash"] = text_hash

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)


# ============ 差异检测 ============
def detect_changes(old_info, new_info):
    """比较新旧信息，返回差异描述列表"""
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
        # 计算差异程度
        if not old_text:
            changes.append(f"📝 页面正文首次出现 ({len(new_text)}字)")
        elif not new_text:
            changes.append(f"⚠️ 页面正文已消失")
        else:
            # 简单差异统计
            len_diff = len(new_text) - len(old_text)
            pct_change = abs(len_diff) / max(len(old_text), 1) * 100
            changes.append(f"📝 页面正文有变化（字数变化: {len_diff:+d}, {pct_change:.1f}%）")

    return changes


# ============ 邮件发送 ============
def send_email(subject, body):
    """发送邮件通知"""
    if not SMTP_PASSWORD:
        log("⚠️ 未配置SMTP密码/授权码，无法发送邮件。请设置 SMTP_PASSWORD 环境变量。")
        log(f"邮件主题: {subject}")
        log(f"邮件正文: {body[:500]}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = ", ".join(TO_EMAILS)
        msg["Subject"] = subject

        # HTML正文
        html_body = f"""
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: 'Microsoft YaHei', sans-serif; max-width: 700px;">
            <h2 style="color: #c0392b;">🔔 海州区人民政府公告页面监控提醒</h2>
            <hr style="border: 1px solid #eee;">
            <pre style="white-space: pre-wrap; font-family: 'Microsoft YaHei', sans-serif; font-size: 14px; line-height: 1.8; background: #f9f9f9; padding: 15px; border-radius: 8px;">{body}</pre>
            <hr style="border: 1px solid #eee;">
            <p style="color: #888; font-size: 12px;">
                📎 监控页面：<a href="{TARGET_URL}">{TARGET_URL}</a><br>
                ⏰ 检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                🤖 此邮件由自动化监控系统自动发送
            </p>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())

        log(f"✅ 邮件已发送至: {', '.join(TO_EMAILS)}")
        return True

    except Exception as e:
        log(f"❌ 邮件发送失败: {e}")
        return False


# ============ 主循环 ============
def run_once():
    """执行一次检查"""
    log(f"🔍 开始检查页面: {TARGET_URL}")

    try:
        html, info = fetch_page(TARGET_URL)
        html_hash, text_hash = compute_hashes(html, info)

        saved_html_hash, saved_text_hash = load_saved_hashes()
        baseline = load_baseline()

        # 首次运行 - 建立基准
        if saved_html_hash is None:
            log("📸 首次运行，建立基准快照...")
            save_hashes(html_hash, text_hash)
            baseline = update_baseline(info, html_hash, text_hash)
            log(f"✅ 基准已建立: {info['title']} | {info['pub_date']} | {info['source']}")
            log(f"   正文长度: {len(info['content_text'])} 字符")
            log(f"   HTML哈希: {html_hash[:16]}...")
            return True

        # 比较哈希值
        html_changed = html_hash != saved_html_hash
        text_changed = text_hash != saved_text_hash

        if not html_changed and not text_changed:
            log(f"✅ 页面无变化 ({datetime.now().strftime('%H:%M:%S')})")
            # 更新最后检查时间
            if baseline:
                baseline["last_check_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                    json.dump(baseline, f, ensure_ascii=False, indent=2)
            return True

        # 检测到变化！
        log(f"🚨 检测到页面变化！HTML变化: {html_changed}, 文本变化: {text_changed}")

        # 构建旧信息
        old_info = {
            "title": baseline.get("page_title", "") if baseline else "",
            "pub_date": baseline.get("publish_date", "") if baseline else "",
            "source": baseline.get("source", "") if baseline else "",
            "content_text": "",
        }

        # 尝试从旧快照还原旧文本（如果有保存）
        # 这里我们用当前基准里的信息来对比

        changes = detect_changes(old_info, info)

        if not changes:
            changes.append("🔄 页面HTML结构有变化，但关键信息（标题/日期/来源/正文）未检测到明显差异。可能是样式或脚本更新。")

        diff_summary = "\n\n".join(changes)

        # 记录变化
        if baseline:
            record_change(baseline, info, html_hash, text_hash, diff_summary)
        else:
            baseline = update_baseline(info, html_hash, text_hash)

        # 更新哈希
        save_hashes(html_hash, text_hash)

        # 发送邮件通知
        subject = f"🔔 海州区公告页面有变动 - {datetime.now().strftime('%m-%d %H:%M')}"
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
💡 建议：请立即打开页面查看最新内容。
"""

        send_email(subject, body)

        # 同时打印变化内容
        log(f"📋 变化详情:\n{diff_summary}")

        return True

    except Exception as e:
        log(f"❌ 检查失败: {e}")

        # 连续失败时也发邮件通知
        subject = f"⚠️ 海州区公告页面监控异常 - {datetime.now().strftime('%m-%d %H:%M')}"
        body = f"""监控系统无法访问目标页面！

🔗 页面地址：{TARGET_URL}
❌ 错误信息：{str(e)}
⏰ 检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请检查网络连接或页面是否可访问。
"""
        send_email(subject, body)
        return False


def main():
    log("=" * 60)
    log("🚀 海州区人民政府公告页面监控系统启动")
    log(f"📎 目标URL: {TARGET_URL}")
    log(f"📧 通知邮箱: {', '.join(TO_EMAILS)}")
    log(f"⏱  检查间隔: {CHECK_INTERVAL}秒 ({CHECK_INTERVAL//60}分钟)")
    log("=" * 60)

    consecutive_failures = 0

    while True:
        success = run_once()

        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            # 连续失败时延长间隔，避免频繁报错
            if consecutive_failures > 3:
                log(f"⚠️ 连续失败 {consecutive_failures} 次，下次检查将在 {(consecutive_failures - 2) * CHECK_INTERVAL // 60} 分钟后进行")

        # 动态调整间隔
        sleep_time = CHECK_INTERVAL
        if consecutive_failures > 3:
            sleep_time = min(consecutive_failures * CHECK_INTERVAL, 4 * CHECK_INTERVAL)

        log(f"💤 等待 {sleep_time // 60} 分钟后进行下次检查...")
        time.sleep(sleep_time)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="网站监控脚本")
    parser.add_argument("--once", action="store_true", help="仅运行一次检查")
    parser.add_argument("--password", type=str, help="SMTP邮箱授权码")
    args = parser.parse_args()

    if args.password:
        SMTP_PASSWORD = args.password
    elif os.environ.get("SMTP_PASSWORD"):
        SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]

    if args.once:
        run_once()
    else:
        main()
