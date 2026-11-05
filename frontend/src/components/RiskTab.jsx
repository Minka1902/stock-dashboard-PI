import DrawdownSwitch from "./DrawdownSwitch";
import ExitRulesTable from "./ExitRulesTable";
import GuardrailsCard from "./GuardrailsCard";
import MutedSignals from "./MutedSignals";
import RegimeCard from "./RegimeCard";

/**
 * The Risk tab of the Trading Bot page: the switches that pause or downgrade
 * new entries (kill switch, market regime), the order guardrails, the exit
 * rules on your holdings,
 * and which signals currently count.
 */
export default function RiskTab() {
  return (
    <div>
      <DrawdownSwitch />
      <RegimeCard />
      <GuardrailsCard />
      <ExitRulesTable account="manual" title="Exit rules · Portfolio" />
      <MutedSignals />
    </div>
  );
}
