export interface ChipDistributionItem {
    price_low: number
    price_high: number
    percent: number
}

export interface MarginChangeItem {
    price_low: number
    price_high: number
    prev_pct: number
    curr_pct: number
    change: number
}

export interface Dim6ScoreItem {
    score: number
    label: string
    detail: string
}

export interface Dim6Score {
    chip_density: Dim6ScoreItem
    margin_change: Dim6ScoreItem
    winner_position: Dim6ScoreItem
    cost_rise: Dim6ScoreItem
    overshoot: Dim6ScoreItem
    support_level: Dim6ScoreItem
}

export interface PriceStage {
    name: string
    start_date: string
    end_date: string
    start_price: number
    end_price: number
    change_pct: number
    winner_rate_start: number
    winner_rate_end: number
}

export interface ChipDeepResult {
    meta: {
        symbol: string
        analysis_date: string
        data_date?: string
        lookback_days?: number
        error?: string
    }
    current: {
        close?: number
        weight_avg?: number
        cost_5pct?: number
        cost_50pct?: number
        cost_95pct?: number
        winner_rate?: number
    }
    price_stages?: PriceStage[]
    chip_distribution: ChipDistributionItem[]
    margin_change_2w: MarginChangeItem[]
    dim6_score: Dim6Score
    dim6_total: number
    rating: number
    summary_text: string
    detailed_summary?: string
}
