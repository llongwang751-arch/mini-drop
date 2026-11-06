# 图册验证材料

所有命令从仓库根目录执行，使用已安装的 web Playwright/Chrome。测试只读取本地图册，不调用业务 API、模型或采集器。

```powershell
node docs/diagrams/drop-system/evidence/qa-atlas.mjs
node docs/diagrams/drop-system/evidence/capture-atlas-read.mjs
python docs/diagrams/drop-system/evidence/verify-and-package.py
```

`qa-atlas.mjs` 保存入口 SHA 和离线交互结果。`capture-atlas-read.mjs` 先校验图与成功浏览器收据绑定，再原生截取 READ 主 SVG；不改已交付 HTML。验证打包器核对图、收据、审阅、源码和 PNG 的 SHA，并检查 ZIP CRC。

`viewer-operations/` 是对图03真实执行交互/规范导出检查的原始材料。`canonical.png`、`canonical.svg` 与 `share-card.png` **展示已发现的细注导出排版局限，不能当作交付的静态图**。清楚的静态主图在上级 `read-png/`。

独立图像审阅需要实际查看截图；自动化脚本不能代替审阅。各 `*-visual-review.json` 是本轮实际看图后保存的记录。
