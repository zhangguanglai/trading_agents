import { useState } from 'react'
import { Search, Loader2, Download, Share2, BarChart3, TrendingUp, TrendingDown, Lightbulb, AlertTriangle, CheckCircle, XCircle, Info } from 'lucide-react'
import { api } from '@/services/api'
import type { ChipDeepResult, CoreInsight } from '@/types/chipDeep'

export default function ChipDeep() {
    const [symbol, setSymbol] = useState('')
    const [result, setResult] = useState<ChipDeepResult | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    const handleAnalyze = async () => {
        if (!symbol.trim()) return
        setLoading(true)
        setError('')
        try {
            const data = await api.chipDeepAnalyze(symbol.trim())
            setResult(data)
        } catch (err: any) {
            setError(err.message || '分析失败，请稍后重试')
        } finally {
            setLoading(false)
        }
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') handleAnalyze()
    }

    return (
        <div className="space-y-6 max-w-6xl mx-auto">
            {/* 页面标题 */}
            <div className="flex items-center gap-3">
                <BarChart3 className="w-6 h-6 text-blue-500" />
                <h1 className="text-2xl font-black text-slate-900 dark:text-white">筹码深度分析</h1>
            </div>

            {/* 搜索栏 */}
            <div className="card">
                <div className="flex gap-3">
                    <div className="flex-1 relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-400" />
                        <input
                            type="text"
                            value={symbol}
                            onChange={(e) => setSymbol(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="输入股票代码或名称，如 000951.SZ 或 中国重汽"
                            className="w-full pl-10 pr-4 py-3 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                        />
                    </div>
                    <button
                        onClick={handleAnalyze}
                        disabled={loading || !symbol.trim()}
                        className="px-6 py-3 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-bold transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                    >
                        {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Search className="w-5 h-5" />}
                        {loading ? '分析中...' : '分析'}
                    </button>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                    支持别名搜索：茅台、招行、平安、神华、宁王、迪王等
                </p>
            </div>

            {/* 错误提示 */}
            {error && (
                <div className="card border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20">
                    <p className="text-red-600 dark:text-red-400">{error}</p>
                </div>
            )}

            {/* 加载状态 */}
            {loading && (
                <div className="card flex flex-col items-center justify-center py-16">
                    <Loader2 className="w-12 h-12 text-blue-500 animate-spin mb-4" />
                    <p className="text-slate-500">正在获取筹码数据并计算六维评分...</p>
                    <p className="text-xs text-slate-400 mt-2">首次分析可能需要 5-10 秒</p>
                </div>
            )}

            {/* 分析结果 */}
            {result && !loading && (
                <>
                    {/* 头部摘要卡 */}
                    <SummaryCard result={result} />

                    {/* 核心洞察 */}
                    {result.core_insights && result.core_insights.length > 0 && (
                        <CoreInsightsCard insights={result.core_insights} />
                    )}

                    {/* 价格走势阶段 */}
                    {result.price_stages && result.price_stages.length > 0 && (
                        <PriceStagesCard stages={result.price_stages} />
                    )}

                    {/* 六维评分 */}
                    <Dim6ScoreCard result={result} />

                    {/* 筹码分布 */}
                    <ChipDistributionCard result={result} />

                    {/* 边际变化 */}
                    <MarginChangeCard result={result} />

                    {/* 详细报告 */}
                    <DetailedReport result={result} />

                    {/* 操作栏 */}
                    <ActionBar result={result} />
                </>
            )}
        </div>
    )
}

function SummaryCard({ result }: { result: ChipDeepResult }) {
    const { meta, current, dim6_total, rating } = result
    const stars = '⭐'.repeat(rating)

    return (
        <div className="card">
            <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-black text-slate-900 dark:text-white">
                    {meta.name ? `${meta.name} (${meta.symbol})` : meta.symbol} <span className="text-sm font-normal text-slate-500">筹码分析报告</span>
                </h2>
                <span className="text-2xl">{stars}</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <MetricItem label="当前价" value={`${current.close?.toFixed(2) || '-'}`} />
                <MetricItem label="平均成本" value={`${current.weight_avg?.toFixed(2) || '-'}`} />
                <MetricItem label="获利盘" value={`${current.winner_rate?.toFixed(1) || '-'}%`} />
                <MetricItem label="六维评分" value={`${dim6_total}/5.5`} highlight={dim6_total >= 4} />
            </div>
        </div>
    )
}

function MetricItem({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
    return (
        <div className={`p-3 rounded-xl ${highlight ? 'bg-blue-50 dark:bg-blue-500/10' : 'bg-slate-50 dark:bg-slate-800/50'}`}>
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <p className={`text-lg font-black ${highlight ? 'text-blue-600 dark:text-blue-400' : 'text-slate-900 dark:text-white'}`}>{value}</p>
        </div>
    )
}

function Dim6ScoreCard({ result }: { result: ChipDeepResult }) {
    const dims = [
        { key: 'chip_density', name: '筹码密度', desc: '当前价附近筹码集中程度' },
        { key: 'margin_change', name: '边际变化', desc: '近期筹码聚集还是散开' },
        { key: 'winner_position', name: '获利盘', desc: '持仓获利比例与情绪' },
        { key: 'cost_rise', name: '成本抬升', desc: '底部是否抬高' },
        { key: 'overshoot', name: '超跌程度', desc: '价格偏离成本幅度' },
        { key: 'support_level', name: '下方支撑', desc: '破位后是否有缓冲' },
    ] as const

    // 根据标签确定卡片样式
    const getCardStyle = (label: string) => {
        if (label.includes('✅✅')) return 'border-emerald-300 dark:border-emerald-400/50 bg-emerald-100/50 dark:bg-emerald-500/10'
        if (label.includes('✅')) return 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-500/5'
        if (label.includes('⚠️')) return 'border-amber-200 dark:border-amber-500/30 bg-amber-50/50 dark:bg-amber-500/5'
        return 'border-red-200 dark:border-red-500/30 bg-red-50/50 dark:bg-red-500/5'
    }

    return (
        <div className="card">
            <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">六维评分</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {dims.map((dim) => {
                    const score = result.dim6_score[dim.key]
                    return (
                        <div key={dim.key} className={`p-4 rounded-xl border ${getCardStyle(score.label)}`}>
                            <div className="flex items-center justify-between mb-2">
                                <span className="font-bold text-slate-800 dark:text-slate-200">{dim.name}</span>
                                <span className="text-xl">{score.label}</span>
                            </div>
                            <p className="text-xs text-slate-500 mb-1">{dim.desc}</p>
                            <p className="text-sm text-slate-700 dark:text-slate-300">{score.detail}</p>
                        </div>
                    )
                })}
            </div>
            <div className="mt-4 p-3 rounded-xl bg-blue-50 dark:bg-blue-500/10 border border-blue-200 dark:border-blue-500/20">
                <p className="text-sm text-blue-800 dark:text-blue-300">
                    <span className="font-bold">综合判定：</span>
                    {result.summary_text}
                </p>
            </div>
        </div>
    )
}

