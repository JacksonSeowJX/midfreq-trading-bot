/** Short names for the API's response models (apps/api/src/midfreq/api/schemas.py). */
import type { components } from './schema'

type S = components['schemas']

export type Health = S['Health']
export type EquityPoint = S['EquityPoint']
export type ForwardTest = S['ForwardTest']
export type ForwardTrade = S['ForwardTrade']
export type Holding = S['Holding']
export type SessionSummary = S['SessionSummary']
export type SessionDetail = S['SessionDetail']
export type SessionTrade = S['SessionTrade']
export type AccountInfo = S['AccountInfo']
export type AccountView = S['AccountView']
export type AccountSnapshot = S['AccountSnapshotOut']
export type Position = S['PositionOut']
export type Order = S['OrderOut']
export type ResultRow = S['ResultRow']
export type ResultsSummary = S['ResultsSummary']
export type RealityCheckConfig = S['RealityCheckConfig']
export type SearchCorrection = S['SearchCorrection']
export type Overview = S['Overview']
export type ResearchFile = S['ResearchFile']
export type ResearchFileContent = S['ResearchFileContent']

// reference data
export type StrategyInfo = S['StrategyInfo']
export type StrategyParam = S['StrategyParam']
export type Universe = S['Universe']
export type Candle = S['Candle']

// runs (background jobs)
export type Job = S['Job']
export type JobKind = Job['kind']
export type JobStatus = Job['status']
export type JobCapabilities = S['JobCapabilities']
export type RiskSettings = S['RiskSettings']
export type SizingMethod = RiskSettings['sizing']
export type BacktestRequest = S['BacktestRequest']
export type CompareRequest = S['CompareRequest']
export type OptimizeRequest = S['OptimizeRequest']
export type BacktestResult = S['BacktestResult']
export type CompareResult = S['CompareResult']
export type CompareRow = S['CompareRow']
export type OptimizeResult = S['OptimizeResult']
export type GridRow = S['GridRow']
export type WalkForwardWindow = S['WalkForwardWindow']
export type SeriesPoint = S['SeriesPoint']
export type TradeOut = S['TradeOut']
export type Indicator = S['Indicator']
export type PriceChartData = S['PriceChart']

// saved runs (the run store: Run history)
export type SavedRun = S['RunSummary']
export type SavedRunKind = SavedRun['kind']
export type SavedRunDetail = S['RunDetail']
/**
 * GET /api/runs/{id}/tables/{name} with format=json. The contract leaves it undeclared, since the
 * same route also answers with a CSV or Parquet file; this is what the API sends.
 */
export interface SavedRunTable {
  run_id: string
  name: string
  columns: string[]
  /** at most the request's limit (100,000 by default); NaN as null, times as ISO 8601 */
  rows: Record<string, unknown>[]
  /** the table's full length */
  total: number
}
export type TableFormat = 'csv' | 'parquet'
