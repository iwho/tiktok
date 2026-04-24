# Polymarket Order Monitor

监听 [Poly Market](https://polymarket.com) 上某个账号的订单，有订单成交后输出订单的详细信息。

## 功能

- **轮询模式（默认）**：每隔 N 秒查询一次 Polymarket CLOB REST API，检测新成交。
- **WebSocket 模式**：通过 Polymarket WebSocket 订阅实时接收成交事件。
- 启动时自动加载历史成交，避免重复报告旧订单。
- 每笔新成交打印完整详情，包含市场问题、方向、价格、数量、交易哈希等。
- 自动通过 Gamma API 补充市场名称和链接。

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行

```bash
# 轮询模式（默认，每 5 秒查询一次）
python polymarket_monitor.py 0xYourWalletAddress

# 自定义轮询间隔（10 秒）
python polymarket_monitor.py 0xYourWalletAddress --interval 10

# WebSocket 实时模式
python polymarket_monitor.py 0xYourWalletAddress --mode ws
```

将 `0xYourWalletAddress` 替换为你要监听的 Polymarket 账户的以太坊地址。

## 输出示例

```
Polymarket Order Monitor
监听账户: 0xabc123...
模式    : 轮询（5s）

[16:00:00] 初始化，加载历史订单…
[16:00:01] 初始化完成，已记录 42 笔历史成交。
[16:00:01] 开始监听新成交（每 5 秒轮询一次）…

============================================================
🔔  新成交订单 / New Filled Order
============================================================
  交易 ID       : abc123-def456-...
  市场 ID       : 0x...
  市场问题      : Will X happen before Y?
  市场链接      : https://polymarket.com/event/...
  结果 (Outcome): Yes
  方向 (Side)   : BUY
  成交价 (Price): 0.65
  数量 (Size)   : 100.0
  Maker 地址    : 0xabc...
  Taker 地址    : 0xdef...
  交易角色      : MAKER
  状态 (Status) : CONFIRMED
  成交时间      : 2024-01-15 16:00:00 UTC
  交易哈希      : 0x...
  Polygonscan   : https://polygonscan.com/tx/0x...
============================================================
```

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `address` | 要监听的以太坊地址（必填） | — |
| `--interval` | 轮询间隔秒数（仅 poll 模式） | `5` |
| `--mode` | 监听模式：`poll`（轮询）或 `ws`（WebSocket） | `poll` |

## 依赖

| 包 | 用途 |
|----|------|
| `requests` | HTTP 请求 Polymarket CLOB & Gamma REST API |
| `websocket-client` | WebSocket 实时订阅（`--mode ws` 时需要） |

## API 说明

本项目使用以下 Polymarket 公开 API（无需认证）：

- **CLOB API** `https://clob.polymarket.com/trades` — 查询账户成交记录
- **Gamma API** `https://gamma-api.polymarket.com/markets` — 获取市场名称/链接
- **WebSocket** `wss://ws-subscriptions-clob.polymarket.com/ws/user` — 实时成交推送
