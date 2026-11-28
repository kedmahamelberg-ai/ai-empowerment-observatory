(() => {
  "use strict";

  const site = "https://observatory.hamelberg-ai.com";
  if (location.origin !== site ||
      /bot\b|spider|crawler|headless|ChatGPT-User|Claude-User/i.test(navigator.userAgent)) return;

  const pages = new Set([
    "/", "/about/", "/contact/", "/edu/", "/methodology/", "/privacy/",
    "/pro/", "/report/", "/reports/", "/status/"
  ]);
  const pagePath = location.pathname.replace(/\/index\.html$/, "/");
  if (!pages.has(pagePath)) return;

  const namedSources = new Map([
    ["google", "google"], ["google.com", "google"],
    ["chatgpt", "chatgpt"], ["chatgpt.com", "chatgpt"], ["chat.openai.com", "chatgpt"],
    ["claude", "claude"], ["claude.ai", "claude"], ["claude.com", "claude"]
  ]);
  const measurementId = "G-QC9205V7V7";
  const reportPathPattern = /^\/reports\/(?:.*\/)?([a-z0-9_-]+)\.pdf$/i;
  const permittedEvents = new Set([
    "quarterly_report_download", "newsletter_signup_click",
    "organisation_enquiry_click", "pro_interest_click"
  ]);
  const pageFields = { page_location: site + pagePath, page_referrer: "", page_path: pagePath };
  let recorded = false;
  let tagStarted = false;
  let restored = false;

  function arrivalSource() {
    let host = "";
    try {
      const referrer = new URL(document.referrer);
      if (referrer.protocol === "http:" || referrer.protocol === "https:") host = referrer.hostname;
    } catch {}
    if (host === location.hostname) return "internal";
    const domain = host.replace(/^www\./, "");
    if (/^google\.(?:com|[a-z]{2,3}|(?:co|com)\.[a-z]{2})$/.test(domain)) return "google";
    const provider = namedSources.get(domain);
    if (provider) return provider;
    const tag = new URLSearchParams(location.search).get("utm_source")?.trim().toLowerCase();
    return namedSources.get(tag) || (host ? "other_referral" : "direct_or_unknown");
  }

  function trackEvent(name, params = {}) {
    if (!recorded || !permittedEvents.has(name)) return;
    const fields = { ...pageFields };
    if (name === "quarterly_report_download" && /^[a-z0-9_-]{1,80}$/i.test(params.report_id)) {
      fields.report_id = params.report_id;
    }
    window.gtag("event", name, fields);
  }

  function startGoogleAnalytics() {
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () {
      window.dataLayer.push(arguments);
    };
    window.gtag("consent", "default", {
      analytics_storage: "denied",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied"
    });
    window.gtag("js", new Date());
    window.gtag("config", measurementId, {
      ...pageFields,
      send_page_view: false,
      allow_google_signals: false,
      allow_ad_personalization_signals: false
    });
    const script = document.createElement("script");
    script.async = true;
    script.referrerPolicy = "no-referrer";
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    document.head.appendChild(script);
  }

  function recordVisiblePage() {
    if (recorded || document.visibilityState !== "visible") return;
    recorded = true;
    fetch("https://tricsbvxamjzfeckivhz.supabase.co/rest/v1/rpc/observatory_record_page_view", {
      method: "POST",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      keepalive: true,
      headers: { apikey: "sb_publishable_MZIVSCpa5w6JAhSptPIqrQ_vTmzhbfF", "Content-Type": "application/json" },
      body: JSON.stringify({ p_path: pagePath, p_source: restored ? "direct_or_unknown" : arrivalSource() })
    }).catch(() => {});
    if (!tagStarted) {
      startGoogleAnalytics();
      tagStarted = true;
    }
    window.gtag("event", "page_view", pageFields);
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link) return;
    const url = new URL(link.href, site);
    const report = url.origin === site && reportPathPattern.exec(url.pathname);
    if (report) trackEvent("quarterly_report_download", { report_id: report[1] });
    const clickEvent = {
      "newsletter-signup": "newsletter_signup_click",
      "organisation-enquiry": "organisation_enquiry_click",
      "pro-interest": "pro_interest_click"
    }[link.closest("[data-analytics]")?.dataset.analytics];
    if (clickEvent) trackEvent(clickEvent);
  }, { passive: true });

  window.AIEOAnalytics = { trackEvent };
  window.addEventListener("pageshow", event => {
    if (!event.persisted) return;
    restored = true;
    recorded = false;
    recordVisiblePage();
  });
  document.addEventListener("visibilitychange", recordVisiblePage);
  recordVisiblePage();
})();
