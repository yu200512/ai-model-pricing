# AI 模型定价数据管道文档

> 本文档描述从原始数据采集到扣子技能消费的完整数据流设计。

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                         采集端（本地 / CI）                           │
│                                                                      │
│   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐   │
│   │ refresh-      │     │ HTML 解析     │     │ Markdown 生成   │   │
│   │ pricing.sh   │────▶│ (Python)      │────▶│ + 定价表.md      │   │
│   └──────────────┘     └──────────────┘     └────────┬─────────┘   │
│         │                                           │               │
│         │  ┌──────────────┐                        │               │
│         └─▶│ Playwright    │                        │               │
│            │ scraper       │────────────────────────▶               │
│            └──────────────┘                                   │       │
└──────────────────────────────────────────────────────────────│───────┘
                                                                   │
                                                                   ▼
                                              ┌─────────────────────────────────┐
                                              │        GitHub 仓库               │
                                              │  yu200512/ai-model-pricing       │
                                              │                                  │
                                              │  pricing_data.json  ← 结构化数据  │
                                              │  定价表.md         ← 原始对照    │
                                              │  schemas/          ← Schema 定义 │
                                              │  scrapers/         ← 采集脚本   │
                                              └──────────────┬──────────────────┘
                                                             │ raw.githubusercontent.com
                                                             │ (CDN，全球可访问)
                                                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         扣子技能端（Coze Bot）                         │
│                                                                      │
│   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐   │
│   │ 用户查询      │────▶│ 意图识别      │────▶│ 定价数据查询     │   │
│   │ "Kimi K2多少" │     │ (LLM 解析)    │     │ (拉取 JSON)      │   │
│   └──────────────┘     └──────────────┘     └────────┬─────────┘   │
│                                                       │               │
│                                                       ▼               │
│                                              ┌──────────────────┐   │
│                                              │ Markdown 格式化   │   │
│                                              │ 回复用户          │   │
│                                              └──────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 数据流各阶段说明

### Stage 1：本地采集（refresh-pricing.sh）

**输入：** 供应商官方定价页面（HTML/SPA）

**处理：**

| 供应商 | 采集方式 | 备注 |
|--------|---------|------|
| DeepSeek | `curl` 直接拉 HTML | 静态页面，可直接解析 |
| OpenAI | `curl` 直接拉 HTML | 静态页面 |
| Anthropic | `curl` 直接拉 HTML | 静态页面 |
| xAI/Grok | `curl` 直接拉 HTML | 静态页面 |
| Google | `curl` 直接拉 HTML | 静态页面 |
| 智谱AI | Playwright headless | SPA，DOM 由 JS 渲染 |
| Qwen | Playwright headless | SPA |
| Kimi | Playwright headless | SPA |
| 星火 | Playwright headless | SPA |
| 百川 | Playwright headless | SPA |
| MiniMax | Playwright headless | SPA |
| 小米 | Playwright headless | SPA |
| 腾讯云 | Playwright headless | SPA |

**输出：** `定价表.md`（原始 Markdown 对照文件）

---

### Stage 2：结构化解析（新增步骤）

> 这是从纯 Markdown 到结构化 JSON 的关键一步。

**输入：** `定价表.md`

**输出：** `pricing_data.json`

**处理脚本：** `scripts/parse_pricing_to_json.py`

```python
# 解析流程伪代码

def parse_markdown_to_json(md_content: str) -> dict:
    """
    将 定价表.md 解析为 pricing_data.json
    策略：正则 + 分供应商分块解析
    """
    data = {
        "version": "1.0.0",
        "updated": extract_date(md_content),
        "sourceRepo": "https://github.com/yu200512/ai-model-pricing",
        "providers": []
    }

    # 按 ## N、供应商名 分块
    sections = split_by_provider(md_content)  # ["## 一、DeepSeek ...", "## 二、智谱AI ..."]

    for section in sections:
        provider = parse_provider_block(section)
        data["providers"].append(provider)

    return data


def parse_provider_block(section: str) -> dict:
    """
    解析单个供应商块
    难点：
    - 同一供应商下有多个模型（model）
    - 每个模型可能有多个定价 tier（按上下文分段）
    - 不同模型 type 不同（text/vision/video/tts/embedding...）
    """
    # 提取元信息
    provider_name = extract_h2_title(section)
    data_source_url = extract_url(section)
   采集方式 = extract_method(section)
    currency = detect_currency(section)  # USD / CNY / MIXED

    # 提取模型表格
    tables = extract_markdown_tables(section)

    models = []
    for table in tables:
        # table.rows = [{"col": val}, ...]
        model = parse_model_table(table, provider_name)
        models.append(model)

    return {
        "name": provider_name,
        "id": normalize_id(provider_name),
        "category": "国内" if is_chinese_provider(provider_name) else "国外",
        "currency": currency,
        "dataSource": {
            "url": data_source_url,
            "method": 采集方式,
            "verifiedAt": datetime.now().isoformat()
        },
        "models": models
    }


def parse_model_table(table: MarkdownTable, provider: str) -> dict:
    """
    通用 Markdown 表格 → model 对象
    识别逻辑：
    1. 表头列名判断模型 type
    2. 根据列名推断字段映射（输入/输出/缓存）
    3. 根据 provider 识别 billingUnit
    """
    # 表格列名标准化（各家列名不一致）
    col_map = normalize_columns(table.headers, provider)

    rows = table.rows

    # 如果多行代表同一模型不同定价层级，合并为 pricing tiers
    tiers = []
    for row in rows:
        tier = build_pricing_tier(row, col_map, provider)
        tiers.append(tier)

    return {
        "name": extract_model_name(rows[0], col_map),
        "type": infer_model_type(table.headers),
        "pricing": tiers
    }
```

