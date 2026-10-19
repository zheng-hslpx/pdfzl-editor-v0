# PDF Editor v0

轻量级自托管 PDF 编辑器，架构模式参考 [Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF)（MIT）。

## 技术栈

- 后端：Python 3.13 + FastAPI + PyMuPDF（fitz）
- 前端：React 19 + Vite 7 + TypeScript + Mantine 8 + PDF.js
- Office 转换：LibreOffice headless

## 快速开始

```bash
# 后端（端口 8000）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# 前端（端口 5173，/api 代理到 8000）
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173

## 开发进度

见 `E:\worksapce\PDF demo1\tasks\20261019\todo.list`。

> 许可说明：PyMuPDF 为 AGPL-3.0，本项目仅供学习研究使用。
