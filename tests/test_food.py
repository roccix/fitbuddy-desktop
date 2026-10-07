import unittest

from fitbuddy_desktop import foodfacts, model

WEETABIX = {
    "id": 1, "name": "Weetabix", "calories": 395, "proteinG": 12, "carbsG": 69, "fatsG": 2,
    "barcode": "5010029000382",
    "ingredients": [{"name": "Weetabix", "quantity": 1, "unitWeightG": 100, "kcalPer100": 395.0,
                     "proteinPer100": 12.0, "carbsPer100": 69.0, "fatsPer100": 2.0}],
}


class GramsTest(unittest.TestCase):
    def test_saved_food_keeps_per_100g_rates(self):
        food = model.View({"savedFoods": [WEETABIX]}, []).meal_presets()[0].foods[0]
        self.assertEqual(100, food.weight)
        half = model.Food.of(food.name, model.scale_ingredients(food.ingredients, 50))
        self.assertEqual((50, 198, 6, 35, 1), (half.weight, half.calories, half.protein, half.carbs, half.fats))

    def test_payload_sends_ingredients_to_the_phone(self):
        food = model.Food.of("Weetabix", model.scale_ingredients(
            model.View({"savedFoods": [WEETABIX]}, []).meal_presets()[0].foods[0].ingredients, 30))
        payload = model.meal_op_payload("Breakfast", 0, [food])["foods"][0]
        self.assertEqual(119, payload["calories"])
        self.assertEqual([{"name": "Weetabix", "quantity": 1, "unitWeightG": 30, "kcalPer100": 395.0,
                           "proteinPer100": 12.0, "carbsPer100": 69.0, "fatsPer100": 2.0}],
                         payload["ingredients"])
        plain = model.meal_op_payload("Snack", 0, [model.Food("Apple", 1.0, 80, 0, 20, 0)])["foods"][0]
        self.assertNotIn("ingredients", plain)

    def test_counted_items_keep_their_count(self):
        eggs = [model.Ingredient("Egg", 2, 60, 140, 12, 1, 10)]
        self.assertEqual((3, 60), (model.scale_ingredients(eggs, 180)[0].quantity, 60))
        self.assertEqual((1, 125), (model.scale_ingredients(eggs, 125)[0].quantity,
                                    model.scale_ingredients(eggs, 125)[0].unit_weight))

    def test_totals_typed_by_hand_become_a_rate(self):
        ing = model.Ingredient.from_totals("Rice", 80, 280, 6, 62, 1)
        self.assertEqual(350, model.Food.of("Rice", model.scale_ingredients([ing], 100)).calories)

    def test_rounding_matches_kotlin(self):
        self.assertEqual(3, model.round_half_up(2.5))
        self.assertEqual(4, model.round_half_up(3.5))


class BarcodeTest(unittest.TestCase):
    def test_saved_food_found_by_barcode(self):
        view = model.View({"savedFoods": [WEETABIX]}, [])
        self.assertEqual("Weetabix", view.food_by_barcode(" 5010029000382\n").name)
        self.assertIsNone(view.food_by_barcode("123"))

    def test_product_uses_serving_size_as_default_weight(self):
        food = foodfacts.product_to_food("1", {
            "product_name": "Fruits Cocco", "brands": "Fage", "serving_size": "150 g",
            "nutriments": {"energy-kcal_100g": 116, "proteins_100g": 7, "carbohydrates_100g": 10,
                           "fat_100g": 5},
        })
        self.assertEqual(("Fruits Cocco · Fage", 150, 174), (food.name, food.weight, food.calories))

    def test_per_serving_only_is_converted_to_per_100g(self):
        food = foodfacts.product_to_food("1", {
            "product_name": "Bar", "serving_size": "22g",
            "nutriments": {"energy-kcal_serving": 88, "proteins_serving": 2.2},
        })
        self.assertAlmostEqual(400, food.ingredients[0].kcal100)
        self.assertEqual((22, 88, 2), (food.weight, food.calories, food.protein))

    def test_misreads_fail_the_check_digit(self):
        from fitbuddy_desktop.scanner import valid_barcode
        self.assertTrue(valid_barcode("5201054058152"))
        self.assertTrue(valid_barcode("96385074"))
        self.assertFalse(valid_barcode("5201054058153"))
        self.assertFalse(valid_barcode("https://example.com"))

    def test_kilojoules_only_are_converted(self):
        food = foodfacts.product_to_food("1", {"product_name": "Crackers", "nutriments": {"energy-kj_100g": 1674}})
        self.assertEqual(400, food.calories)
        food = foodfacts.product_to_food("1", {"product_name": "Crackers", "nutriments": {"energy_100g": "1674"}})
        self.assertEqual(400, food.calories)

    def test_string_nutriments_are_read(self):
        food = foodfacts.product_to_food("1", {"product_name": "Pasta", "nutriments": {
            "energy-kcal_100g": "359", "proteins_100g": "12,5", "carbohydrates_100g": "71", "fat_100g": "1.5"}})
        self.assertEqual((359, 13, 71, 2), (food.calories, food.protein, food.carbs, food.fats))

    def test_name_prefers_italian_and_one_brand(self):
        self.assertEqual("Nutella B-ready · Ferrero", foodfacts.product_name({
            "product_name": "Biscuits Noisettes et Cacao", "product_name_it": "Nutella B-ready",
            "brands": "Ferrero, Nutella biscuits"}))
        self.assertEqual("Nutella", foodfacts.product_name({"product_name": "Nutella", "brands": "Nutella, Ferrero"}))
        self.assertEqual("Barilla", foodfacts.product_name({"product_name": " ", "brands": "Barilla"}))
        self.assertEqual("Packaged food", foodfacts.product_name({}))

    def test_upc_matches_its_ean13_form(self):
        saved = dict(WEETABIX, barcode="049000028911")
        self.assertEqual("Weetabix", model.View({"savedFoods": [saved]}, []).food_by_barcode("0049000028911").name)

    def test_product_without_calories_is_rejected(self):
        with self.assertRaises(foodfacts.ProductNotFound):
            foodfacts.product_to_food("1", {"product_name": "Water", "nutriments": {}})


if __name__ == "__main__":
    unittest.main()
