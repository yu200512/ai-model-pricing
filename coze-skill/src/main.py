"""Coze skill entry for AI model pricing lookup.

Only standard-library modules are required.  The skill uses a hybrid strategy:
Chinese/domestic providers try their official pricing pages first as a freshness
probe, then all structured data is read from the latest GitHub JSON fallback;
overseas providers only use GitHub JSON.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USD_TO_CNY = 7.2
CACHE_TTL_SECONDS = 300
GITHUB_JSON_URL = "https://raw.githubusercontent.com/yu200512/ai-model-pricing/main/pricing_data.json"
LOCAL_JSON_PATH = Path(__file__).resolve().parents[2] / "pricing_data.json"

DOMESTIC_PROVIDERS = {
    "DeepSeek": "https://api-docs.deepseek.com/quick_start/pricing",
    "智谱AI": "https://open.bigmodel.cn/pricing",
    "Z.ai": "https://open.bigmodel.cn/pricing",
    "Qwen": "https://modelstudio.console.alibabacloud.com/ap-southeast-1/?tab=doc#/doc/?type=model&url=prices",
    "Kimi": "https://platform.kimi.com/docs/pricing/chat-k26",
    "星火": "https://xinghuo.xfyun.cn/sparkapi",
    "百川": "https://platform.baichuan-ai.com/prices",
    "MiniMax": "https://platform.minimaxi.com/docs/guides/pricing-paygo",
    "小米": "https://platform.xiaomimimo.com/docs/zh-CN/pricing",
    "腾讯云": "https://cloud.tencent.com/document/product/1823/130055",
    "字节豆包": "https://www.volcengine.com/docs/82379/1544106",
    "火山引擎": "https://www.volcengine.com/docs/82379/1544106",
}
FOREIGN_KEYWORDS = ("OpenAI", "Anthropic", "Claude", "xAI", "Grok", "Google", "Gemini")

_CACHE: Dict[str, Any] = {"expires": 0.0, "data": None, "source": ""}


def http_get(url: str, timeout: int = 10) -> str:
    """Fetch URL text with a browser-like User-Agent and timeout."""
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 CozePricingSkill/1.0"})
    with urlopen(request, timeout=timeout) as response:  # nosec - fixed URLs/user query not interpolated except fallback URL constant
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def probe_domestic_sources(message: str) -> Dict[str, str]:
    """Try direct official-page fetches for domestic providers mentioned by user.

    The live pages are heterogeneous/SPA-heavy, so this function records whether
    a direct freshness probe succeeded and lets GitHub JSON remain the structured
    fallback used for answer generation.
    """
    result: Dict[str, str] = {}
    for name, url in DOMESTIC_PROVIDERS.items():
        if name.lower() in message.lower():
            try:
                text = http_get(url, timeout=10)
                result[name] = "官方页可访问" if len(text) > 200 else "官方页内容过短，已回退 GitHub"
            except (HTTPError, URLError, TimeoutError, OSError):
                result[name] = "官方页访问失败，已回退 GitHub"
    return result


def load_pricing_data() -> Dict[str, Any]:
    """Load and cache latest JSON from GitHub raw URL for five minutes."""
    now = time.time()
    if _CACHE.get("data") and now < float(_CACHE.get("expires", 0)):
        return _CACHE["data"]
    try:
        body = http_get(GITHUB_JSON_URL, timeout=10)
        data = json.loads(body)
        _CACHE.update({"data": data, "expires": now + CACHE_TTL_SECONDS, "source": "GitHub"})
        return data
    except Exception as exc:  # broad by design: skill must return Markdown, not crash Coze
        cached = _CACHE.get("data")
        if cached:
            return cached
        if LOCAL_JSON_PATH.exists():
            try:
                data = json.loads(LOCAL_JSON_PATH.read_text(encoding="utf-8"))
                _CACHE.update({"data": data, "expires": now + CACHE_TTL_SECONDS, "source": "local"})
                return data
            except Exception:
                pass
        return {"version": "unknown", "updated": "unknown", "providers": [], "error": str(exc)}


def normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def iter_models(data: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Yield provider/model pairs from pricing JSON."""
    for provider in data.get("providers", []):
        for model in provider.get("models", []):
            yield provider, model


def tier_to_usd(tier: Dict[str, Any], key: str) -> Optional[float]:
    """Return a tier price as USD, converting CNY with the fixed exchange rate."""
    value = tier.get(key)
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    currency = tier.get("currency")
    if currency == "CNY":
        return price / USD_TO_CNY
    return price


