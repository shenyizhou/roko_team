#!/usr/bin/env python3
"""
特性量化评分 + 精灵重排 + 最优队伍组建

特性评分原则: 与技能评分同源，按效果量化
"""

import json, re
from pathlib import Path
from itertools import combinations

DATA_DIR = Path(__file__).parent.parent / "data"

# ===== 属性 × 种族 综合战斗能力评分 =====
_ATTR_CHART = None
_SHORT_MAP = {"幽": "幽灵", "恶": "恶魔", "普通": "一般"}

def _load_chart():
    global _ATTR_CHART
    if _ATTR_CHART is None:
        with open(DATA_DIR / "attribute_chart.json") as f:
            _ATTR_CHART = json.load(f)
    return _ATTR_CHART

def _norm(a):
    return _SHORT_MAP.get(a, a)

IV = 10  # 个体值 (7~10，取10)

def base_to_actual(base_stat, nature_mod=0.0, is_hp=False):
    """
    生命: (1.7*(种族 + 3*个体) + 70) × (1 + 性格修正) + 100
    其他: (1.1*(种族 + 3*个体) + 10) × (1 + 性格修正) + 50
    计算细则：系数×种族部分先四舍五入，整体算完再四舍五入一次
    """
    if is_hp:
        step1 = round(1.7 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 70) * (1 + nature_mod) + 100
        return round(raw)  # 第二次四舍五入
    else:
        step1 = round(1.1 * (base_stat + 3 * IV))  # 第一次四舍五入
        raw = (step1 + 10) * (1 + nature_mod) + 50
        return round(raw)  # 第二次四舍五入

# 攻击属性热门度分级
_HOT_T1 = {"一般", "翼", "水", "地", "机械", "火"}
_HOT_T2 = {"光", "恶魔", "冰", "电", "武"}

def _atk_weight(atk_type):
    if atk_type in _HOT_T1: return 2.0
    if atk_type in _HOT_T2: return 1.5
    return 1.0

