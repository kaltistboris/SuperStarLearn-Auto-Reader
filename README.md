# 📖 超星学习通 — 自动阅读脚本

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.52+-green.svg)](https://playwright.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 模拟人类阅读行为，在超星学习通阅读任务中自动滚动文档，积累阅读时长。
>
> 🖱️ 核心原理：**`page.mouse.wheel()` 模拟鼠标滚轮** — 浏览器原生路由，天然跨 iframe，行为与真人一致。

---

## ✨ 特性

- 🖱️ **鼠标滚轮模拟** — 不依赖 iframe 引用，不注入 JS 滚动，最接近真人操作
- 🧠 **类人行为** — 随机间隔 + 随机步长 + 回滚 + 长暂停 + 鼠标微动
- 🛡️ **反检测** — 隐藏 `navigator.webdriver`，伪装浏览器指纹，使用系统 Edge
- 🔀 **智能标签页切换** — 自动从个人空间跳到阅读页面
- ♻️ **故障自动恢复** — 阅读页被关闭时自动切到下一个标签页
- ⏰ **定时停止** — `--duration 30` 指定分钟数，到点自动退出
- 📊 **运行统计** — 退出时打印滚动次数、距离、时长

---

## 🚀 快速开始

### 环境要求

- Python 3.12+
- Microsoft Edge 浏览器（Windows 10/11 自带）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

### 安装

```bash
# 克隆仓库
git clone https://github.com/kaltistboris/SuperStarLearn-Auto-Reader.git
cd SuperStarLearn-Auto-Reader

# 安装依赖
uv sync

# 如果用 pip
pip install playwright
playwright install chromium  # 仅安装驱动，不会下载 Chromium（我们用的是系统 Edge）
```

### 运行

```bash
# 一直滚动，手动 Ctrl+C 停止
uv run python main.py

# 30 分钟后自动停止
uv run python main.py --duration 30

# 自定义 URL + 45 分钟
uv run python main.py --duration 45 --url "https://passport2.chaoxing.com"
```

### 操作流程

```
1. 终端运行脚本 → Edge 自动打开
2. 在浏览器中手动登录学习通
3. 进入阅读任务页面（推荐按 F11 全屏）
4. 回到终端按 Enter → 自动滚动开始
5. 终端实时显示每次滚动的方向和距离
6. Ctrl+C 或定时到期 → 打印统计 → 退出
```

---

## ⚙️ 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--duration` | `-d` | `None` | 运行时长（分钟），不指定则手动停止 |
| `--url` | `-u` | `https://mooc1.chaoxing.com` | 起始 URL |
| `--min-interval` | | `2.0` | 最小滚动间隔（秒） |
| `--max-interval` | | `8.0` | 最大滚动间隔（秒） |

示例：

```bash
# 慢速模式（更像真人慢慢读）
uv run python main.py --min-interval 5 --max-interval 15 --duration 60

# 从统一登录页开始
uv run python main.py --url "https://passport2.chaoxing.com"
```

---

## 🔧 工作原理

```
┌──────────────┐
│  launch Edge │  channel="msedge" → 系统自带 Edge
└──────┬───────┘
       ▼
┌──────────────┐
│  open URL    │  学习通首页 / 登录页
└──────┬───────┘
       ▼
┌──────────────┐
│  manual login│  👆 用户手动登录 + 导航到阅读页
└──────┬───────┘
       ▼
┌──────────────┐
│  pick page   │  🔀 自动从个人空间切到阅读标签页
└──────┬───────┘
       ▼
┌──────────────────────────────────────┐
│           SCROLL LOOP                │
│  ┌──────┐  ┌──────┐  ┌───────────┐  │
│  │ wheel │→│ sleep │→│ jiggle    │  │  ← 每 2~8s 一次
│  │ 100~  │  │ 2~8s │  │ mouse 30% │  │
│  │ 500px │  └──────┘  └───────────┘  │
│  └──────┘                            │
│     │                                │
│     ├─ 12% 回滚 (50~200px)           │
│     ├─ 每 3~5min 长暂停 15~45s       │
│     └─ 页面关了 → ♻️ 自动切下一个    │
└──────────────────────────────────────┘
       ▼
┌──────────────┐
│  stats + exit│  📊 打印运行统计
└──────────────┘
```

### 为什么用 mouse.wheel() 而非 JS scrollBy？

| 方式 | 问题 |
|------|------|
| `frame.evaluate("window.scrollBy(...)")` | iframe 引用失效 → 崩溃；跨域限制；PDF 图片模式无效 |
| **`page.mouse.wheel(0, delta)`** | ✅ 浏览器内核自动路由到鼠标下方的元素，天然跨 iframe |

---

## 📁 项目结构

```
SuperStarLearn-Auto-Reader/
├── main.py              # 🎯 主脚本（~400 行）
├── pyproject.toml       # 📦 项目配置
├── README.md            # 📖 本文件
└── docs/
    └── technical-guide.md  # 🔬 技术原理详解（推荐阅读）
```

---

## ❓ FAQ

**Q: 会被学习通检测到吗？**

A: 脚本已做多层反检测（隐藏 webdriver + 真实 Edge + 类人行为随机化）。目前使用一切正常，但不能保证 100%。如发现异常，可调大 `--min-interval` 参数使节奏更慢。

**Q: 文档没滚动怎么办？**

A: 确保阅读页**在前台**且文档已加载完毕。推荐按 F11 全屏。脚本会自动从个人空间切到阅读标签页。

**Q: 滚动太快/太慢？**

A: 通过 `--min-interval` 和 `--max-interval` 调整。默认 2~8 秒一次。

**Q: 支持 macOS / Linux 吗？**

A: 理论上支持。将 `channel="msedge"` 改为 `channel="chrome"` 或去掉 channel 参数使用 Playwright 自带 Chromium 即可。

**Q: 怎么知道阅读时长有没有涨？**

A: 运行一段时间后去学习通任务页面刷新查看。

---

## ⚠️ 免责声明

本工具仅供**学习 Python 浏览器自动化技术**之用。请遵守学习通平台的使用条款，合理使用。使用者自行承担一切责任。

---

## 📄 License

MIT
