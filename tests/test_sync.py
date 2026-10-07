import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from fitbuddy_desktop import model
from fitbuddy_desktop.server import client_allowed, make_server
from fitbuddy_desktop.store import Store

TS = model.timestamp_for("2026-10-07", dt.time(12, 30))

SNAPSHOT = {
    "version": 7,
    "exportedAt": 0,
    "profile": {
        "id": 1, "age": 20, "weightKg": 70.0, "heightCm": 175.0, "dailyTargetCalories": 2400,
        "targetProteinG": 150, "targetCarbsG": 250, "targetFatsG": 80, "lastUpdatedTimestamp": 0,
        "nutritionTargetHistory": [
            {"from": "2026-01-01", "kcal": 2200, "proteinG": 140, "carbsG": 230, "fatsG": 70},
            {"from": "2026-10-01", "kcal": 2500, "proteinG": 160, "carbsG": 260, "fatsG": 85},
        ],
    },
    "foodLogs": [{"id": 5, "dishName": "Pranzo", "timestamp": TS, "dateString": "2026-10-07",
                  "calories": 700, "proteinG": 40, "carbsG": 80, "fatsG": 20}],
    "mealFoods": [{"id": 1, "mealLogId": 5, "name": "Pasta", "servings": 1.0, "orderIndex": 0,
                   "calories": 700, "proteinG": 40, "carbsG": 80, "fatsG": 20}],
    "exerciseLogs": [{"id": 3, "activityName": "Corsa", "timestamp": TS, "dateString": "2026-10-07",
                      "caloriesBurned": 300, "durationMinutes": 30}],
    "measurements": [{"id": 9, "timestamp": TS, "dateString": "2026-10-07", "weightKg": 70.5}],
    "mealPresets": [{"id": 1, "name": "Colazione", "calories": 400, "proteinG": 20, "carbsG": 50,
                     "fatsG": 10, "createdAt": 0, "lastUsedAt": 5,
                     "foods": [{"name": "Yogurt", "servings": 1.0, "calories": 400, "proteinG": 20,
                                "carbsG": 50, "fatsG": 10}]}],
    "savedFoods": [],
}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_is_created_once(self):
        first = self.store.config()["token"]
        self.assertEqual(first, self.store.config()["token"])
        self.assertRegex(first, r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")

    def test_ack_and_snapshot_land_together(self):
        op = self.store.enqueue(model.ADD_EXERCISE, exercise={"activityName": "Bici"})
        ops, need = self.store.apply_sync(snapshot_hash="h1")
        self.assertTrue(need)
        self.assertEqual([op["id"]], [o["id"] for o in ops])
        ops, need = self.store.apply_sync(acked=[op["id"]], snapshot=SNAPSHOT, snapshot_hash="h2")
        self.assertFalse(need)
        self.assertEqual([], ops)
        self.assertEqual(SNAPSHOT, self.store.snapshot())
        _, need = self.store.apply_sync(snapshot_hash="h2")
        self.assertFalse(need)
        _, need = self.store.apply_sync(snapshot_hash="other")
        self.assertTrue(need)

    def test_cancel(self):
        op = self.store.enqueue(model.DELETE_MEAL, targetId=5)
        self.assertTrue(self.store.cancel(op["id"]))
        self.assertEqual([], self.store.ops())


class ViewTest(unittest.TestCase):
    def test_day_merges_snapshot_and_pending_ops(self):
        ops = [
            {"id": "a", "type": model.ADD_MEAL,
             "meal": model.meal_op_payload("Cena", TS + 1, [model.Food("Pizza", 1.0, 900, 30, 100, 35)])},
            {"id": "b", "type": model.DELETE_EXERCISE, "targetId": 3},
        ]
        day = model.View(SNAPSHOT, ops).day("2026-10-07")
        self.assertEqual(["Pranzo", "Cena"], [m.name for m in day.meals])
        self.assertEqual("a", day.meals[1].pending_op)
        self.assertEqual(1600, day.eaten)
        self.assertEqual([], day.exercises)
        self.assertEqual(0, day.burned)
        self.assertEqual(2500, day.targets.kcal)
        self.assertEqual(2200, model.View(SNAPSHOT, []).targets_for("2026-09-30").kcal)

    def test_presets(self):
        presets = model.View(SNAPSHOT, []).meal_presets()
        self.assertEqual(["Colazione"], [p.name for p in presets])
        self.assertEqual(400, presets[0].calories)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.server = make_server(self.store, port=0, host="::1")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://[::1]:{self.server.server_address[1]}/api/v1/sync"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, body, token=None):
        token = token or self.store.config()["token"]
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_round_trip(self):
        op = self.store.enqueue(model.ADD_MEASUREMENT, measurement={"timestamp": TS, "weightKg": 70.0})
        reply = self.post({"protocol": 1, "sentAt": 0, "appVersion": "x", "snapshot": SNAPSHOT,
                           "snapshotHash": "h"})
        self.assertEqual([op["id"]], [o["id"] for o in reply["ops"]])
        self.assertFalse(reply["needSnapshot"])
        reply = self.post({"protocol": 1, "sentAt": 0, "appVersion": "x", "ackedOps": [op["id"]],
                           "snapshotHash": "h"})
        self.assertEqual([], reply["ops"])

    def test_token_is_case_and_dash_insensitive(self):
        token = self.store.config()["token"].replace("-", "").lower()
        reply = self.post({"protocol": 1, "sentAt": 0, "appVersion": "x", "snapshotHash": "h"}, token)
        self.assertTrue(reply["needSnapshot"])

    def test_rejects_bad_token_and_protocol(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post({"protocol": 1}, token="WRONG")
        self.assertEqual(401, ctx.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post({"protocol": 99})
        self.assertEqual(409, ctx.exception.code)

    def test_client_allowed(self):
        self.assertTrue(client_allowed("100.101.1.2", allow_lan=False))
        self.assertTrue(client_allowed("::ffff:127.0.0.1", allow_lan=False))
        self.assertFalse(client_allowed("192.168.1.5", allow_lan=False))
        self.assertTrue(client_allowed("192.168.1.5", allow_lan=True))
        self.assertFalse(client_allowed("8.8.8.8", allow_lan=True))


if __name__ == "__main__":
    unittest.main()