def calc_combat_score(attrs, stats, rec_skills=None):
    """
    属性×种族值 综合战斗能力评分：攻击能力 + 防御能力 + 速度线
    攻击分基于伤害公式：技能威力 × 对应攻击力 × 本系加成
    返回 (total, breakdown)
    """
    if not attrs:
        return 0, {"attack": 0, "defense": 0, "speed": 0}

    chart = _load_chart()
    normalized = [_norm(a) for a in attrs]

    # ---- 属性 matchup 计算 ----
    atk_vs = {}
    for e in chart["attributes"]:
        atk_n = e["nameCn"]
        atk_vs[atk_n] = {}
        for ms, targets in e["battleMultiplier"]["offense"].items():
            for t in targets:
                atk_vs[atk_n][t] = float(ms)

    # 属性联防面 (0~55)
    defense_raw = 15.0
    for atk_t in atk_vs:
        best_m = 2.0
        for def_t in normalized:
            m = atk_vs[atk_t].get(def_t, 1.0)
            best_m = min(best_m, m)
        w = _atk_weight(atk_t)
        if best_m <= 0.5:
            defense_raw += 3.5 * w   # 抵抗
        elif best_m >= 2.0:
            defense_raw -= 3.0 * w   # 弱点
    type_defense = max(defense_raw * 0.75, 0)

    # 属性打击面：精灵属性 + 携带技能的属性（技能打击面）
    offense_attrs = set(normalized)
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power > 0:
                offense_attrs.add(sk.get("element", "普通") if isinstance(sk, dict) else "普通")
    stab_se = set()
    stab_immune = set()
    for atk_t in offense_attrs:
        for def_t, m in atk_vs.get(atk_t, {}).items():
            if m >= 2:
                stab_se.add(def_t)
            elif m == 0:
                stab_immune.add(def_t)
    can_hit = 18 - len(stab_immune)
    type_offense = max(can_hit * 1.6 + len(stab_se) * 1.2 - len(stab_immune) * 2.0 + 3, 0)

    # 属性协同 (0~25)
    if len(normalized) == 2:
        t1, t2 = normalized
        weak, resist = {}, {}
        for t in [t1, t2]:
            wset, rset = set(), set()
            for atk_t in atk_vs:
                m = atk_vs[atk_t].get(t, 1.0)
                if m >= 2: wset.add(atk_t)
                elif m <= 0.5: rset.add(atk_t)
            weak[t] = wset; resist[t] = rset
        covered = set(); dup = set()
        for w in weak[t1]:
            if w in resist[t2]: covered.add(w)
            elif w in weak[t2]: dup.add(w)
        for w in weak[t2]:
            if w in resist[t1]: covered.add(w)
            elif w in weak[t1]: dup.add(w)
        type_synergy = len(covered) * 5.5 - len(dup) * 4.0
    else:
        type_synergy = 3

    # ---- 种族值维度 ----
    hp = stats.get("hp", 80)
    atk = stats.get("atk", 80)
    matk = stats.get("matk", 80)
    def_ = stats.get("def", 80)
    mdef = stats.get("mdef", 80)
    spd = stats.get("spd", 80)

    # 计算实际速度值（默认+20%性格修正，对应高速输出手）
    actual_spd = base_to_actual(spd, 0.2)

    # 1. 攻击能力: 取最强单技能伤害 = (实际攻击 ÷ 目标防御) × 威力 × 本系
    # 只看最强输出技能的能力，不是所有技能的总和
    actual_atk = base_to_actual(atk, 0.2)
    actual_matk = base_to_actual(matk, 0.2)
    TARGET_DEF = 200  # 标准目标防御值
    SCALE = 0.35  # 缩放到与防御分同量级

    phys_best = 0.0
    spec_best = 0.0
    if rec_skills:
        for sk in rec_skills:
            power = sk.get("power", 0) if isinstance(sk, dict) else 0
            if power <= 0:
                continue
            category = sk.get("category", "") if isinstance(sk, dict) else ""
            element = sk.get("element", "普通") if isinstance(sk, dict) else "普通"
            stab = 1.5 if element in attrs else 1.0
            # 伤害 = 实际攻击 ÷ 标准防御 × 威力 × 本系
            damage = (actual_atk if "物理" in category else actual_matk) / TARGET_DEF * power * stab
            if "物理" in category:
                phys_best = max(phys_best, damage)
            else:
                spec_best = max(spec_best, damage)

    phys_atk_score = round(phys_best * SCALE, 1)
    spec_atk_score = round(spec_best * SCALE, 1)
    # 取物攻和特攻中更高的，代表精灵的最高输出能力
    atk_stat = max(phys_atk_score, spec_atk_score)
    # 攻击分 = 种族攻击 + 技能打击面
    type_atk = type_offense * 0.45
    attack_score = round(atk_stat + type_atk, 1)

    # 2. 防御能力: 基于伤害公式，拆分为物防/特防分（纯种族值）
    # 伤害 ∝ 攻击÷防御，能承受的总伤害 ∝ HP×防御
    # 物攻环境主导 (65%物理，35%特殊)
    scale = 180
    phys_bulk = hp * def_ / scale
    spec_bulk = hp * mdef / scale
    phys_def_score = round(phys_bulk * 0.65, 1)
    spec_def_score = round(spec_bulk * 0.35, 1)
    defense_score = round(phys_def_score + spec_def_score, 1)  # 纯种族防御分

    # 3a. 属性得分：联防面 + 协同（属性组合的抗性价值）
    type_def = type_defense * 0.6
    type_syn = max(type_synergy, 0)  # 协同不惩罚（弱协同=0分）
    attr_score = round(type_def + type_syn, 1)

    # 3b. 速度分：连续量化计算 - 低于100基础为0，超过后抛物线增长
    # 公式：speed_score = max(spd - 98, 0) ^ 1.5 * 0.32
    if spd >= 100:
        speed_score = round(pow(max(spd - 98, 0), 1.5) * 0.32, 1)
        speed_line = spd
    else:
        speed_score = 0
        speed_line = 0

    # 无权重：直接相加，一视同仁
    total = round(attack_score + defense_score + speed_score + attr_score, 1)
    return total, {
        "attack": attack_score,
        "phys_atk": round(phys_atk_score, 1),
        "spec_atk": round(spec_atk_score, 1),
        "defense": defense_score,
        "phys_def": phys_def_score,
        "spec_def": spec_def_score,
        "speed": speed_score,
        "speed_line": speed_line,
        "actual_spd": actual_spd,
        "attr_score": attr_score,
        "type_atk": round(type_atk, 1),
        "type_def": round(type_def, 1),
        "type_syn": round(type_syn, 1),
    }


def _find_int(pattern, text, default=0):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


def _cond_mult(desc):
    """返回条件折扣系数：硬条件(天气/环境/队友)折扣最大，软条件(自身行动)中等"""
    if not desc:
        return 1.0
    # 硬条件: 需要天气/环境/队友支持/击败触发
    if re.search(r"天气|雨天|晴天|沙暴|暴风雪|环境|水系环境|己方队伍|队友|队伍中|主动击败", desc):
        return 0.35
    # 中等条件: 需要自身行动触发（应对/击败/入场/离场/每携带等）
    if re.search(r"应对成功|主动击败|入场|离场|每携带\d|每拥有|每回合|受到|若", desc):
        return 0.55
    # 轻微条件: 力竭(几乎必然发生)、更换等
    if re.search(r"力竭|更换", desc):
        return 0.85
    return 1.0


