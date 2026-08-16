# CLAUDE.md — 店铺打印助手

## 这个项目是什么

给父母经营的打印复印店做的桌面工具。**实际操作者是年纪较大的长辈，不熟悉电脑。**

v1 解决五件事：

1. 顾客通过微信发来的**手机拍照文档**，带阴影、纸张底色发灰发黄，黑白激光机直接打出来一片脏 —— 做自动去底增强。
2. 办公文档（Word/Excel/PDF）**一键打印**，不用教长辈在各个软件里点"文件 → 打印 → 设置"。
3. 拍照件**转成可编辑 Word** 并排好版。
4. **证件二合一**：身份证正反面、户口本两页拍照后拼到一张 A4，**必须按实物尺寸打**（缩了复印件会被退回重做）。见 `docs/10-证件二合一.md`。
5. 微信收到的文件**自动出现在首页**，不用在文件夹里翻找。

第二阶段（未实现，见 `docs/08-第二阶段-微信小程序.md`）：微信小程序让顾客自助选文件 → 预览 → 支付 → 店铺自动打印。

## 第一原则：简单易用是硬约束

这不是加分项。任何"多点一下就行"的设计都要重新想。写代码前先问：**长辈第一次看到这个界面，会不会不知道下一步点哪里？**

## 双机环境（改代码前务必分清）

| | 开发机（Claude 当前所在） | 店铺机 |
|---|---|---|
| 系统 | Windows 11 专业版 26100 | **Windows 10** |
| Python | 3.14.7 `C:\Users\shanz\AppData\Local\Python\pythoncore-3.14-64` | 无，只跑打包产物 |
| Office | Microsoft Office 2016+ `C:\Program Files\Microsoft Office\Root\Office16\` | Microsoft Office |
| 打印机 | 只有虚拟打印机（Adobe PDF、Microsoft Print to PDF） | **柯美 bizhub 225i，只能黑白** |
| 微信 | 客户端 4.1.11.24（`Weixin.exe`），但目录是 3.x 布局 `Documents\WeChat Files\wxid_xxx\FileStorage\` | 待现场确认 3.x / 4.x |
| 分辨率 | 1920×1080 | 待确认 |

**当前机器不是店铺机。**涉及柯美 225i 的真实行为、店铺的微信版本、店铺屏幕分辨率，在本机都无法验证 —— 相关代码要写成可配置 + 有降级路径，并在报告里明确说"这部分未能在真机验证"。

开发机上用 **Microsoft Print to PDF** 当替身打印机跑通全链路。

## 技术栈（已定，不要临时换）

Python 3.14 + PySide6 桌面应用，PyInstaller `--onedir` 打包成免安装文件夹。

核心库：`opencv-python-headless`、`pillow`、`numpy`、`PyMuPDF`、`rapidocr` + `onnxruntime`、`pywin32`、`python-docx`、`watchdog`。版本钉死在 `pyproject.toml`。

## 禁止事项（都是踩过或已验证的坑）

- **不要引入 `paddlepaddle` / `paddleocr`** —— cp314 上没有任何可安装的 `paddlepaddle` 轮子，已实测 `No matching distribution found`。本地 OCR 一律用 `rapidocr`（onnxruntime 后端）。
- **不要用 `opencv-python` 或 `opencv-contrib-python`**，只用 `opencv-python-headless` —— 完整版自带一套 Qt，会和 PySide6 的 Qt 冲突导致启动崩溃。
- **不要在 UI 线程跑 OCR、Office COM 转换、全分辨率图像处理** —— 一律丢 `QThreadPool` / 子进程，界面必须始终可响应。
- **不要在 `core/` 里 import 任何 Qt 模块**。`core/` 是纯逻辑，第二阶段服务端要原样复用。
- **不要给用户看英文、异常堆栈、错误码**。所有面向用户的文字都从 `texts.py` 取。
- **不要加"高级设置"到主界面**。设置入口刻意隐藏（标题栏连点 5 次）。

## 界面硬性规则（可量化，便于 review）

- 正文字号 ≥ 16pt，按钮文字 ≥ 18pt，首页卡片标题 ≥ 22pt
- 主按钮高度 ≥ 56px，首页卡片 ≥ 220×160px，可点区域不小于 44×44px
- 完成一件事的点击数 ≤ 3（首页选功能 → 选文件 → 开始打印）
- **窗口最小 1024×640**，启动即最大化。店铺屏幕未确认，按 1366×768 算可用高度只有 700 出头，
  窗口太高会让底部「开始打印」被任务栏压住 —— 主操作点不到就等于工具废了
- 无菜单栏、无工具栏、无标签页；每个子页面左上角都有大号「← 返回」
- 份数之类的数字用「− 3 +」大加减按钮，不用输入框、不用微调框
- 彩色相关选项直接禁用并写明「本店打印机只有黑白」
- 状态要有大字反馈：「正在打印第 2 页 / 共 5 页」→「打印好了 ✓」
- 错误提示写成人话：`打印机没有连上，请看看它的电源和数据线`，不是 `Error 0x800706BA`

## 目录约定

```
src/shop_print/
  core/     纯逻辑，无 Qt。enhance / convert / printing / ocr / cards / intake / history
  ui/       PySide6 界面，只做展示与调度，不写业务算法
  texts.py  所有面向用户的中文文案（含友好错误话术）
  paths.py  所有路径（缓存、日志、待打印目录、样式表、模型）统一在此定义，不要散落硬编码
  config.py 配置读写，落在 %LOCALAPPDATA%\ShopPrint\config.json
