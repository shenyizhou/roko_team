#!/usr/bin/env python3
"""
队伍协同性分析器
"""
import json
import statistics
from pathlib import Path
from .attribute_matrix import AttributeMatrix
from .pet_scorer import PetScorer
from .role_classifier import RoleClassifier
from .system_detector import SystemDetector
from .build_analyzer import BuildAnalyzer

DATA_DIR = Path(__file__).parent.parent / "data"


class TeamAnalyzer:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        self.attr_matrix = AttributeMatrix()
        self.pet_scorer = PetScorer()
        self.role_classifier = RoleClassifier()
        self.system_detector = SystemDetector()
        self.build_analyzer = BuildAnalyzer()

        # 属性简称到标准名的映射（洛克王国世界18属性体系）
        self._short_attrs = {
            "火": "火", "水": "水", "草": "草", "冰": "冰",
            "电": "电", "普通": "一般", "武": "武", "毒": "毒",
            "地": "地", "翼": "翼", "虫": "虫",
            "幽": "幽灵", "恶": "恶魔", "龙": "龙", "幻": "幻",
            "钢": "机械", "光": "光", "萌": "萌",
        }

    def _normalize_element(self, element: str) -> str:
        """统一属性名，处理简称"""
        return self._short_attrs.get(element, element)

    def _count_team_element_skills(self, team_pets_except_idx: int) -> dict:
        """统计队伍中其他宠物各属性攻击技能数量"""
        counts = {}
        for pet in team_pets_except_idx:
            all_skills = pet["skills"].get("learnset", []) + pet["skills"].get("recommended", [])
            for sk in all_skills:
                if sk.get("power", 0) > 0:  # 只统计攻击技能
                    elem = self._normalize_element(sk.get("element", ""))
                    counts[elem] = counts.get(elem, 0) + 1
        return counts

    def analyze_team(self, team_ids: list[str]) -> dict:
        """分析一个6宠队伍"""
        if len(team_ids) != 6:
            return {"error": f"队伍需要6只宠物，当前有{len(team_ids)}只"}

        # 获取每只宠物的数据（支持按名称模糊匹配）
        team_pets = []
        resolved_ids = []
        for pet_id in team_ids:
            pet = self.pets.get(pet_id)
            if not pet:
                # 尝试按名称匹配
                found = False
                for pid, p in self.pets.items():
                    pname = p.get("name", "")
                    if pname.startswith(pet_id) or pet_id in pname:
                        pet = p
                        pet_id = pid
                        found = True
                        break
                if not found:
                    return {"error": f"宠物不存在: {pet_id}"}
            pet["id"] = pet_id
            team_pets.append(pet)
            resolved_ids.append(pet_id)
        team_ids = resolved_ids

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

        cost_std = statistics.stdev(avg_costs) if len(avg_costs) > 1 else 0
        energy_synergy = max(0, 100 - cost_std * 15)

        # 4. 生存梯队分析（前4只的抗打击能力）
        first_four_hp = sum(p["stats"]["hp"] * p["stats"]["def"] for p in team_pets[:4])
        first_four_spd = sum(p["stats"]["spd"] for p in team_pets[:4])

        # 5. 速度线分析
        speed_line = sorted([p["stats"]["spd"] for p in team_pets], reverse=True)

        # ========================================
        # 6. 负面特性分析（新增）
        # ========================================
        debuff_penalty = 0
        debuff_details = []

        for i, ps in enumerate(pet_scores):
            db = ps.get("debuff", {})
            if db.get("has_debuff"):
                pet = team_pets[i]
                detail = {
                    "name": ps["name"],
                    "description": db["debuff_description"],
                    "penalty": 0,
                    "mitigation": "",
                }

                if db.get("hp_loss_on_entry"):
                    # 入场扣血：等效生存能力打折
                    hp_ratio = db.get("hp_loss_ratio", 0)
                    loss_penalty = 50 * hp_ratio  # 50%扣血 = 25分惩罚
                    detail["penalty"] = round(loss_penalty, 1)
                    detail["mitigation"] = f"等效HP仅{int((1-hp_ratio)*100)}%"

                elif db.get("energy_zero"):
                    # 初始能量为0：检查队友是否有对应属性技能支持
                    restore_elem = db.get("energy_restore_element", "")
                    other_pets = [team_pets[j] for j in range(6) if j != i]
                    elem_skills = self._count_team_element_skills(other_pets)

                    support_count = elem_skills.get(restore_elem, 0)
                    if support_count >= 4:
                        # 队友有足够对应属性技能，惩罚减轻
                        detail["penalty"] = 5
                        detail["mitigation"] = f"队友有{support_count}个{restore_elem}系技能 → 可回能"
                    elif support_count >= 2:
                        detail["penalty"] = 10
                        detail["mitigation"] = f"队友仅有{support_count}个{restore_elem}系技能 → 勉强回能"
                    else:
                        detail["penalty"] = 18
                        detail["mitigation"] = f"队友缺乏{restore_elem}系技能({support_count}个) → 难以回能"

                elif db.get("death_buff_enemy"):
                    # 力竭给敌方增益：固定高风险惩罚
                    detail["penalty"] = 15
                    detail["mitigation"] = "被击败时敌方获得强化"

                elif db.get("death_extra_loss"):
                    detail["penalty"] = 8
                    detail["mitigation"] = "被击败时额外损失魔力"

                debuff_penalty += detail["penalty"]
                debuff_details.append(detail)

        # 原评分
        original_total = (
            avg_score * 0.4 +  # 平均个体质量
            (coverage["coverage_score"] + 20) * 2 * 0.25 +  # 属性联防
            energy_synergy * 0.2 +  # 能量协同
            (first_four_hp / 20000 * 100) * 0.15 -  # 前4只抗打击能力
            debuff_penalty * 0.3  # 负面特性扣分
        )

        # 新增：体系和角色分析
        system_result = self.system_detector.detect(team_ids)

        # 新增：构筑深度分析
        build_result = self.build_analyzer.full_analysis(team_ids)

        # 新的综合评分
        role_score = system_result["role_config"]["reasonable_score"]
        system_score = system_result["best_system"]["score"] if system_result["best_system"] else 0
        build_score = build_result["build_quality_score"]

        # 按新评分公式
        new_total = (
            original_total * 0.4 +
            role_score * 0.15 +
            system_score * 0.15 +
            build_score * 0.3  # 构筑质量权重最高
        )

        return {
            "team_score": round(new_total, 1),
            "original_score": round(original_total, 1),
            "pet_scores": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "total": p["scores"]["total"],
                    "attrs": p["attrs"],
                    "avg_cost": p["skill_data"]["avg_cost"],
                    "efficiency": p["skill_data"]["efficiency"],
                    "debuff": p.get("debuff", {}),
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
            },
            "debuff_analysis": {
                "total_penalty": round(debuff_penalty, 1),
                "details": debuff_details,
            },
            "system_analysis": system_result,
            "build_analysis": build_result,
            "role_breakdown": {
                pet_id: self.role_classifier.classify(pet_id)
                for pet_id in team_ids
            },
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
            debuff_warn = ""
            if p.get("debuff", {}).get("has_debuff"):
                debuff_warn = f"  ⚠ {p['debuff']['debuff_description']}"
            print(f"  {i}. {p['name']:<12} ({attrs:<10}) 评分: {p['total']:5.1f}  能耗: {p['avg_cost']:.1f}  效率: {p['efficiency']:.1f}{debuff_warn}")

            # 推荐技能
            skills = self.pet_scorer.recommend_skills(p["id"])
            if skills:
                print(f"     推荐技能:", end="")
                for j, sk in enumerate(skills):
                    pwr_str = f"威力{sk['power']}" if sk['power'] > 0 else "—"
                    print(f"\n       {j+1}. {sk['name']}({sk['element']}/{sk['category']}) 能耗{sk['cost']} {pwr_str}  {sk['desc'][:30]}", end="")
                print()

        # 负面特性警告
        debuff = result.get("debuff_analysis", {})
        if debuff.get("details"):
            print("\n【负面特性分析】")
            for d in debuff["details"]:
                print(f"  ⚠ {d['name']}: {d['description']}")
                print(f"     队伍扣分: -{d['penalty']:.1f} | {d['mitigation']}")
            print(f"  负面特性总扣分: -{debuff['total_penalty']:.1f}")

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

        # 角色配置分析
        print("\n【角色配置分析】")
        role_names = {"cleaner": "清场手", "starter": "首发", "support": "辅助", "finisher": "扫尾"}
        for r_key, r_name in role_names.items():
            count = result["system_analysis"]["role_config"]["counts"].get(r_key, 0)
            check = "✓" if count >= 1 else ""
            print(f"  {r_name}: {count}只 {check}")
        print(f"  配置合理性: {result['system_analysis']['role_config']['reasonable_score']}/100")

        # 体系识别结果
        print("\n【体系识别结果】")
        if result["system_analysis"]["systems"]:
            for i, sys in enumerate(result["system_analysis"]["systems"], 1):
                status = "完整体系 ✓" if sys["is_complete"] else "不完整"
                print(f"  {i}. {sys['name']} - 完成度 {sys['score']}% ({status})")
                if sys["details"]:
                    detail_str = ", ".join([f"{k}: {v}" for k, v in sys["details"].items()])
                    print(f"     详情: {detail_str}")
        else:
            print("  未检测到明确体系")

        # 首领化冲突检测
        print("\n【首领化分析】")
        lc = result["system_analysis"]["leader_conflict"]
        if lc["dependent_pets"]:
            pet_str = ", ".join(lc["dependent_pets"])
            warn = "  ⚠ 冲突!" if lc["has_conflict"] else ""
            print(f"  依赖首领化精灵: {pet_str} ({lc['count']}只){warn}")
            if lc["has_conflict"]:
                print(f"  警告: 首领化只能用一次，建议只保留1个核心首领化精灵")
        else:
            print("  无首领化依赖")

        # 构筑深度分析
        build = result.get("build_analysis", {})
        if build:
            print(f"\n【构筑风格】: {build['build_style']['primary_style']}")

            print("\n【PVP热门精灵应对】")
            hot_cov = build.get("hot_pet_coverage", {})
            print(f"  {hot_cov.get('summary', '无数据')}")

            # 显示高危热门
            red_threats = hot_cov.get("red_threats", [])
            if red_threats:
                print(f"  【无应对手段的热门精灵】")
                for t in red_threats[:8]:
                    print(f"    🔴 {t['name']}(速度{t['speed']}) {t['attrs']}")
                if len(red_threats) > 8:
                    print(f"    ... 还有{len(red_threats)-8}只")

            # 显示勉强应对的
            yellow_threats = hot_cov.get("yellow_threats", [])
            if yellow_threats:
                print(f"  【仅1只可应对的热门精灵】")
                for t in yellow_threats[:5]:
                    print(f"    🟡 {t['name']}(速度{t['speed']}) -> {t['counter_pets']}")
                if len(yellow_threats) > 5:
                    print(f"    ... 还有{len(yellow_threats)-5}只")

            print(f"\n【构筑综合评分】: {build['build_quality_score']}/100")

        print("\n" + "=" * 70)
        return result


if __name__ == "__main__":
    # 测试：用评分最高的6只组队
    analyzer = TeamAnalyzer()
    rankings = analyzer.pet_scorer.get_all_rankings()
    top6_ids = [p["id"] for p in rankings[:6]]

    print("测试队伍：评分最高的6只宠物")
    analyzer.print_team_report(top6_ids)
