# 扣子（Coze）技能设计方案

---

## 一、技能定位

### 基本信息

| 项目 | 内容 |
|------|------|
| **技能名称** | AI Token Price |
| **名称拼音** | AI Jiage Chaxun |
| **一句话简介** | 查询全球主流 AI 模型最新 API 价格，支持比价和场景推荐 |
| **分类建议** | 工具类 → API 相关 |
| **适用平台** | 扣子 Bot（国内版） |
| **付费模式** | 免费（数据来源于公开信息，无额外成本） |

---

## 二、核心功能

### F1：按提供商 / 模型名查询定价

**触发示例：**

- "DeepSeek-V4-Pro 多少钱"
- "Kimi K2 的价格"
- "智谱 AI 所有模型价格"
- "OpenAI GPT-5.4 定价"

**输出示例：**

```
DeepSeek-V4-Pro 定价（数据更新：2026-05-02）

模型类型：文本生成 / 推理
上下文窗口：1M tokens
最大输出：384K tokens

| 计费项 | 折后价（当前） | 原价 |
|--------|--------------|------|
| 输入（缓存命中） | $0.003625 | $0.0145 |
| 输入（缓存未命中） | $0.435 | $1.74 |
| 输出 | $0.87 | $3.48 |

⚠️ 当前 75% 折扣，有效期至 2026/05/31 15:59 UTC
```

---

### F2：场景推荐（性价比分析）

**触发示例：**

- "翻译任务用哪个最便宜"
- "批量文本分类用什么模型划算"
- "长文档分析推荐"
- "性价比最高的推理模型"

**输出示例：**

```
💡 翻译任务推荐（2026-05-02 更新）

综合性价比 TOP 3：

🥇 GLM-4-FlashX-250414（智谱 AI）
   输入 ¥0.5/M | 输出 ¥3/M | 加权价 $0.014/M
   128K 上下文，Batch 可至 $0.007/M
   → 适合大批量翻译

🥈 Gemini 2.5 Flash-Lite（Google）
   $0.10/M 输入 | $0.40/M 输出
   免费额度可用，多语言能力强
   → 适合全球化项目

🥉 DeepSeek-V4-Flash
   $0.14/M 输入 | $0.28/M 输出
   1M 上下文，缓存命中仅 $0.0028/M
   → 适合中英对照翻译
```

---

### F3：两模型价格对比

**触发示例：**

- "DeepSeek-V4-Flash 和 Kimi K2.6 哪个便宜"
- "GPT-5.4 对比 Claude Sonnet 4.6"
- "智谱 GLM-5 和 Kimi K2.6 价格差多少"

**输出示例：**

```
模型对比：DeepSeek-V4-Flash vs Kimi K2.6

| 维度 | DeepSeek-V4-Flash | Kimi K2.6 |
|------|------------------|-----------|
| 供应商 | DeepSeek | 月之暗面 |
| 货币 | USD | CNY（≈$0.91） |
| 输入（缓存未命中） | $0.14/M | ¥6.5/M ≈ $0.90/M |
| 输出 | $0.28/M | ¥27/M ≈ $3.75/M |
| 上下文 | 1M | 262K |
| 免费额度 | ❌ | ❌ |

结论：DeepSeek-V4-Flash 输入比 Kimi K2.6 便宜 6.4 倍，
      输出便宜 13.4 倍。
```

---

### F4：全量定价表展示

**触发示例：**

- "所有国内模型价格"
- "列出 OpenAI 所有模型"
- "腾讯云 TokenHub 定价"

**输出：** Markdown 表格，支持按供应商 / 类型筛选

---

## 三、技术实现

### 3.1 数据拉取

```python
import json, urllib.request

def fetch_pricing_data() -> dict:
    url = (
        "https://raw.githubusercontent.com/"
        "yu200512/ai-model-pricing/main/pricing_data.json"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())
```

> 扣子 Python 节点支持 `urllib` / `requests`，无需额外安装依赖。

### 3.2 意图识别

用户 query → LLM（扣子内置）→ 意图标签

```python
INTENT_PATTERNS = {
    "query_price": [
        "多少钱", "价格", "定价", "cost", "price", "报价"
    ],
    "compare": [
        "对比", "比较", "哪个便宜", "哪个划算", "vs", "对比",
        "差别", "差多少"
    ],
    "recommend": [
        "推荐", "适合", "性价比", "划算", "最便宜", "最好用"
    ],
    "list_all": [
        "所有", "全部", "列出", "list"
    ]
}

def parse_intent(user_message: str) -> str:
    msg_lower = user_message.lower()
    for intent, keywords in INTENT_PATTERNS.items():
        if any(kw in msg_lower for kw in keywords):
            return intent
    return "unknown"
```

### 3.3 模型查询

