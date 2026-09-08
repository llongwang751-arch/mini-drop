import * as echarts from "echarts/core";
import { BarChart, GraphChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// Register only the chart types and components used by Mini-Drop. Importing
// the full `echarts` bundle adds hundreds of kilobytes of unused charts.
echarts.use([
  BarChart,
  GraphChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export default echarts;