**设计要点：**

1. **表格列名标准化映射**：各家用不同列名，需要映射到统一字段
   - `"输入价格"`, `"输入"`, `"Input"` → `input`
   - `"输出价格"`, `"输出"`, `"Output"` → `output`
   - `"缓存命中"`, `"缓存读取"`, `"Cache Hit"` → `cacheHit`

2. **上下文分段识别**：智谱/腾讯云等按 32K/128K/200K 分段，每段独立 tier

3. **货币识别**：百川用「元/千tokens」，需要在输出时换算为标准单位

4. **非 token 定价模型**：CogView-4（¥0.06/次）、CogTTS（¥4/万字符）等用 `unit: "per_call"` / `"per_character"`

5. **过渡方案**：在完整 HTML 解析器成熟前，用正则从 Markdown 表格提取数据，减少人工维护成本

---

### Stage 3：GitHub 存储

**仓库：** `yu200512/ai-model-pricing`

**文件结构：**

```
ai-model-pricing/
├── README.md
├── pricing_data.json          ← 主数据文件（技能直接消费）
├── 定价表.md                   ← 原始 Markdown 备份
├── schemas/
│   └── pricing_schema.json    ← JSON Schema 定义
├── scrapers/
│   ├── deepseek.sh
│   ├── zhipuai.js
│   ├── qwen.js
│   ├── kimi.js
│   ├── xinghuo.js
│   ├── baichuan.js
│   ├── minimax.js
│   ├── xiaomi.js
│   ├── hunyuan.js
│   ├── openai.sh
│   ├── anthropic.sh
│   ├── xai.sh
│   └── google.sh
├── scripts/
│   ├── refresh-pricing.sh     ← 主脚本，调用各 scraper
│   └── parse_pricing_to_json.py  ← Markdown → JSON 解析
└── .github/
    └── workflows/
        └── ci.yml             ← 定时 CI，自动抓取+提交
```

**更新机制：**

- **手动触发**：运行 `refresh-pricing.sh` → 生成 `定价表.md` → 运行 `parse_pricing_to_json.py` → 提交 PR
- **CI 自动**：GitHub Actions 定时（每天 UTC 00:00）执行采集流程，有变更则自动开 PR
- **数据消费**：`pricing_data.json` 通过 `raw.githubusercontent.com` 对外提供，无需认证

---

## Schema 字段说明

顶层字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | Schema 版本，遵循 semver |
| `updated` | string (date) | 数据最后更新日期 |
| `sourceRepo` | string (uri) | GitHub 仓库地址 |
| `providers` | array | 供应商数组 |

**Provider 对象：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 展示名称（如"DeepSeek"） |
| `id` | string | 唯一标识（如"deepseek"） |
| `category` | enum | `"国内"` 或 `"国外"` |
| `currency` | enum | `USD` / `CNY` / `MIXED` |
| `dataSource` | object | 采集来源和方式 |
| `models` | array | 该供应商下所有模型 |

**Model 对象：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模型名（如 `kimi-k2.6`） |
| `aliases` | array | 曾用名/别名 |
| `type` | enum | 模型主类型（见下） |
| `subtypes` | array | 附加标签（如 `thinking`） |
| `contextLength` | integer | 标准上下文窗口 |
| `maxOutput` | integer | 最大输出 token |
| `modalities` | array | 支持的模态 |
| `capabilities` | array | 能力标签 |
| `pricing` | array | 定价层级（≥1 个 tier） |
| `free` | boolean | 是否免费 |
| `deprecated` | boolean | 是否已弃用 |