def best_text_cost(model: Dict[str, Any]) -> Optional[float]:
    """Compute weighted USD text cost: input*0.7 + output*0.3, or unified."""
    best: Optional[float] = None
    for tier in model.get("pricing", []):
        unified = tier_to_usd(tier, "unified")
        inp = tier_to_usd(tier, "input")
        out = tier_to_usd(tier, "output")
        if unified is not None:
            cost = unified
        elif inp is not None and out is not None:
            cost = inp * 0.7 + out * 0.3
        else:
            continue
        if tier.get("unit") not in ("per_1m_tokens", "per_1k_tokens"):
            continue
        if tier.get("unit") == "per_1k_tokens":
            cost *= 1000
        best = cost if best is None else min(best, cost)
    return best


def format_price_value(value: Optional[float], currency: str, unit: str) -> str:
    """Format one price with USD conversion for CNY prices."""
    if value is None:
        return "—"
    if currency == "CNY":
        return f"¥{value:g}（约 ${value / USD_TO_CNY:.4g}）/{unit_label(unit)}"
    if currency == "USD_CNY":
        return f"{value:g}/{unit_label(unit)}"
    return f"${value:g}/{unit_label(unit)}"


def unit_label(unit: str) -> str:
    """Human-readable billing unit label."""
    labels = {
        "per_1m_tokens": "1M tokens",
        "per_1k_tokens": "1K tokens",
        "per_call": "次",
        "per_minute": "分钟",
        "per_character": "字符",
        "per_video_second": "秒",
        "per_image": "张",
        "per_1k_characters": "1K字符",
        "per_10k_characters": "万字符",
        "fixed": "固定价",
    }
    return labels.get(unit, unit)