def score_trait(trait_name, desc):
    """
    量化特性价值，返回 (score, details)
    评分与技能体系对齐，单位等效
    """
    score = 0
    pts = {}

    cm = _cond_mult(desc)  # 统一条件折扣系数

    # === 1. 威力提升 ===
    pw = _find_int(r"威力\+(\d+)%", desc) or _find_int(r"威力\+(\d+)", desc)
    if pw == 0:
        pw = _find_int(r"威力\+(\d+)", desc)
    if pw > 0:
        v = round(min(pw * 0.2 * cm, 18), 1)
        pts["威力+"] = v; score += v

    # 全技能威力+
    apw = _find_int(r"全技能威力\+(\d+)", desc) or _find_int(r"全技能威力永久\+(\d+)", desc)
    if apw > 0:
        v = round(apw * 0.25 * cm, 1)
        pts["全技能威力+"] = v; score += v

    # === 2. 能耗操作 ===
    cost_down = _find_int(r"能耗-(\d+)", desc) or _find_int(r"能耗永久-(\d+)", desc)
    if cost_down > 0:
        scope = "全技能" if "全技能" in desc else "技能"
        v = round(cost_down * (6 if "全技能" in desc else 4) * cm, 1)
        pts[f"{scope}能耗-"] = v; score += v

    # 携带技能能耗减少
    if "携带" in desc:
        cd = _find_int(r"能耗-(\d+)", desc)
        if cd > 0:
            v = round(cd * 5 * cm, 1)
            pts["携带能耗-"] = v; score += v

    # 敌方能耗增加
    enemy_cost = _find_int(r"能耗\+(\d+)", desc)
    if enemy_cost > 0 and "敌方" in desc:
        v = round(enemy_cost * 3 * cm, 1)
        pts["敌能耗+"] = v; score += v

    # === 3. 能量/生命操作 ===
    energy = _find_int(r"回复(\d+)能量", desc)
    if energy > 0:
        v = round(min(energy * 1.5, 15) * cm, 1)
        pts["回能"] = v; score += v

    # 偷取敌方能量
    steal_e = _find_int(r"偷取.*?(\d+)能量", desc) or _find_int(r"失去(\d+)能量", desc)
    if steal_e > 0 and "敌方" in desc:
        v = round(steal_e * 2 * cm, 1)
        pts["偷能量"] = v; score += v

    # 回血
    heal_pct = _find_int(r"回复(\d+)%生命", desc)
    if heal_pct > 0:
        v = round(heal_pct * 0.08 * cm, 1)
        pts["回血"] = v; score += v

    # 吸血
    leech = _find_int(r"(\d+)%吸血", desc)
    if leech > 0:
        v = round((6 if leech >= 50 else 4) * cm, 1)
        pts["吸血"] = v; score += v

    # === 4. 属性强化 ===
    atk = _find_int(r"双攻\+(\d+)%", desc) or _find_int(r"物攻\+(\d+)%", desc) or _find_int(r"魔攻\+(\d+)%", desc)
    if atk > 0:
        v = round(atk / 10 * 3.5 * cm, 1)
        pts["攻+%"] = v; score += v

    # 双防+
    df = _find_int(r"双防\+(\d+)%", desc) or _find_int(r"物防\+(\d+)%", desc) or _find_int(r"魔防\+(\d+)%", desc)
    if df > 0:
        v = round(df / 10 * 2.3 * cm, 1)
        pts["防+%"] = v; score += v

    # 速度+
    spd = _find_int(r"速度\+(\d+)", desc) or _find_int(r"速度永久-(\d+)", desc)
    if spd > 0:
        v = round(spd / 10 * 2.0, 1)
        pts["速+"] = v; score += v

    # === 5. 状态/印记 ===
    poison = _find_int(r"(\d+)层中毒", desc)
    if poison > 0 and "获得" in desc:
        v = round(poison * 4 * cm, 1)
        pts["中毒层"] = v; score += v
    if "中毒" in desc and "触发次数" in desc:
        v = round(8 * cm, 1)
        pts["中毒触发+"] = v; score += v

    burn = _find_int(r"(\d+)层灼烧", desc)
    if burn > 0:
        v = round(burn * 3 * cm, 1)
        pts["灼烧层"] = v; score += v

    freeze = _find_int(r"(\d+)层冻结", desc)
    if freeze > 0:
        v = round(freeze * 4 * cm, 1)
        pts["冻结层"] = v; score += v

    star_mark = _find_int(r"(\d+)层星陨", desc)
    if star_mark > 0:
        v = round(star_mark * 6 * cm, 1)
        pts["星陨印记"] = v; score += v

    thorn = _find_int(r"(\d+)层棘刺", desc)
    if thorn > 0:
        v = round(8 * cm, 1)
        pts["棘刺印记"] = v; score += v

    # 印记偷取
    if "偷取" in desc and "印记" in desc:
        v = 10
        pts["偷印记"] = v; score += v

    # === 6. 迅捷/先手 ===
    if "迅捷" in desc:
        # 条件性迅捷减分
        if "获得迅捷" in desc:
            v = 14
        elif "携带" in desc and "获得迅捷" in desc:
            v = 12
        else:
            v = 10
        pts["迅捷"] = v; score += v

    # 先手+
    prior = _find_int(r"先手\+(\d+)", desc)
    if prior > 0:
        v = prior * 6
        pts["先手+"] = v; score += v

    # === 7. 入场/离场效果 ===
    if "入场" in desc:
        pts["入场效果"] = 3; score += 3
    if "离场" in desc and "更换" in desc:
        pts["离场增益"] = 5; score += 5
    if "脱离" in desc:
        pts["脱离"] = 8; score += 8

    # === 8. 应对效果 — 主体效果已由对应段计算，此处仅给应对触发奖励
    if "应对成功" in desc:
        if "威力翻倍" in desc:
            v = 10  # 威力翻倍=独特应对机制
            pts["应对威力翻倍"] = v; score += v
        elif "技能能耗" in desc:
            v = 3  # 主体已由能耗段用cm折扣计算
            pts["应对触发"] = v; score += v
        elif "回复" in desc:
            v = _find_int(r"回复(\d+)%生命", desc) * 0.05
            pts["应对回血"] = round(v, 1); score += v
        elif "全技能威力" in desc:
            v = 4  # 主体已由威力段用cm折扣计算
            pts["应对触发"] = v; score += v

    # === 9. 连击/奉献 ===
    combo = _find_int(r"连击数\+(\d+)", desc)
    if combo > 0:
        v = combo * 4
        pts["连击数+"] = v; score += v
    if "奉献" in desc:
        n = _find_int(r"(\d+)次随机奉献", desc) or _find_int(r"(\d+)次奉献", desc) or 1
        # 奉献是虫系专属机制，给队友的奉献只有虫系队友能受益
        # 个体评估时折半（默认只有一半队友受益）
        v = round(n * 6 * cm, 1)
        if "队伍" in desc or "己方" in desc:
            v = round(v * 0.4, 1)  # 给队伍的奉献，非虫系队友无法受益
        pts["奉献"] = v; score += v

    # === 10. 特殊机制 ===
    # 迸发效果
    if "迸发" in desc and "威力" in desc:
        pw = _find_int(r"威力\+(\d+)", desc)
        v = pw * 0.15
        pts["迸发威力"] = round(v, 1); score += v

    # 迸发能耗
    if "迸发" in desc and "能耗" in desc:
        v = 6
        pts["迸发能耗-"] = v; score += v

    # 反伤
    reflect = _find_int(r"造成(\d+)威力", desc)
    if reflect > 0 and ("受到" in desc or "每受到" in desc):
        v = round(reflect / 10 * 1.5, 1)
        pts["反伤"] = v; score += v

    # 伤害减免
    dmg_reduce = _find_int(r"伤害-(\d+)%", desc)
    if dmg_reduce > 0 or "伤害-50%" in desc:
        v = 10
        pts["减伤"] = v; score += v

    # 继承增益/减益
    if "继承" in desc or ("更换" in desc and "增益" in desc):
        v = 8
        pts["继承"] = v; score += v

    # 萌化相关
    if "萌化" in desc and ("不受限制" in desc or "层数" in desc):
        v = 6
        pts["萌化增强"] = v; score += v

    # 蓄力相关
    if "蓄力" in desc and "能耗" in desc:
        v = 8
        pts["蓄力能耗-"] = v; score += v

    # 印记共存 (如里拉鳐: 赋予的印记不会替换，同时生效)
    if "印记" in desc and ("不会替换" in desc or "同时生效" in desc):
        v = 10
        pts["印记共存"] = v; score += v

    # 复活机制 — 游戏中最强特性之一，价值极高
    if "复活" in desc:
        v = 35  # 第二条命，战略价值极高
        # 力竭N回合后复活：可预测、可规划，更强
        if "力竭" in desc and "回合后复活" in desc:
            v += 10
        pts["复活"] = v; score += v

    # 免死/保留1血
    if "保留1生命" in desc or "免疫此次伤害" in desc:
        v = 22
        pts["免死"] = v; score += v

    # 能力上限突破 (能量超过上限、萌化层数不受限)
    if "不受限制" in desc or "超过" in desc and ("上限" in desc or "能量" in desc):
        v = 8
        pts["上限突破"] = v; score += v

    # 打断敌方冷却
    if "打断" in desc and "冷却" in desc:
        v = 10
        pts["打断冷却"] = v; score += v

    # 印记效率 (星陨消耗一半仍满伤)
    if "星陨" in desc and "仅消耗一半" in desc:
        v = 12
        pts["星陨效率"] = v; score += v

    # 增益翻倍/额外层数
    if ("增益" in desc and "额外" in desc and "层数" in desc) or ("翻倍" in desc and "增益" in desc):
        v = 10
        pts["增益翻倍"] = v; score += v

    # 敌方离场效果 (通用)
    if "敌方精灵离场" in desc and "更换" in desc and "失去" in desc:
        e = _find_int(r"失去(\d+)能量", desc) or _find_int(r"(\d+)层中毒", desc)
        v = max(e * 2, 6)
        pts["离场惩罚"] = v; score += v

    # 中毒转化印记 (毒→印记)
    if "中毒转化为" in desc and "印记" in desc:
        v = 10
        pts["毒转印记"] = v; score += v

    # 灼烧衰减变增长
    if "灼烧" in desc and "衰减变为增长" in desc:
        v = 12
        pts["灼烧逆转"] = v; score += v

    # 替换精灵 (队友空能量时自动替换)
    if "替换" in desc and "能量等于0" in desc:
        v = 8
        pts["自动替换"] = v; score += v

    # 蓄力效果转化
    if "蓄力" in desc and "变为" in desc:
        v = 8
        pts["蓄力转化"] = v; score += v

    # 携带技能系别计数加成 (每携带1个X系技能...)
    m = re.search(r"每携带1个(.{1,3})系技能", desc)
    if m:
        v = 6  # 保守估计携带2-3个同系技能
        pts["系别加成"] = v; score += v

    # 种族值大幅增加 (正面)
    if "种族值大幅增加" in desc or "大幅提升种族值" in desc:
        v = 12
        pts["种族值+"] = v; score += v

    # === 11. 负面评分 ===
    # 种族值操作 (大幅增加=积极，但额外损失魔力=消极)
    if "力竭" in desc and "额外损失" in desc:
        v = -8
        pts["力竭惩罚"] = v; score += v
    if "额外损失" in desc and "魔力" in desc:
        v = -6
        pts["额外魔力损失"] = v; score += v
    if "失去自己一半" in desc:
        v = -6
        pts["自伤"] = v; score += v
    if "额外扣除4点魔力" in desc or "扣除4点魔力" in desc:
        v = -12
        pts["高魔力惩罚"] = v; score += v

    # 技能位限制（圣剑系列）- 暂不扣分
    # if "仅可以使用1号位技能" in desc and "3号" not in desc:
    #     v = -18
    #     pts["单技能位限制"] = v; score += v
    # if "仅可使用1号和3号位技能" in desc:
    #     v = -8
    #     pts["双技能位限制"] = v; score += v

    return round(score, 1), pts


