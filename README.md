# toktok

一个轻量的 Polymarket 客户端，用来通过 `slug` 调用 Gamma API 获取市场数据。

## 目录结构

```text
toktok/
├─ pyproject.toml
├─ requirements.txt
├─ run.py
├─ src/
│  └─ toktok/
│     ├─ client.py
│     ├─ exceptions.py
│     ├─ okx_client.py
│     ├─ __init__.py
│     ├─ __main__.py
│     └─ main.py
└─ tests/
   ├─ test_cli.py
   ├─ test_client.py
   ├─ test_okx.py
   ├─ test_okx_client.py
   └─ test_smoke.py
```

## 快速开始

1. 安装依赖：`pip install -r requirements.txt`
2. 直接运行：`python run.py <slug>`
3. 或作为模块运行：`python -m toktok <slug>`

例如：

```bash
python run.py trump-win-2028
```

默认会输出格式化 JSON；如果你想要紧凑输出，可增加 `--compact`。

## Python 代码调用（Polymarket）

```python
from toktok import PolymarketClient

with PolymarketClient() as client:
    market = client.get_market_by_slug("trump-win-2028")
    print(market)
```

## Python 代码调用（OKX）

`OkxClient` 对应 `tests/test_okx.py` 中使用到的接口，封装了：

- `get_instruments`
- `get_opt_summary`
- `get_mark_price`
- `get_index_tickers`
- `place_order`
- `get_latest_btc_option_put`
- `get_latest_btc_option_call`

仅使用公开行情接口：

```python
from toktok import OkxClient

client = OkxClient(flag="0")
print(client.get_instruments(inst_type="OPTION", inst_family="BTC-USD"))
print(client.get_opt_summary(inst_family="BTC-USD", exp_time="260427"))
print(client.get_mark_price(inst_id="BTC-USD-260427-77750-C"))
print(client.get_index_tickers(inst_id="BTC-USD"))
print(client.get_latest_btc_option_put())
print(client.get_latest_btc_option_call())
```

使用环境变量初始化交易接口：

```bash
set OKX_API_KEY=你的APIKey
set OKX_SECRET_KEY=你的SecretKey
set OKX_PASS_PHRASE=你的Passphrase
set OKX_FLAG=1
```

```python
from toktok import OkxClient

client = OkxClient.from_env()
result = client.place_order(
    inst_id="BTC-USD-260427-77750-P",
    td_mode="cross",
    cl_ord_id="b15",
    side="sell",
    ord_type="limit",
    px="0.0100",
    sz="1",
)
print(result)
```

## 交易循环（买 DOWN）

会持续执行：

1. 生成当前 `btc-updown-5m-{timestamp}` slug
2. 查找该市场的 DOWN token
3. 如果当前 slug 还没下过单，则按固定参数下 BUY 单（默认价格 `0.2`、金额 `$1`）
4. 轮询订单状态，检测到成交就打印

先设置私钥：

```bash
set TOKTOK_PRIVATE_KEY=你的私钥
```

然后启动：

```bash
python run.py --trade-loop
```

可选参数：

- `--buy-price`（默认 `0.2`）
- `--buy-usd`（默认 `1.0`）
- `--poll-interval`（默认 `5` 秒）
- `--clob-host`（默认 `https://clob.polymarket.com`）
- `--chain-id`（默认 `137`）

## 可选：安装开发依赖

```bash
pip install -e .[dev]
pytest
```

## 错误处理

- `PolymarketNotFoundError`：找不到指定 `slug`
- `PolymarketRequestError`：超时、网络错误等请求失败
- `PolymarketAPIError`：返回非 2xx、非法 JSON 或异常响应结构
- `OKXConfigError`：缺少 OKX 环境变量或交易凭证不完整
- `OKXError`：OKX SDK 缺失或接口返回异常
