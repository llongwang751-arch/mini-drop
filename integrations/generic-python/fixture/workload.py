"""冻结的合成负载；性能对照只使用已有正确行为，边界缺陷由独立新增测试复现。

返回真实 Python 对象，由镜像中的可信驱动检查类型、跨调用可变对象隔离和数值。
"""
from business import price_cart


def run():
    rows = [{'sku': 'Item-' + str(i % 8), 'qty': i % 4 + 1, 'price_cents': i % 7 + 10} for i in range(64)]
    return price_cart(rows)
