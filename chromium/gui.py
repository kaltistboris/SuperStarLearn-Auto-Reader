"""
超星学习通 — 自动阅读 GUI（tkinter 版）
========================================
在独立线程运行 Playwright 异步逻辑，主线程维持 tkinter 界面。
不与 main.py CLI 冲突，两者独立可用。

用法:
    uv run python gui.py
"""

import asyncio
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

# 从 main.py 导入核心组件（不修改 main.py）
from main import (
    DEFAULT_URL, DEFAULT_BROWSER, BROWSER_CHOICES,
    SCROLL_MIN_INTERVAL, SCROLL_MAX_INTERVAL,
    SCROLL_MIN_PX, SCROLL_MAX_PX,
    BACK_PROBABILITY,
    PAUSE_INTERVAL_MIN, PAUSE_INTERVAL_MAX,
    PAUSE_DURATION_MIN, PAUSE_DURATION_MAX,
    Stats, Scroller, schedule_stop,
    launch_browser, _pick_reading_page,
)

# ═══════════════════════════════════════════════════════════════
# 日志重定向：后台线程 print → GUI Text 组件
# ═══════════════════════════════════════════════════════════════

class LogRedirector:
    """拦截 sys.stdout，通过线程安全队列将输出送到 tkinter Text 组件"""

    def __init__(self, widget: tk.Text):
        self.widget = widget
        self.queue: queue.Queue = queue.Queue()
        self._original = sys.stdout

    def write(self, text: str) -> None:
        if text:  # 保留 \n，确保逐行显示
            self.queue.put(text)

    def flush(self) -> None:
        pass  # stdout 兼容接口

    def start(self) -> None:
        sys.stdout = self

    def stop(self) -> None:
        sys.stdout = self._original

    def poll(self) -> None:
        """主线程定时调用，从队列取文本写入 Text 组件"""
        try:
            while True:
                text = self.queue.get_nowait()
                self.widget.insert(tk.END, text)
                self.widget.see(tk.END)  # 自动滚到底部
        except queue.Empty:
            pass


# ═══════════════════════════════════════════════════════════════
# 后台工作线程：运行 Playwright / asyncio
# ═══════════════════════════════════════════════════════════════

