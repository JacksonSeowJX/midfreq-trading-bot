/**
 * ECharts for the research views, tree-shaken to the chart types the dashboard uses. This
 * module (and so ECharts) loads only with the pages that import it.
 */
import { BarChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import ReactEChartsCore from 'echarts-for-react/esm/core'

echarts.use([
  BarChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

export type EChartOption = echarts.EChartsCoreOption

export function EChart({ option, height, label }: { option: EChartOption; height: number; label: string }) {
  return (
    <div role="img" aria-label={label}>
      <ReactEChartsCore echarts={echarts} option={option} notMerge lazyUpdate style={{ height, width: '100%' }} />
    </div>
  )
}
