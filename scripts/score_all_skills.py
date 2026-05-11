#!/usr/bin/env python3
"""
洛克王国世界 - 全技能量化评分系统 v5

核心设计:
1. 输出技能: 纯性价比 power/(cost+2)，实际伤害归入精灵种族值计算
2. 异常状态: EDV折算（期望回合×存活概率×贴现）
   灼烧 2%/层,每回合减半,换人消失 → 6.0 EDV/层
   中毒 3%/层,换人消失         → 12.0 EDV/层
   中毒印记 3%/层,不消失       → 20.0 EDV/层
3. 属性系数只乘伤害部分(威力+灼烧+中毒)，不乘印记/控制/强化
4. HP依赖威力衰减 (彗星类: 按残血60%HP损失折算)
5. 灾厄类应对状态自伤按失败率×威力折算
6. 风墙类防御迅捷对标吓退 (全队受益)
7. 防御技能独立成表
8. 异常状态层数递减: layers^0.75 (多层=对手更快换人)
"""


import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.attribute_matrix import AttributeMatrix

DATA_DIR = Path(__file__).parent.parent / "data"
am = AttributeMatrix()
# v7优化: 压缩属性系数区间，避免极端属性的技能评分过低
# 原公式: 1.0 + offense * 0.15 → 范围[0.4, 1.3]，差距3.25倍
# 新公式: 压缩到[0.65, 1.2]范围，差距1.85倍
def calc_type_bonus(offense_score):
    # 基础分偏移 + 系数压缩
    base = 1.0 + offense_score * 0.10  # 系数从0.15降到0.10
    # 对低分属性额外补偿
    if offense_score <= -3:  # 虫/草/一般
        base += 0.15
    elif offense_score <= -2:  # 电/毒
        base += 0.1
    return round(base, 2)

type_bonus = {a: calc_type_bonus(am.gamescores[a]['offense']) for a in am.attr_names}

def _find_int(pattern, text, default=0):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


# ============================================================
# 量化常量
# ============================================================

# --- 1. 输出技能性价比公式: power/(cost+2) ---
def power_value(power, cost):
    return power / (cost + 2)

# 基准HP (用于%伤害折算EDV)
AVG_HP = 300

# --- 2. 异常状态 EDV 折算表 ---
# 灼烧: 2%/层,每回合减半,换人消失,受属性克制
# 期望回合 = Σ 0.5^(t-1) × 存活(t) × 0.85^(t-1) × 2%×HP
BURN_EDV_PER_LAYER = 4.0
# 中毒: 3%/层,不减半,换人消失,受属性克制
POISON_EDV_PER_LAYER = 12.0
# 中毒印记: 3%/层,不减半,不消失,不受属性克制
POISON_MARK_EDV_PER_LAYER = 20.0

# --- 3. 减伤价值公式 (防御技能核心)
def defense_value(def_pct, cost):
    # v7优化: 减伤基础分从2.3调到2.5，避免防御技能S级过多
    base = def_pct / 2.5  # 70%减伤 = 28基础分 (原30.4)
    if def_pct >= 100:
        base += 25  # 完全免疫是质变，非线性的10%
    if cost <= 1:
        coeff = 1.0
    elif cost <= 2:
        coeff = 0.9
    elif cost <= 3:
        coeff = 0.7
    elif cost <= 4:
        coeff = 0.5
    else:
        coeff = 0.5  # 5费防御费用惩罚已在大费扣分体现, 不双重惩罚
    return base * coeff

# --- 4. 强化价值公式 (状态技能核心)
# v7优化: 增加强化边际效益递减
# 原理: 从0%→100%是质变，但100%→200%边际效益明显降低
def _buff_diminish(pct, base_value):
    """强化边际递减函数: 100%以内1.0倍，100-130%0.6倍，130%以上0.35倍"""
    if pct <= 100:
        return pct / 10 * base_value
    elif pct <= 130:
        return 100 / 10 * base_value + (pct - 100) / 10 * base_value * 0.6
    else:
        return 100 / 10 * base_value + 30 / 10 * base_value * 0.6 + (pct - 130) / 10 * base_value * 0.35

def buff_value(atk_pct, spd_pct, def_pct):
    return _buff_diminish(atk_pct, 3.5) + _buff_diminish(spd_pct, 2.0) + _buff_diminish(def_pct, 2.8)

# --- 印记基础分
MARK = {
    "湿润": 38, "光合": 40, "龙噬": 32, "星陨": 12,
    "风起": 28, "蓄电": 24, "蓄势": 20,
    "中毒印": POISON_MARK_EDV_PER_LAYER, "棘刺": 24, "降灵": 24, "减速": 20, "攻击": 20,
}
MARK_LAYER = 6

# --- 应对分
COUNTER = {"打断应对": 14}

# --- 先手
PRIORITY = lambda n: 10 + n * 5

# --- 控制状态分
# v7优化: 灼烧降低到每层2分（可换人清除，实际收益有限）
DEBUFF_LAYER = {"中毒": POISON_EDV_PER_LAYER, "萌化": 20, "冻结": 8, "灼烧": BURN_EDV_PER_LAYER}
CONTROL = {"眩晕": 18, "寄生": 12}

# --- 成长
GROWTH = {"power": 0.4, "combo": 4}

# --- 条件折扣 & 防御惩罚
DEF_PENALTY_MULT = 1.0  # 防御费用惩罚倍率
# v7优化: 应对成功率按类型分级，不是统一50%
# 应对攻击 = 高概率，对面每回合都用攻击 (70-80%)
# 应对打断 = 中概率，只有特定技能能打断 (60%)
# 应对状态 = 低概率，需要预判对面用状态 (40%)
def cond_discount(desc, cost, power=0):
    """根据应对类型计算成功率折扣"""
    # 先判断应对什么类型
    if "应对状态" in desc:
        base = 0.4  # 应对状态成功率最低
    elif "应对攻击" in desc:
        base = 0.6  # 对方不一定攻击, 防御buff不稳定
    elif "应对" in desc and ("打断" in desc or "攻击" in desc.split("应对")[-1]):
        base = 0.55
    else:
        base = 0.5  # 其他应对按中间值

    # 费用微调：低费更灵活，成功率略高
    if cost <= 2:
        return min(base * 1.05, 0.75)
    return base
