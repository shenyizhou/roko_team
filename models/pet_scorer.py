#!/usr/bin/env python3
"""
宠物综合评分模型
基于：种族值、属性、特性、技能能量效率
"""
import json
from pathlib import Path
from .attribute_matrix import AttributeMatrix

DATA_DIR = Path(__file__).parent.parent / "data"


class PetScorer:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        self.attr_matrix = AttributeMatrix()

        # 权重配置
        self.weights = {
            "stats": 0.35,      # 种族值权重
            "type": 0.20,       # 属性价值权重
            "feature": 0.15,    # 特性价值权重
            "skill": 0.30,      # 技能质量权重
        }

    def calc_stats_score(self, stats: dict) -> float:
        """计算种族值评分"""
        # HP和速度权重更高（死1只=损失25%生命点）
        weighted = (
            stats["hp"] * 1.2 +
            stats["atk"] * 1.0 +
            stats["matk"] * 1.0 +
            stats["def"] * 1.1 +
            stats["mdef"] * 1.1 +
            stats["spd"] * 1.5
        )
        return weighted / 6.8  # 归一化

    def calc_type_score(self, attrs: list[str]) -> float:
        """计算属性价值评分"""
        type_data = self.attr_matrix.get_type_score(attrs)
        # 进攻 + 防守评分，防守权重更高
        return type_data["offense"] * 0.3 + type_data["defense_score"] * 0.7

    def calc_feature_score(self, trait: dict) -> float:
        """计算特性价值评分（基于文本关键词）"""
        desc = trait.get("desc", "")
        name = trait.get("name", "")
        full_text = f"{name} {desc}"

        score = 0
        keywords = {
            # 回能相关（极其重要）
            "回复能量": 20, "回能": 15, "获得能量": 15, "能量+": 12,

            # 控制/免控
            "睡眠": 8, "麻痹": 8, "冰冻": 8, "冻结": 8, "混乱": 6,
            "免疫": 10, "解除控制": 8,

            # 强化
            "物攻+": 6, "魔攻+": 6, "速度+": 8, "防御+": 5, "魔防+": 5,
            "全属性+": 15, "攻防": 10,

            # 伤害/回复
            "造成伤害": 5, "威力": 3, "回复HP": 8, "回血": 8, "吸血": 10,

            # 特殊机制
            "迅捷": 15, "先手": 12, "入场": 8, "离场": 8,
            "复活": 25, "不屈": 20, "重生": 20,

            # 克制相关
            "克制": 8, "抵抗": 5,
        }

        for kw, val in keywords.items():
            if kw in full_text:
                score += val

        return score

    def calc_skill_score(self, skills: dict) -> dict:
        """计算技能评分"""
        all_skills = skills.get("learnset", []) + skills.get("recommended", [])

        if not all_skills:
            return {"score": 0, "avg_power": 0, "avg_cost": 0, "efficiency": 0}

        # 计算平均威力、能耗、效率
        attack_skills = [s for s in all_skills if s.get("power", 0) > 0]

        if not attack_skills:
            return {"score": 0, "avg_power": 0, "avg_cost": 0, "efficiency": 0}

        # 威力/能耗比（能量效率核心指标）
        total_power = sum(s.get("power", 0) for s in attack_skills)
        total_cost = sum(s.get("cost", 0) for s in attack_skills)
        avg_power = total_power / len(attack_skills)
        avg_cost = total_cost / len(attack_skills)

        # 能量效率 = 威力/能耗（取倒数避免低能耗过高评分）
        efficiency = avg_power / max(avg_cost, 1)

        # 技能多样性评分
        skill_elements = set(s.get("element") for s in all_skills)
        diversity_score = len(skill_elements) * 2

        # 控制技能加分
        control_keywords = ["睡眠", "麻痹", "冰冻", "冻结", "混乱", "恐惧", "迷惑"]
        control_score = sum(2 for s in all_skills for kw in control_keywords if kw in s.get("desc", ""))

        return {
            "score": efficiency * 3 + diversity_score + control_score,
            "avg_power": avg_power,
            "avg_cost": avg_cost,
            "efficiency": efficiency,
            "skill_count": len(all_skills),
        }

    def score_pet(self, pet_id: str) -> dict:
        """计算单只宠物的综合评分"""
        pet = self.pets.get(pet_id)
        if not pet:
            return {"error": "Pet not found"}

        # 各项评分
        stats_raw = self.calc_stats_score(pet["stats"])
        type_raw = self.calc_type_score(pet["attrs"])
        feature_raw = self.calc_feature_score(pet["trait"])
        skill_data = self.calc_skill_score(pet["skills"])

        # 归一化到0-100
        stats_score = min(stats_raw / 150 * 100, 100)
        type_score = min(type_raw / 10 * 100, 100)  # 属性分通常0-8
        feature_score = min(feature_raw / 50 * 100, 100)
        skill_score = min(skill_data["score"] / 30 * 100, 100)

        # 综合评分
        total_score = (
            stats_score * self.weights["stats"] +
            type_score * self.weights["type"] +
            feature_score * self.weights["feature"] +
            skill_score * self.weights["skill"]
        )

        return {
            "id": pet_id,
            "name": pet["name"],
            "attrs": pet["attrs"],
            "stats": pet["stats"],
            "trait": pet["trait"],
            "scores": {
                "total": round(total_score, 1),
                "stats": round(stats_score, 1),
                "type": round(type_score, 1),
                "feature": round(feature_score, 1),
                "skill": round(skill_score, 1),
            },
            "skill_data": {
                "avg_power": round(skill_data["avg_power"], 1),
                "avg_cost": round(skill_data["avg_cost"], 1),
                "efficiency": round(skill_data["efficiency"], 1),
                "skill_count": skill_data.get("skill_count", 0),
            }
        }

    def get_all_rankings(self) -> list:
        """获取所有宠物的排行榜"""
        rankings = []
        for pet_id in self.pets.keys():
            result = self.score_pet(pet_id)
            if "error" not in result:
                rankings.append(result)

        rankings.sort(key=lambda x: -x["scores"]["total"])
        return rankings


if __name__ == "__main__":
    scorer = PetScorer()
    rankings = scorer.get_all_rankings()

    print("=" * 70)
    print("宠物综合评分排行榜（前20）")
    print("=" * 70)
    print(f"{'排名':<4}{'名称':<12}{'属性':<12}{'总分':>8}{'种族':>8}{'属性':>8}{'特性':>8}{'技能':>8}")
    print("-" * 70)

    for i, pet in enumerate(rankings[:20], 1):
        s = pet["scores"]
        attrs = "/".join(pet["attrs"])
        print(f"{i:<4}{pet['name']:<12}{attrs:<12}{s['total']:>8.1f}{s['stats']:>8.1f}{s['type']:>8.1f}{s['feature']:>8.1f}{s['skill']:>8.1f}")

    print("\n" + "=" * 70)
    print("分项排行榜Top5:")
    print("=" * 70)

    for category in ["stats", "type", "feature", "skill"]:
        print(f"\n【{category.upper()}】")
        top5 = sorted(rankings, key=lambda x: -x["scores"][category])[:5]
        for i, pet in enumerate(top5, 1):
            print(f"  {i}. {pet['name']:<12} {pet['scores'][category]:.1f}")
