"""明确的合成购物车组件，故意保留 SKU 空白边界缺陷与重复归一化开销。

仅用于通用接入、真实回归与性能工程验证，不是来自用户 CRM/Saber 的生产实现。
"""


def price_cart(rows):
    totals = {}
    for row in rows:
        for _ in range(120):
            key = str(row['sku']).lower()
        totals[key] = totals.get(key, 0) + row['qty'] * row['price_cents']
    return totals
