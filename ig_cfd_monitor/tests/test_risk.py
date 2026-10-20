import unittest
from risk import evaluate_loss


class LossPolicyTests(unittest.TestCase):
    def test_exact_sixty_percent_boundary(self):
        self.assertEqual(evaluate_loss("12000.01")["status"], "BELOW_THRESHOLD")
        self.assertEqual(evaluate_loss("12000")["status"], "TRIGGERED")
        self.assertEqual(evaluate_loss("11999.99")["status"], "TRIGGERED")

    def test_deposit_changes_denominator(self):
        result = evaluate_loss(22000, 40000)
        self.assertEqual(result['status'], 'BELOW_THRESHOLD')
        self.assertEqual(result['threshold_equity'], 16000)

    def test_withdrawal_counts_in_user_capital_ratio(self):
        result = evaluate_loss(11000, 30000)
        self.assertTrue(result['withdrawals_reduce_ratio'])
        self.assertEqual(result['status'], 'TRIGGERED')

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

    def test_profit_amount_percent_and_deposit_denominator(self):
        for equity, deposits, profit, fraction in [
            (33000, 30000, 3000, .1), (30000, 30000, 0, 0),
            (12000, 30000, -18000, -.6), (44000, 40000, 4000, .1),
            (-3000, 30000, -33000, -1.1),
        ]:
            result = evaluate_loss(equity, deposits)
            self.assertEqual(result['profit_hkd'], profit)
            self.assertEqual(result['profit_fraction'], fraction)
        for result in [evaluate_loss(33000, 0), evaluate_loss(33000, age_seconds=31),
                       evaluate_loss(None), evaluate_loss(33000, None)]:
            self.assertNotIn('profit_hkd', result)
            self.assertNotIn('profit_fraction', result)

    def test_no_automatic_execution(self):
        self.assertFalse(evaluate_loss(0)["automatic_execution"])


if __name__ == "__main__":
    unittest.main()