function ChipDistributionCard({ result }: { result: ChipDeepResult }) {
    if (!result.chip_distribution || result.chip_distribution.length === 0) {
        return (
            <div className="card">
                <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">筹码分布</h3>
                <p className="text-slate-500">暂无筹码分布数据</p>
            </div>
        )
    }

    const maxPct = Math.max(...result.chip_distribution.map(d => d.percent), 1)

    return (
        <div className="card">
            <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">筹码分布</h3>
            <div className="space-y-2">
                {result.chip_distribution.map((item, i) => (
                    <div key={i} className="flex items-center gap-3">
                        <span className="w-24 text-sm text-slate-600 dark:text-slate-400 text-right">
                            {item.price_low.toFixed(1)}-{item.price_high.toFixed(1)}
                        </span>
                        <div className="flex-1 h-6 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                                style={{ width: `${(item.percent / maxPct) * 100}%` }}
                            />
                        </div>
                        <span className="w-16 text-sm font-bold text-slate-700 dark:text-slate-300">
                            {item.percent.toFixed(1)}%
                        </span>
                    </div>
                ))}
            </div>
        </div>
    )
}

function MarginChangeCard({ result }: { result: ChipDeepResult }) {
    if (!result.margin_change_2w || result.margin_change_2w.length === 0) {
        return (
            <div className="card">
                <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">边际变化（2周）</h3>
                <p className="text-slate-500">暂无足够历史数据进行边际变化分析</p>
            </div>
        )
    }

    return (
        <div className="card">
            <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">边际变化（2周）</h3>
            <div className="space-y-2">
                {result.margin_change_2w.map((item, i) => (
                    <div key={i} className="flex items-center justify-between p-3 rounded-xl bg-slate-50 dark:bg-slate-800/50">
                        <span className="text-sm text-slate-600 dark:text-slate-400">
                            {item.price_low.toFixed(1)}-{item.price_high.toFixed(1)}
                        </span>
                        <div className="flex items-center gap-4">
                            <span className="text-xs text-slate-500">{item.prev_pct.toFixed(1)}% → {item.curr_pct.toFixed(1)}%</span>
                            <span className={`text-sm font-bold ${item.change > 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                {item.change > 0 ? '+' : ''}{item.change.toFixed(1)}%
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

function PriceStagesCard({ stages }: { stages?: ChipDeepResult['price_stages'] }) {
    if (!stages || stages.length === 0) return null
    return (
        <div className="card">
            <h3 className="text-lg font-black text-slate-900 dark:text-white mb-4">价格走势总览</h3>
            <div className="space-y-3">
                {stages.map((stage, i) => (
                    <div key={i} className={`p-4 rounded-xl border ${
                        stage.name === '大涨' 
                            ? 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-500/5' 
                            : 'border-red-200 dark:border-red-500/30 bg-red-50/50 dark:bg-red-500/5'
                    }`}>
                        <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                                {stage.name === '大涨' ? (
                                    <TrendingUp className="w-5 h-5 text-emerald-600" />
                                ) : (
                                    <TrendingDown className="w-5 h-5 text-red-600" />
                                )}
                                <span className="font-bold text-slate-800 dark:text-slate-200">{stage.name}</span>
                            </div>
                            <span className={`text-sm font-bold ${
                                stage.change_pct > 0 ? 'text-emerald-600' : 'text-red-600'
                            }`}>
                                {stage.change_pct > 0 ? '+' : ''}{stage.change_pct}%
                            </span>
                        </div>
                        <div className="grid grid-cols-2 gap-4 text-sm">
                            <div>
                                <p className="text-slate-500">时间</p>
                                <p className="text-slate-700 dark:text-slate-300">{stage.start_date} → {stage.end_date}</p>
                            </div>
                            <div>
                                <p className="text-slate-500">价格</p>
                                <p className="text-slate-700 dark:text-slate-300">{stage.start_price} → {stage.end_price}</p>
                            </div>
                            <div>
                                <p className="text-slate-500">获利盘变化</p>
                                <p className="text-slate-700 dark:text-slate-300">{stage.winner_rate_start.toFixed(1)}% → {stage.winner_rate_end.toFixed(1)}%</p>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

function DetailedReport({ result }: { result: ChipDeepResult }) {
    const [expanded, setExpanded] = useState(false)

    return (
        <div className="card">
            <button
                onClick={() => setExpanded(!expanded)}
                className="flex items-center justify-between w-full"
            >
                <h3 className="text-lg font-black text-slate-900 dark:text-white">详细报告</h3>
                <span className="text-slate-500 text-sm">{expanded ? '收起' : '展开'}</span>
            </button>
            {expanded && (
                <div className="mt-4 space-y-4 text-sm text-slate-700 dark:text-slate-300">
                    {result.detailed_summary ? (
                        <div className="whitespace-pre-line">
                            {result.detailed_summary}
                        </div>
                    ) : (
                        <>
                            <section>
                                <h4 className="font-bold text-slate-900 dark:text-white mb-2">一、价格与成本</h4>
                                <p>当前收盘价：{result.current.close?.toFixed(2)}</p>
                                <p>加权平均成本：{result.current.weight_avg?.toFixed(2)}</p>
                                <p>5%成本位：{result.current.cost_5pct?.toFixed(2)}</p>
                                <p>50%成本位：{result.current.cost_50pct?.toFixed(2)}</p>
                                <p>95%成本位：{result.current.cost_95pct?.toFixed(2)}</p>
                            </section>
                            <section>
                                <h4 className="font-bold text-slate-900 dark:text-white mb-2">二、分析总结</h4>
                                <p>{result.summary_text}</p>
                            </section>
                        </>
                    )}
                </div>
            )}
        </div>
    )
}

function CoreInsightsCard({ insights }: { insights: CoreInsight[] }) {
    const getIcon = (level: string) => {
        switch (level) {
            case 'success': return <CheckCircle className="w-5 h-5 text-emerald-600" />
            case 'warning': return <AlertTriangle className="w-5 h-5 text-amber-600" />
            case 'danger': return <XCircle className="w-5 h-5 text-red-600" />
            default: return <Info className="w-5 h-5 text-blue-600" />
        }
    }

    const getCardStyle = (level: string) => {
        switch (level) {
            case 'success': return 'border-emerald-200 dark:border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-500/5'
            case 'warning': return 'border-amber-200 dark:border-amber-500/30 bg-amber-50/50 dark:bg-amber-500/5'
            case 'danger': return 'border-red-200 dark:border-red-500/30 bg-red-50/50 dark:bg-red-500/5'
            default: return 'border-blue-200 dark:border-blue-500/30 bg-blue-50/50 dark:bg-blue-500/5'
        }
    }

    return (
        <div className="card">
            <div className="flex items-center gap-2 mb-4">
                <Lightbulb className="w-5 h-5 text-amber-500" />
                <h3 className="text-lg font-black text-slate-900 dark:text-white">核心洞察</h3>
            </div>
            <div className="space-y-3">
                {insights.map((insight, i) => (
                    <div key={i} className={`p-4 rounded-xl border ${getCardStyle(insight.level)}`}>
                        <div className="flex items-start gap-3">
                            <div className="mt-0.5 flex-shrink-0">{getIcon(insight.level)}</div>
                            <div>
                                <h4 className="font-bold text-slate-800 dark:text-slate-200 mb-1">{insight.title}</h4>
                                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{insight.content}</p>
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}

function ActionBar({ result }: { result: ChipDeepResult }) {
    const handleDownloadJSON = () => {
        const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `chip-deep-${result.meta.symbol}-${result.meta.analysis_date}.json`
        a.click()
        URL.revokeObjectURL(url)
    }

    return (
        <div className="flex gap-3">
            <button
                onClick={handleDownloadJSON}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium transition-colors"
            >
                <Download className="w-4 h-4" />
                下载 JSON
            </button>
            <button
                onClick={() => navigator.clipboard.writeText(window.location.href)}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-medium transition-colors"
            >
                <Share2 className="w-4 h-4" />
                复制链接
            </button>
        </div>
    )
}
