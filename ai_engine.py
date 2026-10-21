"""
ai_engine.py — LLM API 调用与结构化解析
支持 DeepSeek API (OpenAI 兼容) 和 Claude API
"""
import os
import json
import base64
import time
from openai import OpenAI
import anthropic


def _deepseek_client(api_key, base_url=None):
    return OpenAI(
        api_key=api_key,
        base_url=base_url or os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    )


def _claude_client(api_key):
    return anthropic.Anthropic(api_key=api_key)


# ── Paper Summarization ──

SUMMARIZE_SYSTEM = """你是一位电化学/有机化学领域的资深研究者。请为学术论文生成精炼的结构化中文总结。
严格按以下 JSON 格式输出（不要输出任何其他内容，不要用 markdown 代码块包裹）：
{
  "title": "论文标题的中文翻译",
  "journal": "期刊名缩写 年份",
  "authors": "第一作者 et al.",
  "summary": "## 研究背景\\n\\n(2-3句)\\n\\n## 关键方法\\n\\n(3-5句，含催化剂/电极/条件)\\n\\n## 主要发现\\n\\n(3-5句)\\n\\n## 创新点\\n\\n- 创新1\\n- 创新2\\n- 创新3",
  "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
  "reaction_types": ["反应类型1", "反应类型2"]
}"""


