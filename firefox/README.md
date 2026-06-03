# Firefox 版 — 开发中

此目录预留为 Firefox 浏览器自动化版本。

计划与 chromium/ 版保持接口一致：
- chromium/main.py → firefox/main.py（CLI）
- chromium/gui.py  → firefox/gui.py（GUI）

差异：启动 Firefox 引擎而非 Chromium（pw.firefox.launch()）
