"""
api_manager.py — 本地持久化多 API 配置管理器
负责保存、读取、测试和切换用户的 LLM API 档案 (data/api_profiles.json)
支持 OpenAI 兼容格式 (DeepSeek, 月之暗面 Kimi, 通义千问, 本地 Ollama, OpenAI 等) 与 Claude API
"""
import os
import json
import uuid
import time
from typing import Dict, List, Optional, Tuple

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
PROFILES_FILE = os.path.join(DATA_DIR, "api_profiles.json")

# ------------------------------------------------------------------
# 环境自愈：NO_PROXY 里的 [::1] 会让 httpx 客户端直接构造失败
# ------------------------------------------------------------------
def sanitize_proxy_env() -> None:
    """修掉会让 httpx / OpenAI 客户端**构造**就失败的 NO_PROXY 写法。

    Clash、v2rayN 这类代理工具常把 IPv6 回环写成 [::1] 塞进 NO_PROXY。
    httpx 0.28 解析 no_proxy 时会按 ':' 切 host:port，"[::1]" 被切成
    host="[:"、port="1]"，接着 int("1]") 抛 InvalidURL: Invalid port: ':1]'。
    于是任何 OpenAI(...) / httpx.Client() 一 new 出来就炸，报错还伪装成
    "调用测试失败"，让人以为是 API Key 或 Base URL 的问题。
    这里把方括号剥掉（裸 ::1 不会被误切），其余条目原样保留。
    """
    for var in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(var)
        if not raw:
            continue
        out = []
        for item in raw.split(","):
            s = item.strip()
            if not s:
                continue
            if "[" in s or "]" in s:
                s = s.replace("[", "").replace("]", "").strip()
                if not s:
                    continue
            if s not in out:
                out.append(s)
        os.environ[var] = ",".join(out)


sanitize_proxy_env()

# 常见 API 预设模板，方便用户一键填入
DEFAULT_PRESETS = [
    {
        "id_preset": "deepseek",
        "name": "DeepSeek (深度求索)",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "placeholder_key": "sk-...",
        "docs": "https://platform.deepseek.com"
    },
    {
        "id_preset": "claude",
        "name": "Anthropic Claude",
        "provider": "claude",
        "base_url": "",
        "model": "claude-sonnet-5",
        "placeholder_key": "sk-ant-...",
        "docs": "https://console.anthropic.com"
    },
    {
        "id_preset": "openai",
        "name": "OpenAI (GPT-4o / GPT-4o-mini)",
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "placeholder_key": "sk-...",
        "docs": "https://platform.openai.com"
    },
    {
        "id_preset": "moonshot",
        "name": "Kimi (月之暗面 Moonshot)",
        "provider": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "placeholder_key": "sk-...",
        "docs": "https://platform.moonshot.cn"
    },
    {
        "id_preset": "dashscope",
        "name": "通义千问 (阿里云百炼 DashScope)",
        "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "placeholder_key": "sk-...",
        "docs": "https://bailian.console.aliyun.com"
    },
    {
        "id_preset": "ollama",
        "name": "本地 Ollama (离线私有 / 无需Key)",
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "placeholder_key": "(本地可留空)",
        "docs": "https://ollama.com"
    },
    {
        "id_preset": "custom",
        "name": "自定义 OpenAI 兼容接口",
        "provider": "openai_compatible",
        "base_url": "https://your-api-endpoint.com/v1",
        "model": "",
        "placeholder_key": "sk-...",
        "docs": ""
    }
]


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config() -> dict:
    """读取所有 API 配置"""
    _ensure_dir()
    if not os.path.exists(PROFILES_FILE):
        return {
            "active_id": "",
            "profiles": []
        }
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {"active_id": "", "profiles": []}
            if "profiles" not in data or not isinstance(data["profiles"], list):
                data["profiles"] = []
            return data
    except Exception as e:
        print(f"Error loading {PROFILES_FILE}: {e}")
        return {"active_id": "", "profiles": []}


def save_config(config: dict) -> bool:
    """持久化保存配置到本地文件"""
    _ensure_dir()
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving {PROFILES_FILE}: {e}")
        return False


def get_profiles() -> List[dict]:
    """获取所有已保存的配置列表"""
    cfg = load_config()
    return cfg.get("profiles", [])


def get_active_profile() -> Optional[dict]:
    """获取当前处于激活选中状态的 API 配置"""
    cfg = load_config()
    active_id = cfg.get("active_id", "")
    profiles = cfg.get("profiles", [])
    if not profiles:
        return None
    for p in profiles:
        if p.get("id") == active_id:
            return p
    # 如果没匹配上或为空，默认返回第一个
    return profiles[0]


