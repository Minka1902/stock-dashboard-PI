import AuditTab from "./AuditTab";
import BotsTab from "./BotsTab";
import BrokerTab from "./BrokerTab";
import LotsTab from "./LotsTab";
import KillSwitchButton from "./KillSwitchButton";
import PaperTab from "./PaperTab";
import PerformanceTab from "./PerformanceTab";
import RiskTab from "./RiskTab";
import RulesTab from "./RulesTab";
import ScannerTab from "./ScannerTab";
import Segmented from "./Segmented";
import { useRoute } from "../hooks/useRoute";
import { navigateTo } from "../lib/nav";
import styles from "./TradingBotPanel.module.css";

const VIEW = "trading-bot";

// Tab key -> { label, title, render(props) }. The key is the `?tab=` value, so
// other pages can deep-link straight to one (e.g. /trading-bot?tab=risk).
const TABS = {
  bots: { label: "Bots", title: "Demo bots and their activity", render: (p) => <BotsTab cash={p.cash} onCashChanged={p.onCashChanged} /> },
  risk: { label: "Risk", title: "Kill switch, regime, exit rules and signal mutes", render: () => <RiskTab /> },
  paper: { label: "Paper", title: "Paper account and order ticket", render: () => <PaperTab /> },
  broker: { label: "Broker", title: "Your Alpaca paper account: orders, fills and reconciliation", render: () => <BrokerTab /> },
  performance: { label: "Performance", title: "Returns, drawdown, exposure and correlation per account", render: () => <PerformanceTab /> },
  rules: { label: "Rules", title: "Build, backtest and save your own trading rules", render: () => <RulesTab /> },
  lots: { label: "Lots & rebalance", title: "Tax lots, wash sales and the plan back to your target weights", render: () => <LotsTab /> },
  scanner: { label: "Scanner", title: "Today's matches across the market, live", render: () => <ScannerTab /> },
  audit: { label: "Audit", title: "Append-only log of orders, refusals and setting changes", render: () => <AuditTab /> },
};

const TAB_OPTIONS = Object.entries(TABS).map(([value, t]) => ({ value, label: t.label, title: t.title }));

const DEFAULT_TAB = TAB_OPTIONS[0].value;

/**
 * The Trading Bot page: a tab host for everything that trades or guards
 * trading (demo bots, risk rules, paper account, performance, rules, scanner,
 * audit). The selected tab lives in the URL so it survives reloads and can be
 * linked to. Props are passed through to the tabs that need them.
 */
export default function TradingBotPanel(props) {
  const route = useRoute();

  const tab = route.kind === "view" && TABS[route.tab] ? route.tab : DEFAULT_TAB;

  const selectTab = (next) => navigateTo({ kind: "view", view: VIEW, tab: next === DEFAULT_TAB ? null : next }, { replace: true });

  return (
    <section className={styles.panel} id="trading-bot">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Trading Bot</h2>
          <p className={styles.subtitle}>
            Demo bots, risk rules, paper trading and an Alpaca paper account. Signals read completed daily
            closes; stops and fills use live prices. No real money: live trading is off.
          </p>
        </div>
        <KillSwitchButton />
      </header>
      <div className={styles.tabs}>
        <Segmented options={TAB_OPTIONS} value={tab} onChange={selectTab} ariaLabel="Trading Bot sections" />
      </div>
      <div role="tabpanel" aria-label={TABS[tab].label}>
        {TABS[tab].render(props)}
      </div>
    </section>
  );
}
