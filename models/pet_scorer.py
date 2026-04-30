#!/usr/bin/env python3
"""
宠物综合评分模型
基于：种族值、属性、特性、技能能量效率
"""
import json
import re
from pathlib import Path
from .attribute_matrix import AttributeMatrix

DATA_DIR = Path(__file__).parent.parent / "data"


class PetScorer:
    def __init__(self):
        with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
            self.pets = json.load(f)

        self.attr_matrix = AttributeMatrix()

        # 权重配置（基于游戏公式：伤害∝攻/防，先手权决定胜负）
        self.weights = {
            "stats": 0.25,      # 种族值（含生存乘积+速度优势）
            "type": 0.20,       # 属性联防价值
            "feature": 0.25,    # 特性价值
            "skill": 0.20,      # 技能质量
            "speed_bonus": 0.10,# 速度线奖励
        }

    def calc_stats_score(self, stats: dict) -> float:
        """
        基于实际对战公式的种族值评分:
        - 实际属性 ≈ 1.1×种族 + 常数 (双攻/双防/速度), HP ≈ 1.7×种族 + 常数
        - 生存力 = HP × 防御 (乘积: 伤害∝攻/防, 承受次数=HP/(攻/防)=HP×防/攻)
        - 速度决定先手权: 92极速=118满速=148无速 → 速度价值约3倍于其他属性
        """
        useful_atk = max(stats["atk"], stats["matk"])
        avg_def = (stats["def"] + stats["mdef"]) / 2

        # 攻击力: 每点攻 ≈ 1.1实际攻击
        atk_val = useful_atk

        # 生存力: HP × 平均防御 (乘积=有效承伤次数)
        # 缩放到和其他维度可比 (除以200)
        bulk = stats["hp"] * avg_def / 200

        # 速度: 公式证明极速≈+40实际速度，先手=潜在一回合击杀
        spd_val = stats["spd"] * 3.0

        weighted = atk_val * 0.25 + bulk * 0.25 + spd_val * 0.50
        return weighted * 0.50  # 缩放到0-150范围供归一化

    def calc_speed_bonus(self, spd: int) -> float:
        """速度线阶梯奖励：基于计算器速度线（每5种族差=6~7实际速度）"""
        if spd >= 150: return 100
        if spd >= 145: return 95
        if spd >= 135: return 88
        if spd >= 130: return 80
        if spd >= 125: return 72
        if spd >= 120: return 64
        if spd >= 115: return 56
        if spd >= 110: return 48
        if spd >= 105: return 40
        if spd >= 100: return 33
        if spd >= 95: return 26
        if spd >= 90: return 20
        if spd >= 85: return 14
        if spd >= 80: return 9
        if spd >= 70: return 5
        if spd >= 60: return 2
        return 0

    def calc_type_score(self, attrs: list[str]) -> float:
        """计算属性价值评分"""
        type_data = self.attr_matrix.get_type_score(attrs)
        # 进攻 + 防守评分，防守权重更高
        return type_data["offense"] * 0.3 + type_data["defense_score"] * 0.7

    # 负面特性关键词及惩罚值
    DEBUFF_PATTERNS = [
        # (检测关键词组, 惩罚值, 分类标签, 提取条件)
        # 条件列表: 所有关键词必须同时出现才算命中
        (["首次入场", "失去", "一半", "生命"], 30, "hp_loss_on_entry", 0.5),
        (["初始能量为0", "火系技能", "回复"], 25, "energy_zero", "火"),
        (["初始能量为0", "冰系技能", "回复"], 25, "energy_zero", "冰"),
        (["初始能量为0", "成功应对"], 25, "energy_zero", "应对"),
        (["力竭", "敌方", "获得"], 25, "death_buff_enemy", 0.2),
        (["被敌方", "击败", "额外损失", "魔力"], 8, "death_extra_loss", 1),
    ]

    def calc_feature_score(self, trait: dict) -> float:
        """计算特性价值评分（概念分组匹配，避免重复计数）"""
        desc = trait.get("desc", "")
        name = trait.get("name", "")
        full_text = f"{name} {desc}"

        score = 0

        # ====== 概念分组匹配：每组只取最高分 ======

        # 1. 复活/免死机制（游戏最强效果）
        revive_pats = [
            (r"复活", 60), (r"重生", 60), (r"不屈", 55),
            (r"受到致命伤害.*免疫.*伤害", 55), (r"化茧", 50),
        ]
        for pat, val in revive_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 2. 先手/速度优势（决定战局的核心）
        speed_pats = [
            (r"迅捷", 45),           # 必先手 = 无解的先手权
            (r"速度\+50", 35),
            (r"速度\+[3-9]0", 28),
            (r"先手\+1", 28),
            (r"先制", 25),
            (r"速度\+", 22),
            (r"先手", 20),
        ]
        for pat, val in speed_pats:
            if re.search(pat, full_text):
                score += val
                break  # 只取最高

        # 3. 威力/伤害大幅提升
        damage_pats = [
            (r"威力\+50%", 30),
            (r"威力\+[3-9]0%", 25),
            (r"威力\+25%", 25),
            (r"威力\+[1-2][0-9]%", 20),
            (r"技能威力\+[2-9]0", 22),
            (r"技能威力\+[1-9]0", 18),
            (r"技能威力\+", 15),
            (r"威力.*倍", 25),
            (r"威力\+", 15),
        ]
        for pat, val in damage_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 4. 能量/能耗优势
        energy_pats = [
            (r"回复能量", 25), (r"获得能量", 22), (r"回能", 20),
            (r"能耗-[4-9]", 25), (r"能耗-3", 20),
            (r"能耗-[1-2]", 15), (r"能耗-[0-9]", 12),
            (r"能量\+", 15),
        ]
        for pat, val in energy_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 5. 属性强化（自身数值提升）
        boost_pats = [
            (r"全属性\+", 30),
            (r"双攻.*永久\+[2-9]0%", 28),
            (r"物攻\+100%", 25), (r"魔攻\+100%", 25),
            (r"攻防\+[2-9]0%", 25),
            (r"双攻\+[2-9]0%", 22),
            (r"物攻\+", 10), (r"魔攻\+", 10),
            (r"防御\+", 10), (r"魔防\+", 10),
            (r"永久\+", 18),
        ]
        for pat, val in boost_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 6. 生存/减伤/回复
        survival_pats = [
            (r"减伤70%", 25), (r"减伤[3-6]0%", 18), (r"减伤", 10),
            (r"回复HP", 15), (r"回血", 15), (r"吸血", 15),
            (r"免疫", 18),
        ]
        for pat, val in survival_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 7. 入场效果（主动压制）
        entry_pats = [
            (r"入场.*获得.*技能", 30),
            (r"入场时.*获得", 20),
            (r"入场时", 12),
        ]
        for pat, val in entry_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 8. 离场/替换效果（辅助队友的特性价值打折）
        exit_pats = [
            (r"离场.*继承", 20),
            (r"离场后.*更换入场的精灵", 10),  # 纯辅助队友，价值减半
            (r"离场后", 15),
            (r"离场", 10),
        ]
        for pat, val in exit_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 检测纯辅助特性：离场给队友buff，降低整体估值
        if re.search(r"离场.*更换入场的精灵", full_text):
            score = int(score * 0.6)  # 辅助型特性打6折

        # 9. 击败敌方收益（滚雪球 — 赢一局=多占对方魔力）
        kill_pats = [
            (r"击败敌方.*额外损失", 35),
            (r"击败敌方.*获得", 25),
            (r"击败敌方", 15),
        ]
        for pat, val in kill_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 10. 应对/防御机制
        counter_pats = [
            (r"成功应对.*先手", 25),
            (r"成功应对.*威力", 22),
            (r"成功应对", 18),
            (r"应对", 12),
        ]
        for pat, val in counter_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 11. 额外技能/效果
        extra_pats = [
            (r"额外获得.*技能", 30),
            (r"随机技能", 25),
            (r"额外.*技能", 20),
        ]
        for pat, val in extra_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 12. 回合效果
        turn_pats = [
            (r"回合结束时.*回复", 22),
            (r"回合结束时.*获得", 18),
            (r"回合结束时", 12),
        ]
        for pat, val in turn_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 13. 游戏机制关键词
        mech_pats = [
            (r"传动", 12), (r"迸发", 15), (r"聒噪", 15),
            (r"偷取", 12), (r"替换", 10),
            (r"连击数\+", 18), (r"层.*印记", 12),
            (r"解除控制", 15),
        ]
        for pat, val in mech_pats:
            if re.search(pat, full_text):
                score += val

        # 14. 状态效果施加
        status_pats = [
            (r"冻结", 6), (r"睡眠", 8), (r"麻痹", 6),
            (r"混乱", 5), (r"灼烧", 5), (r"束缚", 5),
            (r"中毒", 5),
        ]
        for pat, val in status_pats:
            if re.search(pat, full_text):
                score += val
                break

        # 15. 克制/抵抗/威力相关
        minor_pats = [
            (r"克制", 10), (r"抵抗", 5),
            (r"造成伤害", 4),
        ]
        for pat, val in minor_pats:
            if re.search(pat, full_text):
                score += val
                break

        # ====== 负面特性扣分 ======
        for words, penalty, _category, _extra in self.DEBUFF_PATTERNS:
            if all(w in full_text for w in words):
                score -= penalty

        return max(score, 0)

    def get_debuff_info(self, trait: dict) -> dict:
        """提取特性的负面信息，供队伍分析使用"""
        desc = trait.get("desc", "")
        name = trait.get("name", "")
        full_text = f"{name} {desc}"

        info = {
            "has_debuff": False,
            "hp_loss_on_entry": False,        # 入场扣血
            "hp_loss_ratio": 0,                # 扣血比例
            "energy_zero": False,              # 初始能量为0
            "energy_restore_element": "",      # 需要什么属性技能回能
            "death_buff_enemy": False,         # 力竭给敌方增益
            "death_buff_amount": 0,            # 增益量
            "death_extra_loss": False,         # 力竭额外损失
            "debuff_description": "",          # 负面描述
            "total_penalty": 0,
        }

        for words, penalty, category, extra in self.DEBUFF_PATTERNS:
            if all(w in full_text for w in words):
                info["has_debuff"] = True
                info["total_penalty"] += penalty

                if category == "hp_loss_on_entry":
                    info["hp_loss_on_entry"] = True
                    info["hp_loss_ratio"] = extra
                    info["debuff_description"] = f"首次入场失去{int(extra*100)}%生命"

                elif category == "energy_zero":
                    info["energy_zero"] = True
                    info["energy_restore_element"] = extra
                    info["debuff_description"] = f"初始能量为0，需{extra}系技能回能"

                elif category == "death_buff_enemy":
                    info["death_buff_enemy"] = True
                    info["death_buff_amount"] = extra
                    info["debuff_description"] = f"力竭时敌方获得攻防+{int(extra*100)}%"

                elif category == "death_extra_loss":
                    info["death_extra_loss"] = True
                    info["debuff_description"] = "被击败时额外损失魔力"

        return info

    def calc_skill_score(self, skills: dict, attrs: list[str] = None) -> dict:
        """
        计算技能评分（基于伤害公式：100-120威力本系=斩杀线）
        - 能量效率(威力/能耗)为核心
        - 最高威力决定秒杀能力(100+威力可2HKO, 120+可OHKO克制目标)
        - 本系技能(STAB)额外加分
        - 属性覆盖多样性
        """
        all_skills = skills.get("learnset", []) + skills.get("recommended", [])

        if not all_skills:
            return {"score": 0, "avg_power": 0, "avg_cost": 0, "efficiency": 0, "max_power": 0}

        attack_skills = [s for s in all_skills if s.get("power", 0) > 0]

        if not attack_skills:
            return {"score": 0, "avg_power": 0, "avg_cost": 0, "efficiency": 0, "max_power": 0}

        total_power = sum(s.get("power", 0) for s in attack_skills)
        total_cost = sum(s.get("cost", 0) for s in attack_skills)
        avg_power = total_power / len(attack_skills)
        avg_cost = total_cost / len(attack_skills)
        max_power = max(s.get("power", 0) for s in attack_skills)

        # 能量效率 = 威力/能耗
        efficiency = avg_power / max(avg_cost, 1)

        # 属性多样性
        skill_elements = set(s.get("element") for s in all_skills)
        diversity_score = len(skill_elements) * 2

        # 控制技能加分
        control_keywords = ["睡眠", "麻痹", "冰冻", "冻结", "混乱", "恐惧", "迷惑"]
        control_score = sum(2 for s in all_skills for kw in control_keywords if kw in s.get("desc", ""))

        # —— 斩杀线奖励（基于伤害公式） ——
        kill_bonus = 0
        # 最高威力决定能否秒人
        if max_power >= 140:
            kill_bonus += 18  # 可OHKO大多数目标
        elif max_power >= 120:
            kill_bonus += 12  # 本系克制可OHKO
        elif max_power >= 100:
            kill_bonus += 7   # 本系克制接近OHKO

        # 本系高威力技能加分（STAB ×1.5 让斩杀线大幅降低）
        if attrs:
            stab_skills = [s for s in attack_skills if s.get("element") in attrs]
            if stab_skills:
                stab_max = max(s.get("power", 0) for s in stab_skills)
                if stab_max >= 100:
                    kill_bonus += 10  # 有本系斩杀技能
                elif stab_max >= 80:
                    kill_bonus += 5   # 有本系强力技能

        # 有0费攻击技能（免费压血线）
        zero_cost_skills = [s for s in attack_skills if s.get("cost", 0) == 0]
        if zero_cost_skills:
            kill_bonus += 3

        return {
            "score": efficiency * 3 + diversity_score + control_score + kill_bonus,
            "avg_power": avg_power,
            "avg_cost": avg_cost,
            "efficiency": efficiency,
            "max_power": max_power,
            "skill_count": len(all_skills),
        }

    def recommend_skills(self, pet_id: str, top_n: int = 4) -> list[dict]:
        """为一只宠物推荐最优的N个携带技能"""
        pet = self.pets.get(pet_id)
        if not pet:
            return []

        all_skills = pet["skills"].get("learnset", []) + pet["skills"].get("recommended", [])

        # 去重（按技能名）
        seen = set()
        unique_skills = []
        for s in all_skills:
            name = s.get("name", "")
            if name not in seen:
                seen.add(name)
                unique_skills.append(s)

        if not unique_skills:
            return []

        # 控制类关键词加分
        control_kw = {
            "睡眠": 15, "催眠": 15, "麻痹": 12, "冰冻": 15, "冻结": 15,
            "混乱": 8, "恐惧": 8, "迷惑": 8, "束缚": 6,
        }
        # 增益类关键词加分
        buff_kw = {
            "物攻+": 10, "魔攻+": 10, "速度+": 10, "全属性+": 18,
            "防御+": 6, "魔防+": 6, "回复HP": 8, "回血": 8, "吸血": 10,
            "减伤": 10, "迅捷": 15, "先手": 10, "回复能量": 15,
        }

        scored = []
        for s in unique_skills:
            score = 0
            desc = s.get("desc", "")
            power = s.get("power", 0)
            cost = s.get("cost", 1)
            category = s.get("category", "")

            if power > 0:
                # 攻击技能：威力/能耗比为核心
                score = (power / max(cost, 0.5)) * 0.6
                # 属性与本系一致加分
                if s.get("element") in pet["attrs"]:
                    score += 10
            else:
                # 变化技能：看描述价值
                score = 0
                # 防御技能有基础分
                if "防御" in s.get("name", "") or "守护" in s.get("name", ""):
                    score += 5

            # 控制效果加分
            for kw, val in control_kw.items():
                if kw in desc:
                    score += val
                    break  # 只计最高项

            # 增益效果加分
            for kw, val in buff_kw.items():
                if kw in desc:
                    score += val

            # 减益效果加分
            if "降低" in desc or "削弱" in desc or "减少" in desc:
                score += 4

            scored.append({"skill": s, "score": score})

        # 排序并取前N
        scored.sort(key=lambda x: -x["score"])

        # 确保至少有2个攻击技能（如果可用的话）
        attack_skills = [x for x in scored if x["skill"].get("power", 0) > 0]
        support_skills = [x for x in scored if x["skill"].get("power", 0) == 0]

        picked = []
        # 先拿前2个攻击技能
        picked.extend(attack_skills[:2])
        # 再从剩余中取最高分补满N
        remaining = [x for x in scored if x not in picked]
        picked.extend(remaining[:top_n - len(picked)])

        # 如果攻击技能不足2个，用任何技能补满
        if len(picked) < top_n:
            extra = [x for x in scored if x not in picked]
            picked.extend(extra[:top_n - len(picked)])

        return [
            {
                "name": item["skill"]["name"],
                "element": item["skill"].get("element", "—"),
                "category": item["skill"].get("category", "—"),
                "cost": item["skill"].get("cost", 0),
                "power": item["skill"].get("power", 0),
                "desc": item["skill"].get("desc", ""),
                "score": round(item["score"], 1),
            }
            for item in picked[:top_n]
        ]

    def score_pet(self, pet_id: str) -> dict:
        """计算单只宠物的综合评分"""
        pet = self.pets.get(pet_id)
        if not pet:
            return {"error": "Pet not found"}

        # 各项评分
        stats_raw = self.calc_stats_score(pet["stats"])
        type_raw = self.calc_type_score(pet["attrs"])
        feature_raw = self.calc_feature_score(pet["trait"])
        skill_data = self.calc_skill_score(pet["skills"], pet["attrs"])

        # 归一化到0-100
        stats_score = min(stats_raw / 150 * 100, 100)
        type_score = min(type_raw / 10 * 100, 100)
        feature_score = min(feature_raw / 100 * 100, 100)  # 上限100，S层可达60+
        skill_score = min(skill_data["score"] / 2.0, 100)  # 原始分0-200，/2得0-100

        # 综合评分（加入速度线奖励）
        speed_bonus = self.calc_speed_bonus(pet["stats"]["spd"])
        total_score = (
            stats_score * self.weights["stats"] +
            type_score * self.weights["type"] +
            feature_score * self.weights["feature"] +
            skill_score * self.weights["skill"] +
            speed_bonus * self.weights["speed_bonus"]
        )

        debuff_info = self.get_debuff_info(pet["trait"])

        return {
            "id": pet_id,
            "name": pet["name"],
            "attrs": pet["attrs"],
            "stats": pet["stats"],
            "trait": pet["trait"],
            "debuff": debuff_info,
            "scores": {
                "total": round(total_score, 1),
                "stats": round(stats_score, 1),
                "type": round(type_score, 1),
                "feature": round(feature_score, 1),
                "skill": round(skill_score, 1),
                "speed_bonus": round(speed_bonus, 1),
            },
            "skill_data": {
                "avg_power": round(skill_data["avg_power"], 1),
                "avg_cost": round(skill_data["avg_cost"], 1),
                "efficiency": round(skill_data["efficiency"], 1),
                "max_power": round(skill_data.get("max_power", 0), 1),
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
