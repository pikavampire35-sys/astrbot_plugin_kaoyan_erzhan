"""状态机：压力、精力、信念、崩溃与自愈、各科进度、按天滚动。

刻意不依赖 AstrBot，也不依赖任何第三方库，方便单独跑测试和 demo。
所有对外方法都接受 ``now``（带时区的 datetime），不在函数内部偷偷取当前时间。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import kaoyan_script as ks

CST = timezone(timedelta(hours=8))

STATE_VERSION = 1

# 一天里真正在学书的时段（用于累计学习时长）
STUDY_PHASES = frozenset({"am_study", "pm_study", "night_study"})

# 二战的人不会真的"没压力"，再平静也压着一条底
STRESS_FLOOR = 42.0

BREAKDOWN_AT = 82.0  # 到这个值就有可能崩
BREAKDOWN_HARD_AT = 92.0  # 到这儿基本一定要崩

MOOD_LABEL = {
    "steady": "还算稳",
    "normal": "正常",
    "tight": "绷着",
    "anxious": "焦虑",
    "numb": "麻木",
    "breakdown": "破防",
    "force": "硬撑",
}


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def now_cst() -> datetime:
    return datetime.now(CST)


def exam_date_for(d: date) -> date:
    """当年 12 月的第三个周六，过了就取下一年。

    考研初试基本都在 12 月倒数第二个周末，用"第三个周六"近似足够。
    """
    def third_saturday(year: int) -> date:
        first = date(year, 12, 1)
        offset = (5 - first.weekday()) % 7  # 周六 weekday()==5
        return first + timedelta(days=offset + 14)

    cand = third_saturday(d.year)
    if cand < d:
        cand = third_saturday(d.year + 1)
    return cand


@dataclass
class SubjectState:
    key: str
    done: float
    rounds: int
    errors: int

    @property
    def spec(self) -> ks.SubjectSpec:
        return ks.SUBJECT_BY_KEY[self.key]

    @property
    def percent(self) -> float:
        return clamp(self.done / self.spec.total * 100.0)

    def to_dict(self) -> dict:
        return {"done": round(self.done, 2), "rounds": self.rounds, "errors": self.errors}

    @classmethod
    def from_dict(cls, key: str, d: dict) -> "SubjectState":
        return cls(
            key=key,
            done=float(d.get("done", 0.0)),
            rounds=int(d.get("rounds", 0)),
            errors=int(d.get("errors", 0)),
        )


@dataclass
class DayLog:
    date: str
    minutes: float = 0.0
    words: int = 0
    log: list[list[str]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    rolled: bool = False  # 今天的事件/进度结算做了没

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "minutes": round(self.minutes, 1),
            "words": self.words,
            "log": self.log[-40:],
            "events": self.events,
            "rolled": self.rolled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DayLog":
        return cls(
            date=d.get("date", ""),
            minutes=float(d.get("minutes", 0)),
            words=int(d.get("words", 0)),
            log=[list(x) for x in d.get("log", [])][-40:],
            events=list(d.get("events", [])),
            rolled=bool(d.get("rolled", False)),
        )


class KaoyanState:
    """一天一天活下去的那个她。"""

    def __init__(self, data: dict | None = None, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self.created_at: str = ""
        self.start_date: str = ""
        self.target: str = ks.DEFAULT_TARGET
        self.stress: float = 55.0
        self.energy: float = 80.0
        self.resolve: float = 62.0
        self.mode: str = "normal"  # normal | breakdown | force
        self.mode_since: str = ""
        self.breakdown_reason: str = ""
        self.mode_until: str = ""
        self.last_tick: str = ""
        self.last_check: str = ""
        self.last_phase: str = ""
        self.subjects: dict[str, SubjectState] = {}
        self.today: DayLog = DayLog(date="")
        self.stats: dict = {
            "breakdowns": 0,
            "study_days": 0,
            "total_minutes": 0,
            "force_backs": 0,
        }
        if data:
            self.load_dict(data)

    # ------------------------------------------------------------ 构造

    @classmethod
    def new(
        cls, now: datetime, target: str | None = None, rng: random.Random | None = None
    ) -> "KaoyanState":
        """新建一份存档。二战从当年 3 月开始算起。"""
        self = cls(rng=rng)
        self.created_at = now.isoformat()
        start = now.date() - timedelta(days=238)
        self.start_date = start.isoformat()
        self.target = target or ks.DEFAULT_TARGET
        self.stress = 68.0
        self.energy = 74.0
        self.resolve = 64.0
        self.subjects = {
            key: SubjectState(key, v["done"], v["rounds"], v["errors"])
            for key, v in ks.INITIAL_PROGRESS.items()
        }
        self.today = DayLog(date=now.date().isoformat())
        self.last_tick = now.isoformat()
        self.last_phase = self.phase(now).key
        self.add_log(now, f"第 {self.day_index(now)} 天")
        return self

    # ------------------------------------------------------------ 存档

    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "created_at": self.created_at,
            "start_date": self.start_date,
            "target": self.target,
            "stress": round(self.stress, 1),
            "energy": round(self.energy, 1),
            "resolve": round(self.resolve, 1),
            "mode": self.mode,
            "mode_since": self.mode_since,
            "mode_until": self.mode_until,
            "breakdown_reason": self.breakdown_reason,
            "last_tick": self.last_tick,
            "last_check": self.last_check,
            "last_phase": self.last_phase,
            "subjects": {k: v.to_dict() for k, v in self.subjects.items()},
            "today": self.today.to_dict(),
            "stats": self.stats,
        }

    def load_dict(self, d: dict) -> None:
        self.created_at = d.get("created_at", "")
        self.start_date = d.get("start_date", "")
        self.target = d.get("target", ks.DEFAULT_TARGET)
        self.stress = float(d.get("stress", 55.0))
        self.energy = float(d.get("energy", 80.0))
        self.resolve = float(d.get("resolve", 62.0))
        self.mode = d.get("mode", "normal")
        self.mode_since = d.get("mode_since", "")
        self.mode_until = d.get("mode_until", "")
        self.breakdown_reason = d.get("breakdown_reason", "")
        self.last_tick = d.get("last_tick", "")
        self.last_check = d.get("last_check", "")
        self.last_phase = d.get("last_phase", "")
        self.subjects = {
            s.key: SubjectState.from_dict(s.key, d.get("subjects", {}).get(s.key, {}))
            for s in ks.SUBJECTS
        }
        for key, v in ks.INITIAL_PROGRESS.items():
            # 老存档缺科目时补上，避免面板 KeyError
            self.subjects.setdefault(
                key, SubjectState(key, v["done"], v["rounds"], v["errors"])
            )
        self.today = DayLog.from_dict(d.get("today", {"date": ""}))
        self.stats = {**self.stats, **(d.get("stats") or {})}

    @classmethod
    def load(cls, path: Path, rng: random.Random | None = None) -> "KaoyanState":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data, rng=rng)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    # ------------------------------------------------------------ 时间与阶段

    def phase(self, now: datetime) -> ks.Phase:
        return ks.phase_at(now.hour * 60 + now.minute)

    def day_index(self, now: datetime) -> int:
        try:
            start = date.fromisoformat(self.start_date)
        except ValueError:
            return 1
        return (now.date() - start).days + 1

    def days_left(self, now: datetime) -> int:
        return (exam_date_for(now.date()) - now.date()).days

    def add_log(self, now: datetime, text: str) -> None:
        if self.today.date != now.date().isoformat():
            self.today = DayLog(date=now.date().isoformat())
        self.today.log.append([now.strftime("%H:%M"), text])
        if len(self.today.log) > 40:
            self.today.log = self.today.log[-40:]

    # ------------------------------------------------------------ 主循环

    def tick(self, now: datetime) -> list[str]:
        """把状态推进到 now。返回这段时间里发生的事（用于日志/主动消息）。

        幂等：同一时刻连续调用不会重复推进。
        """
        news: list[str] = []
        if self.today.date != now.date().isoformat():
            news.extend(self._rollover(now))

        try:
            last = datetime.fromisoformat(self.last_tick)
        except ValueError:
            self.last_tick = now.isoformat()
            return news
        if now <= last:
            if now < last:  # 时钟被回拨了，别去补算
                self.last_tick = now.isoformat()
            news.extend(self._check_mode(now))
            return news

        # 分段推进：一次可能跨过好几个阶段，不能拿"当前阶段"去算整段时间
        cursor = last
        guard = 0
        while cursor < now and guard < 200:
            guard += 1
            step = min(now, cursor + timedelta(minutes=15))
            minutes = (step - cursor).total_seconds() / 60.0
            if minutes > 0:
                self._advance(cursor, minutes)
            cursor = step
        if cursor < now:  # 断电很久回来，剩下的粗推进一下
            self._advance(now, min((now - cursor).total_seconds() / 60.0, 24 * 60))

        phase = self.phase(now)
        prev_phase = self.last_phase
        self.last_phase = phase.key
        self.last_tick = now.isoformat()
        if prev_phase and prev_phase != phase.key:
            news.extend(self._on_phase_change(now, prev_phase, phase))

        news.extend(self._check_mode(now))
        news.extend(self._maybe_breakdown(now))
        return news

    def _advance(self, at: datetime, minutes: float) -> None:
        """按 at 这一刻所处的阶段推进 minutes 分钟。"""
        phase = self.phase(at)
        if phase.key in STUDY_PHASES:
            if self.mode == "normal":
                self.today.minutes += minutes
                # 越接近顶越涨不动：压力有饱和，不会无限叠加
                self.stress += 0.028 * minutes * max(0.12, 1.0 - self.stress / 110.0)
                self.energy -= 0.055 * minutes
            else:
                # 崩着或硬撑时还坐在桌前，但没在真学，体力也耗得慢
                self.today.minutes += minutes * 0.45
                self.stress += 0.002 * minutes
                self.energy -= 0.024 * minutes
        elif phase.key == "sleep":
            self.energy = min(88.0, self.energy + 0.130 * minutes)
            self.stress -= 0.008 * minutes
            self.resolve += 0.002 * minutes
        elif phase.key in ("words", "commute", "review", "back"):
            self.energy -= 0.012 * minutes
            self.stress += 0.002 * minutes
        else:
            self.energy -= 0.016 * minutes

        # 精力见底本身就加压力；信念高则压得住
        if self.energy < 30:
            self.stress += 0.006 * minutes

        self.resolve = clamp(self.resolve - 0.0012 * minutes, 0.0, 80.0)
        self.energy = clamp(self.energy)
        if phase.key == "sleep":
            self.stress = clamp(self.stress)
        else:
            self.stress = clamp(max(self.stress, STRESS_FLOOR))

    def _on_phase_change(self, now: datetime, old: str, phase: ks.Phase) -> list[str]:
        news: list[str] = []
        if phase.key in ("am_study", "pm_study", "night_study") and not self.in_breakdown(now):
            self.add_log(now, f"{phase.label}开始")
        if phase.key == "words":
            add = self._rng.randint(360, 440)
            self.today.words += add
            self.add_log(now, f"单词 {add}")
            news.append(f"背完了今天份的单词（{add} 个）")
        if phase.key == "review":
            h = self.today.minutes / 60
            self.add_log(now, f"今天学了 {h:.1f} 小时")
        return news

    # ------------------------------------------------------------ 崩溃 / 自愈

    def in_breakdown(self, now: datetime | None = None) -> bool:
        return self.mode == "breakdown"

    def mood(self) -> str:
        if self.mode == "breakdown":
            return "breakdown"
        if self.mode == "force":
            return "force"
        if self.stress >= 76:
            return "anxious"
        if self.stress >= 60:
            return "tight"
        if self.energy <= 26:
            return "numb"
        if self.stress <= 46 and self.resolve >= 70:
            return "steady"
        return "normal"

    def mood_label(self) -> str:
        return MOOD_LABEL.get(self.mood(), "正常")

    def _maybe_breakdown(self, now: datetime) -> list[str]:
        """隔一段时间判一次，避免每分钟都掷骰子。"""
        try:
            last = datetime.fromisoformat(self.last_check)
        except ValueError:
            last = now - timedelta(hours=2)
        if (now - last).total_seconds() < 100 * 60:
            return []
        self.last_check = now.isoformat()
        if self.mode != "normal":
            return []

        phase = self.phase(now)
        p = 0.0
        if self.stress >= BREAKDOWN_HARD_AT:
            p = 0.32
        elif self.stress >= BREAKDOWN_AT:
            p = 0.05 + max(0.0, 60.0 - self.resolve) / 400.0
        if phase.key == "dusk":
            p += 0.05  # 天黑那阵最容易崩
        if self.energy < 25:
            p += 0.05
        if p <= 0 or self._rng.random() > min(p, 0.95):
            return []

        reason = self._rng.choice(["forget", "wrong", "tired", "score"])
        if self._rng.random() < 0.25:
            reason = self._rng.choice(["family", "peer"])
        return self.trigger_breakdown(now, reason)

    def trigger_breakdown(self, now: datetime, reason: str) -> list[str]:
        if self.mode == "breakdown":
            return []
        self.mode = "breakdown"
        self.mode_since = now.isoformat()
        minutes = self._rng.randint(60, 200)
        self.mode_until = (now + timedelta(minutes=minutes)).isoformat()
        self.breakdown_reason = reason
        self.stats["breakdowns"] = int(self.stats.get("breakdowns", 0)) + 1
        self.stress = clamp(self.stress + 6)
        self.resolve = clamp(self.resolve - 5)
        line = self._rng.choice(ks.BREAKDOWN_LINES.get(reason, ks.BREAKDOWN_LINES["tired"]))
        self.add_log(now, f"崩了：{line}")
        return [f"你破防了：{line}"]

    def comfort(self, now: datetime) -> str | None:
        """被安慰。崩溃中能提前结束，否则小幅降压。返回一句回话。"""
        if self.mode == "breakdown":
            try:
                since = datetime.fromisoformat(self.mode_since)
            except ValueError:
                since = now
            minutes = (now - since).total_seconds() / 60
            self.stress = clamp(self.stress - 12)
            self.resolve = clamp(self.resolve + 6)
            if minutes < 25:
                return self._rng.choice(ks.COMFORT_HARD)
            # 崩了有一会儿了，能听进去
            self._to_force(now, because="comfort")
            return self._rng.choice(ks.COMFORT_SOFT)
        self.stress = clamp(self.stress - 4)
        self.resolve = clamp(self.resolve + 2)
        return None

    def hurt(self, now: datetime) -> str | None:
        """被戳了一下。"""
        self.stress = clamp(self.stress + 9)
        self.resolve = clamp(self.resolve - 4)
        if self.stress >= BREAKDOWN_AT and self.mode == "normal" and self._rng.random() < 0.5:
            self.trigger_breakdown(now, "wrong")
            return self._rng.choice(ks.BREAKDOWN_LINES["wrong"])
        return None

    def _to_force(self, now: datetime, because: str) -> None:
        self.mode = "force"
        self.mode_since = now.isoformat()
        self.mode_until = (now + timedelta(minutes=self._rng.randint(35, 80))).isoformat()
        self.stats["force_backs"] = int(self.stats.get("force_backs", 0)) + 1
        pool = ks.FORCE_LINES.get(because) or ks.FORCE_LINES["self"]
        self.add_log(now, f"逼自己：{self._rng.choice(pool)}")

    def _check_mode(self, now: datetime) -> list[str]:
        if self.mode == "normal":
            return []
        if not self.mode_until:  # 存档坏了，别卡在崩溃里
            self.mode = "normal"
            return []
        try:
            until = datetime.fromisoformat(self.mode_until)
        except ValueError:
            self.mode = "normal"
            return []
        if now < until:
            return []
        if self.mode == "breakdown":
            self._to_force(now, because=self._force_reason())
            return ["她从崩溃里爬起来了，但还在硬撑"]
        # force 结束，回到能学但还很累的状态
        self.mode = "normal"
        self.mode_until = ""
        self.stress = clamp(self.stress - 10)
        self.resolve = clamp(self.resolve + 5)
        self.energy = clamp(self.energy - 3)
        self.add_log(now, "回去看书了")
        return ["她把书翻开了"]

    def _force_reason(self) -> str:
        if self.breakdown_reason in ("family", "peer"):
            return "escape"
        return self._rng.choice(["gap", "sunk", "mother", "self"])

    # ------------------------------------------------------------ 按天结算

    def _rollover(self, now: datetime) -> list[str]:
        news: list[str] = []
        prev = self.today
        if prev.date:
            self.stats["total_minutes"] = int(self.stats.get("total_minutes", 0)) + int(prev.minutes)
            if prev.minutes >= 60:
                self.stats["study_days"] = int(self.stats.get("study_days", 0)) + 1
        # 睡了一夜，缓过来一些，但底压还在
        self.stress = clamp(max(self.stress - 4, STRESS_FLOOR + 2))
        self.energy = clamp(self.energy + self._rng.uniform(-3, 6))
        self.resolve = clamp(self.resolve + 2, 0.0, 80.0)
        if self.mode == "breakdown":
            self.mode = "normal"
            self.mode_until = ""
            news.append("睡了一觉，昨天那阵劲过去了")
        self.today = DayLog(date=now.date().isoformat())
        self.add_log(now, f"第 {self.day_index(now)} 天")
        news.extend(self._daily_events(now))
        self._advance_subjects()
        self.today.rolled = True
        return news

    def _daily_events(self, now: datetime) -> list[str]:
        """每天 1 个主事件，偶尔两个。"""
        n = 1 if self._rng.random() < 0.78 else 2
        pool = list(ks.EVENTS)
        news: list[str] = []
        for _ in range(n):
            if not pool:
                break
            total = sum(e.weight for e in pool)
            r = self._rng.uniform(0, total)
            acc = 0.0
            picked = pool[-1]
            for e in pool:
                acc += e.weight
                if r <= acc:
                    picked = e
                    break
            pool.remove(picked)
            self.stress = clamp(self.stress + picked.stress)
            self.resolve = clamp(self.resolve + picked.resolve)
            self.energy = clamp(self.energy + picked.energy)
            self.today.events.append(picked.text)
            self.add_log(now, picked.text)
            news.append(picked.text)
            if picked.breakdown and self.mode == "normal" and self.stress >= BREAKDOWN_AT - 6:
                news.extend(self.trigger_breakdown(now, picked.breakdown))
                break
        return news

    def _advance_subjects(self) -> None:
        """一天推进一点。崩过的日子也推进——她还是坐在那儿。"""
        factor = 0.6 if self.mode == "force" else 1.0
        for s in self.subjects.values():
            if s.done >= s.spec.total:
                s.done = float(s.spec.total)
                if s.rounds < 4:
                    s.rounds += 1
                continue
            step = self._rng.uniform(0.06, 0.26) * factor
            if s.spec.weight >= 0.20:  # 内科外科推进慢
                step *= 0.75
            s.done = min(s.spec.total, s.done + step)
            if s.spec.weight > 0:
                s.errors += self._rng.randint(0, 4)

    # ------------------------------------------------------------ 对外读数

    def overall_progress(self) -> float:
        total_w = sum(s.spec.weight for s in self.subjects.values() if s.spec.weight)
        if not total_w:
            return 0.0
        acc = 0.0
        for s in self.subjects.values():
            if s.spec.weight:
                # 轮次也要算进去：三轮比一轮值钱
                acc += s.spec.weight * (s.percent / 100.0)
        return acc / total_w * 100.0

    def study_hours_today(self) -> float:
        return self.today.minutes / 60.0


def load_or_create(path: Path, now: datetime, target: str | None = None) -> KaoyanState:
    if path.exists():
        try:
            return KaoyanState.load(path)
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            pass
    return KaoyanState.new(now, target=target)
