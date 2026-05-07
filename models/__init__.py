"""
洛克王国世界量化分析模型

数据架构: 以 spirit_filter_index.json 为唯一基础数据源
"""
import json
from pathlib import Path
from functools import lru_cache

DATA_DIR = Path(__file__).parent.parent / "data"


def _load_filter_index() -> dict:
    with open(DATA_DIR / "spirit_filter_index.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _filter_index_items() -> list:
    return _load_filter_index()["items"]


def _map_pet(item: dict) -> dict:
    """将 spirit_filter_index 条目映射为 pets.json 兼容格式"""
    return {
        "id": item["noText"],
        "name": item["name"],
        "noText": item["noText"],  # 家族编号，同一进化线的精灵共享
        "type_class": item.get("typeClass", ""),
        "attrs": item.get("attrs", []),
        "stats": {
            "total": item.get("race", 0),
            "hp": item.get("hp", 0),
            "atk": item.get("atk", 0),
            "matk": item.get("matk", 0),
            "def": item.get("def", 0),
            "mdef": item.get("mdef", 0),
            "spd": item.get("spd", 0),
        },
        "trait": {
            "name": item.get("traitName", ""),
            "desc": item.get("traitDesc", ""),
        },
        "skill_count": item.get("skillCount", 0),
    }


def get_all_pets() -> dict:
    """返回所有最终形态精灵，兼容原 pets_final.json 格式
    包含 typeClass 为 final/region/boss 的精灵
    """
    items = _filter_index_items()
    result = {}
    for item in items:
        if item.get("typeClass") in ("final", "region", "boss"):
            result[item["name"]] = _map_pet(item)
    return result


def get_pet(name: str) -> dict | None:
    """按名称获取单只精灵，兼容原格式"""
    for item in _filter_index_items():
        if item["name"] == name:
            return _map_pet(item)
    return None


def get_pet_by_no(no_text: str) -> list[dict]:
    """按 noText 获取所有形态"""
    items = _filter_index_items()
    return [_map_pet(item) for item in items if item["noText"] == no_text]


@lru_cache(maxsize=1)
def _load_learnsets() -> dict:
    learnset_path = DATA_DIR / "pet_learnset.json"
    if learnset_path.exists():
        with open(learnset_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


@lru_cache(maxsize=1)
def _load_recommended() -> dict:
    recommended_path = DATA_DIR / "pet_recommended.json"
    if recommended_path.exists():
        with open(recommended_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


@lru_cache(maxsize=1)
def get_family_map() -> dict:
    """返回 {精灵名称: noText} 的家族映射，同一noText = 同一进化线"""
    items = _filter_index_items()
    return {item["name"]: item["noText"] for item in items}


@lru_cache(maxsize=1)
def get_family_members() -> dict:
    """返回 {noText: [精灵名称列表]} 的家族成员映射"""
    items = _filter_index_items()
    result = {}
    for item in items:
        no = item["noText"]
        if no not in result:
            result[no] = []
        result[no].append(item["name"])
    return result


def get_all_pets_with_skills() -> dict:
    """返回所有最终形态精灵 + 技能数据"""
    pets = get_all_pets()
    learnsets = _load_learnsets()
    recommended = _load_recommended()
    for name, pet in pets.items():
        pet["skills"] = {
            "learnset": learnsets.get(name, []),
            "recommended": recommended.get(name, []),
            "other": [],
        }
    return pets
