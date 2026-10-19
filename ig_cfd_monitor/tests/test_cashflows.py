import unittest
from cashflows import money, currency, classify, ledger_summary
from risk import evaluate_loss

class CashFlowTests(unittest.TestCase):
 def test_ig_hong_kong_alias(self):
  self.assertEqual(currency('HK'),'HKD');self.assertEqual(money('HK30,000.00','HK'),30000)
  self.assertEqual(money('HK-98.70','HK'),-98.7)
  self.assertEqual(money('(HK$ 1,234.56)','HKD'),-1234.56)
 def test_invalid_currency_sign_and_grouping(self):
  for x,c in [('US$ 30','HK'),('HK1,2','HK'),('NaN','HK'),('(-1)','HK'),('HK30','USD')]:self.assertIsNone(money(x,c))
 def test_interest_not_withdrawal(self):
  self.assertEqual(classify({'transactionType':'WITH','instrumentName':'Long Interest for 01/01/26 converted at 7.8'}),'financing')
  self.assertEqual(classify({'transactionType':'WITH','instrumentName':'Withdrawal to bank'}),'withdrawal')
  self.assertEqual(classify({'transactionType':'WITH','instrumentName':'Unknown adjustment'}),'unclassified')
 def test_gross_deposit_policy(self):
  self.assertEqual(evaluate_loss(22000,40000)['status'],'BELOW_THRESHOLD')
  self.assertEqual(evaluate_loss(16000,40000)['status'],'TRIGGERED')
  self.assertEqual(evaluate_loss(11000,30000)['status'],'TRIGGERED')
