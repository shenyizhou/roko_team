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
        from . import get_all_pets_with_skills
        self.pets = get_all_pets_with_skills()

        self.attr_matrix = AttributeMatrix()

        # 权重配置（基于游戏公式：伤害∝攻/防，先手权决定胜负）
        self.weights = {
            "stats": 0.25,      # 种族值（含生存乘积+速度优势）
            "type": 0.20,       # 属性联防价值
            "feature": 0.25,    # 特性价值
            "skill": 0.20,      # 技能质量
            "speed_bonus": 0.10,# 速度线奖励
        }

    IV = 60  # 个体值

    @staticmethod
    def _base_to_hp(base_hp: int) -> int:
        """HP实际值 = 1.7 × 种族 + 105（从Excel等效血量反推）"""
        return int(base_hp * 1.7 + 105)

    @staticmethod
    def _base_to_stat(base_stat: int, nature_mod: float = 0.0) -> int:
        """
        实际属性值 = (1.1 × 种族 + 个体值×0.55 + 10) × (1 + 性格修正)
        性格修正: 高速输出手=0.2, 其他=0
        """
        raw = 1.1 * base_stat + PetScorer.IV * 0.55 + 10
        return int(raw * (1 + nature_mod))

    def _determine_attack_type(self, pet_skills: dict, stats: dict = None) -> str:
        """根据技能池和种族值判断是物攻手还是特攻手"""
        physical = 0
        special = 0
        for sk in pet_skills.get("learnset", []) + pet_skills.get("recommended", []):
            if sk.get("power", 0) > 0:
                cat = sk.get("category", "")
                if cat == "物理":
                    physical += 1
                elif cat == "魔法":
                    special += 1
        # 技能数相同时用种族值判断
        if physical == special and stats:
            return "physical" if stats.get("atk", 0) >= stats.get("matk", 0) else "special"
        return "physical" if physical >= special else "special"

    def detect_role(self, stats: dict) -> str:
        """根据种族值分布推断精灵定位"""
        useful_atk = max(stats["atk"], stats["matk"])
        avg_def = (stats["def"] + stats["mdef"]) / 2
        spd = stats["spd"]
        hp = stats["hp"]

        # 联防手: 血厚防高、速度不高
        if hp >= 80 and (hp + avg_def >= 160) and spd < 100:
            return "wall"
        # 输出手: 速度快、攻击高、不太肉
        if spd >= 95 and useful_atk >= 110:
            return "sweeper"
        # 高速脆皮
        if spd >= 110 and hp + avg_def < 170:
            return "sweeper"

        return "balanced"

    def _apply_nature(self, stats: dict, role: str, atk_type: str) -> dict:
        """
        根据定位和攻击类型应用性格加成，返回实际数值
        速度公式: (1.1×种族 + IV×0.55 + 10) × (1 + 性格修正)
        其他属性: (1.1×种族 + IV×0.55 + 10) × 性格倍率
        高速输出手(开朗/胆小): 速度+20%, 非主攻-10%
        中速输出手(固执/聪明): 主攻+10%, 非主攻-10%
        肉盾(沉默/平和):     HP+10%, 非主攻-10%
        """
        hp_mult, atk_mult, matk_mult = 1.0, 1.0, 1.0
        spd_mod = 0.0  # 速度性格修正

        if role == "wall":
            hp_mult = 1.1  # 沉默/平和: +HP
            if atk_type == "physical":
                matk_mult = 0.9  # 沉默: -魔攻
            else:
                atk_mult = 0.9   # 平和: -物攻
        elif role == "sweeper":
            spd = stats["spd"]
            if spd >= 115:
                spd_mod = 0.2  # 开朗/胆小: +20%速度
                if atk_type == "physical":
                    matk_mult = 0.9  # 开朗: -魔攻
                else:
                    atk_mult = 0.9   # 胆小: -物攻
            else:
                if atk_type == "physical":
                    atk_mult = 1.1   # 固执: +物攻
                    matk_mult = 0.9  # -魔攻
                else:
                    matk_mult = 1.1  # 聪明: +魔攻
                    atk_mult = 0.9   # -物攻
        else:
            # balanced: 默认中速输出手
            if atk_type == "physical":
                atk_mult = 1.1
                matk_mult = 0.9
            else:
                matk_mult = 1.1
                atk_mult = 0.9

        return {
            "hp": int(self._base_to_hp(stats["hp"]) * hp_mult),
            "atk": int(self._base_to_stat(stats["atk"]) * atk_mult),
            "matk": int(self._base_to_stat(stats["matk"]) * matk_mult),
            "def": self._base_to_stat(stats["def"]),
            "mdef": self._base_to_stat(stats["mdef"]),
            "spd": self._base_to_stat(stats["spd"], spd_mod),
        }

    def calc_stats_score(self, stats: dict, role: str = "balanced", atk_type: str = "physical") -> float:
        """
        基于定位、性格和实际数值的种族值评分:
        - 联防手: 物防耐久为主（环境物攻主导），攻击次要，速度不重要
        - 输出手: 攻击+速度优先，HP容错率次要
        """
        actual = self._apply_nature(stats, role, atk_type)

        useful_atk = actual["atk"] if atk_type == "physical" else actual["matk"]
        spd = actual["spd"]

        # 有效物防耐久（环境物攻主导，物防权重>特防）
        # 除以200使量纲和atk/spd可比（HP×def/200 ≈ 50~100, atk ≈ 90~140）
        phys_bulk = actual["hp"] * actual["def"] / 200
        spec_bulk = actual["hp"] * actual["mdef"] / 200
        bulk = phys_bulk * 0.7 + spec_bulk * 0.3

        if role == "wall":
            # 联防手: 耐久优先
            weighted = bulk * 0.55 + useful_atk * 0.30 + spd * 0.15
        elif role == "sweeper":
            # 输出手: 攻击+速度共80%
            weighted = useful_atk * 0.40 + spd * 0.40 + bulk * 0.20
        else:
            # 均衡
            weighted = useful_atk * 0.35 + spd * 0.35 + bulk * 0.30

        return weighted * 0.40  # 缩放到0-150范围供归一化

    def calc_speed_bonus(self, base_spd: int) -> float:
        """速度线阶梯奖励：基于实际速度值（不含性格/技能/特性加成）"""
        raw_spd = self._base_to_stat(base_spd)
        if raw_spd >= 220: return 100
        if raw_spd >= 215: return 95
        if raw_spd >= 210: return 88
        if raw_spd >= 205: return 80
        if raw_spd >= 200: return 72
        if raw_spd >= 195: return 64
        if raw_spd >= 190: return 56
        if raw_spd >= 185: return 48
        if raw_spd >= 180: return 40
        if raw_spd >= 175: return 33
        if raw_spd >= 170: return 26
        if raw_spd >= 165: return 20
        if raw_spd >= 160: return 14
        if raw_spd >= 155: return 9
        if raw_spd >= 150: return 5
        if raw_spd >= 145: return 2
        return 0

    def calc_type_score(self, attrs: list[str]) -> float:
        """计算属性价值评分，联防端加权热门输出属性"""
        type_data = self.attr_matrix.get_type_score(attrs)

        # 热门输出属性（天梯主流精灵携带的技能属性）
        HOT_ATK_TYPES = {"一般", "翼", "水", "地", "机械", "火", "光", "恶魔", "冰", "电", "武"}

        # 用 attribute_matrix 的倍率数据计算加权防守
        normalized = [self.attr_matrix._normalize_attr(a) for a in attrs]

        hot_resisted = 0
        cold_resisted = 0
        hot_weak = 0
        cold_weak = 0

        for atk_a in self.attr_matrix.attr_names:
            # 计算该攻击属性对本宠物的实际倍率
            best_mult = 1.0
            for def_a in normalized:
                mult = self.attr_matrix.multipliers.get(atk_a, {}).get(def_a, 1.0)
                best_mult = min(best_mult, mult)  # 取最优（最低倍率）

            is_hot = atk_a in HOT_ATK_TYPES
            if best_mult == 0:  # 免疫/抵抗
                if is_hot:
                    hot_resisted += 1
                else:
                    cold_resisted += 1
            elif best_mult >= 2:  # 被克制
                if is_hot:
                    hot_weak += 1
                else:
                    cold_weak += 1

        # 加权防守分: 抵抗热门=大加分, 热门弱点多=大扣分
        weighted_defense = hot_resisted * 5 + cold_resisted * 1 - hot_weak * 4 - cold_weak * 1

        # 进攻: 保持原有逻辑 (克制面数量)
        offense = type_data["offense"]

        # 缩放到0-10范围 (供后续 /10*100 归一化)
        raw = offense * 0.5 + max(weighted_defense, -10) * 0.25
        return max(raw, 0)

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

        # 本系高威力技能加分（STAB ×1.25 让斩杀线大幅降低）
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

        # ========== v4 改进: 威力边际递减 + 高费惩罚 ==========
        # 边际递减: 0-80威力=100%, 81-120威力=60%, 121+=30%
        if max_power <= 80:
            power_base = max_power / 3
        elif max_power <= 120:
            power_base = 80/3 + (max_power-80)*0.6/3
        else:
            power_base = 80/3 + 40*0.6/3 + (max_power-120)*0.3/3

        # 高费惩罚: >5费每超1费扣2分
        high_cost_count = sum(1 for s in attack_skills if s.get("cost", 0) > 5)
        cost_penalty = high_cost_count * 2

        # 修正后的斩杀线奖励
        kill_bonus = 0
        if max_power >= 140:
            kill_bonus += 12  # 140+威力奖励降低
        elif max_power >= 120:
            kill_bonus += 10
        elif max_power >= 100:
            kill_bonus += 7

        # 本系奖励保持
        if attrs:
            stab_skills = [s for s in attack_skills if s.get("element") in attrs]
            if stab_skills:
                stab_max = max(s.get("power", 0) for s in stab_skills)
                if stab_max >= 100:
                    kill_bonus += 8
                elif stab_max >= 80:
                    kill_bonus += 5

        # ========== v4 改进: 状态技能评分支持 ==========
        status_skills = [s for s in all_skills if s.get("power", 0) == 0]
        status_score = 0
        for s in status_skills:
            desc = s.get("desc", "")
            # 印记(全队收益高)
            for mark in ["湿润印记", "光合印记", "棘刺印记", "龙噬印记", "星陨印记", "中毒印记", "风起印记", "减速印记", "蓄电印记", "降灵印记", "攻击印记"]:
                if mark in desc:
                    status_score += 8
                    break
            # 减伤
            if "减伤70%" in desc or "减伤80%" in desc:
                status_score += 8
            elif "减伤60%" in desc or "减伤50%" in desc:
                status_score += 5
            elif "减伤" in desc:
                status_score += 4
            # 强化增益
            buff_match = re.search(r"(?:物攻|魔攻|双攻)\+(\d+)%", desc)
            if buff_match:
                status_score += min(int(buff_match.group(1)) / 12, 10)
            if re.search(r"速度\+(\d+)%", desc):
                status_score += 6
            if "翻倍增益" in desc:
                status_score += 6
            # 控制
            for kw in ["睡眠", "麻痹", "冰冻", "冻结", "眩晕", "混乱"]:
                if kw in desc:
                    status_score += 3
                    break
            # 迅捷/先手
            if "迅捷" in desc:
                status_score += 4

        total = max(0, power_base - cost_penalty) + diversity_score + control_score + kill_bonus + status_score

        return {
            "score": total,
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

        # 根据种族值+技能池综合判断主力攻击类型（物攻/特攻）
        atk_type = self._determine_attack_type(pet.get("skills", {}), pet.get("stats"))
        stats = pet.get("stats", {})
        if atk_type == "physical":
            useful_atk = stats.get("atk", 0)
            bad_atk = stats.get("matk", 0)
        else:
            useful_atk = stats.get("matk", 0)
            bad_atk = stats.get("atk", 0)
        # 错误类型攻击的收益比率（物攻手用特攻技能的打折比例）
        mismatch_ratio = bad_atk / max(useful_atk, 1)

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
            is_mismatched_atk = False  # 攻击类型与种族值不匹配

            if power > 0:
                # 攻击技能：威力/能耗比为核心
                score = (power / max(cost, 0.5)) * 0.6
                # 属性与本系一致加分
                if s.get("element") in pet["attrs"]:
                    score += 10
                # 攻击类型与种族值不匹配 → 按比例打折
                if atk_type == "physical" and category == "魔法":
                    is_mismatched_atk = True
                elif atk_type == "special" and category == "物理":
                    is_mismatched_atk = True
                if is_mismatched_atk:
                    score *= mismatch_ratio
            else:
                # 变化技能：看描述价值
                score = 0
                # 防御技能有基础分
                if "防御" in s.get("name", "") or "守护" in s.get("name", ""):
                    score += 5
                # 增益与攻击类型不匹配标记
                if atk_type == "special" and "物攻" in desc and "魔攻" not in desc and "双攻" not in desc:
                    is_mismatched_atk = True
                elif atk_type == "physical" and "魔攻" in desc and "物攻" not in desc and "双攻" not in desc:
                    is_mismatched_atk = True

            # 控制效果加分
            for kw, val in control_kw.items():
                if kw in desc:
                    score += val
                    break  # 只计最高项

            # 增益效果加分
            for kw, val in buff_kw.items():
                if kw in desc:
                    if is_mismatched_atk and kw in ("物攻+", "魔攻+"):
                        score += val * mismatch_ratio
                    else:
                        score += val

            # 减益效果加分
            if "降低" in desc or "削弱" in desc or "减少" in desc:
                score += 4

            # 变化技能：总体攻击类型错配打折
            if power == 0 and is_mismatched_atk:
                score *= 0.3

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

    def _get_nature_name(self, role: str, atk_type: str, spd: int) -> str:
        """获取推荐性格名称"""
        if role == "wall":
            return "沉默 (+HP -魔攻)" if atk_type == "physical" else "平和 (+HP -物攻)"
        elif role == "sweeper":
            if spd >= 115:
                return "开朗 (+速度 -魔攻)" if atk_type == "physical" else "胆小 (+速度 -物攻)"
            else:
                return "固执 (+物攻 -魔攻)" if atk_type == "physical" else "聪明 (+魔攻 -物攻)"
        else:
            # balanced: 默认中速输出手性格
            return "固执 (+物攻 -魔攻)" if atk_type == "physical" else "聪明 (+魔攻 -物攻)"

    def _compute_capabilities(self, pet: dict, actual_stats: dict, atk_type: str) -> dict:
        """计算精灵的进攻/防御/速度面板能力"""
        skills = pet.get("skills", {})
        all_skills = skills.get("learnset", []) + skills.get("recommended", [])
        trait_desc = pet.get("trait", {}).get("desc", "")
        pet_name = pet.get("name", "")

        # === 进攻能力 ===
        atk_stat = actual_stats["atk"] if atk_type == "physical" else actual_stats["matk"]
        # 找威力最高的攻击技能
        best_skill = None
        best_power = 0
        for sk in all_skills:
            pwr = sk.get("power", 0)
            cat = sk.get("category", "")
            # 优先同类型技能，其次看威力
            if pwr > 0:
                type_match = (cat == "物理" and atk_type == "physical") or (cat == "魔法" and atk_type == "special")
                if type_match and pwr > best_power:
                    best_power = pwr
                    best_skill = sk
        # 补位：如果没同类型技能，取最高威力
        if not best_skill:
            for sk in all_skills:
                if sk.get("power", 0) > best_power:
                    best_power = sk.get("power", 0)
                    best_skill = sk

        # === 防御能力 ===
        phys_def = actual_stats["def"]
        spec_def = actual_stats["mdef"]
        hp = actual_stats["hp"]

        # === 速度能力 ===
        base_spd = actual_stats["spd"]
        spd_bonus = 0
        spd_source = []

        # 技能加速
        skill_names = {sk.get("name", "") for sk in all_skills}
        if "啮合传递" in skill_names:
            spd_bonus += 80
            spd_source.append("啮合传递 +80")
        if "折射" in skill_names:
            spd_bonus += 50
            spd_source.append("折射 +50")

        # 特性加速（触发一次）
        if pet_name == "黑猫巫师":
            spd_bonus += 50
            spd_source.append("特性触发1次 +50")
        if pet_name == "绒光优优":
            spd_bonus += 50
            spd_source.append("特性触发1次 +50")

        return {
            "offense": {
                "atk_stat": atk_stat,
                "atk_type": atk_type,
                "best_skill_name": best_skill["name"] if best_skill else "—",
                "best_skill_power": best_power,
                "best_skill_element": best_skill.get("element", "") if best_skill else "",
                "effective_damage": atk_stat * best_power if best_skill else 0,
            },
            "defense": {
                "hp": hp,
                "phys_def": phys_def,
                "spec_def": spec_def,
                "phys_bulk": round(hp * phys_def / 200, 1),
                "spec_bulk": round(hp * spec_def / 200, 1),
            },
            "speed": {
                "base": base_spd,
                "bonus": spd_bonus,
                "total": base_spd + spd_bonus,
                "sources": spd_source,
            },
        }

    def score_pet(self, pet_id: str) -> dict:
        """计算单只宠物的综合评分"""
        pet = self.pets.get(pet_id)
        if not pet:
            return {"error": "Pet not found"}

        # 定位和攻击类型
        role = self.detect_role(pet["stats"])
        atk_type = self._determine_attack_type(pet.get("skills", {}), pet.get("stats"))
        actual_stats = self._apply_nature(pet["stats"], role, atk_type)

        # 各项评分
        stats_raw = self.calc_stats_score(pet["stats"], role, atk_type)
        type_raw = self.calc_type_score(pet["attrs"])
        feature_raw = self.calc_feature_score(pet["trait"])
        skill_data = self.calc_skill_score(pet["skills"], pet["attrs"])

        # 归一化到0-100
        stats_score = min(stats_raw / 150 * 100, 100)
        type_score = min(type_raw / 10 * 100, 100)
        feature_score = min(feature_raw / 100 * 100, 100)
        skill_score = min(skill_data["score"] / 2.0, 100)

        # 综合评分
        speed_bonus = self.calc_speed_bonus(pet["stats"]["spd"])
        total_score = (
            stats_score * self.weights["stats"] +
            type_score * self.weights["type"] +
            feature_score * self.weights["feature"] +
            skill_score * self.weights["skill"] +
            speed_bonus * self.weights["speed_bonus"]
        )

        debuff_info = self.get_debuff_info(pet["trait"])
        nature_name = self._get_nature_name(role, atk_type, pet["stats"]["spd"])
        capabilities = self._compute_capabilities(pet, actual_stats, atk_type)

        return {
            "id": pet_id,
            "name": pet["name"],
            "role": role,
            "atk_type": atk_type,
            "nature": nature_name,
            "capabilities": capabilities,
            "attrs": pet["attrs"],
            "stats": pet["stats"],
            "trait": pet["trait"],
            "actual_stats": {k: v for k, v in actual_stats.items()},
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
        """获取所有宠物的排行榜，同一家族多形态同分时合并展示"""
        from . import get_family_map, get_family_members

        family_map = get_family_map()
        family_members_map = get_family_members()

        # 先评分所有精灵
        all_results = {}
        for pet_id in self.pets.keys():
            result = self.score_pet(pet_id)
            if "error" not in result:
                all_results[pet_id] = result

        # 按家族（noText）分组
        families = {}
        for pet_id, result in all_results.items():
            no = family_map.get(pet_id, pet_id)
            if no not in families:
                families[no] = []
            families[no].append(result)

        # 每个家族取最高分，同分合并
        rankings = []
        for no, members in families.items():
            members.sort(key=lambda x: -x["scores"]["total"])
            best_score = members[0]["scores"]["total"]

            # 找出所有同分的形态
            tied = [m for m in members if abs(m["scores"]["total"] - best_score) < 0.01]

            if len(tied) > 1:
                # 多形态同分：合并展示，使用公共前缀作为名称
                names = [m["name"] for m in tied]
                import os
                prefix = os.path.commonprefix(names)
                # 从公共前缀末尾往回找括号，截断到括号前
                cut = prefix.rfind("（")
                if cut == -1:
                    cut = prefix.rfind("(")
                if cut > 1:
                    prefix = prefix[:cut]
                elif len(prefix) < 2:
                    prefix = min(names, key=len)
                merged = dict(tied[0])  # 以第一个的数据为基础
                merged["name"] = prefix
                merged["merged_forms"] = names
                merged["scores"] = dict(tied[0]["scores"])
                rankings.append(merged)
            else:
                rankings.append(members[0])

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
