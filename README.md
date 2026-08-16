# 店铺打印助手

给父母的打印复印店做的 Windows 桌面工具。顾客通过微信把文件发到店铺电脑，这个工具让「打印」这件事变成点两下。

**实际操作者是年纪较大的长辈，不熟悉电脑 —— 简单易用是硬约束，不是加分项。**

<p align="center">
  <img src="docs/images/首页.png" width="720" alt="首页：四张大卡片，一屏到底">
</p>

一屏六张大卡片，没有菜单栏、没有工具栏、没有设置按钮；正文 ≥16pt、主按钮高 ≥56px、做完一件事点击数 ≤3。完整的界面硬性规则见 [docs/06-界面规范](docs/06-界面规范.md)。

## 最核心的一件事：把拍照的文档打干净

顾客发来的多是手机拍的纸质文件，带阴影、纸张发黄发灰。用黑白激光机（柯美 bizhub 225i）直接打，背景一片脏灰、文字发虚，费墨还不能看。

左边是原图，右边是自动去底之后：

<p align="center">
  <img src="docs/images/增强前后对比.png" width="860" alt="去底增强前后对比：背景压成白、文字提成实心黑">
</p>

做法不是调全局对比度或全局阈值（那样一定失败 —— 拍照文档的背景不均匀，照顾了亮的一侧，暗的一侧就糊成整片黑）。而是**先估计出这张图的光照场再把它除掉**（flat-field correction），然后自适应二值化 + 补实心 + 去杂点。原理、参数含义和踩过的坑见 [docs/03-图片增强算法](docs/03-图片增强算法.md)。

界面上只有两个可调项：内容类型（自动 / 文字为主 / 图文混排 / 照片）和一个「淡 ←→ 浓」滑块。

<p align="center">
  <img src="docs/images/照片变清楚.png" width="720" alt="照片变清楚：左右对比 + 强度滑块">
</p>

## v1 的五个功能

| 功能 | 解决什么 |
|---|---|
| **照片变清楚再打印** | 手机拍的文档带阴影、纸张发黄，黑白机直接打一片脏灰。自动去底增强，背景压白、文字提黑 |
| **办公文档一键打印** | Word / Excel / PDF 统一转 PDF → 预览 → 打印。Excel 宽表自动「一页宽」，不再哗哗吐纸 |
| **照片转成文字文档** | OCR + 版面重建，输出能直接改的 Word（不是一堆文字，是排好版的文档） |
| **证件印一张纸** | 身份证正反面、户口本两页拼到一张 A4，**按实物尺寸打**（85.6×54mm），尺寸不保真就明确说出来 |
| **微信收到的文件自动出现** | 监控微信接收目录和 `C:\打印\待打印`，新文件直接显示在首页，不用去文件夹里翻 |

<table>
<tr>
<td width="50%"><img src="docs/images/打印文档.png" alt="打印文档：预览 + 份数加减 + 一个大绿钮"></td>
<td width="50%"><img src="docs/images/照片转文字.png" alt="照片转文字：识别结果可直接编辑"></td>
</tr>
<tr>
<td align="center">份数用「− 3 +」大加减按钮，彩色选项直接禁用并写明「本店打印机只有黑白」</td>
<td align="center">识别结果可以直接改，置信度低的行标红提示核对</td>
</tr>
</table>

### 证件二合一：难的不是拼图，是「毫米」

复印店最常见的活之一。派出所、银行、学校要的是 **1:1 的复印件**，缩了放了都可能被退回来重做。而照片里只有像素、没有毫米，所以尺寸只能从三处来：用户选的证件类型（查预设表）、扫描件的 DPI 元信息、或者抠出卡片量**长宽比**去匹配预设。

<p align="center">
  <img src="docs/images/证件二合一.png" width="760" alt="证件印一张纸：选类型 → 放两张图 → 按实物尺寸拼到一张 A4">
</p>

几个刻意的决定：

