#!/usr/bin/env python3
"""
简知分销平台 - 多账号订单数据导出脚本 (GitHub Actions 云端版)
自动登录多个账号，进入分销管理订单数据页面，导出全部历史订单
验证码通过 ddddocr 自动识别（无人工交互）
凭据从环境变量 ACCOUNTS_JSON 读取
"""

import asyncio
import os
import time
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

# ============================================================
# 配置
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent
BASE_URL = "https://dist-dsh.jianzhiweike.cn/dms"
LOGIN_URL = f"{BASE_URL}#/user/login"
DATA_DIR = PROJECT_DIR / "data" / "exports"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR = str(DATA_DIR.resolve())

# 持久化浏览器 profile 目录（使用 GitHub Actions Cache 缓存）
BROWSER_PROFILES_DIR = PROJECT_DIR / "data" / "browser_profiles"
BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

# 从环境变量读取账号凭据，格式: [{"name":"xxx","phone":"xxx","password":"xxx"}, ...]
ACCOUNTS_JSON = os.environ.get("ACCOUNTS_JSON", "")
if ACCOUNTS_JSON:
    try:
        ACCOUNTS = json.loads(ACCOUNTS_JSON)
    except json.JSONDecodeError:
        print("❌ ACCOUNTS_JSON 环境变量格式错误，应为 JSON 数组")
        ACCOUNTS = []
else:
    # 本地开发 fallback（云端不应走此分支）
    ACCOUNTS = [
        {"name": "毛毛矩阵", "phone": "16602720972", "password": "bzh941122!"},
        {"name": "抖音", "phone": "18702789731", "password": "Zaiteng121314!"},
        {"name": "视频号", "phone": "18702775394", "password": "lz19990102@"},
        {"name": "严总", "phone": "18371236753", "password": "yy123456@"},
    ]

MAX_CAPTCHA_RETRIES = 5
MAX_LOGIN_RETRIES = 3


# ============================================================
# 验证码处理 (纯 OCR，无人工交互)
# ============================================================

def solve_captcha_with_ocr(captcha_path: str) -> str:
    """使用 ddddocr 识别验证码"""
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        with open(captcha_path, 'rb') as f:
            img_bytes = f.read()
        result = ocr.classification(img_bytes)
        return result.strip()
    except ImportError:
        print("  [OCR] ddddocr 未安装，无法识别验证码")
        return ""
    except Exception as e:
        print(f"  [OCR] 识别出错: {e}")
        return ""


async def handle_captcha(page: Page, account_name: str) -> str:
    """处理验证码：仅使用 OCR 自动识别"""
    captcha_dir = DATA_DIR / "captchas"
    captcha_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(MAX_CAPTCHA_RETRIES):
        try:
            captcha_img = page.locator("img[src*='captcha']")
            await captcha_img.wait_for(state="visible", timeout=5000)

            captcha_path = str(captcha_dir / f"{account_name}_captcha_{attempt}.png")
            await captcha_img.screenshot(path=captcha_path)

            # OCR 识别
            code = solve_captcha_with_ocr(captcha_path)
            if code and len(code) >= 4:
                print(f"  [OCR] 识别结果: {code}")
                return code

            print(f"  [验证码] OCR识别失败({attempt+1}/{MAX_CAPTCHA_RETRIES})，刷新重试...")
            # 刷新验证码
            captcha_img = page.locator("img[src*='captcha']")
            try:
                await captcha_img.click()
            except:
                await page.reload()
            await page.wait_for_timeout(2000)

        except PlaywrightTimeout:
            print(f"  [验证码] 未找到验证码图片（可能不需要）")
            return ""
        except Exception as e:
            print(f"  [验证码] 处理出错: {e}")
            await page.wait_for_timeout(1000)

    return ""


# ============================================================
# 登录
# ============================================================

