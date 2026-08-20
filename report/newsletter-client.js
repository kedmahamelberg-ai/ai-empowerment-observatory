(() => {
  "use strict";

  const form = document.querySelector("#monthly-pulse-form");
  const status = document.querySelector("#monthly-pulse-status");
  const otherWrap = document.querySelector("#monthly-pulse-other-wrap");
  const otherInput = document.querySelector("#audience-role-other");

  const projectRef = String(
    window.AIEO_NEWSLETTER_CONFIG?.projectRef || ""
  ).trim();

  if (!projectRef) {
    console.error("AIEO newsletter projectRef is not configured.");
    if (status) {
      status.textContent = "Subscription is temporarily unavailable.";
      status.dataset.state = "error";
      status.hidden = false;
    }
    return;
  }

  const functionsBase =
    `https://${projectRef}.supabase.co/functions/v1`;

  function showStatus(message, state = "info") {
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
    status.hidden = false;
  }

  function setSubmitting(isSubmitting) {
    if (!form) return;
    const button = form.querySelector("button[type='submit']");
    if (!button) return;
    button.disabled = isSubmitting;
    button.setAttribute("aria-busy", String(isSubmitting));
    if (!button.dataset.defaultLabel) {
      button.dataset.defaultLabel = button.textContent || "Subscribe";
    }
    button.textContent = isSubmitting
      ? "Sending…"
      : button.dataset.defaultLabel;
  }

  function selectedRole() {
    if (!form) return "";
    return String(new FormData(form).get("audience_role") || "");
  }

  function syncOtherRole() {
    if (!form || !otherWrap || !otherInput) return;
  
    const isOther = selectedRole() === "other";
  
    otherWrap.hidden = !isOther;
    otherInput.disabled = !isOther;
    otherInput.required = isOther;
  
    if (!isOther) {
      otherInput.value = "";
    }
  }

  async function postJson(path, body) {
    const response = await fetch(`${functionsBase}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(
        payload.error || "The request could not be completed."
      );
    }
    return payload;
  }

  async function confirmFromUrl() {
    const url = new URL(window.location.href);
    const token = url.searchParams.get("confirm");
    if (!token) return;

    showStatus("Confirming your subscription…", "working");

    try {
      const payload = await postJson("newsletter-confirm", { token });
      showStatus(
        payload.message ||
          "You are confirmed. The next Monthly Pulse will arrive by email.",
        "success"
      );
      url.searchParams.delete("confirm");
      window.history.replaceState({}, "", url.toString());
    } catch (error) {
      showStatus(error.message, "error");
    }
  }

  if (form) {
    form.addEventListener("change", (event) => {
      if (event.target?.name === "audience_role") syncOtherRole();
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();

      if (!form.reportValidity()) return;

      const data = new FormData(form);
      const turnstileToken = String(
        data.get("cf-turnstile-response") || ""
      );

      if (!turnstileToken) {
        showStatus("Please complete the anti-bot check.", "error");
        return;
      }

      setSubmitting(true);
      showStatus("Sending your confirmation email…", "working");

      try {
        const payload = await postJson("newsletter-subscribe", {
          email: String(data.get("email") || ""),
          first_name: String(data.get("first_name") || ""),
          audience_role: String(data.get("audience_role") || ""),
          audience_role_other: String(
            data.get("audience_role_other") || ""
          ),
          website: String(data.get("website") || ""),
          consent: data.get("consent") === "on",
          turnstile_token: turnstileToken,
        });

        showStatus(
          payload.message ||
            "Almost there — check your inbox to confirm your subscription.",
          "success"
        );
        form.reset();
        syncOtherRole();

        if (window.turnstile) window.turnstile.reset();
      } catch (error) {
        showStatus(error.message, "error");
        if (window.turnstile) window.turnstile.reset();
      } finally {
        setSubmitting(false);
      }
    });

    syncOtherRole();
  }

  confirmFromUrl();
})();
