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

# --- 威力 ---
POWER_PER_PT = 3.0
MAX_POWER = 45
EFF_BONUS_CAP = 10

# --- 费用机会成本(纯状态) ---
COST_PENALTY = 3  # 每费扣3分

# --- 印记(已按全队总收益量化) ---
MARK = {
    "湿润": 42,   # 全队耗能-1: 12次×1费=12费节省
    "光合": 38,   # 每回合+1能: 8回合×1=8能生成
    "棘刺": 24,   # 入场-6%HP: 4次×18HP=72伤
    "龙噬": 24,   # 3费技能+40%攻: 4次×40=160伤
    "星陨": 24,   # 被击反击: 3次×20伤=60
    "降灵": 22,   # 入场-1能: 3次×1=3能损失
    "风起": 20,   # 首动+20%威: 3次×20=60伤
    "减速": 16,   # -10速度: 战术压制
    "攻击": 16,   # +10%威力: 3次×10=30伤
    "蓄电": 14,
    "蓄势": 12,
    "中毒印": 18,
}
MARK_LAYER = 4  # 每额外层+4

# --- 减益(×0.6清除因子) ---
DEBUFF_LAYER = {"冻结": 1, "灼烧": 1, "中毒": 2, "萌化": 2}
DEBUFF_FLAT = {"寄生": 2, "眩晕": 5, "睡眠": 3, "麻痹": 3, "混乱": 2, "束缚": 2, "恐惧": 2}

# --- 应对 ---
COUNTER = {"打断应对": 33, "应防": 10, "应态": 8, "应攻": 7, "应翻倍": 13, "应冻结": 10}

# --- 先手/迅捷 ---
SWIFT_ATTACK = 28
SWIFT_DEFENSE = 20
PRIORITY = lambda n: 5 + n * 4

# --- 特殊机制 ---
MECH = {
    "吸血50": 6, "吸血": 4,
    "传动": 14, "迸发": 8, "奉献": 6,
    "连击数++": 10, "连击数+": 6,
    "3连击": 6, "2连击": 3,
    "脱离": 10, "敌脱离": 12, "紧急脱离": 6, "返场": 8,
    "攻+脱离": 6,     # 高温回火类: 攻击同时脱离(额外战术价值)
    "防+脱离": 8,     # 泡沫幻影类: 防御同时脱离
    "聒噪": 14,
    "烧能量6+": 10, "烧能量": 6,
    "回能4+": 7, "回能": 4,
    "回血40+": 6, "回血": 3,
    # 纯状态特殊效果
    "技能复制": 8,
    "清减益": 6,
    "清增益": 9,
    "下次减费": 6,
    "偷能量": 6,
    "随机强化": 7,
    "敌加费": 8,
    "天气": 18,
    "翻倍减益": 12,
    "翻倍增益": 12,
    "交换技能": 12,
    "转化增益": 12,
    "交换HP": 8,
    "交换增益减益": 8,
    "继承增益": 8,
    "被动减费": 8,
    "冷却锁定": 6,
    "全队回能": 8,
    "变身": 10,
    # PVP关键技能
    "多系联动": 22,    # 折射: 根据携带技能获得18种效果之一
    "速度威力": 11,    # 闪击: 速度越高威力越高
    "迅捷链": 12,      # 疾风连袭: 重放迅捷技能
    "相邻成长": 6,     # 联动装置: 相邻技能永久成长
}

# --- 减伤 ---
DEFENSE = {"减伤70+": 10, "减伤60": 8, "减伤": 7}

# --- 成长 ---
GROWTH = {"power": 0.25, "combo": 3, "cost6+": 14, "cost4+": 12, "cost3": 8, "cost1-2": 5}

# --- 反印记 ---
ANTI_MARK = {"焚烧烙印": 23, "食腐": 12, "焚毁": 10, "驱散通用": 8, "心灵洞悉": 10}

# --- 惩罚 ---
PENALTY = {"自杀": -35, "蓄力": -16, "自伤": -6, "自减益": -6}