```python
def find_model(query: str, data: dict) -> list[dict]:
    """
    模糊匹配模型名
    1. 精确匹配 model.name
    2. 模糊匹配 model.aliases
    3. 模糊包含匹配
    """
    results = []
    q = query.lower().replace(" ", "").replace("-", "")

    for provider in data["providers"]:
        for model in provider["models"]:
            name = model["name"].lower().replace(" ", "").replace("-", "")
            if q in name or name in q:
                results.append({**model, "_provider": provider["name"]})
                continue

            # aliases 匹配
            for alias in model.get("aliases", []):
                a = alias.lower().replace(" ", "").replace("-", "")
                if q in a or a in q:
                    results.append({**model, "_provider": provider["name"]})
                    break

    return deduplicate_by_name(results)
```

### 3.4 性价比推荐

```python
def compute_weighted_price(model: dict, currency: str = "USD") -> float:
    """
    加权价 = 输入 × 0.7 + 输出 × 0.3
    符合典型用户消费比（输入多输出少）
    汇率: 1 USD = 7.2 CNY
    """
    rates = {"USD": 1.0, "CNY": 1/7.2}
    fx = rates.get(currency, 1.0)

    # 取标准上下文 tier（第一个 non-batch, non-free）
    tier = next(
        (t for t in model["pricing"]
         if t.get("billingMode") != "free"
         and t.get("billingMode") != "batch"),
        model["pricing"][0]
    )

    input_p = (tier.get("input") or tier.get("unified") or 0) * fx
    output_p = (tier.get("output") or tier.get("unified") or 0) * fx
    return input_p * 0.7 + output_p * 0.3


def recommend_for_task(task: str, data: dict) -> list[dict]:
    """
    按场景关键词匹配适合的模型
    task: "翻译" / "长文" / "推理" / "代码" 等
    """
    TASK_MODEL_TYPES = {
        "翻译":          ["text"],
        "文本分类":       ["text"],
        "长文档":         ["text"],
        "推理":           ["reasoning", "text"],
        "代码":           ["code", "text"],
        "图像理解":       ["vision"],
        "图像生成":       ["image_generation"],
        "视频生成":       ["video"],
        "语音合成":       ["tts"],
        "语音识别":       ["stt"],
        "向量检索":       ["embedding"],
    }

    types = TASK_MODEL_TYPES.get(task, ["text"])

    candidates = []
    for provider in data["providers"]:
        for model in provider["models"]:
            if model.get("free") or model.get("deprecated"):
                continue
            if model.get("type") in types or types[0] == "text":
                candidates.append(model)

    # 按加权价排序
    ranked = sorted(
        candidates,
        key=lambda m: compute_weighted_price(m)
    )
    return ranked[:5]
```

### 3.5 响应格式化

```python
def format_model_table(models: list[dict]) -> str:
    """
    输出 Markdown 表格
    """
    lines = [
        "| 模型 | 供应商 | 类型 | 输入 | 输出 | 上下文 | 备注 |",
        "|------|--------|------|------|------|--------|------|"
    ]
    for m in models:
        p = m["_provider"]
        tier = m["pricing"][0]
        unit = tier.get("unit", "per_1m_tokens")
        unit_label = {
            "per_1m_tokens": "/M tokens",
            "per_1k_tokens": "/K tokens",
            "per_call":      "/次",
            "per_minute":    "/分钟",
        }.get(unit, "")

        input_val = tier.get("input") or tier.get("unified") or "—"
        output_val = tier.get("output") or tier.get("unified") or "—"
        ctx = f"{m.get('contextLength', 0)//1000}K"

        notes = m.get("notes", "")
        if m.get("free"):
            notes = "🆓 免费"

        lines.append(
            f"| {m['name']} | {p} | {m['type']} | "
            f"{input_val}{unit_label} | {output_val}{unit_label} | "
            f"{ctx} | {notes} |"
        )

    return "\n".join(lines)
```

---

## 四、SKILL.md（扣子技能标准格式）

```markdown
# AI Token Price

## 技能名称
AI Token Price（AI Jiage Chaxun）

## 一句话简介
查询全球 AI 模型 API 价格，支持比价和场景推荐

## 详细介绍
AI Token Price是一个实时追踪全球 14 家主流 AI 厂商最新 API 定价的技能。覆盖 DeepSeek、智谱 AI、Qwen、Kimi、腾讯云等国内厂商，以及 OpenAI、Anthropic、Google Gemini 等海外厂商。支持按模型名查询、两模型比价、按场景推荐最具性价比方案。

数据每日自动更新，来源为各厂商官方定价页面。

## 3 个使用案例

### 案例 1：查某模型价格
用户：「DeepSeek-V4-Pro 现在多少钱」
回复：展示 DeepSeek-V4-Pro 输入/输出/缓存定价，含折扣状态

### 案例 2：场景推荐
用户：「我想做批量文本分类，哪个模型最划算」
回复：列出性价比 TOP 3 模型，含加权价格对比和推荐理由

### 案例 3：两模型比价
用户：「DeepSeek 和 Kimi K2 哪个便宜」
回复：对比两个模型的输入/输出价格，给出结论
```

---

## 五、必要文件清单

扣子技能包需要以下文件（打包上传至扣子平台）：