- **拿不准就不猜**：护照（1.42）和户口本（1.41）长宽比几乎一样但尺寸差 16%，自动识别要求最优解比"尺寸不同的次优解"近一倍以上，否则让长辈自己点类型
- **纸张方向自己挑**：两页竖着的户口本并排要 220mm 超出 A4，换成横向 A4 就能 1:1 放下，而且两页都还正着看
- **打印时也不缩**：默认的"缩放到可打印区"会因为打印机四周 4–5mm 的边把身份证打成 82mm，证件那条路走 `actual_size` 把页面的 1mm 对准纸的 1mm
- **不保真就说出来**：真放不下才缩，并且在界面上写明"已缩小到 93%"，不悄悄缩

尺寸这条线由断言守着（`tests/test_physical_size.py`）：每个预设的横放竖放各跑一遍，打开生成的 PDF 直接量毫米。顺带把整条打印链路的产物都量了一遍 —— 图片→PDF、txt→PDF、OCR→Word、证件→PDF 全部 210×297mm。**这一遍对出一个一直存在的问题：OCR 转出来的 Word 原来是美国 Letter（215.9×279.4mm）**，python-docx 自带模板就是 Letter，已显式改成 A4。

细节和预设表见 [docs/10-证件二合一](docs/10-证件二合一.md)。

<p align="center">
  <img src="docs/images/微信收到的文件.png" width="720" alt="微信收到的文件：一个文件一张大卡片">
</p>

第二阶段规划（v1 不实现，只在文档里预留架构）：微信小程序让顾客自助选文件 → 预览 → 支付 → 店里自动打印。见 [docs/08](docs/08-第二阶段-微信小程序.md)。

<!-- PLACEHOLDER-REST -->

## 两台机器，别搞混

| | 开发机（写代码的这台） | 店铺机（父母用的那台） |
|---|---|---|
| 系统 | Windows 11 26100 | **Windows 10** |
| Python | 3.14.7 | 不装 Python，只跑打包产物 |
| 打印机 | 只有虚拟打印机（Microsoft Print to PDF 等） | **柯美 bizhub 225i，只能黑白** |
| Office | Microsoft Office 2016+ | Microsoft Office |
| 微信 | 客户端 4.1.11，目录却是 3.x 布局 | 待现场确认 |
| 屏幕 | 1920×1080 | 待确认（按 1366×768 设计） |

**开发机上验证不了的事**：柯美驱动的真实行为、店铺屏幕下的布局、店铺微信的实际路径、弱 CPU 上的 OCR 耗时。所以这些量一律做成配置项 + 自动探测 + 降级路径，并在文档里标明「未在真机验证」。开发期用 **Microsoft Print to PDF 当替身打印机**跑通全链路。详见 [docs/01-环境与设备](docs/01-环境与设备.md)。

## 架构

```
src/shop_print/
  core/     纯逻辑，不 import 任何 Qt —— 第二阶段的服务端 / 店铺 Agent 要原样复用
    enhance.py    照片去底增强（v1 的核心价值）
    convert.py    任意文件 → PDF（图片 / Office COM / txt / PDF）
    office_worker.py  Word、Excel 转换跑在独立子进程里，崩了不拖死主程序
    printing.py   PDF → 打印机（GDI + DEVMODE，份数/双面/纸张/单色显式设死）
    ocr.py        RapidOCR + 版面重建 → docx / txt
    ocr_cloud.py  云端高精度 OCR 的 Provider 协议（v1 只留接口）
    cards.py      证件二合一：抠卡片 → 定实物尺寸 → 排版 → 一张纸的 PDF
    intake.py     文件从哪来：微信目录 / 待打印目录 / 剪贴板 / 拖拽
    history.py    打印记录（SQLite，为第二阶段收费铺路）
  ui/       PySide6，只做展示与调度，不写业务算法
  texts.py  所有面向用户的中文文案（含友好错误话术）
  paths.py  所有路径统一在此定义
  self_check.py  --self-check 自检，店铺机上排障用
```