def score_skill(skill):
    name = skill.get("name", "")
    desc = skill.get("desc", "")
    element = skill.get("element", "")
    power = skill.get("power", 0)
    cost = skill.get("cost", 0)
    full_text = f"{name} {desc}"
    is_pure_status = (power == 0)
    tc = type_bonus.get(element, 1.0)
    pts = {}

    # ========================
    # 分组1: 属性相关维度
    # ========================
    type_dep = 0

    # 1a. 威力效率
    if not is_pure_status:
        base = min(power / POWER_PER_PT, MAX_POWER)
        eff_cost = max(cost, 0.5)
        eff = power / eff_cost
        eff_bonus = min(max(0, (eff - 20) * 0.08), EFF_BONUS_CAP)
        zero_bonus = 6 if cost == 0 else 0
        pe = round(base + eff_bonus + zero_bonus, 1)
        pts["威力效率"] = pe
        type_dep += pe

    # 1b. 先手/迅捷
    if "迅捷" in desc:
        v = SWIFT_ATTACK if not is_pure_status else SWIFT_DEFENSE
        pts["迅捷(攻)" if not is_pure_status else "迅捷(防)"] = v
        type_dep += v
    elif "先手" in desc:
        n = _find_int(r"先手\+(\d+)", desc) or 1
        v = PRIORITY(n)
        pts[f"先手+{n}"] = v; type_dep += v

    # 1c. 应对
    for key in ["打断应对", "应防", "应态", "应攻", "应翻倍", "应冻结"]:
        conds = {
            "打断应对": lambda: "应对" in desc and "打断" in desc,
            "应防": lambda: "应对防御" in desc,
            "应态": lambda: "应对状态" in desc,
            "应攻": lambda: "应对攻击" in desc,
            "应翻倍": lambda: "应对" in desc and ("翻倍" in desc or "变为1.5倍" in desc or "变为2倍" in desc),
            "应冻结": lambda: "应对" in desc and "冻结" in desc and ("翻倍" in desc or "额外" in desc),
        }
        if conds[key]():
            pts[key] = COUNTER[key]; type_dep += COUNTER[key]

    # ========================
    # 分组2: 通用效果
    # ========================
    universal = 0

    # 2a. 印记
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
    found_mark = False
    for mk_name, base, pat in mark_config:
        if mk_name in desc:
            layers = max(1, _find_int(pat, desc))
            v = base + (layers - 1) * MARK_LAYER
            pts[mk_name] = v; universal += v
            found_mark = True; break

    if not found_mark:
        if "星陨" in desc:
            layers = max(1, _find_int(r"(\d+)层星陨", desc))
            v = MARK["星陨"] + (layers - 1) * MARK_LAYER
            pts["星陨印记"] = v; universal += v
        elif "中毒印记" in desc or ("转化为印记" in desc and "中毒" in desc):
            pts["中毒印记"] = MARK["中毒印"]; universal += MARK["中毒印"]
        elif "印记" in desc and "驱散" not in desc and "焚毁" not in name and "食腐" not in name:
            layers = _find_int(r"(\d+)层.{1,3}印记", desc)
            if layers > 0:
                v = 14 + (layers - 1) * MARK_LAYER
                pts["印记(通用)"] = v; universal += v

    # 2b. 印记驱散
    if "焚烧烙印" in full_text: pts["驱散+转化"] = ANTI_MARK["焚烧烙印"]; universal += ANTI_MARK["焚烧烙印"]
    elif "食腐" in full_text and "驱散" in desc: pts["驱散+回血"] = ANTI_MARK["食腐"]; universal += ANTI_MARK["食腐"]
    elif "焚毁" in full_text: pts["驱散印记"] = ANTI_MARK["焚毁"]; universal += ANTI_MARK["焚毁"]
    elif "心灵洞悉" in full_text: pts["印记反制"] = ANTI_MARK["心灵洞悉"]; universal += ANTI_MARK["心灵洞悉"]
    elif "驱散" in desc and "印记" in desc: pts["驱散印记"] = ANTI_MARK["驱散通用"]; universal += ANTI_MARK["驱散通用"]

    # 2c. 减益
    for kw, per_layer in DEBUFF_LAYER.items():
        if kw in desc:
            layers = max(1, _find_int(rf"(\d+)层{kw}", desc))
            pts[kw] = layers * per_layer; universal += layers * per_layer
    for kw, v in DEBUFF_FLAT.items():
        if kw in desc: pts[kw] = v; universal += v

    # 2d. 通用增减益
    CF = 0.6
    if "敌方" in desc:
        if ("物防" in desc and "魔防" in desc) or "双防" in desc:
            pts["敌双防-"] = int(6*CF); universal += int(6*CF)
        elif "物防" in desc or "魔防" in desc:
            pts["敌单防-"] = int(4*CF); universal += int(4*CF)
        if ("物攻" in desc and "魔攻" in desc) or "双攻" in desc:
            pts["敌双攻-"] = int(5*CF); universal += int(5*CF)
        elif "物攻" in desc or "魔攻" in desc:
            pts["敌单攻-"] = int(3*CF); universal += int(3*CF)
        if "速度" in desc and "降低" in desc:
            pts["敌速-"] = 4; universal += 4

    if "自己" in desc or "自身" in desc:
        bv = 0
        # 攻击增益
        if "物攻" in desc or "魔攻" in desc or "双攻" in desc:
            if _find_int(r"物攻\+(\d+)%", desc) >= 100 or _find_int(r"魔攻\+(\d+)%", desc) >= 100:
                bv += 4
            elif _find_int(r"双攻\+(\d+)%", desc) >= 50:
                bv += 4
            elif _find_int(r"物攻\+(\d+)%", desc) >= 50 or _find_int(r"魔攻\+(\d+)%", desc) >= 50:
                bv += 2  # 单攻+50~99%
        # 速度增益
        if re.search(r"速度\+", desc): bv += 3
        # 防御增益
        if re.search(r"(?:物防|魔防|双防)\+", desc) and "-\d" not in desc:
            bv += 3
        if bv > 0: pts["自增益"] = bv; universal += bv

    # 2e. 特殊机制
    # 速度威力(闪击类)
    if not is_pure_status and re.search(r"速度.*越高.*威力|威力.*速度", desc):
        pts["速度威力"] = MECH["速度威力"]; universal += MECH["速度威力"]

    # 多系联动(折射类) - 攻击/状态都可能
    if "携带其他系别技能" in desc:
        pts["多系联动"] = MECH["多系联动"]; universal += MECH["多系联动"]

    if "吸血" in desc:
        pct = _find_int(r"吸血(\d+)%", desc)
        pts["吸血"] = MECH["吸血50"] if pct >= 50 else MECH["吸血"]; universal += pts["吸血"]

    if "传动" in desc:
        layers = _find_int(r"传动(\d+)", desc) or 1
        val = MECH["传动"] * layers
        pts["传动"] = val; universal += val
    for kw in ["迸发", "奉献"]:
        if kw in desc: pts[kw] = MECH[kw]; universal += MECH[kw]

    if "连击数" in desc:
        n = _find_int(r"连击数\+(\d+)", desc) or _find_int(r"连击数.(\d+)", desc)
        k = "连击数++" if n >= 3 else "连击数+"
        pts["连击数+"] = MECH[k]; universal += MECH[k]

    if "连击" in desc and "连击数" not in desc:
        n = _find_int(r"(\d+)连击", desc)
        if n >= 3: pts[f"{n}连击"] = MECH["3连击"]; universal += MECH["3连击"]
        elif n >= 2: pts[f"{n}连击"] = MECH["2连击"]; universal += MECH["2连击"]

    has_escape = False
    if "脱离" in desc:
        if "紧急" in desc: pts["紧急脱离"] = MECH["紧急脱离"]; universal += MECH["紧急脱离"]
        elif "敌" in desc: pts["敌脱离"] = MECH["敌脱离"]; universal += MECH["敌脱离"]
        else: pts["脱离"] = MECH["脱离"]; universal += MECH["脱离"]; has_escape = True

    # 攻击+脱离/防御+脱离 combo
    if has_escape and not is_pure_status:
        pts["攻+脱离"] = MECH["攻+脱离"]; universal += MECH["攻+脱离"]
    if has_escape and is_pure_status and "减伤" in desc:
        pts["防+脱离"] = MECH["防+脱离"]; universal += MECH["防+脱离"]

    if "返场" in desc: pts["返场"] = MECH["返场"]; universal += MECH["返场"]
    if "聒噪" in desc or "全攻击技能能耗" in desc: pts["聒噪"] = MECH["聒噪"]; universal += MECH["聒噪"]

    if "失去" in desc and "能量" in desc and "敌方" in desc:
        e = _find_int(r"失去(\d+)能量", desc)
        pts["烧能量"] = MECH["烧能量6+" if e >= 6 else "烧能量"]; universal += pts["烧能量"]

    if "回复" in desc and "能量" in desc and "敌方" not in desc:
        e = _find_int(r"回复(\d+)能量", desc)
        pts["回能"] = MECH["回能4+" if e >= 4 else "回能"]; universal += pts["回能"]

    if "回复" in desc and ("HP" in desc or "生命" in desc) and "敌方" not in desc and "吸血" not in desc:
        pct = _find_int(r"回复(\d+)%", desc)
        pts["回血"] = MECH["回血40+" if pct >= 40 else "回血"]; universal += pts["回血"]

    # 2f. 减伤
    if "减伤" in desc:
        pct = _find_int(r"减伤(\d+)%", desc)
        if pct >= 70: pts["减伤70%+"] = DEFENSE["减伤70+"]; universal += DEFENSE["减伤70+"]
        elif pct >= 60: pts["减伤60%"] = DEFENSE["减伤60"]; universal += DEFENSE["减伤60"]
        else: pts["减伤"] = DEFENSE["减伤"]; universal += DEFENSE["减伤"]

    # 2g. 成长
    if "威力永久" in desc:
        pw = _find_int(r"威力永久\+(\d+)", desc) or _find_int(r"永久\+(\d+)", desc)
        if pw > 0: pts["威力成长"] = int(pw*GROWTH["power"]); universal += int(pw*GROWTH["power"])
    elif "威力永久翻倍" in desc: pts["威力翻倍成长"] = 14; universal += 14

    if "连击数永久" in desc:
        cg = _find_int(r"连击数永久\+(\d+)", desc)
        if cg > 0: pts["连击成长"] = cg * GROWTH["combo"]; universal += cg * GROWTH["combo"]

    if "能耗永久" in desc:
        cr = _find_int(r"能耗永久-(\d+)", desc)
        if cr >= 6: pts["能耗-6+"] = GROWTH["cost6+"]; universal += GROWTH["cost6+"]
        elif cr >= 4: pts["能耗-4+"] = GROWTH["cost4+"]; universal += GROWTH["cost4+"]
        elif cr >= 3: pts["能耗-3"] = GROWTH["cost3"]; universal += GROWTH["cost3"]
        elif cr >= 1: pts["能耗-1~2"] = GROWTH["cost1-2"]; universal += GROWTH["cost1-2"]

    # 2h. 纯状态技能特殊效果
    if is_pure_status:
        sp = 0
        D = desc  # shorthand

        # 技能复制/变换
        if "随机变成" in D:
            if "敌方" in D: sp += MECH["技能复制"]; pts["变敌方技能"] = MECH["技能复制"]
            elif "己方" in D: sp += MECH["技能复制"]; pts["变队友技能"] = MECH["技能复制"]
            else: sp += MECH["技能复制"]; pts["变技能"] = MECH["技能复制"]

        # 偷取/回复能量
        if "偷取" in D and "能量" in D: sp += MECH["偷能量"]; pts["偷能量"] = MECH["偷能量"]
        if re.search(r"下次.*能耗", D): sp += MECH["下次减费"]; pts["下次减费"] = MECH["下次减费"]

        # 敌方技能加费
        if "敌方" in D and re.search(r"技能能耗\+", D): sp += MECH["敌加费"]; pts["敌加费"] = MECH["敌加费"]
        if "敌方" in D and re.search(r"全技能能耗\+", D): sp += MECH["敌加费"]; pts["敌加费"] = MECH["敌加费"]

        # 清除减益/增益
        if re.search(r"驱散.*(?:减益|自己)", D): sp += MECH["清减益"]; pts["清减益"] = MECH["清减益"]
        if re.search(r"驱散.*(?:增益|敌方|所有)", D): sp += MECH["清增益"]; pts["清增益"] = MECH["清增益"]

        # 天气
        if re.search(r"天气|沙暴|暴风雪|雨天", D): sp += MECH["天气"]; pts["天气"] = MECH["天气"]

        # 翻倍减益/增益
        if re.search(r"减益.*翻倍|层数翻倍", D): sp += MECH["翻倍减益"]; pts["翻倍减益"] = MECH["翻倍减益"]
        if re.search(r"增益翻倍", D): sp += MECH["翻倍增益"]; pts["翻倍增益"] = MECH["翻倍增益"]

        # 增益→中毒
        if re.search(r"增益.*转化", D) and "中毒" in D: sp += MECH["转化增益"]; pts["转化增益"] = MECH["转化增益"]

        # 交换类
        if re.search(r"交换.*生命", D): sp += MECH["交换HP"]; pts["交换HP"] = MECH["交换HP"]
        if re.search(r"交换.*增益", D): sp += MECH["交换增益减益"]; pts["交换增益减益"] = MECH["交换增益减益"]
        if re.search(r"交换.*技能", D): sp += MECH["交换技能"]; pts["交换技能"] = MECH["交换技能"]

        # 继承/传递
        if re.search(r"继承.*增益", D): sp += MECH["继承增益"]; pts["继承增益"] = MECH["继承增益"]
        if re.search(r"被动.*能耗|两侧技能能耗", D): sp += MECH["被动减费"]; pts["被动减费"] = MECH["被动减费"]

        # 全队效果
        if "场下" in D and "回复" in D: sp += MECH["全队回能"]; pts["全队回能"] = MECH["全队回能"]

        # 技能威力调整 (力量吞噬/提气/漫反射等)
        if re.search(r"技能威力[+-]", D) and "敌方" not in D:
            sp += MECH["随机强化"]; pts["技能强化"] = MECH["随机强化"]
        if re.search(r"威力\+[3-9]\d", D) and "敌方" not in D:
            sp += MECH["随机强化"]; pts["技能强化"] = MECH["随机强化"]

        # 下回合强化
        if "下一次" in D and "威力" in D: sp += 5; pts["下回合强化"] = 5

        # 冷却锁定
        if "冷却" in D and "应对" in D: sp += MECH["冷却锁定"]; pts["冷却锁定"] = MECH["冷却锁定"]

        # 疾风连袭: 迅捷链
        if re.search(r"释放.*迅捷", D): sp += MECH["迅捷链"]; pts["迅捷链"] = MECH["迅捷链"]

        # 联动装置: 相邻成长
        if "两侧技能.*永久" in D: sp += MECH["相邻成长"]; pts["相邻成长"] = MECH["相邻成长"]

        universal += sp

    # 2i. 惩罚
    penalty = 0
    if "消耗全部生命" in desc: penalty += PENALTY["自杀"]; pts["自杀"] = PENALTY["自杀"]
    if "蓄力" in desc: penalty += PENALTY["蓄力"]; pts["蓄力"] = PENALTY["蓄力"]
    if "对自己" in desc and "造成" in desc: penalty += PENALTY["自伤"]; pts["自伤"] = PENALTY["自伤"]
    if "降低" in desc and ("自己" in desc or "自身" in desc): penalty += PENALTY["自减益"]; pts["自减益"] = PENALTY["自减益"]
    universal += penalty

    # ========================
    # 最终计算
    # ========================
    if is_pure_status:
        adj_tc = 0.85 + tc * 0.15
        type_adj = type_dep * adj_tc
        cost_penalty = cost * COST_PENALTY
        final = type_adj + universal - cost_penalty
        pts["费机会成本"] = -cost_penalty
    else:
        pw_portion = pts.get("威力效率", 0)
        counter_portion = type_dep - pw_portion
        counter_tc = 1.0 + (tc - 1.0) * 0.4
        type_adj = pw_portion * tc + counter_portion * counter_tc
        final = type_adj + universal

    return final, {
        "name": name, "element": element, "category": skill.get("category", ""),
        "cost": cost, "power": power, "desc": desc,
        "type_coef": tc, "type_dep": round(type_dep, 1),
        "type_adj": round(type_adj, 1), "universal": round(universal, 1),
        "final_score": round(final, 1), "points": pts,
        "is_pure_status": is_pure_status,
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
        if score >= 55: tier = "S"
        elif score >= 42: tier = "A"
        elif score >= 28: tier = "B"
        else: tier = "C"
        tiered.append((tier, score, bd))
    return tiered, len(skill_map)


def print_rankings(tiered, total_count):
    lines = []
    lines.append("=" * 130)
    lines.append(f"技能评分 v3 — 等效伤害量化体系 (总计 {total_count} 个技能)")
    lines.append("纯状态技能: 总分=效果价值-费机会成本(cost×3) | 印记按全队总收益量化")
    lines.append("=" * 130)
    tc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    prev_tier = None
    for rank, (tier, score, bd) in enumerate(tiered, 1):
        tc_counts[tier] += 1
        if tier != prev_tier:
            lines.append(f"\n--- {tier} 级 ---"); prev_tier = tier
        pts = bd["points"]
        pts_str = "; ".join(f"{k}={v}" for k, v in pts.items())
        pwr_str = str(bd["power"]) if bd["power"] > 0 else "—"
        tc_str = f"属×{bd['type_coef']:.2f}" if bd['type_coef'] != 1.0 else ""
        lines.append(f"{rank:<5} {tier:<3} {bd['name']:<16} {bd['element']:<4} {bd['category']:<4} "
                     f"费{bd['cost']:<4} 威{pwr_str:<6} {score:>7.1f}  {tc_str}  {pts_str}")
    lines.append(f"\n{'='*130}")
    lines.append(f"层级分布: S={tc_counts['S']} A={tc_counts['A']} B={tc_counts['B']} C={tc_counts['C']}")
    return "\n".join(lines)


if __name__ == "__main__":
    tiered, total = generate_rankings()
    output = print_rankings(tiered, total)
    (DATA_DIR / "all_skill_rankings.txt").write_text(output, encoding="utf-8")
    print(f"已保存 data/all_skill_rankings.txt")

    json_data = [{
        "rank": i+1, "tier": tier, "name": bd["name"], "element": bd["element"],
        "category": bd["category"], "cost": bd["cost"], "power": bd["power"],
        "score": round(score, 1), "type_coef": bd["type_coef"],
        "type_dep": bd["type_dep"], "type_adj": bd["type_adj"],
        "universal": bd["universal"], "is_pure_status": bd["is_pure_status"],
        "points": bd["points"], "desc": bd["desc"],
    } for i, (tier, score, bd) in enumerate(tiered)]

    with open(DATA_DIR / "all_skill_rankings.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"已保存 data/all_skill_rankings.json")

    tc_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for tier, _, _ in tiered: tc_counts[tier] += 1
    print(f"\n层级分布: S={tc_counts['S']} A={tc_counts['A']} B={tc_counts['B']} C={tc_counts['C']}")

    print("\n" + "=" * 110)
    print("Top 30")
    print("=" * 110)
    for rank, (tier, score, bd) in enumerate(tiered[:30], 1):
        pts = bd["points"]
        pts_short = " | ".join(f"{k}:{v}" for k, v in pts.items())
        print(f"{rank:>2}. [{tier}] {bd['name']:<14} {bd['element']}/{bd['category']} "
              f"费{bd['cost']} 威{bd['power']} → {score:.1f} | {pts_short}")
