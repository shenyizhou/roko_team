#!/usr/bin/env python3
"""
队伍体系识别器
识别毒队、星陨队、雷暴队等战术体系
"""
class SystemDetector:
    def __init__(self):
        from . import get_all_pets_with_skills
        self.pets = get_all_pets_with_skills()

        # 体系定义
        self.systems = {
            "毒队": {
                "core": ["厉毒修萝", "裘卡"],
                "synergy": ["影狸", "剧毒蜈蚣"],
                "calc": self._calc_poison_team,
            },
            "星陨队": {
                "core": ["怖哭菇", "落陨星兔"],
                "synergy": ["帕尔", "暮星辰", "圣羽翼王"],
                "calc": self._calc_starfall_team,
            },
            "独角兽队": {
                "core": ["白金独角兽"],
                "synergy": ["黄金独角兽"],
                "calc": self._calc_unicorn_team,
            },
            "恶魔狼王队": {
                "core": ["恶魔狼", "奇瓦亚"],
                "synergy": ["音速犬"],
                "calc": self._calc_wolfking_team,
            },
            "三三队": {
                "core": ["梦想三三"],
                "synergy": [],
                "calc": self._calc_sansan_team,
            },
            "火神队": {
                "core": ["火神"],
                "synergy": [],
                "calc": self._calc_firegod_team,
            },
            "地武队": {
                "core": ["棋绮后"],
                "synergy": [],
                "calc": self._calc_earth_warrior_team,
            },
            "圣羽翼王队": {
                "core": ["圣羽翼王"],
                "synergy": ["岚鸟"],
                "calc": self._calc_wingking_team,
            },
            "圣剑队": {
                "core": ["圣剑"],
                "synergy": [],
                "calc": self._calc_sword_team,
            },
            "雷暴队": {
                "core": ["闪电鳗鱼", "星光狮"],
                "synergy": [],
                "calc": self._calc_thunder_team,
            },
        }

        # 依赖首领化的精灵
        self.leader_dependent = [
            "白金独角兽", "恶魔狼", "梦想三三", "火神",
            "棋绮后", "圣剑", "圣羽翼王",
        ]

    def _has_skill_keyword(self, team: list[str], keyword: str) -> bool:
        """检查队伍中是否有含关键词的技能"""
        for pet_id in team:
            pet = self.pets.get(pet_id, {})
            # 尝试模糊匹配
            if not pet:
                for pid, p in self.pets.items():
                    if p.get("name", "").startswith(pet_id) or pet_id in p.get("name", ""):
                        pet = p
                        break
            skills = pet.get("skills", {}).get("learnset", []) + pet.get("skills", {}).get("recommended", [])
            for sk in skills:
                if keyword in str(sk.get("desc", "")) or keyword in str(sk.get("name", "")):
                    return True
        return False

    def _has_pet_name_contains(self, members: set[str], keyword: str) -> bool:
        """检查成员集合中是否有宠物名称包含关键词（模糊匹配）"""
        for m in members:
            # 直接从pets中找ID
            pet = self.pets.get(m, None)
            if pet:
                if keyword in pet.get("name", ""):
                    return True
            else:
                # ID不存在，尝试模糊匹配所有宠物的名称
                for pid, p in self.pets.items():
                    if keyword in p.get("name", ""):
                        return True
        return False

    def _count_pet_name_contains(self, members: set[str], keywords: list[str]) -> int:
        """统计成员集合中有多少个宠物名称包含关键词（模糊匹配）"""
        count = 0
        for keyword in keywords:
            if self._has_pet_name_contains(members, keyword):
                count += 1
        return count

    def _calc_poison_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        # 核心: 厉毒修萝 + 裘卡
        core_count = 0
        if self._has_pet_name_contains(members, "厉毒修萝"):
            core_count += 1
            score += 20
        if self._has_pet_name_contains(members, "裘卡"):
            core_count += 1
            score += 20
        details["core"] = core_count

        # 协同: 其他毒系精灵
        synergy_count = 0
        for name in ["影狸", "剧毒蜈蚣"]:
            if self._has_pet_name_contains(members, name):
                synergy_count += 1
        score += synergy_count * 15
        details["synergy"] = synergy_count

        # 有印记驱散/转化
        member_list = list(members)
        if self._has_skill_keyword(member_list, "驱散印记") or self._has_skill_keyword(member_list, "转化"):
            score += 30
            details["has_dispel"] = True

        return {"score": min(100, score), "details": details}

    def _calc_starfall_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        # 核心
        core_count = 0
        if self._has_pet_name_contains(members, "怖哭菇"):
            core_count += 1
            score += 25
        if self._has_pet_name_contains(members, "落陨星兔"):
            core_count += 1
            score += 25
        details["core"] = core_count

        # 炸能量
        if self._has_pet_name_contains(members, "帕尔"):
            score += 25
            details["has_paer"] = True

        # 迅捷触发
        if self._has_pet_name_contains(members, "圣羽翼王") or self._has_pet_name_contains(members, "暮星辰"):
            score += 25
            details["has_swift_trigger"] = True

        return {"score": min(100, score), "details": details}

    def _calc_unicorn_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "白金独角兽"):
            score += 60
            details["core"] = True

        # 有折射技能
        member_list = list(members)
        if self._has_skill_keyword(member_list, "折射"):
            score += 20
            details["has_refract"] = True

        return {"score": min(100, score), "details": details}

    def _calc_wolfking_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        core_count = 0
        if self._has_pet_name_contains(members, "恶魔狼"):
            core_count += 1
            score += 30
        if self._has_pet_name_contains(members, "奇瓦亚"):
            core_count += 1
            score += 20
        details["core"] = core_count

        # 送死组件
        if self._has_pet_name_contains(members, "音速犬"):
            score += 30
            details["has_sacrifice"] = True

        return {"score": min(100, score), "details": details}

    def _calc_sansan_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "梦想三三"):
            score += 60
            details["core"] = True

        # 甜心技能
        member_list = list(members)
        if self._has_skill_keyword(member_list, "甜心"):
            score += 20
            details["has_sweet"] = True

        return {"score": min(100, score), "details": details}

    def _calc_firegod_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "火神"):
            score += 50
            details["core"] = True

        # 火天气
        member_list = list(members)
        if self._has_skill_keyword(member_list, "将天气改为晴天"):
            score += 25
            details["has_fire_weather"] = True

        return {"score": min(100, score), "details": details}

    def _calc_earth_warrior_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "棋绮后"):
            score += 40
            details["core"] = True

        # 地刺精灵数量
        member_list = list(members)
        earth_spike_count = sum(1 for m in member_list if self._has_skill_keyword([m], "地刺"))
        score += earth_spike_count * 15
        details["earth_spike_count"] = earth_spike_count

        return {"score": min(100, score), "details": details}

    def _calc_wingking_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "圣羽翼王"):
            score += 50
            details["core"] = True

        # 翼系协同
        wing_count = 0
        for name in ["岚鸟", "黑羽夫人"]:
            if self._has_pet_name_contains(members, name):
                wing_count += 1
        score += wing_count * 25
        details["wing_synergy"] = wing_count

        return {"score": min(100, score), "details": details}

    def _calc_sword_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        if self._has_pet_name_contains(members, "圣剑"):
            score += 70
            details["core"] = True

        return {"score": min(100, score), "details": details}

    def _calc_thunder_team(self, members: set[str]) -> dict:
        score = 0
        details = {}

        core_count = 0
        if self._has_pet_name_contains(members, "闪电鳗鱼"):
            core_count += 1
            score += 30
        if self._has_pet_name_contains(members, "星光狮"):
            core_count += 1
            score += 30
        details["core"] = core_count

        # 雷暴天气
        member_list = list(members)
        if self._has_skill_keyword(member_list, "雷暴"):
            score += 40
            details["has_thunder_weather"] = True

        return {"score": min(100, score), "details": details}

    def detect(self, team: list[str]) -> dict:
        """检测一支队伍的体系"""
        members = set(team)
        results = []

        for sys_name, sys_config in self.systems.items():
            calc_func = sys_config["calc"]
            result = calc_func(members)
            if result["score"] > 0:
                results.append({
                    "name": sys_name,
                    "score": result["score"],
                    "details": result["details"],
                    "is_complete": result["score"] >= 70,
                })

        # 按得分排序
        results.sort(key=lambda x: -x["score"])

        # 首领化冲突检测
        leader_count = 0
        for m in members:
            pet_name = self.pets.get(m, {}).get("name", m)
            if any(ld in pet_name for ld in self.leader_dependent):
                leader_count += 1
        has_leader_conflict = leader_count > 1 and not self._has_pet_name_contains(members, "恶魔狼")

        # 恶魔狼王队例外，允许多首领候补
        if self._has_pet_name_contains(members, "恶魔狼") and leader_count > 1:
            has_leader_conflict = False

        # 角色配置合理性
        from models.role_classifier import RoleClassifier
        rc = RoleClassifier()

        role_counts = {"cleaner": 0, "starter": 0, "support": 0, "finisher": 0}
        for pet_id in team:
            roles = rc.classify(pet_id)
            if "primary_roles" in roles:
                for r in roles["primary_roles"]:
                    role_counts[r] = role_counts.get(r, 0) + 1

        # 角色合理性评分：每个角色至少有1个，清场手最好有2个
        role_score = 0
        for rname, count in role_counts.items():
            if count >= 1:
                role_score += 25
            if rname == "cleaner" and count >= 2:
                role_score += 15

        return {
            "systems": results[:3],  # 前3个得分最高的体系
            "best_system": results[0] if results else None,
            "leader_conflict": {
                "count": leader_count,
                "has_conflict": has_leader_conflict,
                "dependent_pets": [m for m in members if any(ld in self.pets.get(m, {}).get("name", "") for ld in self.leader_dependent)],
            },
            "role_config": {
                "counts": role_counts,
                "reasonable_score": min(100, role_score),
            },
        }


