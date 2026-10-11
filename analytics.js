(() => {
  "use strict";

  const MEASUREMENT_ID = "G-QC9205V7V7";
  const REPORT_PATH_PATTERN = /\/reports\/.*\.pdf(?:$|\?)/i;

  let analyticsLoaded = false;

  function loadGoogleAnalytics() {
    if (analyticsLoaded) return;
    analyticsLoaded = true;

    window.dataLayer = window.dataLayer || [];
    window.gtag = function () {
      window.dataLayer.push(arguments);
    };

    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(MEASUREMENT_ID)}`;
    document.head.appendChild(script);

    window.gtag("js", new Date());
    window.gtag("config", MEASUREMENT_ID, {
      anonymize_ip: true
    });

    attachCustomTracking();
  }

  function trackEvent(name, params = {}) {
    if (!analyticsLoaded || typeof window.gtag !== "function") return;

    window.gtag("event", name, {
      ...params,
      page_location: window.location.href,
      page_path: window.location.pathname
    });
  }

  function reportIdFromHref(href) {
    try {
      const url = new URL(href, window.location.href);
      const filename = url.pathname.split("/").pop() || "unknown-report";
      return filename.replace(/\.pdf$/i, "");
    } catch {
      return "unknown-report";
    }
  }

  function attachCustomTracking() {
    document.addEventListener(
      "click",
      (event) => {
        const link = event.target.closest("a[href]");
        if (!link) return;

        const href = link.getAttribute("href") || "";

        if (REPORT_PATH_PATTERN.test(href)) {
          trackEvent("quarterly_report_download", {
            report_id: reportIdFromHref(href),
            link_url: new URL(href, window.location.href).href,
            link_text: (link.textContent || "").trim().slice(0, 120)
          });
        }

        if (
          link.matches("[data-analytics='newsletter-signup']") ||
          link.closest("[data-analytics='newsletter-signup']")
        ) {
          trackEvent("newsletter_signup_click", {
            link_url: new URL(href, window.location.href).href
          });
        }

        if (
          link.matches("[data-analytics='organisation-enquiry']") ||
          link.closest("[data-analytics='organisation-enquiry']")
        ) {
          trackEvent("organisation_enquiry_click", {
            link_url: new URL(href, window.location.href).href
          });
        }

        if (
          link.matches("[data-analytics='pro-interest']") ||
          link.closest("[data-analytics='pro-interest']")
        ) {
          trackEvent("pro_interest_click", {
            link_url: new URL(href, window.location.href).href
          });
        }
      },
      { passive: true }
    );
  }

  window.AIEOAnalytics = {
    trackEvent
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadGoogleAnalytics);
  } else {
    loadGoogleAnalytics();
  }
})();
