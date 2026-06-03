"""
超星学习通 — 自动阅读脚本（全屏滚轮版）
=========================================
核心原理：page.mouse.wheel() 模拟鼠标滚轮。
浏览器自动将滚轮事件路由到鼠标下方元素，天然支持跨 iframe。

用法:
    uv run python main.py                              # 默认 Edge
    uv run python main.py --browser chrome              # 使用 Chrome
    uv run python main.py --duration 30                 # 30 分钟后自动停止
    uv run python main.py --tab 2                       # 选择第 3 个标签页

依赖: playwright (channel=msedge/chrome, 使用系统 Edge/Chrome 浏览器)
"""

import asyncio
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

DEFAULT_URL = "https://mooc1.chaoxing.com"
DEFAULT_BROWSER = "edge"
BROWSER_CHOICES = ["edge", "chrome"]

# 滚动
SCROLL_MIN_INTERVAL = 2.0       # 滚动间隔下限（秒）
SCROLL_MAX_INTERVAL = 8.0       # 滚动间隔上限（秒）
SCROLL_MIN_PX = 100             # 每次滚动最小像素
SCROLL_MAX_PX = 500             # 每次滚动最大像素
BACK_PROBABILITY = 0.12         # 回滚概率（模拟回看）
BACK_MIN_PX = 50
BACK_MAX_PX = 200
WHEEL_STEP = 80                 # 鼠标滚轮单步步长（px）

# 长暂停（模拟思考/走神）
PAUSE_INTERVAL_MIN = 180        # 每 3~5 分钟
PAUSE_INTERVAL_MAX = 300
PAUSE_DURATION_MIN = 15         # 暂停 15~45 秒
PAUSE_DURATION_MAX = 45