class GuiWorker:
    """在独立线程中运行 asyncio 事件循环，执行浏览器自动化全流程"""

    def __init__(self, url: str, duration_min: Optional[float],
                 min_iv: float, max_iv: float,
                 browser_type: str = DEFAULT_BROWSER,
                 tab_index: Optional[int] = None):
        self.url = url
        self.duration_min = duration_min
        self.min_iv = min_iv
        self.max_iv = max_iv
        self.browser_type = browser_type
        self.tab_index = tab_index
        self.scroller: Optional[Scroller] = None
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None
        self._done = False
        # 标签页交互选择
        self._tabs_ready = threading.Event()
        self._tab_chosen = threading.Event()
        self._tab_info: list[tuple[int, str]] = []
        self._selected_tab_idx: Optional[int] = None

    # ── 外部控制接口 ──

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def signal_ready(self) -> None:
        """GUI 点「已就绪」后调用，通知工作线程继续"""
        self._ready.set()

    @property
    def tab_ready(self) -> bool:
        """是否有待选择的标签页列表"""
        return self._tabs_ready.is_set() and not self._tab_chosen.is_set()

    def get_tab_info(self) -> list[tuple[int, str]]:
        return self._tab_info

    def select_tab(self, idx: int) -> None:
        """由 GUI 线程调用，用户选择了标签页"""
        if 0 <= idx < len(self._tab_info):
            self._selected_tab_idx = idx
            self._tab_chosen.set()

    def stop(self) -> None:
        if self.scroller:
            self.scroller.stop()

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def error(self) -> Optional[str]:
        return self._error

    # ── 线程入口 ──

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._core())
        except Exception as e:
            self._error = str(e)
            import traceback
            traceback.print_exc()
        finally:
            loop.close()
            self._done = True

    # ── 核心 async 流程（改编自 main.py 的 run()） ──

    async def _core(self) -> None:
        stats = Stats()
        pw = None
        browser = None

        try:
            # 1. 启动浏览器
            label = {"edge": "Edge", "chrome": "Chrome", "firefox": "Firefox"}.get(self.browser_type, self.browser_type)
            print(f"🚀 启动 {label} 浏览器...")
            pw, browser, ctx, page = await launch_browser(browser_type=self.browser_type)

            # 2. 打开 URL
            print(f"📖 打开 {self.url}")
            try:
                await page.goto(self.url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                print("⚠️  无法自动打开页面，请在浏览器地址栏手动输入学习通网址。")
                try:
                    await page.goto("about:blank")
                except Exception:
                    pass

            # 3. 提示用户手动操作，等待「已就绪」信号
            print()
            print("=" * 44)
            print("  👆 请在浏览器中手动完成：")
            print("     1. 登录你的学习通账号")
            print("     2. 进入阅读任务页面")
            print("     3. 按 F11 全屏（推荐）")
            print("     4. 确保文档已加载")
            print()
            print("  准备好后点击 GUI 中的 [✅ 已就绪] 按钮")
            print("=" * 44)

            # 等待 GUI 信号（最长等 10 分钟）
            if not await self._wait_ready(timeout=600):
                print("⚠️  等待超时，请重新运行")
                return

            print("📍 开始分析页面结构...")
            await asyncio.sleep(2)

            # 4. 标签页选择
            if self.tab_index is not None:
                # 用户在表单指定了序号
                page = await _pick_reading_page(ctx, page, tab_index=self.tab_index)
                if page is None:
                    return
            else:
                all_pages = ctx.pages
                if len(all_pages) == 1:
                    # 只有一个标签页，无需选择
                    print(f"✅ 只有一个标签页，直接使用")
                else:
                    # 多个标签页 → 始终弹窗让用户确认
                    print("📋 请从弹出窗口选择要操作的标签页...")
                    self._tab_info = [(i, p.url or "(空白页)")
                                      for i, p in enumerate(all_pages)]
                    self._tabs_ready.set()
                    if not await self._wait_tab_chosen(timeout=120):
                        print("⚠️  标签页选择超时，请重新运行")
                        return
                    page = all_pages[self._selected_tab_idx]
                    await page.bring_to_front()
                    await asyncio.sleep(0.5)

            # 5. 启动滚动
            self.scroller = Scroller(page, stats,
                                     min_iv=self.min_iv, max_iv=self.max_iv,
                                     min_px=SCROLL_MIN_PX, max_px=SCROLL_MAX_PX,
                                     ctx=ctx)

            if self.duration_min:
                schedule_stop(self.duration_min, self.scroller)

            print(f"📊 间隔 {self.min_iv}~{self.max_iv}s | "
                  f"距离 {SCROLL_MIN_PX}~{SCROLL_MAX_PX}px | "
                  f"回滚 {BACK_PROBABILITY*100:.0f}%")
            print(f"📊 长暂停: 每 {PAUSE_INTERVAL_MIN}~{PAUSE_INTERVAL_MAX}s, "
                  f"持续 {PAUSE_DURATION_MIN}~{PAUSE_DURATION_MAX}s\n")

            await self.scroller.run()

        except Exception as e:
            print(f"\n❌ 异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.scroller:
                self.scroller.stop()
            print(stats.summary())
            if browser:
                try:
                    await asyncio.wait_for(browser.close(), timeout=10)
                except Exception:
                    pass
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            print("👋 已退出")

    async def _wait_ready(self, timeout: float = 600) -> bool:
        """轮询等待 GUI 的「已就绪」信号"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._ready.is_set():
                return True
            await asyncio.sleep(0.5)
        return False

    async def _wait_tab_chosen(self, timeout: float = 120) -> bool:
        """轮询等待 GUI 线程完成标签页选择"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._tab_chosen.is_set():
                return True
            await asyncio.sleep(0.3)
        return False


# ═══════════════════════════════════════════════════════════════
# tkinter GUI 主窗口
# ═══════════════════════════════════════════════════════════════

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("超星学习通 — 自动阅读")
        self.root.geometry("680x560")
        self.root.minsize(520, 400)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.worker: Optional[GuiWorker] = None
        self.redirector: Optional[LogRedirector] = None
        self._poll_id: Optional[str] = None
        self._showing_tab_picker = False

        # 样式
        style = ttk.Style()
        style.theme_use("clam")

        self._build_params()
        self._build_controls()
        self._build_log()
        self._set_state("idle")

    # ── 参数输入区 ──

    def _build_params(self) -> None:
        frame = ttk.LabelFrame(self.root, text="参数设置", padding=10)
        frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        # URL
        ttk.Label(frame, text="URL:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.var_url = tk.StringVar(value=DEFAULT_URL)
        self.entry_url = ttk.Entry(frame, textvariable=self.var_url, width=60)
        self.entry_url.grid(row=0, column=1, columnspan=3, sticky=tk.EW, pady=4, padx=(5, 0))

        # Duration + Browser
        ttk.Label(frame, text="运行时长:").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.var_duration = tk.StringVar(value="")
        self.entry_duration = ttk.Entry(frame, textvariable=self.var_duration, width=8)
        self.entry_duration.grid(row=1, column=1, sticky=tk.W, pady=4, padx=(5, 0))
        ttk.Label(frame, text="分钟").grid(row=1, column=2, sticky=tk.W, pady=4)
        # browser 下拉
        ttk.Label(frame, text="浏览器:").grid(row=1, column=3, sticky=tk.W, pady=4, padx=(15, 0))
        self.var_browser = tk.StringVar(value=DEFAULT_BROWSER)
        self.combo_browser = ttk.Combobox(frame, textvariable=self.var_browser, width=10,
                                          values=BROWSER_CHOICES, state="readonly")
        self.combo_browser.grid(row=1, column=4, sticky=tk.W, pady=4, padx=(5, 0))

        # Min interval
        ttk.Label(frame, text="最小间隔:").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.var_min_iv = tk.StringVar(value=str(SCROLL_MIN_INTERVAL))
        self.entry_min_iv = ttk.Entry(frame, textvariable=self.var_min_iv, width=8)
        self.entry_min_iv.grid(row=2, column=1, sticky=tk.W, pady=4, padx=(5, 0))
        ttk.Label(frame, text="秒").grid(row=2, column=2, sticky=tk.W, pady=4)

        # Max interval
        ttk.Label(frame, text="最大间隔:").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.var_max_iv = tk.StringVar(value=str(SCROLL_MAX_INTERVAL))
        self.entry_max_iv = ttk.Entry(frame, textvariable=self.var_max_iv, width=8)
        self.entry_max_iv.grid(row=3, column=1, sticky=tk.W, pady=4, padx=(5, 0))
        ttk.Label(frame, text="秒").grid(row=3, column=2, sticky=tk.W, pady=4)

        frame.columnconfigure(1, weight=1)

    # ── 控制按钮区 ──

    def _build_controls(self) -> None:
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=10, pady=(0, 5))

        self.btn_start = ttk.Button(bar, text="▶ 启动浏览器", command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_ready = ttk.Button(bar, text="✅ 已就绪，开始滚动",
                                    command=self._on_ready, state=tk.DISABLED)
        self.btn_ready.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_stop = ttk.Button(bar, text="⏹ 停止", command=self._on_stop,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.var_status).pack(side=tk.RIGHT)

    # ── 日志输出区 ──

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self.root, text="运行日志", padding=5)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        self.log_text = tk.Text(frame, wrap=tk.WORD, state=tk.NORMAL,
                                font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                                insertbackground="white")
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL,
                                  command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ── 状态管理 ──

    def _set_state(self, state: str) -> None:
        """统一管理按钮和输入的启用/禁用"""
        if state == "idle":
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_ready.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.DISABLED)
            self._enable_params(True)
            self.var_status.set("就绪")
        elif state == "browser_launched":
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_ready.configure(state=tk.NORMAL)
            self.btn_stop.configure(state=tk.DISABLED)
            self._enable_params(False)
            self.var_status.set("等待登录...")
        elif state == "scrolling":
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_ready.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
            self._enable_params(False)
            self.var_status.set("运行中")
        elif state == "stopping":
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_ready.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.DISABLED)
            self.var_status.set("停止中...")

    def _enable_params(self, enabled: bool) -> None:
        s = tk.NORMAL if enabled else tk.DISABLED
        for w in [self.entry_url, self.entry_duration,
                   self.entry_min_iv, self.entry_max_iv,
                   self.combo_browser]:
            w.configure(state=s)

    # ── 按钮事件 ──

    def _on_start(self) -> None:
        """点击「启动浏览器」"""
        # 收集并校验参数
        url = self.var_url.get().strip()
        if not url:
            messagebox.showerror("参数错误", "URL 不能为空")
            return

        duration_str = self.var_duration.get().strip()
        duration_min = None
        if duration_str:
            try:
                duration_min = float(duration_str)
                if duration_min <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("参数错误", "运行时长必须为正数")
                return

        try:
            min_iv = float(self.var_min_iv.get())
            max_iv = float(self.var_max_iv.get())
        except ValueError:
            messagebox.showerror("参数错误", "间隔必须为数字")
            return
        if min_iv > max_iv:
            messagebox.showerror("参数错误", "最小间隔不能大于最大间隔")
            return
        if min_iv < 1.0:
            messagebox.showerror("参数错误", "最小间隔不能小于 1 秒")
            return

        # 收集浏览器选择
        browser_type = self.var_browser.get()

        # 启动日志重定向
        self.redirector = LogRedirector(self.log_text)
        self.redirector.start()

        # 启动后台工作线程（tab_index=None → 弹窗选择）
        self.worker = GuiWorker(url, duration_min, min_iv, max_iv,
                                browser_type=browser_type, tab_index=None)
        self.worker.start()

        self._set_state("browser_launched")
        self._start_polling()

    def _on_ready(self) -> None:
        """点击「已就绪」"""
        if self.worker:
            self.worker.signal_ready()
        self._set_state("scrolling")

    def _on_stop(self) -> None:
        """点击「停止」"""
        self._set_state("stopping")
        if self.worker:
            self.worker.stop()

    def _on_close(self) -> None:
        """窗口关闭"""
        if self.worker:
            self.worker.stop()
        if self.redirector:
            self.redirector.stop()
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()

    # ── 日志轮询 ──

    def _start_polling(self) -> None:
        """开始每 150ms 轮询日志队列和工作线程状态"""
        self._poll()

    def _poll(self) -> None:
        if self.redirector:
            self.redirector.poll()

        # 检测是否需要弹出标签页选择窗口
        if (self.worker and self.worker.tab_ready
                and not self._showing_tab_picker):
            self._showing_tab_picker = True
            self._show_tab_picker()

        # 检查工作线程是否结束
        if self.worker and self.worker.is_done:
            self._showing_tab_picker = False
            self._on_worker_done()
            return

        self._poll_id = self.root.after(150, self._poll)

    def _on_worker_done(self) -> None:
        """工作线程结束后的清理"""
        if self.redirector:
            self.redirector.stop()
        self._showing_tab_picker = False
        self._set_state("idle")
        self.worker = None
        self.redirector = None

    # ── 标签页选择对话框 ──

    def _show_tab_picker(self) -> None:
        """弹出标签页选择窗口"""
        if not self.worker:
            self._showing_tab_picker = False
            return

        tabs = self.worker.get_tab_info()
        if not tabs:
            self._showing_tab_picker = False
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("选择标签页")
        dialog.geometry("520x360")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)  # 禁止直接关

        ttk.Label(dialog, text="当前打开了多个页面，请选择要操作的标签页：",
                  wraplength=480).pack(pady=(10, 5), padx=10, anchor=tk.W)

        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        listbox = tk.Listbox(frame, font=("Consolas", 10),
                             selectbackground="#0078d4")
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL,
                                  command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)

        for i, (idx, url) in enumerate(tabs):
            display = f"  [{idx + 1}]  {url[:72]}"
            listbox.insert(tk.END, display)

        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.selection_set(0)

        def on_ok():
            sel = listbox.curselection()
            if sel:
                self.worker.select_tab(sel[0])
                dialog.destroy()
                self._showing_tab_picker = False

        def on_cancel():
            if self.worker:
                self.worker.select_tab(0)
                dialog.destroy()
                self._showing_tab_picker = False
            else:
                dialog.destroy()
                self._showing_tab_picker = False

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="取消（默认第 1 个）", command=on_cancel).pack(side=tk.RIGHT)

    # ── 启动 ──

    def run(self) -> None:
        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    App().run()
