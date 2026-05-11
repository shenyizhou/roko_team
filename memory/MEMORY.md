# Project Patterns

## Key Architecture
- `data/spirit_filter_index.json` — single source of truth for all pet data
- `models/__init__.py` — data access layer, all pets/skills loaded from filter index
- `scripts/score_all_skills.py` — skill scoring engine
- `scripts/score_traits_and_rank.py` — pet scoring + team building
- `data/_boss_info.json` — remove list for intermediate evolutions (matched by base name prefix)

## Region Form Skill Inheritance
Region forms (typeClass=region) inherit skills from same-NO final forms (typeClass=final).
e.g., 霜翼领主 → 岚鸟, 蹦蹦果 → 蹦蹦花
Mapping maintained in `_get_region_base_map()` in models/__init__.py.
Name matching uses parenthetical suffix preservation (e.g., "霜翼领主（本来的样子）" → "岚鸟（本来的样子）").

## Trait Scoring
- `TRAIT_SCORES` in score_traits_and_rank.py
- 飓风 trait: -10 only (negative part). 迅捷 value integrated into per-skill scores.
- 疾风连袭: dynamically scored based on other 迅捷 skills via swift_strike_score()

## Attribute Scoring
See `memory/attribute_scoring.md` for details.
Resist: +7*w, Weakness: -5*w, no compression.
Target: ~100 for top defensive types, ~0 for worst.

## Skill Scoring
- 迅捷 bonus formula: attack=16+pv*0.4, defense=18+dv*0.35, status=12+bonuses
- Layer diminishing: per_layer * layers^0.75
- Condition discount: 应对攻击 cd=0.6, 应对状态 cd=0.4
