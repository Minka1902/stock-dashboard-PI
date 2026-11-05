import SelectMenu from "./SelectMenu";
import styles from "./VersionPicker.module.css";

function labelOf(v) {
  const tag = v.current ? " · running" : v.version === "legacy" ? " · before versioning" : "";
  return `${v.version}${tag} · n=${v.n} · ${v.first_date} → ${v.last_date}`;
}

/**
 * Picks which model version's record to show. Records are never mixed across
 * versions, so this is how you compare a reweighted model with the old one.
 * Renders a plain caption when there is only one version.
 *
 * Props: versions ([{ version, n, first_date, last_date, current }]), value, onChange.
 */
export default function VersionPicker({ versions = [], value, onChange }) {
  if (versions.length <= 1) {
    return <span className="caption">Model {value}</span>;
  }
  return (
    <SelectMenu label="Model version" value={value} className={styles.picker}
                options={versions.map((v) => ({ value: v.version, label: labelOf(v) }))}
                onChange={onChange} />
  );
}
