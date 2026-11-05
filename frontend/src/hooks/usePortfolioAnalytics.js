import { useEffect, useState } from "react";
import { getPortfolioAnalytics } from "../api";

/** The analytics report for one account ("manual" | "paper" | "bot:<id>"). */
export function usePortfolioAnalytics(account) {
  const [state, setState] = useState({ data: null, error: null, account: null });

  useEffect(() => {
    let live = true;
    getPortfolioAnalytics(account)
      .then((data) => { if (live) setState({ data, error: null, account }); })
      .catch((e) => { if (live) setState({ data: null, error: e.message, account }); });
    return () => { live = false; };
  }, [account]);

  const loading = state.account !== account;
  return { data: loading ? null : state.data, error: loading ? null : state.error, loading };
}
