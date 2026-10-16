import unittest
from risk import evaluate_loss


class LossPolicyTests(unittest.TestCase):
    def test_exact_sixty_percent_boundary(self):
        self.assertEqual(evaluate_loss("12000.01")["status"], "BELOW_THRESHOLD")
        self.assertEqual(evaluate_loss("12000")["status"], "TRIGGERED")
        self.assertEqual(evaluate_loss("11999.99")["status"], "TRIGGERED")

    def test_deposit_cannot_mask_existing_loss(self):
        result = evaluate_loss(22000, 10000)
        self.assertEqual(result["status"], "TRIGGERED")
        self.assertEqual(result["threshold_equity"], 22000)

    def test_withdrawal_is_not_a_loss(self):
        result = evaluate_loss(11000, -19000)
        self.assertEqual(result["economic_pnl"], 0)
        self.assertEqual(result["status"], "BELOW_THRESHOLD")

    def test_equity_not_cash_balance_is_used(self):
        self.assertEqual(evaluate_loss(12000)["loss_fraction"], 0.6)

    def test_missing_and_nonfinite_inputs_are_unknown(self):
        for value in [None, "", "bad", "NaN", "Infinity", "-Infinity"]:
            with self.subTest(value=value):
                self.assertEqual(evaluate_loss(value)["status"], "UNKNOWN")

    def test_future_and_stale_values_do_not_claim_safety(self):
        for age in [-1, 31, 180]:
            self.assertEqual(evaluate_loss(30000, age_seconds=age)["status"], "STALE")
        self.assertEqual(evaluate_loss(30000, age_seconds=30)["status"], "BELOW_THRESHOLD")

    def test_profit_and_negative_equity(self):
        self.assertEqual(evaluate_loss(40000)["loss_fraction"], 0)
        self.assertEqual(evaluate_loss(-100)["status"], "TRIGGERED")

    def test_no_automatic_execution(self):
        self.assertFalse(evaluate_loss(0)["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
