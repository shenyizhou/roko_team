#!/usr/bin/env python3
"""
抓取洛克王国世界Wiki的所有数据
数据源：https://www.rocoworldwiki.com/data/*
"""
import requests
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://www.rocoworldwiki.com"
DATA_DIR = Path(__file__).parent.parent / "data"

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch_json(url_path: str) -> dict:
    """抓取JSON数据"""
    url = f"{BASE_URL}{url_path}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def save_json(data: dict, filename: str):
    """保存JSON数据"""
    with open(DATA_DIR / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ Saved: {filename}")


def fetch_all_skills():
    """抓取所有技能详情"""
    print("Fetching skill index...")
    skill_index = fetch_json("/data/skill-detail-index.json")
    print(f"Total skills: {len(skill_index['items'])}")

    # 先保存索引
    save_json(skill_index, "skill_index.json")

    # 并行抓取所有技能详情
    skills = {}
    failed = []

    def fetch_one_skill(skill):
        filename = skill["fileName"]
        try:
            skill_data = fetch_json(f"/data/skills/{filename}")
            return (skill["id"], skill_data)
        except Exception as e:
            return (skill["id"], None, str(e))

    print(f"Fetching {len(skill_index['items'])} skills in parallel...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_one_skill, skill) for skill in skill_index["items"]]

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if len(result) == 2:
                skill_id, skill_data = result
                skills[skill_id] = skill_data
            else:
                skill_id, _, err = result
                failed.append((skill_id, err))

            if i % 50 == 0:
                print(f"  Progress: {i}/{len(skill_index['items'])}")
                time.sleep(0.5)  # 避免请求过快

    print(f"✓ Fetched {len(skills)} skills, failed: {len(failed)}")
    if failed:
        print(f"  Failed IDs: {[f[0] for f in failed[:10]]}...")

    # 保存完整技能数据
    save_json(skills, "skills.json")
    return skills


def fetch_spirit_dex():
    """抓取宠物图鉴"""
    print("\nFetching spirit dex...")
    spirit_dex = fetch_json("/data/spirit-dex.json")
    print(f"Total spirits: {spirit_dex['totalParsed']} (usable: {spirit_dex['totalUsable']})")
    save_json(spirit_dex, "spirit_dex.json")
    return spirit_dex


def fetch_attribute_chart():
    """抓取属性克制表"""
    print("\nFetching attribute chart...")
    attr_chart = fetch_json("/data/attribute-chart.json")
    print(f"Total attributes: {len(attr_chart['attributes'])}")
    save_json(attr_chart, "attribute_chart.json")
    return attr_chart


def build_pet_skill_mapping(skills: dict):
    """从技能learners字段反向构建宠物-技能映射"""
    print("\nBuilding pet-skill mapping...")

    pet_skills = {}  # pet_id -> [skill_ids]
    pet_names = {}   # pet_id -> pet_name

    for skill_id, skill in skills.items():
        if not skill or "learners" not in skill:
            continue

        for learner in skill["learners"]:
            pet_id = learner["id"]
            pet_name = learner["name"]

            if pet_id not in pet_skills:
                pet_skills[pet_id] = []
            pet_skills[pet_id].append(skill_id)

            if pet_id not in pet_names:
                pet_names[pet_id] = pet_name

    # 构建完整映射
    pet_skill_mapping = []
    for pet_id, skill_ids in pet_skills.items():
        pet_skill_mapping.append({
            "pet_id": pet_id,
            "pet_name": pet_names.get(pet_id, ""),
            "skill_count": len(skill_ids),
            "skills": skill_ids
        })

    pet_skill_mapping.sort(key=lambda x: -x["skill_count"])
    print(f"Total pets with skills: {len(pet_skill_mapping)}")
    print(f"Top 3 pets with most skills:")
    for p in pet_skill_mapping[:3]:
        print(f"  {p['pet_name']} ({p['pet_id']}): {p['skill_count']} skills")

    save_json(pet_skill_mapping, "pet_skill_mapping.json")
    return pet_skill_mapping


def main():
    """主函数"""
    DATA_DIR.mkdir(exist_ok=True)
    print("=" * 60)
    print("洛克王国世界 Wiki 数据抓取")
    print("=" * 60)

    # 1. 属性克制表
    fetch_attribute_chart()

    # 2. 宠物图鉴
    fetch_spirit_dex()

    # 3. 所有技能
    skills = fetch_all_skills()

    # 4. 构建宠物-技能映射
    build_pet_skill_mapping(skills)

    print("\n" + "=" * 60)
    print("✓ All data fetched successfully!")
    print(f"✓ Data saved to: {DATA_DIR.absolute()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