def summarize_paper(full_text: str, provider: str, api_key: str, model: str = None, base_url: str = None):
    """统一入口：用 LLM 总结论文"""
    text = (full_text or '')[:12000]
    prompt = f"请总结以下英文学术论文：\n\n{text}"

    if provider == 'deepseek':
        client = _deepseek_client(api_key, base_url)
        resp = client.chat.completions.create(
            model=model or 'deepseek-chat',
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    elif provider == 'claude':
        client = _claude_client(api_key)
        resp = client.messages.create(
            model=model or 'claude-sonnet-5',
            max_tokens=2000,
            system=SUMMARIZE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        # 移除可能的 markdown 代码块包裹
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            if raw.endswith('```'):
                raw = raw[:-3]
        return json.loads(raw)

    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Figure Description ──

DESCRIBE_SYSTEM = """You are a chemistry researcher. Describe the figure so it can be searched.
Output JSON only (no markdown):
{
  "description": "Detailed description of what the figure shows (3-6 sentences, English)",
  "figure_type": "one of: reaction_scheme, NMR_spectrum, CV_curve, XRD_pattern, SEM_image, TEM_image, mechanism_diagram, data_chart, table, molecular_structure, other",
  "key_observations": "Key peaks, values, trends, or structures visible",
  "search_tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
}"""


def describe_figure(image_path: str, caption: str, provider: str, api_key: str, model: str = None, base_url: str = None):
    """统一入口：用 Vision LLM 描述图片"""
    caption = caption or 'No caption available'

    if provider == 'claude':
        client = _claude_client(api_key)
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(image_path)[1].lower()
        mime = 'image/png' if ext == '.png' else 'image/jpeg'

        resp = client.messages.create(
            model=model or 'claude-sonnet-5',
            max_tokens=600,
            system=DESCRIBE_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"Describe this figure from a chemistry paper.\nCaption: {caption}"},
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": img_b64}},
            ]}],
        )
        raw = resp.content[0].text
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1]
            if raw.endswith('```'):
                raw = raw[:-3]
        return json.loads(raw)

    elif provider == 'deepseek':
        # DeepSeek 目前 vision 能力有限，基于 caption 做文本推断描述
        client = _deepseek_client(api_key, base_url)
        resp = client.chat.completions.create(
            model=model or 'deepseek-chat',
            messages=[
                {"role": "system", "content": DESCRIBE_SYSTEM},
                {"role": "user", "content": f"Figure caption: {caption}\n\nDescribe what this figure likely shows and suggest search tags."},
            ],
            temperature=0.3,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Paper Chat / Q&A ──

CHAT_SYSTEM_TEMPLATE = """你是一位国际顶尖学术期刊资深审稿人与研究导师（精通电化学、有机合成、纳米材料与催化领域）。
请根据下面提供的学术论文具体内容（包含题录、提取的图表图注清单及正文节选），回答用户的专业学术提问。

要求：
1. 严谨实事求是：直接依据提供的正文或图注内容作答，不得编造实验数据。若论文中未提及具体数值，请如实说明；
2. 实验细节精确：涉及合成摩尔比、催化剂负载量、电化学工作站参数（如电位范围、扫速、电解液浓度、参比电极类型）、产率/法拉第效率时，请明确列出具体数值、单位及实验条件；
3. 图表多模态关联：当用户问及某张图（如 Figure 3a、Scheme 2）时，结合图表清单中的高精度图注与正文讨论进行深度对比分析；
4. 结构清晰美观：使用 Markdown 格式（列表、加粗、对比表格等），必要时注明信息来源在论文中的对应段落或图号。

【论文背景与内容】：
{paper_context}
"""


def build_paper_context(paper: dict, figures: list = None, max_text_chars: int = 25000) -> str:
    """构建包含题录信息、所有图表高精度图注与正文的长上下文背景"""
    parts = []
    parts.append(f"# 论文信息\n- 标题: {paper.get('title', '未知')}")
    if paper.get('authors'):
        parts.append(f"- 作者: {paper.get('authors')}")
    if paper.get('journal'):
        parts.append(f"- 期刊: {paper.get('journal')}")
    if paper.get('year'):
        parts.append(f"- 发表年份: {paper.get('year')}")
    if paper.get('doi'):
        parts.append(f"- DOI: {paper.get('doi')}")

    if figures:
        parts.append("\n# 本文图表清单及对应图注 (Figures & Captions):")
        for f in figures:
            fig_idx = f.get('fig_index', '')
            cap = (f.get('caption') or '').strip()
            desc = f.get('ai_description', '')
            ftype = f.get('figure_type', '')
            info = f"- [Figure {fig_idx}]"
            if ftype:
                info += f" 类型: {ftype}"
            if cap:
                info += f" 图注: {cap}"
            if desc:
                try:
                    d_obj = json.loads(desc) if isinstance(desc, str) else desc
                    d_txt = d_obj.get('description', '') if isinstance(d_obj, dict) else str(desc)
                    if d_txt:
                        info += f" (内容解读: {d_txt[:120]})"
                except Exception:
                    pass
            parts.append(info)

    full_txt = paper.get('full_text', '') or ''
    if full_txt:
        parts.append(f"\n# 论文正文节选 (Full Text):\n{full_txt[:max_text_chars]}")

    return "\n".join(parts)


def chat_with_paper(paper: dict, figures: list, history: list, user_query: str,
                    provider: str, api_key: str, model: str = None, base_url: str = None) -> str:
    """与当前论文进行专业学术对话问答"""
    paper_ctx = build_paper_context(paper, figures)
    sys_prompt = CHAT_SYSTEM_TEMPLATE.format(paper_context=paper_ctx)

    if provider == 'deepseek':
        client = _deepseek_client(api_key, base_url)
        messages = [{"role": "system", "content": sys_prompt}]
        for h in history:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": user_query})

        resp = client.chat.completions.create(
            model=model or 'deepseek-chat',
            messages=messages,
            temperature=0.3,
            max_tokens=3000,
        )
        return resp.choices[0].message.content or ""

    elif provider == 'claude':
        client = _claude_client(api_key)
        claude_messages = []
        for h in history:
            claude_messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        claude_messages.append({"role": "user", "content": user_query})

        resp = client.messages.create(
            model=model or 'claude-sonnet-5',
            max_tokens=3000,
            system=sys_prompt,
            messages=claude_messages,
        )
        return resp.content[0].text or ""

    else:
        raise ValueError(f"Unknown provider: {provider}")


def copilot_chat(system_prompt: str, history: list, user_query: str,
                 provider: str, api_key: str, model: str = None, base_url: str = None) -> str:
    """全库与系统通用智能 Copilot 对话接口"""
    if provider == 'deepseek':
        client = _deepseek_client(api_key, base_url=base_url)
        messages = [{"role": "system", "content": system_prompt}]
        for turn in (history or [])[-8:]:
            messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        messages.append({"role": "user", "content": user_query})

        resp = client.chat.completions.create(
            model=model or 'deepseek-chat',
            messages=messages,
            temperature=0.3,
            max_tokens=2500,
        )
        return resp.choices[0].message.content or ""

    elif provider == 'claude':
        client = _claude_client(api_key)
        claude_messages = []
        for turn in (history or [])[-8:]:
            claude_messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
        claude_messages.append({"role": "user", "content": user_query})

        resp = client.messages.create(
            model=model or 'claude-sonnet-5',
            max_tokens=2500,
            system=system_prompt,
            messages=claude_messages,
        )
        return resp.content[0].text or ""
    else:
        raise ValueError(f"Unknown provider: {provider}")


if __name__ == '__main__':
    print('ai_engine.py ready')