**Model Type 枚举：**

| 值 | 说明 |
|----|------|
| `text` | 文本生成 |
| `vision` | 视觉理解（图像/视频/文件） |
| `video` | 视频生成 |
| `audio` | 音频处理 |
| `embedding` | 向量嵌入 |
| `tts` | 语音合成 |
| `stt` | 语音识别 |
| `image_generation` | 图像生成 |
| `music_generation` | 音乐生成 |
| `code` | 代码专用 |
| `reasoning` | 推理模型 |
| `search` | 搜索增强 |
| `fine_tuning` | 微调 |
| `realtime` | 实时对话 |
| `3d_generation` | 3D 生成 |
| `mixed` | 多模态混合 |

**PricingTier 对象（最小定价单元）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `contextRange` | object | 适用上下文范围，含 min/max |
| `billingMode` | string | 计费模式（standard/batch/free） |
| `input` | number | 输入单价 |
| `output` | number | 输出单价 |
| `unified` | number | 统一单价（合并输入输出） |
| `cacheHit` | number | 缓存命中单价 |
| `cacheWrite` | number | 缓存写入单价 |
| `batchDiscount` | number | Batch 折扣率 |
| `unit` | enum | 计费单位 |
| `currency` | enum | 货币类型 |
| `notes` | string | 补充说明 |

---

## 解析脚本设计思路

### 核心挑战

各供应商数据结构差异极大，无法用统一表格模板：

1. **DeepSeek**：极简，3 行表格（缓存命中/未命中/输出）
2. **智谱AI**：同一模型按上下文分段（≤32K / 32K+），不同类型分开（旗舰/视觉/Embedding/语音）
3. **百川**：用「元/千tokens」，且统一价无输入/输出拆分
4. **MiniMax 视频**：按视频秒数计费，非 token
5. **智谱语音**：按万字符计费

### 解析策略：Provider-Specific + 通用回退

```python
PARSERS = {
    "deepseek": parse_deepseek,
    "zhipuai":  parse_zhipuai,
    "qwen":     parse_qwen_generic,
    "kimi":     parse_kimi,
    "xinghuo":  parse_xinghuo,
    "baichuan": parse_baichuan,
    "minimax":  parse_minimax,
    "xiaomi":   parse_xiaomi,
    "hunyuan":  parse_hunyuan,
    "openai":   parse_openai,
    "anthropic": parse_anthropic,
    "xai":      parse_xai,
    "google":   parse_google,
}

def parse_provider_section(section_text: str) -> dict:
    provider_id = detect_provider(section_text)
    parser = PARSERS.get(provider_id, parse_generic)
    return parser(section_text)
```

### JSON Schema 验证

每次生成 JSON 后，用 `jsonschema` 库验证：

```python
import json, jsonschema

with open("schemas/pricing_schema.json") as f:
    schema = json.load(f)

with open("pricing_data.json") as f:
    data = json.load(f)

jsonschema.validate(data, schema)  # 无异常 = 通过
```

---

## GitHub 仓库文件结构规划

```
yu200512/ai-model-pricing/
│
├── pricing_data.json          ★ 扣子技能直接拉取此文件
│                              路径: raw.githubusercontent.com/yu200512/ai-model-pricing/main/pricing_data.json
│
├── 定价表.md                   原始 Markdown， humans can read，用于人工核对
│
├── schemas/
│   └── pricing_schema.json    JSON Schema，定义数据结构
│
├── scrapers/                   各供应商采集脚本
│   ├── deepseek.sh            curl → HTML → stdout markdown
│   ├── zhipuai.js             Playwright → innerText → stdout markdown
│   └── ...
│
├── scripts/
│   ├── refresh-pricing.sh     主脚本，调用所有 scraper，输出 定价表.md
│   ├── parse_pricing_to_json.py  解析 Markdown → JSON
│   └── validate_schema.py      JSON Schema 验证
│
├── .github/
│   └── workflows/
│       ├── scrape.yml         定时抓取（每天）
│       └── validate.yml       JSON Schema 验证 PR 检查
│
└── README.md
```

---

## 数据消费端（扣子技能）

技能通过一行 HTTP GET 获取最新数据：

```
GET https://raw.githubusercontent.com/yu200512/ai-model-pricing/main/pricing_data.json
```

扣子 Python 运行环境可直接用 `requests` 或 `urllib`，无需代理。

**数据量估算：** 13 家供应商 × 平均 20 个模型 × 结构化 JSON ≈ 30-50 KB（gzipped ~10KB），单次拉取 < 100ms。