# ═══════════════════════════════════════════════════════════════
# 反检测脚本
# ═══════════════════════════════════════════════════════════════

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ];
        p.item = i => p[i]; p.namedItem = n => p.find(x => x.name === n); p.refresh = () => {};
        return p;
    },
});
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
delete window.callPhantom;
delete window._phantom;
delete window.__phantomas;
"""

# ═══════════════════════════════════════════════════════════════
# 统计
# ═══════════════════════════════════════════════════════════════

@dataclass
class Stats:
    start: datetime = field(default_factory=datetime.now)
    scrolls: int = 0
    down_px: int = 0
    up_px: int = 0
    paused_sec: float = 0.0

    @property
    def elapsed(self) -> float:
        return (datetime.now() - self.start).total_seconds()

    def summary(self) -> str:
        s = self.elapsed
        h, m = int(s // 3600), int((s % 3600) // 60)
        sec = int(s % 60)
        net = self.down_px - self.up_px
        lines = [
            "=" * 44,
            f"  运行时长:     {h:02d}:{m:02d}:{sec:02d}",
            f"  滚动次数:     {self.scrolls}",
            f"  向下/向上:     {self.down_px} / {self.up_px} px",
            f"  净滚动:        {net} px",
            f"  累计暂停:      {self.paused_sec:.0f} 秒",
            "=" * 44,
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 核心：滚动引擎
# ═══════════════════════════════════════════════════════════════

class Scroller:
    """类人滚轮滚动引擎"""

    def __init__(self, page: Page, stats: Stats,
                 min_iv: float = SCROLL_MIN_INTERVAL,
                 max_iv: float = SCROLL_MAX_INTERVAL,
                 min_px: int = SCROLL_MIN_PX,
                 max_px: int = SCROLL_MAX_PX,
                 ctx=None):
        self.page = page
        self.ctx = ctx
        self.stats = stats
        self.min_iv = min_iv
        self.max_iv = max_iv
        self.min_px = min_px
        self.max_px = max_px
        self._running = False
        self._last_pause = time.time()

    async def run(self) -> None:
        self._running = True
        print("▶️  自动滚动已启动\n")
        await self._safe_center_mouse()
        while self._running:
            await self._maybe_pause()
            ok = await self._scroll_once()
            if not ok:
                break
            await self._sleep(random.uniform(self.min_iv, self.max_iv))
            await self._safe_jiggle()

    def stop(self) -> None:
        self._running = False

    # ── 鼠标定位 ──

    async def _safe_center_mouse(self) -> None:
        try:
            vp = self.page.viewport_size
            if vp:
                await self.page.mouse.move(vp["width"] // 2, vp["height"] // 3, steps=5)
        except Exception:
            pass  # 页面可能尚未就绪，不影响后续滚动

    # ── 滚动 ──

    async def _scroll_once(self) -> bool:
        """返回 False 表示页面已关闭且无法恢复，应停止"""
        try:
            if random.random() < BACK_PROBABILITY:
                px = random.randint(BACK_MIN_PX, BACK_MAX_PX)
                await self._wheel(-px)
                self.stats.up_px += px
                print(f"  ⬆️  回滚 {px} px")
            else:
                px = random.randint(self.min_px, self.max_px)
                await self._wheel(px)
                self.stats.down_px += px
                print(f"  ⬇️  下滚 {px} px")
            self.stats.scrolls += 1
            return True
        except Exception as e:
            msg = str(e).lower()
            if "closed" in msg or "target" in msg:
                # 尝试从 context 恢复：找另一个打开的阅读页
                if self.ctx:
                    recovered = await _try_recover_page(self.ctx, self.page)
                    if recovered:
                        self.page = recovered
                        print("  ♻️  已切换到另一个阅读标签页\n")
                        return True
                print("\n⚠️  阅读页面已关闭，停止滚动")
                return False
            print(f"  ⚠️  滚动异常: {e}")
            return True  # 非致命错误，继续

    async def _wheel(self, total_px: int) -> None:
        steps = max(1, abs(total_px) // WHEEL_STEP)
        delta = total_px / steps
        for _ in range(steps):
            if not self._running:
                break
            await self.page.mouse.wheel(0, delta)
            await asyncio.sleep(0.05)

    # ── 长暂停 ──

    async def _maybe_pause(self) -> None:
        if time.time() - self._last_pause < random.uniform(PAUSE_INTERVAL_MIN, PAUSE_INTERVAL_MAX):
            return
        dur = random.uniform(PAUSE_DURATION_MIN, PAUSE_DURATION_MAX)
        print(f"\n  💤 长暂停 {dur:.0f} 秒（模拟走神）...\n")
        self.stats.paused_sec += dur
        self._last_pause = time.time()
        await self._sleep(dur)

    # ── 微操作 ──

    async def _safe_jiggle(self) -> None:
        if random.random() >= 0.3:
            return
        try:
            vp = self.page.viewport_size
            if vp:
                x = random.randint(100, vp["width"] - 100)
                y = random.randint(100, vp["height"] - 100)
                await self.page.mouse.move(x, y, steps=random.randint(3, 6))
        except Exception:
            pass

    # ── 可中断 sleep ──

    async def _sleep(self, sec: float) -> None:
        while sec > 0 and self._running:
            await asyncio.sleep(min(sec, 1.0))
            sec -= 1.0


# ═══════════════════════════════════════════════════════════════
# 浏览器启动
# ═══════════════════════════════════════════════════════════════

async def launch_browser(browser_type: str = "edge"):
    """
    启动指定浏览器并配置反检测上下文。
    支持: edge, chrome
    """
    ua_map = {
        "edge": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        "chrome": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    channel_map = {"edge": "msedge", "chrome": "chrome"}

    user_agent = ua_map[browser_type]
    channel = channel_map[browser_type]

    pw = await async_playwright().start()

    browser = await pw.chromium.launch(
        channel=channel,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )

    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=user_agent,
        bypass_csp=True,
    )
    await context.add_init_script(STEALTH_JS)
    page = await context.new_page()
    return pw, browser, context, page


# ═══════════════════════════════════════════════════════════════
# 定时停止（后台线程）
# ═══════════════════════════════════════════════════════════════

def schedule_stop(duration_min: float, scroller: Scroller):
    import threading

    def _waiter():
        time.sleep(duration_min * 60)
        if scroller._running:
            print(f"\n⏰ 定时 {duration_min:.0f} 分钟已到，自动停止...")
            scroller.stop()

    t = threading.Thread(target=_waiter, daemon=True)
    t.start()
    print(f"⏰ 已设置定时: {duration_min:.0f} 分钟后自动停止")


# ═══════════════════════════════════════════════════════════════
# 智能页面选择
# ═══════════════════════════════════════════════════════════════

READING_PAGE_MARKERS = ["studentstudy", "mycourse", "chapterId", "courseId",
                         "mooc2-ans", "panview", "knowledge"]


async def _try_recover_page(ctx, closed_page: Page) -> Optional[Page]:
    """页面被关闭时，尝试在其他标签页中找到阅读页"""
    for p in ctx.pages:
        if p == closed_page:
            continue
        try:
            purl = (p.url or "").lower()
            if any(m in purl for m in READING_PAGE_MARKERS):
                await p.bring_to_front()
                await asyncio.sleep(0.3)
                return p
        except Exception:
            continue
    return None


async def _pick_reading_page(ctx, current_page: Page,
                              tab_index: Optional[int] = None) -> Optional[Page]:
    """
    智能选择阅读页面：
    1. 如果指定了 --tab，按标签页位置选择（0-base）
    2. 如果当前页面已是阅读页，直接返回
    3. 扫描所有标签页，列出候选让用户交互选择
    4. 找不到则提示
    """
    all_pages: list[Page] = ctx.pages
    current_url = (current_page.url or "").lower()

    # ═══ 1. --tab 模式 ═══
    if tab_index is not None:
        if tab_index < 0 or tab_index >= len(all_pages):
            print(f"❌ --tab {tab_index} 超出范围，共有 {len(all_pages)} 个标签页")
            return None
        chosen = all_pages[tab_index]
        await chosen.bring_to_front()
        await asyncio.sleep(0.5)
        print(f"✅ 已切换到标签页 [{tab_index}]: {chosen.url[:80]}")
        return chosen

    # ═══ 2. 当前页面就是阅读页 ═══
    if any(marker in current_url for marker in READING_PAGE_MARKERS):
        print(f"✅ 当前页面已是阅读页: {current_page.url[:80]}")
        return current_page

    # ═══ 3. 扫描所有标签页 ═══
    print("📍 扫描所有标签页...")
    candidates = []
    for p in all_pages:
        purl = (p.url or "").lower()
        if any(marker in purl for marker in READING_PAGE_MARKERS):
            candidates.append(p)

    print(f"   共 {len(all_pages)} 个标签页，{len(candidates)} 个匹配阅读任务")

    if len(candidates) == 0:
        print("⚠️  未找到阅读页面！")
        print("   请在浏览器中打开阅读任务页面，然后重新运行。")
        print("   也可用 --tab N 指定标签页位置（0-base，如 --tab 2）")
        return None

    # 打印所有标签页
    print()
    print("  ┌─ 标签页列表 ──────────────────────────")
    for i, p in enumerate(all_pages):
        purl = (p.url or "").lower()
        tag = " ● 匹配" if any(m in purl for m in READING_PAGE_MARKERS) else ""
        print(f"  │ [{i}] {p.url[:80]}{tag}")
    print("  └─────────────────────────────────────────")
    print()

    if len(candidates) == 1:
        chosen = candidates[0]
        await chosen.bring_to_front()
        await asyncio.sleep(0.5)
        print(f"✅ 自动切换到唯一匹配标签页: {chosen.url[:80]}")
        return chosen

    # ═══ 4. 多个匹配 → 交互式选择 ═══
    print(f"⚠️  发现 {len(candidates)} 个匹配标签页，请选择：")
    for p in candidates:
        idx = all_pages.index(p)
        print(f"   [{idx}] {p.url[:80]}")

    selected = await _interactive_pick(all_pages)
    if selected is not None:
        return selected
    print("  ⏭️  跳过，使用当前页面继续")
    return current_page if current_page.url != "about:blank" else candidates[0]


async def _interactive_pick(all_pages: list) -> Optional[Page]:
    """交互式选择标签页。返回选中的 Page，或 None 表示跳过"""
    while True:
        try:
            choice = await asyncio.get_event_loop().run_in_executor(
                None, input,
                "  输入序号 (0~{}), 或按 Enter 跳过: ".format(len(all_pages) - 1)
            )
            if choice.strip() == "":
                return None
            idx = int(choice.strip())
            if 0 <= idx < len(all_pages):
                chosen = all_pages[idx]
                await chosen.bring_to_front()
                await asyncio.sleep(0.5)
                print(f"✅ 已切换到标签页 [{idx}]: {chosen.url[:80]}")
                return chosen
            print(f"   ❌ 序号超出范围（0~{len(all_pages)-1}）")
        except ValueError:
            print("   ❌ 请输入数字")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

async def run(url: str, duration_min: Optional[float] = None,
              min_iv: float = SCROLL_MIN_INTERVAL,
              max_iv: float = SCROLL_MAX_INTERVAL,
              tab_index: Optional[int] = None,
              browser_type: str = DEFAULT_BROWSER):
    stats = Stats()
    pw = None
    browser: Optional[Browser] = None
    scroller: Optional[Scroller] = None

    def on_exit(*_):
        if scroller:
            scroller.stop()

    signal.signal(signal.SIGINT, lambda *_: on_exit())

    try:
        browser_label = {"edge": "Edge", "chrome": "Chrome"}.get(browser_type, browser_type)
        print(f"🚀 启动 {browser_label} 浏览器...")

        pw, browser, ctx, page = await launch_browser(browser_type=browser_type)

        print(f"📖 打开 {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"⚠️  自动导航失败: {e}\n   请在浏览器中手动输入学习通网址。")
            try:
                await page.goto("about:blank")
            except Exception:
                pass

        print()
        print("=" * 44)
        print("  👆 请在浏览器中手动完成：")
        print("     1. 输入学习通网址并登录")
        print("     2. 进入阅读任务页面")
        print("     3. 切换到全屏模式（F11）— 推荐")
        print("     4. 确保文档内容已加载")
        print()
        print("  准备好后按 Enter 开始自动滚动...")
        print("=" * 44)

        await asyncio.get_event_loop().run_in_executor(None, input)

        # 等内容渲染
        await asyncio.sleep(2)

        # 智能选择页面：如果当前是个人空间，自动切到阅读页
        page = await _pick_reading_page(ctx, page, tab_index=tab_index)
        if page is None:
            print("❌ 未找到阅读页面，请确保已打开阅读任务页面后重试")
            return

        scroller = Scroller(page, stats, min_iv=min_iv, max_iv=max_iv, ctx=ctx)

        if duration_min:
            schedule_stop(duration_min, scroller)

        print(f"📊 间隔 {min_iv}~{max_iv}s | "
              f"距离 {SCROLL_MIN_PX}~{SCROLL_MAX_PX}px | "
              f"回滚 {BACK_PROBABILITY*100:.0f}%")
        print(f"📊 长暂停: 每 {PAUSE_INTERVAL_MIN}~{PAUSE_INTERVAL_MAX}s, "
              f"持续 {PAUSE_DURATION_MIN}~{PAUSE_DURATION_MAX}s\n")

        await scroller.run()

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if scroller:
            scroller.stop()
        print(stats.summary())
        if browser:
            try:
                await asyncio.wait_for(browser.close(), timeout=10)
            except Exception:
                pass
        # 显式停止 playwright，避免 pipe 泄漏警告
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        print("👋 已退出")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="超星学习通自动阅读 — 全屏滚轮版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  uv run python main.py\n"
               "  uv run python main.py --browser chrome\n"
               "  uv run python main.py --duration 30\n"
               "  uv run python main.py --tab 2",
    )
    p.add_argument("--duration", "-d", type=float, default=None,
                   help="运行时长（分钟），不指定则手动 Ctrl+C 停止")
    p.add_argument("--url", "-u", type=str, default=DEFAULT_URL,
                   help=f"起始 URL（默认: {DEFAULT_URL}）")
    p.add_argument("--min-interval", type=float, default=SCROLL_MIN_INTERVAL,
                   help=f"最小滚动间隔秒数（默认: {SCROLL_MIN_INTERVAL}）")
    p.add_argument("--max-interval", type=float, default=SCROLL_MAX_INTERVAL,
                   help=f"最大滚动间隔秒数（默认: {SCROLL_MAX_INTERVAL}）")
    p.add_argument("--tab", "-t", type=int, default=None,
                   help="标签页序号（0-base），不指定则自动匹配或交互选择")
    p.add_argument("--browser", "-b", type=str, default=DEFAULT_BROWSER,
                   choices=BROWSER_CHOICES,
                   help=f"浏览器（默认: {DEFAULT_BROWSER}）")
    args = p.parse_args()

    if args.min_interval > args.max_interval:
        print("❌ --min-interval 不能大于 --max-interval")
        sys.exit(1)
    if args.min_interval < 1.0:
        print("❌ --min-interval 不能小于 1 秒")
        sys.exit(1)

    print("=" * 44)
    print("  超星学习通 — 自动阅读脚本（全屏滚轮版）")
    print("=" * 44)

    asyncio.run(run(
        url=args.url,
        duration_min=args.duration,
        min_iv=args.min_interval,
        max_iv=args.max_interval,
        tab_index=args.tab,
        browser_type=args.browser,
    ))


if __name__ == "__main__":
    main()
