# 参考的开源项目

- PaddleOCR 的 `paddleocr-doc-parsing` 是 Apache-2.0 技能。PP-StructureV3 区分文字、公式、表格和图形区域，恢复多栏阅读顺序。本项目将这些流程写成适用物理试题的逐块转写指令，由当前选用的 Agent 处理。 [PaddleOCR 原技能](https://github.com/PaddlePaddle/PaddleOCR/blob/main/skills/paddleocr-doc-parsing/SKILL.md) · [PP-StructureV3 文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md)
- UniMERNet 使用独立公式区域做图像转 LaTeX 识别。本项目借鉴分开核对公式结构与文本的做法。 [UniMERNet 项目与 Apache-2.0 许可证](https://github.com/opendatalab/UniMERNet)
- pix2tex 聚焦单个公式图像并提供转写结果渲染。本项目借鉴对照原图复核局部结构的做法。 [LaTeX-OCR 项目与 MIT 许可证](https://github.com/lukas-blecher/LaTeX-OCR)

本技能的提示词为项目编写。它在应用现有的 Agent 调用中使用，无需单独安装这些项目或配置其云端服务。
