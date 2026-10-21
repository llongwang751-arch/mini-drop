"""原冻结回归：既有正常输入与返回对象契约，不包含待新增的空白 SKU 边界测试。"""
import unittest
from business import price_cart


class OriginalContract(unittest.TestCase):
    def test_totals_and_type(self):
        actual = price_cart([{'sku': 'Book', 'qty': 2, 'price_cents': 125}, {'sku': 'BOOK', 'qty': 1, 'price_cents': 125}])
        self.assertIs(type(actual), dict)
        self.assertEqual(actual, {'book': 375})

    def test_outputs_do_not_share_mutable_state(self):
        one = price_cart([{'sku': 'Book', 'qty': 1, 'price_cents': 125}])
        one['book'] = 0
        self.assertEqual(price_cart([{'sku': 'Book', 'qty': 1, 'price_cents': 125}]), {'book': 125})
