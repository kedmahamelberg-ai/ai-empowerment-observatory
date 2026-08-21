"use strict";

(() => {
  const config = window.AIEO_NEWSLETTER_CONFIG || {};
  const projectRef = String(config.projectRef || "").trim();
  const nextEditionLabel = String(
    config.nextEditionLabel || "the next scheduled edition"
  );
  const currentSignalUrl = String(config.currentSignalUrl || "/edu/");

  const form = document.getElementById("monthly-pulse-form");
  const statusEl = document.getElementById("monthly-pulse-status");
  const roleSelect = document.getElementById("audience-role");
  const otherWrap = document.getElementById("monthly-pulse-other-wrap");
  const otherInput = document.getElementById("audience-role-other");
  const confirmationPanel = document.getElementById(
    "monthly-pulse-confirmation"
  );
  const confirmationTitle = document.getElementById("confirmation-title");
  const confirmationMessage = document.getElementById(
    "confirmation-message"
  );
  const confirmationAction = document.getElementById("confirmation-action");
  const confirmationRetry = document.getElementById("confirmation-retry");

  function endpoint(functionName) {
    if (!projectRef) {
      throw new Error("AIEO newsletter projectRef is not configured.");
    }

    return `https://${projectRef}.supabase.co/functions/v1/${functionName}`;
  }

  function setFormStatus(message, state) {
    if (!statusEl) return;
    statusEl.textContent = message;
    statusEl.dataset.state = state;
    statusEl.hidden = !message;
  }

  function resetTurnstile() {
    if (window.turnstile && typeof window.turnstile.reset === "function") {
      try {
        window.turnstile.reset();
      } catch (error) {
        console.warn("Turnstile reset was not available.", error);
      }
    }
  }

  function syncOtherRole() {
    const showOther = roleSelect?.value === "other";

    if (otherWrap) {
      otherWrap.hidden = !showOther;
    }

    if (otherInput) {
      otherInput.required = Boolean(showOther);
      otherInput.disabled = !showOther;

      if (!showOther) {
        otherInput.value = "";
      }
    }
  }

  async function readJson(response) {
    const text = await response.text();
    if (!text) return {};

    try {
      return JSON.parse(text);
    } catch {
      return { message: text };
    }
  }

  function showConfirmationState(state) {
    if (!form || !confirmationPanel || !confirmationTitle || !confirmationMessage) {
      return;
    }

    document.documentElement.classList.add("monthly-pulse-confirmation-mode");
    form.hidden = true;
    confirmationPanel.hidden = false;

    if (confirmationAction) {
      confirmationAction.hidden = true;
      confirmationAction.href = currentSignalUrl;
    }

    if (confirmationRetry) {
      confirmationRetry.hidden = true;
    }

    const states = {
      working: {
        title: "Confirming your subscription…",
        message: "Please keep this page open for a moment."
      },
      confirmed: {
        title: "You’re confirmed.",
        message:
          `The next AI Empowerment Pulse will arrive on ${nextEditionLabel}. ` +
          "One useful signal, once a month.",
        actionText: "See the current signal →",
        actionHref: currentSignalUrl
      },
      already_confirmed: {
        title: "You’re already confirmed.",
        message:
          `Your email is registered for the next AI Empowerment Pulse on ` +
          `${nextEditionLabel}.`,
        actionText: "See the current signal →",
        actionHref: currentSignalUrl
      },
      expired: {
        title: "This confirmation link has expired.",
        message:
          "Return to the Pulse page and submit your email again to receive a new confirmation link.",
        actionText: "Return to the Pulse signup →",
        actionHref: "/report/"
      },
      invalid: {
        title: "This confirmation link is invalid.",
        message:
          "Return to the Pulse page and submit your email again.",
        actionText: "Return to the Pulse signup →",
        actionHref: "/report/"
      },
      temporarily_unavailable: {
        title: "We could not confirm you just yet.",
        message:
          "Your confirmation link is still valid. Please try again in a moment.",
        retry: true
      }
    };

    const selected = states[state] || states.temporarily_unavailable;
    confirmationTitle.textContent = selected.title;
    confirmationMessage.textContent = selected.message;

    if (confirmationAction && selected.actionText) {
      confirmationAction.textContent = selected.actionText;
      confirmationAction.href = selected.actionHref;
      confirmationAction.hidden = false;
    }

    if (confirmationRetry && selected.retry) {
      confirmationRetry.hidden = false;
    }
  }

  function cleanConfirmationUrl(state) {
    const nextUrl = new URL(window.location.href);
    nextUrl.search = "";

    if (state === "confirmed") {
      nextUrl.searchParams.set("confirmed", "1");
    } else if (state === "already_confirmed") {
      nextUrl.searchParams.set("confirmation", "already");
    } else if (state === "expired") {
      nextUrl.searchParams.set("confirmation", "expired");
    } else if (state === "invalid") {
      nextUrl.searchParams.set("confirmation", "invalid");
    }

    window.history.replaceState({}, "", nextUrl.toString());
  }

  async function confirmSubscription(token) {
    showConfirmationState("working");

    try {
      const response = await fetch(endpoint("newsletter-confirm"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token })
      });
      const result = await readJson(response);
      const state = String(result.state || "");

      if (response.ok && state === "confirmed") {
        cleanConfirmationUrl("confirmed");
        showConfirmationState("confirmed");
        return;
      }

      if (response.ok && state === "already_confirmed") {
        cleanConfirmationUrl("already_confirmed");
        showConfirmationState("already_confirmed");
        return;
      }

      if (state === "expired" || response.status === 410) {
        cleanConfirmationUrl("expired");
        showConfirmationState("expired");
        return;
      }

      if (state === "invalid" || response.status === 404) {
        cleanConfirmationUrl("invalid");
        showConfirmationState("invalid");
        return;
      }

      console.error("Newsletter confirmation failed", {
        status: response.status,
        state
      });
      showConfirmationState("temporarily_unavailable");
    } catch (error) {
      console.error("Newsletter confirmation request failed", error);
      showConfirmationState("temporarily_unavailable");
    }
  }

  async function submitForm(event) {
    event.preventDefault();
    syncOtherRole();

    if (!form || !form.reportValidity()) return;

    const formData = new FormData(form);
    const turnstileToken = String(
      formData.get("cf-turnstile-response") || ""
    );

    if (!turnstileToken) {
      setFormStatus("Please complete the anti-bot check.", "error");
      return;
    }

    const submitButton = form.querySelector("button[type='submit']");
    if (submitButton) submitButton.disabled = true;
    setFormStatus("Sending your confirmation email…", "working");

    const payload = {
      email: String(formData.get("email") || "").trim(),
      first_name: String(formData.get("first_name") || "").trim(),
      audience_role: String(formData.get("audience_role") || ""),
      audience_role_other:
        roleSelect?.value === "other"
          ? String(formData.get("audience_role_other") || "").trim()
          : "",
      website: String(formData.get("website") || ""),
      consent: formData.get("consent") === "on",
      turnstile_token: turnstileToken
    };

    try {
      const response = await fetch(endpoint("newsletter-subscribe"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const result = await readJson(response);

      if (!response.ok || result.ok === false) {
        throw new Error(
          String(result.error || "The subscription could not be processed.")
        );
      }

      if (result.already_subscribed) {
        setFormStatus(
          "You are already subscribed to the Monthly Pulse.",
          "success"
        );
      } else {
        setFormStatus(
          `Almost there — check your inbox to confirm. ` +
            `The next Pulse is scheduled for ${nextEditionLabel}.`,
          "success"
        );
        form.reset();
        syncOtherRole();
      }

      resetTurnstile();
    } catch (error) {
      console.error("Newsletter signup failed", error);
      setFormStatus(
        error instanceof Error
          ? error.message
          : "The subscription could not be processed. Please try again.",
        "error"
      );
      resetTurnstile();
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  }

  roleSelect?.addEventListener("change", syncOtherRole);
  syncOtherRole();
  form?.addEventListener("submit", submitForm);

  const params = new URLSearchParams(window.location.search);
  const token = String(params.get("confirm") || "").trim();
  const confirmed = params.get("confirmed") === "1";
  const persistedState = params.get("confirmation");

  if (confirmationRetry && token) {
    confirmationRetry.addEventListener("click", () => {
      confirmSubscription(token);
    });
  }

  if (token) {
    confirmSubscription(token);
  } else if (confirmed) {
    showConfirmationState("confirmed");
  } else if (persistedState === "already") {
    showConfirmationState("already_confirmed");
  } else if (persistedState === "expired") {
    showConfirmationState("expired");
  } else if (persistedState === "invalid") {
    showConfirmationState("invalid");
  }
})();
