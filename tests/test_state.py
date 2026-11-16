"""状态机的单元测试。全部不依赖 AstrBot。"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pytest

import kaoyan_script as ks
import kaoyan_state as stmod

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=stmod.CST)


def make(seed: int = 7) -> "stmod.KaoyanState":
    return stmod.KaoyanState.new(T0, rng=random.Random(seed))


def test_exam_date_is_third_saturday_of_december():
    assert stmod.exam_date_for(date(2026, 1, 5)) == date(2026, 12, 19)
    # 考试那天本身还算今年，过了就滚到明年
    assert stmod.exam_date_for(date(2026, 12, 19)) == date(2026, 12, 19)
    assert stmod.exam_date_for(date(2026, 12, 20)) == date(2027, 12, 18)


def test_new_state_has_all_subjects():
    st = make()
    assert set(st.subjects) == {s.key for s in ks.SUBJECTS}
    assert 0 < st.overall_progress() < 100
    assert st.days_left(T0) > 0


def test_tick_is_idempotent():
    st = make()
    st.tick(T0 + timedelta(minutes=30))
    snapshot = (st.stress, st.energy, round(st.today.minutes, 2))
    st.tick(T0 + timedelta(minutes=30))
    assert (st.stress, st.energy, round(st.today.minutes, 2)) == snapshot


def test_tick_accumulates_study_minutes():
    st = make()
    st.tick(T0 + timedelta(hours=3))  # 8:00 -> 11:00 全在上午自习
    assert st.today.minutes >= 170
    assert st.energy < 80


def test_clock_rollback_is_ignored():
    st = make()
    st.tick(T0 + timedelta(hours=2))
    stress = st.stress
    st.tick(T0)  # 时间倒流
    assert st.stress == stress


def test_breakdown_goes_to_force_then_normal():
    st = make()
    st.trigger_breakdown(T0, "forget")
    assert st.mode == "breakdown"
    assert st.stats["breakdowns"] == 1

    until = datetime.fromisoformat(st.mode_until)
    st.tick(until + timedelta(minutes=1))
    assert st.mode == "force"

    until2 = datetime.fromisoformat(st.mode_until)
    st.tick(until2 + timedelta(minutes=1))
    assert st.mode == "normal"
    assert st.stats["force_backs"] == 1


def test_force_does_not_trigger_another_breakdown():
    st = make()
    st.trigger_breakdown(T0, "wrong")
    st.tick(datetime.fromisoformat(st.mode_until) + timedelta(minutes=1))
    assert st.mode == "force"
    st.stress = 99
    st._maybe_breakdown(T0 + timedelta(hours=5))
    assert st.mode == "force"  # 硬撑期间不会再崩一次


def test_comfort_ends_breakdown_after_a_while():
    st = make()
    st.trigger_breakdown(T0, "tired")
    early = st.comfort(T0 + timedelta(minutes=5))
    assert early in ks.COMFORT_HARD
    assert st.mode == "breakdown"

    st.trigger_breakdown(T0, "tired")
    late = st.comfort(T0 + timedelta(minutes=30))
    assert late in ks.COMFORT_SOFT
    assert st.mode == "force"


def test_hurt_raises_stress_and_drops_resolve():
    st = make()
    s0, r0 = st.stress, st.resolve
    st.hurt(T0)
    assert st.stress > s0 or st.mode == "breakdown"
    assert st.resolve <= r0


def test_rollover_moves_to_next_day():
    st = make()
    st.tick(T0 + timedelta(hours=2))
    day0 = st.today.date
    st.tick(T0 + timedelta(days=1, hours=1))
    assert st.today.date != day0
    assert st.today.date == (T0 + timedelta(days=1)).date().isoformat()
    assert st.stats["study_days"] >= 1


def test_rollover_clears_breakdown():
    st = make()
    st.trigger_breakdown(T0, "family")
    st.tick(T0 + timedelta(days=1, hours=1))
    assert st.mode != "breakdown"


def test_progress_is_monotonic_and_capped():
    st = make()
    prev = st.overall_progress()
    for i in range(1, 40):
        st.tick(T0 + timedelta(days=i, hours=1))
        cur = st.overall_progress()
        assert cur >= prev - 1e-6
        prev = cur
    assert 0 <= st.overall_progress() <= 100


def test_values_stay_in_range_after_long_run():
    st = make(seed=3)
    t = T0
    for _ in range(60):
        t += timedelta(minutes=90)
        st.tick(t)
        for v in (st.stress, st.energy, st.resolve):
            assert 0 <= v <= 100


def test_save_and_load_roundtrip(tmp_path):
    st = make()
    st.tick(T0 + timedelta(hours=4))
    path = tmp_path / "state.json"
    st.save(path)

    loaded = stmod.KaoyanState.load(path)
    assert loaded.stress == pytest.approx(st.stress, abs=0.2)
    assert loaded.mode == st.mode
    assert loaded.day_index(T0) == st.day_index(T0)
    assert set(loaded.subjects) == set(st.subjects)


def test_corrupt_save_is_replaced(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    st = stmod.load_or_create(path, T0)
    # 存档坏了就重建：二战已经进行了一阵子，不会是第 1 天
    assert st.day_index(T0) > 1


def test_load_fills_missing_subjects():
    st = make()
    data = st.to_dict()
    data["subjects"].pop("surgery")
    restored = stmod.KaoyanState(data)
    assert "surgery" in restored.subjects
