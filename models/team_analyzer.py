#!/usr/bin/env python3
"""
队伍协同性分析器
"""
import json
from pathlib import Path
from .attribute_matrix import AttributeMatrix
from .pet_scorer import PetScorer

DATA_DIR = Path(__file__).parent.parent / "data"


class TeamAnalyzer:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        self.attr_matrix = AttributeMatrix()
        self.pet_scorer = PetScorer()

    def analyze_team(self, team_ids: list[str]) -> dict:
        """分析一个6宠队伍"""
        if len(team_ids) != 6:
            return {"error": f"队伍需要6只宠物，当前有{len(team_ids)}只"}

        # 获取每只宠物的数据
        team_pets = []
        for pet_id in team_ids:
            pet = self.pets.get(pet_id)
            if not pet:
                return {"error": f"宠物不存在: {pet_id}"}
            pet["id"] = pet_id
            team_pets.append(pet)

        # 1. 队伍综合评分
        pet_scores = [self.pet_scorer.score_pet(pet_id) for pet_id in team_ids]
        total_score = sum(s["scores"]["total"] for s in pet_scores)
        avg_score = total_score / 6

        # 2. 属性联防分析
        team_attrs = [pet["attrs"] for pet in team_pets]
        coverage = self.attr_matrix.get_team_coverage(team_attrs)

        # 3. 能量协同分析
        avg_costs = []
        efficiencies = []
        for s in pet_scores:
            avg_costs.append(s["skill_data"]["avg_cost"])
            efficiencies.append(s["skill_data"]["efficiency"])

        # 计算标准差（能量曲线平滑度）
        import statistics
        cost_std = statistics.stdev(avg_costs) if len(avg_costs) > 1 else 0
        energy_synergy = max(0, 100 - cost_std * 15)  # 标准差越小，协同性越好

        # 4. 生存梯队分析（前4只的抗打击能力）
        first_four_hp = sum(p["stats"]["hp"] * p["stats"]["def"] for p in team_pets[:4])
        first_four_spd = sum(p["stats"]["spd"] for p in team_pets[:4])

        # 5. 速度线分析
        speed_line = sorted([p["stats"]["spd"] for p in team_pets], reverse=True)

        # 综合评分
        team_total = (
            avg_score * 0.4 +  # 平均个体质量
            (coverage["coverage_score"] + 20) * 2 * 0.25 +  # 属性联防（加20避免负数）
            energy_synergy * 0.2 +  # 能量协同
            (first_four_hp / 20000 * 100) * 0.15  # 前4只抗打击能力
        )

        return {
            "team_score": round(team_total, 1),
            "pet_scores": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "total": p["scores"]["total"],
                    "attrs": p["attrs"],
                    "avg_cost": p["skill_data"]["avg_cost"],
                    "efficiency": p["skill_data"]["efficiency"],
                }
                for p in pet_scores
            ],
            "attribute_coverage": {
                "covered_attrs_count": coverage["covered_attrs"],
                "resisted_attrs_count": coverage["resisted_attrs"],
                "weak_attrs_count": coverage["weak_attrs"],
                "coverage_score": coverage["coverage_score"],
                "weak_list": coverage["weak_list"],
                "resist_list": coverage["resist_list"],
            },
            "energy_analysis": {
                "avg_costs": [round(c, 1) for c in avg_costs],
                "team_avg_cost": round(sum(avg_costs) / 6, 1),
                "cost_std": round(cost_std, 2),
                "energy_synergy_score": round(energy_synergy, 1),
                "avg_efficiencies": [round(e, 1) for e in efficiencies],
                "team_avg_efficiency": round(sum(efficiencies) / 6, 1),
            },
            "survival_analysis": {
                "first_four_hp_def_product": round(first_four_hp),
                "first_four_total_speed": first_four_spd,
                "speed_line": speed_line,
            }
        }

    def print_team_report(self, team_ids: list[str]):
        """打印队伍分析报告"""
        result = self.analyze_team(team_ids)

        if "error" in result:
            print(f"错误: {result['error']}")
            return

        print("=" * 70)
        print(f"队伍分析报告 - 综合评分: {result['team_score']:.1f}")
        print("=" * 70)

        print("\n【宠物列表】")
        for i, p in enumerate(result["pet_scores"], 1):
            attrs = "/".join(p["attrs"])
            print(f"  {i}. {p['name']:<12} ({attrs:<10}) 评分: {p['total']:5.1f}  能耗: {p['avg_cost']:.1f}  效率: {p['efficiency']:.1f}")

        print("\n【属性联防分析】")
        cov = result["attribute_coverage"]
        print(f"  属性覆盖: {cov['covered_attrs_count']} / 18")
        print(f"  抵抗属性: {cov['resisted_attrs_count']} 个")
        print(f"  弱点属性: {cov['weak_attrs_count']} 个")
        print(f"  联防评分: {cov['coverage_score']}")
        if cov["weak_list"]:
            print(f"  弱点属性: {', '.join(cov['weak_list'])}")
        if cov["resist_list"]:
            print(f"  抵抗属性: {', '.join(cov['resist_list'][:10])}{'...' if len(cov['resist_list']) > 10 else ''}")

        print("\n【能量协同分析】")
        eng = result["energy_analysis"]
        print(f"  队伍平均能耗: {eng['team_avg_cost']}")
        print(f"  能耗标准差: {eng['cost_std']:.2f} (越小越平滑)")
        print(f"  能量协同评分: {eng['energy_synergy_score']:.1f}")
        print(f"  队伍平均效率: {eng['team_avg_efficiency']:.1f} 威力/能耗")

        print("\n【生存梯队分析】")
        surv = result["survival_analysis"]
        print(f"  前4只抗打击指数: {surv['first_four_hp_def_product']}")
        print(f"  速度线: {surv['speed_line']}")

        print("\n" + "=" * 70)
        return result


if __name__ == "__main__":
    # 测试：用评分最高的6只组队
    analyzer = TeamAnalyzer()
    rankings = analyzer.pet_scorer.get_all_rankings()
    top6_ids = [p["id"] for p in rankings[:6]]

    print("测试队伍：评分最高的6只宠物")
    analyzer.print_team_report(top6_ids)
