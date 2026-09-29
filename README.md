# Course Auto Assistant · 刷课小助手

Windows 11 中文桌面程序，基于 **Python + Playwright + Tkinter**，不是 Playwright 的分叉或源码复制。

**当前版本：v0.1.0（基础工程版）。英盛登录后页面尚未完成实站适配，当前不能下载后直接全自动跑课。**

## 已确认的需求

Windows 11；中文 EXE；只支持 `https://qy.yingsheng.com/26058/courselist/`；自动选择未完成课程；允许本机保存登录；首版 v0.1.0。未知业务规则和页面入口必须先核实，不擅自决定。

## 首版实际内容

- 中文界面：打开课程中心、自动队列入口、暂停/继续/停止、状态和课程队列表。
- 使用本机 Microsoft Edge 的**专用浏览器资料目录**，人工登录后本机保存；可清除登录，不读取日常浏览器配置。
- 页面适配采集：手动选择标签页，分别采集课程中心、章节列表、播放器的元素结构，只保存本机，不上传。
- 配置驱动队列引擎：课程扫描、明确分页、精确状态匹配、普通HTML5播放、iframe视频发现、结束后回读平台状态。
- 英盛配置默认 `verified: false` 且选择器为空；没有核实的配置时，自动入口停止并说明原因，**不会猜入口或把零结果当完成**。
- 独立单元测试、合成网页浏览器测试、Windows打包和打包后自检、版本检查、Release工作流。

队列引擎的合成网页测试不等于英盛实站验证。详情见 [待确认事项](docs/DECISIONS.md)。

## Windows 使用

Release 中选 `Course-Auto-Assistant-v0.1.0-windows-x64.zip`，解压并运行同名 EXE。无需安装 Python，**需本机已经安装 Microsoft Edge**。EXE未做商业代码签名；请核对仓库来源和 SHA256，不需要关闭安全软件或以管理员身份运行。

首版先点“打开课程中心 / 登录”，人工登录，然后切换“英盛页面适配”采集三份结构，按 [使用指南](docs/USER_GUIDE.md) 核实页面。暂不要把它当作已经完成英盛适配的成品。

## 自动队列约束

仅通过正常页面读取和播放器播放处理课程，不修改播放速度、视频进度、学习请求、心跳或学分。视频 `ended` 仅表示播放器结束，必须在平台页面重新确认完成。签到、身份验证、异常提示、未知状态或长时间停顿会停止并提示人工处理，不自动代答或确认在场。

## 源码运行与测试

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/release_tools.py build
```

生产运行复用 Edge，不需要下载 Playwright 的 Chromium。开发依赖固定版本，升级需重新测试。Linux开发测试可安装相应Chromium或设置 `CAA_TEST_CHROMIUM` 指向测试浏览器；这不是正式支持Linux用户。

## 结构

```text
course_auto_assistant/
  app.py       # 中文界面；主线程不调用Playwright
  browser.py   # 单独工作线程、浏览器和队列状态
  site.py      # 已核实配置驱动的页面适配、只读诊断
  core.py      # 配置、域名限制、本机存储和实例锁
config/yingsheng.template.json  # 未核实的空模板，不是可运行的站点配置
scripts/release_tools.py       # 版本核对、Windows打包、EXE自检
.github/workflows/release.yml  # 检查 -> 测试 -> 打包 -> 发布
VERSION / CHANGELOG.md
```

## 版本发布

更新 `VERSION`、`course_auto_assistant/__init__.py` 和 `CHANGELOG.md` 顶部记录，一起提交。PR执行Windows测试与打包；合入main并通过后，工作流为尚未发布的版本创建Release，上传EXE、ZIP与SHA256。已发布版本不覆盖，同版本的源码变更不能冒充新Release。后续补实站适配按patch迭代，明确记录验证证据。

## 数据与许可

本机数据在 `%LOCALAPPDATA%\CourseAutoAssistant\`，包括专用浏览器资料、适配配置和运行记录，不在Git仓库。详见 [隐私说明](docs/PRIVACY.md)。项目自身的开源许可证尚未由仓库所有者选定，本次不擅自添加。第三方组件保留各自许可，发布包包含相应说明。

## 技术依据

- Playwright Python：https://playwright.dev/python/docs/library
- Edge通道：https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge
- 登录资料注意事项：https://playwright.dev/python/docs/auth
- PyInstaller：https://pyinstaller.org/en/stable/