def set_active_profile_id(profile_id: str) -> bool:
    """设置当前激活的 API 配置 ID"""
    cfg = load_config()
    cfg["active_id"] = profile_id
    return save_config(cfg)


def mask_key(key: str) -> str:
    """对 API Key 进行脱敏保护显示"""
    if not key:
        return ""
    k = key.strip()
    if len(k) <= 8:
        return "****"
    return f"{k[:4]}****{k[-4:]}"


def save_or_update_profile(
    name: str,
    provider: str,
    api_key: str,
    model: str,
    base_url: str = "",
    profile_id: str = None
) -> Tuple[bool, str, dict]:
    """
    保存或更新 API 配置
    如果 profile_id 为空，则创建新档案；否则更新旧档案。
    如果 api_key 为空且 profile_id 存在，保留原有的 api_key。
    """
    cfg = load_config()
    profiles = cfg.get("profiles", [])
    
    name = (name or "").strip()
    if not name:
        name = f"{provider} - {model or '默认'}"
    
    provider = provider.strip()
    model = (model or "").strip()
    base_url = (base_url or "").strip()
    if provider == "deepseek" and not base_url:
        base_url = "https://api.deepseek.com"

    target_profile = None
    if profile_id:
        for p in profiles:
            if p.get("id") == profile_id:
                target_profile = p
                break

    if target_profile:
        # 更新现有配置
        target_profile["name"] = name
        target_profile["provider"] = provider
        if api_key.strip():  # 只有输入了新 key 才更新，留空不改变
            target_profile["api_key"] = api_key.strip()
        target_profile["model"] = model
        target_profile["base_url"] = base_url
        target_profile["updated_at"] = int(time.time())
        res_profile = target_profile
    else:
        # 新增配置
        new_id = str(uuid.uuid4())[:8]
        new_profile = {
            "id": new_id,
            "name": name,
            "provider": provider,
            "api_key": api_key.strip(),
            "model": model,
            "base_url": base_url,
            "created_at": int(time.time()),
            "updated_at": int(time.time())
        }
        profiles.append(new_profile)
        cfg["active_id"] = new_id  # 新增后默认选中
        res_profile = new_profile

    cfg["profiles"] = profiles
    ok = save_config(cfg)
    return ok, res_profile["id"], res_profile


def delete_profile(profile_id: str) -> bool:
    """删除指定配置"""
    cfg = load_config()
    profiles = cfg.get("profiles", [])
    new_profiles = [p for p in profiles if p.get("id") != profile_id]
    cfg["profiles"] = new_profiles
    if cfg.get("active_id") == profile_id:
        cfg["active_id"] = new_profiles[0]["id"] if new_profiles else ""
    return save_config(cfg)


def test_api_connection(provider: str, api_key: str, model: str, base_url: str = "") -> Tuple[bool, str]:
    """
    连通性测试：发起一次极简调用，验证配置和网络是否畅通
    """
    try:
        from openai import OpenAI
        import anthropic

        sanitize_proxy_env()  # 老进程里环境可能还没自愈过

        provider = (provider or "").strip()
        model = (model or "").strip()
        base_url = (base_url or "").strip()
        api_key = (api_key or "").strip()

        if provider in ["deepseek", "openai_compatible", "openai"]:
            b_url = base_url
            if provider == "deepseek" and not b_url:
                b_url = "https://api.deepseek.com"
            client = OpenAI(
                api_key=api_key or "no-key",
                base_url=b_url or None,
                timeout=15.0
            )
            target_model = model or ("deepseek-chat" if provider == "deepseek" else "gpt-4o-mini")
            resp = client.chat.completions.create(
                model=target_model,
                messages=[{"role": "user", "content": "Hi, reply 'OK' only."}],
                max_tokens=10,
                temperature=0.1
            )
            reply = resp.choices[0].message.content or ""
            return True, f"连接成功！模型响应: {reply.strip()[:30]}"

        elif provider == "claude":
            client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
            target_model = model or "claude-sonnet-5"
            resp = client.messages.create(
                model=target_model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi, reply 'OK' only."}]
            )
            reply = resp.content[0].text or ""
            return True, f"连接成功！Claude 响应: {reply.strip()[:30]}"
        else:
            return False, f"未知的 Provider 类型: {provider}"
    except Exception as e:
        err_msg = str(e)
        if "AuthenticationError" in err_msg or "401" in err_msg:
            return False, f"认证失败(401)：请检查 API Key 是否正确"
        elif "NotFoundError" in err_msg or "404" in err_msg:
            return False, f"模型不存在(404)：请检查模型名称 '{model}' 或 Base URL 是否正确"
        elif "ConnectError" in err_msg or "connection error" in err_msg.lower():
            return False, f"网络连接错误：无法连接至接口地址，请检查 Base URL 或网络代理"
        return False, f"调用测试失败: {err_msg[:120]}"
