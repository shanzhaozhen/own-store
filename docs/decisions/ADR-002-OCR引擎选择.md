# ADR-002 · OCR 引擎选择

- **日期**：2026-08-16
- **状态**：已采纳
- **决策**：本地用 RapidOCR 3.x（onnxruntime + PP-OCRv5 mobile 模型）；云端高精度做成可插拔接口，v1 只留接口不实现

## 背景

需求是「拍照的图片需要转成文字文档并且排版」。识别对象是中文为主的手机拍照文档，交付物是能编辑的 Word。

## 决定性事实：PaddleOCR 用不了

原本 PaddleOCR 是中文 OCR 的首选（精度最好、生态最全）。但它依赖 `paddlepaddle`，而在本项目的 Python 3.14 上：

```
> python -m pip index versions paddlepaddle
ERROR: No matching distribution found for paddlepaddle
```

cp314 没有轮子。同一次探测里 `paddleocr` 自身有包（3.7.0），但没有后端框架等于装不上。

可选的绕法都不划算：为 OCR 单独维护一个 Python 3.12 环境（打包出两套解释器，部署和排错复杂度翻倍）、或者自己编译 paddlepaddle（不现实）。

## 采纳：RapidOCR

`rapidocr` 3.9.2 + `onnxruntime` 1.28.0，两者都有 cp314 轮子（已实测）。

它用的就是 PP-OCR 系列模型，只是换成 ONNX 推理，**精度接近 PaddleOCR，但不需要 paddlepaddle**。

选 **mobile** 模型而不是 server 模型：

- 模型约 15 MB，可以随包分发，**首次使用不联网** —— 店铺网络不稳，不能卡在下载上
- CPU 上单页一两秒；server 模型精度提升有限但慢好几倍，店铺机配置未知且预计较弱，长辈等不起

## 云端高精度：留接口，不实现

用户选的是「本地为主 + 云端高精度可选」。

本地坐标聚类做段落还原够用，但**表格、多栏排版、复杂公文吃力**。云端视觉大模型或带版面分析的云 OCR 能直接输出结构化文本，这类场景质量高一个档次。

所以 `core/ocr_cloud.py` 定义协议：

```python
class CloudOcrProvider(Protocol):
    name: str

    def recognize(self, image: np.ndarray) -> str:  # 返回 Markdown
        ...
```

**统一返回 Markdown 而不是坐标框**：换 provider（百度 / 腾讯 / 视觉大模型）不用改下游，下游只需要一个 Markdown → docx 转换器。

v1 界面上「高精度识别」按钮置灰，提示「还没设置」；`config.json` 里填好 provider 和 key 后自动启用。

不在 v1 就接的理由：需要 API key、实名注册、按量付费，且依赖店铺网络。先把离线免费那条路做扎实，云端作为增量。

## 附带决定：OCR 的输入是增强后的图

送 OCR 的不是原始照片，而是 `enhance.prepare_for_ocr()` 的输出（透视校正 + 去阴影 + 灰度）。透视校正让文字行变水平，检测框更准；去阴影让暗部文字不被吞掉。

**但不能送二值化后的图** —— 过度二值化吃掉笔画细节，反而降低识别率。

这样「照片变清楚再打印」和「照片转文字文档」共用同一条前处理链，不重复实现。

## 复查条件

如果以后把项目降到 Python 3.12/3.13，或 `paddlepaddle` 出了 cp314 轮子，可以重新评估 PaddleOCR。但只有在 RapidOCR 精度确实不够用时才值得折腾。
