# -*- coding: utf-8 -*-
"""
多图横向对比幕布 (Figure Side-by-Side Comparator) 核心状态与数据管理模块
设计原则：极简、高容错、零冗余、易上手。
最多支持 4 张图表同时进行横向几何与数据比对。
"""

MAX_COMPARE_COUNT = 4
SESSION_BASKET_KEY = "figure_compare_basket"
SESSION_OPEN_KEY = "figure_compare_is_open"


def get_basket(state) -> list:
    """获取当前对比篮子中的图表列表，确保永不返回 None"""
    if SESSION_BASKET_KEY not in state or not isinstance(state[SESSION_BASKET_KEY], list):
        state[SESSION_BASKET_KEY] = []
    return state[SESSION_BASKET_KEY]


def is_in_basket(state, figure_id) -> bool:
    """检查指定 figure_id 是否已在对比篮中"""
    basket = get_basket(state)
    fid_str = str(figure_id)
    return any(str(item.get("id")) == fid_str for item in basket)


def toggle_basket(state, fig_item: dict) -> tuple[bool, str]:
    """
    智能添加或移除对比图表（添加/移除双用切换）：
    - 若已存在，则无缝移除；
    - 若不存在且未满，则加入并自动保留核心元数据；
    - 若已满（>=4），返回友好拦截提示。
    """
    basket = get_basket(state)
    fid = str(fig_item.get("id"))

    # 1. 查找是否已存在
    for idx, item in enumerate(basket):
        if str(item.get("id")) == fid:
            basket.pop(idx)
            return True, "已从对比幕布移出"

    # 2. 检查容量上限
    if len(basket) >= MAX_COMPARE_COUNT:
        return False, f"对比幕布已满（最多并排比对 {MAX_COMPARE_COUNT} 张），请先移除一张再添加"

    # 3. 规范化存入字段，避免空指针与脏数据
    clean_item = {
        "id": fig_item.get("id"),
        "image_path": fig_item.get("image_path", ""),
        "caption": (fig_item.get("caption") or "").strip(),
        "figure_type": fig_item.get("figure_type") or "Figure",
        "paper_id": fig_item.get("paper_id") or "",
        "paper_title": fig_item.get("paper_title") or "未知文献",
        "journal": fig_item.get("journal") or "",
        "year": fig_item.get("year") or "",
        "page_number": fig_item.get("page_number") or "",
        "paper_abs_path": fig_item.get("paper_abs_path") or "",
    }
    basket.append(clean_item)
    return True, "已成功加入对比幕布"


def remove_from_basket(state, figure_id) -> bool:
    """从对比篮中安全移除指定 ID 的图表"""
    basket = get_basket(state)
    fid_str = str(figure_id)
    for idx, item in enumerate(basket):
        if str(item.get("id")) == fid_str:
            basket.pop(idx)
            return True
    return False


def clear_basket(state):
    """一键清空对比篮"""
    state[SESSION_BASKET_KEY] = []


def is_compare_open(state) -> bool:
    """获取当前幕布展开状态"""
    return bool(state.get(SESSION_OPEN_KEY, False))


def set_compare_open(state, open_state: bool):
    """设置对比幕布展开/折叠状态"""
    state[SESSION_OPEN_KEY] = bool(open_state)
