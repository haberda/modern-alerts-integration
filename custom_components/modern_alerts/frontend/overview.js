/* Native web component; no build tooling or external dependencies. */
const labels = {
  not_running: "Not running", condition_inactive: "Condition inactive",
  acknowledged: "Acknowledged", snoozed: "Snoozed", source_unavailable: "Source unavailable",
  quiet_hours: "Quiet hours", outside_weekly_schedule: "Outside weekly schedule",
  presence: "Presence rule not met", group_or_rate_delay: "Grouping or minimum interval",
  no_notification_destinations: "No notification destinations",
  waiting_for_escalation: "Waiting for escalation", already_run_this_incident: "Already ran this incident",
  already_running: "Running"
};
const el = (tag, text, parent) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (parent) parent.append(node);
  return node;
};
const when = value => value ? new Date(value).toLocaleString() : "—";
const blockers = values => values.map(value => labels[value] || value).join(", ") || "Eligible at the next opportunity";

class ModernAlertsOverview extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._filter = "all";
    this._search = "";
    this._expanded = new Set();
    const style = el("style", undefined, this.shadowRoot);
    style.textContent = `
      :host {display:block;color:var(--primary-text-color);background:var(--primary-background-color);min-height:100%;font-family:var(--paper-font-body1_-_font-family, sans-serif)}
      header {display:flex;align-items:center;gap:16px;padding:16px;background:var(--app-header-background-color);color:var(--app-header-text-color)}
      h1 {font-size:22px;margin:0;flex:1} h2 {font-size:18px;margin:0 0 8px} h3 {font-size:16px}
      main {max-width:1200px;margin:auto;padding:20px} .tools {display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
      input,select,button {font:inherit;padding:10px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
      button {cursor:pointer} button:disabled {opacity:.5;cursor:default} a {color:var(--primary-color)}
      .grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:16px}
      article {padding:20px;border:1px solid var(--divider-color);border-radius:12px;background:var(--card-background-color)}
      .status {font-weight:600} .actions {display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
      p {line-height:1.5;margin:8px 0} .muted {color:var(--secondary-text-color)}
      pre {white-space:pre-wrap;overflow-wrap:anywhere} details {margin-top:16px} summary {cursor:pointer}
      .error {color:var(--error-color)} dialog {max-width:min(700px,85vw);max-height:80vh;overflow:auto;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:12px}
    `;
    const header = el("header", undefined, this.shadowRoot);
    const menu = el("ha-menu-button", undefined, header);
    this._menu = menu;
    el("h1", "Modern Alerts", header);
    const main = el("main", undefined, this.shadowRoot);
    const tools = el("div", undefined, main); tools.className = "tools";
    const search = el("input", undefined, tools);
    search.type = "search"; search.placeholder = "Search alerts"; search.setAttribute("aria-label", "Search alerts");
    search.addEventListener("input", () => {this._search = search.value.toLowerCase(); this.renderRows();});
    const filter = el("select", undefined, tools); filter.setAttribute("aria-label", "Filter alerts");
    for (const value of ["all", "active", "acknowledged", "snoozed", "suspended", "errors", "idle"]) {
      const option = el("option", value[0].toUpperCase() + value.slice(1), filter); option.value = value;
    }
    filter.addEventListener("change", () => {this._filter = filter.value; this.renderRows();});
    this._button(tools, "Refresh", () => this.refresh());
    this._settings = el("a", "Add, duplicate, import, or configure alerts", tools);
    this._settings.href = "/config/integrations/integration/modern_alerts";
    this._notice = el("p", "Loading alerts…", main); this._notice.setAttribute("role", "status");
    this._grid = el("div", undefined, main); this._grid.className = "grid";
    this._dialog = el("dialog", undefined, this.shadowRoot);
    this._dialog.setAttribute("aria-label", "Delivery explanation and preview");
  }
  set hass(value) {
    this._hass = value; this._menu.hass = value;
    this._settings.hidden = !value.user?.is_admin;
    if (!this._rows && this.isConnected) this.refresh();
  }
  set narrow(value) {this._menu.narrow = value;}
  connectedCallback() {
    this.refresh();
    this._timer = window.setInterval(() => this.refresh(), 5000);
  }
  disconnectedCallback() {window.clearInterval(this._timer);}
  async refresh() {
    if (!this._hass || this._loading || !this.isConnected) return;
    this._loading = true;
    try {
      const rows = await this._hass.callWS({type: "modern_alerts/overview"});
      if (!this.isConnected) return;
      const changed = JSON.stringify(rows) !== JSON.stringify(this._rows);
      this._rows = rows;
      if (changed) this.renderRows();
    } catch (error) {
      this._notice.textContent = `Unable to load alerts: ${error.message || error}`;
    } finally {this._loading = false;}
  }
  _button(parent, label, action, disabled = false) {
    const button = el("button", label, parent); button.disabled = disabled;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {await action();} catch (error) {this._notice.textContent = `${label} failed: ${error.message || error}`;}
      finally {button.disabled = disabled;}
    });
    return button;
  }
  _status(row) {
    if (!row.loaded) return `Not loaded (${row.state})`;
    return [row.active ? "Active" : "Idle", row.acknowledged && "Acknowledged", row.snoozed_until && "Snoozed", row.source_suspended && "Source unavailable"].filter(Boolean).join(" · ");
  }
  _errors(row) {return Object.keys({...row.notification_errors, ...row.output_errors, ...row.policy_errors}).length || row.missing_profiles;}
  renderRows() {
    if (!this._rows) return;
    const rows = this._rows.filter(row => row.name.toLowerCase().includes(this._search) && (
      this._filter === "all" || (this._filter === "active" && row.active) ||
      (this._filter === "acknowledged" && row.acknowledged) || (this._filter === "snoozed" && row.snoozed_until) ||
      (this._filter === "suspended" && row.source_suspended) || (this._filter === "errors" && (this._errors(row) || !row.loaded)) ||
      (this._filter === "idle" && row.loaded && !row.active)
    )).sort((a,b) => Number(Boolean(b.active)) - Number(Boolean(a.active)) || a.name.localeCompare(b.name));
    this._notice.textContent = `${rows.length} of ${this._rows.length} alerts · Updates every 5 seconds`;
    this._grid.replaceChildren();
    for (const row of rows) {
      const card = el("article", undefined, this._grid);
      el("h2", row.name, card);
      el("p", this._status(row), card).className = "status";
      if (!row.loaded) continue;
      el("p", `Incident started: ${when(row.incident_started)}`, card);
      el("p", `Stage: ${row.escalation_stage}`, card);
      el("p", `Next repeat opportunity: ${when(row.next_repeat_opportunity)}`, card);
      if (row.snoozed_until) el("p", `Snooze ends: ${when(row.snoozed_until)}`, card);
      if (row.pending_deadline) el("p", `${row.pending_transition ? "Activation" : "Recovery"} pending until ${when(row.pending_deadline)}`, card);
      el("p", `Notifications: ${blockers(row.notification_blockers)}`, card);
      if (this._errors(row)) el("p", `Delivery issues: ${JSON.stringify({...row.notification_errors, ...row.output_errors, ...row.policy_errors})}${row.missing_profiles ? " · Unavailable profiles" : ""}`, card).className = "error";
      const actions = el("div", undefined, card); actions.className = "actions";
      const invoke = async service => {
        await this._hass.callService("modern_alerts", service, {entity_id: row.entity_id}); await this.refresh();
      };
      this._button(actions, row.acknowledged ? "Resume" : "Acknowledge", () => invoke(row.acknowledged ? "unacknowledge" : "acknowledge"), !row.can_control || !row.active || !row.can_acknowledge);
      this._button(actions, row.snoozed_until ? "Cancel snooze" : "Snooze", () => invoke(row.snoozed_until ? "cancel_snooze" : "snooze"), !row.can_control || !row.active || !row.enable_snooze || row.acknowledged);
      this._button(actions, "Explain delivery", () => this.explain(row));
      const history = el("details", undefined, card); history.open = this._expanded.has(row.entry_id);
      history.addEventListener("toggle", () => {if (history.open) this._expanded.add(row.entry_id); else this._expanded.delete(row.entry_id);});
      el("summary", "Recent history", history);
      for (const event of [...row.history].reverse().slice(0, 10)) el("p", `${when(event.at)} · ${event.event.replaceAll("_", " ")}${event.output_id ? ` · ${event.output_id}` : ""}`, history);
      if (!row.history.length) el("p", "No recorded events", history);
    }
  }
  async explain(row) {
    const detail = this._hass.user?.is_admin ? await this._hass.callWS({type: "modern_alerts/preview", entry_id: row.entry_id}) : row;
    this._dialog.replaceChildren();
    el("h2", `Delivery: ${detail.name}`, this._dialog);
    el("p", "This is a read-only snapshot. Future delivery depends on the condition and policies at that time. No notification or device action is sent.", this._dialog);
    el("p", `Notifications: ${blockers(detail.notification_blockers)}`, this._dialog);
    el("p", `Current destinations: ${detail.destinations.join(", ") || "None"}`, this._dialog);
    el("p", `Next repeat opportunity: ${when(detail.next_repeat_opportunity)}`, this._dialog);
    el("p", `Next escalation opportunity: ${when(detail.next_escalation_opportunity)}`, this._dialog);
    el("p", `Buffered notification due: ${when(detail.buffered_notification_due)}`, this._dialog);
    el("p", "Time policies are checked within one minute; presence changes are checked immediately. Snooze, acknowledgement, and unavailable sources can hold scheduled opportunities.", this._dialog);
    for (const output of detail.outputs) el("p", `${output.name} (${output.type}): ${blockers(output.blockers)}`, this._dialog);
    if (detail.preview) {
      el("h3", "Current template preview", this._dialog);
      for (const [key, value] of Object.entries(detail.preview)) {
        el("p", key[0].toUpperCase() + key.slice(1), this._dialog);
        el("pre", value ?? "Not configured", this._dialog);
      }
    }
    this._button(this._dialog, "Close", () => this._dialog.close());
    if (!this._dialog.open) this._dialog.showModal();
  }
}
if (!customElements.get("modern-alerts-overview")) customElements.define("modern-alerts-overview", ModernAlertsOverview);
