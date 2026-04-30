#!/usr/bin/env python3
"""
洛克王国世界 - 全技能量化评分系统 v3

核心设计:
1. 以"等效伤害值"(EDV)为统一度量: 1 EDV ≈ 1点伤害价值
   基准: 3费60威 = 60/(3*20) = 1.0 EDV/费

2. 纯状态技能的费用 = 机会成本:
   cost_penalty = cost * 3 (1费≈3分, 参考同费攻击技能的基础伤害价值)

3. 印记: 按全队总收益量化
   湿润印记 = 全队耗能-1 → 12次使用×20EDV=240 → 80→归一化42
   光合印记 = 每回合+1能 → 8回合×20EDV=160 → 53→归一化38

4. 减益: ×0.6清除因子 (洗礼1费/除厄2费可清除)
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.attribute_matrix import AttributeMatrix

DATA_DIR = Path(__file__).parent.parent / "data"
am = AttributeMatrix()
type_bonus = {a: round(1.0 + am.gamescores[a]['offense'] * 0.15, 2) for a in am.attr_names}

def _find_int(pattern, text, default=0):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else default


# ============================================================
# 量化常量
# ============================================================

# === v6: 统一实战价值评估体系 ===
# 核心设计原则: 从底层统一三类技能平衡，无事后校准

# --- 1. 统一费用惩罚函数 (所有技能共用)
# 原理: 能量是最宝贵的资源，费用越高风险越大
# 1费=0, 2费=1, 3费=3, 4费=5, 5费=7, 6费+=每费+3
def fee_penalty(c):
    if c <= 0: return 0
    if c <= 1: return 1
    if c <= 2: return 3
    if c <= 3: return 6
    if c <= 4: return 10
    if c <= 5: return 15
    return 15 + (c - 5) * 6

# --- 2. 威力价值公式 (输出技能核心)
POWER_COEFF = 2.5  # 每10威力基础分

def power_value(power, cost):
    if power <= 80:
        base = power / 10 * POWER_COEFF
    elif power <= 120:
        base = 8 * POWER_COEFF + (power - 80) * 0.5 / 10 * POWER_COEFF
    else:
        base = 8 * POWER_COEFF + 40 * 0.5 / 10 * POWER_COEFF + (power - 120) * 0.25 / 10 * POWER_COEFF
    cost_diff = abs(cost - 3)
    eff_factor = max(0.30, 1.0 - cost_diff * 0.12)
    return base * eff_factor

MAX_POWER = 28

# --- 3. 减伤价值公式 (防御技能核心)
def defense_value(def_pct, cost):
    base = def_pct / 2.3  # 70%减伤 = 30.4基础分
    if cost <= 1:
        coeff = 1.0
    elif cost <= 2:
        coeff = 0.85
    elif cost <= 3:
        coeff = 0.6
    elif cost <= 4:
        coeff = 0.4
    else:
        coeff = 0.2
    return base * coeff

# --- 4. 强化价值公式 (状态技能核心)
def buff_value(atk_pct, spd_pct, def_pct):
    return atk_pct / 10 * 3.5 + spd_pct / 10 * 2.0 + def_pct / 10 * 2.3

# --- 印记基础分
MARK = {
    "湿润": 48, "光合": 40, "龙噬": 32, "星陨": 12,
    "风起": 28, "蓄电": 24, "蓄势": 20,
    "中毒印": 28, "棘刺": 24, "降灵": 24, "减速": 20, "攻击": 20,
}
MARK_LAYER = 6

# --- 应对分
COUNTER = {"打断应对": 14}

# --- 先手/迅捷
SWIFT_ATTACK = 24
SWIFT_DEFENSE = 20
PRIORITY = lambda n: 7 + n * 5

# --- 控制状态分
DEBUFF_LAYER = {"中毒": 5, "萌化": 7, "冻结": 4, "灼烧": 3}  # 萌化永久不消→高; 灼烧换人即消→低
CONTROL = {"眩晕": 18, "寄生": 12}

# --- 成长
GROWTH = {"power": 0.4, "combo": 4}

# --- 条件折扣 & 防御惩罚
COND_DISCOUNT = 0.5   # 若/额外/应对条件触发的强化，按50%期望值
DEF_PENALTY_MULT = 1.0  # 防御费用惩罚倍率
# 条件性强化检测模式
_COND_RE = r'(?:若|如果|额外获得|本技能位于|应对.{0,10}(?:改为|获得))'

# --- 反印记
ANTI_MARK = {"焚烧烙印": 34, "食腐": 18, "焚毁": 16, "驱散通用": 14, "心灵洞悉": 18}

# --- 惩罚
PENALTY = {"自杀": -25, "蓄力": -18, "自伤": -5, "自减益": -4}

# --- 特殊机制基础分 (传动=0，非正面机制)
MECH_BASE = {
    "传动": 0, "迸发": 12, "脱离": 10, "敌脱离": 14,
    "聒噪": 14, "烧能量": 12, "回能": 10, "清减益": 10, "清增益": 10,
    "天气": 20, "翻倍减益": 16, "交换技能": 12, "转化增益": 14,
    "全队回能": 12, "变身": 12, "迅捷链": 14, "相邻成长": 10,
}
def score_skill(skill):
    """v6: 统一实战价值评估 - 三类技能从底层平衡，无事后校准"""
    name = skill.get("name", "")
    desc = skill.get("desc", "")
    element = skill.get("element", "")
    power = skill.get("power", 0)
    cost = skill.get("cost", 0)
    full_text = f"{name} {desc}"
    tc = type_bonus.get(element, 1.0)
    pts = {}

    final = 0

    # ========================
    # 1. 核心价值计算
    # ========================

    # --- 1a. 输出技能: 威力核心 ---
    if power > 0:
        pv = min(power_value(power, cost), MAX_POWER)
        pts["威力"] = round(pv, 1)
        final += pv

        # 0费攻击额外加分
        if cost == 0:
            pts["0费奖励"] = 3
            final += 3

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

    # 先手/迅捷
    if "迅捷" in desc:
        if power > 0:
            pts["迅捷(攻)"] = SWIFT_ATTACK
            final += SWIFT_ATTACK
        else:
            pts["迅捷"] = SWIFT_DEFENSE
            final += SWIFT_DEFENSE
    elif "先手" in desc:
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
    # 辅助: 检测印记是否仅在应对子句中 (条件折扣)
    def _mark_cond(mk_name):
        if "应对" in desc and mk_name not in desc.split("应对")[0]:
            return True
        return False

    for mk_name, base, pat in mark_config:
        if mk_name in desc:
            layers = max(1, _find_int(pat, desc))
            v = base + (layers - 1) * MARK_LAYER
            if _mark_cond(mk_name):
                v = int(v * COND_DISCOUNT)
                mk_name += "(条件)"
            pts[mk_name] = v
            final += v
            break
    else:
        if "星陨" in desc:
            layers = max(1, _find_int(r"(\d+)层星陨", desc))
            v = MARK["星陨"] + (layers - 1) * MARK_LAYER
            if _mark_cond("星陨"):
                v = int(v * COND_DISCOUNT)
            pts["星陨印记"] = v
            if _mark_cond("星陨"):
                pts["星陨印记(条件)"] = pts.pop("星陨印记")
            final += v
        elif "中毒印记" in desc or ("转化为印记" in desc and "中毒" in desc):
            v = MARK["中毒印"]
            if _mark_cond("中毒"):
                v = int(v * COND_DISCOUNT)
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

    # 控制状态 (层式减益) — 萌化只计敌方/双方；中毒印记已含中毒不计重复
    for kw, per_layer in DEBUFF_LAYER.items():
        if kw in desc:
            if kw == "萌化":
                if "敌方" not in desc and "双方" not in desc:
                    continue
            if kw == "中毒" and ("中毒印记" in desc or "转化为印记" in desc):
                continue  # 中毒印记已包含中毒效果，不重复计
            layers = max(1, _find_int(rf"(\d+)层{kw}", desc))
            pts[f"{kw}({layers}层)"] = layers * per_layer
            final += layers * per_layer
    # 单次强控
    for kw, v in CONTROL.items():
        if kw in desc:
            pts[kw] = v
            final += v

    # 敌方削弱
    CF = 0.7  # 可清除系数
    if "敌方" in desc:
        if ("物防" in desc and "魔防" in desc) or "双防" in desc:
            pts["敌双防-"] = int(6 * CF); final += int(6 * CF)
        elif "物防" in desc or "魔防" in desc:
            pts["敌单防-"] = int(4 * CF); final += int(4 * CF)
        if ("物攻" in desc and "魔攻" in desc) or "双攻" in desc:
            pts["敌双攻-"] = int(5 * CF); final += int(5 * CF)
        elif "物攻" in desc or "魔攻" in desc:
            pts["敌单攻-"] = int(3 * CF); final += int(3 * CF)
        if "速度" in desc and "降低" in desc:
            pts["敌速-"] = 3; final += 3
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
    if atk_pct > 0 or spd_pct > 0 or def_pct > 0 or has_double:
        bv = buff_value(atk_pct, spd_pct, def_pct)
        # 条件强化打折
        if cond_atk:
            bv -= atk_pct / 10 * 3.5 * (1 - COND_DISCOUNT)
        if cond_spd:
            bv -= spd_pct / 10 * 2.0 * (1 - COND_DISCOUNT)
        if cond_def:
            bv -= def_pct / 10 * 2.3 * (1 - COND_DISCOUNT)
        if atk_pct > 0:
            k = f"攻+{atk_pct}%" + ("(条件)" if cond_atk else "")
            pts[k] = round(atk_pct / 10 * 3.5 * (COND_DISCOUNT if cond_atk else 1.0), 1)
        if spd_pct > 0:
            suffix = "%" if spd_is_pct else ""
            k = f"速+{spd_pct}{suffix}" + ("(条件)" if cond_spd else "")
            pts[k] = round(spd_pct / 10 * 2.0 * (COND_DISCOUNT if cond_spd else 1.0), 1)
        if def_pct > 0:
            k = f"防+{def_pct}%" + ("(条件)" if cond_def else "")
            pts[k] = round(def_pct / 10 * 2.3 * (COND_DISCOUNT if cond_def else 1.0), 1)
        # 攻+速协同: 同时加输出和先手 → 推队能力
        if atk_pct > 0 and spd_pct > 0:
            synergy = min(atk_pct, spd_pct) / 10 * 2.0
            if cond_atk or cond_spd:
                synergy *= COND_DISCOUNT
            pts["攻速协同"] = round(synergy, 1)
            bv += synergy
        # 翻倍增益: 有自身buff→按buff值50%估算; 纯翻倍→保守估计12
        if has_double:
            if atk_pct == 0 and spd_pct == 0 and def_pct == 0:
                double_val = 12
            else:
                double_val = (atk_pct / 10 * 3.5 + spd_pct / 10 * 2.0 + def_pct / 10 * 2.3) * 0.5
            double_val = min(double_val, 20)
            pts["翻倍增益"] = round(double_val, 1)
            bv += double_val
        final += bv

    # 特殊机制
    if "吸血" in desc:
        pct = _find_int(r"吸血(\d+)%", desc)
        v = 6 if pct >= 50 else 4
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
    if re.search(r"连击数\+|连击数永久|连击数翻倍", desc):
        n = _find_int(r"连击数\+(\d+)", desc) or _find_int(r"连击数永久\+(\d+)", desc) or 1
        n = min(n, 5)
        pts["连击数+"] = n * GROWTH["combo"]; final += n * GROWTH["combo"]
    if "连击" in desc and "连击数" not in desc:
        n = _find_int(r"(\d+)连击", desc) or 2
        pts[f"{n}连击"] = 3 if n >= 2 else 0; final += 3 if n >= 2 else 0
    if "脱离" in desc:
        if "紧急" in desc:
            pts["紧急脱离"] = 5; final += 5
        elif "敌" in desc:
            pts["敌脱离"] = MECH_BASE["敌脱离"]; final += MECH_BASE["敌脱离"]
        else:
            pts["脱离"] = MECH_BASE["脱离"]; final += MECH_BASE["脱离"]
    if "聒噪" in desc or "全攻击技能能耗" in desc:
        pts["聒噪"] = MECH_BASE["聒噪"]; final += MECH_BASE["聒噪"]
    if ("失去" in desc or "偷取" in desc) and "能量" in desc and "敌方" in desc:
        e = _find_int(r"(?:失去|偷取)(\d+)能量", desc)
        v = 8 if e >= 6 else MECH_BASE["烧能量"]
        pts["烧能量"] = v; final += v
    if "回复" in desc and "能量" in desc and "敌方" not in desc:
        e = _find_int(r"回复(\d+)能量", desc)
        v = 6 if e >= 4 else MECH_BASE["回能"]
        pts["回能"] = v; final += v
    if "回复" in desc and ("HP" in desc or "生命" in desc) and "敌方" not in desc and "吸血" not in desc:
        pts["回血"] = 4; final += 4
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
            gv = pw * GROWTH["power"] * (COND_DISCOUNT if is_cond else 1.0)
            k = "威力成长(条件)" if is_cond else "威力成长"
            pts[k] = int(gv); final += int(gv)
    elif power == 0 and "威力" in desc:
        # 状态技能临时威力强化 (如"攻击技能威力+70")
        tmp = _find_int(r"威力\+(\d+)", desc)
        if tmp > 0:
            v = min(tmp * 0.15, 10)
            pts["威力强化"] = round(v, 1); final += v
        pts["威力强化"] = round(v, 1); final += v

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

    if "蓄力" in desc:
        pts["蓄力"] = PENALTY["蓄力"]; final += PENALTY["蓄力"]
    if "对自己" in desc and "造成" in desc:
        pts["自伤"] = PENALTY["自伤"]; final += PENALTY["自伤"]
    # 应对失败风险: 自身有负面效果，仅应对成功才转为正面
    if re.search(r'(?:获得|自己).{0,10}[-−]\d+%.{0,30}应对', desc):
        pts["应对风险"] = -5; final += -5

    # 属性系数修正 (攻击技能影响更大)
    if power > 0:
        pts["属性系数"] = round(tc, 2)
        final = round(final * tc, 1)

    # ========================
    # 3. 费用惩罚 (仅非输出技能，防御技能惩罚更高)
    # ========================
    if power == 0:
        penalty = fee_penalty(cost)
        is_defense = "减伤" in desc or ("减少" in desc and "伤害" in desc)
        if is_defense:
            penalty = int(penalty * DEF_PENALTY_MULT)
        if penalty > 0:
            pts["费用惩罚"] = -penalty
        final = max(0, final - penalty)

    return round(final, 1), {
        "name": name, "element": element, "category": skill.get("category", ""),
        "cost": cost, "power": power, "desc": desc,
        "type_coef": tc,
        "score": round(final, 1), "points": pts,
    }


def generate_rankings():
    with open(DATA_DIR / "pets_final.json", encoding="utf-8") as f:
        pets = json.load(f)
    skill_map = {}
    for pid, pet in pets.items():
        for sk in pet["skills"].get("learnset", []) + pet["skills"].get("recommended", []):
            if sk["name"] not in skill_map:
                skill_map[sk["name"]] = sk
    results = [(score_skill(sk)[0], score_skill(sk)[1]) for sk in skill_map.values()]
    results.sort(key=lambda x: -x[0])
    tiered = []
    for score, bd in results:
        if score >= 32: tier = "S"
        elif score >= 24: tier = "A"
        elif score >= 14: tier = "B"
        else: tier = "C"
        tiered.append((tier, score, bd))
    return tiered, len(skill_map)


def print_rankings(tiered, total_count):
    lines = []
    lines.append("=" * 100)
    lines.append(f"技能评分 v6 — 统一实战价值体系 (总计 {total_count} 个技能)")
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


def save_json(tiered):
    json_data = [{
        "rank": i+1, "tier": tier, "name": bd["name"], "element": bd["element"],
        "category": bd["category"], "cost": bd["cost"], "power": bd["power"],
        "score": round(score, 1), "type_coef": bd["type_coef"],
        "is_pure_status": bd["power"] == 0,
        "points": bd["points"], "desc": bd["desc"],
    } for i, (tier, score, bd) in enumerate(tiered)]
    with open(DATA_DIR / "all_skill_rankings.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    tiered, total = generate_rankings()
    output = print_rankings(tiered, total)
    (DATA_DIR / "all_skill_rankings.txt").write_text(output, encoding="utf-8")
    print(f"已保存 data/all_skill_rankings.txt")
    save_json(tiered)
    print(f"已保存 data/all_skill_rankings.json")
    tc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for tier, _, _ in tiered: tc_counts[tier] += 1
    print(f"\n层级分布: S={tc_counts['S']} A={tc_counts['A']} B={tc_counts['B']} C={tc_counts['C']}")
