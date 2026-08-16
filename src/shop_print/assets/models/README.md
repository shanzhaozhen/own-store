# assets/models —— OCR 模型

放 RapidOCR 用的 ONNX 模型。当前实际是 **PP-OCRv6 small**（检测 + 识别）加一个方向分类模型，三个文件约 30 MB：

| 文件 | 作用 | 大小 |
|---|---|---|
| `PP-OCRv6_det_small.onnx` | 文字检测（找框） | ~9.5 MB |
| `PP-OCRv6_rec_small.onnx` | 文字识别（认字） | ~20 MB |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | 方向分类 | ~0.6 MB |

**模型随打包产物一起分发，运行时不联网下载** —— 店铺网络不一定稳，长辈不能卡在"正在下载模型"上。`core/ocr.py` 里用 `Global.model_root_dir` 指到这个目录。

模型文件体积大，不进 git 仓库（见 `.gitignore`）。换机器开发时：

```powershell
# 1. 先让 rapidocr 自己下载一次（跑真实引擎的用例即可）
pytest tests\test_ocr.py -m needs_samples -q
# 2. 从 site-packages\rapidocr\models\ 拷到这里
.\scripts\prepare-models.ps1
```

打包时由 `scripts\build.ps1` 的 `--add-data` 带上。
