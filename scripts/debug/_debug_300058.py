"""调试 300058 止损止盈 + 风险回报比"""
import sys, io
sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import asyncio
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer

async def main():
    a = ChipDeepAnalyzer("300058.SZ", 30)

    # 拉数据
    perf_df = await a._get_cyq_perf()
    perf_df = perf_df.sort_values("trade_date").reset_index(drop=True).tail(30)
    latest_date = str(perf_df["trade_date"].max()).replace("-", "")

    chips_df = await a._get_cyq_chips(latest_date)
    # 用 perf_df 中的 his_high 作为当前价 fallback（不依赖 daily 接口）
    close = float(perf_df["his_high"].iloc[-1]) if "his_high" in perf_df.columns else 0
    weight_avg = float(perf_df["weight_avg"].iloc[-1])

    print(f"close={close}, weight_avg={weight_avg}, latest_date={latest_date}")
    if close <= 0:
        print("[FAIL] close=0, Tushare timeout")
        return

    # 测试分位价格
    for q in [5, 15, 20]:
        qp = a._calc_chip_quantile_price(chips_df, q)
        if qp:
            dist = (close - qp) / close * 100
            print(f"  {q}%分位价 = {qp:.2f}, 距当前 {dist:+.1f}%")

    # 测试旧峰
    old_peak = a._calc_old_peak_price(chips_df, close)
    print(f"  旧峰 (close上方) = {old_peak}")

    # perf_df 字段说明
    print(f"\n[perf_df 字段检查]")
    for col in perf_df.columns:
        val = perf_df[col].iloc[-1]
        print(f"  {col} = {val} (type: {type(val).__name__})")

    # 周期高点（从 close 字段取最大）
    if "close" in perf_df.columns:
        period_high = float(perf_df["close"].max())
        print(f"  周期内最高 close = {period_high}")
    if "his_high" in perf_df.columns:
        his_high_val = float(perf_df["his_high"].iloc[-1])
        print(f"  his_high (perf_df末值) = {his_high_val}")
    if "cost_95pct" in perf_df.columns:
        c95 = float(perf_df["cost_95pct"].iloc[-1])
        print(f"  cost_95pct (perf_df末值) = {c95}")

    # 综合止损止盈（参数顺序：perf_df, chips_df, close, weight_avg）
    stp = a._calc_stop_loss_take_profit(perf_df, chips_df, close, weight_avg)
    print(f"\n  止损: {stp['stop_loss']:.2f}")
    print(f"  第一止盈: {stp['take_profit_1']:.2f}")
    print(f"  第二止盈: {stp['take_profit_2']:.2f}")
    print(f"  第三止盈: {stp['take_profit_3']:.2f}")
    print(f"  风险回报比 (rr_ratio): {stp['risk_reward_ratio']:.4f}")
    print(f"  1:{stp['risk_reward_ratio']:.1f}")

asyncio.run(main())