```
ai-token-price/
│
├── SKILL.md                    ★ 技能说明文件（必填）
├── README.md                    技能详细介绍
├── icon.png                     技能图标（建议 200×200px，PNG）
│
├── src/
│   ├── __init__.py
│   ├── main.py                 ★ 入口，扣子 Bot 调用此脚本
│   ├── fetcher.py              数据拉取
│   ├── parser.py               意图解析 + 模型查询
│   ├── formatter.py            Markdown 表格格式化
│   └── recommender.py           性价比分析
│
├── data/
│   └── pricing_schema.json     Schema 副本（用于本地验证）
│
└── tests/
    ├── test_parser.py          意图解析单元测试
    ├── test_recommender.py     推荐逻辑测试
    └── test_formatter.py       格式化输出测试
```

> 扣子平台的 Python 技能直接上传 zip 包即可，平台自动解压运行。

---

## 六、main.py 参考实现

```python
"""
AI Token Price - 扣子技能入口
接收用户消息，返回 Markdown 格式的定价回复
"""

import json
from typing import Optional

# 数据拉取（缓存避免重复请求）
_pricing_data: Optional[dict] = None


def get_pricing_data() -> dict:
    global _pricing_data
    if _pricing_data is not None:
        return _pricing_data

    import urllib.request
    url = (
        "https://raw.githubusercontent.com/"
        "yu200512/ai-model-pricing/main/pricing_data.json"
    )
    with urllib.request.urlopen(url, timeout=15) as resp:
        _pricing_data = json.loads(resp.read().decode())
    return _pricing_data


def handle(message: str) -> str:
    """
    扣子 Bot 调用入口
    message: 用户原始输入
    返回: Markdown 字符串
    """
    data = get_pricing_data()
    msg = message.strip()

    # 意图识别
    intent = detect_intent(msg)

    if intent == "query_price":
        return handle_price_query(msg, data)
    elif intent == "compare":
        return handle_compare(msg, data)
    elif intent == "recommend":
        return handle_recommend(msg, data)
    elif intent == "list_all":
        return handle_list_all(msg, data)
    else:
        return (
            "🤔 抱歉，我还没理解您的问题。\n\n"
            "试试这样问我：\n"
            "• 「DeepSeek-V4-Pro 多少钱」\n"
            "• 「翻译用什么模型便宜」\n"
            "• 「GPT-5.4 和 Claude 比价」\n"
            "• 「列出所有国内模型价格」"
        )


def detect_intent(msg: str) -> str:
    msg_l = msg.lower()
    if any(k in msg_l for k in ["多少钱", "价格", "定价", "报价", "price", "cost"]):
        return "query_price"
    if any(k in msg_l for k in ["对比", "比较", "哪个便宜", "vs", "哪个划算"]):
        return "compare"
    if any(k in msg_l for k in ["推荐", "适合", "最便宜", "划算", "性价比", "用什么"]):
        return "recommend"
    if any(k in msg_l for k in ["所有", "全部", "列出", "list"]):
        return "list_all"
    return "query_price"  # 默认走查询
```

---

## 七、上架准备清单

### 7.1 封面图建议

| 要求 | 说明 |
|------|------|
| 尺寸 | 300×300px（扣子要求） |
| 构图 | 左侧：💰/📊 图标元素；右侧：留白放技能名 |
| 配色 | 深色背景（#1A1A2E）+ 金色/绿色数字强调 |
| 文字 | 封面不放文字，技能名在技能描述里 |
| 参考素材 | 💹 📈 💲 🔍 🤖 |

### 7.2 分类建议

```
一级分类：效率工具
二级分类：AI & 机器学习
标签：API、价格查询、AI、LLM、比价
```

### 7.3 付费模式建议

**推荐：完全免费**

- 成本极低（仅 HTTP GET 请求，无存储/计算费用）
- 数据来自公开信息，无版权问题
- 免费有利于快速积累用户量

**如需付费：** 建议采用「免费版每日 50 次查询 + Pro 版无限」的阶梯模式

### 7.4 定价建议（如付费）

| 套餐 | 价格 | 说明 |
|------|------|------|
| 免费版 | ¥0 | 每日 50 次，限基础查询 |
| Pro 版 | ¥6/月 或 ¥50/年 | 无限查询，含全量比价报告 |

---

## 八、运营与维护

### 数据更新机制

| 方式 | 频率 | 说明 |
|------|------|------|
| GitHub Actions CI | 每日 1 次 | 自动抓取 → 自动开 PR |
| 人工复核 | 按需 | CI 的 PR 需人工 review 后合并 |
| 数据时效 | 每日 | `updated` 字段记录日期 |

### 常见问题（FAQ）

**Q: 数据多久更新一次？**
A: 每日自动更新（GitHub Actions 定时任务）。

**Q: 为什么价格和官网不一致？**
A: 请附上具体模型名和官网链接，收到后会在 24h 内核实更新。

**Q: 支持查询历史价格吗？**
A: 暂不支持，当前只提供最新定价。

**Q: 非中国区的 DeepSeek 价格如何？**
A: 当前数据为国际版定价（美元），如需中国版请联系补充。