一条贯穿设计：**一切文件先归一化成 PDF，再统一预览、统一打印**。预览器只认一种格式，打印路径只有一条，顾客看到的预览和打出来的纸一定是同一份数据。见 [docs/02-架构与分层](docs/02-架构与分层.md)。

## 开发

```powershell
.\scripts\setup-dev.ps1                         # 建 venv + 装依赖（会把 opencv 换回 headless）

.\scripts\运行.bat                               # 启动界面（双击也行）
.\scripts\运行.bat -SelfCheck                    # 不开界面，出一份自检报告
.\.venv\Scripts\python.exe -m shop_print         # 同上，手敲版
```

其它常用的：

```powershell
.\.venv\Scripts\python.exe -m shop_print.core.enhance <图片路径>   # 去底算法调试，输出前后对比图
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check . ; .\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe scripts\make_screenshots.py            # 重新生成 README / docs 插图
```

测试 203 个，其中标了 `needs_office` / `needs_printer` / `needs_samples` 的需要本机有 Office、打印机或 OCR 模型。判据刻意写成**方向性的性质**（背景该变白、文字该保持黑、滑块该单调有效），而不是像素级快照 —— 否则调参天天挂测试。物理尺寸例外：`tests/test_physical_size.py` 直接量毫米，因为"打出来必须是实物大小"是硬要求。

## 打包与部署

```powershell
.\scripts\打包.bat               # 一键：ruff → pytest → 拷 OCR 模型 → 生成图标 → PyInstaller
.\scripts\打包.bat -Clean        # 换过依赖 / 改过 add-data 时用
.\scripts\运行.bat -Packaged     # 跑一次打包产物，确认不是靠 .venv 才能起来
```

产物在 `dist\打印助手\`（约 414 MB，`--onedir`）。模型和图标都不进 git，所以打包脚本把"准备资源"也串进去了 —— 换台机器 clone 下来直接 build 会打出一个「没有 OCR 模型」的产物，而这种产物在开发机上一眼看不出问题，到店铺机点「照片转文字」才炸。

装到店铺机：双击 `scripts\安装到店铺电脑.bat` —— 拷到 `C:\ShopPrint\`（旧版本自动改名留着好回滚）、公共桌面放「打印助手」和「待打印」两个图标、可选开机自启。

打包后**没有界面也能排障**：

```powershell
.\dist\打印助手\打印助手.exe --self-check
```

逐项检查随包资源、打印机、中文字体，并测一次 OCR 耗时，报告写到 `%LOCALAPPDATA%\ShopPrint\logs\自检报告.txt`（让父母把这个文件发回来就行）。去店铺之前先让他们双击 `scripts\采集店铺环境.bat`，拿到柯美 225i 的准确名称、微信目录、屏幕分辨率。详见 [docs/07-打包与部署](docs/07-打包与部署.md)。

## 当前状态

代码全部写完，`ruff` 干净、203 个测试通过，开发机上打包并实测通过（窗口 1.5 秒出来、OCR 一张 2.9 秒、自检全过）。

还没做的：**店铺机实测**（柯美 225i 的双面 / A3 / 实际画质，以及证件打出来驱动有没有照着 PDF 的尺寸走），以及**用真实顾客样张校准去底参数** —— 后者是当前最大的风险，合成图只能证明代码跑通，不能证明效果好。

## 隐私

`samples/` 里的真实样张常含身份证、合同、成绩单，`.gitignore` 默认忽略整个目录；仓库里的插图全部用**合成样张**（`tests/synth.py` 渲染的假合同和假卡片，人名地址都是编的）。证件照片和拼好的 PDF 只落在本机 `%LOCALAPPDATA%\ShopPrint\cache\`，记得定期清空。技术细节只进本地日志，不上传任何地方。

## 文档

从 [docs/README.md](docs/README.md) 进入。项目约定和禁止事项在 [CLAUDE.md](CLAUDE.md)。

