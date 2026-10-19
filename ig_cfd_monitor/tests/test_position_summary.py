import unittest
from position_summary import group_positions
class PositionSummaryTests(unittest.TestCase):
 def row(self,**args):return dict(epic='X',currency='USD',expiry='-',direction='BUY',size=1,level=100,**args)
 def test_quantity_weighted_not_simple_or_net_average(self):
  base=self.row();rows=[base,dict(base,size=3,level=104),dict(base,direction='SELL',size=2,level=110)]
  g=group_positions(rows)[0]
  self.assertEqual(g['BUY']['average_entry'],103);self.assertEqual(g['BUY']['size'],4);self.assertEqual(g['BUY']['count'],2)
  self.assertEqual(g['SELL']['average_entry'],110);self.assertEqual(g['SELL']['size'],2)
 def test_currency_expiry_epic_isolation(self):
  base=self.row();self.assertEqual(len(group_positions([base,dict(base,currency='JPY'),dict(base,expiry='DEC'),dict(base,epic='Y')])),4)
 def test_missing_invalid_data_never_dilutes_average(self):
  base=self.row()
  for missing in [dict(base,level=None),dict(base,size=-1),dict(base,level=float('inf'))]:
   group=group_positions([base,missing])[0]
   self.assertIsNone(group['BUY']['average_entry']);self.assertIsNone(group['BUY']['size']);self.assertEqual(group['BUY']['invalid'],1)
 def test_empty_short_only_and_negative_price(self):
  self.assertEqual(group_positions([]),[])
  g=group_positions([dict(self.row(),direction='SELL',level=-10)])[0]
  self.assertEqual(g['SELL']['average_entry'],-10);self.assertIsNone(g['BUY']['average_entry'])
if __name__=='__main__':unittest.main()