if __name__ == "__main__":
    sd = SystemDetector()

    # 测试雷暴队（纯净名称）
    test_team = ["闪电鳗鱼", "星光狮", "影狸", "黑羽夫人", "寂灭骨龙", "贝古斯"]
    result = sd.detect(test_team)
    print("测试1 - 纯净名称队伍体系检测:")
    for sys in result["systems"]:
        print(f"  {sys['name']}: {sys['score']}分 {'✓' if sys['is_complete'] else ''}")

    print(f"首领化冲突: {result['leader_conflict']}")
    print()

    # 测试雷暴队（NO.XXX ID格式，实际数据格式 - ID就是NO.XXX，名称包含关键词）
    test_team_id = ["NO.073", "NO.034", "NO.xxx", "NO.xxx", "NO.xxx", "NO.xxx"]
    result_id = sd.detect(test_team_id)
    print("测试2 - NO.XXX ID格式队伍体系检测:")
    for sys in result_id["systems"]:
        print(f"  {sys['name']}: {sys['score']}分 {'✓' if sys['is_complete'] else ''}")

    print(f"首领化冲突: {result_id['leader_conflict']}")
    print(f"角色配置: {result_id['role_config']}")
    print()

    # 测试雷暴队 - 真正完整雷暴队（NO.XXX ID格式）
    test_thunder_complete = ["NO.073", "NO.034", "NO.xxx", "NO.xxx", "NO.xxx", "NO.xxx"]
    # 这个测试只需要核心精灵就能得到60分，加上雷暴天气就是100分
    # 让我们验证一下识别逻辑是否正确
    result_thunder = sd.detect(test_thunder_complete)
    print("测试3 - 完整雷暴队（包含闪电鳗鱼+星光狮，ID格式NO.XXX）:")
    for sys in result_thunder["systems"]:
        print(f"  {sys['name']}: {sys['score']}分 {'✓' if sys['is_complete'] else ''}")
    thunder_result = next(sys for sys in result_thunder["systems"] if sys["name"] == "雷暴队")
    print(f"  核心精灵计数: {thunder_result['details']['core_count'] if 'core_count' in thunder_result['details'] else thunder_result['details']['core']}")
