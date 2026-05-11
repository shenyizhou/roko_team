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


@lru_cache(maxsize=1)
def _get_region_base_map() -> dict:
    """返回 {region_form_name: base_species_name} 映射
    同一NO下的region形态应继承final形态的技能"""
    items = _filter_index_items()
    no_groups = {}
    for item in items:
        no = item["noText"]
        if no not in no_groups:
            no_groups[no] = {"final": [], "region": []}
        tc = item.get("typeClass", "")
        if tc == "region":
            no_groups[no]["region"].append(item["name"])
        elif tc == "final":
            no_groups[no]["final"].append(item["name"])
    result = {}
    for no, group in no_groups.items():
        if group["region"] and group["final"]:
            base = group["final"][0]
            for region_name in group["region"]:
                result[region_name] = base
    return result


def _match_skills(name: str, skill_map: dict) -> list:
    """按名称匹配技能数据, 兼容长短名(如 化蝶 vs 化蝶(奇丽花的样子))
    region形态继承同NO下final形态的技能"""
    if name in skill_map:
        return skill_map[name]
    base = name.split("（")[0] if "（" in name else name
    if base in skill_map:
        return skill_map[base]
    # region形态 → 同NO的final形态继承技能
    region_base_map = _get_region_base_map()
    if name in region_base_map:
        base_species = region_base_map[name]
        if "（" in name:
            suffix = "（" + name.split("（", 1)[1]
            result = skill_map.get(base_species + suffix, [])
            if result:
                return result
        return skill_map.get(base_species, [])
    return []


def get_all_pets_with_skills() -> dict:
    """返回所有最终形态精灵 + 技能数据"""
    pets = get_all_pets()
    learnsets = _load_learnsets()
    recommended = _load_recommended()
    for name, pet in pets.items():
        pet["skills"] = {
            "learnset": _match_skills(name, learnsets),
            "recommended": _match_skills(name, recommended),
            "other": [],
        }
    return pets
