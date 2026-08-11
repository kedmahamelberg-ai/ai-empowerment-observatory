(() => {
  "use strict";

  const MEASUREMENT_ID = "G-QC9205V7V7";
  const CONSENT_KEY = "aieo_analytics_consent_v1";
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

  function createConsentBanner() {
    if (document.getElementById("aieo-consent")) return;

    const banner = document.createElement("aside");
    banner.id = "aieo-consent";
    banner.className = "aieo-consent";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-live", "polite");
    banner.setAttribute("aria-label", "Analytics preferences");

    banner.innerHTML = `
      <div class="aieo-consent__copy">
        <strong>Help improve the Observatory</strong>
        <p>
          With your permission, we use Google Analytics to understand how the
          Observatory is used — including page views and report downloads.
          Analytics is off until you choose “Allow analytics”.
        </p>
      </div>
      <div class="aieo-consent__actions">
        <button type="button" class="aieo-consent__button aieo-consent__button--secondary" data-consent="deny">
          No thanks
        </button>
        <button type="button" class="aieo-consent__button aieo-consent__button--primary" data-consent="allow">
          Allow analytics
        </button>
      </div>
    `;

    document.body.appendChild(banner);

    banner.addEventListener("click", (event) => {
      const button = event.target.closest("[data-consent]");
      if (!button) return;

      const choice = button.dataset.consent;
      try {
        localStorage.setItem(CONSENT_KEY, choice);
      } catch {}

      if (choice === "allow") {
        loadGoogleAnalytics();
      }

      banner.remove();
    });
  }

  function initializeConsent() {
    let choice = null;

    try {
      choice = localStorage.getItem(CONSENT_KEY);
    } catch {}

    if (choice === "allow") {
      loadGoogleAnalytics();
      return;
    }

    if (choice === "deny") {
      return;
    }

    createConsentBanner();
  }

  window.AIEOAnalytics = {
    trackEvent,
    resetConsent() {
      try {
        localStorage.removeItem(CONSENT_KEY);
      } catch {}
      window.location.reload();
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeConsent);
  } else {
    initializeConsent();
  }
})();