def score_pet(pet_name, pet_data, learnset_skills, rec_skills, skill_scores, boss_bonus=0):
    """
    精灵综合评分 = 推荐技能总分 + 特性分
    boss_bonus: 首领化加成（先提升种族值，再计算战斗能力）
    """
    # 技能分 (推荐配置)
    skill_total = 0
    if rec_skills:
        for sk in rec_skills:
            sk_name = sk["name"] if isinstance(sk, dict) else sk
            skill_total += skill_scores.get(sk_name, 0)

    # 特性分
    trait = pet_data.get("trait", {})
    trait_score, trait_pts = score_trait(trait.get("name", ""), trait.get("desc", ""))

    # 属性×种族 综合战斗能力评分: 攻击能力 + 防御能力 + 速度线
    attrs = pet_data.get("attrs", [])
    stats = dict(pet_data.get("stats", {}))  # copy to avoid modifying original

    # 特性对种族的直接影响：先于 combat_score 计算生效
    # 失去一半生命 → 有效HP减半（防御力折半）
    trait_desc = trait.get("desc", "")
    if "失去自己一半" in trait_desc or "失去一半" in trait_desc:
        stats['hp'] = stats['hp'] // 2

    # 首领化：先按比例提升种族值（boss_bonus 换算为种族提升百分比）
    # boss_bonus 每 10 分 ≈ 种族值整体提升约 10%
    if boss_bonus > 0:
        race_boost = 1 + (boss_bonus / 100)  # 20分 ≈ +20% 种族
        for k in stats:
            if k != 'total':
                stats[k] = int(stats[k] * race_boost)

    attr_bonus, attr_detail = calc_combat_score(attrs, stats, rec_skills)

    # 权重: 特性得分暂置0（仅保留负面惩罚），属性种族合并为战斗能力
    trait_positive = max(trait_score, 0)
    # 首领化不再重复加分（已通过种族提升体现在战斗能力中）
    total = round(skill_total + (trait_score - trait_positive) * 4 + attr_bonus, 1)

    return total, {
        "skill_score": round(skill_total, 1),
        "trait_score": trait_score,
        "trait_pts": trait_pts,
        "combat_score": attr_bonus,
        "combat_detail": attr_detail,
        "boss_bonus": boss_bonus,
        "trait_name": trait.get("name", ""),
        "trait_desc": trait.get("desc", ""),
        "attrs": attrs,
        "stats": stats,
    }


