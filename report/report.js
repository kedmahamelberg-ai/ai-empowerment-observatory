"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json",
  report: "/data/reports/latest.json",
  config: "/data/site-config.json",
  publicConfig: "/data/public-config.json"
};

async function safeJSON(path) {
  try {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

function signed(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function dateRange(eventsPayload) {
  const values = [];
  for (const event of eventsPayload?.events || []) {
    if (event.event_date) values.push(event.event_date);
    for (const source of event.sources || []) {
      if (source.published_at) values.push(source.published_at);
    }
  }
  const dates = values.map(value => new Date(value)).filter(date => !Number.isNaN(date.getTime())).sort((a, b) => a - b);
  if (!dates.length) return null;
  return { start: dates[0], end: dates[dates.length - 1] };
}

function formatDate(date) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function dominantKey(distribution) {
  return Object.entries(distribution || {}).sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0] || "unclear";
}

function readable(value) {
  return String(value || "").replaceAll("_", " ").replace(/^./, char => char.toUpperCase());
}

function takeaways(lenses) {
  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const amp = lenses.global.amplification;
  const gap = Number(amp.directional_amplification_gap || 0);
  const ratio = Number(amp.coverage_event_ratio || 0);
  const narrative = dominantKey(coverage.narrative_distribution);
  const eventIndex = Number(event.empowerment_index || 0);

  return [
    {
      label: "Overall direction",
      title: Math.abs(eventIndex) < 5 ? "The unique-event signal is close to neutral" : `The Event Lens is ${eventIndex > 0 ? "expansion-oriented" : "contraction-oriented"}`,
      text: `The current Event Empowerment Index is ${signed(event.empowerment_index)} on a -100 to +100 scale.`
    },
    {
      label: "Attention versus events",
      title: Math.abs(gap) < 1 ? "Media volume barely shifts the direction" : `Media volume shifts the signal ${gap > 0 ? "toward expansion" : "toward contraction"}`,
      text: `The Directional Amplification Gap is ${signed(gap)} points (Coverage minus Event).`
    },
    {
      label: "Duplication",
      title: ratio < 1.1 ? "Most coverage units represent distinct developments" : "Repeated coverage materially exceeds unique-event volume",
      text: `The current Coverage/Event ratio is ${ratio.toFixed(2)}.`
    },
    {
      label: "Narrative climate",
      title: `${readable(narrative)} is the largest Coverage Lens frame`,
      text: "Narrative framing is reported separately from substantive empowerment direction."
    }
  ];
}

async function storeRequest(config, payload) {
  if (!config?.supabase_url || !config?.supabase_anon_key || config.supabase_anon_key.includes("YOUR_")) {
    return { stored: false, reason: "Subscriber storage is not configured." };
  }

  const response = await fetch(`${config.supabase_url}/rest/v1/report_requests`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "apikey": config.supabase_anon_key,
      "Authorization": `Bearer ${config.supabase_anon_key}`,
      "Prefer": "return=minimal"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok && response.status !== 409) {
    throw new Error(`Signup request failed: ${response.status}`);
  }

  return { stored: true };
}

async function init() {
  const [lenses, events, reportMeta, config, publicConfig] = await Promise.all([
    safeJSON(paths.lenses),
    safeJSON(paths.events),
    safeJSON(paths.report),
    safeJSON(paths.config),
    safeJSON(paths.publicConfig)
  ]);

  if (!lenses?.global) return;

  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const range = dateRange(events);
  const period = reportMeta?.observation_window || (range ? `${formatDate(range.start)}–${formatDate(range.end)}` : "Current release");
  const reportPath = reportMeta?.file || config?.report?.download_path || "/reports/ai-empowerment-pulse-latest.pdf";
  const reportSlug = reportMeta?.slug || "ai-empowerment-pulse-latest";

  document.getElementById("report-title").textContent = reportMeta?.title || "AI Empowerment Pulse";
  document.getElementById("report-period").textContent = `${reportMeta?.edition || "Current-window brief"} · ${period}`;
  document.getElementById("cover-coverage-count").textContent = Number(coverage.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("cover-event-count").textContent = Number(event.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("fact-window").textContent = period;
  document.getElementById("fact-coverage").textContent = Number(coverage.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("fact-events").textContent = Number(event.unit_count_ai_relevant || 0).toLocaleString("en-GB");

  document.getElementById("takeaway-grid").innerHTML = takeaways(lenses).map(item => `
    <article>
      <span>${item.label}</span>
      <h3>${item.title}</h3>
      <p>${item.text}</p>
    </article>
  `).join("");

  const form = document.getElementById("report-form");
  const status = document.getElementById("form-status");

  form.addEventListener("submit", async eventObject => {
    eventObject.preventDefault();
    status.textContent = "Preparing your report…";

    const payload = {
      first_name: document.getElementById("first-name").value.trim(),
      last_name: document.getElementById("last-name").value.trim(),
      email: document.getElementById("work-email").value.trim().toLowerCase(),
      report_slug: reportSlug,
      privacy_acknowledged: document.getElementById("privacy-acknowledged").checked,
      newsletter_opt_in: document.getElementById("newsletter-opt-in").checked,
      source: "report_page"
    };

    try {
      const result = await storeRequest(publicConfig, payload);
      if (typeof window.gtag === "function") {
        window.gtag("event", "quarterly_report_download", {
          report_slug: reportSlug,
          signup_stored: result.stored
        });
      }
      status.textContent = result.stored
        ? "Thank you. Your download is starting."
        : "Your download is starting. Signup storage is not configured yet.";
      window.setTimeout(() => {
        window.location.href = reportPath;
      }, 350);
    } catch (error) {
      console.error(error);
      status.textContent = "The signup could not be saved, but the report is still available.";
      window.setTimeout(() => {
        window.location.href = reportPath;
      }, 600);
    }
  });
}

init();
