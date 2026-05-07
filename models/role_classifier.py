#!/usr/bin/env python3
"""
精灵角色分类器
识别每只精灵可以是：清场手(Cleaner)、首发(Starter)、辅助(Support)、扫尾(Finisher)
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


class RoleClassifier:
    def __init__(self):
        from . import get_all_pets_with_skills
        self.pets = get_all_pets_with_skills()

    def _has_buff_skills(self, pet_id: str) -> dict:
        """检查是否有强化技能"""
        pet = self.pets.get(pet_id, {})
        skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
        has_atk_buff = any(re.search(r"[物魔双]攻\+\d+%", str(sk.get("desc", ""))) for sk in skills)
        has_spd_buff = any("速度+" in str(sk.get("desc", "")) for sk in skills)
        return {"atk": has_atk_buff, "spd": has_spd_buff}

    def _has_heal_skills(self, pet_id: str) -> bool:
        """检查是否有回复技能"""
        pet = self.pets.get(pet_id, {})
        skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
        heal_keywords = ["回复", "治疗", "回血", "休息回复", "续航"]
        return any(kw in str(sk.get("desc", "")) for kw in heal_keywords for sk in skills)

    def classify(self, pet_id: str) -> dict:
        """为一只精灵计算四个角色的得分"""
        pet = self.pets.get(pet_id)
        if not pet:
            # 如果不是直接匹配，尝试按名称搜索
            for pid, p in self.pets.items():
                if p.get("name", "").startswith(pet_id) or pet_id in p.get("name", ""):
                    pet = p
                    pet_id = pid
                    break
        if not pet:
            return {"error": "宠物不存在"}

        stats = pet.get("stats", {})
        spd = stats.get("spd", 0)
        hp = stats.get("hp", 0)
        defense = stats.get("def", 0)
        trait = pet.get("trait", {}).get("desc", "")

        cleaner_score = 0
        starter_score = 0
        support_score = 0
        finisher_score = 0

        # 清场手评分
        buffs = self._has_buff_skills(pet_id)

        # 强化超速型: 有加速技能 + 强化后速度 > 130 + 有输出技能
        if buffs["spd"] and spd * 1.5 > 130 and buffs["atk"]:
            cleaner_score += 30
        elif buffs["spd"] and spd * 1.5 > 120:
            cleaner_score += 20

        # 高速强化型: 基础速度快 + 有强化
        if spd >= 120 and buffs["atk"]:
            cleaner_score += 25
        if spd >= 110 and buffs["atk"]:
            cleaner_score += 15

        # 特性优秀型
        trait_keywords = ["能量", "力竭", "斩杀", "迸发", "折射"]
        if any(kw in trait for kw in trait_keywords):
            cleaner_score += 20

        # 肉盾强化型: 身板硬 + 有回复 + 有强化
        if hp * defense >= 25000 and self._has_heal_skills(pet_id) and buffs["atk"]:
            cleaner_score += 25
        elif hp * defense >= 20000 and self._has_heal_skills(pet_id):
            cleaner_score += 15

        # 首发评分
        pet_skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])

        # 天气技能
        has_weather = any("将天气改为" in str(sk.get("desc", "")) for sk in pet_skills)
        if has_weather:
            starter_score += 30

        # 迅捷技能
        has_swift = any("迅捷" in str(sk.get("desc", "")) or sk.get("cost", 99) == 0 for sk in pet_skills)
        if has_swift:
            starter_score += 30

        # 控制技能
        control_keywords = ["眩晕", "寄生", "聒噪", "惊吓"]
        has_control = any(kw in str(sk.get("desc", "")) for kw in control_keywords for sk in pet_skills)
        if has_control:
            starter_score += 20

        # 速度快
        if spd >= 110:
            starter_score += 20

        # 辅助评分
        # 驱散类技能
        utility_keywords = ["驱散印记", "清增益", "清减益", "焚烧烙印", "食腐", "转化印记"]
        has_dispel = any(kw in str(sk.get("desc", "")) for kw in utility_keywords for sk in pet_skills)
        if has_dispel:
            support_score += 30

        # 队友增益技能
        team_buff_keywords = ["力量增效", "速度强化", "魔攻强化", "给队友", "全体"]
        has_team_buff = any(kw in str(sk.get("desc", "")) for kw in team_buff_keywords for sk in pet_skills)
        if has_team_buff:
            support_score += 25

        # 联动特性
        if "印记" in trait and "队友" in trait:
            support_score += 25

        # 高防低攻
        atk = stats.get("atk", 0) + stats.get("matk", 0)
        if (defense + stats.get("mdef", 0)) > atk * 1.5:
            support_score += 20

        # 扫尾评分
        # 斩杀类技能
        finish_keywords = ["斩杀", "HP越低", "血越少", "收割"]
        has_finish = any(kw in str(sk.get("desc", "")) for kw in finish_keywords for sk in pet_skills)
        if has_finish:
            finisher_score += 40

        # 先制
        if has_swift:
            finisher_score += 30

        # 高速高攻但身板脆
        if spd >= 110 and atk >= 180 and hp * defense < 15000:
            finisher_score += 30

        # 归一化到0-100
        def normalize(score):
            return min(100, max(0, score))

        scores = {
            "cleaner": normalize(cleaner_score),
            "starter": normalize(starter_score),
            "support": normalize(support_score),
            "finisher": normalize(finisher_score),
        }

        # 取前2个角色
        sorted_roles = sorted(scores.items(), key=lambda x: -x[1])
        primary_roles = [r[0] for r in sorted_roles[:2] if r[1] >= 30]

        return {
            "pet_id": pet_id,
            "pet_name": pet.get("name", pet_id),
            "role_scores": scores,
            "primary_roles": primary_roles,
            "primary_role_names": [{"cleaner": "清场手", "starter": "首发", "support": "辅助", "finisher": "扫尾"}[r] for r in primary_roles],
        }


if __name__ == "__main__":
    # 测试
    rc = RoleClassifier()
    test_pets = ["闪电鳗鱼", "星光狮", "厉毒修萝", "帕尔", "圣剑"]
    for pid in test_pets:
        result = rc.classify(pid)
        if "error" not in result:
            print(f"{result['pet_name']}: {result['primary_role_names']}")
            print(f"  得分: {result['role_scores']}")