COND_DISCOUNT_BASE = 0.5  # 统一兜底值
# 条件性强化检测模式
_COND_RE = r'(?:若|如果|额外获得|本技能位于|应对.{0,10}(?:改为|获得))'

# --- 反印记
ANTI_MARK = {"焚烧烙印": 28, "食腐": 18, "焚毁": 22, "驱散通用": 14, "心灵洞悉": 18}

# --- 惩罚
PENALTY = {"自杀": -80, "蓄力": -18, "自伤": -5, "自伤应对": -30, "自减益": -4}

# --- 特殊机制基础分 (传动=0，非正面机制)
MECH_BASE = {
    "传动": 0, "迸发": 12, "脱离": 10, "敌脱离": 14,
    "聒噪": 14, "烧能量": 12, "回能": 10, "清减益": 10, "清增益": 10,
    "天气": 20, "翻倍减益": 16, "交换技能": 12, "转化增益": 14,
    "全队回能": 12, "变身": 12, "迅捷链": 14, "相邻成长": 10,
}
# v7优化: 脱离机制战术价值全面提升 (换人是回合制游戏核心战术)
# 分级体系: 无条件 > 条件触发 | 敌方脱离 > 自己脱离 | 紧急 = 更快的脱离
ESCAPE_VALUE = {
    "self_uncond": 26,      # 无条件自己脱离 (如高温回火: 造成伤害后必脱离)
    "self_cond": 14,        # 条件性自己脱离 (应对攻击才触发, 如泡沫幻影)
    "self_uncond_emerg": 12,  # 无条件紧急自己脱离 (主动脱离)
    "self_cond_emerg": 8,    # 条件性紧急自己脱离 (如掩护)
    "enemy_uncond": 28,       # 无条件敌方脱离
    "enemy_cond": 20,         # 条件性敌方脱离 (如吓退)
    "enemy_uncond_emerg": 33, # 无条件紧急敌方脱离
    "enemy_cond_emerg": 24,   # 条件性紧急敌方脱离
    "both_uncond": 32,        # 双方都脱离 (强制重置场面)
    "inherit_buff": 8,        # 脱离时传增益给下一个
    "next_heal_energy": 6,    # 脱离时给下一个回能
    "next_defense": 5,        # 脱离时给下一个减伤 (随机上场减分)
}
def score_skill(skill):
    """v5: 统一实战价值评估 - 三类技能从底层平衡，无事后校准"""
    name = skill.get("name", "")
    desc = skill.get("desc", "")
    element = skill.get("element", "")
    power = skill.get("power", 0)
    cost = skill.get("cost", 0)
    full_text = f"{name} {desc}"
    tc = type_bonus.get(element, 1.0)
    pts = {}

    final = 0
    damage_total = 0  # 受属性克制的伤害部分(威力+灼烧+中毒)

    # ========================
    # 1. 核心价值计算
    # ========================

    # --- 1a. 输出技能: 纯性价比 ---
    if power > 0:
        # HP依赖威力衰减 (如彗星: 每失去5%生命威力-10, 残血用, 按60%HP损失算)
        if re.search(r"每失去\d+%生命.*威力[−-]\d+", desc):
            pct_per_step = _find_int(r"每失去(\d+)%生命", desc) or 5
            power_loss = _find_int(r"威力[−-](\d+)", desc) or 10
            hp_lost = 60  # 残血使用, 按损失60%HP估算
            power = max(0, power - (hp_lost // pct_per_step) * power_loss)
        pv = power_value(power, cost)
        pts["威力"] = round(pv, 1)
        final += pv
        damage_total += pv

        # 0费攻击额外奖励（免费压血线）
        if cost == 0:
            pts["0费奖励"] = 5
            final += 5

    # --- 1b. 防御技能: 减伤核心 ---
    if "减伤" in desc or "减少" in desc and ("伤害" in desc or "受到" in desc):
        pct = _find_int(r"减伤(\d+)%", desc) or _find_int(r"减少(\d+)%", desc)
        if pct > 0:
            dv = defense_value(pct, cost)
            pts[f"减伤{pct}%"] = round(dv, 1)
            final += dv

    # ========================
    # 2. 通用附加效果 (所有技能共用)
    # ========================

    # 迅捷标记 (后置计算, 见下方)
    has_swift = "迅捷" in desc
    # 先手
    if not has_swift and "先手" in desc:
        n = _find_int(r"先手\+(\d+)", desc)
        if n > 0:
            v = PRIORITY(n)
            pts[f"先手+{n}"] = v
            final += v
        elif "先手-" in desc:
            n = _find_int(r"先手-(\d+)", desc) or 1
            v = -PRIORITY(n)
            pts[f"先手-{n}"] = v
            final += v

    # 应对
    conds = {
        "打断应对": lambda: "应对" in desc and "打断" in desc,
    }
    for key, fn in conds.items():
        if fn():
            pts[key] = COUNTER[key]
            final += COUNTER[key]

    # 印记
    mark_config = [
        ("湿润印记", MARK["湿润"], r"(\d+)层湿润"),
        ("棘刺印记", MARK["棘刺"], r"(\d+)层棘刺"),
        ("降灵印记", MARK["降灵"], r"(\d+)层降灵"),
        ("龙噬印记", MARK["龙噬"], r"(\d+)层龙噬"),
        ("光合印记", MARK["光合"], r"(\d+)层光合"),
        ("风起印记", MARK["风起"], r"(\d+)层风起"),
        ("减速印记", MARK["减速"], r"(\d+)层减速"),
        ("攻击印记", MARK["攻击"], r"(\d+)层攻击"),
        ("蓄电印记", MARK["蓄电"], r"(\d+)层蓄电"),
        ("蓄势印记", MARK["蓄势"], r"(\d+)层蓄势"),
    ]
    # 辅助: 检测印记是否为条件触发 (应对/转化)
    def _mark_cond(mk_name):
        # v7优化: 转化为印记需要前置状态，按条件折扣处理
        if "转化" in desc:
            return True
        if "应对" in desc and mk_name not in desc.split("应对")[0]:
            return True
        return False

    cd = cond_discount(desc, cost, power)  # 本技能的条件折扣
    for mk_name, base, pat in mark_config:
        if mk_name in desc:
            layers = max(1, _find_int(pat, desc))
            v = base + (layers - 1) * MARK_LAYER
            if _mark_cond(mk_name):
                v = int(v * cd)
                mk_name += "(条件)"
            pts[mk_name] = v
            final += v
            break
    else:
        if "星陨" in desc:
            layers = max(1, _find_int(r"(\d+)层星陨", desc))
            v = MARK["星陨"] + (layers - 1) * MARK_LAYER
            if _mark_cond("星陨"):
                v = int(v * cd)
            pts["星陨印记"] = v
            if _mark_cond("星陨"):
                pts["星陨印记(条件)"] = pts.pop("星陨印记")
            final += v
        elif "中毒印记" in desc or ("转化为印记" in desc and "中毒" in desc):
            v = MARK["中毒印"]
            if _mark_cond("中毒"):
                v = int(v * cd)
            pts["中毒印记"] = v
            final += v
        elif "印记" in desc and "驱散" not in desc and "焚毁" not in name and "食腐" not in name:
            layers = _find_int(r"(\d+)层.{1,3}印记", desc)
            if layers > 0:
                v = 10 + (layers - 1) * MARK_LAYER
                pts["印记(通用)"] = v
                final += v

    # 印记驱散
    if "焚烧烙印" in full_text:
        pts["驱散+转化"] = ANTI_MARK["焚烧烙印"]; final += ANTI_MARK["焚烧烙印"]
    elif "食腐" in full_text and "驱散" in desc:
        pts["驱散+回血"] = ANTI_MARK["食腐"]; final += ANTI_MARK["食腐"]
    elif "焚毁" in full_text:
        pts["驱散印记"] = ANTI_MARK["焚毁"]; final += ANTI_MARK["焚毁"]
    elif "心灵洞悉" in full_text:
        pts["印记反制"] = ANTI_MARK["心灵洞悉"]; final += ANTI_MARK["心灵洞悉"]
    elif "驱散" in desc and "印记" in desc:
        pts["驱散印记"] = ANTI_MARK["驱散通用"]; final += ANTI_MARK["驱散通用"]

    # 控制状态 (层式减益) — 灼烧/中毒受属性克制; 萌化只计敌方/双方；中毒印记已含中毒不计重复
    # 层数递减: 多层=对手更快换人, layers^0.75 边际递减
    for kw, per_layer in DEBUFF_LAYER.items():
        if kw in desc:
            if kw == "萌化":
                if "敌方" not in desc and "双方" not in desc:
                    continue
            if kw == "中毒" and ("中毒印记" in desc or "转化为印记" in desc):
                continue  # 中毒印记已包含中毒效果，不重复计
            layers = max(1, _find_int(rf"(\d+)层{kw}", desc))
            v = per_layer * layers ** 0.75
            pts[f"{kw}({layers}层)"] = round(v, 1)
            final += v
            if kw in ("灼烧", "中毒"):
                damage_total += v
    # 单次强控
    for kw, v in CONTROL.items():
        if kw in desc:
            pts[kw] = v
            final += v

    # 敌方削弱 (排除比较句式：X比敌方越高/越低)
    CF = 0.7  # 可清除系数
    def _debuff_score(pct, base, layers=1):
        """减益动态记分：递减函数 × 可清除系数 × 层数"""
        return _buff_diminish(pct, base) * CF * layers

    if "敌方" in desc:
        is_comparison = "比敌方" in desc  # 鸣沙陷阱: 物防比敌方越高
        if not is_comparison:
            # 提取减益百分比：双防=物防+魔防共用同一百分比（如"敌方双防-30%"）
            dual_def = _find_int(r"双防-(\d+)%", desc)
            pdef_pct = dual_def or _find_int(r"物防-(\d+)%", desc)
            mdef_pct = dual_def or _find_int(r"魔防-(\d+)%", desc)
            # 提取层数
            def_layers = max(1, _find_int(r"(\d+)层(?:.{0,5}(?:双防|物防|魔防))", desc))

            dual_atk = _find_int(r"双攻-(\d+)%", desc)
            patk_pct = dual_atk or _find_int(r"物攻-(\d+)%", desc)
            matk_pct = dual_atk or _find_int(r"魔攻-(\d+)%", desc)
            atk_layers = max(1, _find_int(r"(\d+)层(?:.{0,5}(?:双攻|物攻|魔攻))", desc))

            spd_pct = _find_int(r"速度-(\d+)%", desc) or _find_int(r"速度-(\d+)", desc)
            spd_layers = max(1, _find_int(r"(\d+)层(?:.{0,5}速度)", desc))

            if pdef_pct > 0:
                v = _debuff_score(pdef_pct, 1.7, def_layers)
                pts[f"敌物防-{pdef_pct}%{'×'+str(def_layers) if def_layers>1 else ''}"] = round(v, 1)
                final += v
            if mdef_pct > 0:
                v = _debuff_score(mdef_pct, 1.7, def_layers)
                pts[f"敌魔防-{mdef_pct}%{'×'+str(def_layers) if def_layers>1 else ''}"] = round(v, 1)
                final += v
            if patk_pct > 0:
                v = _debuff_score(patk_pct, 2.5, atk_layers)
                pts[f"敌物攻-{patk_pct}%{'×'+str(atk_layers) if atk_layers>1 else ''}"] = round(v, 1)
                final += v
            if matk_pct > 0:
                v = _debuff_score(matk_pct, 2.5, atk_layers)
                pts[f"敌魔攻-{matk_pct}%{'×'+str(atk_layers) if atk_layers>1 else ''}"] = round(v, 1)
                final += v
            if spd_pct > 0:
                v = _debuff_score(spd_pct, 2.0, spd_layers)
                pts[f"敌速度-{spd_pct}%{'×'+str(spd_layers) if spd_layers>1 else ''}"] = round(v, 1)
                final += v
        if "技能能耗" in desc:
            pts["敌加费"] = 6; final += 6

    # 自身强化 — 条件折扣: buff仅出现在条件子句中才打折
    def _is_cond(buff_re):
        """buff是否仅在条件子句中出现(不在正常描述中)"""
        before = desc
        for mk in ['若', '如果', '应对', '额外获得', '本技能位于']:
            if mk in before:
                before = before.split(mk)[0]
        return not re.search(buff_re, before)

    atk_pct = max(
        _find_int(r"物攻\+(\d+)%", desc),
        _find_int(r"魔攻\+(\d+)%", desc),
        _find_int(r"双攻\+(\d+)%", desc),
    )
    spd_pct = _find_int(r"速度\+(\d+)%", desc) or _find_int(r"速度\+(\d+)[^%]", desc) or _find_int(r"速度永久\+(\d+)", desc)
    spd_is_pct = bool(_find_int(r"速度\+(\d+)%", desc))
    def_pct = max(
        _find_int(r"物防\+(\d+)%", desc),
        _find_int(r"魔防\+(\d+)%", desc),
        _find_int(r"双防\+(\d+)%", desc),
    )
    has_double = "增益翻倍" in desc or "翻倍增益" in desc
    cond_atk = atk_pct > 0 and _is_cond(r'[物魔双]攻\+')
    cond_spd = spd_pct > 0 and _is_cond(r'速度\+|速度永久\+')
    cond_def = def_pct > 0 and _is_cond(r'[物魔双]防\+')
    cd = cond_discount(desc, cost, power)  # 本技能的条件折扣
    if atk_pct > 0 or spd_pct > 0 or def_pct > 0 or has_double:
        bv = buff_value(atk_pct, spd_pct, def_pct)
        # 条件强化打折
        if cond_atk:
            bv -= atk_pct / 10 * 3.5 * (1 - cd)
        if cond_spd:
            bv -= spd_pct / 10 * 2.0 * (1 - cd)
        if cond_def:
            bv -= def_pct / 10 * 2.8 * (1 - cd)
        if atk_pct > 0:
            k = f"攻+{atk_pct}%" + ("(条件)" if cond_atk else "")
            # v7优化: 使用递减后的数值显示
            atk_val = _buff_diminish(atk_pct, 3.5) * (cd if cond_atk else 1.0)
            pts[k] = round(atk_val, 1)
        if spd_pct > 0:
            suffix = "%" if spd_is_pct else ""
            k = f"速+{spd_pct}{suffix}" + ("(条件)" if cond_spd else "")
            # v7优化: 使用递减后的数值显示
            spd_val = _buff_diminish(spd_pct, 2.0) * (cd if cond_spd else 1.0)
            pts[k] = round(spd_val, 1)
        if def_pct > 0:
            k = f"防+{def_pct}%" + ("(条件)" if cond_def else "")
            # v7优化: 使用递减后的数值显示
            def_val = _buff_diminish(def_pct, 2.8) * (cd if cond_def else 1.0)
            pts[k] = round(def_val, 1)
        # 攻+速协同: 同时加输出和先手 → 推队能力
        if atk_pct > 0 and spd_pct > 0:
            synergy = min(atk_pct, spd_pct) / 10 * 2.0
            if cond_atk or cond_spd:
                synergy *= cd
            pts["攻速协同"] = round(synergy, 1)
            bv += synergy
        # 翻倍增益: 有自身buff→按buff值50%估算; 纯翻倍→保守估计12
        if has_double:
            if atk_pct == 0 and spd_pct == 0 and def_pct == 0:
                double_val = 12
            else:
                double_val = (atk_pct / 10 * 3.5 + spd_pct / 10 * 2.0 + def_pct / 10 * 2.8) * 0.5
            double_val = min(double_val, 20)
            pts["翻倍增益"] = round(double_val, 1)
            bv += double_val
        final += bv

    # 特殊机制
    # 血气类: 受到致命伤害时保留1生命值 (防秒杀)
    if "保留1" in desc and "生命" in desc:
        pts["保留1血"] = 6; final += 6
    if "吸血" in desc:
        pct = _find_int(r"吸血(\d+)%", desc) or _find_int(r"(\d+)%吸血", desc)
        # v7优化: 吸血按比例分级 (高吸血=高续航价值)
        if pct >= 100: v = 8
        elif pct >= 50: v = 6
        elif pct >= 20: v = 5
        else: v = 4
        pts["吸血"] = v; final += v
    if "传动" in desc:
        layers = _find_int(r"传动(\d+)", desc) or 1
        v = MECH_BASE["传动"] * layers
        if v != 0:
            pts["传动"] = v; final += v
    if "迸发" in desc:
        pts["迸发"] = MECH_BASE["迸发"]; final += MECH_BASE["迸发"]
    if "萌化转移给敌方" in desc or ("转移" in desc and "萌化" in desc and "敌方" in desc):
        pts["萌化转移"] = 10; final += 10
    if "全技能能耗永久" in desc:
        n = _find_int(r"全技能能耗永久-(\d+)", desc) or 1
        pts["能耗永久-"] = n * 8; final += n * 8
    # v7优化: 技能冷却减少效果评分（防御技能冷却-1战略价值很高）
    if "冷却" in desc and "-" in desc:
        n = _find_int(r"冷却-(\d+)", desc) or 1
        is_cond = "应对" in desc or "若" in desc
        v = n * 8 * (0.5 if is_cond else 1.0)  # 基础分从6→8
        pts["冷却-"] = int(v); final += int(v)
    # 本技能能耗永久减少效果评分
    # 应对成功永久降费战略价值极高：水刃4费→0费，天洪7费→1费
    if "本技能能耗" in desc and "-" in desc:
        n = _find_int(r"本技能能耗(?:永久)?-(\d+)", desc) or 1
        is_perm = "永久" in desc
        is_cond = "应对" in desc or "若" in desc
        if is_perm:
            base = 6  # 永久降费基础分
            # 应对成功率折扣：应对状态40%，应对攻击/打断75%
            if "应对状态" in desc:
                cd = 0.4
            elif "应对攻击" in desc or "应对打断" in desc:
                cd = 0.75
            else:
                cd = 0.5 if is_cond else 1.0
        else:
            base = 4 if n >= 3 else 3  # 降费多更值钱
            cd = 0.5 if is_cond else 1.0
        v = n * base * cd
        pts["能耗-"] = int(v); final += int(v)
    # 本技能能耗永久+N (如无畏之心: 使用后永久+2费)
    if "本技能能耗" in desc and "+" in desc and "永久" in desc:
        n = _find_int(r"本技能能耗永久\+(\d+)", desc) or _find_int(r"能耗永久\+(\d+)", desc) or 1
        v = -n * 5
        pts["能耗永久+"] = v; final += v
    # 使用后能耗重置 (如气沉丹田: 降费后重置, 降费白费)
    if "能耗重置" in desc:
        pts["能耗重置"] = -8; final += -8
    # v7优化: 每次受伤获得增益的机制（如嗜痛）
    # 只有少量技能会连击，期望叠加1.3次（大部分1次，少数2-3次）
    if ("每次受到伤害" in desc or "每次受伤" in desc) and "+" in desc:
        # 精确匹配攻/防/速增益百分比（排除减伤%、吸血%等干扰）
        buff_pct = max(
            _find_int(r"物攻\+(\d+)%", desc),
            _find_int(r"魔攻\+(\d+)%", desc),
            _find_int(r"双攻\+(\d+)%", desc),
            _find_int(r"[物魔双]防\+(\d+)%", desc),
            _find_int(r"速度\+(\d+)%", desc),
            0
        )
        if buff_pct == 0:
            buff_pct = 10  # 保底
        # 按期望1.3次叠加 + 应对成功率折扣0.5
        expected_val = _buff_diminish(int(buff_pct * 1.3), 3.5) * 0.5
        pts["受伤成长"] = round(expected_val, 1); final += expected_val
    if re.search(r"连击数\+|连击数永久|连击数翻倍", desc):
        n = _find_int(r"连击数\+(\d+)", desc) or _find_int(r"连击数永久\+(\d+)", desc) or 1
        n = min(n, 5)
        pts["连击数+"] = n * GROWTH["combo"]; final += n * GROWTH["combo"]
    if "连击" in desc and "连击数" not in desc:
        n = _find_int(r"(\d+)连击", desc) or 2
        # v7优化: 连击基础分按连击数分级 (触发奉献/连击相关机制)
        if n >= 4: v = 4
        elif n >= 2: v = 3
        else: v = 2  # 1连击也有价值(触发奉献)
        pts[f"{n}连击"] = v; final += v
    # v7优化: 奉献机制记分
    if "奉献" in desc:
        n = _find_int(r"(\d+)次奉献", desc) or 1
        v = n * 6  # 每层奉献价值6分(一次额外触发机会)
        pts["奉献"] = v; final += v
    # v7优化: 锁换人机制 (控制价值高)
    if "无法更换精灵" in desc:
        n = _find_int(r"(\d+)回合无法更换", desc) or 3
        v = n * 4  # 每回合控制值4分
        pts["锁换人"] = v; final += v
    # v7优化: 威力倍数机制 (如"威力变为10倍")
    if "威力变为" in desc and "倍" in desc:
        mul = _find_int(r"威力变为(\d+)倍", desc) or _find_int(r"变为(\d+)倍", desc)
        if mul and mul >= 2:
            # 应对状态下才能触发，按条件折扣
            is_cond = "应对" in desc or "若" in desc
            cd = cond_discount(desc, cost, power) if is_cond else 0.6
            # 威力倍数价值 = 基础威力 × (倍数-1) × 系数 × 折扣
            v = int(power * (mul - 1) * 0.08 * cd)
            pts[f"威力×{mul}"] = v; final += v
    # v7优化: 威力永久翻倍 (超级成长)
    if "威力永久翻倍" in desc:
        # 每层翻倍按初始威力×0.5算，期望2层
        v = int(power * 0.08 * 2)
        pts["威力翻倍"] = v; final += v
    # 电磁偏转类: 下回合/所选技能使用次数+1 (=额外一次行动, 高战略价值)
    if "使用次数" in desc and "+" in desc and ("下回合" in desc or "所选技能" in desc):
        v = 18  # 多一次技能使用=额外一回合
        is_cond = "应对" in desc
        if is_cond:
            cd = cond_discount(desc, cost, power)
            v = int(v * cd)
        pts["使用次数+"] = v; final += v
    # v7优化: 下次攻击技能威力翻倍 (一次性buff, 翻倍=期望+80威力=约25分)
    if "下次攻击技能" in desc and "翻倍" in desc:
        v = 25
        is_cond = "应对" in desc
        if is_cond:
            cd = cond_discount(desc, cost, power)
            v = int(v * cd)
        pts["下次翻倍"] = v; final += v
    # 听桥类: 应对成功先手反击, 用对手技能威力反杀=一局定胜负的战略价值
    if "造成伤害" in desc and "威力与" in desc and "相等" in desc:
        pts["反击秒杀"] = 35; final += 35
    # v7优化: 本技能威力等于敌方能耗×N (动态威力)
    if "本技能威力等于" in desc and "能耗" in desc:
        n = _find_int(r"能耗的(\d+)倍", desc) or 10
        # 按敌方平均能耗5计算期望值，减半
        expected_val = min(5 * n * 0.08, 15)
        pts["威力=能耗"] = round(expected_val, 1); final += expected_val
    # v7优化: 脱离机制分级评分 + 应对类型折扣
    if "脱离" in desc:
        is_self_escape = "自己" in desc or "己方" in desc or (not ("敌方" in desc or "敌" in desc))
        is_enemy_escape = "敌方" in desc or "敌" in desc
        is_both_escape = ("敌方" in desc or "敌" in desc) and "自己" in desc and "均脱离" in desc
        # 遁地类: "减伤50%并脱离，应对攻击" — 脱离是主效果, 非条件
        is_conditional = ("应对" in desc or "若" in desc) and "并脱离" not in desc
        is_emergency = "紧急" in desc

        # 计算应对成功率折扣: 应对攻击>应对打断>应对状态
        cd = 1.0
        if is_conditional:
            if "应对状态" in desc:
                cd = 0.4  # 应对状态成功率低
            elif "应对攻击" in desc or "应对打断" in desc:
                cd = 0.75  # 应对攻击/打断成功率高
            else:
                cd = 0.6

        if is_both_escape:
            pts["双方脱离"] = ESCAPE_VALUE["both_uncond"]; final += ESCAPE_VALUE["both_uncond"]
        else:
            if is_enemy_escape:
                if is_emergency:
                    base_v = ESCAPE_VALUE["enemy_uncond_emerg"]
                    k = "敌紧急脱离(条件)" if is_conditional else "敌紧急脱离"
                else:
                    base_v = ESCAPE_VALUE["enemy_uncond"]
                    k = "敌脱离(条件)" if is_conditional else "敌脱离"
                v = int(base_v * cd) if is_conditional else base_v
                pts[k] = v; final += v
            if is_self_escape and not is_both_escape:
                if is_emergency:
                    base_v = ESCAPE_VALUE["self_uncond_emerg"]
                    k = "紧急脱离(条件)" if is_conditional else "紧急脱离"
                else:
                    base_v = ESCAPE_VALUE["self_uncond"]
                    k = "脱离(条件)" if is_conditional else "脱离"
                v = int(base_v * cd) if is_conditional else base_v
                pts[k] = v; final += v
        # 脱离附带效果
        if ("继承" in desc or "传" in desc) and "增益" in desc:
            pts["传增益"] = ESCAPE_VALUE["inherit_buff"]; final += ESCAPE_VALUE["inherit_buff"]
        if ("下一个" in desc or "入场" in desc) and "能量" in desc and "回复" in desc:
            pts["下一个回能"] = ESCAPE_VALUE["next_heal_energy"]; final += ESCAPE_VALUE["next_heal_energy"]
        if ("下一个" in desc or "入场" in desc) and "减伤" in desc:
            pts["下一个减伤"] = ESCAPE_VALUE["next_defense"]; final += ESCAPE_VALUE["next_defense"]
    if "聒噪" in desc or "全攻击技能能耗" in desc:
        pts["聒噪"] = MECH_BASE["聒噪"]; final += MECH_BASE["聒噪"]
    if ("失去" in desc or "偷取" in desc) and "能量" in desc and "敌方" in desc:
        e = _find_int(r"(?:失去|偷取)(\d+)能量", desc)
        v = 8 if e >= 6 else MECH_BASE["烧能量"]
        pts["烧能量"] = v; final += v
    if "回复" in desc and "能量" in desc and "敌方" not in desc:
        e = _find_int(r"回复(\d+)能量", desc) or 0
        # v7优化: 回能按实际数值分级 (1能量≈2分, 边际效益递减)
        if e >= 10: v = 20
        elif e >= 8: v = 16
        elif e >= 5: v = 14
        elif e >= 4: v = 12
        elif e >= 3: v = 10
        elif e >= 2: v = 8
        elif e >= 1: v = 6
        else: v = 10  # 未知数量按保底算
        # 全队回能加成 (场下每个精灵都回能 → 价值×2)
        if "每个精灵" in desc or "全队" in desc or "场下" in desc:
            v = int(v * 2)
        # 应对性回能打折 (如抽枝: 应对状态才回能)
        if "应对" in desc and "回复" in desc.split("应对")[-1]:
            v = int(v * 0.6)
        pts["回能"] = v; final += v
    if "回复" in desc and ("HP" in desc or "生命" in desc) and "敌方" not in desc and "吸血" not in desc:
        # 回血: 稀有效果, 每10%=8分
        pct = _find_int(r"回复(\d+)%", desc) or _find_int(r"回复(\d+)%生命", desc) or _find_int(r"(\d+)%生命", desc) or 0
        if pct > 0:
            v = pct * 0.8
            # 条件性回血 (如抽枝: 应对状态才回血)
            if "应对" in desc:
                before = desc.split("应对")[0] if "应对" in desc else desc
                if "回复" not in before and "生命" not in before:
                    cd = cond_discount(desc, cost, power)
                    v = round(v * cd, 1)
            pts["回血"] = round(v, 1); final += v
        elif "减免的伤害变为回复" in desc:
            v = 28  # 100%减伤下伤害转回血 = 近乎无敌的续航
            pts["回血"] = v; final += v
        else:
            v = 5
            pts["回血"] = v; final += v
    if "清减益" in desc or ("驱散" in desc and "减益" in desc and "敌方" not in desc and "自己的减益" in desc):
        pts["清减益"] = MECH_BASE["清减益"]; final += MECH_BASE["清减益"]
    if "清增益" in desc or (re.search(r"驱散.*增益|清除.*增益", desc) and "驱散自己的减益" not in desc):
        pts["清增益"] = MECH_BASE["清增益"]; final += MECH_BASE["清增益"]
    if re.search(r"天气(?!系别)", desc) or "沙暴" in desc or "暴风雪" in desc or "雨天" in desc:
        pts["天气"] = MECH_BASE["天气"]; final += MECH_BASE["天气"]
    if re.search(r"减益的?层数翻倍|翻倍减益", desc):
        pts["翻倍减益"] = MECH_BASE["翻倍减益"]; final += MECH_BASE["翻倍减益"]
    if "交换技能" in desc:
        pts["交换技能"] = MECH_BASE["交换技能"]; final += MECH_BASE["交换技能"]
    if "转化增益" in desc:
        pts["转化增益"] = MECH_BASE["转化增益"]; final += MECH_BASE["转化增益"]
    if "释放" in desc and "迅捷" in desc and "连" in desc:
        pts["迅捷链"] = MECH_BASE["迅捷链"]; final += MECH_BASE["迅捷链"]

    # 成长类
    if "威力永久" in desc:
        pw = _find_int(r"威力永久\+(\d+)", desc) or _find_int(r"永久\+(\d+)", desc)
        if pw > 0:
            # 条件性成长折扣 (如"每应对成功，威力永久+X")
            before = desc
            for mk in ['若', '如果', '应对', '额外获得', '本技能位于']:
                if mk in before:
                    before = before.split(mk)[0]
            is_cond = not re.search(r'威力永久', before)
            cd = cond_discount(desc, cost, power)
            gv = pw * GROWTH["power"] * (cd if is_cond else 1.0)
            k = "威力成长(条件)" if is_cond else "威力成长"
            pts[k] = int(gv); final += int(gv)
    elif power == 0 and "威力" in desc:
        # 状态技能临时威力强化 (如"攻击技能威力+70", "全技能威力+40")
        tmp = _find_int(r"威力\+(\d+)", desc) or 0
        if tmp > 0:
            if "全技能" in desc:
                coeff = 0.28  # 全队每技能受益
            elif "攻击技能" in desc:
                coeff = 0.22  # 仅攻击技能
            else:
                coeff = 0.18  # 单技能或未知范围
            v = min(tmp * coeff, 14)
            pts["威力强化"] = round(v, 1); final += v
        # v7优化: 额外获得(条件性)威力也计入
        extra = _find_int(r"额外获得威力\+(\d+)", desc) or 0
        if extra > 0:
            ev = min(extra * coeff * 0.5, 8)  # 条件触发打5折
            pts["威力强化(+条件)"] = round(ev, 1); final += ev
    # v7优化: 敌方威力降低 (变相减伤)
    if "敌方" in desc and "技能威力" in desc and "-" in desc:
        n = _find_int(r"技能威力-(\d+)", desc)
        if n > 0:
            v = int(n * 0.08)
            pts["敌威力-"] = v; final += v

    # 负面惩罚
    if "消耗全部生命" in desc:
        pts["自杀"] = PENALTY["自杀"]; final += PENALTY["自杀"]
    # 自身获得负面属性 (非应对条件，确凿的自debuff)
    for m in re.finditer(r'自己.{0,25}?([物魔双]攻|[物魔双]防|速度)[-−](\d+)', desc):
        stat, val = m.group(1), int(m.group(2))
        # 跳过应对句式: 这是应对失败的默认状态，已有应对风险扣分
        if re.search(rf'{stat}[-−]{val}.*应对', desc):
            continue
        if '攻' in stat:
            penalty = -round(val / 10 * 2.5, 1)
        elif '防' in stat:
            penalty = -round(val / 10 * 1.7, 1)
        else:
            penalty = -round(val / 10 * 2.0, 1)
        pts[f"自{stat}-{val}"] = penalty
        final += penalty
    # 纯自萌化 (非敌方/双方) 是永久负面
    if "自己获得萌化" in desc and "敌方" not in desc and "双方" not in desc:
        pts["自萌化"] = -7; final += -7

    # 龙血类: 下次技能无需蓄力 (=跳过蓄力惩罚+即时使用, 高战术价值)
    if "无需蓄力" in desc:
        v = 14
        if "应对" in desc:
            cd = cond_discount(desc, cost, power)
            v = int(v * cd)
        pts["免蓄力"] = v; final += v
    # 蓄力惩罚: 排除"可以在蓄力状态下使用"(龙血类提供蓄力对策,非自身蓄力)
    if "蓄力" in desc and "可以在蓄力" not in desc and "无需蓄力" not in desc:
        pts["蓄力"] = PENALTY["蓄力"]; final += PENALTY["蓄力"]
    if "对自己" in desc and "造成" in desc:
        # 灾厄类技能: 应对状态才打敌方, 默认自伤 → 按失败率(60%)折算威力惩罚
        if "应对状态" in desc:
            self_dmg_penalty = -int(power * 0.6 / 3)  # 60%失败率 × 威力/3 = 期望自伤
            pts["自伤(应对)"] = self_dmg_penalty
            final += self_dmg_penalty
        else:
            pts["自伤"] = PENALTY["自伤"]; final += PENALTY["自伤"]
    # 应对失败风险: 自身有负面效果，仅应对成功才转为正面
    if re.search(r'(?:获得|自己).{0,10}[-−]\d+%.{0,30}应对', desc):
        pts["应对风险"] = -5; final += -5

    # v7优化: 动态威力技能(power=0但是攻击技能)标记
    is_dynamic_attack = power == 0 and ("造成物伤" in desc or "造成魔伤" in desc)

    # ========================
    # 迅捷后置计算 (基于核心价值: 威力/减伤 + 附带效果)
    # ========================
    if has_swift:
        if power > 0 or is_dynamic_attack:
            pv = power_value(power, cost)
            swift = 16 + pv * 0.4
            swift = max(16, min(28, round(swift, 1)))
        elif "减伤" in desc or ("减少" in desc and "伤害" in desc):
            # 防御先手价值: 风墙类技能让换人=防御, 全队受益, 对标吓退(28-33)
            dv = defense_value(_find_int(r"减伤(\d+)%", desc) or _find_int(r"减少(\d+)%", desc) or 50, cost)
            swift = 18 + dv * 0.35
            swift = max(18, min(26, round(swift, 1)))
        else:
            # 状态先手价值 = 基础12 + 效果关联
            swift = 12
            if "清增益" in desc: swift += 4
            if "清减益" in desc: swift += 3
            if any(kw in desc for kw in ['眩晕', '寄生', '萌化', '焚毁']): swift += 4
            if "连击数" in desc: swift += 2
            swift = max(12, min(18, swift))
        pts["迅捷(攻)" if power > 0 else "迅捷"] = swift
        final += swift

    # 属性系数：只作用在伤害部分(威力+灼烧+中毒)，不乘印记/控制/强化
    if power > 0 or is_dynamic_attack:
        pts["属性系数"] = round(tc, 2)
        utility = final - damage_total
        final = round(damage_total * tc, 1) + utility

    # ========================
    # 3. 费用 (仅非输出技能扣机会成本)
    # ========================
    if power == 0 and not is_dynamic_attack:
        if cost == 0:
            pts["0费基础分"] = 4
            final += 4
        elif cost > 0:
            # 每费 ≈ 4 EDV 机会成本 (简化线性替代旧非线性fee_penalty)
            cost_penalty = cost * 4
            pts["费用"] = -cost_penalty
            final = max(0, final - cost_penalty)

    # 纯变化技能打断风险: 可被打断应对完全废掉, 减伤类除外
    if skill.get("category") == "变化" and "减伤" not in desc:
        pts["打断风险"] = -3
        final += -3

    return round(final, 1), {
        "name": name, "element": element, "category": skill.get("category", ""),
        "cost": cost, "power": power, "desc": desc,
        "type_coef": tc,
        "score": round(final, 1), "points": pts,
    }


def generate_rankings():
    with open(DATA_DIR / "pet_learnset.json", encoding="utf-8") as f:
        learnsets = json.load(f)
    with open(DATA_DIR / "pet_recommended.json", encoding="utf-8") as f:
        recommended = json.load(f)
    skill_map = {}
    for pname, skills in learnsets.items():
        for sk in skills:
            if sk["name"] not in skill_map:
                skill_map[sk["name"]] = sk
    for pname, skills in recommended.items():
        for sk in skills:
            if sk["name"] not in skill_map:
                skill_map[sk["name"]] = sk
    results = [(score_skill(sk)[0], score_skill(sk)[1]) for sk in skill_map.values()]
    results.sort(key=lambda x: -x[0])
    # 分离防御技能 (减伤类)
    defense_results = [(s, bd) for s, bd in results if "减伤" in bd["desc"]]
    offense_results = [(s, bd) for s, bd in results if "减伤" not in bd["desc"]]
    tiered = []
    for score, bd in results:
        if score >= 32: tier = "S"
        elif score >= 24: tier = "A"
        elif score >= 14: tier = "B"
        else: tier = "C"
        tiered.append((tier, score, bd))
    def_tiered = []
    for score, bd in defense_results:
        if score >= 32: tier = "S"
        elif score >= 24: tier = "A"
        elif score >= 14: tier = "B"
        else: tier = "C"
        def_tiered.append((tier, score, bd))
    return tiered, def_tiered, len(skill_map), len(defense_results)


def print_rankings(tiered, total_count):
    lines = []
    lines.append("=" * 100)
    lines.append(f"技能评分 v5 — 统一实战价值体系 (总计 {total_count} 个技能)")
    lines.append("三类技能从底层平衡: 输出×威力边际递减 | 防御×费用效率 | 状态×效果综合")
    lines.append("=" * 100)
    tc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    prev_tier = None
    for rank, (tier, score, bd) in enumerate(tiered, 1):
        tc_counts[tier] += 1
        if tier != prev_tier:
            lines.append(f"\n--- {tier} 级 ---"); prev_tier = tier
        pts = bd["points"]
        pts_str = "; ".join(f"{k}={v}" for k, v in pts.items())
        pwr_str = str(bd["power"]) if bd["power"] > 0 else "—"
        lines.append(f"{rank:<4} {tier:<3} {bd['name']:<12} {bd['element']:<4} {bd['category']:<4} "
                     f"费{bd['cost']:<4} 威{pwr_str:<4} {score:>6.1f}  {pts_str}")
    lines.append(f"\n{'='*100}")
    lines.append(f"层级分布: S={tc_counts['S']} A={tc_counts['A']} B={tc_counts['B']} C={tc_counts['C']}")
    return "\n".join(lines)


def save_json(tiered, filename="all_skill_rankings.json"):
    json_data = [{
        "rank": i+1, "tier": tier, "name": bd["name"], "element": bd["element"],
        "category": bd["category"], "cost": bd["cost"], "power": bd["power"],
        "score": round(score, 1), "type_coef": bd["type_coef"],
        "is_pure_status": bd["power"] == 0,
        "points": bd["points"], "desc": bd["desc"],
    } for i, (tier, score, bd) in enumerate(tiered)]
    with open(DATA_DIR / filename, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    tiered, def_tiered, total, def_total = generate_rankings()
    output = print_rankings(tiered, total)
    (DATA_DIR / "all_skill_rankings.txt").write_text(output, encoding="utf-8")
    print(f"已保存 data/all_skill_rankings.txt")
    save_json(tiered)
    print(f"已保存 data/all_skill_rankings.json")
    # 防御技能独立表
    def_output = print_rankings(def_tiered, def_total)
    def_output = def_output.replace("技能评分 v6", "防御技能评分 v6")
    (DATA_DIR / "all_defense_skill_rankings.txt").write_text(def_output, encoding="utf-8")
    print(f"已保存 data/all_defense_skill_rankings.txt")
    save_json(def_tiered, "all_defense_skill_rankings.json")
    print(f"已保存 data/all_defense_skill_rankings.json")
    tc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for tier, _, _ in tiered: tc_counts[tier] += 1
    print(f"\n全部技能层级分布: S={tc_counts['S']} A={tc_counts['A']} B={tc_counts['B']} C={tc_counts['C']}")
    dc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for tier, _, _ in def_tiered: dc_counts[tier] += 1
    print(f"防御技能层级分布: S={dc_counts['S']} A={dc_counts['A']} B={dc_counts['B']} C={dc_counts['C']}")
