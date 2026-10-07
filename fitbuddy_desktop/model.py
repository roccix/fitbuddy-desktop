"""Read-side view over the phone snapshot merged with ops still queued on the PC."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

ADD_MEAL = "add_meal"
ADD_EXERCISE = "add_exercise"
ADD_MEASUREMENT = "add_measurement"
DELETE_MEAL = "delete_meal"
DELETE_EXERCISE = "delete_exercise"
DELETE_MEASUREMENT = "delete_measurement"


def date_of(timestamp_ms: int) -> str:
    return dt.datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")


def timestamp_for(date: str, time: dt.time) -> int:
    day = dt.date.fromisoformat(date)
    return int(dt.datetime.combine(day, time).timestamp() * 1000)


@dataclass
class Food:
    name: str
    servings: float
    calories: int
    protein: int
    carbs: int
    fats: int


@dataclass
class Meal:
    name: str
    timestamp: int
    calories: int
    protein: int
    carbs: int
    fats: int
    foods: list[Food] = field(default_factory=list)
    id: int | None = None
    pending_op: str | None = None
    pending_delete: str | None = None


@dataclass
class Exercise:
    name: str
    timestamp: int
    calories: int
    minutes: int
    id: int | None = None
    pending_op: str | None = None
    pending_delete: str | None = None


@dataclass
class Measurement:
    timestamp: int
    weight: float
    body_fat: float | None
    id: int | None = None
    pending_op: str | None = None
    pending_delete: str | None = None


@dataclass
class Targets:
    kcal: int
    protein: int
    carbs: int
    fats: int


@dataclass
class Day:
    date: str
    meals: list[Meal]
    exercises: list[Exercise]
    measurements: list[Measurement]
    targets: Targets | None

    def _live(self, rows):
        return [r for r in rows if not r.pending_delete]

    @property
    def eaten(self) -> int:
        return sum(m.calories for m in self._live(self.meals))

    @property
    def burned(self) -> int:
        return sum(e.calories for e in self._live(self.exercises))

    def macro(self, name: str) -> int:
        return sum(getattr(m, name) for m in self._live(self.meals))


@dataclass
class Preset:
    name: str
    foods: list[Food]

    @property
    def calories(self) -> int:
        return sum(f.calories for f in self.foods)


class View:
    def __init__(self, snapshot: dict | None, ops: list[dict]):
        self.snapshot = snapshot or {}
        self.ops = ops
        self._deletes = {
            (op["type"], op.get("targetId")): op["id"]
            for op in ops
            if op["type"] in (DELETE_MEAL, DELETE_EXERCISE, DELETE_MEASUREMENT)
        }

    @property
    def has_data(self) -> bool:
        return bool(self.snapshot)

    @property
    def pending_count(self) -> int:
        return len(self.ops)

    # --- days ------------------------------------------------------------------------------

    def day(self, date: str) -> Day:
        # Deletions made on the PC take effect here immediately, like on the phone.
        return Day(
            date=date,
            meals=sorted((m for m in self._meals(date) if not m.pending_delete), key=lambda m: m.timestamp),
            exercises=sorted((e for e in self._exercises(date) if not e.pending_delete),
                             key=lambda e: e.timestamp),
            measurements=sorted(self._measurements(date), key=lambda m: m.timestamp),
            targets=self.targets_for(date),
        )

    def _meals(self, date: str) -> list[Meal]:
        foods_by_meal: dict[int, list[dict]] = {}
        for row in self.snapshot.get("mealFoods", []):
            foods_by_meal.setdefault(row["mealLogId"], []).append(row)
        meals = []
        for log in self.snapshot.get("foodLogs", []):
            if log["dateString"] != date:
                continue
            rows = sorted(foods_by_meal.get(log["id"], []), key=lambda r: r.get("orderIndex", 0))
            meals.append(Meal(
                name=log["dishName"], timestamp=log["timestamp"], calories=log["calories"],
                protein=log["proteinG"], carbs=log["carbsG"], fats=log["fatsG"],
                foods=[_food(r) for r in rows], id=log["id"],
                pending_delete=self._deletes.get((DELETE_MEAL, log["id"])),
            ))
        for op in self.ops:
            meal = op.get("meal")
            if op["type"] != ADD_MEAL or not meal or date_of(meal["timestamp"]) != date:
                continue
            foods = [_food(f) for f in meal["foods"]]
            meals.append(Meal(
                name=meal["dishName"], timestamp=meal["timestamp"],
                calories=sum(f.calories for f in foods), protein=sum(f.protein for f in foods),
                carbs=sum(f.carbs for f in foods), fats=sum(f.fats for f in foods),
                foods=foods, pending_op=op["id"],
            ))
        return meals

    def _exercises(self, date: str) -> list[Exercise]:
        rows = [
            Exercise(
                name=log["activityName"], timestamp=log["timestamp"], calories=log["caloriesBurned"],
                minutes=log["durationMinutes"], id=log["id"],
                pending_delete=self._deletes.get((DELETE_EXERCISE, log["id"])),
            )
            for log in self.snapshot.get("exerciseLogs", [])
            if log["dateString"] == date
        ]
        for op in self.ops:
            ex = op.get("exercise")
            if op["type"] == ADD_EXERCISE and ex and date_of(ex["timestamp"]) == date:
                rows.append(Exercise(
                    name=ex["activityName"], timestamp=ex["timestamp"], calories=ex["caloriesBurned"],
                    minutes=ex["durationMinutes"], pending_op=op["id"],
                ))
        return rows

    def _measurements(self, date: str) -> list[Measurement]:
        return [m for m in self.all_measurements() if date_of(m.timestamp) == date]

    def all_measurements(self) -> list[Measurement]:
        rows = [
            Measurement(
                timestamp=m["timestamp"], weight=m["weightKg"], body_fat=m.get("bodyFatPct"),
                id=m["id"], pending_delete=self._deletes.get((DELETE_MEASUREMENT, m["id"])),
            )
            for m in self.snapshot.get("measurements", [])
        ]
        for op in self.ops:
            m = op.get("measurement")
            if op["type"] == ADD_MEASUREMENT and m:
                rows.append(Measurement(
                    timestamp=m["timestamp"], weight=m["weightKg"], body_fat=m.get("bodyFatPct"),
                    pending_op=op["id"],
                ))
        return sorted((m for m in rows if not m.pending_delete), key=lambda m: m.timestamp)

    def latest_weight(self) -> Measurement | None:
        rows = self.all_measurements()
        return rows[-1] if rows else None

    # --- targets / history -----------------------------------------------------------------

    def targets_for(self, date: str) -> Targets | None:
        profile = self.snapshot.get("profile")
        if not profile:
            return None
        history = sorted(profile.get("nutritionTargetHistory") or [], key=lambda p: p["from"])
        match = next((p for p in reversed(history) if p["from"] <= date), None)
        if match:
            return Targets(match["kcal"], match["proteinG"], match["carbsG"], match["fatsG"])
        return Targets(
            profile["dailyTargetCalories"], profile["targetProteinG"],
            profile["targetCarbsG"], profile["targetFatsG"],
        )

    def history(self, end: str, days: int) -> list[Day]:
        last = dt.date.fromisoformat(end)
        return [self.day((last - dt.timedelta(days=i)).isoformat()) for i in range(days)]

    # --- libraries -------------------------------------------------------------------------

    def meal_presets(self) -> list[Preset]:
        presets = []
        for p in self.snapshot.get("mealPresets", []):
            foods = [_food(f) for f in p.get("foods") or []]
            if not foods:
                foods = [Food(p["name"], 1.0, p["calories"], p["proteinG"], p["carbsG"], p["fatsG"])]
            presets.append((p.get("lastUsedAt", 0), Preset(p["name"], foods)))
        for f in self.snapshot.get("savedFoods", []):
            food = Food(f["name"], 1.0, f["calories"], f["proteinG"], f["carbsG"], f["fatsG"])
            presets.append((f.get("lastUsedAt", 0), Preset(f["name"], [food])))
        presets.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [p for _, p in presets]

    def recent_exercises(self, limit: int = 30) -> list[str]:
        names: list[str] = []
        for log in sorted(self.snapshot.get("exerciseLogs", []), key=lambda r: -r["timestamp"]):
            if log["activityName"] not in names:
                names.append(log["activityName"])
        return names[:limit]


def _food(row: dict) -> Food:
    return Food(
        name=row["name"], servings=row.get("servings", 1.0), calories=row["calories"],
        protein=row["proteinG"], carbs=row["carbsG"], fats=row["fatsG"],
    )


def meal_op_payload(name: str, timestamp: int, foods: list[Food]) -> dict:
    return {
        "dishName": name,
        "timestamp": timestamp,
        "foods": [
            {
                "name": f.name, "servings": f.servings, "calories": f.calories,
                "proteinG": f.protein, "carbsG": f.carbs, "fatsG": f.fats,
            }
            for f in foods
        ],
    }
