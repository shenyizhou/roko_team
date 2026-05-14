"""
洛克王国世界量化分析模型

数据架构:
- data/pets/*.json: 精灵详细数据（604个精灵，每个独立文件）
- data/skills.csv: 技能库数据（486个技能）
"""
import json
import csv
from pathlib import Path
from functools import lru_cache

DATA_DIR = Path(__file__).parent.parent / "data"
PETS_DIR = DATA_DIR / "pets"
SKILLS_CSV = DATA_DIR / "skills.csv"


# ========== 精灵数据加载 ==========

def _load_single_pet(pet_file: Path) -> dict:
    """加载单个精灵JSON文件"""
    with open(pet_file, encoding="utf-8") as f:
        raw = json.load(f)

    # 统一字段映射到内部格式
    attrs = [raw["element"]]
    if raw.get("element2"):
        attrs.append(raw["element2"])

    return {
        "id": raw.get("id", raw["name"]),
        "name": raw["name"],
        "noText": raw.get("noText", raw["name"]),  # 家族编号
        "form": raw.get("form", ""),
        "regionalFormName": raw.get("regionalFormName", ""),
        "stage": raw.get("stage", ""),
        "type_class": raw.get("stage", ""),  # 兼容旧字段
        "attrs": attrs,
        "stats": {
            "hp": int(raw.get("hp") or 0),
            "atk": int(raw.get("physicalAttack") or 0),
            "matk": int(raw.get("magicAttack") or 0),
            "def": int(raw.get("physicalDefense") or 0),
            "mdef": int(raw.get("magicDefense") or 0),
            "spd": int(raw.get("speed") or 0),
            "total": sum([
                int(raw.get("hp") or 0),
                int(raw.get("physicalAttack") or 0),
                int(raw.get("magicAttack") or 0),
                int(raw.get("physicalDefense") or 0),
                int(raw.get("magicDefense") or 0),
                int(raw.get("speed") or 0),
            ]),
        },
        "trait": {
            "name": raw.get("ability", ""),
            "desc": raw.get("abilityDesc", ""),
        },
        "size": raw.get("size", ""),
        "weight": raw.get("weight", ""),
        "description": raw.get("description", ""),
        # 技能相关
        "skills_raw": {
            "natural": raw.get("skills", []),
            "bloodline": raw.get("bloodlineSkills", []),
            "learnable": raw.get("learnableSkillStones", []),
        },
    }


@lru_cache(maxsize=1)
def _all_pet_files() -> list[Path]:
    """获取所有精灵文件路径"""
    return list(PETS_DIR.glob("*.json"))


@lru_cache(maxsize=1)
def get_all_pets() -> dict:
    """返回所有精灵（兼容旧格式，按name为key）"""
    result = {}
    for f in _all_pet_files():
        pet = _load_single_pet(f)
        result[pet["name"]] = pet
    return result


def get_pet(name: str) -> dict | None:
    """按名称获取单只精灵"""
    pets = get_all_pets()
    return pets.get(name)


@lru_cache(maxsize=1)
def get_family_map() -> dict:
    """返回 {精灵名称: noText} 的家族映射，同一noText = 同一进化线"""
    pets = get_all_pets()
    return {name: pet["noText"] for name, pet in pets.items()}


@lru_cache(maxsize=1)
def get_family_members() -> dict:
    """返回 {noText: [精灵名称列表]} 的家族成员映射"""
    pets = get_all_pets()
    result = {}
    for name, pet in pets.items():
        no = pet["noText"]
        if no not in result:
            result[no] = []
        result[no].append(name)
    return result


# ========== 技能数据加载 ==========

@lru_cache(maxsize=1)
def _load_skills_csv() -> dict:
    """加载技能CSV并按名称索引"""
    skills = {}
    with open(SKILLS_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row["name"].strip()
            # 处理空的power字段
            power = row.get("power", "").strip()
            try:
                power_val = int(power) if power else 0
            except (ValueError, TypeError):
                power_val = 0

            skills[name] = {
                "name": name,
                "element": row.get("element", ""),
                "category": row.get("category", ""),
                "cost": int(row.get("cost", 0) or 0),
                "power": power_val,
                "desc": row.get("effect", ""),
            }
    return skills


def get_all_skills() -> dict:
    """返回所有技能，按名称索引"""
    return _load_skills_csv()


def get_skill(name: str) -> dict | None:
    """按名称获取单个技能"""
    skills = _load_skills_csv()
    return skills.get(name)


# ========== 精灵技能组合（兼容旧接口）==========

def _get_pet_skills(pet: dict) -> list[dict]:
    """获取精灵的所有技能详情（天生+血脉+可学习）"""
    all_skill_names = set()
    all_skill_names.update(pet["skills_raw"]["natural"])
    all_skill_names.update(pet["skills_raw"]["bloodline"])
    all_skill_names.update(pet["skills_raw"]["learnable"])

    skills = _load_skills_csv()
    result = []
    for name in all_skill_names:
        if name in skills:
            result.append(skills[name])
    return result


def get_all_pets_with_skills() -> dict:
    """返回所有精灵 + 完整技能数据（兼容旧接口）"""
    pets = get_all_pets()
    result = {}
    for name, pet in pets.items():
        pet_with_skill = dict(pet)
        all_skills = _get_pet_skills(pet)
        # 区分learnset（天生+血脉）和 recommended（可学习技能石）
        natural_names = set(pet["skills_raw"]["natural"])
        bloodline_names = set(pet["skills_raw"]["bloodline"])
        learn_names = natural_names | bloodline_names

        pet_with_skill["skills"] = {
            "learnset": [s for s in all_skills if s["name"] in learn_names],
            "recommended": [s for s in all_skills if s["name"] in pet["skills_raw"]["learnable"]],
            "other": [],
        }
        result[name] = pet_with_skill
    return result