docs/       知识库，见 docs/README.md 索引
scripts/    setup-dev.ps1 装环境、prepare-models.ps1 拷 OCR 模型、make_icon.py 生成图标、
            build.ps1 打包（入口是 launcher.py，不是 __main__.py）、
            安装到店铺电脑.bat + install-to-shop.ps1、采集店铺环境.bat + collect-shop-env.ps1
samples/    真实拍照样张，调参与回归测试基准
tests/      conftest.py 会把 %LOCALAPPDATA% 重定向到临时目录，别往真实数据目录写
```

面向长辈的脚本一律做成「中文名 `.bat` 外壳 + ASCII 名 `.ps1` 逻辑」：`.bat` 里只放 ASCII，
中文提示写在 `.ps1` 里 —— cmd 换代码页时中文会变乱码。

## 常用命令

```powershell
.\scripts\setup-dev.ps1            # 建 venv + 装依赖 + 把 opencv 换回 headless
.\.venv\Scripts\Activate.ps1

python -m shop_print              # 启动界面
python -m shop_print.core.enhance <图片路径>   # 增强算法命令行调试
ruff check . ; ruff format .
pytest -q
.\scripts\prepare-models.ps1      # 打包前：拷 OCR 模型进 assets
.\scripts\build.ps1               # 打包到 dist\打印助手\
```

## 改完代码必须做的验证

1. `ruff check .` 与 `pytest -q` 通过。
2. 改了界面 → 真的启动一次 `python -m shop_print`，走完受影响的那条路径。
3. 改了打印相关 → 用「Microsoft Print to PDF」实际打一次，打开输出 PDF 肉眼确认页数、缩放、方向。
4. 改了增强算法 → 用 `samples/` 里的真实样张跑前后对比，把参数变化记进 `docs/03-图片增强算法.md`。
5. 临时产物（测试 PDF、中间图片）用完删掉，不要留在仓库里。

## 写文档的规矩

有价值的决策和踩坑写进 `docs/`，并在 `docs/README.md` 加一行索引；重大技术选择写 `docs/decisions/ADR-xxx.md`。
文档用中文，写"为什么这么选"和"换成别的会怎样"，不要复述代码能自己说明的东西。

