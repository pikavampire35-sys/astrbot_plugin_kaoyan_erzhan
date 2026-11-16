"""不装 AstrBot 也能跑。用来检查状态机是不是像个活人，顺便生成 README 里的示例。

    python demo.py            # 模拟一天，打印时间线 + 面板
    python demo.py --days 30  # 模拟 30 天，看崩溃频率和进度曲线
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

import kaoyan_panel as panel
import kaoyan_state as stmod

CST = stmod.CST


def day_sim(st, begin: datetime, end: datetime, verbose: bool = True):
    """按 10 分钟步进推进，打印过程中发生的事。"""
    t = begin
    seen = set()
    while t <= end:
        news = st.tick(t)
        for n in news:
            if n not in seen:
                if verbose:
                    print(f"  {t.strftime('%H:%M')} · {n}")
                seen.add(n)
        t += timedelta(minutes=10)
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--date", default="2026-11-16")
    ap.add_argument("--drama", action="store_true", help="17:40 强插一次崩溃，方便看完整过程")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    start = datetime.fromisoformat(args.date).replace(tzinfo=CST)
    st = stmod.KaoyanState.new(start, target="南方某医科大 临床医学专硕（1051）", rng=rng)

    if args.days <= 1:
        print(f"=== {start.date()} · 第 {st.day_index(start)} 天 ===")
        morning = start.replace(hour=5, minute=30)
        night = start.replace(hour=23, minute=59)
        if args.drama:
            t = start.replace(hour=17, minute=40)
            day_sim(st, morning, t)
            for line in st.trigger_breakdown(t, "forget"):
                print(f"  {t.strftime('%H:%M')} · {line}")
            day_sim(st, t, night)
        else:
            day_sim(st, morning, night)
        print()
        print(panel.render(st, start.replace(hour=23, minute=50)))
        print()
        print(panel.render_progress(st))
        return

    total_breaks = 0
    rows = []
    for i in range(args.days):
        d = start + timedelta(days=i)
        before = int(st.stats.get("breakdowns", 0))
        day_sim(st, d.replace(hour=5, minute=30), d.replace(hour=23, minute=59), verbose=False)
        breaks = int(st.stats.get("breakdowns", 0)) - before
        total_breaks += breaks
        rows.append(
            (
                d.date().isoformat(),
                f"{st.stress:5.1f}",
                f"{st.energy:5.1f}",
                f"{st.resolve:5.1f}",
                f"{st.study_hours_today():4.1f}h",
                f"{st.overall_progress():5.1f}%",
                "崩" * breaks or "-",
            )
        )

    print("日期        压力  精力  信念  学习  西综进度  破防")
    for r in rows:
        print("  ".join(r))
    print(f"\n{args.days} 天共破防 {total_breaks} 次，平均 {args.days / max(total_breaks, 1):.1f} 天一次")
    print(panel.render(st, start + timedelta(days=args.days - 1, hours=21)))


if __name__ == "__main__":
    main()