def main():
    # Load data
    with open(DATA_DIR / "pets.json") as f:
        pets = json.load(f)
    with open(DATA_DIR / "pet_learnset.json") as f:
        learnsets = json.load(f)
    with open(DATA_DIR / "pet_recommended.json") as f:
        recommended = json.load(f)
    with open(DATA_DIR / "all_skill_rankings.json") as f:
        rankings = json.load(f)

    skill_scores = {s['name']: s['score'] for s in rankings}

    # Load boss info for 首领化 bonuses
    boss_info = {}
    try:
        with open(DATA_DIR / "_boss_info.json") as f:
            boss_info = json.load(f)
    except Exception:
        pass
    # Build set of pets to remove (intermediate/boss duplicates)
    removed_pets = {n for n, bi in boss_info.items() if bi.get('remove')}

    # Score all pets
    pet_scores = {}
    for name, pet in pets.items():
        if name in removed_pets:
            continue
        if name not in learnsets:
            continue
        bb = boss_info.get(name, {}).get('bonus', 0)
        score, meta = score_pet(
            name, pet, learnsets.get(name, []),
            recommended.get(name, []), skill_scores, bb
        )
        rec_skills = [sk["name"] for sk in recommended.get(name, [])]
        pet_scores[name] = {
            "id": name,
            "name": name,
            "score": score,
            "attrs": meta["attrs"],
            "stats": meta["stats"],
            "trait_name": meta["trait_name"],
            "trait_desc": meta["trait_desc"],
            "trait_score": meta["trait_score"],
            "trait_pts": meta["trait_pts"],
            "skill_score": meta["skill_score"],
            "combat_score": meta["combat_score"],
            "combat_detail": meta["combat_detail"],
            "boss_bonus": meta["boss_bonus"],
            "recommended_skills": rec_skills,
        }

    # Sort by score
    ranked = sorted(pet_scores.values(), key=lambda x: -x["score"])

    # 去重：棋家族的黑子和白子完全一样，只保留一个（白子）
    seen = set()
    unique_ranked = []
    for p in ranked:
        name = p["name"]
        # 提取基础名称（去掉"（白子）"或"（黑子）"后缀）
        base_name = name.replace("（白子）", "").replace("（黑子）", "")
        if base_name in seen:
            continue
        seen.add(base_name)
        unique_ranked.append(p)
    ranked = unique_ranked

    # Save rankings
    with open(DATA_DIR / "all_pet_rankings.json", "w") as f:
        json.dump(ranked, f, ensure_ascii=False, indent=2)

    # Print top 30
    print("=" * 90)
    print("精灵综合排名 (技能 + 战斗能力[攻防速])")
    print("=" * 90)
    def _format_combat(cd, combat_score, actual_spd):
        parts = []
        pa = cd.get('phys_atk', cd.get('attack', 0))
        sa = cd.get('spec_atk', 0)
        if pa > 0 and sa > 0:
            parts.append(f"物攻{pa:.0f}/特攻{sa:.0f}")
        elif pa > 0:
            parts.append(f"物攻{pa:.0f}")
        elif sa > 0:
            parts.append(f"特攻{sa:.0f}")
        pd = cd.get('phys_def', 0)
        sd = cd.get('spec_def', 0)
        parts.append(f"物防{pd:.0f}/特防{sd:.0f}")
        parts.append(f"速{cd.get('speed',0):.0f}/{actual_spd}")
        parts.append(f"属性{cd.get('attr_score',0):.0f}")
        return f"{combat_score} ({' '.join(parts)})"

    for i, p in enumerate(ranked[:30], 1):
        cd = p.get('combat_detail', {})
        actual_spd = cd.get('actual_spd', 0)
        combat_str = _format_combat(cd, p['combat_score'], actual_spd)
        boss_str = f" 首领化+{p['boss_bonus']:.0f}" if p.get('boss_bonus', 0) > 0 else ""
        trait_str = f" 负面={p['trait_score']:.0f}" if p['trait_score'] < 0 else ""
        sk_names = " · ".join(p.get("recommended_skills", [])[:4])
        print(f"{i:>2}. {p['name']:<12} {p['score']:>6.1f}  "
              f"(技能={p['skill_score']:.0f} [{sk_names}] {combat_str}{boss_str}{trait_str}) "
              f"【{p['trait_name']}】")

    # === 最优队伍组建 (体系协同版) ===
    print("\n" + "=" * 90)
    print("最优队伍组建 (体系协同搜索)")
    print("=" * 90)

    score_map = {p["name"]: p["score"] for p in ranked}

    def has_defense_skill(name):
        rec = recommended.get(name, [])
        return any("减伤" in sk["desc"] for sk in rec)

    def has_utility_skill(name):
        rec = recommended.get(name, [])
        return any(
            "驱散" in sk["desc"] or "印记" in sk["desc"] or "眩晕" in sk["desc"]
            for sk in rec
        )

    def get_attrs(name):
        return pets.get(name, {}).get("attrs", [])

    # 体系定义
    RAIN_SETTERS = [n for n in score_map if any(
        sk["name"] == "落雨" for sk in learnsets.get(n, [])
    )]
    RAIN_BENEFICIARY = "卷毛鸭"

    THUNDER_CORE = ["闪电鳗鱼", "星光狮"]
    SHADOW_CORE = ["影狸", "黑羽夫人"]

    # 体系包: (成员列表, 协同加分, 描述)
    SYNERGY_PACKAGES = []
    for rs in RAIN_SETTERS:
        if rs != RAIN_BENEFICIARY and RAIN_BENEFICIARY in score_map:
            SYNERGY_PACKAGES.append(([rs, RAIN_BENEFICIARY], 80, f"雨天体系({rs}+卷毛鸭)"))
    if all(m in score_map for m in THUNDER_CORE):
        SYNERGY_PACKAGES.append((THUNDER_CORE, 60, "雷暴体系(闪电鳗鱼+星光狮)"))
    if all(m in score_map for m in SHADOW_CORE):
        SYNERGY_PACKAGES.append((SHADOW_CORE, 50, "换场压制(影狸+黑羽夫人)"))

    def eval_team(members, synergy_bonus=0):
        """评估队伍质量: 总分+协同+约束+特性配合"""
        if len(members) != 6:
            return -9999, {}
        # 属性约束: 同属性最多2个
        attr_count = {}
        for m in members:
            for a in get_attrs(m):
                if attr_count.get(a, 0) >= 2:
                    return -9999, {}
                attr_count[a] = attr_count.get(a, 0) + 1

        base_score = sum(score_map.get(m, 0) for m in members)
        def_count = sum(1 for m in members if has_defense_skill(m))
        utl_count = sum(1 for m in members if has_utility_skill(m))
        all_attrs = set()
        for m in members:
            all_attrs.update(get_attrs(m))

        # 约束惩罚 + 特性团队配合检查
        penalty = 0
        if def_count < 3:
            penalty -= (3 - def_count) * 15

        # 特性需要队伍配合但队伍没有
        for m in members:
            trait_desc = pets.get(m, {}).get("trait", {}).get("desc", "")
            pet_attrs = get_attrs(m)
            # 奉献类特性：需要同系队友（虫系专属机制）
            if "奉献" in trait_desc:
                same_type_teammates = sum(
                    1 for other in members if other != m
                    and set(pet_attrs) & set(get_attrs(other))
                )
                if same_type_teammates == 0:
                    penalty -= 35  # 奉献没虫系队友=完全浪费
            # 击败触发类特性：条件苛刻
            if "主动击败" in trait_desc:
                penalty -= 12

        total = base_score + synergy_bonus + penalty

        # 首领化：一队只能触发一次，只计最高加分
        boss_bonuses = [pet_scores.get(m, {}).get('boss_bonus', 0) for m in members]
        max_boss = max(boss_bonuses) if boss_bonuses else 0
        # base_score 已包含全部首领化加分，减去多余的只保留最高
        excess_boss = sum(boss_bonuses) - max_boss
        total -= excess_boss

        return total, {
            "base": base_score, "synergy": synergy_bonus, "penalty": penalty,
            "defense": def_count, "utility": utl_count, "attr_coverage": len(all_attrs),
        }

    # 搜索最优队伍
    best_team = None
    best_info = None
    best_total = -9999

    top_pool = [p["name"] for p in ranked[:60]]  # 候选池

    def fill_team(core, bonus, existing_total=None):
        """贪心补位：逐个尝试候选人，用eval_team评估"""
        nonlocal best_team, best_info, best_total
        current = list(core)
        for _ in range(6 - len(core)):
            best_fill_score = -9999
            best_fill = None
            for cand in top_pool:
                if cand in current:
                    continue
                trial = current + [cand]
                if len(trial) < 6:
                    # 中间步骤只看属性不冲突
                    ac = {}
                    ok = True
                    for m in trial:
                        for a in get_attrs(m):
                            if ac.get(a, 0) >= 2:
                                ok = False; break
                        if not ok: break
                        ac[a] = ac.get(a, 0) + 1
                    if not ok:
                        continue
                    # 用个体分做启发式
                    fill_score = score_map.get(cand, 0)
                else:
                    total, info = eval_team(trial, bonus)
                    fill_score = total
                if fill_score > best_fill_score:
                    best_fill_score = fill_score
                    best_fill = cand
            if best_fill:
                current.append(best_fill)
        if len(current) == 6:
            total, info = eval_team(current, bonus)
            if total > best_total:
                best_total = total
                best_team = list(current)
                best_info = info

    # 策略0: 无体系（纯高分，贪心补位）
    fill_team([], 0)

    # 策略1: 单体系 + 贪心补位
    for pkg_members, pkg_bonus, pkg_desc in SYNERGY_PACKAGES:
        if len(pkg_members) > 6:
            continue
        fill_team(pkg_members, pkg_bonus)
        if best_info:
            best_info["desc"] = pkg_desc

    # 策略2: 双体系（不重叠）+ 贪心补位
    for i, (pkg1_m, pkg1_b, pkg1_d) in enumerate(SYNERGY_PACKAGES):
        for j, (pkg2_m, pkg2_b, pkg2_d) in enumerate(SYNERGY_PACKAGES):
            if i >= j:
                continue
            if set(pkg1_m) & set(pkg2_m):
                continue
            all_pkg = pkg1_m + pkg2_m
            if len(all_pkg) > 6:
                continue
            combined_bonus = pkg1_b + pkg2_b
            fill_team(all_pkg, combined_bonus)
            if best_info:
                best_info["desc"] = f"{pkg1_d} + {pkg2_d}"

    # === 体系技能修正：确保关键发动机技能被携带 ===
    def adjust_skills_for_synergy(team_members):
        """给体系成员换上关键发动机技能"""
        adjusted = {}
        for name in team_members:
            rec_sk = recommended.get(name, [])
            if not rec_sk:
                adjusted[name] = []
                continue
            learnset = {sk["name"]: sk for sk in learnsets.get(name, [])}

            # 雨天体系：如果该精灵能学落雨，强制换上
            if name in RAIN_SETTERS:
                has_rain = any(sk["name"] == "落雨" for sk in rec_sk)
                if not has_rain and "落雨" in learnset:
                    # 用落雨替换分数最低的非防御技能
                    scored = [(sk, skill_scores.get(sk["name"], 0)) for sk in rec_sk]
                    scored.sort(key=lambda x: x[1])
                    for old_sk, _ in scored:
                        if "减伤" not in old_sk.get("desc", ""):  # 保留防御
                            rec_sk = [learnset["落雨"] if s["name"] == old_sk["name"] else s for s in rec_sk]
                            break

            # 雷暴体系：如果该精灵能学雷暴，强制换上
            if name in THUNDER_CORE:
                has_thunder = any(sk["name"] == "雷暴" for sk in rec_sk)
                if not has_thunder and "雷暴" in learnset:
                    scored = [(sk, skill_scores.get(sk["name"], 0)) for sk in rec_sk]
                    scored.sort(key=lambda x: x[1])
                    for old_sk, _ in scored:
                        if "减伤" not in old_sk.get("desc", ""):
                            rec_sk = [learnset["雷暴"] if s["name"] == old_sk["name"] else s for s in rec_sk]
                            break

            adjusted[name] = rec_sk
        return adjusted

    team_skills = adjust_skills_for_synergy(best_team)

    # 输出
    desc = best_info.pop("desc", "无特定体系")
    print(f"体系: {desc}")
    print(f"队伍: {', '.join(best_team)}")
    print(f"总分: {best_total:.1f} (基础={best_info['base']:.0f} 协同=+{best_info['synergy']} 罚={best_info['penalty']})")
    print(f"防御: {best_info['defense']}/6 | 功能: {best_info['utility']}/6 | 属性覆盖: {best_info['attr_coverage']}种")
    print()
    for n in best_team:
        p = next(x for x in ranked if x["name"] == n)
        rec_sk = team_skills.get(n, [])
        sk_names = [sk["name"] for sk in rec_sk] if rec_sk else []
        print(f"  {n:<12} {p['score']:>5.1f}  {p['attrs']}  "
              f"【{p['trait_name']}】{p['trait_desc'][:45]}")
        print(f"    技能: {' · '.join(sk_names)}")
        print()

    # Save team
    selected = best_team
    def_count = best_info["defense"]
    utl_count = best_info["utility"]
    all_attrs = set()
    for n in selected:
        all_attrs.update(get_attrs(n))

    # Save team
    team_data = {
        "members": selected,
        "total_score": sum(p["score"] for p in ranked if p["name"] in selected),
        "defense_count": def_count,
        "utility_count": utl_count,
        "attr_coverage": len(all_attrs),
    }
    pets_data = {}
    for n in selected:
        pets_data[n] = {
            "score": next(x["score"] for x in ranked if x["name"] == n),
            "attrs": get_attrs(n),
            "trait": pet_scores[n]["trait_name"],
            "recommended_skills": [sk["name"] for sk in team_skills.get(n, [])],
        }
    team_data["details"] = pets_data

    with open(DATA_DIR / "optimal_team.json", "w") as f:
        json.dump(team_data, f, ensure_ascii=False, indent=2)
    print(f"\n最优队伍已保存到 data/optimal_team.json")

    # Print pet_rankings.txt as well
    lines = []
    lines.append("=" * 100)
    lines.append("精灵综合排名 (技能 + 战斗能力[攻防速])")
    lines.append("=" * 100)
    for i, p in enumerate(ranked, 1):
        cd = p.get('combat_detail', {})
        actual_spd = cd.get('actual_spd', 0)
        combat_str = _format_combat(cd, p['combat_score'], actual_spd)
        boss_str = f" 首领化+{p['boss_bonus']:.0f}" if p.get('boss_bonus', 0) > 0 else ""
        trait_str = f" 负面={p['trait_score']:.0f}" if p['trait_score'] < 0 else ""
        sk_names = " · ".join(p.get("recommended_skills", [])[:4])
        lines.append(
            f"{i:>3}. {p['name']:<14} {p['score']:>6.1f}  "
            f"技能={p['skill_score']:.0f} [{sk_names}] {combat_str}{boss_str}{trait_str}  "
            f"【{p['trait_name']}】{p['trait_desc'][:50]}"
        )
    (DATA_DIR / "all_pet_rankings.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"排名已保存到 data/all_pet_rankings.txt")


if __name__ == "__main__":
    main()