async def login(page: Page, account: dict) -> bool:
    """登录简知分销平台（如果 profile 中已有有效 session 则跳过）"""
    name = account["name"]
    phone = account["phone"]
    password = account["password"]

    print(f"\n{'='*60}")
    print(f"🔐 登录账号: {name} ({phone})")
    print(f"{'='*60}")

    # 先尝试访问首页，检查是否已有有效登录态
    try:
        await page.goto(BASE_URL + "#/product/hot", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)
        current_url = page.url
        if "login" not in current_url.lower():
            print(f"  [登录] ✅ 已有有效登录态（profile 复用），跳过登录！")
            return True
        else:
            print(f"  [登录] 登录态已过期，需要重新登录")
    except Exception:
        print(f"  [登录] 检查登录态失败，执行正常登录")

    for attempt in range(MAX_LOGIN_RETRIES):
        try:
            print(f"  [登录-第{attempt+1}次] 打开登录页...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            phone_input = page.locator("input[placeholder*='手机'], input[type='text']").first
            await phone_input.wait_for(state="visible", timeout=10000)
            await phone_input.fill("")
            await phone_input.fill(phone)
            print(f"  [登录] 已填写手机号: {phone}")

            password_input = page.locator("input[placeholder*='密码'], input[type='password']").first
            await password_input.wait_for(state="visible", timeout=5000)
            await password_input.fill(password)
            print(f"  [登录] 已填写密码")

            # 处理验证码（纯 OCR）
            captcha_code = await handle_captcha(page, name)
            if captcha_code:
                captcha_input = page.locator("input[placeholder*='验证码']")
                if await captcha_input.count() > 0:
                    await captcha_input.fill(captcha_code)
                    print(f"  [登录] 已填写验证码: {captcha_code}")

            login_btn = page.locator("button:has-text('登录'), button:has-text('登 录')").first
            if await login_btn.count() == 0:
                login_btn = page.locator("button[type='submit']").first
            if await login_btn.count() == 0:
                login_btn = page.locator("span:has-text('登 录'), span:has-text('登录')").locator("..").first

            if await login_btn.count() == 0:
                print(f"  [登录] ⚠️ 未找到登录按钮")
                screenshot_path = str(DATA_DIR / f"debug_{name}_login.png")
                await page.screenshot(path=screenshot_path, full_page=True)
                return False

            await login_btn.click()
            print(f"  [登录] 点击登录按钮...")

            await page.wait_for_timeout(3000)
            current_url = page.url

            # 检查错误提示
            error_selectors = [
                ".el-message--error", ".ant-message-error", ".error-tip",
                ".login-error", ".error-message", ".ant-notification-notice-description",
            ]
            for err_sel in error_selectors:
                try:
                    error_msg = page.locator(err_sel).first
                    if await error_msg.count() > 0 and await error_msg.is_visible():
                        text = await error_msg.text_content()
                        if text and text.strip():
                            print(f"  [登录] ❌ 错误提示: {text.strip()}")
                except:
                    pass

            if "login" not in current_url.lower() or "/dms#/" in current_url and "login" not in current_url:
                await page.wait_for_timeout(2000)
                print(f"  [登录] ✅ 登录成功! 当前页面: {current_url}")
                return True

            print(f"  [登录] 登录未成功，URL: {current_url}")
            await page.wait_for_timeout(1000)

        except Exception as e:
            print(f"  [登录] 异常: {e}")
            await page.wait_for_timeout(2000)

    print(f"  [登录] ❌ 账号 {name} 登录失败")
    return False


# ============================================================
# 导航到订单数据页面
# ============================================================

async def navigate_to_orders(page: Page) -> bool:
    """导航到分销管理 - 订单数据页面"""
    print(f"\n  📋 导航到订单数据页面...")

    try:
        possible_urls = [
            f"{BASE_URL}#/channel/order",
            f"{BASE_URL}#/distribution/order",
            f"{BASE_URL}#/order/list",
        ]

        for url in possible_urls:
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await page.wait_for_timeout(2000)
                current = page.url
                page_text = await page.locator("body").text_content() or ""
                if "order" in current.lower() or "订单" in page_text:
                    print(f"  [导航] 通过URL成功到达: {current}")
                    return True
            except:
                continue

        # URL导航失败，尝试通过菜单点击
        print("  [导航] URL导航未成功，尝试通过菜单...")

        dist_menu_selectors = [
            "li:has-text('分销管理')", "div:has-text('分销管理')",
            "span:has-text('分销管理')", ".el-menu-item:has-text('分销')",
            "a:has-text('分销管理')",
        ]

        for selector in dist_menu_selectors:
            try:
                el = page.locator(selector).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(1500)
                    break
            except:
                continue

        order_menu_selectors = [
            "li:has-text('订单数据')", "span:has-text('订单数据')",
            "span:has-text('订单管理')", "a:has-text('订单数据')",
        ]

        for selector in order_menu_selectors:
            try:
                el = page.locator(selector).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(3000)
                    print(f"  [菜单] 点击了订单数据")
                    return True
            except:
                continue

        print("  [导航] ⚠️ 无法找到订单数据页面入口")
        return False

    except Exception as e:
        print(f"  [导航] 异常: {e}")
        return False


# ============================================================
# 导出订单数据
# ============================================================

async def export_orders(page: Page, account_name: str) -> list:
    """导出订单数据"""
    print(f"\n  📊 开始导出订单数据...")

    downloaded_files = []
    account_export_dir = DATA_DIR / account_name
    account_export_dir.mkdir(parents=True, exist_ok=True)

    try:
        await page.wait_for_timeout(3000)

        page_text = await page.locator("body").text_content() or ""
        has_order_page = any(kw in page_text for kw in ["订单", "分销管理", "order"])
        if not has_order_page:
            print(f"  [导出] ⚠️ 当前页面似乎不是订单页面，尝试导航...")
            nav_ok = await navigate_to_orders(page)
            if not nav_ok:
                print(f"  [导出] 导航失败，跳过导出")
                return []

        # 查找导出按钮
        export_selectors = [
            "button:has-text('导出')", "button:has-text('导出数据')",
            "button:has-text('导 出')", "button:has-text('下载')",
            "span:has-text('导出'):not(:has(span))", ".export-btn",
            "[title='导出']", "a:has-text('导出')", "div[role='button']:has-text('导出')",
        ]

        export_btn = None
        for selector in export_selectors:
            try:
                el = page.locator(selector).first
                if await el.count() > 0 and await el.is_visible():
                    export_btn = el
                    break
            except:
                continue

        if export_btn is None:
            all_buttons = page.locator("button")
            try:
                count = await all_buttons.count()
                for i in range(min(count, 50)):
                    try:
                        btn = all_buttons.nth(i)
                        text = await btn.text_content()
                        if text and ("导出" in text or "下载" in text):
                            export_btn = btn
                            break
                    except:
                        continue
            except:
                pass

        if export_btn is None:
            print(f"  [导出] ⚠️ 未找到导出按钮")
            return []

        # 设置下载监听
        downloads = []
        async def handle_download(download):
            try:
                suggested = download.suggested_filename
                save_path = str(account_export_dir / suggested)
                await download.save_as(save_path)
                print(f"  [下载] ✅ 文件已保存: {save_path}")
                downloads.append(save_path)
            except Exception as e:
                print(f"  [下载] 保存失败: {e}")

        page.on("download", handle_download)

        print(f"  [导出] 点击导出按钮...")
        await export_btn.click()
        await page.wait_for_timeout(3000)

        # 可能弹出确认对话框
        try:
            confirm_btn = page.locator("button:has-text('确认'), button:has-text('确定')").first
            if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                await confirm_btn.click()
                await page.wait_for_timeout(3000)
        except:
            pass

        # 等待更多下载
        await page.wait_for_timeout(8000)

        downloaded_files.extend(downloads)

        if not downloaded_files:
            print(f"  [导出] ⚠️ 未检测到下载文件")

    except Exception as e:
        print(f"  [导出] 异常: {e}")
        import traceback
        traceback.print_exc()

    return downloaded_files


# ============================================================
# 主流程
# ============================================================

async def process_account(p: object, account: dict) -> list:
    """处理单个账号的完整流程（使用独立持久化 profile）"""
    name = account["name"]

    profile_dir = str(BROWSER_PROFILES_DIR / name)
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    context = await p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=True,
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",  # GitHub Actions 需要
        ],
    )
    page = await context.new_page()

    try:
        login_success = await login(page, account)
        if not login_success:
            print(f"  ❌ 账号 {name} 登录失败，跳过")
            return []

        nav_success = await navigate_to_orders(page)
        if not nav_success:
            print(f"  ⚠️ 账号 {name} 导航到订单页失败")

        files = await export_orders(page, name)
        return files

    except Exception as e:
        print(f"  ❌ 账号 {name} 处理出错: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        await context.close()


async def main():
    print("=" * 60)
    print("🚀 简知分销平台 - 多账号订单数据导出 (云端版)")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   账号数: {len(ACCOUNTS)}")
    print(f"   下载目录: {DOWNLOAD_DIR}")
    print(f"   环境: {'GitHub Actions' if os.environ.get('GITHUB_ACTIONS') else '本地'}")
    print("=" * 60)

    if not ACCOUNTS:
        print("❌ 无可用账号，请设置 ACCOUNTS_JSON 环境变量")
        return []

    async with async_playwright() as p:
        all_files = []
        for account in ACCOUNTS:
            files = await process_account(p, account)
            all_files.extend(files)
            await asyncio.sleep(2)

    print(f"\n{'='*60}")
    print(f"✅ 全部完成！")
    print(f"   共导出文件: {len(all_files)} 个")
    for f in all_files:
        print(f"   📄 {f}")
    print(f"{'='*60}")

    # 保存结果清单
    result_path = DATA_DIR / "export_results.json"
    result_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "files": all_files,
        "accounts": [a["name"] for a in ACCOUNTS],
    }, ensure_ascii=False, indent=2))
    print(f"📝 结果清单: {result_path}")

    return all_files


if __name__ == "__main__":
    asyncio.run(main())
