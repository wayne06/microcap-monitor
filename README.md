# 微盘股多空监测系统

A股最小市值400只微盘股的多空指标监测仪表盘，包含市场宽度、资金集中度、行业资金流向、RSI等指标，并提供仓位建议。

## 功能

- **ADL 腾落线**：微盘400只股票的上涨家数-下跌家数差值
- **平均RSI**：14日周期的相对强弱指标均值
- **20日均线行业占比**：统计站上20日均线的二级行业比例，判断市场是否普涨
- **资金集中度**：Top 10%成交额股票占总成交额比例
- **行业5日资金流**：二级行业近5日净流入/流出 Top 5
- **多空优势概率**：综合评分（ADL + RSI + 涨跌幅 + 宽度），自动给出仓位建议
- **模拟数据回退**：API不可用时自动使用合理模拟数据

## 快速启动

```bash
pip install -r requirements.txt
python server.py
```

打开 http://127.0.0.1:9876（或终端提示的端口）

## 数据来源

优先从东方财富 API 实时获取 A 股全市场股票数据，按市值升序取最小400只。API 限流时自动回退到模拟数据。

## 部署

一键部署到 Render：
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=<YOUR_GITHUB_URL>)

或手动部署：
```bash
git clone <your-repo>
cd microcap-monitor
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 10000
```
