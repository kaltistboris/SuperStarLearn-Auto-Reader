# 🔬 学习通自动阅读脚本 — 技术原理与知识指南

> 本文档对应 `main.py` v3 全屏滚轮版（~400 行）。
> 覆盖 `page.mouse.wheel()` 跨 iframe 滚动、反检测、类人行为模拟、异步编程、智能页面切换等核心知识点。

---

## 目录

1. [浏览器自动化原理](#1-浏览器自动化原理)
2. [核心：mouse.wheel() 跨 iframe 滚动](#2-核心mousewheel-跨-iframe-滚动)
3. [反检测技术](#3-反检测技术)
4. [类人行为模拟](#4-类人行为模拟)
5. [智能页面切换与故障恢复](#5-智能页面切换与故障恢复)
6. [定时停止（daemon 线程）](#6-定时停止daemon-线程)
7. [异步编程模型](#7-异步编程模型)
8. [关键代码逐段解析](#8-关键代码逐段解析)
9. [扩展阅读与参考](#9-扩展阅读与参考)

---

## 1. 浏览器自动化原理

### 1.1 三大主流工具对比

| 特性 | Playwright | Selenium | Puppeteer |
|------|-----------|----------|-----------|
| 开发商 | Microsoft | 开源社区 | Google |
| 浏览器支持 | Chromium, Firefox, WebKit | 所有主流浏览器 | Chromium, Firefox |
| 自动等待 | ✅ 内置 | ❌ 需手动 | ❌ 需手动 |
| 移动端模拟 | ✅ 内置 | ❌ 需第三方 | ✅ 内置 |
| 网络拦截 | ✅ `page.route()` | ❌ 需代理 | ✅ |
| 多标签页管理 | ✅ `context.pages` | ❌ 需 `window_handles` | ✅ |

**为什么本项目选择 Playwright？**

1. **`channel="msedge"`**：直接使用系统自带 Edge，无需额外下载浏览器内核，且指纹更自然
2. **Python 异步原生支持**：配合 `asyncio`，长时间运行的脚本不会阻塞
3. **多标签页 API 完善**：`context.pages` 和 `page.bring_to_front()` 让标签页切换非常简洁

### 1.2 Playwright 架构：Browser / Context / Page 三层模型

```
┌─────────────────────────────────────────┐
│              Browser                     │
│  ┌─────────────────────────────────────┐│
│  │         BrowserContext              ││
│  │  ┌──────────┐  ┌──────────┐        ││
│  │  │  Page 1  │  │  Page 2  │  ...   ││
│  │  └──────────┘  └──────────┘        ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

- **Browser**：浏览器进程，通过 CDP 协议通信
- **BrowserContext**：浏览器会话，独立的 Cookie / Storage，所有标签页共享
- **Page**：一个标签页

### 1.3 CDP 协议简介

CDP (Chrome DevTools Protocol) 是 Chromium 内核的调试协议。Playwright 通过 WebSocket 发送 CDP 指令。本项目滚轮滚动本质上发的是 `Input.dispatchMouseEvent`（wheel 类型）。

---

## 2. 核心：mouse.wheel() 跨 iframe 滚动

### 2.1 为什么不用 JS scrollBy？

v1/v2 版本试图用 `frame.evaluate("window.scrollBy(...)")` 在 iframe 内滚动。这带来三大问题：

| 问题 | 说明 |
|------|------|
| **iframe 引用失效** | 学习通动态销毁/重建 iframe，刚拿到的 `Frame` 对象瞬间变成 `Target closed` |
| **跨域限制** | iframe 可能来自 `mooc2-ans.chaoxing.com`，与主页面不同源 |
| **PDF 图片模式** | 学习通把 PDF 拆成 `<img>` 标签而非文本，`scrollHeight` 计算不准 |

### 2.2 mouse.wheel() 原理

```python
await page.mouse.wheel(0, delta_y)  # delta_y > 0 向下滚，< 0 向上滚
```

`page.mouse.wheel()` 在 **CDP 层面** 发送 `Input.dispatchMouseEvent`（type=wheel）。浏览器内核接收后，自动查找鼠标坐标下方的 DOM 元素，将滚轮事件路由过去。

**关键优势**：
- **天然跨 iframe**：浏览器内核的路由机制不关心元素在哪一层 iframe，只管"鼠标下面是什么"
- **不依赖 Frame 引用**：不需要 `frame_locator()`、不需要 `content_frame()`
- **事件级真实**：和真人滚鼠标滚轮产生的浏览器事件完全一致

### 2.3 分步滚动模拟真实滚轮

```python
WHEEL_STEP = 80  # 单步 80px

async def _wheel(self, total_px: int) -> None:
    steps = max(1, abs(total_px) // WHEEL_STEP)  # 300px → 3~4 步
    delta = total_px / steps
    for _ in range(steps):
        await self.page.mouse.wheel(0, delta)
        await asyncio.sleep(0.05)  # 50ms 间隔，模拟物理滚轮惯性
```

真人滚轮一次通常滚 80~120px（取决于系统设置）。把 300px 拆成 3~4 次 50ms 间隔的小滚轮，产生的浏览器事件序列更接近真人。

### 2.4 为什么全屏模式推荐？

全屏（F11）下：
- 文档占据更多视口面积，鼠标更容易落在内容区
- 减少浏览器 chrome UI（标签栏、地址栏）对滚轮事件的干扰
- 学习通阅读任务可能检测窗口焦点/全屏状态

---

## 3. 反检测技术

### 3.1 网站如何检测自动化？

| 检测点 | 正常浏览器 | 裸奔自动化工具 |
|--------|-----------|-------------|
| `navigator.webdriver` | `undefined` / `false` | `true` |
| `window.chrome` | `{runtime: {}, ...}` | `undefined` |
| `navigator.plugins` | PDF Viewer 等 | `[]` 空数组 |
| `navigator.languages` | `['zh-CN', 'zh', 'en']` | `[]` |
| `navigator.hardwareConcurrency` | 4 / 8 / 16 | 0 |
| 鼠标轨迹 | 自然曲线 | 瞬间位移 |
| 滚动事件 | 非匀速 | 匀速机械 |

### 3.2 本项目策略

#### 策略 1：注入 Stealth JS（add_init_script）

```python
await context.add_init_script(STEALTH_JS)
```

`add_init_script` 确保在**每个页面加载之前**执行，在网站 JS 运行前覆盖属性。

核心原理：`Object.defineProperty(navigator, 'webdriver', { get: () => false })` — 用 getter 拦截读取，始终返回 `false`。

#### 策略 2：真实浏览器 + 非无头

```python
await pw.chromium.launch(
    channel="msedge",   # 系统 Edge，非 Playwright 自带 Chromium
    headless=False,     # 可见窗口
    args=["--disable-blink-features=AutomationControlled"],
)
```

无头模式的 `navigator.webdriver` 必然为 `true`，且缺少 GPU/字体渲染指纹。`--disable-blink-features=AutomationControlled` 禁用 Blink 引擎内置的自动化标记。

#### 策略 3：真实浏览器上下文

```python
context = await browser.new_context(
    viewport={"width": 1366, "height": 768},  # 常见笔记本分辨率
    locale="zh-CN",
    timezone_id="Asia/Shanghai",
    user_agent="...Edg/131.0.0.0",            # 真实 Edge UA
)
```

每个参数都在伪装"一台正常的中国用户 Windows 笔记本"。

### 3.3 行为层反检测

属性伪装只能过第一关。如果平台做**行为分析**（滚动加速度分布、鼠标移动熵值、暂停模式），需要类人行为模拟来对抗——见下一章。

---

## 4. 类人行为模拟

### 4.1 随机化策略

本项目的所有时间参数都使用**均匀分布**（`random.uniform`）：

```python
interval = random.uniform(2, 8)   # 滚动间隔 2~8 秒
distance = random.randint(100, 500)  # 滚动距离 100~500px
```

**为什么用均匀分布而非正态/指数分布？**

| 分布 | 优点 | 缺点 |
|------|------|------|
| 均匀 | 简单、可控、不会极端值 | 不够"自然" |
| 指数 | 短间隔高频（更真实） | 可能出现极长间隔 |
| 正态 | 集中在均值附近 | 尾部行为不够丰富 |

当前选择均匀分布的原因：**简单可理解，对抗当前学习通的检测足够有效**。

### 4.2 回滚行为（12% 概率）

```python
BACK_PROBABILITY = 0.12
```

每次滚动前生成随机数，若 < 0.12 则向上回滚 50~200px，模拟"回看刚读过的内容"。

心理学依据：Miller's Law（短时记忆 7±2 组块），读者意识到没理解时会本能往回翻。

### 4.3 长暂停（每 3~5 分钟，暂停 15~45 秒）

```python
PAUSE_INTERVAL_MIN = 180  # 3 分钟
PAUSE_INTERVAL_MAX = 300  # 5 分钟
PAUSE_DURATION_MIN = 15   # 15 秒
PAUSE_DURATION_MAX = 45   # 45 秒
```

没有人能连续数小时匀速阅读。长暂停模拟接电话、喝水、思考。平台检测到连续 30 分钟无暂停就会触发怀疑。

### 4.4 鼠标微操作（30% 概率）

```python
async def _safe_jiggle(self) -> None:
    if random.random() >= 0.3:  # 30% 概率
        return
    await self.page.mouse.move(x, y, steps=random.randint(3, 6))
```

`steps=N` 参数让鼠标分步移动（3~6 步），产生简短的虚拟轨迹而非瞬间跳转。

---

## 5. 智能页面切换与故障恢复

### 5.1 问题背景

学习通的工作流：登录 → 个人空间 (`i.chaoxing.com/base`) → 点课程 → 新标签页打开阅读页 (`mooc1-1.chaoxing.com/mycourse/studentstudy?...`)。

如果脚本绑在个人空间标签页上滚动，不仅无效，关掉它还会崩溃。

### 5.2 阅读页自动识别

```python
READING_PAGE_MARKERS = ["studentstudy", "mycourse", "chapterId",
                         "courseId", "mooc2-ans", "panview", "knowledge"]
```

`_pick_reading_page` 的逻辑：

```
用户按 Enter 后
  ├─ 当前页 URL 含 studentstudy/mycourse 等 → ✅ 直接用
  ├─ 当前是个人空间 (i.chaoxing.com/base) → 扫描所有标签页
  │   ├─ 找到阅读页 → bring_to_front() + 切换
  │   └─ 找不到 → ⚠️ 提示用户
  └─ 其他情况 → ⚠️ 提示用户
```

关键技术：`context.pages` 获取所有标签页列表，`p.bring_to_front()` 激活指定标签页。

### 5.3 运行时页面关闭自动恢复

```python
async def _scroll_once(self) -> bool:
    try:
        await self._wheel(px)
    except Exception as e:
        if "closed" in str(e).lower():
            recovered = await _try_recover_page(self.ctx, self.page)
            if recovered:
                self.page = recovered  # 切换到另一个阅读页
                return True
            return False  # 全关了，停止
```

`_try_recover_page` 在 `context.pages` 中找另一个含 `studentstudy` 等关键词的标签页。找到就自动切过去继续滚，找不到就优雅退出。

这对学习通很实用：如果你手动关阅读页去回消息，只要课程页面还开着，脚本就能自动恢复。

---

## 6. 定时停止（daemon 线程）

### 6.1 为什么用后台线程？

定时停止是一个**阻塞等待**操作（`time.sleep(duration * 60)`）。如果在 async 主循环中 `await asyncio.sleep(...)`，会阻塞滚动循环本身。

解决方案：用 `threading.Thread(daemon=True)` 启动一个独立线程等待，时间到了调用 `scroller.stop()`。

```python
def schedule_stop(duration_min: float, scroller: Scroller):
    def _waiter():
        time.sleep(duration_min * 60)  # 阻塞等待（不干扰 async）
        if scroller._running:
            scroller.stop()             # 设置标志位

    t = threading.Thread(target=_waiter, daemon=True)
    t.start()
```

### 6.2 daemon 线程的特点

`daemon=True` 意味着：
- 主线程退出时，daemon 线程**自动被杀死**，不用手动 join
- 适合"定时回调"这类不持有资源的辅助任务

### 6.3 与可中断 sleep 的协作

```python
async def _sleep(self, sec: float) -> None:
    while sec > 0 and self._running:  # ← daemon 线程设置 _running=False
        await asyncio.sleep(min(sec, 1.0))
        sec -= 1.0
```

`_sleep` 每秒检查一次 `self._running`。daemon 线程调用 `stop()` 后，最长 1 秒内滚动循环就会退出。这保证了定时停止的**响应延迟 ≤ 1 秒**。

---

## 7. 异步编程模型

### 7.1 为什么用异步？

本脚本需要**长时间运行**（可能数小时）且**大部分时间在等待**（滚动间隔）。如果用同步 `time.sleep()`，整个程序在等待期间冻结，无法响应 Ctrl+C。

| 问题 | 同步 | 异步 |
|------|------|------|
| `sleep(5)` 期间 CPU | 线程阻塞 | 释放给事件循环 |
| Ctrl+C 响应 | 延迟到 sleep 结束 | 最多 1 秒 |
| 并发操作 | 需多线程 | 协程天然并发 |

### 7.2 协程与 await

```python
async def hello():
    return "world"

result = hello()       # → coroutine 对象，不执行
result = await hello() # → 等待执行完毕，得到 "world"
```

`await` 的含义：**"我不需要 CPU 了，事件循环去处理其他任务，这个操作完成后回来继续"**。

### 7.3 可中断 sleep 模式

```python
async def _sleep(self, sec: float) -> None:
    while sec > 0 and self._running:
        await asyncio.sleep(min(sec, 1.0))  # 每次最多睡 1 秒
        sec -= 1.0
```

把 60 秒拆成 60 个 1 秒片段。每秒醒来检查 `self._running`，一旦被 `stop()` 设为 `False` 就立即退出。

### 7.4 信号处理

```python
signal.signal(signal.SIGINT, lambda *_: on_exit())
```

`SIGINT` 是 Ctrl+C 信号。信号处理器**只在主线程运行**，而 Playwright 在事件循环中。所以处理器只设置 `stop()` 标志，由事件循环中的 `_sleep` 检测并退出。

---

## 8. 关键代码逐段解析

### 8.1 整体架构

```
main()
  ├─ argparse 解析 --duration / --url / --min-interval / --max-interval
  └─ asyncio.run(run(...))
       ├─ launch_browser()                → pw, browser, context, page
       ├─ page.goto(url)                  → 尝试打开学习通
       ├─ input()                         → 等待用户手动登录 + 导航
       ├─ _pick_reading_page(ctx, page)   → 智能选阅读标签页
       ├─ schedule_stop(duration, scroller) → daemon 线程定时器
       └─ scroller.run()                  → 主滚动循环
            ├─ _maybe_pause()             → 长暂停检测
            ├─ _scroll_once()             → 滚一次（含故障恢复）
            │    └─ _wheel(px)            → 分步 wheel()
            ├─ _sleep(interval)           → 可中断等待
            └─ _safe_jiggle()             → 鼠标微动
```

### 8.2 Scroller 类

```python
class Scroller:
    def __init__(self, page, stats, min_iv, max_iv, min_px, max_px, ctx=None):
```

| 参数 | 作用 |
|------|------|
| `page` | 当前操作的 Page 对象（可能被 `_try_recover_page` 替换） |
| `ctx` | BrowserContext 引用 — 用于页面关闭时扫描其他标签页 |
| `stats` | Stats 实例 — 累加滚动次数/距离/暂停时间 |
| `min_iv / max_iv` | 滚动间隔范围 |
| `min_px / max_px` | 滚动距离范围 |
| `_running` | 控制标志 — `stop()` 设为 False |
| `_last_pause` | 上次长暂停时间戳 |

### 8.3 _scroll_once 的容错设计

```python
async def _scroll_once(self) -> bool:
    try:
        # ... 正常滚动逻辑 ...
        return True
    except Exception as e:
        if "closed" in str(e).lower():
            recovered = await _try_recover_page(self.ctx, self.page)
            if recovered:
                self.page = recovered   # ← 关键：替换 page 引用
                return True             # ← 继续滚动
            return False                # ← 全关了，停止
        return True                     # ← 非致命错误，忽略
```

三层容错：
1. **正常** → 滚动 + 统计 + 返回 True
2. **页面关闭但可恢复** → 切标签页 + 返回 True
3. **无可恢复** → 返回 False → 主循环退出

### 8.4 _try_recover_page

```python
async def _try_recover_page(ctx, closed_page: Page) -> Optional[Page]:
    for p in ctx.pages:
        if p == closed_page:
            continue
        purl = (p.url or "").lower()
        if any(m in purl for m in READING_PAGE_MARKERS):
            await p.bring_to_front()
            return p
    return None
```

遍历 `context.pages`，排除已关闭的那一页，找第一个 URL 包含 `studentstudy`/`mycourse` 等关键词的标签页并激活。

### 8.5 清理流程

```python
finally:
    if scroller: scroller.stop()
    print(stats.summary())
    if browser:
        await browser.close()
    if pw:
        await pw.stop()    # ← 显式停止 playwright，消除 pipe 泄漏
```

`pw.stop()` 是**消除 `Event loop is closed` / `I/O operation on closed pipe` 警告的关键**。如果不显式调用，Playwright 的子进程 transport 会在 event loop 销毁后才析构，导致 pipe 泄漏。

---

## 9. 扩展阅读与参考

### 工具文档
- [Playwright Python 官方文档](https://playwright.dev/python/)
- [Playwright Mouse API](https://playwright.dev/python/docs/api/class-mouse)
- [Chrome DevTools Protocol - Input.dispatchMouseEvent](https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent)

### 反检测相关
- [puppeteer-extra-stealth](https://github.com/berstend/puppeteer-extra) — JS 生态最完善的反检测插件
- [Browser Fingerprinting 科普](https://fingerprint.com/blog/browser-fingerprinting-techniques/)

### Python 异步编程
- [Python asyncio 官方文档](https://docs.python.org/3/library/asyncio.html)
- [Real Python: Async IO in Python](https://realpython.com/async-io-python/)

### 本项目演进历史

| 版本 | 滚动方式 | 代码量 | 主要问题 |
|------|---------|--------|---------|
| v1 | JS `scrollBy` in iframe | ~890 行 | iframe 引用失效 + DNS 错误 |
| v2 | `frame_locator` + 轮询等待 | ~790 行 | `Target closed` + 探测逻辑过于复杂 |
| **v3** | **`mouse.wheel()`** | **~400 行** | ✅ 稳定 |

---

> 📅 最后更新：2026-05-28
> 💡 本文档随 `main.py` 同步维护，如有代码变更请同步更新此文档。
