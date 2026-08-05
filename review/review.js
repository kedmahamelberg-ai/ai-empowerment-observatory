"use strict";

const statusElement = document.getElementById("status");
const resultsElement = document.getElementById("results");
const searchInput = document.getElementById("search-input");
const countryFilter = document.getElementById("country-filter");

let payload = null;

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function countriesFor(candidate) {
  return (candidate.matched_searches ?? []).map((item) => item.country);
}

function renderStatus() {
  const meta = payload.meta;
  statusElement.innerHTML = `
    <strong>${escapeHTML(meta.candidate_count_after_global_deduplication)} unique candidates</strong>
    collected from ${escapeHTML(meta.successful_searches)} of
    ${escapeHTML(meta.configured_searches)} configured searches on
    ${escapeHTML(meta.run_date)}.
    ${escapeHTML(meta.warning)}
  `;
}

function populateCountries() {
  const countries = new Set();

  for (const candidate of payload.candidates) {
    for (const country of countriesFor(candidate)) {
      countries.add(country);
    }
  }

  for (const country of [...countries].sort()) {
    const option = document.createElement("option");
    option.value = country;
    option.textContent = country;
    countryFilter.append(option);
  }
}

function render() {
  const term = searchInput.value.trim().toLocaleLowerCase();
  const selectedCountry = countryFilter.value;

  const candidates = payload.candidates.filter((candidate) => {
    const haystack = `${candidate.title} ${candidate.publisher}`.toLocaleLowerCase();
    const termMatch = !term || haystack.includes(term);
    const countryMatch =
      !selectedCountry || countriesFor(candidate).includes(selectedCountry);
    return termMatch && countryMatch;
  });

  if (!candidates.length) {
    resultsElement.innerHTML =
      '<div class="empty">No candidates match the current filters.</div>';
    return;
  }

  resultsElement.innerHTML = candidates
    .map((candidate) => {
      const tags = countriesFor(candidate)
        .map(
          (country) =>
            `<span class="market-tag">${escapeHTML(country)}</span>`
        )
        .join("");

      return `
        <article class="candidate">
          <div class="candidate-meta">
            <span>${escapeHTML(candidate.publisher)}</span>
            <span>${escapeHTML(candidate.iso_date || candidate.displayed_date || "Date unavailable")}</span>
          </div>

          <h2>
            <a
              href="${escapeHTML(candidate.link)}"
              target="_blank"
              rel="noopener noreferrer"
            >
              ${escapeHTML(candidate.title)}
            </a>
          </h2>

          <div class="market-tags">${tags}</div>
        </article>
      `;
    })
    .join("");
}

async function initialize() {
  try {
    const response = await fetch("../data/review/latest.json", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error(`Review data request failed with status ${response.status}`);
    }

    payload = await response.json();
    renderStatus();
    populateCountries();
    render();

    searchInput.addEventListener("input", render);
    countryFilter.addEventListener("change", render);
  } catch (error) {
    console.error(error);
    statusElement.innerHTML = `
      <strong>No collection has been published yet.</strong>
      Run the “Update AI News Collection” workflow manually after adding
      the SERPAPI_KEY repository secret.
    `;
    resultsElement.innerHTML = `
      <div class="error">${escapeHTML(error.message)}</div>
    `;
  }
}

initialize();
