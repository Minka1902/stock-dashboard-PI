import { useState } from "react";
import { currencyForSymbol, formatCount, formatMoney } from "../lib/format";
import Tooltip from "./Tooltip";
import Term from "./Term";
import styles from "./CompanyInfo.module.css";

const SUMMARY_CLAMP = 320;

function pct(v, digits = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function num(v, digits = 2) {
  return v == null ? "—" : Number(v).toFixed(digits);
}

// The value cell is ellipsised, so it shows its full text on hover — but only
// when it is actually cut off.
function Row({ label, value }) {
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      <Tooltip truncate><span className={styles.rowValue}>{value}</span></Tooltip>
    </div>
  );
}

// Officer pay is in the company's reporting currency (Yahoo financialCurrency),
// which can differ from the listing's: TEVA.TA trades in ₪ but reports in USD,
// LUMI.TA reports in ILS. When the code isn't known it is USD by Yahoo's
// convention, and it's spelled out wherever it differs from the share price's
// currency so a "$" is never read as shekels (or the reverse).
function officerPayFormatter(profile, ticker) {
  const payCcy = profile?.financial_currency || "USD";
  const listingCcy = currencyForSymbol(ticker) || "USD";
  return (n) => {
    const money = formatMoney(n, payCcy, { compact: true });
    return payCcy === listingCcy ? money : `${money} ${payCcy}`;
  };
}

function Section({ title, children }) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>{title}</h4>
      {children}
    </div>
  );
}

/**
 * Who this company is: identity, business summary, valuation, the people who
 * run it and the institutions that own it. Every field is optional — anything
 * the fundamentals source hasn't returned renders as "—" rather than being
 * guessed at. Which sections appear is a user preference (Settings → Company
 * information).
 */
export default function CompanyInfo({ company, ticker, show = {} }) {
  const [expanded, setExpanded] = useState(false);

  const profile = company?.profile || null;
  const officers = company?.officers || [];
  const payLabel = officerPayFormatter(profile, ticker);
  const holders = company?.holders || [];
  const name = company?.name || null;

  const wants = {
    profile: show.profile !== false,
    valuation: show.valuation !== false,
    officers: show.officers !== false,
    holders: show.holders !== false,
  };

  // Nothing stored yet for this ticker — say so instead of rendering empty rows.
  if (!profile && !name && !officers.length && !holders.length) {
    return (
      <p className={styles.empty}>
        No company profile stored for {ticker} yet. It fills in the next time the
        fundamentals source runs for a watched or held ticker.
      </p>
    );
  }

  const summary = profile?.summary || "";
  const long = summary.length > SUMMARY_CLAMP;
  const shown = expanded || !long ? summary : `${summary.slice(0, SUMMARY_CLAMP).trimEnd()}…`;

  const location = [profile?.city, profile?.country].filter(Boolean).join(", ");

  return (
    <div className={styles.wrap}>
      {wants.profile && (
        <Section title="Profile">
          <div className={styles.identity}>
            <span className={styles.name}>{name || ticker}</span>
            {(profile?.sector || profile?.industry) && (
              <span className={styles.tags}>
                {[profile?.sector, profile?.industry].filter(Boolean).join(" · ")}
              </span>
            )}
          </div>
          {summary && (
            <p className={styles.summary}>
              {shown}
              {long && (
                <button className={styles.more} onClick={() => setExpanded((e) => !e)}>
                  {expanded ? "less" : "more"}
                </button>
              )}
            </p>
          )}
          <div className={styles.rows}>
            <Row label="Market cap" value={profile?.market_cap != null
              // In the listing's currency (Yahoo reports a .TA market cap in shekels).
              ? formatMoney(profile.market_cap, currencyForSymbol(ticker) || "USD", { compact: true }) : "—"} />
            <Row label="Employees" value={profile?.employees != null
              ? formatCount(profile.employees) : "—"} />
            <Row label="Headquarters" value={location || "—"} />
            <Row
              label="Website"
              value={profile?.website ? (
                <a className={styles.link} href={profile.website}
                   target="_blank" rel="noreferrer noopener">
                  {profile.website.replace(/^https?:\/\/(www\.)?/, "")}
                </a>
              ) : "—"}
            />
          </div>
        </Section>
      )}

      {wants.valuation && profile && (
        <Section title="Valuation">
          <div className={styles.rows}>
            <Row label={<Term term="pe_ratio">P/E (trailing)</Term>} value={num(profile.pe_ratio)} />
            <Row label={<Term term="pe_ratio">P/E (forward)</Term>} value={num(profile.forward_pe)} />
            <Row label={<Term term="peg">PEG</Term>} value={num(profile.peg_ratio)} />
            <Row label={<Term term="price_to_book">Price / book</Term>} value={num(profile.pb_ratio)} />
            <Row label="Revenue growth" value={pct(profile.revenue_growth)} />
            <Row label="Profit margin" value={pct(profile.profit_margin)} />
          </div>
        </Section>
      )}

      {wants.holders && (
        <Section title="Ownership">
          <div className={styles.rows}>
            <Row label="Held by insiders" value={pct(profile?.insider_pct)} />
            <Row label="Held by institutions" value={pct(profile?.institution_pct)} />
          </div>
          {holders.length > 0 ? (
            <ul className={styles.holders}>
              {holders.map((h) => (
                <li key={h.holder} className={styles.holder}>
                  <Tooltip truncate><span className={styles.holderName}>{h.holder}</span></Tooltip>
                  <span className={styles.holderPct}>{pct(h.pct_held, 2)}</span>
                  <span className={styles.holderMeta}>
                    {h.shares != null ? formatCount(h.shares) : "—"} sh
                    {h.reported_at ? ` · ${h.reported_at}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.none}>No institutional holders recorded.</p>
          )}
        </Section>
      )}

      {wants.officers && (
        <Section title="Leadership">
          {officers.length > 0 ? (
            <ul className={styles.officers}>
              {officers.map((o) => (
                <li key={o.name} className={styles.officer}>
                  <Tooltip truncate><span className={styles.officerName}>{o.name}</span></Tooltip>
                  <span className={styles.officerTitle}>{o.title || "—"}</span>
                  <span className={styles.officerMeta}>
                    {o.age ? `age ${o.age}` : ""}
                    {o.pay ? `${o.age ? " · " : ""}${payLabel(o.pay)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.none}>No officers recorded.</p>
          )}
        </Section>
      )}
    </div>
  );
}