def format_model(provider: Dict[str, Any], model: Dict[str, Any]) -> str:
    """Render one model as Markdown."""
    lines = [f"### {model.get('name', '未知模型')}", f"- 供应商：{provider.get('name', '未知')}"]
    if provider.get("category") == "国外":
        lines.append(f"- 数据来源：GitHub JSON（更新：{provider.get('dataSource', {}).get('verifiedAt', '未知')}）")
    else:
        lines.append("- 数据来源：国内供应商优先官方页探测，结构化数据来自 GitHub JSON 兜底")
    if model.get("contextLength"):
        lines.append(f"- 上下文：{model['contextLength']:,} tokens")
    if model.get("type"):
        lines.append(f"- 类型：{model['type']}")
    lines.append("| 计费 | 输入 | 输出 | 统一价 | 缓存命中 |")
    lines.append("|---|---:|---:|---:|---:|")
    for tier in model.get("pricing", [])[:4]:
        unit = tier.get("unit", "per_1m_tokens")
        currency = tier.get("currency", provider.get("currency", "USD"))
        desc = tier.get("contextRange", {}).get("description", tier.get("billingMode", "标准"))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(desc or "标准"),
                    format_price_value(tier.get("input"), currency, unit),
                    format_price_value(tier.get("output"), currency, unit),
                    format_price_value(tier.get("unified"), currency, unit),
                    format_price_value(tier.get("cacheHit"), currency, unit),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def find_matches(data: Dict[str, Any], query: str, limit: int = 8) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Fuzzy match by model name, alias, or provider name."""
    cleaned_query = re.sub(r"(价格|定价|多少钱|费用|查询|查一下)", "", query, flags=re.I)
    q = normalize(cleaned_query or query)
    if not q:
        return []
    scored: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for provider, model in iter_models(data):
        hay = normalize(" ".join([provider.get("name", ""), provider.get("id", ""), model.get("name", ""), " ".join(model.get("aliases", []))]))
        if q in hay:
            score = 100 + len(q)
        elif hay in q:
            score = 80 + len(hay)
        else:
            parts = [p for p in re.split(r"\s+", query) if len(p) >= 2]
            score = sum(10 for p in parts if normalize(p) in hay)
        if score > 0:
            scored.append((score, provider, model))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(p, m) for _, p, m in scored[:limit]]


def detect_intent(message: str) -> str:
    """Classify user intent into query_price/compare/recommend/list_all."""
    text = message.lower()
    if any(k in message for k in ("全部", "列表", "所有", "供应商")) or "list" in text:
        return "list_all"
    if any(k in message for k in ("对比", "比较", "compare", " vs ", "VS", "和")):
        return "compare"
    if any(k in message for k in ("推荐", "性价比", "便宜", "适合", "recommend")):
        return "recommend"
    return "query_price"


def extract_compare_terms(message: str) -> List[str]:
    """Extract up to two model/provider terms for comparison."""
    chunks = re.split(r"\s*(?:对比|比较|compare|vs|VS|和|与|,|，)\s*", message)
    terms = [re.sub(r"(价格|定价|多少钱|哪个|更|便宜|贵|模型)", "", c).strip() for c in chunks]
    return [t for t in terms if len(t) >= 2][:2]


def answer_query(data: Dict[str, Any], message: str) -> str:
    """Answer a price query."""
    matches = find_matches(data, message, limit=5)
    if not matches:
        return "## 未找到匹配模型\n\n请换一个模型名或供应商名，例如：`gpt-5.4`、`DeepSeek`、`Gemini 2.5 Flash`。"
    lines = ["## 价格查询结果"]
    for provider, model in matches:
        lines.append(format_model(provider, model))
    return "\n\n".join(lines)


def answer_compare(data: Dict[str, Any], message: str) -> str:
    """Answer a two-model comparison."""
    terms = extract_compare_terms(message)
    selected: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for term in terms:
        found = find_matches(data, term, limit=1)
        if found:
            selected.append(found[0])
    if len(selected) < 2:
        return "## 需要两个模型才能对比\n\n示例：`对比 gpt-5.4 和 Claude Sonnet 4.6`"
    lines = ["## 模型价格对比", "| 模型 | 供应商 | 最优加权价（USD/1M tokens） | 类型 | 上下文 |", "|---|---|---:|---|---:|"]
    for provider, model in selected[:2]:
        cost = best_text_cost(model)
        lines.append(f"| {model.get('name')} | {provider.get('name')} | {('$%.4g' % cost) if cost is not None else '—'} | {model.get('type', '—')} | {model.get('contextLength', '—')} |")
    lines.append("\n> 加权价按 输入×0.7 + 输出×0.3 估算；人民币按 1 USD ≈ 7.2 CNY 换算。")
    return "\n".join(lines)


def answer_recommend(data: Dict[str, Any], message: str) -> str:
    """Recommend top three cost-effective models for a scenario."""
    text = message.lower()
    candidates: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    for provider, model in iter_models(data):
        mtype = model.get("type", "")
        caps = " ".join(model.get("capabilities", [])).lower()
        if any(k in message for k in ("图像", "图片", "视觉")) and mtype not in ("vision", "image_generation"):
            continue
        if any(k in message for k in ("语音", "音频", "tts", "asr")) and mtype not in ("audio", "tts", "stt", "realtime"):
            continue
        if any(k in message for k in ("推理", "思考", "reason")) and "thinking" not in caps and mtype != "reasoning":
            continue
        if any(k in message for k in ("代码", "code")) and "code" not in caps and mtype != "code":
            continue
        cost = best_text_cost(model)
        if cost is not None:
            candidates.append((cost, provider, model))
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return "## 暂无可推荐结果\n\n当前数据里没有匹配该场景且可计算 token 单价的模型。"
    lines = ["## 性价比推荐 TOP 3", "| 排名 | 模型 | 供应商 | 估算价 | 理由 |", "|:--:|---|---|---:|---|"]
    for idx, (cost, provider, model) in enumerate(candidates[:3], 1):
        reason = "低 token 成本"
        if model.get("contextLength") and int(model["contextLength"]) >= 200000:
            reason += "，长上下文"
        if model.get("capabilities"):
            reason += "，" + "/".join(model["capabilities"][:2])
        lines.append(f"| {idx} | {model.get('name')} | {provider.get('name')} | ${cost:.4g}/1M tokens | {reason} |")
    lines.append("\n> 价格统一折算为 USD；实际账单请以官方页面为准。")
    return "\n".join(lines)


def answer_list_all(data: Dict[str, Any]) -> str:
    """Return all models grouped by provider."""
    lines = ["## AI 模型价格库总览"]
    for provider in data.get("providers", []):
        models = provider.get("models", [])
        lines.append(f"\n### {provider.get('name')}（{len(models)} 个）")
        names = [m.get("name", "未知") for m in models[:20]]
        lines.append("- " + "、".join(names))
        if len(models) > 20:
            lines.append(f"- ……另有 {len(models) - 20} 个")
    lines.append(f"\n> 数据更新时间：{data.get('updated', '未知')}；国外供应商仅使用 GitHub JSON。")
    return "\n".join(lines)


def handle(message: str) -> str:
    """Coze skill entry point; return Markdown response for a user message."""
    message = (message or "").strip()
    data = load_pricing_data()
    if data.get("error") and not data.get("providers"):
        return f"## 数据加载失败\n\n无法从 GitHub 拉取价格 JSON：`{data['error']}`"

    probes = probe_domestic_sources(message)
    intent = detect_intent(message)
    if intent == "compare":
        answer = answer_compare(data, message)
    elif intent == "recommend":
        answer = answer_recommend(data, message)
    elif intent == "list_all":
        answer = answer_list_all(data)
    else:
        answer = answer_query(data, message)

    footer = [f"\n---\n_数据版本：{data.get('version', 'unknown')}，更新：{data.get('updated', 'unknown')}，查询时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"]
    if probes:
        footer.append("_官方页探测：" + "；".join(f"{k}={v}" for k, v in probes.items()) + "_")
    if any(k.lower() in message.lower() for k in FOREIGN_KEYWORDS):
        footer.append("_注：国外供应商按要求仅从 GitHub JSON 读取。_")
    return answer + "\n" + "\n".join(footer)


if __name__ == "__main__":
    print(handle("推荐便宜的文本模型"))
