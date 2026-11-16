"""把状态画成能直接发到 QQ 的文本面板。

排版上只依赖"每行自己完整"，不假设等宽字体对齐——手机 QQ 里中文和方块字符宽度
并不一致，所以不做列对齐，宁可每行短一点。
"""

from __future__ import annotations

from datetime import datetime

import kaoyan_script as ks
import kaoyan_state as st_mod

WEEKDAY = "一二三四五六日"

FULL = "▓"
EMPTY = "░"


def bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    return FULL * filled + EMPTY * (width - filled)


def _dash(v: float, width: int = 10) -> str:
    return bar(v, width)


def _mood_note(st: "st_mod.KaoyanState") -> str:
    m = st.mood()
    if m == "breakdown":
        return "现在什么都不想看"
    if m == "force":
        return "硬撑着，嘴唇都干"
    if m == "anxious":
        return "心里一直有个声音在催"
    if m == "tight":
        return "绷着，但还能走"
    if m == "numb":
        return "累到没什么感觉了"
    if m == "steady":
        return "难得踏实一点"
    return "还行"


def render(st: "st_mod.KaoyanState", now: datetime, detail: bool = True) -> str:
    day = st.day_index(now)
    left = st.days_left(now)
    phase = st.phase(now)
    lines: list[str] = []

    if st.mode == "breakdown":
        lines.append("（她把书推开了）")
    elif st.mode == "force":
        lines.append("（深吸一口气，把书又拉回来）")

    lines.append(
        f"二战 第 {day} 天 · 距初试 {left} 天 · {now.month}月{now.day}日 周{WEEKDAY[now.weekday()]}"
    )
    lines.append(f"{now.strftime('%H:%M')} {phase.label}｜{_mood_note(st)}")

    lines.append("")
    lines.append(f"压力 {_dash(st.stress)} {st.stress:.0f}")
    lines.append(f"精力 {_dash(st.energy)} {st.energy:.0f}")
    lines.append(f"信念 {_dash(st.resolve)} {st.resolve:.0f}")

    lines.append("")
    lines.append(f"西医综合 {st.overall_progress():.1f}%")
    for spec in ks.SUBJECTS:
        s = st.subjects.get(spec.key)
        if not s:
            continue
        name = spec.name
        if spec.weight > 0:
            lines.append(
                f"{name} {bar(s.percent)} {s.percent:.0f}% "
                f"{ks.ROUND_LABEL.get(s.rounds, '')}".rstrip()
            )
        else:
            extra = f"第 {s.done:.0f} {spec.unit}" if spec.key == "politics" else f"{s.done:.0f}/{s.spec.total} {spec.unit}"
            lines.append(f"{name} {bar(s.percent)} {s.percent:.0f}% {extra}")

    lines.append("")
    lines.append(f"今天 {st.study_hours_today():.1f} 小时 · 单词 {st.today.words}")

    if detail:
        entries = st.today.log[-6:]
        if entries:
            lines.append("——")
            for t, text in entries:
                lines.append(f"{t} {text}")

    lines.append("")
    lines.append(f"累计破防 {st.stats.get('breakdowns', 0)} 次 · 把自己劝回去 {st.stats.get('force_backs', 0)} 次")
    return "\n".join(lines)


def render_brief(st: "st_mod.KaoyanState", now: datetime) -> str:
    """一行版，给打卡/状态查询的短回复用。"""
    return (
        f"第 {st.day_index(now)} 天 · 压力 {st.stress:.0f} · "
        f"精力 {st.energy:.0f} · 信念 {st.resolve:.0f} · "
        f"今天 {st.study_hours_today():.1f}h · 西综 {st.overall_progress():.0f}%"
    )


def render_progress(st: "st_mod.KaoyanState") -> str:
    lines = [f"西医综合 {st.overall_progress():.1f}%"]
    for spec in ks.SUBJECTS:
        s = st.subjects.get(spec.key)
        if not s:
            continue
        if spec.weight > 0:
            lines.append(f"{spec.name} {s.done:.1f}/{spec.total} 章 {ks.ROUND_LABEL.get(s.rounds, '')} 错题 {s.errors}")
        elif spec.key == "english":
            lines.append(f"英语 真题 {s.done:.0f}/{spec.total} 套 {ks.ROUND_LABEL.get(s.rounds, '')}")
        else:
            lines.append(f"政治 肖1000 第 {s.done:.0f}/{spec.total} 章 {ks.ROUND_LABEL.get(s.rounds, '')}")
    return "\n".join(lines)
