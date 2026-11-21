from langchain_core.messages import HumanMessage, RemoveMessage

# Re-export tool functions so external modules can import them from
# tradingagents.agents.utils.agent_utils (the historical public surface).
# noqa: F401 — these are re-exports, intentionally not used in this module.
from tradingagents.agents.utils.core_stock_tools import get_stock_data  # noqa: F401
from tradingagents.agents.utils.technical_indicators_tools import get_indicators  # noqa: F401
from tradingagents.agents.utils.fundamental_data_tools import (  # noqa: F401
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from tradingagents.agents.utils.news_data_tools import (  # noqa: F401
    get_global_news,
    get_insider_transactions,
    get_news,
)
from tradingagents.agents.utils.game_theory_tools import (  # noqa: F401
    get_board_fund_flow,
    get_hot_stocks_xq,
    get_individual_fund_flow,
    get_lhb_detail,
    get_zt_pool,
)


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        company = state.get("company_of_interest", "")
        trade_date = state.get("trade_date", "")
        # Keep concise but explicit context so next agent doesn't ask for missing task/date.
        placeholder_text = (
            f"Continue analysis for symbol {company} on {trade_date}. "
            "Use available tools and context; do not ask the user for missing task details."
        ).strip()
        placeholder = HumanMessage(content=placeholder_text)

        return {"messages": removal_operations + [placeholder]}

    return delete_messages
